"""Idempotency ledger: what this agent has done per idempotency key (SQLite, survives restarts).

The portal itself has no idempotency, so the key is also written into the order's 备注 field; before entering an
order the agent searches the portal for it. The ledger is the fast path; the portal is the source of truth.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

LedgerStatus = Literal["submitting", "verified", "unverified", "rejected", "portal_rejected"]

SCHEMA = """CREATE TABLE IF NOT EXISTS ledger (
    key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, customer_id TEXT NOT NULL, customer_po TEXT NOT NULL,
    status TEXT NOT NULL, order_no TEXT, updated_at TEXT NOT NULL)"""


class LedgerEntry(BaseModel):
    key: str
    payload_hash: str
    customer_id: str
    customer_po: str
    status: LedgerStatus
    order_no: str | None = None
    updated_at: str


class Ledger:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(SCHEMA)

    def get(self, key: str) -> LedgerEntry | None:
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM ledger WHERE key = ?", (key,)).fetchone()
        return LedgerEntry(**dict(row)) if row else None

    def put(self, key: str, payload_hash: str, customer_id: str, customer_po: str, status: LedgerStatus,
            order_no: str | None = None) -> None:
        now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO ledger VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(key) DO UPDATE SET "
                         "status = excluded.status, order_no = COALESCE(excluded.order_no, ledger.order_no), "
                         "updated_at = excluded.updated_at",
                         (key, payload_hash, customer_id, customer_po, status, order_no, now))
