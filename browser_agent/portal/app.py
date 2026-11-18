"""The fake legacy web ERP / supplier portal (FastAPI, server-rendered HTML, session cookie, no API).

Never point the agent at a real third-party site: this portal exists so the demo has something to automate.
The /admin pages are demo controls (layout switch, fault injection) that a real system would not have.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import secrets
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from .db import OrderLine, PortalDB, SearchCriteria
from .entry import Entry, EntryLine, parse_date, to_new_order, validate_header, validate_lines
from .invoice_pdf import invoice_pdf
from .layout_v1 import LayoutV1
from .layout_v2 import LayoutV2
from .layout_v3 import LayoutV3
from .views import ConfirmView, FormData, Layout, PageCtx, esc

LAYOUTS: dict[str, Layout] = {"v1": LayoutV1(), "v2": LayoutV2(), "v3": LayoutV3()}
COOKIE = "LEGACYSESSID"


class PortalSettings(BaseModel):
    layout: str = Field("v1", pattern="^v[123]$")
    submit_delay_once: float = Field(0.0, ge=0, le=60, description="下一次提交在保存后再拖延的秒数（故障注入）")
    drop_next_submit: bool = Field(False, description="下一次提交拖延后丢失、不保存（故障注入）")


@dataclass
class Session:
    username: str
    display: str
    entry: Entry = field(default_factory=Entry)


class PortalState:
    def __init__(self, db: PortalDB, system_date: dt.date):
        self.db = db
        self.system_date = system_date
        self.settings = PortalSettings()
        self.sessions: dict[str, Session] = {}

    @property
    def layout(self) -> Layout:
        return LAYOUTS[self.settings.layout]


def _html(text: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(text, status_code=status)


async def _params(request: Request) -> FormData:
    params = dict(request.query_params)
    if request.method == "POST":
        params.update({k: v for k, v in (await request.form()).items() if isinstance(v, str)})
    return params


def create_portal_app(db: PortalDB, system_date: dt.date) -> FastAPI:
    state = PortalState(db, system_date)
    app = FastAPI(title="Fake legacy portal", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.portal = state

    def session(request: Request) -> Session | None:
        return state.sessions.get(request.cookies.get(COOKIE, ""))

    def ctx(s: Session) -> PageCtx:
        return PageCtx(s.display, state.system_date.isoformat())

    def to_login() -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    def login_page() -> HTMLResponse:
        return _html(state.layout.login(None))

    @app.post("/login")
    async def login(request: Request) -> Response:
        username, password = state.layout.parse_login(await _params(request))
        display = db.check_login(username, password)
        if display is None:
            return _html(state.layout.login("用户名或密码错误"), 401)
        token = secrets.token_hex(16)
        state.sessions[token] = Session(username, display)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, token, httponly=True)
        return response

    @app.get("/logout")
    def logout(request: Request) -> Response:
        state.sessions.pop(request.cookies.get(COOKIE, ""), None)
        return to_login()

    @app.get("/")
    @app.post("/")
    def home(request: Request) -> Response:
        s = session(request)
        return _html(state.layout.home(ctx(s))) if s else to_login()

    @app.api_route("/orders", methods=["GET", "POST"])
    async def search(request: Request) -> Response:
        s = session(request)
        if not s:
            return to_login()
        customers = db.customers()
        search_request = state.layout.parse_search(await _params(request), customers)
        result = None
        if search_request.searched and not search_request.errors:
            f = search_request.form
            dates = {name: (value, parse_date(value)) for name, value in (("from", f.date_from), ("to", f.date_to))}
            bad = [value for value, parsed in dates.values() if value and parsed is None]
            if bad:
                search_request.errors = [f"日期格式不正确：{value}" for value in bad]
            else:
                criteria = SearchCriteria(f.order_no, f.cust_po, f.customer, dates["from"][1], dates["to"][1])
                result = db.search_orders(criteria, search_request.page)
        return _html(state.layout.search(ctx(s), search_request, customers, result))

    def confirm_view(entry: Entry) -> ConfirmView:
        new = to_new_order(entry, db)
        customer = db.customer(new.customer_code)
        assert customer is not None
        lines = []
        for i, line in enumerate(new.lines, 1):
            product = db.product(line.sku)
            assert product is not None
            lines.append(OrderLine(i, product.sku, product.name, product.spec, product.unit, line.qty, line.unit_price))
        return ConfirmView(customer, new.cust_po, new.order_date.isoformat(),
                           new.delivery_date.isoformat() if new.delivery_date else None, new.remark, lines)

    def render_entry(s: Session, errors: list[str]) -> HTMLResponse:
        confirm = confirm_view(s.entry) if s.entry.step == 3 else None
        return _html(state.layout.entry(ctx(s), s.entry, db.customers(), confirm, errors))

    @app.get("/orders/new")
    def entry_page(request: Request, reset: str = "") -> Response:
        s = session(request)
        if not s:
            return to_login()
        if reset:
            s.entry = Entry()
        return render_entry(s, [])

    @app.post("/orders/new")
    async def entry_post(request: Request) -> Response:
        s = session(request)
        if not s:
            return to_login()
        entry, layout = s.entry, state.layout
        action = layout.read_entry(await _params(request), entry, db.customers())
        errors: list[str] = []
        if action == "add_line":
            entry.lines.append(EntryLine())
        elif action == "next1" and entry.step == 1:
            errors = validate_header(entry, db)
            entry.step = 1 if errors else 2
        elif action == "next2" and entry.step == 2:
            errors = validate_lines(entry, db)
            entry.step = 2 if errors else 3
        elif action == "save" and entry.step == 1:
            errors = validate_header(entry, db) + validate_lines(entry, db)
            entry.step = 1 if errors else 3
        elif action == "prev":
            entry.step = 1 if layout.single_page_entry else max(1, entry.step - 1)
        elif action == "submit" and entry.step == 3:
            errors = validate_header(entry, db) + validate_lines(entry, db)
            if not errors:
                delay, drop = state.settings.submit_delay_once, state.settings.drop_next_submit
                state.settings = state.settings.model_copy(update={"submit_delay_once": 0.0, "drop_next_submit": False})
                if drop:  # slow and lost: nothing is saved
                    await asyncio.sleep(delay)
                    return _html(layout.message(ctx(s), "系统繁忙，请稍后再试"), 503)
                order_no = db.create_order(to_new_order(entry, db), s.username, state.system_date)
                s.entry = Entry()
                if delay:
                    await asyncio.sleep(delay)  # the order is already saved: a slow response, not a failed one
                return _html(layout.saved(ctx(s), order_no))
        return render_entry(s, errors)

    @app.get("/orders/{order_no}")
    def detail(request: Request, order_no: str) -> Response:
        s = session(request)
        if not s:
            return to_login()
        order = db.order(order_no)
        if order is None:
            return _html(state.layout.message(ctx(s), f"单据不存在：{order_no}"), 404)
        return _html(state.layout.detail(ctx(s), order))

    @app.get("/orders/{order_no}/invoice.pdf")
    def invoice(request: Request, order_no: str) -> Response:
        s = session(request)
        if not s:
            return to_login()
        order = db.order(order_no)
        if order is None or order.invoice is None:
            return _html(state.layout.message(ctx(s), f"没有可下载的发票：{order_no}"), 404)
        return Response(invoice_pdf(order), media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{order.invoice.invoice_no}.pdf"'})

    @app.api_route("/stock", methods=["GET", "POST"])
    async def stock(request: Request) -> Response:
        s = session(request)
        if not s:
            return to_login()
        query = state.layout.parse_stock(await _params(request))
        products = db.find_products(query) if query else (None if query is None else [])
        return _html(state.layout.stock(ctx(s), query or "", products))

    @app.get("/admin/settings")
    def settings_page() -> HTMLResponse:
        radios = "".join(
            f'<label><input type="radio" name="layout" value="{name}"{" checked" if name == state.settings.layout else ""}>'
            f" {name} {esc(layout.label)}</label><br>" for name, layout in LAYOUTS.items())
        return _html(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>演示控制</title></head>
<body style="font:14px sans-serif;margin:30px"><h2>演示控制（真实系统没有这个页面）</h2>
<form method="post" action="/admin/settings"><p>界面版本：<br>{radios}</p>
<p>下一次提交拖延秒数（故障注入）：<input name="submit_delay_once" value="{state.settings.submit_delay_once:g}"></p>
<p><label><input type="checkbox" name="drop_next_submit" value="1"{" checked" if state.settings.drop_next_submit else ""}>
拖延之后丢弃这次提交（不保存）</label></p>
<button type="submit">保存</button></form><p><a href="/">返回系统</a></p><p>演示数据均为虚构。</p></body></html>""")

    @app.post("/admin/settings")
    async def settings_form(request: Request) -> Response:
        form = await _params(request)
        state.settings = PortalSettings(layout=form.get("layout", state.settings.layout),
                                        submit_delay_once=float(form.get("submit_delay_once", "0") or 0),
                                        drop_next_submit=form.get("drop_next_submit") == "1")
        return RedirectResponse("/admin/settings", status_code=303)

    @app.get("/admin/api/settings")
    def get_settings() -> PortalSettings:
        return state.settings

    @app.post("/admin/api/settings")
    def set_settings(settings: PortalSettings) -> PortalSettings:
        state.settings = settings
        return state.settings

    return app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def serve_in_background(app: FastAPI, port: int) -> Iterator[str]:
    """Run an ASGI app with uvicorn in a daemon thread; yields its base URL."""
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError(f"server on port {port} did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)
