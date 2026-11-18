"""OUTBOX_DATA_DIR: one folder for the user's data, independent of where the program sits."""

import io
import os
import stat

import pytest

from outbox_mcp import admin_cli
from outbox_mcp.auth.config import AuthConfig
from outbox_mcp.config import Config
from outbox_mcp.errors import ConfigError
from outbox_mcp.server import build_service
from outbox_mcp.storage.database import Database

BASE = {
    "OUTBOX_SMTP_HOST": "localhost", "OUTBOX_SMTP_PORT": "2525", "OUTBOX_SMTP_SECURITY": "none",
    "OUTBOX_FROM_ADDRESS": "me@example.com",
}


def _auth_env(extra):
    from outbox_mcp.auth.passwords import hash_password

    return {"OUTBOX_PUBLIC_BASE_URL": "https://example.org",
            "OUTBOX_OAUTH_PASSWORD_HASH": hash_password("pw", n=2**4), **extra}


def test_defaults_live_inside_the_data_folder(tmp_path):
    folder = tmp_path / "Mail Assistant Müller (Ünï)"
    c = Config.from_env({**BASE, "OUTBOX_DATA_DIR": str(folder)})
    assert c.db_path == folder / "outbox.db" and c.recipients_file == folder / "recipients.json"
    assert AuthConfig.from_env(_auth_env({"OUTBOX_DATA_DIR": str(folder)})).db_path == folder / "auth.db"
    assert folder.is_dir()
    if os.name == "posix":
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700


def test_a_single_file_can_still_go_elsewhere(tmp_path):
    c = Config.from_env({**BASE, "OUTBOX_DATA_DIR": str(tmp_path / "d"), "OUTBOX_DB_PATH": str(tmp_path / "x.db")})
    assert c.db_path == tmp_path / "x.db" and c.recipients_file == tmp_path / "d" / "recipients.json"


def test_without_the_setting_nothing_changes():
    c = Config.from_env(BASE)
    assert str(c.db_path) == "outbox.db" and str(c.recipients_file) == "recipients.json"


def test_the_folder_must_be_absolute():
    with pytest.raises(ConfigError):
        Config.from_env({**BASE, "OUTBOX_DATA_DIR": "relative/folder"})


def test_an_unusable_folder_gives_a_readable_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(ConfigError) as e:
        Config.from_env({**BASE, "OUTBOX_DATA_DIR": str(blocker / "sub")})
    assert "cannot be used" in str(e.value)


def test_the_service_works_in_the_folder_and_after_it_is_moved(tmp_path):
    first = tmp_path / "one" / "Mail Assistant"
    env = {**BASE, "OUTBOX_DATA_DIR": str(first)}
    service = build_service(Config.from_env(env), env)
    assert (first / "outbox.db").exists()
    service.recipient_store.add("Me", "me@example.com", [], 1000.0, "test", scope="own")
    del service
    moved = tmp_path / "two" / "Neuer Ort"
    moved.parent.mkdir()
    first.rename(moved)
    env2 = {**BASE, "OUTBOX_DATA_DIR": str(moved)}
    again = build_service(Config.from_env(env2), env2)
    assert [r.address for r in again.recipient_store.all_records()] == ["me@example.com"]


def test_admin_finds_the_database_through_the_folder(tmp_path):
    folder = tmp_path / "Daten"
    out = io.StringIO()
    env = {"OUTBOX_DATA_DIR": str(folder)}
    assert admin_cli.main(["add", "Test", "t@example.com"], env=env, stdout=out) == 0
    assert (folder / "outbox.db").exists()
    out = io.StringIO()
    assert admin_cli.main(["list"], env=env, stdout=out) == 0 and "t@example.com" in out.getvalue()
