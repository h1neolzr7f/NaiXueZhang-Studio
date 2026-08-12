"""Shared objects for the Core release runtime."""

from __future__ import annotations

import json
from pathlib import Path

from db import Database
from paths import normalize_config, project_root


ROOT = project_root()
CONFIG = normalize_config(json.loads((ROOT / "config.json").read_text(encoding="utf-8")), ROOT)
DATA_DIR = Path(CONFIG["data_dir"])
WEB_DIR = Path(CONFIG["web_dir"])
DB = Database(DATA_DIR / "aitag.db")
