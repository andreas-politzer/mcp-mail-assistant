"""Claude Desktop can start the stdio server twice (chat, and the pool for Cowork and Code).
Real processes, one shared data folder: both must run, share the data and keep their own
approval page; losing one must not disturb the other."""

import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from outbox_mcp.approval.service import ApprovalConfig
from outbox_mcp.approval.local_listener import find_running_page

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _clean_environment():
    """The caller's own OUTBOX_* settings (a shell with a real setup) must not leak into the test."""
    return {k: v for k, v in os.environ.items() if not k.startswith("OUTBOX_")}


def _free_base():
    for base in range(41000, 49000, 50):
        socks = []
        try:
            for p in range(base, base + 4):
                s = socket.socket()
                s.bind(("127.0.0.1", p))
                socks.append(s)
            return base
        except OSError:
            continue
        finally:
            for s in socks:
                s.close()
    pytest.skip("no free port range")


class Instance:
    def __init__(self, folder, port):
        env = _clean_environment()
        env.update({
            "PYTHONPATH": SRC + os.pathsep + env.get("PYTHONPATH", ""),
            "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_PORT": "2525", "OUTBOX_SMTP_SECURITY": "none",
            "OUTBOX_FROM_ADDRESS": "me@example.com", "OUTBOX_DATA_DIR": str(folder),
            "OUTBOX_APPROVAL_HOST": "localhost", "OUTBOX_APPROVAL_PORT": str(port),
        })
        # started from "/" like Claude Desktop does: nothing may depend on the working directory
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "outbox_mcp"], env=env, cwd=os.path.abspath(os.sep),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                "clientInfo": {"name": "t", "version": "0"}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def rpc(self, method, params=None, timeout=30):
        self._id += 1
        self._send({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        end = time.time() + timeout
        while time.time() < end:
            line = self.proc.stdout.readline()
            if not line:
                break
            try:
                reply = json.loads(line)
            except ValueError:
                continue
            if reply.get("id") == self._id:
                return reply["result"]
        raise AssertionError("no answer: " + self.proc.stderr.read()[-300:] if self.proc.poll() is not None else "no answer")

    def recipients(self):
        result = self.rpc("tools/call", {"name": "list_recipients", "arguments": {}})
        return json.loads(result["content"][0]["text"])["recipients"]

    def stop(self, sig=signal.SIGTERM):
        if self.proc.poll() is None:
            self.proc.send_signal(sig)
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            try:
                stream.close()
            except Exception:
                pass


def _page_ports(base):
    found = []
    for port in range(base, base + 4):
        probe = ApprovalConfig(rp_id="localhost", port=port)
        if find_running_page(probe) == port:
            found.append(port)
    return found


def _wait_ports(base, count, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        ports = _page_ports(base)
        if len(ports) >= count:
            return ports
        time.sleep(0.3)
    return _page_ports(base)


def test_two_instances_share_data_and_each_keeps_its_page(tmp_path):
    base = _free_base()
    folder = tmp_path / "Mail Assistant"
    from concurrent.futures import ThreadPoolExecutor

    a = b = c = None
    try:
        # both start at the same moment on an empty folder, as when Claude Desktop launches
        with ThreadPoolExecutor(2) as pool:
            fa, fb = pool.submit(Instance, folder, base), pool.submit(Instance, folder, base)
            a, b = fa.result(timeout=60), fb.result(timeout=60)
        # both run, on different ports of the same range
        ports = _wait_ports(base, 2)
        assert len(ports) == 2 and len(set(ports)) == 2
        # started from "/": the data is in the folder, not next to the working directory
        assert (folder / "outbox.db").exists()
        assert not Path("/outbox.db").exists()
        # data written through one instance is seen by the other at once
        assert a.recipients() == [] and b.recipients() == []
        done = subprocess.run(
            [sys.executable, "-c", "import sys; from outbox_mcp.admin_cli import main; sys.exit(main(['add','Test','t@example.com','--own']))"],
            env={**_clean_environment(), "PYTHONPATH": SRC, "OUTBOX_DATA_DIR": str(folder)}, capture_output=True, text=True,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        assert [r["address"] for r in a.recipients()] == ["t@example.com"]
        assert [r["address"] for r in b.recipients()] == ["t@example.com"]
        # losing the first one does not disturb the second
        a.stop(signal.SIGKILL)
        assert [r["address"] for r in b.recipients()] == ["t@example.com"]
        assert len(_page_ports(base)) == 1
        # a new instance takes the free slot again
        c = Instance(folder, base)
        assert len(_wait_ports(base, 2)) == 2
        # ending both at the same moment leaves nothing behind
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda i: i.stop(), (b, c)))
        assert _page_ports(base) == []
        assert b.proc.returncode is not None and c.proc.returncode is not None
    finally:
        for instance in (a, b, c):
            if instance is not None:
                instance.stop(signal.SIGKILL)
