import pytest

from outbox_mcp.errors import ValidationError
from outbox_mcp.security.validation import (
    validate_address, validate_body, validate_display_name, validate_subject,
)


@pytest.mark.parametrize("bad", [
    "a\nb", "a\rb", "a\x00b", "a b", "Hi\r\nBcc: x@y.de", "", "   ",
])
def test_subject_rejects(bad):
    with pytest.raises(ValidationError):
        validate_subject(bad, 150)


def test_subject_ok_and_limits():
    assert validate_subject("  Hallo Wörld ", 150) == "Hallo Wörld"
    with pytest.raises(ValidationError):
        validate_subject("x" * 151, 150)
    with pytest.raises(ValidationError):
        validate_subject(5, 150)


def test_body_normalises_and_rejects():
    assert validate_body("a\r\nb\rc", 100) == "a\nb\nc"
    assert validate_body("a\tb", 100) == "a\tb"
    for bad in ["", "  \n", "a\x00b", "a\x1bb"]:
        with pytest.raises(ValidationError):
            validate_body(bad, 100)
    with pytest.raises(ValidationError):
        validate_body("x" * 101, 100)


@pytest.mark.parametrize("bad", [
    "no-at.example.com", "a@b", "a b@example.com", "Name <a@example.com>",
    "a@example.com, b@example.com", "a@example.com\nBcc: x@y.de", ".a@example.com",
    "a..b@example.com", "a@-example.com", "", "(c)a@example.com", "a@example.com;b@example.com",
])
def test_address_rejects(bad):
    with pytest.raises(ValidationError):
        validate_address(bad)


def test_address_ok():
    assert validate_address(" first.last+tag@sub.example.com ") == "first.last+tag@sub.example.com"


def test_display_name():
    assert validate_display_name(" Ferdinand ") == "Ferdinand"
    with pytest.raises(ValidationError):
        validate_display_name("a\nb")
