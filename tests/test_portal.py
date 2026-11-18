"""The fake legacy portal: login, search paging, validation, wizard, layouts, invoice PDF, fault injection."""
from __future__ import annotations

import io
import re
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from pypdf import PdfReader

from browser_agent.portal import PortalSettings, build_portal

from .support import DATA

LOGIN = {
    "v1": {"username": "op01", "password": "Ruifeng#2026"},
    "v2": {"uid": "op01", "pwd": "Ruifeng#2026"},
    "v3": {"ctl00$cph$txtOper": "op01", "ctl00$cph$txtPwd": "Ruifeng#2026", "__EVENTTARGET": "ctl00$cph$lnkLogin"},
}


class PortalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.app = build_portal(self.tmp / "portal.db", DATA, fresh=True)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def login(self, layout: str = "v1") -> None:
        self.app.state.portal.settings = PortalSettings(layout=layout)
        response = self.client.post("/login", data=LOGIN[layout])
        self.assertEqual(response.url.path, "/")

    def test_pages_require_login_and_wrong_password_is_refused(self) -> None:
        self.assertEqual(self.client.get("/orders").url.path, "/login")
        response = self.client.post("/login", data={"username": "op01", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertIn("用户名或密码错误", response.text)

    def test_every_layout_logs_in_with_its_own_field_names(self) -> None:
        for layout in ("v1", "v2", "v3"):
            with self.subTest(layout=layout):
                self.client.cookies.clear()
                self.login(layout)
                self.assertIn("演示数据均为虚构", self.client.get("/").text)

    def test_v1_search_pages_through_thirteen_orders(self) -> None:
        self.login()
        query = {"customer": "C001", "date_from": "2026-10-01", "date_to": "2026-11-15", "search": "1"}
        first = self.client.get("/orders", params=query).text
        second = self.client.get("/orders", params=query | {"page": "2"}).text
        self.assertIn("共 13 条记录，第 1/2 页", first)
        self.assertIn('id="nextPage"', first)
        self.assertNotIn('id="nextPage"', second)
        self.assertEqual(first.count('class="order-link"') + second.count('class="order-link"'), 13)

    def test_layouts_do_not_share_selectors(self) -> None:
        pages = {}
        for layout in ("v1", "v2", "v3"):
            self.client.cookies.clear()
            self.login(layout)
            pages[layout] = self.client.get("/orders/new?reset=1").text
        self.assertIn('id="cust_po"', pages["v1"])
        for layout in ("v2", "v3"):
            self.assertNotIn('id="cust_po"', pages[layout])
            self.assertNotIn('id="btnNext1"', pages[layout])
        self.assertIn("ddlBillY", pages["v3"])  # 年/月/日 drop-downs only in the hold-out layout

    def test_v3_search_requires_compact_dates_and_pages_by_postback(self) -> None:
        self.login("v3")
        form = {"ctl00$cph$txtCustCode": "C004", "ctl00$cph$txtDateFrom": "2026-10-01",
                "__EVENTTARGET": "ctl00$cph$btnQuery"}
        self.assertIn("日期格式错误，应为 yyyymmdd", self.client.post("/orders", data=form).text)
        form |= {"ctl00$cph$txtDateFrom": "20261001", "ctl00$cph$txtDateTo": "20261115",
                 "__EVENTTARGET": "ctl00$cph$gvOrders", "__EVENTARGUMENT": "Page$Next", "ctl00$cph$hidPage": "1"}
        self.assertIn("第2/2页", self.client.post("/orders", data=form).text)

    def test_wizard_validates_then_saves_a_draft(self) -> None:
        self.login()
        self.client.get("/orders/new?reset=1")
        header = {"customer": "C001", "cust_po": "T-1", "order_date": "2026-11-10", "action": "next1"}
        page = self.client.post("/orders/new", data=header | {"delivery_date": "2026-11-01"}).text
        self.assertIn("交货日期不能早于单据日期", page)
        self.client.post("/orders/new", data=header | {"delivery_date": "2026-11-20"})
        page = self.client.post("/orders/new", data={"sku_1": "8.04.0005", "qty_1": "3", "action": "next2"}).text
        self.assertIn("已停用，不能下单", page)
        page = self.client.post("/orders/new", data={"sku_1": "1.01.0003", "qty_1": "3", "price_1": "6.4",
                                                     "action": "next2"}).text
        self.assertIn('id="confirmTotal">19.20<', page)
        page = self.client.post("/orders/new", data={"action": "submit"}).text
        order_no = re.search(r"保存成功！单据编号：<b>(XS2611\d{4})</b>", page).group(1)
        order = self.app.state.portal.db.order(order_no)
        self.assertEqual((order.cust_po, order.status, order.total), ("T-1", "草稿", 19.2))

    def test_portal_has_no_idempotency_of_its_own(self) -> None:
        self.login()
        for _ in range(2):
            self.client.get("/orders/new?reset=1")
            self.client.post("/orders/new", data={"customer": "C001", "cust_po": "SAME", "order_date": "2026-11-10",
                                                  "action": "next1"})
            self.client.post("/orders/new", data={"sku_1": "1.01.0003", "qty_1": "1", "action": "next2"})
            self.client.post("/orders/new", data={"action": "submit"})
        self.assertEqual(len(self.app.state.portal.db.orders_created_by("op01")), 2)

    def test_submit_delay_saves_first_then_answers_late(self) -> None:
        self.login()
        self.client.post("/admin/api/settings", json={"layout": "v1", "submit_delay_once": 0.6})
        self.client.get("/orders/new?reset=1")
        self.client.post("/orders/new", data={"customer": "C002", "cust_po": "SLOW", "order_date": "2026-11-10",
                                              "action": "next1"})
        self.client.post("/orders/new", data={"sku_1": "1.01.0003", "qty_1": "1", "action": "next2"})
        started = time.monotonic()
        self.client.post("/orders/new", data={"action": "submit"})
        self.assertGreaterEqual(time.monotonic() - started, 0.6)
        self.assertEqual(self.client.get("/admin/api/settings").json()["submit_delay_once"], 0)

    def test_invoice_pdf_has_an_extractable_text_layer(self) -> None:
        self.login()
        pdf = self.client.get("/orders/XS26100013/invoice.pdf")
        self.assertEqual(pdf.headers["content-type"], "application/pdf")
        text = PdfReader(io.BytesIO(pdf.content)).pages[0].extract_text()
        self.assertIn("发票号码：FP261000013", text)
        self.assertIn("销售单号：XS26100013", text)
        self.assertEqual(self.client.get("/orders/XS26100023/invoice.pdf").status_code, 404)


if __name__ == "__main__":
    unittest.main()
