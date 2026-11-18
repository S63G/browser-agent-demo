"""Record/replay of model calls, the idempotency ledger and the approval store."""
from __future__ import annotations

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import BaseModel

from browser_agent.approval import ApprovalStore, auto_decider, run_approver
from browser_agent.approval_web import create_approval_app
from browser_agent.ledger import Ledger
from browser_agent.llm import LLMError, Message, ModelGateway, RecordingClient, ReplayClient, Usage
from browser_agent.models import OrderDraft

from .support import task


class ScriptedClient:
    """Returns canned responses in order; records how often it was called."""

    def __init__(self, *responses: str):
        self.responses = list(responses)
        self.usage = Usage()
        self.last_usage = {"prompt_tokens": 10, "completion_tokens": 2}

    def complete(self, messages: list[Message], *, model: str) -> str:
        self.usage.add(self.last_usage)
        return self.responses.pop(0)


class Answer(BaseModel):
    value: int


class TempDirTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


class LlmTest(TempDirTest):
    MESSAGES: list[Message] = [{"role": "user", "content": "q"}]

    def test_recorded_calls_replay_in_order_and_misses_fail(self) -> None:
        recorder = RecordingClient(ScriptedClient('{"value": 1}', '{"value": 2}'), self.tmp, "deepseek")  # type: ignore[arg-type]
        recorder.complete(self.MESSAGES, model="m")
        recorder.complete(self.MESSAGES, model="m")
        record = json.loads(next(self.tmp.glob("*.json")).read_text(encoding="utf-8"))
        self.assertEqual((record["source"], len(record["calls"])), ("live", 2))
        replay = ReplayClient(self.tmp)
        self.assertEqual([replay.complete(self.MESSAGES, model="m") for _ in range(2)], ['{"value": 1}', '{"value": 2}'])
        self.assertEqual(replay.usage.calls, 2)
        with self.assertRaises(LLMError):
            replay.complete([{"role": "user", "content": "other"}], model="m")

    def test_replay_refuses_records_not_made_live(self) -> None:
        recorder = RecordingClient(ScriptedClient("{}"), self.tmp, "deepseek")  # type: ignore[arg-type]
        recorder.complete(self.MESSAGES, model="m")
        path = next(self.tmp.glob("*.json"))
        path.write_text(path.read_text(encoding="utf-8").replace('"live"', '"handmade"'), encoding="utf-8")
        with self.assertRaises(LLMError):
            ReplayClient(self.tmp).complete(self.MESSAGES, model="m")

    def test_structured_retries_once_with_the_domain_check_error(self) -> None:
        client = ScriptedClient('{"value": 5}', '{"value": 7}')
        gateway = ModelGateway(client, "m", "test")
        result = gateway.structured(self.MESSAGES, Answer, lambda a: None if a.value == 7 else "必须是 7")
        self.assertEqual((result.value, client.usage.calls), (7, 2))
        with self.assertRaises(LLMError):
            ModelGateway(ScriptedClient("not json", "{}"), "m", "t").structured(self.MESSAGES, Answer)


class LedgerApprovalTest(TempDirTest):
    def test_ledger_keeps_the_order_number_once_known(self) -> None:
        ledger = Ledger(self.tmp / "agent.db")
        ledger.put("ik-1", "hash", "C001", "PO-1", "submitting")
        ledger.put("ik-1", "hash", "C001", "PO-1", "verified", "XS26110001")
        ledger.put("ik-1", "hash", "C001", "PO-1", "unverified")
        entry = ledger.get("ik-1")
        self.assertEqual((entry.status, entry.order_no), ("unverified", "XS26110001"))
        self.assertIsNone(ledger.get("ik-2"))

    def test_a_decision_is_never_overwritten(self) -> None:
        store = ApprovalStore(self.tmp / "agent.db")
        store.request("ik-1", "h", {"customer": "C001"}, None)
        self.assertTrue(store.decide("ik-1", False, "web"))
        self.assertFalse(store.decide("ik-1", True, "cli"))
        store.request("ik-1", "h", {"customer": "changed"}, None)
        request = store.get("ik-1")
        self.assertEqual((request.status, request.decided_by, request.preview), ("rejected", "web", {"customer": "C001"}))

    def test_wait_times_out_as_pending_and_the_approver_answers(self) -> None:
        store = ApprovalStore(self.tmp / "agent.db")
        store.request("ik-a", "h", {}, None)
        store.request("ik-r", "h", {}, None)
        self.assertEqual(asyncio.run(store.wait("ik-a", 0.2, 0.05)).status, "pending")

        async def approve_all() -> tuple[str, str]:
            stop = asyncio.Event()
            approver = asyncio.create_task(run_approver(store, auto_decider(frozenset({"ik-r"})), stop, 0.05))
            a, r = await store.wait("ik-a", 2, 0.05), await store.wait("ik-r", 2, 0.05)
            stop.set()
            await approver
            return a.status, r.status

        self.assertEqual(asyncio.run(approve_all()), ("approved", "rejected"))

    def test_approval_page_lists_and_decides(self) -> None:
        store = ApprovalStore(self.tmp / "agent.db")
        draft = OrderDraft.model_validate(task("T06")["args"]["order"])
        preview = {"customer": "C001", "customer_po": draft.customer_po, "order_date": "2026-11-02",
                   "lines": [line.model_dump() for line in draft.lines], "total": draft.total,
                   "portal_total": draft.total, "idempotency_key": "ik-web"}
        store.request("ik-web", "h", preview, None)
        client = TestClient(create_approval_app(store))
        self.assertIn(draft.customer_po, client.get("/").text)
        client.post("/decide/ik-web", data={"approve": "1"})
        self.assertEqual(store.get("ik-web").status, "approved")
        self.assertEqual(client.post("/decide/nope", data={"approve": "1"}).status_code, 404)


class DraftTest(unittest.TestCase):
    def test_default_key_ignores_free_text_but_not_content(self) -> None:
        order = task("T06")["args"]["order"]
        a = OrderDraft.model_validate(order)
        b = OrderDraft.model_validate(order | {"source": "elsewhere", "customer_name": None})
        c = OrderDraft.model_validate(order | {"customer_po": "OTHER"})
        self.assertEqual(a.default_key(), b.default_key())
        self.assertNotEqual(a.default_key(), c.default_key())


if __name__ == "__main__":
    unittest.main()
