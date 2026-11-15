"""Create the hash for the admin passphrase. The passphrase is typed hidden and never printed.

    python -m outbox_mcp.admin.passphrase
"""

from __future__ import annotations

import getpass
import sys

from ..auth.passwords import hash_password


def main() -> int:
    first = getpass.getpass("New admin passphrase (min. 12 characters): ")
    if len(first) < 12:
        print("Too short.", file=sys.stderr)
        return 2
    if getpass.getpass("Repeat: ") != first:
        print("The two entries differ.", file=sys.stderr)
        return 2
    print("\nPut this line into the admin .env file:\n")
    print(f"OUTBOX_ADMIN_PASSPHRASE_HASH={hash_password(first)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
