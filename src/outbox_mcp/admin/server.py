"""Entry point of the admin API. Listens on 127.0.0.1 only and has no route through the public tunnel."""

from __future__ import annotations

import os

from .app import build_admin_app
from .config import AdminConfig


def main() -> None:
    import uvicorn

    port = int(os.environ.get("OUTBOX_ADMIN_PORT", "8431"))
    uvicorn.run(build_admin_app(AdminConfig.from_env()), host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
