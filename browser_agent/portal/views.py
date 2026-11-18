"""What every layout must render and parse. The three layouts share data and rules, never DOM or field names."""
from __future__ import annotations

import html
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from .db import Customer, Order, OrderLine, Product, SearchPage
from .entry import Entry

FormData = Mapping[str, str]


def esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def qty(value: float) -> str:
    return f"{value:g}"


def page_number(text: str) -> int:
    return int(text) if text.isdigit() and int(text) > 0 else 1


@dataclass(frozen=True)
class PageCtx:
    user: str
    system_date: str


@dataclass
class SearchForm:
    order_no: str = ""
    cust_po: str = ""
    customer: str = ""
    date_from: str = ""
    date_to: str = ""


@dataclass
class SearchRequest:
    form: SearchForm
    page: int = 1
    searched: bool = False
    errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ConfirmView:
    customer: Customer
    cust_po: str
    order_date: str
    delivery_date: str | None
    remark: str
    lines: list[OrderLine]

    @property
    def total(self) -> float:
        return round(sum(line.amount for line in self.lines), 2)


class Layout(Protocol):
    name: str
    label: str
    single_page_entry: bool

    def login(self, error: str | None) -> str: ...

    def home(self, ctx: PageCtx) -> str: ...

    def search(self, ctx: PageCtx, request: SearchRequest, customers: list[Customer],
               result: SearchPage | None) -> str: ...

    def detail(self, ctx: PageCtx, order: Order) -> str: ...

    def stock(self, ctx: PageCtx, query: str, products: list[Product] | None) -> str: ...

    def entry(self, ctx: PageCtx, entry: Entry, customers: list[Customer], confirm: ConfirmView | None,
              errors: list[str]) -> str: ...

    def saved(self, ctx: PageCtx, order_no: str) -> str: ...

    def message(self, ctx: PageCtx, text: str) -> str: ...

    def parse_login(self, form: FormData) -> tuple[str, str]: ...

    def parse_search(self, params: FormData, customers: list[Customer]) -> SearchRequest: ...

    def parse_stock(self, params: FormData) -> str | None: ...

    def read_entry(self, form: FormData, entry: Entry, customers: list[Customer]) -> str:
        """Copy the submitted fields into ``entry`` and return the action: next1, add_line, next2, save, prev, submit."""
        ...
