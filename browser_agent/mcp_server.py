"""MCP server (stdio): the legacy portal as four tools any MCP client can call.

Writes are never approved by the calling model: create_sales_order_draft waits for a human decision made on the
local approval page (started here, 127.0.0.1:8771) or in the orchestrator's terminal.
"""
from __future__ import annotations

import datetime as dt
import sys
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .approval_web import create_approval_app
from .config import Settings
from .flows import OrderQuery
from .llm import build_gateway
from .models import CreateResult, InvoiceResult, OrderDraft, SearchOrderResult, StockResult
from .portal import serve_in_background
from .service import PortalAgent

INSTRUCTIONS = (
    "Operates the sales portal of 瑞丰机电批发（虚构） through a real browser (the portal has no API). "
    "search_order, check_stock and download_invoice only read. create_sales_order_draft writes: it fills the order "
    "form, then waits for a human to approve on the approval page or in the operator's terminal; it is idempotent "
    "per idempotency_key and only reports 'created' after finding the saved order in the portal. "
    "Every call returns run.trace and screenshots as evidence. Demo data is fictional."
)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)  # stdout carries the MCP protocol


def create_mcp_server(settings: Settings) -> MCPServer:
    agent = PortalAgent(settings, build_gateway(settings))

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        with ExitStack() as stack:
            if settings.approval_web:
                try:
                    url = stack.enter_context(serve_in_background(create_approval_app(agent.approvals),
                                                                  settings.approval_port))
                    log(f"approval page: {url}")
                except RuntimeError:
                    log(f"approval page not started: port {settings.approval_port} is busy (another instance?)")
            log(f"portal: {settings.portal_url}  mode: {settings.mode_label}")
            try:
                yield None
            finally:
                await agent.close()

    server = MCPServer("legacy-portal-browser-agent", instructions=INSTRUCTIONS, lifespan=lifespan)

    @server.tool(description="查询销售订单（读）。可按销售单号、客户采购单号、客户编码、单据日期范围查询，自动翻页。")
    async def search_order(order_no: str = "", customer_po: str = "", customer_id: str = "",
                           date_from: dt.date | None = None, date_to: dt.date | None = None,
                           max_pages: int = Field(5, ge=1, le=20)) -> SearchOrderResult:
        return await agent.search_order(OrderQuery(order_no, customer_po, customer_id, date_from, date_to), max_pages)

    @server.tool(description="查询物料的现存量与可用量（读）。不存在的料号放在 not_found 里返回。")
    async def check_stock(skus: list[str] = Field(..., min_length=1, max_length=20)) -> StockResult:
        return await agent.check_stock(skus)

    @server.tool(description="新建销售订单草稿（写）。填表到门户确认页后等待人工审批，批准后才提交，并在门户中核实。"
                             "同一 idempotency_key 重复调用不会重复建单；不传则按订单内容生成。")
    async def create_sales_order_draft(order: OrderDraft, idempotency_key: str | None = None) -> CreateResult:
        return await agent.create_sales_order_draft(order, idempotency_key)

    @server.tool(description="下载某张销售订单的发票 PDF（读），并解析发票号与金额；未开票时返回 no_invoice。")
    async def download_invoice(order_no: str) -> InvoiceResult:
        return await agent.download_invoice(order_no)

    return server
