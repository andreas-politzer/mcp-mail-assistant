"""HTTP entry point: sign-in (OAuth) and the four mail tools in one process.

Stateless transport: every request stands alone, so the OAuth state in SQLite is all
that must survive. A restart ends running connections; the client then reconnects.
Binds to loopback only. Publish it through a tunnel or reverse proxy that serves HTTPS.
"""

from __future__ import annotations

import os
from typing import Mapping, Optional

from .approval.web import build_approval_routes
from .auth.app import create_authenticated_app
from .auth.config import AuthConfig
from .auth.store import AuthStore
from .config import Config
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
        app.router.routes.extend(build_approval_routes(service.approval))
    return app


def build_app_from_env(env: Optional[Mapping[str, str]] = None):
    e = os.environ if env is None else env
    auth_config = AuthConfig.from_env(e)
    service = build_service(Config.from_env(e), e)
    return build_http_app(auth_config, AuthStore(auth_config.db_path), service)


def main() -> None:
    import uvicorn

    port = int(os.environ.get("OUTBOX_HTTP_PORT", "8430"))
    uvicorn.run(build_app_from_env(), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
