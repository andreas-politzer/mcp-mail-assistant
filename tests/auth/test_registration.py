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
    assert auth_config.max_clients == 200
    assert auth_config.max_unused_clients == 20
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


def test_never_signed_in_registrations_have_their_own_strict_limit(auth_config, tmp_path, clock):
    cfg = dataclasses.replace(auth_config, max_clients=50, max_unused_clients=3, db_path=tmp_path / "u.db")
    store = AuthStore(cfg.db_path, clock=clock)
    app = create_authenticated_app(cfg, store, configure=add_ping, json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        f = Flow(c, store, clock)
        for _ in range(3):
            assert f.register().status_code == 201
        assert f.register().status_code == 400


def test_clients_with_a_finished_sign_in_do_not_use_up_the_strict_limit(auth_config, tmp_path, clock):
    cfg = dataclasses.replace(auth_config, max_clients=50, max_unused_clients=2, db_path=tmp_path / "s.db")
    store = AuthStore(cfg.db_path, clock=clock)
    app = create_authenticated_app(cfg, store, configure=add_ping, json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        f = Flow(c, store, clock)
        for _ in range(4):
            f.full_login()
        assert f.register().status_code == 201
        assert store.client_stats()["never_signed_in"] == 1


def test_refusal_names_the_limit_without_secrets(store):
    from mcp.shared.auth import OAuthClientInformationFull
    from outbox_mcp.auth.store import ClientLimitReached
    import pytest

    def info(i):
        return OAuthClientInformationFull(client_id=f"c{i}", redirect_uris=["https://claude.ai/api/mcp/auth_callback"],
                                          token_endpoint_auth_method="none")
    store.register_client(info(1), max_clients=10, prune_before=0, max_unused=1)
    with pytest.raises(ClientLimitReached) as err:
        store.register_client(info(2), max_clients=10, prune_before=0, max_unused=1)
    assert (err.value.kind, err.value.count, err.value.limit) == ("never_signed_in", 1, 1)


def test_prune_deletes_only_clients_not_in_use(flow, store):
    used = flow.full_login()
    idle = flow.new_client_id()
    assert store.prune_clients() == 1
    assert store.get_client(idle) is None
    assert store.get_client(used["client_id"]) is not None


def test_admin_prune_and_status(flow, store, tmp_path, capsys):
    from outbox_mcp.auth import admin
    flow.new_client_id()
    path = store.path if hasattr(store, "path") else None
    if path is None:
        import pytest
        pytest.skip("store has no path attribute")
    assert admin.main(["status", "--db", str(path)]) == 0
    assert "clients never signed in: 1" in capsys.readouterr().out
    assert admin.main(["prune", "--db", str(path)]) == 0
    assert "pruned: 1" in capsys.readouterr().out


def test_limits_can_be_set_from_the_environment():
    from outbox_mcp.auth.config import AuthConfig
    import pytest
    from outbox_mcp.auth.passwords import hash_password
    env = {"OUTBOX_PUBLIC_BASE_URL": "https://x.example", "OUTBOX_OAUTH_PASSWORD_HASH": hash_password("pw-for-test-1234"),
           "OUTBOX_OAUTH_MAX_CLIENTS": "300", "OUTBOX_OAUTH_MAX_UNUSED_CLIENTS": "7"}
    cfg = AuthConfig.from_env(env)
    assert (cfg.max_clients, cfg.max_unused_clients) == (300, 7)
    from outbox_mcp.auth.config import ConfigError
    with pytest.raises(ConfigError):
        AuthConfig.from_env({**env, "OUTBOX_OAUTH_MAX_UNUSED_CLIENTS": "0"})
