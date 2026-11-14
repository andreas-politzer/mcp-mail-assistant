"""Tokens are bound to this server's address and to the mail scope.

The vault connector runs on another address with its own tokens. Nothing issued
there (or anywhere else) may work here, and vice versa.
"""

import pytest

from auth_helpers import RESOURCE

OTHER = "https://vault.example.test/mcp"


def issue(store, *, resource, scopes=("mail:send",)):
    family = store.new_family("some-client")
    return store.issue_tokens(
        family_id=family, client_id="some-client", scopes=list(scopes),
        resource=resource, subject="owner",
    )


def test_positive_control_matching_resource_is_accepted(flow, store):
    issued = issue(store, resource=RESOURCE)
    assert flow.mcp_initialize(issued.access_token).status_code == 200


def test_token_for_another_resource_is_refused(flow, store):
    issued = issue(store, resource=OTHER)
    assert flow.mcp_initialize(issued.access_token).status_code == 401


def test_token_without_a_resource_is_refused(flow, store):
    issued = issue(store, resource=None)
    assert flow.mcp_initialize(issued.access_token).status_code == 401


def test_token_with_a_foreign_scope_is_forbidden(flow, store):
    issued = issue(store, resource=RESOURCE, scopes=("vault:read",))
    assert flow.mcp_initialize(issued.access_token).status_code == 403


def test_token_from_another_instance_is_unknown_here(flow, tmp_path, clock):
    from outbox_mcp.auth.store import AuthStore

    elsewhere = AuthStore(tmp_path / "elsewhere.db", clock=clock)
    family = elsewhere.new_family("c")
    issued = elsewhere.issue_tokens(
        family_id=family, client_id="c", scopes=["mail:send"], resource=RESOURCE, subject="owner")
    assert flow.mcp_initialize(issued.access_token).status_code == 401


def test_authorize_with_a_foreign_resource_is_refused(flow, store):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    resp = flow.authorize(cid, challenge, resource=OTHER)
    assert resp.status_code in (302, 400)
    if resp.status_code == 302:
        assert "error=" in resp.headers["location"]
        assert "/login" not in resp.headers["location"]
    assert store.counts()["pending_requests"] == 0


def test_authorize_with_our_resource_works(flow):
    t = flow.full_login(resource=RESOURCE)
    assert flow.mcp_initialize(t["access"]).status_code == 200


def test_tokens_are_bound_to_us_even_if_the_client_names_no_resource(flow, store):
    t = flow.full_login()
    assert store.lookup_access(t["access"]).resource == RESOURCE


@pytest.mark.parametrize("scope", ["vault:read", "mail:send vault:read", "admin"])
def test_scopes_other_than_mail_send_are_refused_at_registration(flow, scope):
    assert flow.register(scope=scope).status_code == 400
