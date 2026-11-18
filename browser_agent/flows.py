"""The four portal flows, written as scripted Playwright steps against layout v1.

Every step also carries a plain-language intent, so when a selector breaks (layout v2/v3) the driver can ask the
model to locate the element instead. Readers parse v1 tables directly and fall back to grounded model extraction.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, replace

from playwright.async_api import Download
from pydantic import BaseModel, Field

from .driver import Driver
from .errors import NavigationTimeout, NeedsHuman, PortalRejected
from .models import OrderDetail, OrderDraft, OrderLineOut, OrderRow, StockItem
from .navigator import Navigator
from .steps import Step


@dataclass(frozen=True)
class OrderQuery:
    order_no: str = ""
    customer_po: str = ""
    customer_id: str = ""
    date_from: dt.date | None = None
    date_to: dt.date | None = None


class SubmitTimeout(Exception):
    """The final submit click did not come back in time; the order may or may not have been saved."""


# --------------------------------------------------------------------------- extraction schemas (model fallback)


class OrderListPage(BaseModel):
    rows: list[OrderRow] = Field(default_factory=list, description="查询结果中的订单行；没有结果则为空")


class StockPage(BaseModel):
    found: bool = Field(..., description="页面是否列出了库存结果；提示查无此物料时为 false")
    items: list[StockItem] = Field(default_factory=list)


class ConfirmPage(BaseModel):
    lines: list[OrderLineOut] = Field(default_factory=list, description="确认页上的每一行明细")
    total: float = Field(..., description="确认页上的合计金额（价税合计）")


class SavedPage(BaseModel):
    order_no: str | None = Field(None, description="保存成功提示中的单据编号；没有保存成功提示则为 null")


def _num(text: str) -> float:
    return float(text.replace(",", "").replace("¥", "").strip())


def _fmt(value: float) -> str:
    return f"{value:g}"


class PortalFlows:
    def __init__(self, driver: Driver, base_url: str, username: str, password: str, submit_timeout_ms: int):
        self.d = driver
        self.base_url = base_url
        self.username = username
        self.password = password
        self.submit_timeout_ms = submit_timeout_ms

    # ------------------------------------------------------------------ session and menu

    async def open_home(self) -> None:
        await self.d.goto(self.base_url + "/")
        if self.d.page.url.rstrip("/").endswith("/login"):
            await self.login()

    async def login(self) -> None:
        await self.d.perform(Step("login.username", "在登录页填写登录账号（用户名/工号/操作员代码）", "#username", "fill",
                                  self.username, secret=True))
        await self.d.perform(Step("login.password", "在登录页填写登录密码（口令）", "#password", "fill",
                                  self.password, secret=True))
        await self._click(Step("login.submit", "点击登录按钮进入系统", "#btnLogin", "click"))
        if self.d.page.url.rstrip("/").endswith("/login"):
            raise NeedsHuman("登录失败：" + "；".join(await self.d.errors()))

    async def _menu(self, key: str, intent: str, selector: str) -> None:
        await self.open_home()
        await self._click(Step(f"menu.{key}", intent, selector, "click"))

    async def _click(self, step: Step) -> bool:
        """Click something that loads a new page; False if an optional element is legitimately absent."""
        try:
            return (await self.d.perform(step, expect="navigation")).performed
        except NavigationTimeout as exc:
            raise NeedsHuman(f"{step.intent}：{exc}") from exc

    # ------------------------------------------------------------------ orders

    async def search_orders(self, query: OrderQuery, max_pages: int) -> tuple[list[OrderRow], int]:
        await self._menu("order_search", "打开销售订单查询页面（菜单）", "#menu-order-search")
        fields = [
            ("order_no", "在查询条件中填写销售单号（单据编号/单据号）", "#q_order_no", "fill", query.order_no),
            ("customer_po", "在查询条件中填写客户的采购单号（客户单号/客户订单号）", "#q_cust_po", "fill", query.customer_po),
            ("customer", "在查询条件中选择或填写客户（客户编码）", "#q_customer", "select", query.customer_id),
            ("date_from", "在查询条件中填写单据日期（下单日期）的起始日期", "#q_date_from", "fill",
             query.date_from.isoformat() if query.date_from else ""),
            ("date_to", "在查询条件中填写单据日期（下单日期）的截止日期", "#q_date_to", "fill",
             query.date_to.isoformat() if query.date_to else ""),
        ]
        for key, intent, selector, op, value in fields:
            if value:
                await self.d.perform(Step(f"search.{key}", intent, selector, op, value))  # type: ignore[arg-type]
        await self._click(Step("search.submit", "点击查询（检索）按钮执行查询", "#btnSearch", "click"))
        rows: list[OrderRow] = []
        pages = 0
        while pages < max_pages:
            page_rows = await self._read_order_rows()
            pages += 1
            fresh = [r for r in page_rows if r.order_no not in {x.order_no for x in rows}]
            if page_rows and not fresh:
                break  # the "next page" did not move: stop instead of looping
            rows += fresh
            if not page_rows or pages >= max_pages:
                break
            if not await self._click(
                    Step("search.next_page", "翻到查询结果的下一页；如果已经是最后一页（没有可点的下一页），actions 返回空数组",
                         "#nextPage", "click", optional=True, absent_marker="#pager")):
                break
        return rows, pages

    async def _read_order_rows(self) -> list[OrderRow]:
        async def scripted() -> list[OrderRow]:
            table = await self.d.need("#orderTable")
            rows = []
            for tr in await table.locator("tbody tr").all():
                cells = [c.strip() for c in await tr.locator("td").all_inner_texts()]
                if len(cells) == 7:
                    rows.append(OrderRow(order_no=cells[0], customer_po=cells[1], customer=cells[2],
                                         order_date=cells[3], delivery_date=cells[4] or None, amount=_num(cells[5]),
                                         status=cells[6]))
            return rows

        async def fallback(nav: Navigator, text: str) -> list[OrderRow]:
            page = await nav.extract(OrderListPage, "查询结果列表中的每一行订单（单据号、客户采购单号、客户、单据日期、"
                                                    "交货日期、金额、状态），不含表头和翻页信息", text)
            return page.rows

        return await self.d.read("search.rows", scripted, fallback)

    async def open_order(self, order_no: str) -> OrderDetail | None:
        rows, _ = await self.search_orders(OrderQuery(order_no=order_no), max_pages=1)
        if not any(r.order_no == order_no for r in rows):
            return None
        await self._click(Step("search.open_order", f"在查询结果中点击单据号 {order_no} 的链接，打开单据详情",
                               f'#orderTable a[href="/orders/{order_no}"]', "click", cacheable=False))
        return await self._read_detail()

    async def _read_detail(self) -> OrderDetail:
        async def scripted() -> OrderDetail:
            await self.d.need("#orderHead")
            cell = self.d.page.locator
            lines = []
            for tr in await self.d.page.locator("#orderLines tbody tr").all():
                cells = [c.strip() for c in await tr.locator("td").all_inner_texts()]
                lines.append(OrderLineOut(sku=cells[1], qty=_num(cells[5]), unit_price=_num(cells[6])))
            return OrderDetail(
                order_no=(await cell("#d_order_no").inner_text()).strip(),
                customer=(await cell("#d_customer").inner_text()).strip(),
                customer_po=(await cell("#d_cust_po").inner_text()).strip(),
                order_date=(await cell("#d_order_date").inner_text()).strip(),
                delivery_date=(await cell("#d_delivery_date").inner_text()).strip() or None,
                status=(await cell("#d_status").inner_text()).strip(),
                remark=(await cell("#d_remark").inner_text()).strip(),
                total=_num(await cell("#d_total").inner_text()), lines=lines)

        async def fallback(nav: Navigator, text: str) -> OrderDetail:
            return await nav.extract(OrderDetail, "单据详情：单据号、客户、客户采购单号、单据日期、交货日期、状态、备注、"
                                                  "合计金额，以及每一行明细的物料编码（存货编码）、数量、含税单价", text)

        return await self.d.read("detail", scripted, fallback)

    # ------------------------------------------------------------------ stock

    async def check_stock(self, skus: list[str]) -> tuple[list[StockItem], list[str]]:
        await self._menu("stock", "打开库存查询（存货查询/现存量查询）页面", "#menu-stock")
        items, missing = [], []
        for sku in skus:
            await self.d.perform(Step("stock.sku", "在库存查询条件中填写物料编码（存货编码）", "#stock_sku", "fill", sku))
            await self._click(Step("stock.submit", "点击查询按钮查询库存", "#btnStock", "click"))
            found = [item for item in await self._read_stock() if item.sku == sku]
            if found:
                items += found
            else:
                missing.append(sku)
        return items, missing

    async def _read_stock(self) -> list[StockItem]:
        async def scripted() -> list[StockItem]:
            await self.d.need("#stockTable, .msg-error")
            if await self.d.page.locator(".msg-error").count():
                return []
            items = []
            for tr in await self.d.page.locator("#stockTable tbody tr").all():
                c = [x.strip() for x in await tr.locator("td").all_inner_texts()]
                items.append(StockItem(sku=c[0], name=c[1], spec=c[2], unit=c[3], on_hand=_num(c[4]),
                                       available=_num(c[5]), status=c[6]))
            return items

        async def fallback(nav: Navigator, text: str) -> list[StockItem]:
            page = await nav.extract(StockPage, "库存查询结果：每个物料的编码、名称、规格、单位、现存量（库存数量）、"
                                                "可用量（可用数量）、状态；页面提示查无此物料时 found=false", text)
            return page.items if page.found else []

        return await self.d.read("stock.result", scripted, fallback)

    # ------------------------------------------------------------------ order entry

    async def fill_order_entry(self, draft: OrderDraft, remark: str) -> None:
        """Fill the order form up to the confirm page. Raises PortalRejected with the portal's own messages."""
        await self._menu("order_new", "打开新建销售订单（录入销售单）页面", "#menu-order-new")
        d = self.d
        await d.perform(Step("entry.customer", "选择或填写下单客户（客户编码）", "#customer", "select", draft.customer_id,
                             alternatives=(draft.customer_name,) if draft.customer_name else ()))
        await d.perform(Step("entry.customer_po", "填写客户采购单号（客户单号/客户订单号）", "#cust_po", "fill",
                             draft.customer_po))
        await d.perform(Step("entry.order_date", "填写单据日期（下单日期）", "#order_date", "fill",
                             draft.order_date.isoformat()))
        if draft.delivery_date:
            await d.perform(Step("entry.delivery_date", "填写交货日期（要求到货日）", "#delivery_date", "fill",
                                 draft.delivery_date.isoformat()))
        await d.perform(Step("entry.remark", "填写备注", "#remark", "fill", remark))
        if await self._click(Step("entry.next1", "抬头已填完，点击进入下一步（填写明细）的按钮；如果明细输入框已经在本页、"
                                                 "没有这类按钮，actions 返回空数组", "#btnNext1", "click", optional=True)):
            await self._raise_portal_errors()
        for i, line in enumerate(draft.lines, 1):
            sku = Step(f"entry.line{i}.sku", f"在第 {i} 行明细中填写物料编码（存货编码）；页面上没有第 {i} 行时 actions 返回空数组",
                       f"#sku_{i}", "fill", line.sku, optional=True, absent_marker="#lineTable")
            if not (await d.perform(sku)).performed:
                await self._click(Step("entry.add_line", "点击增加一行明细的按钮（增加行/新增一行/增行）", "#btnAddLine", "click"))
                await d.perform(replace(sku, optional=False))
            await d.perform(Step(f"entry.line{i}.qty", f"在第 {i} 行明细中填写数量", f"#qty_{i}", "fill", _fmt(line.qty)))
            await d.perform(Step(f"entry.line{i}.price", f"在第 {i} 行明细中填写含税单价", f"#price_{i}", "fill",
                                 _fmt(line.unit_price)))
        await self._click(Step("entry.next2", "明细已填完，点击进入核对/确认页面的按钮（下一步/继续/保存）", "#btnNext2", "click"))
        await self._raise_portal_errors()

    async def _raise_portal_errors(self) -> None:
        errors = await self.d.errors()
        if errors:
            await self.d.recorder.screenshot(self.d.page, "portal-errors")
            raise PortalRejected(errors)

    async def read_confirmation(self) -> tuple[list[OrderLineOut], float]:
        async def scripted() -> tuple[list[OrderLineOut], float]:
            await self.d.need("#confirmLines")
            lines = []
            for tr in await self.d.page.locator("#confirmLines tbody tr").all():
                c = [x.strip() for x in await tr.locator("td").all_inner_texts()]
                lines.append(OrderLineOut(sku=c[1], qty=_num(c[5]), unit_price=_num(c[6])))
            return lines, _num(await self.d.page.locator("#confirmTotal").inner_text())

        async def fallback(nav: Navigator, text: str) -> tuple[list[OrderLineOut], float]:
            page = await nav.extract(ConfirmPage, "确认页上的订单明细（物料编码/存货编码、数量、含税单价）和合计金额", text)
            return page.lines, page.total

        return await self.d.read("entry.confirm", scripted, fallback)

    async def submit_order(self) -> str:
        """Click the final submit and return the portal's order number. Raises SubmitTimeout if the page hangs."""
        try:
            await self.d.perform(Step("entry.submit", "点击最终的提交按钮保存订单（提交/确认保存/确定）", "#btnSubmit", "click"),
                                 expect="navigation", timeout_ms=self.submit_timeout_ms)
        except NavigationTimeout as exc:
            raise SubmitTimeout(str(exc)) from exc
        await self._raise_portal_errors()

        async def scripted() -> str | None:
            text = await (await self.d.need("#savedMsg")).inner_text()
            match = re.search(r"XS\d{8}", text)
            return match.group(0) if match else None

        async def fallback(nav: Navigator, text: str) -> str | None:
            return (await nav.extract(SavedPage, "保存成功提示中的单据编号（销售单号）", text)).order_no

        order_no = await self.d.read("entry.saved", scripted, fallback)
        if not order_no:
            raise NeedsHuman("提交后没有看到保存成功的单据编号")
        return order_no

    # ------------------------------------------------------------------ invoice

    async def invoice_download(self) -> Download | None:
        """On an order detail page: the invoice download, or None when the page shows no invoice."""
        step = Step("detail.invoice", "点击下载或打印发票的链接/按钮；如果页面显示未开票、没有发票链接，actions 返回空数组",
                    "#lnkInvoice", "click", optional=True, absent_marker="#noInvoice")
        outcome = await self.d.perform(step, expect="download")
        return outcome.download
