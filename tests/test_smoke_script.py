import importlib.util
import io
import socket
import tempfile
from email import message_from_bytes, policy
from pathlib import Path

import pytest
from aiosmtpd.controller import Controller

from outbox_mcp.config import Config
from outbox_mcp.mail.smtp_client import SmtpClient, SmtpSendFailed, SmtpSendUnknown

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test_infomaniak_smtp.py"
spec = importlib.util.spec_from_file_location("smtp_test_script", SCRIPT)
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)

PASSWORD = "s3cret-pw!"
ENV = {
    "OUTBOX_SMTP_HOST": "smtp.example.com", "OUTBOX_SMTP_USER": "me@example.com",
    "OUTBOX_SMTP_PASSWORD": PASSWORD, "OUTBOX_FROM_ADDRESS": "me@example.com",
}


class FakeSender:
    def __init__(self, error=None):
        self.sent, self.error = [], error

    def send(self, message, envelope_from, envelope_to):
        if self.error:
            raise self.error
        self.sent.append((message, envelope_from, envelope_to))


@pytest.fixture
def cfg():
    return Config.from_env(ENV)


def go(cfg, sender, answer, dry_run=False):
    out = io.StringIO()

    def ask(prompt):
        out.write(prompt + "\n")
        if isinstance(answer, BaseException):
            raise answer
        return answer

    code = script.run(cfg, sender, ask, out, dry_run)
    return code, out.getvalue()


def test_dry_run_shows_everything_and_sends_nothing(cfg):
    s = FakeSender()
    code, out = go(cfg, s, "SEND", dry_run=True)
    assert code == 0 and s.sent == []
    for part in ["From:    me@example.com", "To:      me@example.com", "Subject: outbox-mcp SMTP test",
                 "Character check", "Dry run", "STARTTLS", "no retry"]:
        assert part in out
    assert "Type SEND" not in out  # a dry run never asks for confirmation
    assert PASSWORD not in out


@pytest.mark.parametrize("answer", ["", "y", "yes", "send", "Send", "SEND it", "no", EOFError(), KeyboardInterrupt()])
def test_anything_but_the_exact_word_cancels(cfg, answer):
    s = FakeSender()
    code, out = go(cfg, s, answer)
    assert code == 1 and s.sent == [] and "Cancelled" in out


def test_confirmed_sends_exactly_one_mail_to_the_own_address(cfg):
    s = FakeSender()
    code, out = go(cfg, s, "SEND")
    assert code == 0 and len(s.sent) == 1
    msg, frm, to = s.sent[0]
    assert (frm, to) == ("me@example.com", "me@example.com")
    assert msg["To"].addresses[0].addr_spec == "me@example.com"
    assert msg["Cc"] is None and msg["Bcc"] is None
    assert "Status: sent" in out and "spam folder" in out
    assert PASSWORD not in out


def test_sender_address_is_the_only_possible_recipient():
    cfg = Config.from_env({**ENV, "OUTBOX_FROM_ADDRESS": "other@example.net"})
    s = FakeSender()
    go(cfg, s, "SEND")
    assert s.sent[0][2] == "other@example.net"


@pytest.mark.parametrize("error, status", [
    (SmtpSendFailed("login failed"), "failed"),
    (SmtpSendUnknown("dropped"), "unknown"),
])
def test_failures_are_reported_and_not_retried(cfg, error, status):
    s = FakeSender(error)
    code, out = go(cfg, s, "SEND")
    assert code == 1 and f"Status: {status}" in out and "Reason reported" in out
    assert s.sent == []


def test_password_is_masked_even_in_error_texts(cfg):
    s = FakeSender(SmtpSendFailed(f"535 bad login for secret {PASSWORD}"))
    _, out = go(cfg, s, "SEND")
    assert PASSWORD not in out and "***" in out


def test_no_temporary_files_are_left_behind(cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    go(cfg, FakeSender(), "SEND")
    go(cfg, FakeSender(), "no")
    assert list(tmp_path.iterdir()) == []


def run_main(env, argv=None, tty=True, answer="SEND"):
    out, s = io.StringIO(), FakeSender()
    code = script.main(argv or [], env, lambda p: answer, out, tty, lambda c: s)
    return code, out.getvalue(), s


@pytest.mark.parametrize("change", [
    {"OUTBOX_SMTP_SECURITY": "ssl"},
    {"OUTBOX_SMTP_PORT": "465"},
    {"OUTBOX_SMTP_PORT": "2525"},
    {"OUTBOX_SMTP_SECURITY": "none", "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_PORT": "2525"},
])
def test_main_refuses_anything_but_starttls_on_587(change):
    code, out, s = run_main({**ENV, **change})
    assert code == 2 and "Refusing" in out and s.sent == []


def test_main_refuses_without_interactive_terminal():
    code, out, s = run_main(ENV, tty=False)
    assert code == 2 and "interactive" in out and s.sent == []
    assert run_main(ENV, ["--dry-run"], tty=False)[0] == 0  # a dry run needs no terminal


def test_main_happy_path_and_dry_run():
    code, out, s = run_main(ENV)
    assert code == 0 and len(s.sent) == 1 and PASSWORD not in out
    code, out, s = run_main(ENV, ["--dry-run"])
    assert code == 0 and s.sent == []


def test_main_has_no_way_to_choose_a_recipient():
    for argv in (["--to", "x@example.net"], ["--recipient", "x@example.net"], ["x@example.net"]):
        with pytest.raises(SystemExit) as e:
            script.main(argv, ENV, lambda p: "SEND", io.StringIO(), True, lambda c: FakeSender())
        assert e.value.code == 2


def test_missing_settings_give_a_clean_error_without_traceback():
    code, out, _ = run_main({})
    assert code == 2 and out.startswith("Error:") and "Traceback" not in out


def test_unexpected_errors_print_only_the_class_name():
    out = io.StringIO()

    def boom(_):
        raise RuntimeError(f"contains {PASSWORD}")

    code = script.main([], ENV, lambda p: "SEND", out, True, boom)
    assert code == 2 and "RuntimeError" in out.getvalue() and PASSWORD not in out.getvalue()


def test_end_to_end_with_real_smtp_client_against_local_server():
    class Collect:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append(envelope)
            return "250 OK"

    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    h = Collect()
    c = Controller(h, hostname="127.0.0.1", port=port)
    c.start()
    try:
        cfg = Config.from_env({**ENV, "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_SECURITY": "none",
                               "OUTBOX_SMTP_PORT": str(port), "OUTBOX_SMTP_USER": "", "OUTBOX_SMTP_PASSWORD": ""})
        code, out = go(cfg, SmtpClient("127.0.0.1", port, "", "", "none", 5), "SEND")
    finally:
        c.stop()
    assert code == 0 and len(h.messages) == 1
    assert h.messages[0].rcpt_tos == ["me@example.com"]
    parsed = message_from_bytes(h.messages[0].original_content, policy=policy.default)
    assert parsed["Subject"] == "outbox-mcp SMTP test (Grüße)"
    assert "Grüße, äöüß, €" in parsed.get_content()
