"""Fake legacy web ERP / supplier portal used as the automation target. 演示数据均为虚构。"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fastapi import FastAPI

from .app import LAYOUTS, PortalSettings, create_portal_app, free_port, serve_in_background
from .db import PortalDB

__all__ = ["LAYOUTS", "PortalDB", "PortalSettings", "build_portal", "free_port", "serve_in_background"]


def build_portal(db_path: Path, data_dir: Path, fresh: bool) -> FastAPI:
    """Portal app on ``db_path``; the database is (re)created from the seed when ``fresh`` or missing."""
    seed = json.loads((data_dir / "portal_seed.json").read_text(encoding="utf-8"))
    db = PortalDB.create(db_path, data_dir) if fresh or not db_path.exists() else PortalDB(db_path)
    return create_portal_app(db, dt.date.fromisoformat(seed["system_date"]))
