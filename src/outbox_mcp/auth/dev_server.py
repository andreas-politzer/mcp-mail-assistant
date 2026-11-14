"""Local development server with a dummy ``ping`` tool. Sends no mail."""

from __future__ import annotations

import os
from typing import Mapping, Optional

from .app import create_authenticated_app
from .config import AuthConfig
from .store import AuthStore


def _add_ping(mcp) -> None:
    @mcp.tool()
    def ping() -> str:
        """Harmless test tool."""
        return "pong"


def build_app(env: Optional[Mapping[str, str]] = None):
    config = AuthConfig.from_env(env if env is not None else os.environ)
    return create_authenticated_app(config, AuthStore(config.db_path), configure=_add_ping)


def main() -> None:
    import uvicorn

    uvicorn.run(build_app(), host="127.0.0.1", port=int(os.environ.get("PORT", "8430")))


if __name__ == "__main__":
    main()
