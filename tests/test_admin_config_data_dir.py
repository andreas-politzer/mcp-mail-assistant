"""The admin process finds the database through OUTBOX_DATA_DIR, like the mail server does."""

import pytest

from outbox_mcp.admin.config import AdminConfig
from outbox_mcp.auth.passwords import hash_password
from outbox_mcp.errors import ConfigError


def env(tmp_path, **extra):
    base = {"OUTBOX_ADMIN_PASSPHRASE_HASH": hash_password("a long enough passphrase", n=2**4)}
    base.update(extra)
    return base


def test_data_dir_gives_the_database_path(tmp_path):
    folder = tmp_path / "data"
    config = AdminConfig.from_env(env(tmp_path, OUTBOX_DATA_DIR=str(folder)))
    assert config.db_path == folder / "outbox.db"
    assert folder.is_dir()


def test_a_single_path_still_wins(tmp_path):
    other = tmp_path / "elsewhere.db"
    config = AdminConfig.from_env(env(tmp_path, OUTBOX_DATA_DIR=str(tmp_path / "data"), OUTBOX_DB_PATH=str(other)))
    assert config.db_path == other


def test_neither_setting_is_still_an_error(tmp_path):
    with pytest.raises(ConfigError, match="OUTBOX_DB_PATH"):
        AdminConfig.from_env(env(tmp_path))


def test_a_relative_data_dir_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="absolute"):
        AdminConfig.from_env(env(tmp_path, OUTBOX_DATA_DIR="relative/folder"))
