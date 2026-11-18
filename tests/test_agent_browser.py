"""The agent in a real (headless) Chromium against the fake portal. Needs Playwright's Chromium installed."""
from __future__ import annotations

import asyncio
import datetime as dt
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from browser_agent.approval import auto_decider, run_approver
from browser_agent.flows import OrderQuery
from browser_agent.models import OrderDraft
from browser_agent.service import PortalAgent

from .support import FakeStockModel, Portal, fake_gateway, running_portal, task


T = TypeVar("T")


async def with_approver(agent: PortalAgent, coro_factory: Callable[[], Awaitable[T]],
                        reject: frozenset[str] = frozenset()) -> T:
    """Run a tool call while an approver answers its approval requests (rejecting the given keys)."""
    stop = asyncio.Event()
    approver = asyncio.create_task(run_approver(agent.approvals, auto_decider(reject, "test"), stop, 0.05))
    try:
        return await coro_factory()
    finally:
        stop.set()
        await approver


def draft(task_id: str) -> OrderDraft:
    return OrderDraft.model_validate(task(task_id)["args"]["order"])


class ScriptedV1Test(unittest.IsolatedAsyncioTestCase):
    """Layout v1: the scripted selectors work and no model is configured at all."""

    async def asyncSetUp(self) -> None:
        self.portal_cm = running_portal("v1")
        self.portal: Portal = self.portal_cm.__enter__()
        self.agent = PortalAgent(self.portal.settings(), gateway=None)

    async def asyncTearDown(self) -> None:
        await self.agent.close()
        self.portal_cm.__exit__(None, None, None)

    async def test_read_tools(self) -> None:
        result = await self.agent.search_order(OrderQuery(customer_id="C001", date_from=dt.date(2026, 10, 1),
                                                          date_to=dt.date(2026, 11, 15)))
        self.assertEqual((result.status, len(result.orders), result.pages_read), ("ok", 13, 2))
        stock = await self.agent.check_stock(["1.01.0003", "9.99.9999"])
        self.assertEqual(({i.sku: i.available for i in stock.items}, stock.not_found), ({"1.01.0003": 12.0}, ["9.99.9999"]))
        invoice = await self.agent.download_invoice("XS26100013")
        self.assertEqual((invoice.status, invoice.invoice_no, invoice.amount), ("ok", "FP261000013", 939.6))
        self.assertEqual((await self.agent.download_invoice("XS26100023")).status, "no_invoice")
        self.assertEqual((await self.agent.download_invoice("XS99999999")).status, "not_found")
        self.assertTrue(result.run.screenshots and result.run.trace.endswith("trace.jsonl"))

    async def test_create_is_verified_and_idempotent(self) -> None:
        order = draft("T06")
        first = await with_approver(self.agent, lambda: self.agent.create_sales_order_draft(order))
        second = await self.agent.create_sales_order_draft(order)
        self.assertEqual((first.status, first.verified, first.approved_by), ("created", True, "test"))
        self.assertEqual((second.status, second.order_no), ("already_exists", first.order_no))
        conflict = await self.agent.create_sales_order_draft(order.model_copy(update={"customer_po": "X"}),
                                                             first.idempotency_key)
        self.assertEqual(conflict.status, "conflict")
        self.assertEqual(len(self.portal.db.orders_created_by("op01")), 1)

    async def test_timeout_is_verified_before_any_retry(self) -> None:
        self.portal.set("v1", submit_delay_once=7)
        result = await with_approver(self.agent, lambda: self.agent.create_sales_order_draft(draft("T08")))
        self.assertEqual((result.status, result.verified), ("created", True))
        self.assertEqual(len(self.portal.db.orders_created_by("op01")), 1)
        events = Path(result.run.trace).read_text(encoding="utf-8")
        self.assertIn("submit_timeout", events)
        self.assertIn("found_after_timeout", events)
        self.assertNotIn("retry_submit", events)

    async def test_a_lost_submit_is_retried_once_after_checking(self) -> None:
        self.portal.set("v1", submit_delay_once=7, drop_next_submit=True)
        result = await with_approver(self.agent, lambda: self.agent.create_sales_order_draft(draft("T08")))
        self.assertEqual((result.status, result.verified), ("created", True))
        self.assertEqual(len(self.portal.db.orders_created_by("op01")), 1)
        events = Path(result.run.trace).read_text(encoding="utf-8")
        self.assertLess(events.index("submit_timeout"), events.index("retry_submit"))
        self.assertNotIn("found_after_timeout", events)

    async def test_rejection_and_portal_errors_submit_nothing(self) -> None:
        rejected_key = draft("T09").default_key()
        rejected = await with_approver(self.agent, lambda: self.agent.create_sales_order_draft(draft("T09")),
                                       frozenset({rejected_key}))
        inactive = await with_approver(self.agent, lambda: self.agent.create_sales_order_draft(draft("T10")))
        self.assertEqual(rejected.status, "rejected")
        self.assertEqual(inactive.status, "portal_rejected")
        self.assertIn("已停用", inactive.portal_errors[0])
        self.assertEqual(self.portal.db.orders_created_by("op01"), [])

    async def test_approval_timeout_submits_nothing_and_can_resume(self) -> None:
        agent = PortalAgent(self.portal.settings(approval_timeout_s=0.5), gateway=None)
        try:
            waited = await agent.create_sales_order_draft(draft("T07"))
            self.assertEqual(waited.status, "approval_timeout")
            self.assertEqual(self.portal.db.orders_created_by("op01"), [])
            agent.approvals.decide(waited.idempotency_key, True, "test")
            resumed = await agent.create_sales_order_draft(draft("T07"))
            self.assertEqual(resumed.status, "created")
        finally:
            await agent.close()


class NaiveRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retrying_without_verification_duplicates_the_order(self) -> None:
        """The counterfactual the evaluation reports: why verify-before-retry exists."""
        with running_portal("v1") as portal:
            portal.set("v1", submit_delay_once=7)
            agent = PortalAgent(portal.settings(), gateway=None, verify_before_retry=False)
            try:
                await with_approver(agent, lambda: agent.create_sales_order_draft(draft("T08")))
            finally:
                await agent.close()
            self.assertEqual(len(portal.db.orders_created_by("op01")), 2)


class LayoutV2Test(unittest.IsolatedAsyncioTestCase):
    async def test_scripted_only_hands_over_without_writing(self) -> None:
        with running_portal("v2") as portal:
            agent = PortalAgent(portal.settings(), gateway=None)
            try:
                result = await with_approver(agent, lambda: agent.create_sales_order_draft(draft("T16")))
            finally:
                await agent.close()
            self.assertEqual(result.status, "needs_human")
            self.assertIn("脚本选择器失效", result.message)
            self.assertEqual(portal.db.orders_created_by("op01"), [])

    async def test_model_fallback_plumbing_with_a_fake_model(self) -> None:
        with running_portal("v2") as portal:
            client = FakeStockModel()
            agent = PortalAgent(portal.settings(), gateway=fake_gateway(client))
            try:
                first = await agent.check_stock(["4.01.0013", "1.01.0999"])
                second = await agent.check_stock(["7.02.0005"])
            finally:
                await agent.close()
            self.assertEqual(({i.sku: i.available for i in first.items}, first.not_found), ({"4.01.0013": 349.0}, ["1.01.0999"]))
            self.assertEqual([i.available for i in second.items], [56.0])
            self.assertGreater(first.run.model_steps, 0)
            self.assertGreater(second.run.cache_steps, 0)  # selectors found by the model are reused
            self.assertTrue(all("Ruifeng#2026" not in p for p in client.prompts))  # the password never reaches the model
            self.assertEqual(second.run.model_calls, 1)  # only reading the result needs the model the second time


if __name__ == "__main__":
    unittest.main()
