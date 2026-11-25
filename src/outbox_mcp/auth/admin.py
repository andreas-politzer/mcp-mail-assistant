"""Small admin tool: ``revoke-all`` (emergency stop), ``status`` (counts only) and ``prune``
(delete clients that are not in use)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .store import AuthStore


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="outbox-mcp-auth")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("revoke-all", "status", "prune"):
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
    elif args.command == "prune":
        print(f"pruned: {store.prune_clients()} clients that were not in use were deleted")
    else:
        for table, count in store.counts().items():
            print(f"{table}: {count}")
        stats = store.client_stats()
        print(f"clients never signed in: {stats['never_signed_in']}")
        print(f"clients not in use (prune would delete): {stats['not_in_use']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
