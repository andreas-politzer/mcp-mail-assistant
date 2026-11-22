"""The steps of the setup wizard against a local mail sink, a temporary data folder and a memory keychain."""

import io
import json
import socket

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword

from outbox_mcp import secrets_store, setup_cli, setup_tasks as tasks
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import RecipientStore

PASSWORD = "app-password-123"


class Sink:
    def __init__(self):
        self.messages = []

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def _authenticator(server, session, envelope, mechanism, auth_data):
    ok = isinstance(auth_data, LoginPassword) and auth_data.password.decode() == PASSWORD
    return AuthResult(success=ok, handled=False)


@pytest.fixture
def sink():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    handler = Sink()
    c = Controller(handler, hostname="127.0.0.1", port=port, authenticator=_authenticator, auth_require_tls=False, auth_required=True)
    c.start()
    handler.port = port
    yield handler
    c.stop()


@pytest.fixture
def env(tmp_path):
    folder = tmp_path / "data"
    config = tmp_path / "claude" / "claude_desktop_config.json"
    config.parent.mkdir()
    return {"OUTBOX_DATA_DIR": str(folder), "CLAUDE_DESKTOP_CONFIG": str(config), "HOME": str(tmp_path)}


def box(sink, security="none"):
    return tasks.Mailbox("me@example.org", "127.0.0.1", sink.port, security)


def test_wrong_password_is_reported_and_nothing_is_stored(sink, env):
    with pytest.raises(tasks.SetupError, match="abgelehnt"):
        tasks.save_mailbox(env, box(sink), "falsch")
    assert tasks.load_mailbox(env) is None


def test_unreachable_server_has_a_plain_reason(env):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(tasks.SetupError, match="nicht erreichbar"):
        tasks.check_login(tasks.Mailbox("me@example.org", "127.0.0.1", port, "none"), "x", timeout=2)


def test_unencrypted_is_only_for_localhost():
    with pytest.raises(tasks.SetupError, match="eigenen Computer"):
        tasks.Mailbox("me@example.org", "smtp.example.org", 25, "none").validate()


def test_bad_inputs_are_refused():
    for bad in (
        tasks.Mailbox("kein-at", "h.example.org", 587, "starttls"),
        tasks.Mailbox("me@example.org", "", 587, "starttls"),
        tasks.Mailbox("me@example.org", "h.example.org", 0, "starttls"),
        tasks.Mailbox("me@example.org", "h.example.org", 587, "plain"),
    ):
        with pytest.raises(tasks.SetupError):
            bad.validate()


def test_save_stores_password_in_keychain_and_settings_without_it(env, monkeypatch):
    monkeypatch.setattr(tasks, "check_login", lambda *a, **k: None)
    b = tasks.Mailbox("me@example.org", "smtp.example.org", 587, "starttls")
    tasks.save_mailbox(env, b, PASSWORD)
    assert secrets_store.get_smtp_password("smtp.example.org", "me@example.org") == PASSWORD
    text = (tasks._folder(env) / tasks.MAILBOX_FILE).read_text()
    assert PASSWORD not in text
    assert tasks.load_mailbox(env) == b
    assert tasks.status(env)["password_stored"] is True


def test_a_keychain_that_refuses_is_a_clear_message(env, monkeypatch):
    import keyring
    from keyring.backends import fail

    monkeypatch.setattr(tasks, "check_login", lambda *a, **k: None)
    keyring.set_keyring(fail.Keyring())
    with pytest.raises(tasks.SetupError, match="Schlüsselbund"):
        tasks.save_mailbox(env, tasks.Mailbox("me@example.org", "smtp.example.org", 587, "starttls"), PASSWORD)
    assert tasks.load_mailbox(env) is None


def test_test_mail_arrives_and_marks_the_address_as_own(sink, env, monkeypatch):
    b = tasks.Mailbox("me@example.org", "127.0.0.1", sink.port, "none")
    tasks._write_private(tasks._folder(env) / tasks.MAILBOX_FILE, json.dumps(tasks.asdict(b)))
    tasks.send_test_mail({**env, "OUTBOX_SMTP_PASSWORD": PASSWORD}, "me@example.org", True)
    assert len(sink.messages) == 1 and sink.messages[0].rcpt_tos == ["me@example.org"]
    store = RecipientStore(Database(str(tasks._folder(env) / "outbox.db")))
    rec = store.find("me@example.org")
    assert rec.scope == "own" and rec.status == "active"
    # A second run with the address already on the list must not fail.
    tasks.send_test_mail({**env, "OUTBOX_SMTP_PASSWORD": PASSWORD}, "me@example.org", True)


def test_test_mail_without_marking_leaves_the_list_alone(sink, env):
    b = tasks.Mailbox("me@example.org", "127.0.0.1", sink.port, "none")
    tasks._write_private(tasks._folder(env) / tasks.MAILBOX_FILE, json.dumps(tasks.asdict(b)))
    tasks.send_test_mail({**env, "OUTBOX_SMTP_PASSWORD": PASSWORD}, "you@example.org", False)
    assert not (tasks._folder(env) / "outbox.db").exists()


def test_test_mail_needs_a_stored_password(env):
    tasks._write_private(tasks._folder(env) / tasks.MAILBOX_FILE, json.dumps(tasks.asdict(tasks.Mailbox("me@example.org", "h.example.org", 587, "starttls"))))
    with pytest.raises(tasks.SetupError, match="Passwort"):
        tasks.send_test_mail(env, "me@example.org", False)


def _prepared(env):
    tasks._write_private(tasks._folder(env) / tasks.MAILBOX_FILE, json.dumps(tasks.asdict(tasks.Mailbox("me@example.org", "smtp.example.org", 587, "starttls"))))


def test_connect_creates_the_file_and_the_entry(env):
    _prepared(env)
    path = tasks.claude_config_path(env)
    assert not path.exists()
    assert tasks.connect_claude(env, "/opt/mail-assistant/server") is None
    entry = json.loads(path.read_text())["mcpServers"]["mail-assistant"]
    assert entry["command"] == "/opt/mail-assistant/server" and entry["args"] == ["stdio"]
    assert entry["env"]["OUTBOX_DATA_DIR"] == env["OUTBOX_DATA_DIR"]
    assert entry["env"]["OUTBOX_EXIT_WHEN_STDIN_CLOSES"] == "1"
    assert "OUTBOX_SMTP_PASSWORD" not in entry["env"] and PASSWORD not in path.read_text()
    assert tasks.status(env, "/opt/mail-assistant/server")["claude_connected"] is True


def test_connect_keeps_other_entries_and_makes_a_backup(env):
    _prepared(env)
    path = tasks.claude_config_path(env)
    original = {"theme": "dark", "mcpServers": {"other": {"command": "x"}}}
    path.write_text(json.dumps(original))
    backup = tasks.connect_claude(env, "/opt/ma/server")
    assert backup is not None and json.loads(backup.read_text()) == original
    now = json.loads(path.read_text())
    assert now["theme"] == "dark" and now["mcpServers"]["other"] == {"command": "x"} and "mail-assistant" in now["mcpServers"]
    assert any("1" in line for line in tasks.preview_claude(env, "/opt/ma/server"))


def test_a_broken_configuration_file_is_not_touched(env):
    _prepared(env)
    path = tasks.claude_config_path(env)
    path.write_text("{ not json")
    with pytest.raises(tasks.SetupError, match="nicht lesbar"):
        tasks.connect_claude(env, "/opt/ma/server")
    assert path.read_text() == "{ not json"
    assert not list(path.parent.glob("*backup*"))


def test_relative_program_path_is_refused(env):
    _prepared(env)
    with pytest.raises(tasks.SetupError, match="Pfad"):
        tasks.connect_claude(env, "server")


def test_cli_answers_with_one_json_line_and_never_prints_the_password(env, monkeypatch):
    monkeypatch.setattr(tasks, "check_login", lambda *a, **k: None)
    out = io.StringIO()
    code = setup_cli.main(
        ["save-mailbox", "--email", "me@example.org", "--host", "smtp.example.org", "--port", "587", "--security", "starttls"],
        env=env, stdin=io.StringIO(PASSWORD + "\n"), stdout=out,
    )
    assert code == 0 and json.loads(out.getvalue()) == {"ok": True} and PASSWORD not in out.getvalue()
    out = io.StringIO()
    setup_cli.main(["status"], env=env, stdout=out)
    state = json.loads(out.getvalue())
    assert state["ok"] and state["password_stored"] and state["mailbox"]["host"] == "smtp.example.org"
    out = io.StringIO()
    setup_cli.main(["preview-claude", "--command", "/opt/ma/server"], env=env, stdout=out)
    assert json.loads(out.getvalue())["ok"] is True
    out = io.StringIO()
    setup_cli.main(["send-test", "--to", "kein-at"], env=env, stdout=out)
    assert json.loads(out.getvalue())["ok"] is False


def test_the_error_text_never_contains_the_password(env, monkeypatch):
    import smtplib

    def boom(*a, **k):
        raise smtplib.SMTPResponseException(554, f"bad {PASSWORD} here".encode())

    monkeypatch.setattr(smtplib.SMTP, "__init__", lambda self, *a, **k: boom())
    with pytest.raises(tasks.SetupError) as e:
        tasks.check_login(tasks.Mailbox("me@example.org", "smtp.example.org", 587, "starttls"), PASSWORD)
    assert PASSWORD not in str(e.value)


def test_the_bundle_entry_point_runs_setup_commands(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    entry = Path(__file__).resolve().parent.parent / "packaging" / "entry.py"
    run_env = {"PATH": "", "OUTBOX_DATA_DIR": str(tmp_path / "d"), "CLAUDE_DESKTOP_CONFIG": str(tmp_path / "c.json"), "HOME": str(tmp_path)}
    done = subprocess.run([sys.executable, "-I", str(entry), "setup", "status"], capture_output=True, text=True, env=run_env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"ok": True, "mailbox": None, "password_stored": False, "claude_connected": False, "passkeys": 0}
    bad = subprocess.run([sys.executable, "-I", str(entry), "nope"], capture_output=True, text=True, env=run_env, timeout=60)
    assert bad.returncode == 2


def test_backup_is_private_and_a_symlinked_config_stays_a_link(env, tmp_path):
    import os
    import stat

    _prepared(env)
    real = tmp_path / "dotfiles" / "claude.json"
    real.parent.mkdir()
    real.write_text(json.dumps({"mcpServers": {}}))
    path = tasks.claude_config_path(env)
    os.symlink(real, path)
    backup = tasks.connect_claude(env, "/opt/ma/server")
    assert path.is_symlink()
    assert "mail-assistant" in json.loads(real.read_text())["mcpServers"]
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600


def test_missing_claude_folder_gives_a_clear_message_and_creates_nothing(env, tmp_path):
    _prepared(env)
    env = {**env, "CLAUDE_DESKTOP_CONFIG": str(tmp_path / "nope" / "claude_desktop_config.json")}
    for call in (tasks.preview_claude, tasks.connect_claude):
        with pytest.raises(tasks.SetupError, match="nicht installiert"):
            call(env, "/opt/ma/server")
    assert not (tmp_path / "nope").exists()


def test_entry_carries_the_local_approval_page_settings(env):
    _prepared(env)
    tasks.connect_claude(env, "/opt/ma/server")
    e = json.loads(tasks.claude_config_path(env).read_text())["mcpServers"]["mail-assistant"]["env"]
    assert e["OUTBOX_APPROVAL_HOST"] == "localhost" and e["OUTBOX_APPROVAL_PORT"] == str(tasks.APPROVAL_PORT)


def test_switching_provider_removes_the_old_keychain_entry(sink, env, monkeypatch):
    store = {}
    monkeypatch.setattr(secrets_store, "set_smtp_password", lambda h, u, p: store.__setitem__((h, u), p))
    monkeypatch.setattr(secrets_store, "delete_smtp_password", lambda h, u: store.pop((h, u), None) is not None)
    tasks._write_private(tasks._folder(env) / tasks.MAILBOX_FILE, json.dumps(tasks.asdict(tasks.Mailbox("old@example.org", "old.example.org", 587, "starttls"))))
    store[("old.example.org", "old@example.org")] = "x"
    # "none" is refused after the login check, so use a keychain-backed mailbox on the local sink via a patched check
    monkeypatch.setattr(tasks, "check_login", lambda *a, **k: None)
    tasks.save_mailbox(env, tasks.Mailbox("new@example.org", "new.example.org", 587, "starttls"), PASSWORD)
    assert ("old.example.org", "old@example.org") not in store and ("new.example.org", "new@example.org") in store


def test_approval_link_needs_a_running_page_then_prints_a_one_time_link(env, monkeypatch):
    from outbox_mcp.approval import local_listener

    monkeypatch.setattr(local_listener, "find_running_page", lambda config: None)
    with pytest.raises(tasks.SetupError, match="Cmd\\+Q"):
        tasks.approval_link(env)
    monkeypatch.setattr(local_listener, "find_running_page", lambda config: tasks.APPROVAL_PORT + 1)
    url = tasks.approval_link(env)
    assert url.startswith(f"http://localhost:{tasks.APPROVAL_PORT + 1}/passkey/enroll/")
    assert tasks.status(env)["passkeys"] == 0


# ---- mailbox.json read by the server, local administration, passkey kind

def test_server_reads_the_mailbox_from_the_data_folder_and_the_environment_wins(env, monkeypatch):
    from outbox_mcp.config import Config

    _prepared(env)
    monkeypatch.setattr(secrets_store, "get_smtp_password", lambda host, user: "pw-from-keychain")
    config = Config.from_env({"OUTBOX_DATA_DIR": env["OUTBOX_DATA_DIR"]})
    assert (config.smtp_host, config.smtp_port, config.smtp_user, config.from_address) == ("smtp.example.org", 587, "me@example.org", "me@example.org")
    again = Config.from_env({"OUTBOX_DATA_DIR": env["OUTBOX_DATA_DIR"], "OUTBOX_SMTP_HOST": "other.example.org"})
    assert again.smtp_host == "other.example.org" and again.smtp_user == "me@example.org"


def test_the_claude_entry_no_longer_carries_the_mailbox(env):
    _prepared(env)
    tasks.connect_claude(env, "/opt/ma/server")
    e = json.loads(tasks.claude_config_path(env).read_text())["mcpServers"]["mail-assistant"]["env"]
    assert not any(k.startswith("OUTBOX_SMTP") or k == "OUTBOX_FROM_ADDRESS" for k in e)
    assert e["OUTBOX_DATA_DIR"] == env["OUTBOX_DATA_DIR"]


def _local(env, *argv):
    from outbox_mcp import local_cli

    out = io.StringIO()
    assert local_cli.main(list(argv), env=env, stdout=out) == 0
    return json.loads(out.getvalue())


def test_local_administration_lists_approves_and_changes_scope(env):
    from outbox_mcp.paths import default_file

    store = RecipientStore(Database(str(default_file(env, "outbox.db"))))
    store.suggest("claude", "Velo", "hello@example.com", "test", 1.0)
    snap = _local(env, "snapshot")
    assert snap["ok"] and [r["address"] for r in snap["recipients"]] == ["hello@example.com"] and snap["recipients"][0]["status"] == "suggested"
    done = _local(env, "approve", "--ref", "hello@example.com")
    assert done["ok"] and done["recipient"]["status"] == "active" and done["recipient"]["scope"] == "external"
    assert _local(env, "scope", "--ref", "hello@example.com", "--value", "own")["recipient"]["scope"] == "own"
    gone = _local(env, "remove", "--ref", "hello@example.com")
    assert gone["ok"] and gone["recipient"]["status"] == "removed"
    bad = _local(env, "approve", "--ref", "nobody@example.com")
    assert bad["ok"] is False and "nobody@example.com" in bad["reason"]
    assert _local(env, "passkey-remove", "--id", "abcdefabcdef")["ok"] is False


def test_registration_does_not_restrict_the_kind_of_authenticator(env, monkeypatch):
    from outbox_mcp.approval import service as svc

    seen = {}
    real = svc.generate_registration_options

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(svc, "generate_registration_options", spy)
    service = tasks._approval_service(env)
    token = service.create_enrollment()
    ok, _ = service.enroll_options(token)
    assert ok
    selection = seen["authenticator_selection"]
    assert selection.authenticator_attachment is None
    assert str(selection.user_verification.value) == "required"


# -- the admin application for the desktop app, without a network ---------------------------------

def _api_env(tmp_path):
    return {"OUTBOX_DATA_DIR": str(tmp_path)}


def _ask(api, method, path, body=None):
    return api.handle({"method": method, "path": path, "body": body})


def test_local_api_serves_the_admin_routes_with_its_own_session(tmp_path):
    from outbox_mcp.local_api import LocalApi

    api = LocalApi(_api_env(tmp_path))
    added = _ask(api, "POST", "/admin/recipients", {"name": "Velo", "address": "hello@example.com", "aliases": []})
    assert added["status"] == 201
    listed = _ask(api, "GET", "/admin/recipients")
    assert listed["status"] == 200
    assert [r["address"] for r in listed["data"]["recipients"]] == ["hello@example.com"]
    assert _ask(api, "GET", "/admin/status")["data"]["recipients"]["active"] == 1
    assert _ask(api, "GET", "/admin/recipients?status=nonsense")["status"] == 400


def test_local_api_keeps_the_confirmation_steps(tmp_path):
    from outbox_mcp.local_api import LocalApi

    api = LocalApi(_api_env(tmp_path))
    rec = _ask(api, "POST", "/admin/recipients", {"name": "A", "address": "a@example.org", "aliases": []})["data"]["recipient"]
    first = _ask(api, "POST", f"/admin/recipients/{rec['id']}/scope", {"scope": "own"})
    assert first["data"]["status"] == "confirmation_required"
    done = _ask(api, "POST", f"/admin/recipients/{rec['id']}/scope", {"scope": "own", "confirmation_id": first["data"]["confirmation_id"]})
    assert done["data"]["recipient"]["scope"] == "own"


def test_local_api_refuses_what_is_not_admin_or_is_a_sign_in(tmp_path):
    from outbox_mcp.local_api import LocalApi

    api = LocalApi(_api_env(tmp_path))
    for method, path in [
        ("GET", "/"), ("GET", "/admin/../x"), ("POST", "/admin/login"), ("POST", "/admin/logout"), ("DELETE", "/admin/status"),
        ("POST", "/admin/login?x=1"), ("POST", "/admin/login/"), ("POST", "/admin/LOGIN"), ("POST", "/admin//login"),
        ("POST", "//admin/login"), ("POST", "/admin/%6cogin"), ("POST", "/admin/%2e%2e/x"), ("GET", "/admin\\login"),
        ("GET", "/admin/status\n"), ("GET", "/admin/status\x00"),
    ]:
        assert _ask(api, method, path)["status"] == 400, path
    assert api.handle("nonsense")["status"] == 400


def test_local_api_session_over_lines(tmp_path):
    import io

    from outbox_mcp.local_api import serve

    lines = "\n".join(
        [
            '{"method":"GET","path":"/admin/status","body":null}',
            "not json",
            '{"method":"GET","path":"/admin/recipients","body":null}',
        ]
    )
    out = io.StringIO()
    assert serve(_api_env(tmp_path), io.StringIO(lines + "\n"), out) == 0
    got = [json.loads(x) for x in out.getvalue().splitlines()]
    assert got[0] == {"ready": True}
    assert got[1]["status"] == 200 and got[2]["status"] == 400 and got[3]["data"]["recipients"] == []


# -- the status never reads the keychain -----------------------------------------------------------

def test_status_never_touches_the_keychain(env, monkeypatch):
    from outbox_mcp import secrets_store, setup_tasks as tasks

    (tasks._folder(env) / "mailbox.json").write_text(
        json.dumps({"email": "a@example.org", "host": "smtp.example.org", "port": 465, "security": "ssl", "password_stored": True})
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("the keychain was read")

    monkeypatch.setattr(secrets_store, "get_smtp_password", forbidden)
    assert tasks.status(env)["password_stored"] is True


def test_a_mailbox_file_from_before_the_flag_counts_as_not_stored(env):
    from outbox_mcp import setup_tasks as tasks

    (tasks._folder(env) / "mailbox.json").write_text(
        json.dumps({"email": "a@example.org", "host": "smtp.example.org", "port": 465, "security": "ssl"})
    )
    state = tasks.status(env)
    assert state["mailbox"]["host"] == "smtp.example.org" and state["password_stored"] is False


def test_the_flag_does_not_disturb_the_mail_server_configuration(env, monkeypatch):
    from outbox_mcp import secrets_store, setup_tasks as tasks
    from outbox_mcp.config import _with_mailbox_file

    (tasks._folder(env) / "mailbox.json").write_text(
        json.dumps({"email": "a@example.org", "host": "smtp.example.org", "port": 465, "security": "ssl", "password_stored": True})
    )
    merged = _with_mailbox_file(env)
    assert merged["OUTBOX_SMTP_HOST"] == "smtp.example.org" and "password_stored" not in merged


def test_route_check_lets_encoded_query_values_through():
    from outbox_mcp.local_api import route_allowed

    assert route_allowed("/admin/sources/x/files?cursor=a%2Fb")
    assert route_allowed("/admin/recipients")
    assert not route_allowed("/admin/recipients%2f..")


def test_local_api_keeps_stray_prints_off_the_protocol(monkeypatch, tmp_path):
    import io
    import sys

    from outbox_mcp import local_api, local_cli

    real, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", real)
    monkeypatch.setattr(sys, "stderr", err)
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"method":"GET","path":"/admin/status"}\n'))

    def noisy(self, request):
        print("a library says hello")
        return {"status": 200, "data": {"ok": True}}

    monkeypatch.setattr(local_api.LocalApi, "handle", noisy)
    monkeypatch.setattr(local_api.LocalApi, "__init__", lambda self, env: None)
    assert local_cli.main(["api"], env=_api_env(tmp_path)) == 0
    lines = [json.loads(l) for l in real.getvalue().splitlines()]
    assert lines == [{"ready": True}, {"status": 200, "data": {"ok": True}}]
    assert "a library says hello" in err.getvalue()
