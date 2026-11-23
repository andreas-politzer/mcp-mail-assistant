"""The copy of the database that is made before a schema upgrade."""

import os
import sqlite3

import pytest

from outbox_mcp.errors import ConfigError
from outbox_mcp.storage.database import Database


def _backup(path):
    return f"{path}.before-v{Database.SCHEMA_VERSION}"


def _make_old(path, version=6):
    """A database as an older program left it: current tables, an old version number, one row of our own."""
    db = Database(path)
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO settings(key, value, changed_at, changed_by) VALUES ('marker', 'before', 1, 'test')")
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()
    conn.close()
    return db


def test_a_new_database_gets_no_backup(tmp_path):
    path = tmp_path / "outbox.db"
    Database(path)
    assert not os.path.exists(_backup(path))


def test_an_upgrade_keeps_a_copy_of_the_old_state_with_owner_only_rights(tmp_path):
    path = tmp_path / "outbox.db"
    _make_old(path)
    Database(path)
    copy = _backup(path)
    assert os.path.exists(copy)
    conn = sqlite3.connect(copy)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    assert conn.execute("SELECT value FROM settings WHERE key='marker'").fetchone()[0] == "before"
    conn.close()
    if os.name == "posix":
        assert (os.stat(copy).st_mode & 0o777) == 0o600
    live = sqlite3.connect(path)
    assert live.execute("PRAGMA user_version").fetchone()[0] == Database.SCHEMA_VERSION
    live.close()
    assert not [f for f in os.listdir(tmp_path) if ".backup-" in f]  # no temporary file stays behind


def test_what_is_still_in_the_write_ahead_log_is_in_the_copy(tmp_path):
    path = tmp_path / "outbox.db"
    Database(path)
    holder = sqlite3.connect(path)  # keeps the log from being folded back into the main file
    holder.execute("INSERT INTO settings(key, value, changed_at, changed_by) VALUES ('only-in-log', 'yes', 1, 'test')")
    holder.execute("PRAGMA user_version = 6")
    holder.commit()
    assert os.path.getsize(f"{path}-wal") > 0
    Database(path)
    holder.close()
    conn = sqlite3.connect(_backup(path))
    assert conn.execute("SELECT value FROM settings WHERE key='only-in-log'").fetchone()[0] == "yes"
    conn.close()


def test_a_later_start_does_not_replace_the_first_copy(tmp_path):
    path = tmp_path / "outbox.db"
    _make_old(path)
    Database(path)
    copy = _backup(path)
    before = open(copy, "rb").read()
    # an older number again (as if a second process had read the version before the first finished)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE settings SET value='after' WHERE key='marker'")
    conn.execute("PRAGMA user_version = 6")
    conn.commit()
    conn.close()
    Database(path)
    assert open(copy, "rb").read() == before


def test_a_failed_backup_stops_the_upgrade_and_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "outbox.db"
    _make_old(path)

    def refuse(conn, destination):
        raise OSError("disk full")

    monkeypatch.setattr(Database, "_copy_database", staticmethod(refuse))
    with pytest.raises(ConfigError, match="backed up"):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 6
    conn.close()
    assert not os.path.exists(_backup(path))
    assert not [f for f in os.listdir(tmp_path) if ".backup-" in f]
