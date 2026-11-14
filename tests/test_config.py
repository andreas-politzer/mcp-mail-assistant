import pytest

from outbox_mcp.config import Config
from outbox_mcp.errors import ConfigError

BASE = {
    "OUTBOX_SMTP_HOST": "smtp.example.com", "OUTBOX_SMTP_USER": "me@example.com",
    "OUTBOX_SMTP_PASSWORD": "secret-pw", "OUTBOX_FROM_ADDRESS": "me@example.com",
}


def test_defaults():
    c = Config.from_env(BASE)
    assert (c.smtp_port, c.smtp_security, c.draft_ttl_seconds, c.max_sends_per_hour) == (587, "starttls", 600, 3)


def test_ssl_default_port():
    assert Config.from_env({**BASE, "OUTBOX_SMTP_SECURITY": "ssl"}).smtp_port == 465


def test_password_not_in_repr():
    assert "secret-pw" not in repr(Config.from_env(BASE))


@pytest.mark.parametrize("change", [
    {"OUTBOX_SMTP_HOST": ""}, {"OUTBOX_SMTP_PASSWORD": ""}, {"OUTBOX_FROM_ADDRESS": "bad"},
    {"OUTBOX_SMTP_SECURITY": "plain"}, {"OUTBOX_SMTP_SECURITY": "none"},
    {"OUTBOX_MAX_SENDS_PER_DAY": "0"}, {"OUTBOX_MAX_SENDS_PER_DAY": "abc"},
    {"OUTBOX_SMTP_PORT": "-1"},
])
def test_invalid(change):
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, **change})


def test_security_none_only_localhost():
    c = Config.from_env({**BASE, "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_SECURITY": "none",
                         "OUTBOX_SMTP_PORT": "2525", "OUTBOX_SMTP_USER": "", "OUTBOX_SMTP_PASSWORD": ""})
    assert c.smtp_security == "none"
