"""Layout v3 「hold-out」: an old WebForms-style system, written before any evaluation run and not tuned afterwards.

Differences from v1/v2: every page is one big POST form driven by ``__doPostBack`` links; labels sit in neighbouring
table cells (no <label for>); the customer is typed as a code; dates in the order form are 年/月/日 drop-downs while
search dates are 8-digit ``yyyymmdd`` text; header and lines are on one page followed by a confirm page; grids have
no <thead>, reordered columns, thousands separators and dotted dates; paging is 首页/上页/下页/尾页 plus "转到".
"""
from __future__ import annotations

import re

from .db import Customer, Order, Product, SearchPage
from .entry import Entry, EntryLine, normalise_date
from .views import ConfirmView, FormData, PageCtx, SearchForm, SearchRequest, esc, page_number, qty

CSS = """
body{margin:0;font:12px Tahoma,'宋体',sans-serif;background:#f5f0e1}
#hdr{background:#7a1f1f;color:#fff;padding:8px 12px;font-size:15px}#hdr small{font-size:12px;margin-left:20px}
#wrap{display:flex}#left{width:170px;background:#efe6cc;border-right:1px solid #b8a77a;min-height:520px;padding:6px}
#left td{padding:2px 4px}#left .grp{color:#7a1f1f;font-weight:bold;padding-top:8px}#left a{color:#00c;text-decoration:none}
#body{flex:1;padding:10px}fieldset{border:1px solid #b8a77a;background:#fffdf5;margin-bottom:10px}legend{color:#7a1f1f;font-weight:bold}
td.lbl{background:#efe6cc;text-align:right;padding:2px 6px;white-space:nowrap}td.val{padding:2px 6px}
a.btnlink{display:inline-block;border:1px outset #b8a77a;background:#efe6cc;color:#000;padding:2px 10px;text-decoration:none;margin-right:6px}
table.gv{border-collapse:collapse;background:#fff}table.gv td{border:1px solid #b8a77a;padding:2px 6px}
tr.gvHeader td{background:#7a1f1f;color:#fff;font-weight:bold}tr.gvPager td{background:#efe6cc}
.r{text-align:right}#ftr{color:#666;padding:8px 12px;font-size:11px}
"""

SCRIPT = """<script>function __doPostBack(t,a){var f=document.forms['aspnetForm'];
f.__EVENTTARGET.value=t;f.__EVENTARGUMENT.value=a;f.submit();}</script>"""

P = "ctl00$cph$"  # name prefix of every server control, as WebForms renders it


def _postback(target: str, argument: str = "") -> str:
    return f"javascript:__doPostBack('{P}{target}','{argument}')"


def _link(target: str, text: str, argument: str = "") -> str:
    return f'<a class="btnlink" id="ctl00_cph_{target}" href="{_postback(target, argument)}">{esc(text)}</a>'


def _text(name: str, value: str, size: int = 16, title: str = "") -> str:
    tip = f' title="{esc(title)}"' if title else ""
    return f'<input type="text" name="{P}{name}" id="ctl00_cph_{name}" size="{size}" value="{esc(value)}"{tip}>'


def _form(action: str, inner: str) -> str:
    viewstate = "dDwtMTIzNDU2Nzg5O3Q8O2w8aTwxPjs+O2w8dDw7Oz47Pj47Pg=="
    return (f'<form id="aspnetForm" name="aspnetForm" method="post" action="{action}">'
            '<input type="hidden" name="__EVENTTARGET" value=""><input type="hidden" name="__EVENTARGUMENT" value="">'
            f'<input type="hidden" name="__VIEWSTATE" value="{viewstate}">{inner}</form>')


def _page(ctx: PageCtx, title: str, action: str, inner: str) -> str:
    menu = """<table><tr><td class="grp">【销售管理】</td></tr><tr><td><a href="/orders">销售订单列表</a></td></tr>
<tr><td><a href="/orders/new?reset=1">销售订单录入</a></td></tr><tr><td class="grp">【库存管理】</td></tr>
<tr><td><a href="/stock">现存量查询</a></td></tr><tr><td class="grp">【系统】</td></tr><tr><td><a href="/logout">注销</a></td></tr></table>"""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{esc(title)}</title><style>{CSS}</style>{SCRIPT}</head><body>
<div id="hdr">瑞丰机电批发（虚构）企业管理系统<small>操作员：{esc(ctx.user)}　登录日期：{esc(ctx.system_date)}</small></div>
<div id="wrap"><div id="left">{menu}</div><div id="body">{_form(action, f'<fieldset><legend>{esc(title)}</legend>{inner}</fieldset>')}</div></div>
<div id="ftr">演示数据均为虚构 · 界面 v3（hold-out） · <a href="/admin/settings">演示控制</a></div></body></html>"""


def _errors(errors: list[str]) -> str:
    return f'<span id="ctl00_cph_lblErr" style="color:red">{"<br>".join(esc(e) for e in errors)}</span>' if errors else ""


def _dotted(iso: str | None) -> str:
    return (iso or "").replace("-", ".")


def _date_selects(prefix: str, iso: str, allow_blank: bool) -> str:
    year, month, day = (iso.split("-") + ["", "", ""])[:3] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso) else ("", "", "")

    def select(name: str, values: list[str], current: str, unit: str) -> str:
        options = ['<option value=""></option>'] if allow_blank else []
        options += [f'<option value="{v}"{" selected" if v == current else ""}>{v}</option>' for v in values]
        return f'<select name="{P}{prefix}{name}" id="ctl00_cph_{prefix}{name}">{"".join(options)}</select>{unit}'

    return (select("Y", ["2025", "2026", "2027"], year, "年") + select("M", [f"{m:02d}" for m in range(1, 13)], month, "月")
            + select("D", [f"{d:02d}" for d in range(1, 32)], day, "日"))


def _read_date(form: FormData, prefix: str) -> str:
    parts = [form.get(f"{P}{prefix}{x}", "") for x in "YMD"]
    return normalise_date("-".join(parts)) if all(parts) else ""


class LayoutV3:
    name = "v3"
    label = "hold-out"
    single_page_entry = True

    def login(self, error: str | None) -> str:
        message = f'<p style="color:red">{esc(error)}</p>' if error else ""
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>系统登录</title><style>{CSS}</style>{SCRIPT}</head><body>
<div id="hdr">瑞丰机电批发（虚构）企业管理系统</div><div style="width:360px;margin:80px auto">{_form("/login", f'''
<fieldset><legend>用户登录</legend>{message}<table>
<tr><td class="lbl">操作员代码</td><td class="val">{_text("txtOper", "")}</td></tr>
<tr><td class="lbl">口　令</td><td class="val"><input type="password" name="{P}txtPwd" id="ctl00_cph_txtPwd" size="16"></td></tr>
<tr><td></td><td class="val">{_link("lnkLogin", "登录系统")}</td></tr></table></fieldset>''')}</div>
<div id="ftr">演示数据均为虚构 · 界面 v3（hold-out）</div></body></html>"""

    def home(self, ctx: PageCtx) -> str:
        return _page(ctx, "系统首页", "/", "<p>请从左侧功能菜单进入。</p>")

    def search(self, ctx: PageCtx, request: SearchRequest, customers: list[Customer],
               result: SearchPage | None) -> str:
        f = request.form
        inner = f"""<table>
<tr><td class="lbl">单据号</td><td class="val">{_text("txtBillNo", f.order_no)}</td>
<td class="lbl">客户订单号</td><td class="val">{_text("txtCustPO", f.cust_po)}</td>
<td class="lbl">客户编码</td><td class="val">{_text("txtCustCode", f.customer, 8)}</td></tr>
<tr><td class="lbl">日期从</td><td class="val">{_text("txtDateFrom", f.date_from, 10, "格式：yyyymmdd")}</td>
<td class="lbl">至</td><td class="val">{_text("txtDateTo", f.date_to, 10, "格式：yyyymmdd")}</td>
<td></td><td class="val">{_link("btnQuery", "查询")}</td></tr></table>{_errors(request.errors)}"""
        if result is not None:
            page = result.page
            inner += f'<input type="hidden" name="{P}hidPage" value="{page}">'
            rows = "".join(
                f'<tr><td>{(page - 1) * 10 + i}</td><td>{_dotted(o.order_date)}</td>'
                f'<td><a href="/orders/{esc(o.order_no)}">{esc(o.order_no)}</a></td><td>{esc(o.customer.name)}</td>'
                f'<td>{esc(o.cust_po)}</td><td>{esc(o.status)}</td><td class="r">{o.total:,.2f}</td>'
                f"<td>{_dotted(o.delivery_date)}</td></tr>" for i, o in enumerate(result.rows, 1))
            if not result.rows:
                rows = '<tr><td colspan="8">无记录</td></tr>'

            def nav(arg: str, text: str, enabled: bool) -> str:
                return f'<a href="{_postback("gvOrders", arg)}">{text}</a>' if enabled else f"<span>{text}</span>"

            pager = (f'<tr class="gvPager"><td colspan="8">共{result.total}条　第{page}/{result.pages}页　'
                     f'{nav("Page$First", "首页", page > 1)} {nav("Page$Prev", "上页", page > 1)} '
                     f'{nav("Page$Next", "下页", page < result.pages)} {nav("Page$Last", "尾页", page < result.pages)}　'
                     f'转到{_text("txtGoPage", "", 2)}页 <a href="{_postback("btnGo")}">GO</a></td></tr>')
            inner += ('<table class="gv" id="ctl00_cph_gvOrders" rules="all"><tr class="gvHeader"><td>序号</td><td>单据日期</td>'
                      '<td>单据号</td><td>客户</td><td>客户订单号</td><td>状态</td><td>价税合计</td><td>交货日期</td></tr>'
                      f"{rows}{pager}</table>")
        return _page(ctx, "销售订单列表", "/orders", inner)

    def detail(self, ctx: PageCtx, order: Order) -> str:
        invoice = (f'<a id="ctl00_cph_lnkPrint" href="/orders/{esc(order.order_no)}/invoice.pdf">打印发票</a>' if order.invoice
                   else "发票状态：未开具")
        lines = "".join(
            f"<tr><td>{line.line_no}</td><td>{esc(line.sku)}</td><td>{esc(line.name)}</td><td>{esc(line.spec)}</td>"
            f'<td>{esc(line.unit)}</td><td class="r">{qty(line.qty)}</td><td class="r">{line.unit_price:,.2f}</td>'
            f'<td class="r">{line.amount:,.2f}</td></tr>' for line in order.lines)
        inner = f"""<table>
<tr><td class="lbl">单据号</td><td class="val">{esc(order.order_no)}</td><td class="lbl">单据状态</td><td class="val">{esc(order.status)}</td></tr>
<tr><td class="lbl">客户</td><td class="val">{esc(order.customer.code)} {esc(order.customer.name)}</td><td class="lbl">客户订单号</td><td class="val">{esc(order.cust_po)}</td></tr>
<tr><td class="lbl">单据日期</td><td class="val">{_dotted(order.order_date)}</td><td class="lbl">交货日期</td><td class="val">{_dotted(order.delivery_date)}</td></tr>
<tr><td class="lbl">备注</td><td class="val">{esc(order.remark)}</td><td class="lbl">价税合计</td><td class="val">{order.total:,.2f}</td></tr></table>
<p>{invoice}</p><table class="gv" rules="all"><tr class="gvHeader"><td>序号</td><td>存货编码</td><td>存货名称</td><td>规格型号</td>
<td>主计量</td><td>数量</td><td>含税单价</td><td>价税合计</td></tr>{lines}</table>"""
        return _page(ctx, "销售订单", f"/orders/{order.order_no}", inner)

    def stock(self, ctx: PageCtx, query: str, products: list[Product] | None) -> str:
        inner = (f'<table><tr><td class="lbl">存货编码</td><td class="val">{_text("txtInvCode", query)}</td>'
                 f'<td class="val">{_link("btnQuery", "查询")}</td></tr></table>')
        if products is not None:
            if not products:
                inner += f'<span style="color:red">查无此存货：{esc(query)}</span>'
            else:
                rows = "".join(
                    f"<tr><td>{esc(p.sku)}</td><td>{esc(p.name)}</td><td>{esc(p.spec)}</td><td>{esc(p.unit)}</td>"
                    f'<td class="r">{qty(p.on_hand)}</td><td class="r">{qty(p.available)}</td>'
                    f'<td>{"是" if not p.active else "否"}</td></tr>' for p in products)
                inner += ('<table class="gv" rules="all"><tr class="gvHeader"><td>存货编码</td><td>存货名称</td><td>规格型号</td>'
                          f"<td>主计量</td><td>现存量</td><td>可用量</td><td>是否停用</td></tr>{rows}</table>")
        return _page(ctx, "现存量查询", "/stock", inner)

    def entry(self, ctx: PageCtx, entry: Entry, customers: list[Customer], confirm: ConfirmView | None,
              errors: list[str]) -> str:
        if entry.step != 3:
            rows = "".join(
                f'<tr><td>{i}</td><td><input type="text" name="{P}gvLines$ctl{i:02d}$txtInv" size="12" value="{esc(line.sku)}"></td>'
                f'<td><input type="text" name="{P}gvLines$ctl{i:02d}$txtQty" size="6" value="{esc(line.qty)}"></td>'
                f'<td><input type="text" name="{P}gvLines$ctl{i:02d}$txtPrice" size="8" value="{esc(line.price)}"></td></tr>'
                for i, line in enumerate(entry.lines, 1))
            inner = f"""{_errors(errors)}<table>
<tr><td class="lbl">客户编码</td><td class="val">{_text("txtCustCode", entry.customer, 8)} （输入客户编码）</td>
<td class="lbl">客户订单号</td><td class="val">{_text("txtCustPO", entry.cust_po)}</td></tr>
<tr><td class="lbl">单据日期</td><td class="val">{_date_selects("ddlBill", entry.order_date, False)}</td>
<td class="lbl">交货日期</td><td class="val">{_date_selects("ddlDel", entry.delivery_date, True)}</td></tr>
<tr><td class="lbl">备注</td><td class="val" colspan="3">{_text("txtMemo", entry.remark, 50)}</td></tr></table>
<table class="gv" id="ctl00_cph_gvLines" rules="all"><tr class="gvHeader"><td>序号</td><td>存货编码</td><td>数量</td><td>含税单价</td></tr>{rows}</table>
<p>{_link("lnkAddRow", "增行")}{_link("lnkSave", "保存")}<a class="btnlink" href="/orders/new?reset=1">取消</a></p>"""
        else:
            assert confirm is not None
            lines = "".join(
                f"<tr><td>{line.line_no}</td><td>{esc(line.sku)}</td><td>{esc(line.name)}</td><td>{esc(line.spec)}</td>"
                f'<td class="r">{qty(line.qty)}</td><td class="r">{line.unit_price:,.2f}</td><td class="r">{line.amount:,.2f}</td></tr>'
                for line in confirm.lines)
            inner = f"""<p>请确认以下单据信息：</p><table>
<tr><td class="lbl">客户</td><td class="val">{esc(confirm.customer.code)} {esc(confirm.customer.name)}</td><td class="lbl">客户订单号</td><td class="val">{esc(confirm.cust_po)}</td></tr>
<tr><td class="lbl">单据日期</td><td class="val">{_dotted(confirm.order_date)}</td><td class="lbl">交货日期</td><td class="val">{_dotted(confirm.delivery_date)}</td></tr>
<tr><td class="lbl">备注</td><td class="val" colspan="3">{esc(confirm.remark)}</td></tr></table>
<table class="gv" rules="all"><tr class="gvHeader"><td>序号</td><td>存货编码</td><td>存货名称</td><td>规格型号</td><td>数量</td>
<td>含税单价</td><td>价税合计</td></tr>{lines}</table><p>价税合计：{confirm.total:,.2f}</p>
<p><input type="button" value="确 定" onclick="__doPostBack('{P}btnOK','')">
<input type="button" value="返回修改" onclick="__doPostBack('{P}btnBack','')"></p>"""
        return _page(ctx, "销售订单录入", "/orders/new", inner)

    def saved(self, ctx: PageCtx, order_no: str) -> str:
        inner = (f'<span id="ctl00_cph_lblMsg" style="color:blue">单据 {esc(order_no)} 保存成功。</span>'
                 f'<p><a href="/orders/{esc(order_no)}">查看</a></p>')
        return _page(ctx, "销售订单录入", "/orders/new", inner)

    def message(self, ctx: PageCtx, text: str) -> str:
        return _page(ctx, "提示", "/", f'<span style="color:red">{esc(text)}</span>')

    def parse_login(self, form: FormData) -> tuple[str, str]:
        return form.get(f"{P}txtOper", ""), form.get(f"{P}txtPwd", "")

    def parse_search(self, params: FormData, customers: list[Customer]) -> SearchRequest:
        form = SearchForm(params.get(f"{P}txtBillNo", "").strip(), params.get(f"{P}txtCustPO", "").strip(),
                          params.get(f"{P}txtCustCode", "").strip(), params.get(f"{P}txtDateFrom", "").strip(),
                          params.get(f"{P}txtDateTo", "").strip())
        target, argument = params.get("__EVENTTARGET", ""), params.get("__EVENTARGUMENT", "")
        current = page_number(params.get(f"{P}hidPage", ""))
        if target == f"{P}gvOrders":
            page = {"Page$First": 1, "Page$Prev": current - 1, "Page$Next": current + 1,
                    "Page$Last": 10_000}.get(argument, current)
        elif target == f"{P}btnGo":
            page = page_number(params.get(f"{P}txtGoPage", ""))
        else:
            page = 1
        errors = [f"日期格式错误，应为 yyyymmdd：{value}" for value in (form.date_from, form.date_to)
                  if value and not re.fullmatch(r"\d{8}", value)]
        searched = target in (f"{P}btnQuery", f"{P}gvOrders", f"{P}btnGo")
        return SearchRequest(form, max(page, 1), searched=searched, errors=errors)

    def parse_stock(self, params: FormData) -> str | None:
        if params.get("__EVENTTARGET") != f"{P}btnQuery":
            return None
        return params.get(f"{P}txtInvCode", "").strip()

    def read_entry(self, form: FormData, entry: Entry, customers: list[Customer]) -> str:
        if entry.step != 3:
            entry.customer = form.get(f"{P}txtCustCode", "").strip().upper()
            entry.cust_po = form.get(f"{P}txtCustPO", "")
            entry.order_date = _read_date(form, "ddlBill")
            entry.delivery_date = _read_date(form, "ddlDel")
            entry.remark = form.get(f"{P}txtMemo", "")
            entry.lines = [EntryLine(form.get(f"{P}gvLines$ctl{i:02d}$txtInv", "").strip(),
                                     form.get(f"{P}gvLines$ctl{i:02d}$txtQty", "").strip(),
                                     form.get(f"{P}gvLines$ctl{i:02d}$txtPrice", "").strip())
                           for i in range(1, len(entry.lines) + 1)]
        return {f"{P}lnkAddRow": "add_line", f"{P}lnkSave": "save", f"{P}btnOK": "submit",
                f"{P}btnBack": "prev"}.get(form.get("__EVENTTARGET", ""), "")
