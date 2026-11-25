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

REAL_PORT_BUSY = remote._port_busy
PASSPHRASE = "eine-lange-passphrase-123"
ADDRESS = "https://mein-mac.example.ts.net"


class Launchctl:
    """lag: the old instance stays listed for that many looks after bootout, and bootstrap fails meanwhile (as
    launchd really does while it is still taking the old one down). bootstrap_failures: the first n starts fail."""

    def __init__(self, bootstrap_fails=False, state="running", stays_listed=False, lag=0, bootstrap_failures=0):
        self.calls = []
        self.bootstrap_fails = bootstrap_fails
        self.state = state
        self.loaded = True  # a service that is already there; bootout takes it away, bootstrap brings it back
        self.stays_listed = stays_listed
        self.lag = lag
        self.draining = 0
        self.failures_left = bootstrap_failures

    def __call__(self, args):
        self.calls.append(list(args))
        verb = args[1]
        if verb == "bootout":
            self.loaded = self.stays_listed or self.lag > 0
            self.draining = self.lag
            return subprocess.CompletedProcess(args, 3, "", "Boot-out failed: 3: No such process")
        if verb == "bootstrap" and (self.bootstrap_fails or (self.loaded and self.lag) or self.failures_left > 0):
            self.failures_left = max(0, self.failures_left - 1)
            return subprocess.CompletedProcess(args, 5, "", "Bootstrap failed: 5: Input/output error")
        if verb == "bootstrap":
            self.loaded = True
        if verb == "print":
            if not self.loaded:
                return subprocess.CompletedProcess(args, 113, "", "Could not find service")
            if self.draining > 0:
                self.draining -= 1
                if self.draining == 0:
                    self.loaded = False  # gone after this look
            return subprocess.CompletedProcess(args, 0, f"{remote.LABEL} = {{\n\tstate = {self.state}\n}}\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    def verbs(self):
        return [c[1] for c in self.calls]


@pytest.fixture(autouse=True)
def _port_is_free(monkeypatch):
    """Where the server really runs, 8430 answers. A test must never wait for, or depend on, that."""
    monkeypatch.setattr(remote, "_port_busy", lambda port=remote.PORT: False)


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
    kw.setdefault("sleep", lambda seconds: None)
    kw.setdefault("port_busy", lambda: False)
    return remote.enable(env, program, kw.pop("address", ADDRESS), kw.pop("passphrase", PASSPHRASE),
                         run=launchctl, uid=501, platform="darwin", **kw)


# --- remote.json, read by the server --------------------------------------------------------

def write_remote(env, **data):
    with open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"), "w") as f:
        json.dump(data, f)


def test_file_fills_in_what_the_environment_leaves_empty(env):
    write_remote(env, public_base_url="https://Mein-Mac.example.ts.net/", password_hash="abc")
    merged = remote.with_remote_file(env)
    assert merged["OUTBOX_PUBLIC_BASE_URL"] == "https://Mein-Mac.example.ts.net/"
    assert merged["OUTBOX_APPROVAL_HOST"] == "mein-mac.example.ts.net"  # always the host of the public address
    assert merged["OUTBOX_OAUTH_PASSWORD_HASH"] == "abc"


def test_file_can_switch_on_neutral_tool_names(env):
    write_remote(env, public_base_url="https://f.example.ts.net", password_hash="abc", tool_names="neutral")
    assert remote.with_remote_file(env)["OUTBOX_TOOL_NAMES"] == "neutral"
    assert remote.with_remote_file({**env, "OUTBOX_TOOL_NAMES": "off"})["OUTBOX_TOOL_NAMES"] == "off"


def test_file_without_tool_names_leaves_the_real_names(env):
    write_remote(env, public_base_url="https://f.example.ts.net", password_hash="abc")
    assert "OUTBOX_TOOL_NAMES" not in remote.with_remote_file(env)


def test_enable_switches_neutral_tool_names_on(env, program):
    enable(env, program, Launchctl())
    data = json.loads(open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json")).read())
    assert data["tool_names"] == "neutral"
    assert remote.with_remote_file(env)["OUTBOX_TOOL_NAMES"] == "neutral"


def test_environment_wins_over_the_file(env):
    write_remote(env, public_base_url="https://file.example.ts.net", password_hash="from-file")
    env = {**env, "OUTBOX_PUBLIC_BASE_URL": "https://env.example.org", "OUTBOX_OAUTH_PASSWORD_HASH": "from-env",
           "OUTBOX_APPROVAL_HOST": "env.example.org"}
    merged = remote.with_remote_file(env)
    assert merged["OUTBOX_PUBLIC_BASE_URL"] == "https://env.example.org"
    assert merged["OUTBOX_OAUTH_PASSWORD_HASH"] == "from-env"
    assert merged["OUTBOX_APPROVAL_HOST"] == "env.example.org"


def test_environment_address_without_a_host_does_not_get_one_from_the_file(env):
    write_remote(env, public_base_url="https://file.example.ts.net", password_hash="x")
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
    "", "mein-mac.example.ts.net", "http://mein-mac.example.ts.net", "https://localhost", "https://127.0.0.1", "https://mac.local",
    "https://x.example.ts.net/pfad", "https://x.example.ts.net:8443", "https://user@x.example.ts.net", "https://x.example.ts.net?a=1", "https://x.example.ts.net:abc",
])
def test_unusable_addresses_are_refused(bad):
    with pytest.raises(SetupError):
        remote.clean_address(bad)


def test_address_is_normalised():
    assert remote.clean_address("  https://Mein-Mac.Example.TS.net/ ") == "https://mein-mac.example.ts.net"


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
    assert lc.verbs() == ["bootout", "print", "bootstrap"]  # looks until it is gone, then starts
    assert lc.calls[0][2] == f"gui/501/{remote.LABEL}"
    assert lc.calls[2][2:] == ["gui/501", str(remote.plist_path(env))]
    assert lc.calls[0][0] == lc.calls[2][0] == "/bin/launchctl"
    enable(env, program, lc)  # again: restart with the new settings
    assert lc.verbs() == ["bootout", "print", "bootstrap"] * 2


def test_the_passphrase_never_appears_in_a_command(env, program):
    lc = Launchctl()
    enable(env, program, lc)
    assert PASSPHRASE not in json.dumps(lc.calls)


def test_a_failed_start_leaves_no_service_behind(env, program):
    lc = Launchctl(bootstrap_fails=True)
    with pytest.raises(SetupError, match="nicht starten.*Input/output error"):
        enable(env, program, lc)
    assert not remote.plist_path(env).exists()
    assert lc.verbs() == ["bootout", "print", "bootstrap", "bootstrap", "bootout"]  # one more try, then clean up


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

QUICK = {"sleep": lambda seconds: None, "port_busy": lambda: False}


def disable(env, launchctl, **kw):
    return remote.disable(env, run=launchctl, uid=501, platform="darwin", **{**QUICK, **kw})


def test_disable_removes_the_service_and_keeps_the_rest(env, program):
    enable(env, program, Launchctl())
    other = os.path.join(env["OUTBOX_DATA_DIR"], "recipients.json")
    open(other, "w").write("{}")
    lc = Launchctl()
    assert disable(env, lc) is True
    assert lc.verbs() == ["bootout", "print"]  # stopped, then asked once more: really gone
    assert not remote.plist_path(env).exists()
    assert not os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))
    assert os.path.exists(other) and os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "mailbox.json"))


def test_disable_twice_is_fine(env):
    assert disable(env, Launchctl()) is False
    assert disable(env, Launchctl()) is False


def test_disable_waits_until_the_service_is_gone(env, program):
    enable(env, program, Launchctl())

    class SlowToLeave(Launchctl):
        listed_for = 3

        def __call__(self, args):
            if args[1] == "print" and self.listed_for > 0:
                self.listed_for -= 1
                self.calls.append(list(args))
                return subprocess.CompletedProcess(args, 0, "", "")
            return super().__call__(args)

    lc = SlowToLeave()
    pauses = []
    assert disable(env, lc, sleep=pauses.append) is True
    assert lc.verbs().count("print") == 4 and len(pauses) == 3
    assert not remote.plist_path(env).exists()


def test_disable_keeps_the_files_when_the_service_does_not_leave(env, program):
    enable(env, program, Launchctl())
    with pytest.raises(SetupError, match="nicht rechtzeitig beendet"):
        disable(env, Launchctl(stays_listed=True))
    assert remote.plist_path(env).exists()  # nothing deleted: asking again works
    assert os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))
    assert disable(env, Launchctl()) is True


def test_disable_gives_the_port_time_but_a_busy_port_is_not_an_error(env, program):
    enable(env, program, Launchctl())
    answers = iter([True, True, False])
    pauses = []
    assert disable(env, Launchctl(), port_busy=lambda: next(answers), sleep=pauses.append) is True
    assert len(pauses) == 2  # waited for the port twice, then it was free
    enable(env, program, Launchctl())
    pauses.clear()
    assert disable(env, Launchctl(), port_busy=lambda: True, sleep=pauses.append) is True  # another program holds it
    assert len(pauses) == 20


def test_the_real_port_check_sees_a_listener():
    import socket

    # A free port chosen by the system, never the real one: on a computer where the server runs, 8430 is taken.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen()
        port = probe.getsockname()[1]
        assert REAL_PORT_BUSY(port) is True
    assert REAL_PORT_BUSY(port) is False


# --- the passphrase placeholder ------------------------------------------------------------

def read_remote(env):
    return json.load(open(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json")))


def test_a_chosen_passphrase_is_marked_as_real(env, program):
    answer = enable(env, program, Launchctl())
    assert answer["passphrase_placeholder"] is False
    assert read_remote(env)["passphrase_placeholder"] is False


def test_without_a_passphrase_a_random_one_is_hashed_and_thrown_away(env, program):
    answer = enable(env, program, Launchctl(), passphrase=None)
    data = read_remote(env)
    assert answer["passphrase_placeholder"] is True and data["passphrase_placeholder"] is True
    assert remote.is_valid_hash(data["password_hash"])
    assert not verify_password(PASSPHRASE, data["password_hash"])
    first = data["password_hash"]
    enable(env, program, Launchctl(), passphrase=None)  # again: a new throw-away, still unusable
    assert read_remote(env)["password_hash"] != first
    assert remote.status(env, program, run=Launchctl(), uid=501, platform="darwin")["passphrase_placeholder"] is True


def test_a_chosen_passphrase_survives_a_later_tunnel_step(env, program):
    enable(env, program, Launchctl())
    before = read_remote(env)["password_hash"]
    answer = enable(env, program, Launchctl(), passphrase=None, address="https://neu.example.ts.net")
    data = read_remote(env)
    assert answer["passphrase_placeholder"] is False
    assert data["password_hash"] == before and data["public_base_url"] == "https://neu.example.ts.net"
    assert verify_password(PASSPHRASE, data["password_hash"])


def test_a_given_passphrase_replaces_the_placeholder(env, program):
    enable(env, program, Launchctl(), passphrase=None)
    enable(env, program, Launchctl())
    data = read_remote(env)
    assert data["passphrase_placeholder"] is False and verify_password(PASSPHRASE, data["password_hash"])


def test_a_bad_given_passphrase_is_still_refused_even_with_the_none_option(env, program):
    with pytest.raises(SetupError):
        enable(env, program, Launchctl(), passphrase="kurz")


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


# --- a new passphrase for the phone ---------------------------------------------------------

def renew(env, program, launchctl, **kw):
    kw.setdefault("revoke", lambda e: None)
    return remote.renew_passphrase(env, program, run=launchctl, uid=501, platform="darwin", **kw)


def test_a_generated_passphrase_is_long_random_and_easy_to_read():
    first, second = remote.generate_passphrase(), remote.generate_passphrase()
    assert first != second
    groups = first.split("-")
    assert len(groups) == 5 and all(len(g) == 5 for g in groups)
    assert set(first.replace("-", "")) <= set(remote.PASSPHRASE_ALPHABET)
    assert not set("0O1IL") & set(remote.PASSPHRASE_ALPHABET)
    remote.check_passphrase(first)  # passes the rules for a chosen passphrase


def test_renewing_returns_the_passphrase_once_and_stores_only_its_hash(env, program):
    enable(env, program, Launchctl(), passphrase=None)
    assert read_remote(env)["passphrase_placeholder"] is True
    answer = renew(env, program, Launchctl())
    assert answer["address"] == ADDRESS
    stored = read_remote(env)
    assert stored["passphrase_placeholder"] is False
    assert verify_password(answer["passphrase"], stored["password_hash"])
    assert answer["passphrase"] not in json.dumps(stored)
    assert answer["passphrase"] not in json.dumps(remote.status(env, program, run=Launchctl(), uid=501, platform="darwin"))


def test_renewing_ends_every_earlier_sign_in_before_the_new_hash_takes_over(env, program):
    enable(env, program, Launchctl(), passphrase=None)
    lc = Launchctl()
    seen = []
    renew(env, program, lc, revoke=lambda e: seen.append(("bootout" in lc.verbs(), read_remote(env)["passphrase_placeholder"])))
    assert seen == [(False, True)]  # service not yet restarted and hash not yet replaced when the tokens go
    assert lc.verbs().count("bootstrap") == 1  # restarted afterwards, so it loads the new hash


def test_renewing_replaces_a_real_passphrase_too(env, program):
    enable(env, program, Launchctl())
    old = read_remote(env)["password_hash"]
    answer = renew(env, program, Launchctl())
    assert read_remote(env)["password_hash"] != old
    assert not verify_password(PASSPHRASE, read_remote(env)["password_hash"])
    assert verify_password(answer["passphrase"], read_remote(env)["password_hash"])


def test_renewing_needs_the_tunnel_step_first_and_changes_nothing(env, program):
    called = []
    with pytest.raises(SetupError, match="Tunnel"):
        renew(env, program, Launchctl(), revoke=lambda e: called.append(1))
    assert called == [] and not os.path.exists(os.path.join(env["OUTBOX_DATA_DIR"], "remote.json"))


def test_if_the_sign_ins_cannot_be_ended_the_passphrase_stays_as_it_was(env, program):
    enable(env, program, Launchctl(), passphrase=None)
    before = read_remote(env)

    def broken(_env):
        raise SetupError("Die bisherigen Anmeldungen am Handy lassen sich nicht beenden.")

    with pytest.raises(SetupError, match="beenden"):
        renew(env, program, Launchctl(), revoke=broken)
    assert read_remote(env) == before


def test_other_systems_cannot_renew(env, program):
    with pytest.raises(SetupError, match="noch nicht möglich"):
        remote.renew_passphrase(env, program, run=Launchctl(), uid=501, platform="linux")


def test_the_real_revoke_empties_the_sign_in_database_and_ignores_a_missing_one(env, tmp_path):
    import sqlite3

    from outbox_mcp.auth.store import AuthStore

    path = tmp_path / "auth.db"
    assert remote._revoke_logins({**env, "OUTBOX_OAUTH_DB_PATH": str(path)}) is None
    assert not path.exists()  # nobody ever signed in: nothing is created either
    store = AuthStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO clients(client_id, info_json, created_at) VALUES ('c', '{}', 1)")
    assert store.counts()["clients"] == 1
    remote._revoke_logins({**env, "OUTBOX_OAUTH_DB_PATH": str(path)})
    assert store.counts()["clients"] == 0


# --- the passkey link for the phone ---------------------------------------------------------

def link(env, launchctl, **kw):
    return remote.passkey_link(env, run=launchctl, uid=501, platform="darwin", **kw)


def test_the_phone_link_points_at_the_public_address_and_is_a_one_time_token(env, program):
    import sqlite3

    enable(env, program, Launchctl(), passphrase=None)
    url = link(env, Launchctl())
    assert url.startswith("https://mein-mac.example.ts.net/passkey/enroll/")
    token = url.rsplit("/", 1)[1]
    assert len(token) >= 30
    db = os.path.join(env["OUTBOX_DATA_DIR"], "outbox.db")
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT token_hash, expires_at - created_at FROM passkey_enrollments").fetchall()
    assert len(rows) == 1 and token not in rows[0][0] and rows[0][1] == 600  # only a hash is stored, ten minutes


def test_a_leftover_local_setting_cannot_turn_the_phone_link_into_a_local_one(env, program):
    enable(env, program, Launchctl(), passphrase=None)
    polluted = {**env, "OUTBOX_APPROVAL_HOST": "localhost", "OUTBOX_APPROVAL_PORT": "47821"}
    assert link(polluted, Launchctl()).startswith("https://mein-mac.example.ts.net/")


def test_the_phone_link_needs_the_tunnel_and_a_running_service(env, program):
    with pytest.raises(SetupError, match="Tunnel"):
        link(env, Launchctl())
    enable(env, program, Launchctl(), passphrase=None)
    with pytest.raises(SetupError, match="läuft nicht"):
        link(env, Launchctl(state="waiting"))
    with pytest.raises(SetupError, match="noch nicht möglich"):
        remote.passkey_link(env, run=Launchctl(), uid=501, platform="linux")


def test_with_two_passkeys_the_link_says_what_to_do_instead_of_failing_silently(env, program):
    import sqlite3

    enable(env, program, Launchctl(), passphrase=None)
    link(env, Launchctl())  # creates the database
    db = os.path.join(env["OUTBOX_DATA_DIR"], "outbox.db")
    with sqlite3.connect(db) as conn:
        for n in range(2):
            conn.execute(
                "INSERT INTO passkey_credentials(credential_id, public_key, created_at) VALUES (?, x'00', 1)", (f"id{n}",)
            )
    with pytest.raises(SetupError, match="zwei Passkeys.*Passkeys“"):
        link(env, Launchctl())


def test_cli_prints_the_new_passphrase_once_and_the_phone_link(env, program, monkeypatch):
    lc = Launchctl()
    monkeypatch.setattr(remote, "_run", lc)
    monkeypatch.setattr(remote.sys, "platform", "darwin")
    monkeypatch.setattr(remote.os, "getuid", lambda: 501)
    monkeypatch.setattr(remote, "_revoke_logins", lambda e: None)
    enable(env, program, lc, passphrase=None)
    out = io.StringIO()
    assert setup_cli.main(["passphrase-new", "--command", program], env=env, stdout=out) == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is True and len(answer["passphrase"].split("-")) == 5 and answer["address"] == ADDRESS
    assert verify_password(answer["passphrase"], read_remote(env)["password_hash"])
    out = io.StringIO()
    assert setup_cli.main(["approval-link", "--phone"], env=env, stdout=out) == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is True and answer["url"].startswith("https://mein-mac.example.ts.net/passkey/enroll/")


def test_cli_reports_a_missing_tunnel_for_the_new_passphrase_as_json(env, program, monkeypatch):
    monkeypatch.setattr(remote.sys, "platform", "darwin")
    out = io.StringIO()
    assert setup_cli.main(["passphrase-new", "--command", program], env=env, stdout=out) == 0
    answer = json.loads(out.getvalue())
    assert answer["ok"] is False and "Tunnel" in answer["reason"]


# --- restarting a running service ----------------------------------------------------------

def test_a_restart_waits_until_launchd_has_really_ended_the_old_service(env, program):
    enable(env, program, Launchctl())
    pauses = []
    lc = Launchctl(lag=3)  # the old instance is still listed for three more looks, and bootstrap fails meanwhile
    enable(env, program, lc, sleep=pauses.append)
    assert lc.verbs()[0] == "bootout" and lc.verbs()[-1] == "bootstrap"
    assert lc.verbs().count("print") == 4 and lc.verbs().count("bootstrap") == 1
    assert len(pauses) == 3
    assert remote.status(env, program, run=Launchctl(), uid=501, platform="darwin")["running"] is True


def test_a_service_that_does_not_leave_changes_nothing(env, program):
    enable(env, program, Launchctl())
    before = (read_remote(env), remote.plist_path(env).read_bytes())
    lc = Launchctl(stays_listed=True)
    with pytest.raises(SetupError, match="nicht rechtzeitig"):
        enable(env, program, lc, passphrase="eine-ganz-andere-passphrase-1")
    assert "bootstrap" not in lc.verbs()
    assert (read_remote(env), remote.plist_path(env).read_bytes()) == before  # old hash and service file stay


def test_one_failed_start_is_tried_once_more_after_a_pause(env, program):
    pauses = []
    lc = Launchctl(bootstrap_failures=1)
    enable(env, program, lc, sleep=pauses.append)
    assert lc.verbs().count("bootstrap") == 2 and pauses == [1.0]
    assert remote.plist_path(env).exists()
