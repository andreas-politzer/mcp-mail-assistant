"""Command line behind the setup wizard: one command, one line of JSON on standard output.

    setup status
    setup save-mailbox --email A --host H --port P --security starttls|ssl     (password on stdin)
    setup send-test --to A [--mark-own]
    setup preview-claude --command /absolute/path/to/this/program
    setup connect-claude --command /absolute/path/to/this/program
    setup disconnect-claude                  (removes only our own entry from Claude Desktop's settings)
    setup approval-link [--phone]            (one-time link to register a passkey on the local approval page;
                                              with --phone: on the public page, to be opened on the phone)
    setup passphrase-new --command PATH      (new sign-in passphrase for the phone, printed once as JSON)
    setup remote-status [--command PATH]     (phone use: is the background service set up and running?)
    setup remote-enable --command PATH --url https://...      (passphrase on stdin)
    setup remote-disable                     (stops the service, removes its files; recipients and passkeys stay)
    setup tunnel-status                      (Tailscale state and the public address; changes nothing)
    setup tunnel-enable --command PATH --url https://...      (service + Funnel + check; the URL is the confirmed one)

The data folder comes from OUTBOX_DATA_DIR. Exit code is 0 whenever the answer is JSON ({"ok": false,
"reason": "..."} included); 2 for a malformed command line. The password is read from the first line of
standard input and is never printed, logged or put in an argument. The one exception is `passphrase-new`: the
new passphrase is generated here and printed once, because the person has to see it exactly once.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Mapping, Optional, TextIO

from . import remote, secrets_store, setup_tasks as tasks, tunnel
from .errors import OutboxError


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="setup", description="Steps of the setup wizard.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("status")
    s.add_argument("--command", dest="program", default="")
    m = sub.add_parser("save-mailbox")
    m.add_argument("--email", required=True)
    m.add_argument("--host", required=True)
    m.add_argument("--port", required=True, type=int)
    m.add_argument("--security", required=True, choices=["starttls", "ssl", "none"])
    t = sub.add_parser("send-test")
    t.add_argument("--to", required=True)
    t.add_argument("--mark-own", action="store_true")
    sub.add_parser("approval-link").add_argument("--phone", action="store_true")
    sub.add_parser("disconnect-claude")
    sub.add_parser("remote-disable")
    sub.add_parser("remote-status").add_argument("--command", dest="program", default="")
    sub.add_parser("tunnel-status")
    sub.add_parser("passphrase-new").add_argument("--command", dest="program", required=True)
    te = sub.add_parser("tunnel-enable")
    te.add_argument("--command", dest="program", required=True)
    te.add_argument("--url", required=True)
    re_ = sub.add_parser("remote-enable")
    re_.add_argument("--command", dest="program", required=True)
    re_.add_argument("--url", required=True)
    for name in ("preview-claude", "connect-claude"):
        sub.add_parser(name).add_argument("--command", dest="program", required=True)
    return p


def main(
    argv: Optional[List[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    stdin: Optional[TextIO] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    out = stdout or sys.stdout
    environment = env if env is not None else os.environ
    args = _parser().parse_args(argv)

    def reply(payload: dict) -> int:
        print(json.dumps(payload, ensure_ascii=False), file=out)
        return 0

    try:
        if args.command == "status":
            return reply({"ok": True, **tasks.status(environment, args.program)})
        if args.command == "save-mailbox":
            line = (stdin or sys.stdin).readline().rstrip("\r\n")
            tasks.save_mailbox(environment, tasks.Mailbox(args.email, args.host, args.port, args.security), line)
            return reply({"ok": True})
        if args.command == "send-test":
            tasks.send_test_mail(environment, args.to, args.mark_own)
            return reply({"ok": True})
        if args.command == "approval-link":
            url = remote.passkey_link(environment) if args.phone else tasks.approval_link(environment)
            return reply({"ok": True, "url": url})
        if args.command == "disconnect-claude":
            return reply({"ok": True, "removed": tasks.disconnect_claude(environment)})
        if args.command == "remote-status":
            return reply({"ok": True, **remote.status(environment, args.program)})
        if args.command == "remote-enable":
            line = (stdin or sys.stdin).readline().rstrip("\r\n")
            return reply({"ok": True, **remote.enable(environment, args.program, args.url, line)})
        if args.command == "remote-disable":
            return reply({"ok": True, "removed": remote.disable(environment)})
        if args.command == "tunnel-status":
            return reply({"ok": True, **tunnel.status()})
        if args.command == "tunnel-enable":
            return reply({"ok": True, **tunnel.enable(environment, args.program, args.url)})
        if args.command == "passphrase-new":
            return reply({"ok": True, **remote.renew_passphrase(environment, args.program)})
        if args.command == "preview-claude":
            return reply({"ok": True, "lines": tasks.preview_claude(environment, args.program)})
        tasks.connect_claude(environment, args.program)
        return reply({"ok": True})
    except tunnel.NeedsAdmin as exc:
        return reply({"ok": False, "reason": str(exc), "url": exc.url})
    except tasks.SetupError as exc:
        return reply({"ok": False, "reason": str(exc)})
    except secrets_store.KeychainError as exc:
        return reply({"ok": False, "reason": tasks.keychain_reason(exc)})
    except OutboxError as exc:
        return reply({"ok": False, "reason": f"Das ging nicht: {exc}"})


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
