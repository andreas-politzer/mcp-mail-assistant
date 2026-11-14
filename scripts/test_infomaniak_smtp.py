#!/usr/bin/env python3
"""Manual end-to-end check of your real SMTP account (written for Infomaniak, works with any
provider that offers STARTTLS on port 587).

It goes through the real flow (prepare_email, then send_email) and sends exactly ONE test mail,
only to your own configured sender address. It never accepts another recipient.

Usage (from the project folder):

    uv run --env-file .env python scripts/test_infomaniak_smtp.py            # real test
    uv run --env-file .env.example python scripts/test_infomaniak_smtp.py --dry-run   # no network

Settings come only from the environment (your .env file). Passwords are never printed.
Nothing is retried, and there is no fallback to unencrypted connections.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional, TextIO

from outbox_mcp.config import Config
from outbox_mcp.errors import OutboxError
from outbox_mcp.mail.smtp_client import SmtpClient
from outbox_mcp.security.recipients import Recipient, RecipientList
from outbox_mcp.service import OutboxService, Sender
from outbox_mcp.storage.database import Database

CONFIRM_WORD = "SEND"
REQUIRED_PORT = 587
OWNER = "smtp-test"

EXIT_OK, EXIT_NOT_SENT, EXIT_ERROR = 0, 1, 2


def check_config(config: Config) -> Optional[str]:
    """Return a reason if this script must refuse to run with the given settings."""
    if config.smtp_security != "starttls":
        return "This test requires OUTBOX_SMTP_SECURITY=starttls."
    if config.smtp_port != REQUIRED_PORT:
        return f"This test requires port {REQUIRED_PORT} (STARTTLS)."
    return None


def _safe(text: str, config: Config) -> str:
    """Never let the password appear in output, even inside an error text."""
    if config.smtp_password:
        text = text.replace(config.smtp_password, "***")
    return " ".join(text.split())[:300]


def _message(stamp: str):
    subject = "outbox-mcp SMTP test (Grüße)"
    text = (
        "Hello,\n\n"
        "this is a test message from mcp-mail-assistant (scripts/test_infomaniak_smtp.py).\n"
        "If you can read this, SMTP delivery to your own mailbox works.\n\n"
        "Character check: Grüße, äöüß, €\n"
        f"Sent at (UTC): {stamp}\n"
    )
    return subject, text


def run(
    config: Config,
    sender: Sender,
    input_fn: Callable[[str], str],
    out: TextIO,
    dry_run: bool = False,
) -> int:
    def say(line: str = "") -> None:
        print(line, file=out)

    # The only possible recipient is the configured sender address itself.
    recipients = RecipientList([Recipient("Me", config.from_address)])
    with tempfile.TemporaryDirectory(prefix="outbox-smtp-test-") as tmp:
        cfg = dataclasses.replace(
            config, db_path=Path(tmp) / "test.db", max_sends_per_hour=1, max_sends_per_day=1
        )
        db = Database(cfg.db_path)
        service = OutboxService(cfg, recipients, db, sender, time.time)

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        subject, text = _message(stamp)
        draft = service.prepare_email(OWNER, "Me", subject, text)

        say("This test mail will be sent:")
        say(f"  From:    {config.from_address}")
        say(f"  To:      {draft['to_address']}   (your own address, the only allowed recipient)")
        say(f"  Subject: {draft['subject']}")
        say("  Text:")
        for line in draft["text"].splitlines():
            say(f"    | {line}")
        say(f"  Via:     {config.smtp_host}:{config.smtp_port} (STARTTLS), one attempt, no retry")
        say()

        if dry_run:
            say("Dry run: nothing was sent and no connection was made.")
            return EXIT_OK

        try:
            answer = input_fn(f"Type {CONFIRM_WORD} (capital letters) to send this one mail, anything else cancels: ")
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if answer.strip() != CONFIRM_WORD:
            service.cancel_draft(OWNER, draft["draft_id"])
            say("Cancelled. Nothing was sent.")
            return EXIT_NOT_SENT

        result = service.send_email(OWNER, draft["draft_id"])
        reason = db.get_draft(draft["draft_id"], OWNER).detail

        say("=== Result ===")
        say(f"Status: {result['status']}")
        if result["status"] == "sent":
            say("The mail server accepted the message. This does not yet prove delivery:")
            say("check your inbox (and the spam folder) and look at the umlauts in the text.")
            return EXIT_OK
        say(f"Note: {_safe(result['note'], config)}")
        if reason:
            say(f"Reason reported: {_safe(reason, config)}")
        if result["status"] == "sent_but_not_recorded":
            return EXIT_OK
        return EXIT_NOT_SENT


def main(
    argv: Optional[list] = None,
    env: Optional[Mapping[str, str]] = None,
    input_fn: Callable[[str], str] = input,
    out: Optional[TextIO] = None,
    stdin_is_tty: Optional[bool] = None,
    sender_factory: Callable[[Config], Sender] = lambda c: SmtpClient(
        c.smtp_host, c.smtp_port, c.smtp_user, c.smtp_password, c.smtp_security,
        c.smtp_timeout_seconds, c.smtp_helo,
    ),
) -> int:
    out = out or sys.stdout
    parser = argparse.ArgumentParser(description="Send one test mail to your own address.")
    parser.add_argument("--dry-run", action="store_true", help="show the mail, do not connect or send")
    args = parser.parse_args(argv)

    try:
        config = Config.from_env(env)
        problem = check_config(config)
        if problem:
            print(f"Refusing to run: {problem}", file=out)
            return EXIT_ERROR
        tty = sys.stdin.isatty() if stdin_is_tty is None else stdin_is_tty
        if not args.dry_run and not tty:
            print("Refusing to run: sending needs an interactive terminal for the confirmation.", file=out)
            return EXIT_ERROR
        return run(config, sender_factory(config), input_fn, out, args.dry_run)
    except OutboxError as exc:
        print(f"Error: {exc}", file=out)
    except Exception as exc:  # no traceback: it could expose settings
        print(f"Unexpected error: {exc.__class__.__name__}", file=out)
    return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
