"""The constraints on the model fallback, tested without a browser or a model."""
from __future__ import annotations

import unittest

from browser_agent.flows import StockPage
from browser_agent.models import OrderLineOut, OrderRow, StockItem
from browser_agent.navigator import LocateAnswer, check_actions, grounding_problems
from browser_agent.snapshot import Element, Snapshot
from browser_agent.steps import SECRET, Step, date_spellings, option_matches, resolve_option

CUSTOMERS = (("", "请选择客户"), ("k101", "沪东衡砺设备维修有限公司（C001）"), ("k102", "甬港澄远机械配件厂（C002）"))


def element(ref: str, tag: str = "input", type_: str = "text", label: str = "", options: tuple = ()) -> Element:
    return Element(ref, tag, type_, label, "", "", "", "", options, False)


SNAP = Snapshot("t", (), "", (
    element("e1", label="采购单号"),
    element("e2", "select", "", "客户名称", CUSTOMERS),
    element("e3", "button", "submit", "确认保存"),
    element("e4", "select", "", "年", (("2025", "2025"), ("2026", "2026"))),
    element("e5", "select", "", "月", tuple((f"{m:02d}", f"{m:02d}") for m in range(1, 13))),
))


def answer(*actions: tuple[str, str, str]) -> LocateAnswer:
    return LocateAnswer.model_validate({"actions": [{"ref": r, "op": o, "value": v} for r, o, v in actions]})


class StepValueTest(unittest.TestCase):
    def test_dates_expand_into_formats_and_components(self) -> None:
        variants = Step("k", "i", "#x", "fill", "2026-11-05").variants()
        self.assertEqual(variants["compact"], "20261105")
        self.assertEqual((variants["year"], variants["month"], variants["day"]), ("2026", "11", "05"))

    def test_secret_steps_only_offer_the_placeholder(self) -> None:
        self.assertEqual(Step("k", "i", "#x", "fill", "real-password", secret=True).variants(), {"secret": SECRET})

    def test_short_tokens_must_match_exactly(self) -> None:
        variants = {"day": "05"}
        self.assertTrue(option_matches("5", "5日", variants))
        self.assertFalse(option_matches("15", "15", variants))
        self.assertTrue(option_matches("k101", "沪东衡砺设备维修有限公司（C001）", {"value": "C001"}))

    def test_resolve_option_by_text_value_or_step_value(self) -> None:
        variants = {"value": "C002", "alt0": "甬港澄远机械配件厂"}
        self.assertEqual(resolve_option(list(CUSTOMERS), "甬港澄远机械配件厂（C002）", variants), "k102")
        self.assertEqual(resolve_option(list(CUSTOMERS), "k102", variants), "k102")
        self.assertEqual(resolve_option(list(CUSTOMERS), "甬港澄远机械配件厂", variants), "k102")
        self.assertIsNone(resolve_option(list(CUSTOMERS), "k101", variants))  # an option for another customer

    def test_date_spellings(self) -> None:
        self.assertIn("2026.11.02", date_spellings("2026-11-02"))
        self.assertIn("2026-11-02", date_spellings("20261102"))
        self.assertEqual(date_spellings("XS26110002"), [])


class CheckActionsTest(unittest.TestCase):
    PO = Step("entry.customer_po", "填写客户单号", "#cust_po", "fill", "YG-2611-0457")

    def test_accepts_a_valid_fill(self) -> None:
        self.assertIsNone(check_actions(answer(("e1", "fill", "YG-2611-0457")), self.PO, SNAP))

    def test_rejects_unknown_refs_values_and_ops(self) -> None:
        self.assertIn("不在元素列表", check_actions(answer(("e9", "fill", "YG-2611-0457")), self.PO, SNAP))
        self.assertIn("不在允许的值", check_actions(answer(("e1", "fill", "something else")), self.PO, SNAP))
        self.assertIn("只允许", check_actions(answer(("e3", "click", "")), self.PO, SNAP))

    def test_click_steps_take_exactly_one_click(self) -> None:
        submit = Step("entry.submit", "提交", "#btnSubmit", "click")
        self.assertIsNone(check_actions(answer(("e3", "click", "")), submit, SNAP))
        self.assertIn("最多 1 个动作", check_actions(answer(("e3", "click", ""), ("e3", "click", "")), submit, SNAP))
        self.assertIn("只允许", check_actions(answer(("e1", "fill", "x")), submit, SNAP))

    def test_select_must_denote_the_steps_value(self) -> None:
        customer = Step("entry.customer", "选择客户", "#customer", "select", "C002", alternatives=("甬港澄远机械配件厂",))
        self.assertIsNone(check_actions(answer(("e2", "select", "甬港澄远机械配件厂")), customer, SNAP))
        self.assertIsNotNone(check_actions(answer(("e2", "select", "k101")), customer, SNAP))

    def test_a_date_may_be_split_over_year_and_month_dropdowns(self) -> None:
        date = Step("entry.order_date", "填写单据日期", "#order_date", "fill", "2026-11-05")
        self.assertIsNone(check_actions(answer(("e4", "select", "2026"), ("e5", "select", "11")), date, SNAP))
        self.assertIsNotNone(check_actions(answer(("e5", "select", "12")), date, SNAP))


class GroundingTest(unittest.TestCase):
    PAGE = "销售单号 采购单号 价税合计\nXS26100009 YG-2610-0398 甬港澄远机械配件厂 2026/10/09 ¥13,558.40 已开票"

    def test_values_on_the_page_pass_in_any_spelling(self) -> None:
        row = OrderRow(order_no="XS26100009", customer_po="YG-2610-0398", customer="甬港澄远机械配件厂",
                       order_date="2026-10-09", amount=13558.4, status="已开票")
        self.assertEqual(grounding_problems(row, self.PAGE), [])

    def test_invented_values_are_caught(self) -> None:
        row = OrderRow(order_no="XS26100010", customer_po="YG-2610-0398", customer="甬港澄远机械配件厂",
                       order_date="2026-10-09", amount=13558.0, status="已开票")
        problems = grounding_problems(row, self.PAGE)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any("XS26100010" in p for p in problems))

    def test_nested_lists_are_checked(self) -> None:
        page = "4.01.0013 标准气缸 SC63 件 现存量 349 可用量 300 启用"
        good = StockItem(sku="4.01.0013", name="标准气缸", spec="SC63", unit="件", on_hand=349, available=300,
                         status="启用")
        result = StockPage(found=True, items=[good, good.model_copy(update={"available": 349.5})])
        self.assertEqual(grounding_problems(result, page), ["items[1].available=349.5 不在页面上"])
        self.assertEqual(grounding_problems(OrderLineOut(sku="1.01.0003", qty=3, unit_price=6.4), "1.01.0003 3 6.40"), [])


if __name__ == "__main__":
    unittest.main()
