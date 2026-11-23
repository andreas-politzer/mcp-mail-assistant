"""HTTP entry point: sign-in (OAuth) and the four mail tools in one process.

Stateless transport: every request stands alone, so the OAuth state in SQLite is all
that must survive. A restart ends running connections; the client then reconnects.
Binds to loopback only. Publish it through a tunnel or reverse proxy that serves HTTPS.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Mapping, Optional

from .approval.web import build_approval_routes
from .auth.app import create_authenticated_app
from .auth.config import AuthConfig
from .auth.store import AuthStore
from .config import Config
from .remote import with_remote_file
from .server import build_service, owner_from_token, register_tools
from .service import OutboxService


def build_http_app(
    auth_config: AuthConfig,
    store: AuthStore,
    service: OutboxService,
    *,
    json_response: bool = False,
):
    app = create_authenticated_app(
        auth_config,
        store,
        configure=lambda mcp: register_tools(mcp, service, owner_from_token),
        json_response=json_response,
        stateless_http=True,
        name="outbox",
    )
    if service.approval is not None:
        # The approval and enrollment pages live in the public process; they need no sign-in
        # because the passkey is the proof.
        app.router.routes.extend(build_approval_routes(service.approval, send=service.send_after_approval))
    return app


def build_app_from_env(env: Optional[Mapping[str, str]] = None):
    # remote.json (public address, passphrase hash) and mailbox.json (read in Config) fill in what the
    # environment leaves empty; the environment always wins.
    e = with_remote_file(os.environ if env is None else env)
    auth_config = AuthConfig.from_env(e)
    service = build_service(Config.from_env(e), e)
    if service.approval is not None and service.approval.config.is_local:
        from .errors import ConfigError

        raise ConfigError(
            "OUTBOX_APPROVAL_HOST=localhost is for the stdio server. The HTTP server needs the "
            "public host name its approval links are reached under."
        )
    return build_http_app(auth_config, AuthStore(auth_config.db_path), service)


def _exit_when_stdin_closes(server) -> None:
    """Stop the server when standard input reaches end of file.

    A desktop app that starts the server keeps a pipe to its standard input. When the app ends,
    however it ends (quit, crash, kill), the system closes the pipe and the server follows
    instead of lingering with the port taken.
    """

    def watch() -> None:
        try:
            while sys.stdin.buffer.read(4096):
                pass
        except (OSError, ValueError):
            pass
        server.should_exit = True

    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


def main() -> None:
    import uvicorn

    port = int(os.environ.get("OUTBOX_HTTP_PORT", "8430"))
    from .errors import ConfigError

    try:
        app = build_app_from_env()
    except ConfigError as exc:
        print(f"Mail Assistant: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if os.environ.get("OUTBOX_EXIT_WHEN_STDIN_CLOSES", "").strip().lower() in {"1", "true", "yes"}:
        # Off by default: with no input attached (a service, a terminal in the background)
        # the server would stop at once.
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, timeout_graceful_shutdown=5)
        )
        _exit_when_stdin_closes(server)
        server.run()
        return
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
