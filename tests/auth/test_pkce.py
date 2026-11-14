"""PKCE (S256) and the one-time use of authorization codes."""

from auth_helpers import PASSWORD  # noqa: F401


def get_code(flow):
    cid = flow.new_client_id()
    verifier, challenge = flow.pkce()
    rid = flow.request_id(flow.authorize(cid, challenge))
    done = flow.login(rid)
    return cid, verifier, flow.query_of(done)["code"][0]


def test_authorize_requires_a_code_challenge(flow, store):
    cid = flow.new_client_id()
    resp = flow.authorize(cid, None)
    assert resp.status_code in (302, 400)
    if resp.status_code == 302:
        assert "error=" in resp.headers["location"]
        assert "/login" not in resp.headers["location"]
    assert store.counts()["pending_requests"] == 0


def test_plain_challenge_method_is_refused(flow, store):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    resp = flow.authorize(cid, challenge, method="plain")
    assert resp.status_code in (302, 400)
    if resp.status_code == 302:
        assert "error=" in resp.headers["location"]
        assert "/login" not in resp.headers["location"]
    assert store.counts()["pending_requests"] == 0


def test_wrong_verifier_is_refused(flow):
    cid, _, code = get_code(flow)
    resp = flow.exchange(cid, code, "x" * 43)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_missing_verifier_is_refused(flow):
    cid, _, code = get_code(flow)
    resp = flow.exchange(cid, code, None)
    assert resp.status_code == 400


def test_code_works_once_and_reuse_revokes_what_it_produced(flow):
    cid, verifier, code = get_code(flow)
    first = flow.exchange(cid, code, verifier)
    assert first.status_code == 200
    access, refresh = first.json()["access_token"], first.json()["refresh_token"]
    assert flow.mcp_initialize(access).status_code == 200

    second = flow.exchange(cid, code, verifier)
    assert second.status_code == 400
    assert second.json()["error"] == "invalid_grant"
    # Replay detected: everything issued from that code is dead.
    assert flow.mcp_initialize(access).status_code == 401
    assert flow.refresh(cid, refresh).status_code == 400


def test_code_expires(flow, clock):
    cid, verifier, code = get_code(flow)
    clock.advance(121)
    resp = flow.exchange(cid, code, verifier)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_grant"


def test_code_is_bound_to_its_client(flow):
    cid, verifier, code = get_code(flow)
    other = flow.new_client_id()
    assert flow.exchange(other, code, verifier).status_code == 400


def test_code_is_bound_to_its_redirect_uri(flow):
    cid, verifier, code = get_code(flow)
    resp = flow.exchange(cid, code, verifier, redirect_uri="https://claude.ai/api/mcp/other")
    assert resp.status_code == 400


def test_codes_are_not_stored_in_plaintext(flow, auth_config):
    cid, verifier, code = get_code(flow)
    assert code.encode() not in auth_config.db_path.read_bytes()
