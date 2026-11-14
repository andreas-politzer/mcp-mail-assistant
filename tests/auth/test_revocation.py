"""Revoking one token, and revoking everything."""

from outbox_mcp.auth.admin import main as admin_main
from outbox_mcp.auth.store import AuthStore


def test_revoking_the_access_token_kills_the_whole_family(flow):
    t = flow.full_login()
    assert flow.revoke(t["client_id"], t["access"]).status_code == 200
    assert flow.mcp_initialize(t["access"]).status_code == 401
    assert flow.refresh(t["client_id"], t["refresh"]).status_code == 400


def test_revoking_the_refresh_token_kills_the_access_token_too(flow):
    t = flow.full_login()
    assert flow.revoke(t["client_id"], t["refresh"], hint="refresh_token").status_code == 200
    assert flow.mcp_initialize(t["access"]).status_code == 401
    assert flow.refresh(t["client_id"], t["refresh"]).status_code == 400


def test_another_client_cannot_revoke_my_token(flow):
    t = flow.full_login()
    other = flow.new_client_id()
    assert flow.revoke(other, t["access"]).status_code == 200
    assert flow.mcp_initialize(t["access"]).status_code == 200


def test_revoking_an_unknown_token_is_harmless(flow):
    cid = flow.new_client_id()
    assert flow.revoke(cid, "unknown-token").status_code == 200


def test_revoke_all_removes_everything(flow, store):
    first = flow.full_login()
    second = flow.full_login()
    # A login that was started but not finished must die too.
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    pending = flow.request_id(flow.authorize(cid, challenge))

    store.revoke_all()

    for t in (first, second):
        assert flow.mcp_initialize(t["access"]).status_code == 401
        assert flow.refresh(t["client_id"], t["refresh"]).status_code in (400, 401)
    assert flow.client.get("/login", params={"request": pending}).status_code == 400
    counts = store.counts()
    assert counts["clients"] == counts["auth_codes"] == counts["pending_requests"] == 0
    assert counts["access_tokens"] == counts["refresh_tokens"] == counts["families"] == 0


def test_a_fresh_login_works_after_revoke_all(flow, store):
    flow.full_login()
    store.revoke_all()
    again = flow.full_login()
    assert flow.mcp_initialize(again["access"]).status_code == 200


def test_admin_command_revokes_everything(flow, store, auth_config, capsys):
    t = flow.full_login()
    assert admin_main(["revoke-all", "--db", str(auth_config.db_path)]) == 0
    assert "revoked" in capsys.readouterr().out.lower()
    assert flow.mcp_initialize(t["access"]).status_code == 401


def test_admin_status_shows_counts_but_never_tokens(flow, auth_config, capsys):
    t = flow.full_login()
    assert admin_main(["status", "--db", str(auth_config.db_path)]) == 0
    out = capsys.readouterr().out
    assert "refresh_tokens" in out
    assert t["access"] not in out and t["refresh"] not in out


def test_admin_command_fails_cleanly_for_a_missing_database(tmp_path, capsys):
    assert admin_main(["revoke-all", "--db", str(tmp_path / "nope.db")]) == 2
    assert "not found" in capsys.readouterr().err.lower()
