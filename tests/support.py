"""Shared test helpers: a fresh portal per test, settings pointing at a temp folder, a fake model client."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from browser_agent.config import PROJECT_ROOT, Settings
from browser_agent.llm import Message, ModelGateway, Usage
from browser_agent.portal import PortalDB, PortalSettings, build_portal, free_port, serve_in_background

DATA = PROJECT_ROOT / "data"
TASKS = json.loads((DATA / "eval_tasks.json").read_text(encoding="utf-8"))["tasks"]


def task(task_id: str) -> dict:
    return next(t for t in TASKS if t["id"] == task_id)


@dataclass
class Portal:
    app: FastAPI
    url: str
    out: Path

    @property
    def db(self) -> PortalDB:
        return self.app.state.portal.db

    def set(self, layout: str = "v1", submit_delay_once: float = 0.0, drop_next_submit: bool = False) -> None:
        self.app.state.portal.settings = PortalSettings(layout=layout, submit_delay_once=submit_delay_once,
                                                        drop_next_submit=drop_next_submit)

    def settings(self, **changes: object) -> Settings:
        base = Settings(data_dir=DATA, out_dir=self.out, portal_url=self.url, approval_timeout_s=10.0,
                        approval_web=False)
        return base.with_changes(**changes)


@contextmanager
def running_portal(layout: str = "v1") -> Iterator[Portal]:
    out = Path(tempfile.mkdtemp(prefix="browser_agent_test_"))
    try:
        app = build_portal(out / "portal.db", DATA, fresh=True)
        portal = Portal(app, "", out)
        portal.set(layout)
        with serve_in_background(app, free_port()) as url:
            portal.url = url
            yield portal
    finally:
        shutil.rmtree(out, ignore_errors=True)


class FakeStockModel:
    """Stands in for the model on layout v2's stock page: picks elements by label keywords, reads stock cards.

    Only used to test the fallback plumbing (validation, caching, grounding) without network calls.
    """

    KEYWORDS = {"登录账号": "工号", "登录密码": "登录口令", "登录按钮": "进入系统", "库存查询（存货查询": "存货查询",
                "物料编码（存货编码）": "存货编码", "查询按钮": "查询库存"}

    def __init__(self) -> None:
        self.usage = Usage()
        self.prompts: list[str] = []

    def complete(self, messages: list[Message], *, model: str) -> str:
        prompt = str(messages[-1]["content"])
        self.prompts.append(prompt)
        self.usage.add({"prompt_tokens": len(prompt), "completion_tokens": 20})
        if prompt.startswith("要提取"):
            return json.dumps(self._extract(prompt), ensure_ascii=False)
        step = prompt.split("\n", 1)[0]
        keyword = next(v for k, v in self.KEYWORDS.items() if k in step)
        ref = next(line.split(" | ")[0] for line in prompt.splitlines()
                   if re.match(r"e\d+ \|", line) and keyword in line)
        if "允许的操作：click" in prompt:
            return json.dumps({"actions": [{"ref": ref, "op": "click"}], "reason": "fake"})
        values = json.loads(re.search(r"允许的值：(\[.*\])", prompt).group(1))
        return json.dumps({"actions": [{"ref": ref, "op": "fill", "value": values[0]}], "reason": "fake"})

    @staticmethod
    def _extract(prompt: str) -> dict:
        text = prompt.split("页面文字：\n", 1)[1]
        cards = re.findall(r"存货编码：(\S+)\s+计量单位：(\S+)\s+现存量：(\S+)\s+可用量：(\S+)\s+状态：(\S+)", text)
        items = [{"sku": sku, "name": "x", "spec": "x", "unit": unit, "on_hand": float(on_hand),
                  "available": float(available), "status": status} for sku, unit, on_hand, available, status in cards]
        for item in items:  # name/spec must be grounded too: take them from the card heading
            heading = re.search(r"(\S+) (.+?)\s+存货编码：" + re.escape(item["sku"]), text)
            item["name"], item["spec"] = heading.group(1), heading.group(2)
        return {"found": bool(items), "items": items}


def fake_gateway(client: FakeStockModel) -> ModelGateway:
    return ModelGateway(client, "fake-model", "fake")
