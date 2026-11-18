"""The four tools as one object that owns the browser: search_order, check_stock, create_sales_order_draft,
download_invoice. Used in-process by the evaluation and behind the MCP server.

create_sales_order_draft, in order:
  1. idempotency: the same key with different content is a conflict; a key already verified returns the old order;
  2. the portal is searched for an order carrying the key (crash recovery: the ledger may be behind the portal);
  3. the form is filled up to the portal's confirm page, which is read back and compared with the draft;
  4. a human approves (CLI / approval page / explicit demo flag) looking at the confirm-page screenshot;
  5. submit; if the portal does not answer in time, search for the key BEFORE any retry;
  6. post-submit verification: open the saved order and compare it field by field; only then report "created".
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from pypdf import PdfReader

from .approval import ApprovalStore
from .config import Settings
from .driver import Driver, SelectorCache
from .errors import NeedsHuman, PortalRejected
from .flows import OrderQuery, PortalFlows, SubmitTimeout
from .ledger import Ledger
from .llm import ModelGateway
from .models import (CreateResult, InvoiceResult, OrderDetail, OrderDraft, OrderLineOut, SearchOrderResult,
                     StockResult)
from .navigator import Navigator
from .steps import date_spellings
from .trace import RunRecorder


@dataclass
class Run:
    flows: PortalFlows
    rec: RunRecorder
    page: Page


def _line_keys(lines: list[OrderLineOut]) -> list[tuple[str, float, float]]:
    return sorted((line.sku, round(line.qty, 3), round(line.unit_price, 2)) for line in lines)


def _draft_lines(draft: OrderDraft) -> list[OrderLineOut]:
    return [OrderLineOut(sku=line.sku, qty=line.qty, unit_price=line.unit_price) for line in draft.lines]


def _same_date(text: str | None, expected: object) -> bool:
    return str(expected) in date_spellings(text or "")


def verification_problems(detail: OrderDetail, draft: OrderDraft, key: str) -> list[str]:
    """Differences between what the portal shows and what was meant to be entered."""
    problems = []
    if key not in detail.remark:
        problems.append(f"备注中没有幂等键 {key}")
    if detail.customer_po != draft.customer_po:
        problems.append(f"客户单号 {detail.customer_po} ≠ {draft.customer_po}")
    if draft.customer_id not in detail.customer and not (draft.customer_name and draft.customer_name in detail.customer):
        problems.append(f"客户 {detail.customer} 与 {draft.customer_id} 不符")
    if not _same_date(detail.order_date, draft.order_date):
        problems.append(f"单据日期 {detail.order_date} ≠ {draft.order_date}")
    if draft.delivery_date and not _same_date(detail.delivery_date, draft.delivery_date):
        problems.append(f"交货日期 {detail.delivery_date} ≠ {draft.delivery_date}")
    if _line_keys(detail.lines) != _line_keys(_draft_lines(draft)):
        problems.append("明细行（物料/数量/单价）不一致")
    if abs(detail.total - draft.total) > 0.01:
        problems.append(f"合计 {detail.total:.2f} ≠ {draft.total:.2f}")
    return problems


def parse_invoice(path: Path) -> dict[str, str]:
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    fields = {}
    for name, label in (("invoice_no", "发票号码"), ("order_no", "销售单号"), ("amount", "价税合计")):
        match = re.search(label + r"[：:]\s*([\w.,-]+)", text)
        if match:
            fields[name] = match.group(1)
    return fields


class PortalAgent:
    def __init__(self, settings: Settings, gateway: ModelGateway | None, *, verify_before_retry: bool = True):
        self.settings = settings
        self.gateway = gateway
        self.verify_before_retry = verify_before_retry
        self.ledger = Ledger(settings.agent_db)
        self.approvals = ApprovalStore(settings.agent_db)
        self.cache = SelectorCache(settings.selector_cache)
        self._lock = asyncio.Lock()
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "PortalAgent":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def _browser(self) -> BrowserContext:
        if self._context is None:
            self._playwright = await async_playwright().start()
            browser = await self._playwright.chromium.launch(headless=self.settings.headless,
                                                             slow_mo=self.settings.slow_mo_ms)
            self._context = await browser.new_context(accept_downloads=True, locale="zh-CN",
                                                      viewport={"width": 1280, "height": 900})
        return self._context

    async def close(self) -> None:
        if self._context is not None:
            await self._context.browser.close()  # type: ignore[union-attr]
            self._context = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    @asynccontextmanager
    async def _run(self, tool: str, **args: object) -> AsyncIterator[Run]:
        async with self._lock:
            page = await (await self._browser()).new_page()
            rec = RunRecorder(self.settings.runs_dir, tool)
            rec.event("tool_call", tool=tool, args=args, mode=self.settings.mode_label)
            navigator = Navigator(self.gateway, rec, self.settings.max_model_calls) if self.gateway else None
            driver = Driver(page, rec, self.cache, navigator, self.settings.selector_timeout_ms,
                            self.settings.navigation_timeout_ms)
            flows = PortalFlows(driver, self.settings.portal_url, self.settings.portal_user,
                                self.settings.portal_password, self.settings.submit_timeout_ms)
            try:
                yield Run(flows, rec, page)
            finally:
                await page.close()

    @staticmethod
    async def _handover(run: Run, exc: Exception) -> str:
        message = str(exc) or exc.__class__.__name__
        try:
            await run.rec.screenshot(run.page, "needs-human")
        except PlaywrightError:
            pass
        run.rec.event("needs_human", reason=message)
        return message

    # ------------------------------------------------------------------ read-only tools

    async def search_order(self, query: OrderQuery, max_pages: int = 5) -> SearchOrderResult:
        async with self._run("search_order", **query.__dict__, max_pages=max_pages) as run:
            try:
                rows, pages = await run.flows.search_orders(query, max_pages)
            except (NeedsHuman, PlaywrightError) as exc:
                return SearchOrderResult(status="needs_human", message=await self._handover(run, exc), run=run.rec.info())
            await run.rec.screenshot(run.page, "result")
            run.rec.event("result", orders=[r.order_no for r in rows], pages=pages)
            return SearchOrderResult(status="ok", orders=rows, pages_read=pages, run=run.rec.info())

    async def check_stock(self, skus: list[str]) -> StockResult:
        async with self._run("check_stock", skus=skus) as run:
            try:
                items, missing = await run.flows.check_stock(skus)
            except (NeedsHuman, PlaywrightError) as exc:
                return StockResult(status="needs_human", message=await self._handover(run, exc), run=run.rec.info())
            run.rec.event("result", items=[i.model_dump() for i in items], not_found=missing)
            return StockResult(status="ok", items=items, not_found=missing, run=run.rec.info())

    async def download_invoice(self, order_no: str) -> InvoiceResult:
        async with self._run("download_invoice", order_no=order_no) as run:
            try:
                if await run.flows.open_order(order_no) is None:
                    return InvoiceResult(status="not_found", order_no=order_no, message="门户中查不到该单据",
                                         run=run.rec.info())
                download = await run.flows.invoice_download()
                if download is None:
                    await run.rec.screenshot(run.page, "no-invoice")
                    return InvoiceResult(status="no_invoice", order_no=order_no, message="该单据尚未开票",
                                         run=run.rec.info())
                target = self.settings.downloads_dir / f"{order_no}_{download.suggested_filename}"
                target.parent.mkdir(parents=True, exist_ok=True)
                await download.save_as(target)
            except (NeedsHuman, PlaywrightError) as exc:
                return InvoiceResult(status="needs_human", order_no=order_no, message=await self._handover(run, exc),
                                     run=run.rec.info())
            fields = parse_invoice(target)
            run.rec.event("invoice", file=str(target), fields=fields)
            if fields.get("order_no") != order_no or "invoice_no" not in fields or "amount" not in fields:
                return InvoiceResult(status="needs_human", order_no=order_no, file=str(target),
                                     message=f"下载的 PDF 与单据对不上：{fields}", run=run.rec.info())
            return InvoiceResult(status="ok", order_no=order_no, invoice_no=fields["invoice_no"],
                                 amount=float(fields["amount"].replace(",", "")), file=str(target), run=run.rec.info())

    # ------------------------------------------------------------------ the write tool

    async def create_sales_order_draft(self, draft: OrderDraft, idempotency_key: str | None = None) -> CreateResult:
        key = idempotency_key or draft.default_key()
        payload_hash = draft.payload_hash()
        entry = self.ledger.get(key)
        if entry and entry.payload_hash != payload_hash:
            return CreateResult(status="conflict", idempotency_key=key, order_no=entry.order_no,
                                message="这个幂等键之前用于内容不同的订单，已拒绝；请核对后换一个键")
        if entry and entry.status == "verified":
            return CreateResult(status="already_exists", idempotency_key=key, order_no=entry.order_no, verified=True,
                                message="本地台账显示该订单已录入并核实过，没有再次操作门户")
        previous = self.approvals.get(key)
        if previous and previous.status == "rejected":
            return CreateResult(status="rejected", idempotency_key=key, approved_by=previous.decided_by,
                                message="该订单此前已被审批人驳回；如需重新提交请使用新的幂等键")
        remark = f"[{key}] 浏览器Agent录入"
        async with self._run("create_sales_order_draft", key=key, customer_po=draft.customer_po,
                             source=draft.source) as run:
            try:
                return await self._create(run, draft, key, payload_hash, remark)
            except PortalRejected as exc:
                self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "portal_rejected")
                run.rec.event("portal_rejected", errors=exc.errors)
                return CreateResult(status="portal_rejected", idempotency_key=key, portal_errors=exc.errors,
                                    message="门户校验未通过，未提交，需要人工处理", run=run.rec.info())
            except (NeedsHuman, PlaywrightError) as exc:
                return CreateResult(status="needs_human", idempotency_key=key, message=await self._handover(run, exc),
                                    run=run.rec.info())

    async def _create(self, run: Run, draft: OrderDraft, key: str, payload_hash: str, remark: str) -> CreateResult:
        existing = await self._find_by_key(run, draft, key, attempts=1)
        if existing:
            self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "verified", existing.order_no)
            return CreateResult(status="already_exists", idempotency_key=key, order_no=existing.order_no, verified=True,
                                message="门户中已有带此幂等键的订单（之前的运行已提交），没有重复录入", run=run.rec.info())
        portal_total = await self._fill_and_check(run, draft, remark)
        screenshot = await run.rec.screenshot(run.page, "confirm-page")
        preview = {"idempotency_key": key, "customer": f"{draft.customer_id} {draft.customer_name or ''}".strip(),
                   "customer_po": draft.customer_po, "order_date": draft.order_date.isoformat(),
                   "delivery_date": draft.delivery_date.isoformat() if draft.delivery_date else None,
                   "lines": [line.model_dump() for line in draft.lines], "total": draft.total,
                   "portal_total": portal_total, "source": draft.source, "screenshot": screenshot}
        self.approvals.request(key, payload_hash, preview, screenshot)
        run.rec.event("approval_requested", key=key)
        decision = await self.approvals.wait(key, self.settings.approval_timeout_s)
        run.rec.event("approval_decision", status=decision.status, by=decision.decided_by)
        if decision.status == "pending":
            return CreateResult(status="approval_timeout", idempotency_key=key,
                                message=f"{self.settings.approval_timeout_s:g} 秒内无人审批，未提交；审批后用同一幂等键再调用即可继续",
                                run=run.rec.info())
        if decision.status == "rejected":
            self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "rejected")
            return CreateResult(status="rejected", idempotency_key=key, approved_by=decision.decided_by,
                                message="审批人驳回，未提交", run=run.rec.info())
        self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "submitting")
        order_no = await self._submit(run, draft, key, remark)
        if order_no is None:
            self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "unverified")
            return CreateResult(status="unverified", idempotency_key=key, approved_by=decision.decided_by,
                                message="提交两次都没有得到门户响应，且核实时没找到订单：请人工到门户确认", run=run.rec.info())
        detail = await run.flows.open_order(order_no)
        problems = ["门户中查不到该单据"] if detail is None else verification_problems(detail, draft, key)
        await run.rec.screenshot(run.page, "verified-order")
        run.rec.event("verification", order_no=order_no, problems=problems)
        if problems:
            self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "unverified", order_no)
            return CreateResult(status="unverified", idempotency_key=key, order_no=order_no,
                                approved_by=decision.decided_by, message="提交后核对不一致：" + "；".join(problems),
                                run=run.rec.info())
        self.ledger.put(key, payload_hash, draft.customer_id, draft.customer_po, "verified", order_no)
        return CreateResult(status="created", idempotency_key=key, order_no=order_no, verified=True,
                            approved_by=decision.decided_by, message="已提交并在门户中核实", run=run.rec.info())

    async def _fill_and_check(self, run: Run, draft: OrderDraft, remark: str) -> float:
        """Fill the form to the confirm page and read it back; any difference stops before approval."""
        await run.flows.fill_order_entry(draft, remark)
        lines, total = await run.flows.read_confirmation()
        if _line_keys(lines) != _line_keys(_draft_lines(draft)) or abs(total - draft.total) > 0.01:
            raise NeedsHuman(f"门户确认页与订单不一致（确认页 {len(lines)} 行、合计 {total:.2f}；"
                             f"订单 {len(draft.lines)} 行、合计 {draft.total:.2f}），未提交")
        run.rec.event("confirm_page_matches", total=total)
        return total

    async def _submit(self, run: Run, draft: OrderDraft, key: str, remark: str) -> str | None:
        """Submit at most twice; after a timeout, retry only if a search proves the first attempt left no order."""
        for attempt in (1, 2):
            try:
                order_no = await run.flows.submit_order()
                run.rec.event("submitted", attempt=attempt, order_no=order_no)
                return order_no
            except SubmitTimeout as exc:
                run.rec.event("submit_timeout", attempt=attempt, detail=str(exc))
            if self.verify_before_retry:
                found = await self._find_by_key(run, draft, key, attempts=3)
                if found:
                    run.rec.event("found_after_timeout", order_no=found.order_no)
                    return found.order_no
            if attempt == 2:
                return None
            run.rec.event("retry_submit")
            await self._fill_and_check(run, draft, remark)
        return None

    async def _find_by_key(self, run: Run, draft: OrderDraft, key: str, attempts: int) -> OrderDetail | None:
        """Search the portal for an order carrying this idempotency key in its 备注 (retrying briefly)."""
        for attempt in range(attempts):
            rows, _ = await run.flows.search_orders(OrderQuery(customer_po=draft.customer_po), max_pages=3)
            for row in rows:
                if row.customer_po != draft.customer_po:
                    continue
                detail = await run.flows.open_order(row.order_no)
                if detail and key in detail.remark:
                    return detail
            if attempt < attempts - 1:
                await asyncio.sleep(1.0)
        return None
