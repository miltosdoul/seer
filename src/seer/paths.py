"""
Canonical on-disk locations, following the XDG base directory convention:
settings under ~/.config/seer, application data (the synced per-cluster
SQLite databases) under ~/.local/share/seer. The XDG_* environment
variables override the defaults, as usual.
"""

from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_var: str, fallback: str) -> Path:
    value = os.environ.get(env_var, "").strip()
    return Path(value).expanduser() if value else Path.home() / fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / "seer"
CONFIG_FILE = CONFIG_DIR / "config.yaml"

DATA_DIR = _xdg("XDG_DATA_HOME", ".local/share") / "seer"
