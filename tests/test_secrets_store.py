"""SMTP password in the system keychain: lookup order, errors, admin command."""

import io

import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend

from outbox_mcp import admin_cli, secrets_store
from outbox_mcp.config import Config
from outbox_mcp.errors import ConfigError

ENV = {
    "OUTBOX_SMTP_HOST": "smtp.example.org", "OUTBOX_SMTP_USER": "me@example.org",
    "OUTBOX_FROM_ADDRESS": "me@example.org",
}


def test_round_trip_and_delete():
    secrets_store.set_smtp_password("h.example.org", "u", "pw1")
    assert secrets_store.get_smtp_password("h.example.org", "u") == "pw1"
    assert secrets_store.delete_smtp_password("h.example.org", "u") is True
    assert secrets_store.get_smtp_password("h.example.org", "u") is None
    assert secrets_store.delete_smtp_password("h.example.org", "u") is False


def test_empty_password_is_refused():
    with pytest.raises(ConfigError):
        secrets_store.set_smtp_password("h.example.org", "u", "")


def test_config_reads_the_keychain_when_the_environment_has_no_password():
    secrets_store.set_smtp_password("smtp.example.org", "me@example.org", "from-keychain")
    c = Config.from_env(ENV)
    assert c.smtp_password == "from-keychain" and c.smtp_password_source == "keychain"
    assert "from-keychain" not in repr(c)


def test_environment_password_takes_precedence():
    secrets_store.set_smtp_password("smtp.example.org", "me@example.org", "from-keychain")
    c = Config.from_env({**ENV, "OUTBOX_SMTP_PASSWORD": "from-env"})
    assert c.smtp_password == "from-env" and c.smtp_password_source == "environment"


def test_missing_password_names_both_ways():
    with pytest.raises(ConfigError) as e:
        Config.from_env(ENV)
    assert "OUTBOX_SMTP_PASSWORD" in str(e.value) and "smtp-password set" in str(e.value)


def test_no_keychain_means_not_found_and_a_clear_message():
    from keyring.backends import fail

    keyring.set_keyring(fail.Keyring())
    with pytest.raises(ConfigError) as e:
        Config.from_env(ENV)
    assert "OUTBOX_SMTP_PASSWORD" in str(e.value)
    with pytest.raises(ConfigError) as e:
        secrets_store.set_smtp_password("h.example.org", "u", "x")
    assert "No system keychain" in str(e.value)


def test_locked_keychain_gives_a_readable_error():
    class Locked(KeyringBackend):
        priority = 1

        def get_password(self, service, username):
            raise keyring.errors.KeyringLocked("locked")

        def set_password(self, *a):
            raise keyring.errors.KeyringLocked("locked")

        def delete_password(self, *a):
            raise keyring.errors.KeyringLocked("locked")

    keyring.set_keyring(Locked())
    with pytest.raises(ConfigError) as e:
        Config.from_env(ENV)
    assert "keychain could not be read" in str(e.value)


def _admin(*argv, env=ENV, secrets=(), stdin=None, monkeypatch=None):
    out = io.StringIO()
    answers = iter(secrets)
    if stdin is not None:
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    code = admin_cli.main(list(argv), env=dict(env), stdout=out, secret_func=lambda prompt: next(answers))
    return code, out.getvalue()


def test_admin_set_status_delete(monkeypatch):
    code, text = _admin("smtp-password", "set", secrets=["s3cret", "s3cret"])
    assert code == 0 and "s3cret" not in text
    assert Config.from_env(ENV).smtp_password == "s3cret"
    code, text = _admin("smtp-password", "status")
    assert code == 0 and "password stored" in text and "s3cret" not in text
    code, text = _admin("smtp-password", "delete")
    assert code == 0 and "Removed" in text
    code, text = _admin("smtp-password", "status")
    assert "no password stored" in text


def test_admin_set_from_stdin(monkeypatch):
    code, _ = _admin("smtp-password", "set", "--stdin", stdin="piped-pw\n", monkeypatch=monkeypatch)
    assert code == 0 and Config.from_env(ENV).smtp_password == "piped-pw"


def test_admin_set_refuses_different_entries():
    code, text = _admin("smtp-password", "set", secrets=["a", "b"])
    assert code == 1 and "differ" in text
    assert secrets_store.get_smtp_password("smtp.example.org", "me@example.org") is None


def test_admin_status_mentions_environment_precedence():
    code, text = _admin("smtp-password", "status", env={**ENV, "OUTBOX_SMTP_PASSWORD": "x"})
    assert "takes precedence" in text and "x\n" not in text.replace("takes precedence", "")


def test_admin_needs_host_and_user():
    code, text = _admin("smtp-password", "status", env={})
    assert code == 2


# --- macOS: /usr/bin/security instead of keyring -------------------------------------------

import re as _re
import subprocess as _sp


class FakeSecurity:
    """In-memory stand-in for /usr/bin/security: add via stdin (-X hex, -U), find -w, delete."""

    def __init__(self):
        self.items: dict[tuple[str, str], bytes] = {}
        self.calls: list[list[str]] = []
        self.stdin: list[str] = []
        self.refuse_adds = 0  # how many following adds silently do nothing (security -i hides failures)

    def __call__(self, args, stdin=None, timeout=None):
        self.calls.append(list(args))
        if stdin is not None:
            self.stdin.append(stdin)
        done = lambda code, out="", err="": _sp.CompletedProcess(args, code, out, err)
        if args[0] == "-i":
            m = _re.fullmatch(r'add-generic-password (-U )?-s "(.*)" -a "(.*)" -X ([0-9a-f]+)\n', stdin)
            assert m, stdin
            if self.refuse_adds:
                self.refuse_adds -= 1
                return done(0)
            key = (m[2], m[3])
            if key in self.items and not m[1]:
                return done(0)  # duplicate without -U: refused
            self.items[key] = bytes.fromhex(m[4])
            return done(0)
        key = (args[args.index("-s") + 1], args[args.index("-a") + 1])
        if args[0] == "find-generic-password":
            if key not in self.items:
                return done(44, err="The specified item could not be found in the keychain.")
            data = self.items[key]
            try:
                text = data.decode("ascii")
                ok = text.isprintable()
            except UnicodeDecodeError:
                ok = False
            return done(0, (text if ok else data.hex()) + "\n")
        if args[0] == "delete-generic-password":
            if key not in self.items:
                return done(44, err="The specified item could not be found in the keychain.")
            del self.items[key]
            return done(0)
        raise AssertionError(args)


@pytest.fixture
def mac(monkeypatch):
    fake = FakeSecurity()
    monkeypatch.setattr(secrets_store.sys, "platform", "darwin")
    monkeypatch.setattr(secrets_store.os.path, "exists", lambda p: True)
    monkeypatch.delenv("OUTBOX_KEYCHAIN_BACKEND", raising=False)
    monkeypatch.setattr(secrets_store, "_run", fake)
    return fake


def test_mac_round_trip_with_odd_characters_and_no_keyring(mac, monkeypatch):
    from keyring.backends import fail
    keyring.set_keyring(fail.Keyring())  # proves keyring is not touched on macOS
    for pw in ['Äö ü"x\'$HOME \\ 12!', "abcd", "deadbeef", "plain", "ß"]:
        secrets_store.set_smtp_password("h.example.org", "u@example.org", pw)
        assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == pw
    assert secrets_store.backend_name() == "macos.security"
    assert secrets_store.delete_smtp_password("h.example.org", "u@example.org") is True
    assert secrets_store.delete_smtp_password("h.example.org", "u@example.org") is False
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") is None


def test_mac_password_goes_over_stdin_not_into_arguments(mac):
    secrets_store.set_smtp_password("h.example.org", "u@example.org", "SECRET-pw")
    assert not any("SECRET-pw" in a or "SECRET-pw".encode().hex() in a for c in mac.calls for a in c)
    assert "SECRET-pw" not in "".join(mac.stdin)


def test_mac_replaces_an_older_keyring_item_and_reads_legacy_values(mac):
    account = secrets_store.account_name("h.example.org", "u@example.org")
    mac.items[(secrets_store.SERVICE, account)] = "alt".encode()
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "alt"
    # reading migrated it: it is now our own item and reads the same
    assert mac.items[(secrets_store.SERVICE, account)].startswith(b"ma1:")
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "alt"
    mac.items[(secrets_store.SERVICE, account)] = "Äö".encode()  # non-ASCII comes back as hex
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "Äö"
    secrets_store.set_smtp_password("h.example.org", "u@example.org", "neu")
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "neu"


def test_mac_failed_write_is_reported(mac, monkeypatch):
    real = mac.__call__
    monkeypatch.setattr(secrets_store, "_run", lambda a, stdin=None, timeout=None: _sp.CompletedProcess(a, 0, "", "") if a[0] == "-i" else real(a, stdin))
    with pytest.raises(ConfigError):
        secrets_store.set_smtp_password("h.example.org", "u@example.org", "x")


def test_mac_rejects_addresses_the_keychain_cannot_quote(mac):
    with pytest.raises(ConfigError):
        secrets_store.set_smtp_password("h.example.org", 'u"@example.org', "x")


def test_mac_can_be_switched_back_to_keyring(mac, monkeypatch):
    monkeypatch.setenv("OUTBOX_KEYCHAIN_BACKEND", "keyring")
    assert secrets_store._use_security() is False


def test_mac_a_slow_keychain_gives_a_clear_message(mac, monkeypatch):
    def slow(args, stdin=None, timeout=None):
        raise ConfigError("The keychain did not answer in time.")
    monkeypatch.setattr(secrets_store, "_run", slow)
    with pytest.raises(ConfigError):
        secrets_store.get_smtp_password("h.example.org", "u@example.org")


def test_run_turns_a_timeout_into_a_config_error(monkeypatch):
    def boom(*a, **k):
        raise _sp.TimeoutExpired("security", 1)
    monkeypatch.setattr(secrets_store.subprocess, "run", boom)
    with pytest.raises(ConfigError, match="did not answer"):
        secrets_store._run(["find-generic-password"])


def test_mac_updating_an_existing_entry_never_deletes_it(mac):
    secrets_store.set_smtp_password("h.example.org", "u@example.org", "old")
    mac.calls.clear()
    secrets_store.set_smtp_password("h.example.org", "u@example.org", "new")
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "new"
    assert not any(c[0] == "delete-generic-password" for c in mac.calls)


def test_mac_a_failed_update_keeps_the_old_password(mac):
    secrets_store.set_smtp_password("h.example.org", "u@example.org", "old")
    mac.refuse_adds = 10
    with pytest.raises(ConfigError, match="old one is kept"):
        secrets_store.set_smtp_password("h.example.org", "u@example.org", "new")
    mac.refuse_adds = 0
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "old"


def test_mac_replacing_an_old_entry_retries_the_add(mac):
    account = secrets_store.account_name("h.example.org", "u@example.org")
    mac.items[(secrets_store.SERVICE, account)] = b"alt"
    mac.refuse_adds = 2  # first two adds do nothing, the third works
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") == "alt"
    assert mac.items[(secrets_store.SERVICE, account)].startswith(b"ma1:")


def test_mac_delete_removes_every_duplicate_but_never_loops_forever(monkeypatch):
    codes = iter([0, 0, 0, 44])
    seen = []

    def runner(args, stdin=None, timeout=None):
        seen.append(args[0])
        return _sp.CompletedProcess(args, next(codes), "", "")

    monkeypatch.setattr(secrets_store, "_run", runner)
    assert secrets_store._sec_delete("a") is True and seen == ["delete-generic-password"] * 4
    monkeypatch.setattr(secrets_store, "_run", lambda a, stdin=None, timeout=None: _sp.CompletedProcess(a, 0, "", ""))
    with pytest.raises(ConfigError, match="ten deletions"):
        secrets_store._sec_delete("a")


def test_mac_a_locked_keychain_has_its_own_message(mac, monkeypatch):
    monkeypatch.setattr(
        secrets_store, "_run",
        lambda a, stdin=None, timeout=None: _sp.CompletedProcess(a, 36, "", "User interaction is not allowed."),
    )
    with pytest.raises(ConfigError, match="locked"):
        secrets_store.get_smtp_password("h.example.org", "u@example.org")


def test_mac_not_found_is_looked_up_twice_in_case_another_process_is_writing(mac):
    secrets_store.get_smtp_password("h.example.org", "u@example.org")
    assert [c[0] for c in mac.calls].count("find-generic-password") == 2


def test_password_length_is_limited():
    with pytest.raises(ConfigError, match="longer than"):
        secrets_store.set_smtp_password("h.example.org", "u", "x" * 1025)


def test_the_keyring_way_back_understands_values_written_through_security(monkeypatch):
    keyring.set_password(secrets_store.SERVICE, secrets_store.account_name("h.example.org", "u"), "ma1:" + "pässword".encode().hex())
    assert secrets_store.get_smtp_password("h.example.org", "u") == "pässword"


def test_mac_a_second_process_waits_for_the_lock(mac, monkeypatch, tmp_path):
    import fcntl
    import os

    monkeypatch.setenv("OUTBOX_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(secrets_store, "LOCK_WAIT_SECONDS", 0.3)
    fd = os.open(tmp_path / ".keychain.lock", os.O_CREAT | os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(ConfigError, match="Another part"):
            secrets_store.get_smtp_password("h.example.org", "u@example.org")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    assert secrets_store.get_smtp_password("h.example.org", "u@example.org") is None
