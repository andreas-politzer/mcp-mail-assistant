"""Administration of the recipient registry from the command line.

This is the only place where recipients are approved, rejected, blocked or added. It talks to
the database directly and needs no mail credentials. The AI connector has no access to it.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from typing import Callable, List, Mapping, Optional, TextIO

from .errors import OutboxError
from .storage.database import Database
from .storage.recipient_store import RecipientRecord, RecipientStore

SOURCE = "admin:cli"


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _describe(r: RecipientRecord, out: TextIO) -> None:
    aliases = f"  aliases: {', '.join(r.aliases)}" if r.aliases else ""
    print(f"#{r.id}  {r.name}  <{r.address}>  [{r.status}]  {_when(r.created_at)}{aliases}", file=out)
    if r.note:
        print(f"      Note from Claude (not verified): {r.note}", file=out)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="outbox-admin", description="Manage the allowed recipients.")
    parser.add_argument("--db", help="path of the mail database (default: OUTBOX_DB_PATH)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pending", help="show open suggestions")
    sub.add_parser("list", help="show all recipients with their status")
    approve = sub.add_parser("approve", help="approve a suggestion")
    approve.add_argument("ref", help="id or address")
    approve.add_argument("--name")
    approve.add_argument("--alias", action="append", default=[])
    approve.add_argument("--yes", action="store_true", help="do not ask")
    add = sub.add_parser("add", help="add a recipient directly")
    add.add_argument("name")
    add.add_argument("address")
    add.add_argument("--alias", action="append", default=[])
    for command, text in (("reject", "reject a suggestion"), ("block", "block an address for good"),
                          ("unblock", "lift a block"), ("remove", "take an active recipient off the list")):
        sub.add_parser(command, help=text).add_argument("ref", help="id or address")
    return parser


def main(
    argv: Optional[List[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
    input_func: Callable[[str], str] = input,
) -> int:
    out = stdout or sys.stdout
    environment = env if env is not None else __import__("os").environ
    args = _parser().parse_args(argv)
    db_path = args.db or environment.get("OUTBOX_DB_PATH", "").strip()
    if not db_path:
        print("Error: OUTBOX_DB_PATH is not set (or use --db).", file=out)
        return 2
    store = RecipientStore(Database(db_path))
    now = time.time()
    try:
        if args.command == "pending":
            records = store.pending()
            if not records:
                print("No open suggestions.", file=out)
            for r in records:
                _describe(r, out)
        elif args.command == "list":
            for r in store.all_records():
                _describe(r, out)
        elif args.command == "approve":
            record = store.find(args.ref)
            print(f"Name:    {args.name or record.name}\nAddress: {record.address}", file=out)
            if args.alias:
                print(f"Aliases: {', '.join(args.alias)}", file=out)
            if record.note:
                print(f"Note from Claude (not verified): {record.note}", file=out)
            if not args.yes and input_func("Approve this recipient? [j/N] ").strip().lower() not in (
                "j", "ja", "y", "yes",
            ):
                print("Not approved.", file=out)
                return 0
            done = store.approve(record.id, now, SOURCE, name=args.name, aliases=args.alias)
            print(f"Approved: {done.name} <{done.address}>", file=out)
        elif args.command == "add":
            done = store.add(args.name, args.address, args.alias, now, SOURCE)
            print(f"Added: {done.name} <{done.address}>", file=out)
        else:
            action = getattr(store, args.command)
            done = action(args.ref, now, SOURCE)
            print(f"{args.command.capitalize()}: {done.name} <{done.address}> [{done.status}]", file=out)
    except OutboxError as exc:
        print(f"Error: {exc}", file=out)
        return 1
    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
