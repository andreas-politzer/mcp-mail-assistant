"""Small admin tool: ``revoke-all`` (emergency stop) and ``status`` (counts only)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import AuthStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="outbox-mcp-auth")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("revoke-all", "status"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True, help="path of the auth database")
    args = parser.parse_args(argv)

    path = Path(args.db)
    if not path.exists():
        print(f"Database not found: {path}", file=sys.stderr)
        return 2
    store = AuthStore(path)
    if args.command == "revoke-all":
        store.revoke_all()
        print("revoked: all clients, logins, codes and tokens were deleted")
    else:
        for table, count in store.counts().items():
            print(f"{table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
