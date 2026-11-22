"""Local administration for the desktop app: one command, one line of JSON on standard output.

    local snapshot
    local approve --ref R [--name N] [--own]
    local reject|block|unblock|remove --ref R
    local scope --ref R --value own|external
    local add --name N --address A [--own]
    local passkey-remove --id PREFIX
    local api            (a session: one JSON request per line in, one answer per line out; see local_api)

The data folder comes from OUTBOX_DATA_DIR. The app starts this program as a child process instead of
talking to an admin server: no port is opened, no token is kept. Exit code is 0 whenever the answer is
JSON ({"ok": false, "reason": "..."} included); 2 for a malformed command line.

What this can do is what `outbox-admin` can do on the command line, nothing more. It is for the person
at the computer. It cannot send mail and it gives the chat no new rights.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from typing import List, Mapping, Optional, TextIO

from . import setup_tasks as tasks
from .errors import OutboxError
from .paths import default_file
from .storage.database import Database
from .storage.recipient_store import RecipientRecord, RecipientStore

SOURCE = "app:local"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="local", description="Local administration for the desktop app.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("api")
    a = sub.add_parser("approve")
    a.add_argument("--ref", required=True)
    a.add_argument("--name")
    a.add_argument("--own", action="store_true")
    for name in ("reject", "block", "unblock", "remove"):
        sub.add_parser(name).add_argument("--ref", required=True)
    s = sub.add_parser("scope")
    s.add_argument("--ref", required=True)
    s.add_argument("--value", required=True, choices=["own", "external"])
    add = sub.add_parser("add")
    add.add_argument("--name", required=True)
    add.add_argument("--address", required=True)
    add.add_argument("--own", action="store_true")
    sub.add_parser("passkey-remove").add_argument("--id", required=True)
    return p


def _recipient(r: RecipientRecord) -> dict:
    return {
        "id": r.id, "name": r.name, "address": r.address, "aliases": list(r.aliases), "status": r.status,
        "note": r.note, "suggested_by": r.suggested_by, "created_at": r.created_at,
        "decided_at": r.decided_at, "scope": r.scope,
    }


def snapshot(env: Mapping[str, str]) -> dict:
    """Everything the administration screen shows, in one start of the program."""
    store = RecipientStore(Database(str(default_file(env, "outbox.db"))))
    box = tasks.load_mailbox(env)
    try:
        passkeys = tasks._approval_service(env).list_passkeys()
    except Exception:
        passkeys = []
    return {
        "mailbox": asdict(box) if box else None,
        "recipients": [_recipient(r) for r in store.all_records()],
        "passkeys": [
            {"id": str(k["credential_id"])[:12], "created_at": k["created_at"], "last_used_at": k["last_used_at"]}
            for k in passkeys
        ],
    }


def main(
    argv: Optional[List[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
) -> int:
    out = stdout or sys.stdout
    environment = env if env is not None else os.environ
    args = _parser().parse_args(argv)
    if args.command == "api":
        from .local_api import serve

        # The answers go to the real standard output only. Anything else that prints (a library, a warning)
        # lands on the error stream and cannot break the line protocol.
        if out is sys.stdout:
            sys.stdout = sys.stderr
        return serve(environment, sys.stdin, out)

    def reply(payload: dict) -> int:
        print(json.dumps(payload, ensure_ascii=False), file=out)
        return 0

    try:
        if args.command == "snapshot":
            return reply({"ok": True, **snapshot(environment)})
        if args.command == "passkey-remove":
            removed = tasks._approval_service(environment).remove_passkey(args.id)
            return reply({"ok": True} if removed else {"ok": False, "reason": "Dieser Passkey wurde nicht gefunden."})
        store = RecipientStore(Database(str(default_file(environment, "outbox.db"))))
        now = time.time()
        scope = "own" if getattr(args, "own", False) else "external"
        if args.command == "approve":
            record = store.find(args.ref)
            done = store.approve(record.id, now, SOURCE, name=args.name, scope=scope)
        elif args.command == "add":
            done = store.add(args.name, args.address, [], now, SOURCE, scope=scope)
        elif args.command == "scope":
            done = store.set_scope(args.ref, args.value, now, SOURCE)
        else:
            done = getattr(store, args.command)(args.ref, now, SOURCE)
        return reply({"ok": True, "recipient": _recipient(done)})
    except OutboxError as exc:
        return reply({"ok": False, "reason": f"Das ging nicht: {exc}"})


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
