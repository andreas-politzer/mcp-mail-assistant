"""The HTTP server ends by itself when its parent drops the stdin pipe (opt-in)."""

import json
import socket
import subprocess
import sys
import time

import pytest

from outbox_mcp.auth.passwords import hash_password


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(tmp_path, port, *, flag):
    (tmp_path / "recipients.json").write_text(json.dumps({"recipients": [
        {"name": "Me", "address": "me@example.com", "aliases": ["myself"]}]}))
    import os
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if not k.startswith("OUTBOX_")}  # no settings from the caller's shell
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    env.update({
        "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_PORT": "2525", "OUTBOX_SMTP_SECURITY": "none",
        "OUTBOX_FROM_ADDRESS": "me@example.com",
        "OUTBOX_RECIPIENTS_FILE": str(tmp_path / "recipients.json"),
        "OUTBOX_DB_PATH": str(tmp_path / "mail.db"),
        "OUTBOX_PUBLIC_BASE_URL": "https://example.org",
        "OUTBOX_OAUTH_PASSWORD_HASH": hash_password("pw", n=2**4),
        "OUTBOX_OAUTH_DB_PATH": str(tmp_path / "auth.db"),
        "OUTBOX_HTTP_PORT": str(port),
    })
    if flag:
        env["OUTBOX_EXIT_WHEN_STDIN_CLOSES"] = "1"
    return env


def _wait_open(port, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _start(tmp_path, *, flag):
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "outbox_mcp.http_server"],
        env=_env(tmp_path, port, flag=flag), stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc, port


def test_server_ends_when_stdin_closes(tmp_path):
    proc, port = _start(tmp_path, flag=True)
    try:
        assert _wait_open(port), "server did not start"
        proc.stdin.close()
        assert proc.wait(timeout=15) == 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_server_keeps_running_without_the_option(tmp_path):
    proc, port = _start(tmp_path, flag=False)
    try:
        assert _wait_open(port), "server did not start"
        proc.stdin.close()
        time.sleep(1.5)
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=10)
