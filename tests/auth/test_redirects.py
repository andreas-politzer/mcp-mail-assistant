"""Redirect URIs must match exactly. Only addresses that are proven to be needed are allowed."""

import pytest

from outbox_mcp.auth.config import AuthConfig
from outbox_mcp.auth.redirects import is_allowed_redirect_uri, parse_allowlist
from outbox_mcp.errors import ConfigError

CLAUDE_AI = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_COM = "https://claude.com/api/mcp/auth_callback"

BAD_URIS = [
    "http://claude.ai/api/mcp/auth_callback",
    "https://claude.ai.evil.example/api/mcp/auth_callback",
    "https://evil.example/api/mcp/auth_callback",
    "https://claude.ai@evil.example/api/mcp/auth_callback",
    "https://claude.ai/api/mcp/auth_callback/",
    "https://claude.ai/api/mcp/auth_callback/extra",
    "https://claude.ai/api/mcp/auth_callback?next=https://evil.example",
    "https://claude.ai/api/mcp/auth_callback#frag",
    "https://claude.ai:8443/api/mcp/auth_callback",
    "https://claude.ai/api/mcp/",
    "https://sub.claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "http://localhost:3118/callback",
    "https://*.claude.ai/api/mcp/auth_callback",
    "javascript:alert(1)",
    "",
]


def test_exact_match_is_allowed():
    assert is_allowed_redirect_uri(CLAUDE_AI, (CLAUDE_AI,)) is True


@pytest.mark.parametrize("bad", BAD_URIS)
def test_everything_else_is_rejected(bad):
    assert is_allowed_redirect_uri(bad, (CLAUDE_AI,)) is False


def test_default_allowlist_contains_only_the_documented_claude_ai_address(tmp_path):
    cfg = AuthConfig(base_url="https://outbox.example.test", password_hash="x", db_path=tmp_path / "a.db")
    assert cfg.redirect_uris == (CLAUDE_AI,)


def test_claude_com_is_accepted_only_when_configured_explicitly():
    assert is_allowed_redirect_uri(CLAUDE_COM, (CLAUDE_AI,)) is False
    assert is_allowed_redirect_uri(CLAUDE_COM, (CLAUDE_AI, CLAUDE_COM)) is True


def test_allowlist_parsing_accepts_a_comma_list():
    assert parse_allowlist(f" {CLAUDE_AI} , {CLAUDE_COM} ") == (CLAUDE_AI, CLAUDE_COM)


@pytest.mark.parametrize("bad", [
    "", " , ", "https://*.claude.ai/cb", "http://claude.ai/cb", "https://claude.ai/cb?x=1",
    "https://claude.ai/cb#f", "https://user@claude.ai/cb", "claude.ai/cb", "https://claude.ai",
])
def test_allowlist_parsing_refuses_loose_entries(bad):
    with pytest.raises(ConfigError):
        parse_allowlist(bad)


@pytest.mark.parametrize("bad", BAD_URIS)
def test_registration_refuses_unlisted_redirects_and_stores_nothing(flow, store, bad):
    resp = flow.register(redirect_uris=[bad])
    assert resp.status_code == 400
    assert store.counts()["clients"] == 0


def test_registration_refuses_when_one_of_several_uris_is_bad(flow, store):
    resp = flow.register(redirect_uris=[CLAUDE_AI, "https://evil.example/cb"])
    assert resp.status_code == 400
    assert store.counts()["clients"] == 0


def test_registration_with_the_claude_address_works(flow, store):
    resp = flow.register(redirect_uris=[CLAUDE_AI])
    assert resp.status_code == 201
    assert resp.json()["redirect_uris"] == [CLAUDE_AI]
    assert store.counts()["clients"] == 1


def test_authorize_never_redirects_to_an_unregistered_address(flow):
    cid = flow.new_client_id()
    _, challenge = flow.pkce()
    resp = flow.authorize(cid, challenge, redirect_uri="https://evil.example/cb")
    assert resp.status_code == 400
    assert "location" not in resp.headers


def test_authorize_for_unknown_client_is_a_plain_error(flow):
    _, challenge = flow.pkce()
    resp = flow.authorize("no-such-client", challenge)
    assert resp.status_code == 400
    assert "location" not in resp.headers
