"""Open dynamic client registration must not become a way to fill the disk."""

import dataclasses

from starlette.testclient import TestClient

from outbox_mcp.auth.app import create_authenticated_app
from outbox_mcp.auth.store import AuthStore

from auth_helpers import ISSUER, add_ping, Flow

DAY = 86400
HOUR = 3600


def test_public_clients_get_no_secret(flow):
    body = flow.register().json()
    assert body.get("client_secret") in (None, "")
    assert body["token_endpoint_auth_method"] == "none"


def test_registration_is_capped(auth_config, tmp_path, clock):
    cfg = dataclasses.replace(auth_config, max_clients=3, db_path=tmp_path / "cap.db")
    store = AuthStore(cfg.db_path, clock=clock)
    app = create_authenticated_app(cfg, store, configure=add_ping, json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        f = Flow(c, store, clock)
        for _ in range(3):
            assert f.register().status_code == 201
        assert f.register().status_code in (400, 429, 503)
        assert store.counts()["clients"] == 3


def test_unused_clients_are_pruned_after_an_hour(flow, store, clock):
    for _ in range(3):
        flow.new_client_id()
    clock.advance(HOUR - 60)
    flow.new_client_id()
    assert store.counts()["clients"] == 4  # nothing is old enough yet
    clock.advance(120)
    flow.new_client_id()
    assert store.counts()["clients"] == 2  # the first three are gone, the 4th and the new one stay


def test_clients_that_were_used_are_kept(flow, store, clock):
    t = flow.full_login()
    clock.advance(DAY + 60)
    flow.new_client_id()
    assert store.get_client(t["client_id"]) is not None


def test_clients_whose_tokens_were_all_revoked_are_pruned(flow, store, clock):
    t = flow.full_login()
    assert flow.revoke(t["client_id"], t["access"]).status_code == 200
    clock.advance(HOUR + 60)
    flow.new_client_id()
    assert store.get_client(t["client_id"]) is None


def test_clients_with_only_expired_tokens_are_pruned(flow, store, clock):
    t = flow.full_login()
    clock.advance(90 * DAY + 60)  # the refresh token has run out, the family row still says "not revoked"
    flow.new_client_id()
    assert store.get_client(t["client_id"]) is None


def test_clients_with_a_still_valid_refresh_token_are_kept_for_the_whole_lifetime(flow, store, clock):
    t = flow.full_login()
    clock.advance(89 * DAY)
    flow.new_client_id()
    assert store.get_client(t["client_id"]) is not None


def test_default_limits_are_the_documented_ones(auth_config):
    assert auth_config.max_clients == 20
    assert auth_config.client_prune_seconds == 3600
    assert auth_config.max_pending_requests == 20
    assert auth_config.max_parallel_password_checks == 2
    assert auth_config.max_login_body == 8192


# --- only public clients -------------------------------------------------------------

import pytest


@pytest.mark.parametrize("method", ["client_secret_post", "client_secret_basic", "private_key_jwt"])
def test_confidential_clients_are_refused(flow, store, method):
    resp = flow.register(token_endpoint_auth_method=method)
    assert resp.status_code == 400
    assert store.counts()["clients"] == 0


def test_no_client_secret_is_ever_stored(flow, store):
    cid = flow.new_client_id()
    assert store.get_client(cid).client_secret is None
    with store._tx() as conn:
        raw = conn.execute("SELECT info_json FROM clients").fetchone()["info_json"]
    assert '"client_secret":null' in raw.replace(" ", "")


def test_metadata_advertises_public_clients_only(client):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["token_endpoint_auth_methods_supported"] == ["none"]
    assert body["revocation_endpoint_auth_methods_supported"] == ["none"]


# --- open login attempts are limited -------------------------------------------------

def test_open_logins_are_capped_and_the_overflow_is_refused(auth_config, tmp_path, clock):
    cfg = dataclasses.replace(auth_config, max_pending_requests=3, db_path=tmp_path / "pend.db")
    store = AuthStore(cfg.db_path, clock=clock)
    app = create_authenticated_app(cfg, store, configure=add_ping, json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        f = Flow(c, store, clock)
        cid = f.new_client_id()
        for _ in range(3):
            _, challenge = f.pkce()
            f.request_id(f.authorize(cid, challenge))
        _, challenge = f.pkce()
        resp = f.authorize(cid, challenge)
        assert resp.status_code == 302
        assert "error=temporarily_unavailable" in resp.headers["location"]
        assert "/login" not in resp.headers["location"]
        assert store.counts()["pending_requests"] == 3
        clock.advance(601)  # the old ones expired, so there is room again
        _, challenge = f.pkce()
        f.request_id(f.authorize(cid, challenge))
