import json

import pytest

from outbox_mcp.errors import ConfigError, RecipientNotAllowed
from outbox_mcp.security.recipients import RecipientList


def test_resolve_by_name_alias_address(recipients):
    assert recipients.resolve("ferdinand").address == "ferdinand@example.org"
    assert recipients.resolve("MYSELF").name == "Me"
    assert recipients.resolve("me@example.com").name == "Me"


@pytest.mark.parametrize("key", ["stranger@example.com", "", None, "Fer", "Me, Ferdinand"])
def test_unknown_is_refused(recipients, key):
    with pytest.raises(RecipientNotAllowed):
        recipients.resolve(key)


def test_from_file(tmp_path):
    p = tmp_path / "r.json"
    p.write_text(json.dumps({"recipients": [{"name": "A", "address": "a@example.com", "aliases": ["x"]}]}))
    assert RecipientList.from_file(p).resolve("x").name == "A"


@pytest.mark.parametrize("data", [
    [], {"recipients": "no"}, {"recipients": [{"name": "A", "address": "bad"}]},
    {"recipients": [{"name": "", "address": "a@example.com"}]},
    {"recipients": [{"name": "A", "address": "a@example.com"}, {"name": "B", "address": "A@example.com"}]},
    {"recipients": [{"name": "A", "address": "a@example.com"}, {"name": "a", "address": "b@example.com"}]},
])
def test_invalid_data(data):
    with pytest.raises(ConfigError):
        RecipientList.from_data(data)


def test_missing_and_broken_file(tmp_path):
    with pytest.raises(ConfigError):
        RecipientList.from_file(tmp_path / "nope.json")
    p = tmp_path / "bad.json"
    p.write_text("{")
    with pytest.raises(ConfigError):
        RecipientList.from_file(p)


def test_is_allowed_address(recipients):
    assert recipients.is_allowed_address("ME@example.com")
    assert not recipients.is_allowed_address("x@example.com")
