import pytest
from starlette.testclient import TestClient

from outbox_mcp.auth.config import AuthConfig
from outbox_mcp.auth.dev_server import build_app
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import ConfigError

CLAUDE_AI = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_COM = "https://claude.com/api/mcp/auth_callback"


def env(tmp_path, **override):
    base = {
        "OUTBOX_PUBLIC_BASE_URL": "https://outbox.example.test",
        "OUTBOX_OAUTH_PASSWORD_HASH": hash_password("pw", n=2**4),
        "OUTBOX_OAUTH_DB_PATH": str(tmp_path / "auth.db"),
    }
    base.update(override)
    return {k: v for k, v in base.items() if v is not None}


def test_minimal_environment(tmp_path):
    cfg = AuthConfig.from_env(env(tmp_path))
    assert cfg.resource_url == "https://outbox.example.test/mcp"
    assert cfg.issuer_url == "https://outbox.example.test"
    assert cfg.redirect_uris == (CLAUDE_AI,)
    assert cfg.scopes == ("mail:send",)
    assert cfg.access_token_ttl == 3600
    assert cfg.refresh_token_ttl == 90 * 86400


def test_trailing_slash_in_the_base_url_is_normalised(tmp_path):
    cfg = AuthConfig.from_env(env(tmp_path, OUTBOX_PUBLIC_BASE_URL="https://outbox.example.test/"))
    assert cfg.resource_url == "https://outbox.example.test/mcp"


def test_redirects_can_be_extended_explicitly(tmp_path):
    cfg = AuthConfig.from_env(env(tmp_path, OUTBOX_OAUTH_REDIRECT_URIS=f"{CLAUDE_AI},{CLAUDE_COM}"))
    assert cfg.redirect_uris == (CLAUDE_AI, CLAUDE_COM)


@pytest.mark.parametrize("name", ["OUTBOX_PUBLIC_BASE_URL", "OUTBOX_OAUTH_PASSWORD_HASH"])
def test_required_settings(tmp_path, name):
    with pytest.raises(ConfigError):
        AuthConfig.from_env(env(tmp_path, **{name: None}))


@pytest.mark.parametrize("url", ["http://outbox.example.test", "ftp://x", "outbox.example.test", "https://x/y?z=1"])
def test_base_url_must_be_plain_https(tmp_path, url):
    with pytest.raises(ConfigError):
        AuthConfig.from_env(env(tmp_path, OUTBOX_PUBLIC_BASE_URL=url))


def test_http_is_fine_for_localhost_testing(tmp_path):
    cfg = AuthConfig.from_env(env(tmp_path, OUTBOX_PUBLIC_BASE_URL="http://localhost:8430"))
    assert cfg.resource_url == "http://localhost:8430/mcp"


def test_malformed_password_hash_is_refused(tmp_path):
    with pytest.raises(ConfigError):
        AuthConfig.from_env(env(tmp_path, OUTBOX_OAUTH_PASSWORD_HASH="hunter2"))


@pytest.mark.parametrize("name,value", [
    ("OUTBOX_OAUTH_ACCESS_TTL_SECONDS", "0"), ("OUTBOX_OAUTH_ACCESS_TTL_SECONDS", "abc"),
    ("OUTBOX_OAUTH_REFRESH_TTL_DAYS", "-1"),
])
def test_lifetimes_must_be_positive_numbers(tmp_path, name, value):
    with pytest.raises(ConfigError):
        AuthConfig.from_env(env(tmp_path, **{name: value}))


def test_the_dev_server_builds_from_the_environment_and_serves_discovery(tmp_path):
    app = build_app(env(tmp_path))
    with TestClient(app, base_url="https://outbox.example.test", follow_redirects=False) as c:
        assert c.get("/.well-known/oauth-authorization-server").status_code == 200
        assert c.post("/mcp", json={}).status_code == 401
