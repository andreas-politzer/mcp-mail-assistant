"""A real admin API for the front-end tests: temporary database, a notes folder, a known passphrase.

Started by tests/global-setup.ts. Needs the repository's Python environment (see tests/README.md).
"""

import sys
import tempfile
import time
from pathlib import Path

import uvicorn

from outbox_mcp.admin.app import build_admin_app
from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.storage.database import Database

PASSPHRASE = "test passphrase for the front end"


def main(port: int) -> None:
    work = Path(tempfile.mkdtemp(prefix="admin-ui-test-"))
    notes = work / "notes"
    (notes / "Projekte").mkdir(parents=True)
    (notes / "Privat").mkdir()
    (notes / "Projekte" / "plan.md").write_text("plan", encoding="utf-8")
    (notes / "Privat" / "tagebuch.md").write_text("privat", encoding="utf-8")
    (notes / "start.md").write_text("start", encoding="utf-8")
    db_path = work / "mail.db"
    Database(db_path).ensure_server_folder_source(notes, time.time())
    config = AdminConfig(
        db_path=db_path,
        passphrase_hash=hash_password(PASSPHRASE, n=2**4),
        allowed_hosts=("127.0.0.1", "localhost"),
        allowed_origins=("http://localhost:5180",),
        attachment_root=notes,
    )
    uvicorn.run(build_admin_app(config), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main(int(sys.argv[1]))
