"""Pydantic types shared by the browser flows, the MCP tools, the orchestrator and the evaluation."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, Field


class OrderLineIn(BaseModel):
    sku: str = Field(..., min_length=1, description="我方物料编码，如 4.01.0013")
    qty: float = Field(..., gt=0, description="数量（按销售单位）")
    unit_price: float = Field(..., ge=0, description="含税单价")


class OrderDraft(BaseModel):
    """A sales order to enter into the portal, in the shape Demo A produces after extraction and SKU mapping."""

    customer_id: str = Field(..., min_length=1, description="客户编码，如 C001")
    customer_name: str | None = Field(None, description="客户名称（用于在下拉框中核对）")
    customer_po: str = Field(..., min_length=1, description="客户采购单号")
    order_date: dt.date
    delivery_date: dt.date | None = None
    lines: list[OrderLineIn] = Field(..., min_length=1)
    source: str | None = Field(None, description="来源单据，仅记录在审计日志里")

    def payload_hash(self) -> str:
        """Hash of everything that is written to the portal (not the free-text source or display name)."""
        payload = self.model_dump(mode="json", exclude={"source", "customer_name"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()

    def default_key(self) -> str:
        return "ik-" + self.payload_hash()[:12]

    @property
    def total(self) -> float:
        return round(sum(line.qty * line.unit_price for line in self.lines), 2)


class RunInfo(BaseModel):
    """Where to find the evidence of one tool call, and how it was achieved."""

    run_id: str
    trace: str
    screenshots: list[str] = Field(default_factory=list)
    seconds: float = 0.0
    scripted_steps: int = 0
    cache_steps: int = 0
    model_steps: int = 0
    model_calls: int = 0


class OrderRow(BaseModel):
    order_no: str
    customer_po: str
    customer: str
    order_date: str
    delivery_date: str | None = None
    amount: float
    status: str


class OrderLineOut(BaseModel):
    sku: str
    qty: float
    unit_price: float


class OrderDetail(BaseModel):
    order_no: str
    customer: str
    customer_po: str
    order_date: str
    delivery_date: str | None = None
    status: str
    remark: str = ""
    total: float
    lines: list[OrderLineOut]


class StockItem(BaseModel):
    sku: str
    name: str
    spec: str
    unit: str
    on_hand: float
    available: float
    status: str


ToolStatus = Literal["ok", "needs_human"]


class SearchOrderResult(BaseModel):
    status: ToolStatus
    orders: list[OrderRow] = Field(default_factory=list)
    pages_read: int = 0
    message: str = ""
    run: RunInfo


class StockResult(BaseModel):
    status: ToolStatus
    items: list[StockItem] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    message: str = ""
    run: RunInfo


class InvoiceResult(BaseModel):
    status: Literal["ok", "no_invoice", "not_found", "needs_human"]
    order_no: str
    invoice_no: str | None = None
    amount: float | None = None
    file: str | None = None
    message: str = ""
    run: RunInfo


CreateStatus = Literal[
    "created",            # submitted after approval and verified in the portal
    "already_exists",     # this idempotency key was already entered (ledger or found in the portal); nothing new
    "rejected",           # the human approver said no; nothing submitted
    "approval_timeout",   # no decision in time; nothing submitted; call again with the same key to resume
    "portal_rejected",    # the portal's own validation refused the order (message in portal_errors)
    "conflict",           # the idempotency key was used before with different content
    "needs_human",        # the agent could not finish the form (layout not understood, step cap reached, ...)
    "unverified",         # submitted, but the verification search did not confirm it: a human must check
]


class CreateResult(BaseModel):
    status: CreateStatus
    idempotency_key: str
    order_no: str | None = None
    verified: bool = False
    approved_by: str | None = None
    portal_errors: list[str] = Field(default_factory=list)
    message: str = ""
    run: RunInfo | None = None
