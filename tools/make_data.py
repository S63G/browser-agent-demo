"""Generate the fake portal's seed data and the 20 evaluation tasks (deterministic, fixed random seed).

Inputs (copied from Demo A so the demos share one fictional world):
  data/master/products.json, data/master/customers.json, data/demo_a_orders.json
Outputs:
  data/portal_seed.json   users, stock levels, inactive SKUs, ~50 historical sales orders and invoices
  data/eval_tasks.json    T01–T10 run on layout v1, T11–T20 on layout v2 (T11–T20 are re-run on hold-out v3)

Usage: python tools/make_data.py
"""
from __future__ import annotations

import datetime as dt
import json
import random
from collections.abc import Callable
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SEED = 20261116
SYSTEM_DATE = dt.date(2026, 11, 16)

# Fictional demo operator account of the fake portal (also the default in .env.example).
USERS = [{"username": "op01", "password": "Ruifeng#2026", "display": "录单员 01"}]

# How many historical orders each customer gets: C001 and C004 get enough to need two result pages.
ORDER_COUNTS = {"C001": 13, "C002": 4, "C003": 4, "C004": 12, "C005": 3, "C006": 4, "C007": 3, "C008": 4}

# SKUs switched to 停用 (inactive). 8.04.0005 and 6.02.0009 each occur in exactly one Demo A order (tasks T10/T20).
FORCED_INACTIVE = ["8.04.0005", "6.02.0009"]

PO_FORMATS: dict[str, Callable[[dt.date, random.Random], str]] = {
    "C001": lambda d, r: f"HL-PO-{d:%y%m%d}-0{r.randint(1, 3)}",
    "C002": lambda d, r: f"YG-{d:%y%m}-{r.randint(300, 456):04d}",
    "C003": lambda d, r: f"PRT-{r.randint(54100, 54990)}",
    "C004": lambda d, r: f"CL-PO-{d:%Y%m%d}",
    "C005": lambda d, r: f"BCQ-{d:%y%m%d}",
    "C006": lambda d, r: f"KMS-PO-{r.randint(7600, 7779)}",
    "C007": lambda d, r: f"SD-{d:%m%d}",
    "C008": lambda d, r: f"LX-{d:%y%m}-{r.randint(1, 20):03d}",
}
REMARKS = ["", "", "", "电话下单", "随货附送货单", "客户自提", "月结"]


def load(name: str) -> object:
    return json.loads((DATA / name).read_text(encoding="utf-8"))


def weekdays(start: dt.date, end: dt.date) -> list[dt.date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)
    return days


def build_stock(rng: random.Random, products: list[dict], protected: set[str]) -> list[dict]:
    caps = {"件": 600, "箱": 80, "台": 20, "卷": 60}
    candidates = [p["sku"] for p in products if p["sku"] not in protected]
    inactive = set(FORCED_INACTIVE) | set(rng.sample(candidates, 6))
    stock = []
    for p in products:
        on_hand = rng.randint(0, caps.get(p["unit"], 300))
        reserved = rng.randint(0, on_hand // 4) if on_hand else 0
        stock.append({"sku": p["sku"], "on_hand": on_hand, "reserved": reserved, "active": p["sku"] not in inactive})
    return stock


def status_for(order_date: dt.date, rng: random.Random) -> str:
    if order_date < dt.date(2026, 10, 26):
        return "已开票"
    if order_date < dt.date(2026, 11, 6):
        return "已发货"
    return rng.choice(["已审核", "已审核", "草稿"])


def build_orders(rng: random.Random, customers: list[dict], products: dict[str, dict],
                 reserved_pos: set[str]) -> list[dict]:
    days = weekdays(dt.date(2026, 10, 8), dt.date(2026, 11, 13))
    drafts = []
    for c in customers:
        skus = sorted(c["agreed_prices"]) or sorted(rng.sample(sorted(products), 20))
        for day in sorted(rng.sample(days, ORDER_COUNTS[c["id"]])):
            po = PO_FORMATS[c["id"]](day, rng)
            while po in reserved_pos:
                po += "A"
            reserved_pos.add(po)
            lines = []
            for sku in rng.sample(skus, rng.randint(1, 4)):
                price = c["agreed_prices"].get(sku, products[sku]["list_price"])
                qty = rng.choice([1, 2, 3, 5, 10]) if products[sku]["unit"] in ("箱", "台", "卷") else rng.choice(
                    [6, 10, 12, 20, 50, 100])
                lines.append({"sku": sku, "qty": qty, "unit_price": price})
            drafts.append({"customer_id": c["id"], "cust_po": po, "order_date": day,
                           "delivery_date": day + dt.timedelta(days=rng.choice([5, 7, 10, 14])),
                           "status": status_for(day, rng), "remark": rng.choice(REMARKS), "lines": lines})
    drafts.sort(key=lambda o: (o["order_date"], o["customer_id"]))
    orders, seq, invoice_seq = [], {}, 0
    for o in drafts:
        month = o["order_date"].strftime("%y%m")
        seq[month] = seq.get(month, 0) + 1
        total = round(sum(l["qty"] * l["unit_price"] for l in o["lines"]), 2)
        invoice = None
        if o["status"] == "已开票":
            invoice_seq += 1
            issued = o["order_date"] + dt.timedelta(days=rng.randint(7, 12))
            invoice = {"invoice_no": f"FP{issued:%y%m}{invoice_seq:05d}", "issue_date": issued.isoformat(),
                       "amount": total}
        orders.append({**o, "order_no": f"XS{month}{seq[month]:04d}", "order_date": o["order_date"].isoformat(),
                       "delivery_date": o["delivery_date"].isoformat(), "invoice": invoice})
    return orders


def find_order(orders: list[dict], **conditions: object) -> dict:
    return next(o for o in orders if all(o[k] == v for k, v in conditions.items()))


def demo_a_order(demo_a: list[dict], po: str) -> dict:
    order = next(o for o in demo_a if o["customer_po"] == po)
    return {k: order[k] for k in ("customer_id", "customer_name", "customer_po", "order_date", "delivery_date",
                                  "lines")} | {"source": f"Demo A: {order['source_doc']}"}


def build_tasks(orders: list[dict], stock: list[dict], demo_a: list[dict]) -> list[dict]:
    by_sku = {s["sku"]: s for s in stock}

    def stock_expect(skus: list[str]) -> dict:
        return {"found": {s: by_sku[s]["on_hand"] - by_sku[s]["reserved"] for s in skus if s in by_sku},
                "not_found": [s for s in skus if s not in by_sku]}

    def paged(customer: str, start: str, end: str) -> list[str]:
        return sorted(o["order_no"] for o in orders
                      if o["customer_id"] == customer and start <= o["order_date"] <= end)

    plan = [
        # (layout, search-by-PO customer, paged customer, stock SKUs, invoiced customer, not-invoiced customer,
        #  Demo A POs for: duplicate call, many lines, submit timeout, approver rejects, inactive SKU).
        # Orders created by T06–T08 must not fall into a later search task's result: no C004 orders before T12.
        ("v1", "C002", "C001", ["1.01.0003", "3.01.0019", "9.99.9999"], "C003", "C008",
         ["HL-PO-261102-01", "HL-PO-261112-01", "KMS-PO-7781", "SD-1104", "KMS-PO-7802"]),
        ("v2", "C006", "C004", ["4.01.0013", "7.02.0005", "1.01.0999"], "C005", "C004",
         ["YG-2611-0457", "PRT-55012", "HL-PO-261113-01", "LX-2611-033", "PRT-55071"]),
    ]
    tasks: list[dict] = []
    for layout, po_cust, paged_cust, skus, inv_cust, no_inv_cust, creates in plan:
        po_order = find_order(orders, customer_id=po_cust)
        invoiced = find_order(orders, customer_id=inv_cust, status="已开票")
        not_invoiced = find_order(orders, customer_id=no_inv_cust, invoice=None)
        specs: list[tuple[str, str, str, dict, dict]] = [
            ("search_po", "按客户单号查单", "search_order", {"customer_po": po_order["cust_po"]},
             {"order_nos": [po_order["order_no"]]}),
            ("search_paged", "按客户 + 日期范围查单（结果跨两页）", "search_order",
             {"customer_id": paged_cust, "date_from": "2026-10-01", "date_to": "2026-11-15"},
             {"order_nos": paged(paged_cust, "2026-10-01", "2026-11-15")}),
            ("stock", "查 3 个料号的可用库存（含 1 个不存在的料号）", "check_stock", {"skus": skus},
             stock_expect(skus)),
            ("invoice", "下载已开票订单的发票 PDF", "download_invoice", {"order_no": invoiced["order_no"]},
             {"status": "ok", **invoiced["invoice"]}),
            ("invoice_none", "未开票订单：应如实返回“无发票”", "download_invoice",
             {"order_no": not_invoiced["order_no"]}, {"status": "no_invoice"}),
            ("create_twice", "录入 Demo A 订单，再用同一幂等键调用一次", "create_sales_order_draft",
             {"order": demo_a_order(demo_a, creates[0])}, {"status": "created", "second_status": "already_exists"}),
            ("create_lines", "录入多行订单（含箱装物料）", "create_sales_order_draft",
             {"order": demo_a_order(demo_a, creates[1])}, {"status": "created"}),
            ("create_timeout", "提交后门户响应超时（故障注入 8 秒）：先核实再决定是否重试", "create_sales_order_draft",
             {"order": demo_a_order(demo_a, creates[2])}, {"status": "created", "inject_submit_delay": 8}),
            ("create_rejected", "审批人驳回：不得提交", "create_sales_order_draft",
             {"order": demo_a_order(demo_a, creates[3])}, {"status": "rejected", "approver": "reject"}),
            ("create_inactive", "订单含停用物料：门户报错，转人工", "create_sales_order_draft",
             {"order": demo_a_order(demo_a, creates[4])}, {"status": "portal_rejected"}),
        ]
        for kind, title, tool, args, expect in specs:
            tasks.append({"id": f"T{len(tasks) + 1:02d}", "layout": layout, "kind": kind, "title": title,
                          "tool": tool, "args": args, "expect": expect})
    return tasks


def main() -> None:
    rng = random.Random(SEED)
    products = load("master/products.json")
    customers = load("master/customers.json")
    demo_a = load("demo_a_orders.json")["orders"]
    protected = {line["sku"] for o in demo_a for line in o["lines"]}
    stock = build_stock(rng, products, protected)
    orders = build_orders(rng, customers, {p["sku"]: p for p in products}, {o["customer_po"] for o in demo_a})
    seed = {"note": "Seed data for the fake legacy portal. 演示数据均为虚构。", "generator": "tools/make_data.py",
            "random_seed": SEED, "system_date": SYSTEM_DATE.isoformat(), "users": USERS, "stock": stock,
            "orders": orders}
    (DATA / "portal_seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    tasks = build_tasks(orders, stock, demo_a)
    doc = {"note": "T01–T10 run on layout v1, T11–T20 on layout v2; T11–T20 are re-run on the hold-out layout v3.",
           "generator": "tools/make_data.py", "tasks": tasks}
    (DATA / "eval_tasks.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"portal_seed.json: {len(stock)} SKUs, {len(orders)} orders; eval_tasks.json: {len(tasks)} tasks")


if __name__ == "__main__":
    main()
