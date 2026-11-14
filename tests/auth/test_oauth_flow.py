"""The whole dance, end to end, over HTTP."""

from auth_helpers import CALLBACK, ISSUER, RESOURCE


def test_unauthenticated_mcp_request_points_to_the_metadata(flow):
    resp = flow.mcp_initialize()
    assert resp.status_code == 401
    challenge = resp.headers["www-authenticate"]
    assert challenge.lower().startswith("bearer")
    assert f'resource_metadata="{ISSUER}/.well-known/oauth-protected-resource/mcp"' in challenge


def test_protected_resource_metadata(client):
    body = client.get("/.well-known/oauth-protected-resource/mcp").json()
    assert body["resource"] == RESOURCE
    assert body["scopes_supported"] == ["mail:send"]
    assert len(body["authorization_servers"]) == 1


def test_authorization_server_metadata(client):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["authorization_endpoint"] == ISSUER + "/authorize"
    assert body["token_endpoint"] == ISSUER + "/token"
    assert body["registration_endpoint"] == ISSUER + "/register"
    assert body["revocation_endpoint"] == ISSUER + "/revoke"
    assert set(body["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    # Claude registers as a public client: "none" must be advertised.
    assert "none" in body["token_endpoint_auth_methods_supported"]
    assert "none" in body["revocation_endpoint_auth_methods_supported"]


def test_issuer_is_spelled_the_same_in_both_documents(client):
    prm = client.get("/.well-known/oauth-protected-resource/mcp").json()
    asm = client.get("/.well-known/oauth-authorization-server").json()
    assert prm["authorization_servers"][0] == asm["issuer"]


def test_happy_path(flow):
    t = flow.full_login()
    body = t["body"]
    assert body["token_type"].lower() == "bearer"
    assert body["expires_in"] == 3600
    assert body["scope"] == "mail:send"
    assert body["refresh_token"] and body["access_token"] != body["refresh_token"]
    assert flow.mcp_initialize(t["access"]).status_code == 200


def test_code_redirect_goes_to_the_registered_address_and_echoes_state(flow):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    rid = flow.request_id(flow.authorize(cid, challenge, state="abc-xyz"))
    done = flow.login(rid)
    assert done.status_code == 303
    assert done.headers["location"].startswith(CALLBACK + "?")
    assert flow.query_of(done)["state"] == ["abc-xyz"]


def test_requests_without_or_with_bad_tokens_are_refused(flow):
    assert flow.mcp_initialize().status_code == 401
    assert flow.mcp_initialize("not-a-token").status_code == 401


def test_token_responses_are_not_cacheable(flow):
    cid = flow.new_client_id()
    verifier, challenge = flow.pkce()
    rid = flow.request_id(flow.authorize(cid, challenge))
    code = flow.query_of(flow.login(rid))["code"][0]
    resp = flow.exchange(cid, code, verifier)
    assert "no-store" in resp.headers["cache-control"]
