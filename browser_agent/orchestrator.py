"""End-to-end demo: Demo A's extracted orders → MCP tools (over stdio, like any MCP client) → approval → verify.

The orchestrator is an ordinary MCP client: it starts ``python -m browser_agent mcp`` as a subprocess and calls the
tools by name. Approvals are answered in this terminal, on the approval page the MCP server serves, or by the
explicit --auto-approve demo flag; all of them write to the same approval store.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import ExitStack
from pathlib import Path
from typing import Literal

from mcp import Client, StdioServerParameters

from .approval import ApprovalStore, auto_decider, cli_approver, run_approver
from .config import PROJECT_ROOT, Settings
from .control import PortalControl
from .portal import build_portal, serve_in_background

ApprovalMode = Literal["cli", "web", "auto"]
APPROVAL_LABELS = {"cli": "命令行逐单确认", "web": "审批页 http://127.0.0.1:{port} 上点击批准",
                   "auto": "自动批准（--auto-approve 演示开关）"}


async def call_tool(client: Client, name: str, arguments: dict) -> dict:
    result = await client.call_tool(name, arguments)
    if result.is_error:
        raise RuntimeError(f"{name} failed: " + " ".join(getattr(c, "text", "") for c in result.content))
    assert result.structured_content is not None
    return result.structured_content


def _print_stock(order: dict, stock: dict) -> None:
    available = {item["sku"]: item["available"] for item in stock["items"]}
    for line in order["lines"]:
        have = available.get(line["sku"])
        note = "查无此料号" if have is None else ("库存不足，审批时请留意" if have < line["qty"] else "够")
        print(f"    {line['sku']:<10} 需要 {line['qty']:>6g}  可用 {'-' if have is None else f'{have:g}':>6}  {note}")


async def run_demo(settings: Settings, orders_file: Path, limit: int, approval: ApprovalMode,
                   layout: str | None) -> None:
    orders = json.loads(orders_file.read_text(encoding="utf-8"))["orders"][:limit]
    control = PortalControl(settings.portal_url)
    with ExitStack() as stack:
        if not control.reachable():
            port = int(settings.portal_url.rsplit(":", 1)[1])
            app = build_portal(settings.portal_db, settings.data_dir, fresh=False)
            stack.enter_context(serve_in_background(app, port))
            print(f"门户未运行，已在本进程内启动：{settings.portal_url}（数据库 {settings.portal_db}）")
        if layout:
            control.set(layout=layout)
        print(f"门户界面版本：{control.get()['layout']}    模式：{settings.mode_label}    "
              f"审批：{APPROVAL_LABELS[approval].format(port=settings.approval_port)}")

        # Exactly one approval channel is active, so a terminal prompt never waits for a decision made elsewhere.
        store = ApprovalStore(settings.agent_db)
        stop = asyncio.Event()
        approver = None
        if approval == "cli":
            approver = asyncio.create_task(cli_approver(store, stop))
        elif approval == "auto":
            approver = asyncio.create_task(run_approver(store, auto_decider(), stop))
        env = dict(os.environ) | {"APPROVAL_WEB": "1" if approval == "web" else "0"}
        params = StdioServerParameters(command=sys.executable, args=["-m", "browser_agent", "mcp"], env=env,
                                       cwd=str(PROJECT_ROOT))
        summary: list[tuple[str, str, str]] = []
        try:
            async with Client(params) as client:
                tools = (await client.list_tools()).tools
                print("MCP 工具：" + "、".join(t.name for t in tools))
                for order in orders:
                    print(f"\n== {order['customer_name']}  客户单号 {order['customer_po']}  "
                          f"（来源 {order['source_doc']}）")
                    stock = await call_tool(client, "check_stock", {"skus": [line["sku"] for line in order["lines"]]})
                    print(f"  check_stock：{stock['status']}  （{stock['run']['seconds']}s）")
                    _print_stock(order, stock)
                    draft = {k: order[k] for k in ("customer_id", "customer_name", "customer_po", "order_date",
                                                   "delivery_date", "lines")} | {"source": order["source_doc"]}
                    result = await call_tool(client, "create_sales_order_draft", {"order": draft})
                    print(f"  create_sales_order_draft：{result['status']}  单号 {result['order_no'] or '-'}  "
                          f"已核实 {result['verified']}  {result['message']}")
                    if result["run"]:
                        print(f"    证据：{result['run']['trace']}")
                    summary.append((order["customer_po"], result["status"], result["order_no"] or "-"))
                if orders:
                    found = await call_tool(client, "search_order", {"customer_po": orders[0]["customer_po"]})
                    print(f"\nsearch_order 客户单号 {orders[0]['customer_po']}：" +
                          ("、".join(f"{o['order_no']}（{o['status']}）" for o in found["orders"]) or "无"))
                invoice = await call_tool(client, "download_invoice", {"order_no": _invoiced_order(settings)})
                print(f"download_invoice {invoice['order_no']}：{invoice['status']}  发票号 {invoice['invoice_no']}  "
                      f"金额 {invoice['amount']}  文件 {invoice['file']}")
        finally:
            stop.set()
            if approver:
                await approver
    print("\n汇总：")
    for po, status, order_no in summary:
        print(f"  {po:<18} {status:<16} {order_no}")
    print(f"审计轨迹与截图：{settings.runs_dir}")


def _invoiced_order(settings: Settings) -> str:
    seed = json.loads((settings.data_dir / "portal_seed.json").read_text(encoding="utf-8"))
    return next(o["order_no"] for o in seed["orders"] if o["invoice"])
