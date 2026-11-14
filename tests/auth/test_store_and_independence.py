import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_database_file_is_private(store, auth_config):
    mode = stat.S_IMODE(os.stat(auth_config.db_path).st_mode)
    assert mode == 0o600


def test_auth_package_does_not_depend_on_the_mail_code():
    """Authentication must be testable (and replaceable) without any mail code."""
    code = (
        "import sys; import outbox_mcp.auth.app, outbox_mcp.auth.admin;"
        "bad = sorted(m for m in sys.modules if m in ("
        "'outbox_mcp.service','outbox_mcp.server','outbox_mcp.mail','outbox_mcp.mail.smtp_client',"
        "'outbox_mcp.storage','outbox_mcp.storage.database','outbox_mcp.security.recipients'));"
        "print(bad)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(SRC)}, check=True,
    ).stdout.strip()
    assert out == "[]"


def test_the_verifier_reads_the_store_directly():
    """No HTTP introspection hop: the verifier holds the store and nothing else."""
    from outbox_mcp.auth import verifier

    source = Path(verifier.__file__).read_text()
    assert "httpx" not in source and "requests" not in source and "urllib" not in source
