"""Phone use: remote.json, the background service (LaunchAgent) and its commands. launchctl is never run:
a recording stand-in takes its place, and nothing outside the temporary folders is touched."""

import io
import json
import os
import plistlib
import stat
import subprocess
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from outbox_mcp import remote, setup_cli
from outbox_mcp.auth.passwords import verify_password
from outbox_mcp.setup_tasks import SetupError

PASSPHRASE = "eine-lange-passphrase-123"
ADDRESS = "https://mein-mac.tail1234.ts.net"


class Launchctl:
    def __init__(self, bootstrap_fails=False, state="running"):
        self.calls = []
        self.bootstrap_fails = bootstrap_fails
        self.state = state

    def __call__(self, args):
        self.calls.append(list(args))
        verb = args[1]
        if verb == "bootout":
            return subprocess.CompletedProcess(args, 3, "", "Boot-out failed: 3: No such process")
        if verb == "bootstrap" and self.bootstrap_fails:
            return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error")
        if verb == "print":
            return subprocess.CompletedProcess(args, 0, f"{remote.LABEL} = {{\n\tstate = {self.state}\n}}\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def verbs(self):
        return [c[1] for c in self.calls]


@pytest.fixture
def env(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "mailbox.json").write_text(json.dumps(
        {"email": "me@example.org", "host": "smtp.example.org", "port": 587, "security": "starttls"}))
    return {"OUTBOX_DATA_DIR": str(data), "HOME": str(tmp_path / "home")}


@pytest.fixture
def program(tmp_path):
    path = tmp_path / "Programme" / "Mail Assistant"
    path.parent.mkdir()
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)
    return str(path)


def enable(env, program, launchctl, **kw):
    return remote.enable(env, program, kw.pop("address", ADDRESS), kw.pop("passphrase", PASSPHRASE),
                         run=launchctl, uid=501, platform="darwin", **kw)


# --- remote.json, read by the server --------------------------------------------------------

def write_remote(env, **data):
    with open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"), "w") as f:
        json.dump(data, f)


def test_file_fills_in_what_the_environment_leaves_empty(env):
    write_remote(env, public_base_url="https://Mein-Mac.ts.net/", password_hash="abc")
    merged = remote.with_remote_file(env)
    assert merged["OUTBOX_PUBLIC_BASE_URL"] == "https://Mein-Mac.ts.net/"
    assert merged["OUTBOX_APPROVAL_HOST"] == "mein-mac.ts.net"  # always the host of the public address
    assert merged["OUTBOX_OAUTH_PASSWORD_HASH"] == "abc"


def test_environment_wins_over_the_file(env):
    write_remote(env, public_base_url="https://file.ts.net", password_hash="from-file")
    env = {**env, "OUTBOX_PUBLIC_BASE_URL": "https://env.example.org", "OUTBOX_OAUTH_PASSWORD_HASH": "from-env",
           "OUTBOX_APPROVAL_HOST": "env.example.org"}
    merged = remote.with_remote_file(env)
    assert merged["OUTBOX_PUBLIC_BASE_URL"] == "https://env.example.org"
    assert merged["OUTBOX_OAUTH_PASSWORD_HASH"] == "from-env"
    assert merged["OUTBOX_APPROVAL_HOST"] == "env.example.org"


def test_environment_address_without_a_host_does_not_get_one_from_the_file(env):
    write_remote(env, public_base_url="https://file.ts.net", password_hash="x")
    merged = remote.with_remote_file({**env, "OUTBOX_PUBLIC_BASE_URL": "https://env.example.org"})
    assert "OUTBOX_APPROVAL_HOST" not in merged


@pytest.mark.parametrize("content", ["", "kein json", "[1, 2]", "null", '{"public_base_url": 5, "password_hash": []}'])
def test_broken_file_changes_nothing(env, content):
    with open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"), "w") as f:
        f.write(content)
    assert dict(remote.with_remote_file(env)) == env


def test_without_file_or_data_folder_nothing_changes(env, tmp_path):
    assert remote.with_remote_file(env) == env
    assert remote.with_remote_file({}) == {}


def test_server_starts_from_the_two_files_alone(env, tmp_path):
    from outbox_mcp.http_server import build_app_from_env

    from outbox_mcp.auth.passwords import hash_password

    write_remote(env, public_base_url=ADDRESS, password_hash=hash_password(PASSPHRASE, n=2**4))
    app = build_app_from_env({**env, "OUTBOX_SMTP_PASSWORD": "geheim-123"})
    with TestClient(app, base_url=ADDRESS) as c:
        assert c.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                      headers={"Accept": "application/json, text/event-stream"}).status_code == 401
        meta = c.get("/.well-known/oauth-protected-resource/mcp")
        assert meta.status_code == 200
        assert meta.json()["resource"] == ADDRESS + "/mcp"


# --- checks --------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "", "mein-mac.ts.net", "http://mein-mac.ts.net", "https://localhost", "https://127.0.0.1", "https://mac.local",
    "https://x.ts.net/pfad", "https://x.ts.net:8443", "https://user@x.ts.net", "https://x.ts.net?a=1", "https://x.ts.net:abc",
])
def test_unusable_addresses_are_refused(bad):
    with pytest.raises(SetupError):
        remote.clean_address(bad)


def test_address_is_normalised():
    assert remote.clean_address("  https://Mein-Mac.TS.net/ ") == "https://mein-mac.ts.net"


@pytest.mark.parametrize("bad", ["", "kurz", "elf-zeichen", " führende-leerzeichen-123", "x" * 2000])
def test_weak_or_odd_passphrases_are_refused(bad):
    with pytest.raises(SetupError):
        remote.check_passphrase(bad)


# --- enable ---------------------------------------------------------------------------------

def test_enable_writes_hash_not_passphrase_and_a_slim_plist(env, program):
    lc = Launchctl()
    answer = enable(env, program, lc)
    assert answer["address"] == ADDRESS

    path = os.path.join(env["OUTBOX_DATA_DIR"], "remote.json")
    text = open(path).read()
    assert PASSPHRASE not in text
    data = json.loads(text)
    assert data["public_base_url"] == ADDRESS
    assert verify_password(PASSPHRASE, data["password_hash"])
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

    plist_file = remote.plist_path(env)
    raw = plist_file.read_bytes()
    assert PASSPHRASE.encode() not in raw and data["password_hash"].encode() not in raw
    plist = plistlib.loads(raw)
    assert plist["Label"] == remote.LABEL
    assert plist["ProgramArguments"] == [program, "serve"]
    assert plist["EnvironmentVariables"] == {"OUTBOX_DATA_DIR": env["OUTBOX_DATA_DIR"]}  # nothing else, no secret
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
    assert plist["ThrottleInterval"] == 10
    logs = str(remote.log_folder(env))
    assert plist["StandardOutPath"].startswith(logs) and plist["StandardErrorPath"].startswith(logs)
    assert logs.endswith("/Library/Logs/Mail Assistant")
    assert stat.S_IMODE(os.stat(remote.log_folder(env)).st_mode) == 0o700


def test_enable_stops_the_old_service_before_it_starts_the_new_one(env, program):
    lc = Launchctl()
    enable(env, program, lc)
    assert lc.verbs() == ["bootout", "bootstrap"]
    assert lc.calls[0][2] == f"gui/501/{remote.LABEL}"
    assert lc.calls[1][2:] == ["gui/501", str(remote.plist_path(env))]
    assert lc.calls[0][0] == lc.calls[1][0] == "/bin/launchctl"
    enable(env, program, lc)  # again: restart with the new settings
    assert lc.verbs() == ["bootout", "bootstrap"] * 2


def test_the_passphrase_never_appears_in_a_command(env, program):
    lc = Launchctl()
    enable(env, program, lc)
    assert PASSPHRASE not in json.dumps(lc.calls)


def test_a_failed_start_leaves_no_service_behind(env, program):
    lc = Launchctl(bootstrap_fails=True)
    with pytest.raises(SetupError, match="nicht starten.*Input/output error"):
        enable(env, program, lc)
    assert not remote.plist_path(env).exists()
    assert lc.verbs() == ["bootout", "bootstrap", "bootout"]


@pytest.mark.parametrize("what", ["address", "passphrase"])
def test_bad_input_changes_nothing(env, program, what):
    lc = Launchctl()
    with pytest.raises(SetupError):
        enable(env, program, lc, **({"address": "http://x"} if what == "address" else {"passphrase": "kurz"}))
    assert lc.calls == []
    assert not remote.plist_path(env).exists()
    assert not os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))


def test_enable_needs_a_mailbox(env, program):
    os.unlink(os.path.join(env["OUTBOX_DATA_DIR"], "mailbox.json"))
    with pytest.raises(SetupError, match="Postfach"):
        enable(env, program, Launchctl())


@pytest.mark.parametrize("command", [
    "", "relativ/pfad", "/gibt/es/nicht", "/private/var/AppTranslocation/ABC/d/Mail Assistant.app/Contents/MacOS/x",
    "/Volumes/Mail Assistant/Mail Assistant.app/Contents/MacOS/x",
])
def test_the_program_path_must_be_a_real_installed_one(env, command):
    lc = Launchctl()
    with pytest.raises(SetupError):
        enable(env, command, lc)
    assert lc.calls == []


def test_other_systems_are_told_plainly(env, program):
    with pytest.raises(SetupError, match="nur für den Mac"):
        remote.enable(env, program, ADDRESS, PASSPHRASE, run=Launchctl(), platform="linux")
    with pytest.raises(SetupError, match="nur für den Mac"):
        remote.disable(env, run=Launchctl(), platform="win32")
    assert remote.status(env, platform="linux") == {"supported": False, "enabled": False}


# --- status ---------------------------------------------------------------------------------

def test_status_before_and_after(env, program):
    before = remote.status(env, program, run=Launchctl(), uid=501, platform="darwin")
    assert before["enabled"] is False and before["running"] is False and before["address"] is None

    enable(env, program, Launchctl())
    after = remote.status(env, program, run=Launchctl(), uid=501, platform="darwin")
    assert after["enabled"] and after["loaded"] and after["running"]
    assert after["address"] == ADDRESS
    assert after["program_ok"] and after["program_matches"]
    dumped = json.dumps(after)
    assert "scrypt" not in dumped and PASSPHRASE not in dumped  # never the hash


def test_status_tells_a_stopped_service_from_a_running_one(env, program):
    enable(env, program, Launchctl())
    waiting = remote.status(env, program, run=Launchctl(state="waiting"), uid=501, platform="darwin")
    assert waiting["loaded"] is True and waiting["running"] is False


def test_status_notices_a_moved_program(env, program):
    enable(env, program, Launchctl())
    os.unlink(program)
    moved = remote.status(env, program, run=Launchctl(), uid=501, platform="darwin")
    assert moved["enabled"] is True and moved["program_ok"] is False
    other = remote.status(env, "/Applications/Mail Assistant.app/Contents/MacOS/x", run=Launchctl(), uid=501, platform="darwin")
    assert other["program_matches"] is False


def test_status_survives_a_missing_launchctl(env, program):
    enable(env, program, Launchctl())

    def broken(args):
        raise FileNotFoundError("launchctl")

    state = remote.status(env, program, run=broken, uid=501, platform="darwin")
    assert state["enabled"] is True and state["loaded"] is False


# --- disable --------------------------------------------------------------------------------

def test_disable_removes_the_service_and_keeps_the_rest(env, program):
    enable(env, program, Launchctl())
    other = os.path.join(env["OUTBOX_DATA_DIR"], "recipients.json")
    open(other, "w").write("{}")
    lc = Launchctl()
    assert remote.disable(env, run=lc, uid=501, platform="darwin") is True
    assert lc.verbs() == ["bootout"]
    assert not remote.plist_path(env).exists()
    assert not os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))
    assert os.path.exists(other) and os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "mailbox.json"))


def test_disable_twice_is_fine(env):
    assert remote.disable(env, run=Launchctl(), uid=501, platform="darwin") is False
    assert remote.disable(env, run=Launchctl(), uid=501, platform="darwin") is False


# --- the command line -----------------------------------------------------------------------

def test_cli_takes_the_passphrase_from_stdin_and_never_prints_it(env, program, monkeypatch):
    lc = Launchctl()
    monkeypatch.setattr(remote, "_run", lc)
    monkeypatch.setattr(remote.sys, "platform", "darwin")
    monkeypatch.setattr(remote.os, "getuid", lambda: 501)
    argv = ["remote-enable", "--command", program, "--url", ADDRESS]
    assert PASSPHRASE not in " ".join(argv)
    out = io.StringIO()
    code = setup_cli.main(argv, env=env, stdin=io.StringIO(PASSPHRASE + "\n"), stdout=out)
    assert code == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is True and answer["address"] == ADDRESS
    assert PASSPHRASE not in out.getvalue() and "scrypt" not in out.getvalue()


def test_cli_reports_a_problem_as_json(env, program, monkeypatch):
    monkeypatch.setattr(remote.sys, "platform", "darwin")
    out = io.StringIO()
    code = setup_cli.main(["remote-enable", "--command", program, "--url", "http://x"], env=env,
                          stdin=io.StringIO("irgendwas-langes-123\n"), stdout=out)
    assert code == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is False and "https" in answer["reason"]
