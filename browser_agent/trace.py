"""Evidence of every tool call: a JSONL trace plus screenshots in runs/<run_id>/."""
from __future__ import annotations

import datetime as dt
import itertools
import json
import secrets
import time
from pathlib import Path

from playwright.async_api import Page

from .models import RunInfo

_counter = itertools.count(1)


class RunRecorder:
    def __init__(self, runs_dir: Path, tool: str):
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_id = f"{stamp}-{next(_counter):03d}-{tool}-{secrets.token_hex(2)}"
        self.dir = runs_dir / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.trace_path = self.dir / "trace.jsonl"
        self.screenshots: list[str] = []
        self.scripted_steps = 0
        self.cache_steps = 0
        self.model_steps = 0
        self.model_calls = 0
        self._started = time.monotonic()

    def event(self, kind: str, **data: object) -> None:
        record = {"t": dt.datetime.now().astimezone().isoformat(timespec="milliseconds"),
                  "elapsed_s": round(time.monotonic() - self._started, 3), "event": kind, **data}
        with self.trace_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    async def screenshot(self, page: Page, label: str) -> str:
        path = self.dir / f"{len(self.screenshots) + 1:02d}-{label}.png"
        await page.screenshot(path=str(path), full_page=True)
        self.screenshots.append(str(path))
        self.event("screenshot", label=label, file=path.name, url=page.url)
        return str(path)

    def info(self) -> RunInfo:
        return RunInfo(run_id=self.run_id, trace=str(self.trace_path), screenshots=list(self.screenshots),
                       seconds=round(time.monotonic() - self._started, 2), scripted_steps=self.scripted_steps,
                       cache_steps=self.cache_steps, model_steps=self.model_steps, model_calls=self.model_calls)
