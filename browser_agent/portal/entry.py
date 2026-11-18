"""State and validation of the multi-step "新建销售订单" form, shared by all layouts.

Field values are kept as the raw strings the user typed, so a page with validation errors re-renders them.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .db import NewLine, NewOrder, PortalDB

DEFAULT_ROWS = 3


@dataclass
class EntryLine:
    sku: str = ""
    qty: str = ""
    price: str = ""

    @property
    def blank(self) -> bool:
        return not (self.sku or self.qty or self.price)


@dataclass
class Entry:
    step: int = 1                 # 1 = header (and lines on single-page layouts), 2 = lines, 3 = confirm
    customer: str = ""            # customer code
    cust_po: str = ""
    order_date: str = ""          # ISO date when parsed, otherwise what was typed
    delivery_date: str = ""
    remark: str = ""
    lines: list[EntryLine] = field(default_factory=lambda: [EntryLine() for _ in range(DEFAULT_ROWS)])

    def filled_lines(self) -> list[EntryLine]:
        return [line for line in self.lines if not line.blank]


def parse_date(text: str) -> dt.date | None:
    """Accepts 2026-11-02, 2026/11/02, 2026.11.02 and 20261102."""
    text = text.strip()
    match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text) or re.fullmatch(r"(\d{4})(\d{2})(\d{2})", text)
    if not match:
        return None
    try:
        return dt.date(*(int(g) for g in match.groups()))
    except ValueError:
        return None


def normalise_date(text: str) -> str:
    parsed = parse_date(text)
    return parsed.isoformat() if parsed else text.strip()


def _number(text: str) -> float | None:
    try:
        return float(text.replace(",", "").strip())
    except ValueError:
        return None


def validate_header(entry: Entry, db: PortalDB) -> list[str]:
    errors = []
    if not entry.customer:
        errors.append("客户不能为空")
    elif db.customer(entry.customer) is None:
        errors.append(f"客户编码不存在：{entry.customer}")
    if not entry.cust_po.strip():
        errors.append("客户单号不能为空")
    order_date = parse_date(entry.order_date)
    if order_date is None:
        errors.append("单据日期格式不正确" if entry.order_date else "单据日期不能为空")
    if entry.delivery_date:
        delivery = parse_date(entry.delivery_date)
        if delivery is None:
            errors.append("交货日期格式不正确")
        elif order_date and delivery < order_date:
            errors.append("交货日期不能早于单据日期")
    return errors


def validate_lines(entry: Entry, db: PortalDB) -> list[str]:
    filled = entry.filled_lines()
    if not filled:
        return ["至少录入一行明细"]
    errors = []
    for i, line in enumerate(entry.lines, 1):
        if line.blank:
            continue
        product = db.product(line.sku.strip())
        if product is None:
            errors.append(f"第 {i} 行：物料编码不存在（{line.sku}）")
        elif not product.active:
            errors.append(f"第 {i} 行：物料 {product.sku} 已停用，不能下单")
        qty = _number(line.qty)
        if qty is None or qty <= 0:
            errors.append(f"第 {i} 行：数量必须大于 0")
        if line.price and (_number(line.price) is None or (_number(line.price) or 0) < 0):
            errors.append(f"第 {i} 行：单价格式不正确")
    return errors


def to_new_order(entry: Entry, db: PortalDB) -> NewOrder:
    lines = []
    for line in entry.filled_lines():
        product = db.product(line.sku.strip())
        assert product is not None
        price = _number(line.price) if line.price else product.list_price
        lines.append(NewLine(product.sku, _number(line.qty) or 0.0, price if price is not None else product.list_price))
    order_date = parse_date(entry.order_date)
    assert order_date is not None
    return NewOrder(entry.customer, entry.cust_po.strip(), order_date, parse_date(entry.delivery_date),
                    entry.remark.strip(), lines)
