"""SQLite storage of the fake legacy portal (瑞丰机电批发（虚构） sales system).

Like many legacy systems it has no idempotency of its own: submitting the same order twice creates two orders.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE users (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, display TEXT NOT NULL);
CREATE TABLE customers (code TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (
    sku TEXT PRIMARY KEY, name TEXT NOT NULL, spec TEXT NOT NULL, unit TEXT NOT NULL, list_price REAL NOT NULL,
    on_hand REAL NOT NULL, reserved REAL NOT NULL, active INTEGER NOT NULL);
CREATE TABLE orders (
    order_no TEXT PRIMARY KEY, customer_code TEXT NOT NULL, cust_po TEXT NOT NULL, order_date TEXT NOT NULL,
    delivery_date TEXT, status TEXT NOT NULL, remark TEXT NOT NULL, total REAL NOT NULL, created_by TEXT NOT NULL);
CREATE TABLE order_lines (
    order_no TEXT NOT NULL, line_no INTEGER NOT NULL, sku TEXT NOT NULL, qty REAL NOT NULL, unit_price REAL NOT NULL,
    PRIMARY KEY (order_no, line_no));
CREATE TABLE invoices (invoice_no TEXT PRIMARY KEY, order_no TEXT NOT NULL, issue_date TEXT NOT NULL, amount REAL NOT NULL);
"""

PAGE_SIZE = 10


def _hash_password(username: str, password: str) -> str:
    return hashlib.sha256(f"legacy-salt:{username}:{password}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Customer:
    code: str
    name: str


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    spec: str
    unit: str
    list_price: float
    on_hand: float
    reserved: float
    active: bool

    @property
    def available(self) -> float:
        return self.on_hand - self.reserved


@dataclass(frozen=True)
class OrderLine:
    line_no: int
    sku: str
    name: str
    spec: str
    unit: str
    qty: float
    unit_price: float

    @property
    def amount(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass(frozen=True)
class Invoice:
    invoice_no: str
    issue_date: str
    amount: float


@dataclass(frozen=True)
class Order:
    order_no: str
    customer: Customer
    cust_po: str
    order_date: str
    delivery_date: str | None
    status: str
    remark: str
    total: float
    lines: tuple[OrderLine, ...] = ()
    invoice: Invoice | None = None


@dataclass(frozen=True)
class SearchCriteria:
    order_no: str = ""
    cust_po: str = ""
    customer: str = ""
    date_from: dt.date | None = None
    date_to: dt.date | None = None


@dataclass(frozen=True)
class SearchPage:
    rows: list[Order]
    total: int
    page: int
    pages: int


@dataclass
class NewLine:
    sku: str
    qty: float
    unit_price: float


@dataclass
class NewOrder:
    customer_code: str
    cust_po: str
    order_date: dt.date
    delivery_date: dt.date | None
    remark: str
    lines: list[NewLine] = field(default_factory=list)


class PortalDB:
    def __init__(self, path: Path):
        self.path = path

    @classmethod
    def create(cls, path: Path, data_dir: Path) -> "PortalDB":
        """Create a fresh database from data/master and data/portal_seed.json (an existing file is replaced)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        products = json.loads((data_dir / "master" / "products.json").read_text(encoding="utf-8"))
        customers = json.loads((data_dir / "master" / "customers.json").read_text(encoding="utf-8"))
        seed = json.loads((data_dir / "portal_seed.json").read_text(encoding="utf-8"))
        stock = {s["sku"]: s for s in seed["stock"]}
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.executescript(SCHEMA)
            conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [
                (u["username"], _hash_password(u["username"], u["password"]), u["display"]) for u in seed["users"]])
            conn.executemany("INSERT INTO customers VALUES (?, ?)", [(c["id"], c["name"]) for c in customers])
            conn.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                (p["sku"], p["name"], p["spec"], p["unit"], p["list_price"], stock[p["sku"]]["on_hand"],
                 stock[p["sku"]]["reserved"], int(stock[p["sku"]]["active"])) for p in products])
            for o in seed["orders"]:
                total = round(sum(line["qty"] * line["unit_price"] for line in o["lines"]), 2)
                conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'seed')",
                             (o["order_no"], o["customer_id"], o["cust_po"], o["order_date"], o["delivery_date"],
                              o["status"], o["remark"], total))
                conn.executemany("INSERT INTO order_lines VALUES (?, ?, ?, ?, ?)", [
                    (o["order_no"], i, line["sku"], line["qty"], line["unit_price"])
                    for i, line in enumerate(o["lines"], 1)])
                if o["invoice"]:
                    inv = o["invoice"]
                    conn.execute("INSERT INTO invoices VALUES (?, ?, ?, ?)",
                                 (inv["invoice_no"], o["order_no"], inv["issue_date"], inv["amount"]))
        return cls(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def check_login(self, username: str, password: str) -> str | None:
        """Display name of the user, or None when the credentials are wrong."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row and row["password_hash"] == _hash_password(username, password):
            return row["display"]
        return None

    def customers(self) -> list[Customer]:
        with closing(self._connect()) as conn:
            return [Customer(r["code"], r["name"]) for r in conn.execute("SELECT * FROM customers ORDER BY code")]

    def customer(self, code: str) -> Customer | None:
        return next((c for c in self.customers() if c.code == code), None)

    def product(self, sku: str) -> Product | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM products WHERE sku = ?", (sku,)).fetchone()
        return _product(row) if row else None

    def find_products(self, query: str) -> list[Product]:
        """Exact SKU match first; otherwise a name/spec contains-search (at most 20 rows)."""
        exact = self.product(query)
        if exact:
            return [exact]
        like = f"%{query}%"
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT * FROM products WHERE name LIKE ? OR spec LIKE ? ORDER BY sku LIMIT 20",
                                (like, like)).fetchall()
        return [_product(r) for r in rows]

    def search_orders(self, criteria: SearchCriteria, page: int) -> SearchPage:
        where, args = ["1 = 1"], []
        if criteria.order_no:
            where.append("o.order_no = ?")
            args.append(criteria.order_no)
        if criteria.cust_po:
            where.append("o.cust_po LIKE ?")
            args.append(f"%{criteria.cust_po}%")
        if criteria.customer:
            where.append("o.customer_code = ?")
            args.append(criteria.customer)
        if criteria.date_from:
            where.append("o.order_date >= ?")
            args.append(criteria.date_from.isoformat())
        if criteria.date_to:
            where.append("o.order_date <= ?")
            args.append(criteria.date_to.isoformat())
        clause = " AND ".join(where)
        with closing(self._connect()) as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM orders o WHERE {clause}", args).fetchone()[0]
            pages = max(1, -(-total // PAGE_SIZE))
            page = min(max(1, page), pages)
            numbers = [r[0] for r in conn.execute(
                f"SELECT o.order_no FROM orders o WHERE {clause} ORDER BY o.order_date DESC, o.order_no DESC "
                f"LIMIT ? OFFSET ?", [*args, PAGE_SIZE, (page - 1) * PAGE_SIZE])]
        return SearchPage([self.order(n) for n in numbers], total, page, pages)  # type: ignore[misc]

    def order(self, order_no: str) -> Order | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c "
                               "ON c.code = o.customer_code WHERE o.order_no = ?", (order_no,)).fetchone()
            if not row:
                return None
            lines = tuple(OrderLine(r["line_no"], r["sku"], r["name"], r["spec"], r["unit"], r["qty"], r["unit_price"])
                          for r in conn.execute(
                              "SELECT l.*, p.name, p.spec, p.unit FROM order_lines l JOIN products p ON p.sku = l.sku "
                              "WHERE l.order_no = ? ORDER BY l.line_no", (order_no,)))
            inv = conn.execute("SELECT * FROM invoices WHERE order_no = ?", (order_no,)).fetchone()
        return Order(row["order_no"], Customer(row["customer_code"], row["customer_name"]), row["cust_po"],
                     row["order_date"], row["delivery_date"], row["status"], row["remark"], row["total"], lines,
                     Invoice(inv["invoice_no"], inv["issue_date"], inv["amount"]) if inv else None)

    def create_order(self, new: NewOrder, created_by: str, system_date: dt.date) -> str:
        total = round(sum(line.qty * line.unit_price for line in new.lines), 2)
        prefix = f"XS{system_date:%y%m}"
        with closing(self._connect()) as conn, conn:
            last = conn.execute("SELECT MAX(order_no) FROM orders WHERE order_no LIKE ?", (prefix + "%",)).fetchone()[0]
            order_no = f"{prefix}{int(last[len(prefix):]) + 1 if last else 1:04d}"
            conn.execute("INSERT INTO orders VALUES (?, ?, ?, ?, ?, '草稿', ?, ?, ?)",
                         (order_no, new.customer_code, new.cust_po, new.order_date.isoformat(),
                          new.delivery_date.isoformat() if new.delivery_date else None, new.remark, total, created_by))
            conn.executemany("INSERT INTO order_lines VALUES (?, ?, ?, ?, ?)", [
                (order_no, i, line.sku, line.qty, line.unit_price) for i, line in enumerate(new.lines, 1)])
        return order_no

    def orders_created_by(self, username: str) -> list[Order]:
        """Orders entered through the UI (not seed data); used by the evaluation to count duplicates."""
        with closing(self._connect()) as conn:
            numbers = [r[0] for r in conn.execute(
                "SELECT order_no FROM orders WHERE created_by = ? ORDER BY order_no", (username,))]
        return [o for o in (self.order(n) for n in numbers) if o]


def _product(row: sqlite3.Row) -> Product:
    return Product(row["sku"], row["name"], row["spec"], row["unit"], row["list_price"], row["on_hand"],
                   row["reserved"], bool(row["active"]))
