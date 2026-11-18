"""One setting for where the data lives: OUTBOX_DATA_DIR.

A desktop app (or the wizard that sets it up) chooses the platform's usual folder for the user's
data and passes it here. Inside it the database, the sign-in database and the recipients file get
their default names. A single file can still be placed elsewhere with its own setting, and without
OUTBOX_DATA_DIR nothing changes: the defaults stay relative file names, as before.

The folder must be given as an absolute path, so that nothing depends on the directory a program
happens to be started from (Claude Desktop starts servers from "/").
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional

from .errors import ConfigError


def data_dir(env: Mapping[str, str]) -> Optional[Path]:
    raw = (env.get("OUTBOX_DATA_DIR") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise ConfigError("OUTBOX_DATA_DIR must be an absolute path.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            os.chmod(path, 0o700)
    except OSError as exc:
        raise ConfigError(f"The data folder {path} cannot be used ({exc.__class__.__name__}).") from None
    return path


def default_file(env: Mapping[str, str], name: str) -> Path:
    folder = data_dir(env)
    return folder / name if folder is not None else Path(name)
