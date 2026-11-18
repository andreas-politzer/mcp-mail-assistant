"""Administration of the recipient registry from the command line.

This is the only place where recipients are approved, rejected, blocked or added. It talks to
the database directly and needs no mail credentials. The AI connector has no access to it.
"""

from __future__ import annotations

import argparse
import getpass
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
    state = f"{r.status}, {r.scope}" if r.status == "active" else r.status
    print(f"#{r.id}  {r.name}  <{r.address}>  [{state}]  {_when(r.created_at)}{aliases}", file=out)
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
    approve.add_argument("--own", action="store_true", help="an address of your own (no approval outside the chat later)")
    add = sub.add_parser("add", help="add a recipient directly")
    add.add_argument("name")
    add.add_argument("address")
    add.add_argument("--alias", action="append", default=[])
    add.add_argument("--own", action="store_true", help="an address of your own (no approval outside the chat later)")
    scope = sub.add_parser("scope", help="mark an active recipient as your own address or as external")
    scope.add_argument("ref", help="id or address")
    scope.add_argument("value", choices=("own", "external"))
    pw = sub.add_parser("smtp-password", help="store, check or remove the SMTP password in the system keychain")
    pw.add_argument("action", choices=["set", "status", "delete"])
    pw.add_argument("--stdin", action="store_true", help="read the password from standard input (for scripts)")
    sub.add_parser("passkey-enroll", help="print a one-time link to register an approval passkey")
    sub.add_parser("passkey-list", help="show the registered approval passkeys")
    sub.add_parser("passkey-remove", help="remove an approval passkey").add_argument(
        "id", help="the first characters of its id (see passkey-list)")
    for command, text in (("reject", "reject a suggestion"), ("block", "block an address for good"),
                          ("unblock", "lift a block"), ("remove", "take an active recipient off the list")):
        sub.add_parser(command, help=text).add_argument("ref", help="id or address")
    return parser


def main(
    argv: Optional[List[str]] = None,
    env: Optional[Mapping[str, str]] = None,
    stdout: Optional[TextIO] = None,
    input_func: Callable[[str], str] = input,
    secret_func: Callable[[str], str] = getpass.getpass,
) -> int:
    out = stdout or sys.stdout
    environment = env if env is not None else __import__("os").environ
    args = _parser().parse_args(argv)
    if args.command == "smtp-password":
        return _smtp_password(args, environment, out, secret_func)
    db_path = args.db or environment.get("OUTBOX_DB_PATH", "").strip()
    if not db_path and (environment.get("OUTBOX_DATA_DIR") or "").strip():
        from .paths import default_file

        try:
            db_path = str(default_file(environment, "outbox.db"))
        except OutboxError as exc:
            print(f"Error: {exc}", file=out)
            return 2
    if not db_path:
        print("Error: OUTBOX_DB_PATH is not set (or use --db, or set OUTBOX_DATA_DIR).", file=out)
        return 2
    database = Database(db_path)
    store = RecipientStore(database)
    now = time.time()
    try:
        if args.command.startswith("passkey-"):
            return _passkeys(args, environment, database, out)
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
            done = store.approve(
                record.id, now, SOURCE, name=args.name, aliases=args.alias,
                scope="own" if args.own else "external",
            )
            print(f"Approved: {done.name} <{done.address}> [{done.scope}]", file=out)
        elif args.command == "add":
            done = store.add(
                args.name, args.address, args.alias, now, SOURCE, scope="own" if args.own else "external"
            )
            print(f"Added: {done.name} <{done.address}> [{done.scope}]", file=out)
        elif args.command == "scope":
            done = store.set_scope(args.ref, args.value, now, SOURCE)
            print(f"Scope: {done.name} <{done.address}> [{done.scope}]", file=out)
        else:
            action = getattr(store, args.command)
            done = action(args.ref, now, SOURCE)
            print(f"{args.command.capitalize()}: {done.name} <{done.address}> [{done.status}]", file=out)
    except OutboxError as exc:
        print(f"Error: {exc}", file=out)
        return 1
    return 0


def _smtp_password(args, environment, out: TextIO, secret_func: Callable[[str], str]) -> int:
    from . import secrets_store as keychain

    host = (environment.get("OUTBOX_SMTP_HOST") or "").strip()
    user = (environment.get("OUTBOX_SMTP_USER") or "").strip()
    if not host or not user:
        print("Error: OUTBOX_SMTP_HOST and OUTBOX_SMTP_USER must be set.", file=out)
        return 2
    label = f"{user}@{host}"
    try:
        if args.action == "set":
            if args.stdin:
                password = sys.stdin.readline().rstrip("\r\n")
            else:
                password = secret_func(f"SMTP password for {label}: ")
                if secret_func("Repeat: ") != password:
                    print("The two entries differ. Nothing stored.", file=out)
                    return 1
            keychain.set_smtp_password(host, user, password)
            print(f"Stored the SMTP password for {label} in {keychain.backend_name()}.", file=out)
        elif args.action == "delete":
            removed = keychain.delete_smtp_password(host, user)
            print("Removed from the keychain." if removed else "No password was stored.", file=out)
        else:
            if (environment.get("OUTBOX_SMTP_PASSWORD") or "").strip():
                print("OUTBOX_SMTP_PASSWORD is set in the environment and takes precedence.", file=out)
            stored = keychain.get_smtp_password(host, user) is not None
            print(f"Keychain ({keychain.backend_name()}): "
                  f"{'password stored' if stored else 'no password stored'} for {label}.", file=out)
    except OutboxError as exc:
        print(f"Error: {exc}", file=out)
        return 1
    return 0


def _passkeys(args, environment, database: Database, out: TextIO) -> int:
    from .approval.service import ApprovalConfig, ApprovalService

    config = ApprovalConfig.from_env(environment)
    if config is None:
        print("Error: OUTBOX_APPROVAL_HOST is not set. Set it to the public host name of the mail server.", file=out)
        return 2
    service = ApprovalService(database, config, time.time)
    if args.command == "passkey-enroll":
        note = None
        if config.is_local:
            # The local page runs inside the assistant's own process, possibly on a later port than
            # the configured one (another instance may hold it): print the link of the page that answers.
            from dataclasses import replace

            from .approval.local_listener import find_running_page

            port = find_running_page(config)
            if port is None:
                note = "No approval page answers right now. It starts together with the assistant; restart the assistant first."
            elif port != config.port:
                service.config = replace(config, port=port)
        token = service.create_enrollment()
        if token is None:
            print("The maximum number of passkeys is registered. Remove one first (passkey-remove).", file=out)
            return 1
        print("Open this link on the device that should hold the passkey. It works once, for 10 minutes:", file=out)
        print(service.enrollment_url(token), file=out)
        if note:
            print(note, file=out)
    elif args.command == "passkey-list":
        keys = service.list_passkeys()
        if not keys:
            print("No passkeys registered.", file=out)
        for k in keys:
            used = _when(k["last_used_at"]) if k["last_used_at"] else "never"
            print(f"{k['credential_id'][:12]}...  registered {_when(k['created_at'])}  last used {used}", file=out)
    else:
        if service.remove_passkey(args.id):
            print("Passkey removed.", file=out)
        else:
            print("No single passkey matches that id.", file=out)
            return 1
    return 0


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
