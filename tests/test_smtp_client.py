import re
import socket
import threading
import time
from email import message_from_bytes, policy

import pytest
from aiosmtpd.controller import Controller

from outbox_mcp.mail.message_builder import build_message
from outbox_mcp.mail.smtp_client import SmtpClient, SmtpSendFailed, SmtpSendUnknown


class Collect:
    def __init__(self, refuse_rcpt=False):
        self.messages = []
        self.refuse_rcpt = refuse_rcpt

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        if self.refuse_rcpt:
            return "550 no such user"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.messages.append(envelope)
        return "250 OK"


def msg(body="Grüße ä ö ü €"):
    return build_message("me@example.com", "Me", "you@example.org", "You", "Betreff äöü", body)


@pytest.fixture
def server():
    handlers = []

    def start(handler):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        c = Controller(handler, hostname="127.0.0.1", port=port)
        c.start()
        c.port_number = port
        handlers.append(c)
        return c

    yield start
    for c in handlers:
        c.stop()


def test_success_utf8(server):
    h = Collect()
    c = server(h)
    SmtpClient("127.0.0.1", c.port_number, "", "", "none", 5).send(
        msg(), "me@example.com", "you@example.org"
    )
    assert len(h.messages) == 1
    assert h.messages[0].rcpt_tos == ["you@example.org"]
    parsed = message_from_bytes(h.messages[0].original_content, policy=policy.default)
    assert parsed["Subject"] == "Betreff äöü"
    assert parsed.get_content().replace("\r\n", "\n").rstrip("\n") == "Grüße ä ö ü €"


def test_starttls_not_offered_means_failed_and_nothing_sent(server):
    h = Collect()
    c = server(h)
    port = c.port_number
    with pytest.raises(SmtpSendFailed):
        SmtpClient("127.0.0.1", port, "", "", "starttls", 5).send(msg(), "me@example.com", "you@example.org")
    assert h.messages == []


def test_recipient_refused_is_failed(server):
    h = Collect(refuse_rcpt=True)
    c = server(h)
    with pytest.raises(SmtpSendFailed):
        SmtpClient("127.0.0.1", c.port_number, "", "", "none", 5).send(
            msg(), "me@example.com", "you@example.org"
        )
    assert h.messages == []


def test_connection_refused_is_failed():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(SmtpSendFailed):
        SmtpClient("127.0.0.1", port, "", "", "none", 2).send(msg(), "me@example.com", "you@example.org")


def test_drop_after_data_is_unknown():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def run():
        conn, _ = srv.accept()
        f = conn.makefile("rwb")
        f.write(b"220 hi\r\n"); f.flush()
        while True:
            line = f.readline().strip().upper()
            if line.startswith(b"EHLO") or line.startswith(b"HELO"):
                f.write(b"250 hi\r\n")
            elif line.startswith(b"MAIL") or line.startswith(b"RCPT"):
                f.write(b"250 OK\r\n")
            elif line == b"DATA":
                f.write(b"354 go\r\n"); f.flush()
                while f.readline().strip() != b".":
                    pass
                conn.close()  # no answer after the message body
                return
            f.flush()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    with pytest.raises(SmtpSendUnknown):
        SmtpClient("127.0.0.1", port, "", "", "none", 5).send(msg(), "me@example.com", "you@example.org")
    srv.close()


def test_password_masked():
    c = SmtpClient("h", 1, "u", "topsecret", "none")
    assert "topsecret" not in c._clean(RuntimeError("login failed for topsecret"))


# ---- B1/B2: what really goes over the wire --------------------------------------------


class RawServer:
    """A minimal strict SMTP server that records the raw DATA bytes.

    data_reply: bytes sent after the message ends; None = close the connection;
    b'HANG' = never answer."""

    def __init__(self, data_reply=b"250 OK\r\n"):
        self.data_reply = data_reply
        self.data = b""
        self.after_data = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        conn, _ = self.sock.accept()
        conn.settimeout(10)
        f = conn.makefile("rwb")
        f.write(b"220 hi\r\n"); f.flush()
        try:
            while True:
                line = f.readline()
                if not line:
                    return
                cmd = line.strip().upper()
                if cmd.startswith((b"EHLO", b"HELO")):
                    f.write(b"250 hi\r\n")
                elif cmd.startswith((b"MAIL", b"RCPT")):
                    f.write(b"250 OK\r\n")
                elif cmd == b"DATA":
                    f.write(b"354 go\r\n"); f.flush()
                    while True:
                        chunk = f.readline()
                        self.data += chunk
                        if chunk.rstrip(b"\r\n") == b".":  # end of data: a line with only a dot
                            break
                    if self.data_reply is None:
                        return
                    if self.data_reply == b"HANG":
                        time.sleep(5)
                        return
                    f.write(self.data_reply); f.flush()
                elif cmd == b"QUIT":
                    f.write(b"221 bye\r\n"); f.flush()
                    return
                else:
                    self.after_data.append(line)
                    f.write(b"250 OK\r\n")
                f.flush()
        except OSError:
            pass
        finally:
            conn.close()

    def close(self):
        self.sock.close()


@pytest.fixture
def raw():
    servers = []

    def make(**kw):
        s = RawServer(**kw)
        servers.append(s)
        return s

    yield make
    for s in servers:
        s.close()


def deliver(server, body, timeout=5):
    m = build_message("me@example.com", "Me", "you@example.org", "You", "Betreff", body)
    SmtpClient("127.0.0.1", server.port, "", "", "none", timeout).send(m, "me@example.com", "you@example.org")


def unstuff(data: bytes) -> bytes:
    """What an SMTP server does with received DATA: drop the final '.' line, remove dot-stuffing."""
    lines = data.split(b"\r\n")
    assert lines[-2:] == [b".", b""], "data must end with a single '.' line"
    return b"\r\n".join(l[1:] if l.startswith(b"..") else l for l in lines[:-2]) + b"\r\n"


BODIES = [
    "Zeile1\n.\nMAIL FROM:<evil@example.com>\nRCPT TO:<x@example.org>\nDATA\n\n.\nRest",
    ".",
    ".\n.\n.",
    ".versteckt\n..doppelt\nnormal",
    "Grüße ä ö ü €\nzweite Zeile",
    "x" * 3000,  # very long line
]


@pytest.mark.parametrize("body", BODIES)
def test_wire_uses_crlf_only(raw, body):
    s = raw()
    deliver(s, body)
    assert not re.search(rb"(?<!\r)\n", s.data), "bare LF on the wire"
    assert not re.search(rb"\r(?!\n)", s.data), "bare CR on the wire"


@pytest.mark.parametrize("body", BODIES)
def test_wire_dot_stuffing_and_single_terminator(raw, body):
    s = raw()
    deliver(s, body)
    lines = s.data.split(b"\r\n")
    # exactly one line consists of a single dot, and it is the last one: nothing can end the data early
    assert [i for i, l in enumerate(lines) if l == b"."] == [len(lines) - 2]
    # no line may start with a lone dot followed by text without being stuffed
    assert all(not (l.startswith(b".") and not l.startswith(b"..")) for l in lines[:-2])
    assert s.after_data == []  # nothing from the body was interpreted as an SMTP command


@pytest.mark.parametrize("body", BODIES)
def test_wire_roundtrip_returns_original_text(raw, body):
    s = raw()
    deliver(s, body)
    parsed = message_from_bytes(unstuff(s.data), policy=policy.default)
    assert parsed.get_content().replace("\r\n", "\n").rstrip("\n") == body.rstrip("\n")


def test_lone_dot_line_is_stuffed_on_the_wire(raw):
    s = raw()
    deliver(s, "vorher\n.\nnachher")
    assert b"\r\n..\r\n" in s.data  # the line '.' became '..'
    assert b"\r\n.\r\nnachher" not in s.data


def test_rejection_after_data_is_failed(raw):
    s = raw(data_reply=b"554 rejected\r\n")
    with pytest.raises(SmtpSendFailed):
        deliver(s, "Text")


def test_timeout_while_waiting_for_data_reply_is_unknown(raw):
    s = raw(data_reply=b"HANG")
    with pytest.raises(SmtpSendUnknown):
        deliver(s, "Text", timeout=1)
