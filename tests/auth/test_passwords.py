import pytest

from outbox_mcp.auth.passwords import hash_password, verify_password


def test_roundtrip():
    stored = hash_password("s3cret pass", n=2**4)
    assert verify_password("s3cret pass", stored) is True


def test_wrong_password_fails():
    stored = hash_password("s3cret pass", n=2**4)
    assert verify_password("s3cret pasS", stored) is False
    assert verify_password("", stored) is False


def test_hashes_are_salted():
    assert hash_password("same", n=2**4) != hash_password("same", n=2**4)


@pytest.mark.parametrize("garbage", [
    "", "plain", "scrypt:x", "scrypt:16:8:1:!!:!!", "scrypt:16:8:1:AAAA", "md5:16:8:1:AAAA:BBBB",
    "scrypt:-4:8:1:AAAA:BBBB", "scrypt:16:0:1:AAAA:BBBB", "scrypt:15:8:1:AAAA:BBBB",
])
def test_malformed_hashes_never_verify(garbage):
    assert verify_password("anything", garbage) is False


def test_hash_is_safe_inside_an_env_file():
    # uv's env-file parser drops "$name", stops at unquoted spaces and chokes on quotes.
    stored = hash_password("pw", n=2**4)
    assert not any(ch in stored for ch in "$ \t'\"\\#")


def test_empty_password_is_refused():
    with pytest.raises(ValueError):
        hash_password("", n=2**4)


def test_default_cost_is_not_trivial():
    stored = hash_password("pw")
    assert int(stored.split(":")[1]) >= 2**14
