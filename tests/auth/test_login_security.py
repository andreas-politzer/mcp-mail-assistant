"""A failed login must never produce an authorization code."""

from auth_helpers import CALLBACK, ISSUER, PASSWORD


def start(flow):
    cid = flow.new_client_id()
    verifier, challenge = flow.pkce()
    rid = flow.request_id(flow.authorize(cid, challenge))
    return cid, verifier, rid


def test_authorize_endpoint_never_issues_a_code(flow, store):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    resp = flow.authorize(cid, challenge)
    assert resp.status_code == 302
    location = resp.headers["location"]
    assert location.startswith(ISSUER + "/login?request=")
    assert "code=" not in location
    assert store.counts()["auth_codes"] == 0


def test_authorize_via_post_is_no_shortcut_either(flow, store):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    resp = flow.client.post("/authorize", data={
        "response_type": "code", "client_id": cid, "redirect_uri": CALLBACK,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": "s",
    })
    assert resp.status_code == 302
    assert "code=" not in resp.headers["location"]
    assert store.counts()["auth_codes"] == 0


def test_wrong_password_creates_no_code(flow, store):
    _, _, rid = start(flow)
    resp = flow.login(rid, "wrong password")
    assert resp.status_code == 401
    assert "location" not in resp.headers
    assert store.counts()["auth_codes"] == 0


def test_empty_password_creates_no_code(flow, store):
    _, _, rid = start(flow)
    resp = flow.login(rid, "")
    assert resp.status_code == 401
    assert store.counts()["auth_codes"] == 0


def test_missing_csrf_token_is_refused(flow, store):
    _, _, rid = start(flow)
    flow.login_page(rid)
    resp = flow.login(rid, csrf=None)
    assert resp.status_code == 403
    assert store.counts()["auth_codes"] == 0


def test_wrong_csrf_token_is_refused(flow, store):
    _, _, rid = start(flow)
    flow.login_page(rid)
    resp = flow.login(rid, csrf="x" * 30)
    assert resp.status_code == 403
    assert store.counts()["auth_codes"] == 0


def test_missing_csrf_cookie_is_refused(flow, store):
    _, _, rid = start(flow)
    token = flow.csrf_of(flow.login_page(rid))
    flow.client.cookies.clear()
    resp = flow.login(rid, csrf=token)
    assert resp.status_code == 403
    assert store.counts()["auth_codes"] == 0


def test_unknown_request_id_is_refused(flow, store):
    assert flow.client.get("/login", params={"request": "nope"}).status_code == 400
    resp = flow.client.post("/login", data={
        "request": "nope", "password": PASSWORD, "csrf": "x", "action": "approve"})
    assert resp.status_code in (400, 403)
    assert store.counts()["auth_codes"] == 0


def test_a_request_can_only_be_approved_once(flow, store):
    _, _, rid = start(flow)
    first = flow.login(rid)
    assert first.status_code == 303
    second = flow.client.post("/login", data={
        "request": rid, "password": PASSWORD, "csrf": "x", "action": "approve"})
    assert second.status_code in (400, 403)
    assert store.counts()["auth_codes"] == 1


def test_expired_request_is_refused(flow, store, clock):
    _, _, rid = start(flow)
    clock.advance(601)
    assert flow.client.get("/login", params={"request": rid}).status_code == 400
    resp = flow.client.post("/login", data={
        "request": rid, "password": PASSWORD, "csrf": "x", "action": "approve"})
    assert resp.status_code in (400, 403)
    assert store.counts()["auth_codes"] == 0


def test_three_wrong_guesses_kill_the_request(flow, store):
    _, _, rid = start(flow)
    for _ in range(3):
        assert flow.login(rid, "nope").status_code == 401
    resp = flow.client.post("/login", data={
        "request": rid, "password": PASSWORD, "csrf": "x", "action": "approve"})
    assert resp.status_code in (400, 403)
    assert flow.client.get("/login", params={"request": rid}).status_code == 400
    assert store.counts()["auth_codes"] == 0


def test_global_lockout_blocks_even_the_correct_password(flow, store):
    _, _, rid1 = start(flow)
    for _ in range(3):
        flow.login(rid1, "nope")
    _, _, rid2 = start(flow)
    for _ in range(2):
        flow.login(rid2, "nope")
    resp = flow.login(rid2, PASSWORD)
    assert resp.status_code == 429
    assert "retry-after" in resp.headers
    assert "location" not in resp.headers
    assert store.counts()["auth_codes"] == 0


def test_lockout_ends_after_the_window(flow, store, clock):
    _, _, rid1 = start(flow)
    for _ in range(3):
        flow.login(rid1, "nope")
    _, _, rid2 = start(flow)
    for _ in range(2):
        flow.login(rid2, "nope")
    assert flow.login(rid2, PASSWORD).status_code == 429
    clock.advance(901)
    _, _, rid3 = start(flow)  # the old request has expired by now
    assert flow.login(rid3, PASSWORD).status_code == 303


def test_a_successful_login_resets_the_failure_counter(flow, store):
    _, _, rid = start(flow)
    flow.login(rid, "nope")
    flow.login(rid, "nope")
    assert store.login_failure_count() == 2
    assert flow.login(rid).status_code == 303
    assert store.login_failure_count() == 0


def test_deny_returns_access_denied_and_no_code(flow, store):
    _, _, rid = start(flow)
    resp = flow.login(rid, "", action="deny")
    assert resp.status_code == 303
    query = flow.query_of(resp)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["st-123"]
    assert "code" not in query
    assert store.counts()["auth_codes"] == 0
    assert store.counts()["pending_requests"] == 0


def test_login_page_names_the_destination_and_is_hardened(flow):
    _, _, rid = start(flow)
    page = flow.login_page(rid)
    assert page.status_code == 200
    assert "claude.ai" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    assert "no-store" in page.headers["cache-control"]
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    cookie = page.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" in cookie


def test_login_page_escapes_the_client_name(flow):
    cid = flow.new_client_id(client_name="<script>alert(1)</script>")
    _, challenge = flow.pkce()
    rid = flow.request_id(flow.authorize(cid, challenge))
    page = flow.login_page(rid)
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_login_page_tells_the_user_to_continue_only_if_they_connected_claude_themselves(flow):
    _, _, rid = start(flow)
    page = flow.login_page(rid)
    assert "nur fortfahren" in page.text.lower()
    assert "selbst" in page.text
    assert "Ablehnen" in page.text


def test_oversized_login_post_is_refused_before_anything_happens(flow, store):
    _, _, rid = start(flow)
    csrf = flow.csrf_of(flow.login_page(rid))
    resp = flow.client.post("/login", data={
        "request": rid, "password": "x" * 100_000, "csrf": csrf, "action": "approve"})
    assert resp.status_code == 413
    assert store.counts()["auth_codes"] == 0
    assert store.login_failure_count() == 0


def test_oversized_login_post_without_content_length_is_refused(flow, store):
    _, _, rid = start(flow)

    def chunks():
        for _ in range(200):
            yield b"password=" + b"x" * 1000 + b"&"

    resp = flow.client.post("/login", content=chunks(),
                            headers={"content-type": "application/x-www-form-urlencoded"})
    assert resp.status_code == 413
    assert store.counts()["auth_codes"] == 0


def test_login_post_must_be_a_plain_form(flow):
    _, _, rid = start(flow)
    resp = flow.client.post("/login", json={"request": rid, "password": PASSWORD})
    assert resp.status_code == 415


def test_login_page_may_only_submit_to_itself_and_the_allowed_return_origin(flow):
    """Chromium applies form-action to the redirect after the POST, so the allowed return
    origin (claude.ai) must be listed, and nothing else."""
    _, _, rid = start(flow)
    csp = flow.login_page(rid).headers["content-security-policy"]
    directive = [d.strip() for d in csp.split(";") if d.strip().startswith("form-action")][0]
    assert directive == "form-action 'self' https://claude.ai"


def test_form_action_follows_the_configured_redirect_allowlist(auth_config, tmp_path, clock):
    import dataclasses

    from starlette.testclient import TestClient

    from auth_helpers import Flow, add_ping
    from outbox_mcp.auth.app import create_authenticated_app
    from outbox_mcp.auth.store import AuthStore

    other = "https://example.org/oauth/cb"
    cfg = dataclasses.replace(auth_config, redirect_uris=(CALLBACK, other), db_path=tmp_path / "x.db")
    store = AuthStore(cfg.db_path, clock=clock)
    app = create_authenticated_app(cfg, store, configure=add_ping, json_response=True)
    with TestClient(app, base_url=ISSUER, follow_redirects=False) as c:
        f = Flow(c, store, clock)
        cid = f.new_client_id()
        _, challenge = f.pkce()
        rid = f.request_id(f.authorize(cid, challenge))
        csp = f.login_page(rid).headers["content-security-policy"]
        assert "form-action 'self' https://claude.ai https://example.org;" in csp + ";"
