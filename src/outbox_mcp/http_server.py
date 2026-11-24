"""HTTP entry point: sign-in (OAuth) and the four mail tools in one process.

Stateless transport: every request stands alone, so the OAuth state in SQLite is all
that must survive. A restart ends running connections; the client then reconnects.
Binds to loopback only. Publish it through a tunnel or reverse proxy that serves HTTPS.
"""

from __future__ import annotations

import os
import sys
import threading
from dataclasses import replace
from typing import Mapping, Optional

from .approval.web import build_approval_routes
from .auth.app import create_authenticated_app
from .auth.config import AuthConfig
from .auth.store import AuthStore
from .config import Config
from .probe import MODE_MATRIX, MODE_PING_ROOT, PROBE_OPTIONS, register_probe_tools
from .probe_matrix import VariantSelector, register_matrix_tools
from .probe import mode_from_env as probe_mode_from_env
from .remote import with_remote_file
from .server import SERVER_OPTIONS, build_service, owner_from_token, register_tools
from .service import OutboxService


def build_http_app(
    auth_config: AuthConfig,
    store: AuthStore,
    service: OutboxService,
    *,
    json_response: bool = False,
    probe: Optional[str] = None,
):
    if probe:
        # Diagnosis only: one harmless tool instead of the mail tools (see probe.py).
        configure = register_matrix_tools if probe == MODE_MATRIX else register_probe_tools
        options, name = PROBE_OPTIONS, "probe"
    else:
        configure = lambda mcp: register_tools(mcp, service, owner_from_token)  # noqa: E731
        options, name = SERVER_OPTIONS, "outbox"
    app = create_authenticated_app(
        auth_config,
        store,
        configure=configure,
        json_response=json_response,
        stateless_http=True,
        name=name,
        server_options=options,
    )
    app.state.mcp_path = auth_config.mcp_path
    app.state.probe = probe
    if probe == MODE_MATRIX:
        app.add_middleware(VariantSelector, mcp_path=auth_config.mcp_path)
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
    probe = probe_mode_from_env(e.get("OUTBOX_PROBE"))
    if probe == MODE_PING_ROOT:
        # Diagnosis only: tool endpoint at "/" and no special scope. Only `ping` is reachable (see probe.py).
        auth_config = replace(auth_config, mcp_path="/", scopes=())
    service = build_service(Config.from_env(e), e)
    if service.approval is not None and service.approval.config.is_local:
        from .errors import ConfigError

        raise ConfigError(
            "OUTBOX_APPROVAL_HOST=localhost is for the stdio server. The HTTP server needs the "
            "public host name its approval links are reached under."
        )
    return build_http_app(auth_config, AuthStore(auth_config.db_path), service, probe=probe)


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
    from .compat import LegacyEra, mode_from_env

    mcp_path = getattr(getattr(app, "state", None), "mcp_path", "/mcp")
    if getattr(getattr(app, "state", None), "probe", None):
        print(
            f"Mail Assistant: PROBE MODE '{app.state.probe}' (OUTBOX_PROBE): only ping (or dummy tools) is offered, "
            "no real mail tool. Remove the setting to go back to normal.",
            file=sys.stderr,
            flush=True,
        )
    legacy_mode = mode_from_env(os.environ.get("OUTBOX_LEGACY_ERA"))
    if legacy_mode is not None:
        # Off by default. Makes the server look like one of the older protocol generation (see compat.py).
        app = LegacyEra(app, legacy_mode, mcp_path)
        print(f"Mail Assistant: compatibility mode '{legacy_mode}' on (OUTBOX_LEGACY_ERA).", file=sys.stderr, flush=True)
    if os.environ.get("OUTBOX_DEBUG_HTTP", "").strip().lower() in {"1", "true", "yes"}:
        from .diag import RequestLog

        app = RequestLog(app, mcp_path=mcp_path)
        print("Mail Assistant: request log on (OUTBOX_DEBUG_HTTP), lines start with DIAG.", file=sys.stderr, flush=True)
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
