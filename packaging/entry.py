"""Entry point of the bundled program (PyInstaller): one executable, several commands.

    serve    the HTTP server (phone and remote use)
    stdio    the server for Claude Desktop, over standard input and output
    admin    the command line for recipients, passkeys and the keychain password
    setup    the steps of the setup wizard (one line of JSON per command)
    local    local administration for the desktop app (one line of JSON per command)
    version  print the version

Build it with PyInstaller (onedir) from this file; the desktop app starts the result with these commands.
"""

from __future__ import annotations

import sys

COMMANDS = ("serve", "stdio", "admin", "setup", "local", "version")


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("usage: {" + "|".join(COMMANDS) + "}", file=sys.stderr)
        return 2
    command = sys.argv[1]
    del sys.argv[1]
    if command == "version":
        from importlib.metadata import PackageNotFoundError, version

        try:
            print(version("outbox-mcp"))
        except PackageNotFoundError:
            print("unknown")
        return 0
    if command == "serve":
        from outbox_mcp.http_server import main as run
    elif command == "stdio":
        from outbox_mcp.server import main as run
    elif command == "setup":
        from outbox_mcp.setup_cli import run
    elif command == "local":
        from outbox_mcp.local_cli import run
    else:
        from outbox_mcp.admin_cli import run
    result = run()
    return result if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
