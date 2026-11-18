"""Human approval of every write. Requests live in SQLite so any approver can answer them:
the CLI prompt of the orchestrator, the local approval web page, or (demo/eval only) an explicit auto-approve flag.

The MCP tool waits for the decision; there is deliberately no tool that lets the calling model approve itself.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

Decision = Literal["pending", "approved", "rejected"]

SCHEMA = """CREATE TABLE IF NOT EXISTS approvals (
    key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, preview TEXT NOT NULL, screenshot TEXT,
    status TEXT NOT NULL, decided_by TEXT, note TEXT, created_at TEXT NOT NULL, decided_at TEXT)"""


class ApprovalRequest(BaseModel):
    key: str
    payload_hash: str
    preview: dict
    screenshot: str | None
    status: Decision
    decided_by: str | None = None
    note: str | None = None
    created_at: str
    decided_at: str | None = None


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class ApprovalStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(SCHEMA)

    def _rows(self, sql: str, args: tuple = ()) -> list[ApprovalRequest]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, args).fetchall()
        return [ApprovalRequest(**{**dict(r), "preview": json.loads(r["preview"])}) for r in rows]

    def get(self, key: str) -> ApprovalRequest | None:
        rows = self._rows("SELECT * FROM approvals WHERE key = ?", (key,))
        return rows[0] if rows else None

    def pending(self) -> list[ApprovalRequest]:
        return self._rows("SELECT * FROM approvals WHERE status = 'pending' ORDER BY created_at")

    def recent(self, limit: int = 20) -> list[ApprovalRequest]:
        return self._rows("SELECT * FROM approvals ORDER BY created_at DESC LIMIT ?", (limit,))

    def request(self, key: str, payload_hash: str, preview: dict, screenshot: str | None) -> ApprovalRequest:
        """Create the request, or refresh the evidence of a still-pending one. A decision is never overwritten."""
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            conn.execute("INSERT INTO approvals (key, payload_hash, preview, screenshot, status, created_at) "
                         "VALUES (?, ?, ?, ?, 'pending', ?) ON CONFLICT(key) DO UPDATE SET preview = excluded.preview, "
                         "screenshot = excluded.screenshot WHERE approvals.status = 'pending'",
                         (key, payload_hash, json.dumps(preview, ensure_ascii=False), screenshot, _now()))
        request = self.get(key)
        assert request is not None
        return request

    def decide(self, key: str, approve: bool, by: str, note: str = "") -> bool:
        """Record a decision on a pending request; False if it was already decided or does not exist."""
        with closing(sqlite3.connect(self.path, timeout=10)) as conn, conn:
            cursor = conn.execute("UPDATE approvals SET status = ?, decided_by = ?, note = ?, decided_at = ? "
                                  "WHERE key = ? AND status = 'pending'",
                                  ("approved" if approve else "rejected", by, note, _now(), key))
            return cursor.rowcount == 1

    async def wait(self, key: str, timeout_s: float, poll_s: float = 0.3) -> ApprovalRequest:
        deadline = time.monotonic() + timeout_s
        while True:
            request = self.get(key)
            assert request is not None
            if request.status != "pending" or time.monotonic() >= deadline:
                return request
            await asyncio.sleep(poll_s)


def format_preview(preview: dict) -> str:
    lines = [f"客户：{preview['customer']}    客户单号：{preview['customer_po']}",
             f"单据日期：{preview['order_date']}    交货日期：{preview.get('delivery_date') or '（无）'}",
             f"明细 {len(preview['lines'])} 行，合计 {preview['total']:.2f}（门户确认页显示 {preview['portal_total']:.2f}）"]
    lines += [f"  {line['sku']}  × {line['qty']:g}  @ {line['unit_price']:.2f}" for line in preview["lines"]]
    lines.append(f"幂等键：{preview['idempotency_key']}    确认页截图：{preview.get('screenshot') or '无'}")
    return "\n".join(lines)


async def run_approver(store: ApprovalStore, decide: Callable[[ApprovalRequest], tuple[bool, str] | None],
                       stop: asyncio.Event, poll_s: float = 0.3) -> None:
    """Answer pending requests with ``decide`` (None = leave pending) until ``stop`` is set."""
    while not stop.is_set():
        for request in store.pending():
            verdict = decide(request)
            if verdict is not None:
                store.decide(request.key, verdict[0], verdict[1])
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_s)
        except TimeoutError:
            pass


def auto_decider(reject_keys: frozenset[str] = frozenset(),
                 by: str = "auto-approve（演示开关）") -> Callable[[ApprovalRequest], tuple[bool, str]]:
    return lambda request: (request.key not in reject_keys, by)


async def cli_approver(store: ApprovalStore, stop: asyncio.Event, poll_s: float = 0.3) -> None:
    """Ask in the terminal for each pending request (y = approve, anything else = reject)."""
    asked: set[str] = set()
    while not stop.is_set():
        for request in store.pending():
            if request.key in asked:
                continue
            asked.add(request.key)
            print("\n=== 待审批：新建销售订单草稿 ===\n" + format_preview(request.preview), flush=True)
            answer = await asyncio.to_thread(input, "批准提交吗？[y/N] ")
            approved = answer.strip().lower() in ("y", "yes", "是")
            if store.decide(request.key, approved, "cli"):
                print("已批准，开始提交。" if approved else "已驳回，不会提交。", flush=True)
            else:
                print("这条请求已在审批页面上处理过，以那边的决定为准。", flush=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=poll_s)
        except TimeoutError:
            pass
