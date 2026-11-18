"""Layout v1 「经典版」: top menu, table forms with ids, a three-step order wizard. The scripted flows target this."""
from __future__ import annotations

from urllib.parse import urlencode

from .db import Customer, Order, Product, SearchPage
from .entry import Entry, EntryLine, normalise_date
from .views import ConfirmView, FormData, PageCtx, SearchForm, SearchRequest, esc, page_number, qty

CSS = """
body{font:12px SimSun,'宋体',serif;margin:0;background:#d4d0c8}
#banner{background:linear-gradient(#0a246a,#3a6ea5);color:#fff;padding:6px 10px}
#banner span{margin-left:30px}#banner a{color:#ff0;margin-left:20px}
#menu{background:#ece9d8;border-bottom:1px solid #808080;width:100%}#menu td{padding:4px 12px}
#menu a{color:#000080;text-decoration:none}#menu a:hover{text-decoration:underline}
#main{background:#fff;margin:8px;padding:10px;border:1px solid #808080;min-height:300px}
h2{font-size:14px;color:#000080;border-bottom:1px solid #000080;margin-top:0}
table.form td{padding:3px 6px}table.form td.lbl{text-align:right;background:#ece9d8}
table.grid{border-collapse:collapse;margin-top:8px}table.grid th{background:#3a6ea5;color:#fff;padding:3px 8px}
table.grid td{border:1px solid #a0a0a0;padding:2px 8px}table.grid td.num{text-align:right}
ul.errors{color:#c00;border:1px solid #c00;background:#fff0f0;padding:6px 20px}
.msg-error{color:#c00}.disabled{color:#999}#pager{margin-top:6px}
#steps{color:#666;margin-bottom:8px}#steps b{color:#000080}
#savedMsg{color:#006400;font-size:14px}
#footer{text-align:center;color:#444;font-size:11px;padding:6px}
"""


def _page(ctx: PageCtx, title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{esc(title)} - 瑞丰机电批发 销售管理系统</title>
<style>{CSS}</style></head><body>
<div id="banner"><b>瑞丰机电批发（虚构）· 销售管理系统 V3.2</b><span>操作员：{esc(ctx.user)}</span>
<span>系统日期：{esc(ctx.system_date)}</span><a id="lnkLogout" href="/logout">退出</a></div>
<table id="menu"><tr><td><a id="menu-home" href="/">首页</a></td><td><a id="menu-order-search" href="/orders">销售订单查询</a></td>
<td><a id="menu-order-new" href="/orders/new?reset=1">新建销售订单</a></td><td><a id="menu-stock" href="/stock">库存查询</a></td></tr></table>
<div id="main"><h2>{esc(title)}</h2>{body}</div>
<div id="footer">演示数据均为虚构 · 界面版本 v1（经典版） · <a href="/admin/settings">演示控制</a></div></body></html>"""


def _errors(errors: list[str]) -> str:
    if not errors:
        return ""
    return '<ul class="errors">' + "".join(f"<li>{esc(e)}</li>" for e in errors) + "</ul>"


def _customer_options(customers: list[Customer], selected: str) -> str:
    options = ['<option value="">--请选择--</option>']
    options += [f'<option value="{esc(c.code)}"{" selected" if c.code == selected else ""}>{esc(c.code)} - {esc(c.name)}'
                f'</option>' for c in customers]
    return "".join(options)


class LayoutV1:
    name = "v1"
    label = "经典版"
    single_page_entry = False

    def login(self, error: str | None) -> str:
        message = f'<p class="msg-error">{esc(error)}</p>' if error else ""
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>系统登录 - 瑞丰机电批发 销售管理系统</title>
<style>{CSS}</style></head><body><div id="banner"><b>瑞丰机电批发（虚构）· 销售管理系统 V3.2</b></div>
<div id="main" style="width:320px;margin:60px auto"><h2>系统登录</h2>{message}
<form method="post" action="/login"><table class="form">
<tr><td class="lbl"><label for="username">用户名</label></td><td><input id="username" name="username"></td></tr>
<tr><td class="lbl"><label for="password">密码</label></td><td><input id="password" name="password" type="password"></td></tr>
</table><p><input id="btnLogin" type="submit" value="登 录"></p></form></div>
<div id="footer">演示数据均为虚构 · 界面版本 v1（经典版）</div></body></html>"""

    def home(self, ctx: PageCtx) -> str:
        return _page(ctx, "首页", "<p>欢迎使用销售管理系统。请从上方菜单选择功能。</p>")

    def search(self, ctx: PageCtx, request: SearchRequest, customers: list[Customer],
               result: SearchPage | None) -> str:
        f = request.form
        form = f"""<form method="get" action="/orders"><table class="form"><tr>
<td class="lbl"><label for="q_order_no">单据编号</label></td><td><input id="q_order_no" name="order_no" value="{esc(f.order_no)}"></td>
<td class="lbl"><label for="q_cust_po">客户单号</label></td><td><input id="q_cust_po" name="cust_po" value="{esc(f.cust_po)}"></td>
<td class="lbl"><label for="q_customer">客户</label></td><td><select id="q_customer" name="customer">{_customer_options(customers, f.customer)}</select></td></tr><tr>
<td class="lbl"><label for="q_date_from">单据日期从</label></td><td><input id="q_date_from" name="date_from" type="date" value="{esc(f.date_from)}"></td>
<td class="lbl"><label for="q_date_to">至</label></td><td><input id="q_date_to" name="date_to" type="date" value="{esc(f.date_to)}"></td>
<td></td><td><button id="btnSearch" type="submit" name="search" value="1">查 询</button></td></tr></table></form>"""
        body = form + _errors(request.errors)
        if result is not None:
            rows = "".join(
                f'<tr><td><a class="order-link" href="/orders/{esc(o.order_no)}">{esc(o.order_no)}</a></td>'
                f"<td>{esc(o.cust_po)}</td><td>{esc(o.customer.code)} {esc(o.customer.name)}</td><td>{esc(o.order_date)}</td>"
                f'<td>{esc(o.delivery_date or "")}</td><td class="num">{o.total:.2f}</td><td>{esc(o.status)}</td></tr>'
                for o in result.rows) or '<tr><td colspan="7" class="empty">没有符合条件的记录</td></tr>'
            body += ('<table id="orderTable" class="grid"><thead><tr><th>单据编号</th><th>客户单号</th><th>客户</th>'
                     '<th>单据日期</th><th>交货日期</th><th>金额</th><th>状态</th></tr></thead>'
                     f"<tbody>{rows}</tbody></table>{self._pager(f, result)}")
        return _page(ctx, "销售订单查询", body)

    @staticmethod
    def _pager(f: SearchForm, result: SearchPage) -> str:
        def link(page: int) -> str:
            query = {"order_no": f.order_no, "cust_po": f.cust_po, "customer": f.customer, "date_from": f.date_from,
                     "date_to": f.date_to, "search": "1", "page": str(page)}
            return "/orders?" + urlencode(query)

        prev_link = (f'<a id="prevPage" href="{esc(link(result.page - 1))}">上一页</a>' if result.page > 1
                     else '<span class="disabled">上一页</span>')
        next_link = (f'<a id="nextPage" href="{esc(link(result.page + 1))}">下一页</a>' if result.page < result.pages
                     else '<span class="disabled">下一页</span>')
        return (f'<div id="pager">共 {result.total} 条记录，第 {result.page}/{result.pages} 页　'
                f"{prev_link}　{next_link}</div>")

    def detail(self, ctx: PageCtx, order: Order) -> str:
        invoice = (f'<a id="lnkInvoice" href="/orders/{esc(order.order_no)}/invoice.pdf">下载发票（{esc(order.invoice.invoice_no)}）</a>'
                   if order.invoice else '<span id="noInvoice">未开票</span>')
        lines = "".join(
            f"<tr><td>{line.line_no}</td><td>{esc(line.sku)}</td><td>{esc(line.name)}</td><td>{esc(line.spec)}</td>"
            f'<td>{esc(line.unit)}</td><td class="num">{qty(line.qty)}</td><td class="num">{line.unit_price:.2f}</td>'
            f'<td class="num">{line.amount:.2f}</td></tr>' for line in order.lines)
        body = f"""<table id="orderHead" class="form">
<tr><td class="lbl">单据编号</td><td id="d_order_no">{esc(order.order_no)}</td><td class="lbl">状态</td><td id="d_status">{esc(order.status)}</td></tr>
<tr><td class="lbl">客户</td><td id="d_customer">{esc(order.customer.code)} {esc(order.customer.name)}</td><td class="lbl">客户单号</td><td id="d_cust_po">{esc(order.cust_po)}</td></tr>
<tr><td class="lbl">单据日期</td><td id="d_order_date">{esc(order.order_date)}</td><td class="lbl">交货日期</td><td id="d_delivery_date">{esc(order.delivery_date or "")}</td></tr>
<tr><td class="lbl">备注</td><td id="d_remark" colspan="3">{esc(order.remark)}</td></tr>
<tr><td class="lbl">金额合计</td><td id="d_total">{order.total:.2f}</td><td class="lbl">发票</td><td>{invoice}</td></tr></table>
<table id="orderLines" class="grid"><thead><tr><th>行号</th><th>物料编码</th><th>名称</th><th>规格</th><th>单位</th><th>数量</th>
<th>单价</th><th>金额</th></tr></thead><tbody>{lines}</tbody></table>
<p><a href="javascript:history.back()">返回</a></p>"""
        return _page(ctx, "销售订单明细", body)

    def stock(self, ctx: PageCtx, query: str, products: list[Product] | None) -> str:
        body = f"""<form method="get" action="/stock"><table class="form"><tr>
<td class="lbl"><label for="stock_sku">物料编码</label></td><td><input id="stock_sku" name="sku" value="{esc(query)}"></td>
<td><button id="btnStock" type="submit">查 询</button></td></tr></table></form>"""
        if products is not None:
            if not products:
                body += f'<p class="msg-error">未找到物料：{esc(query)}</p>'
            else:
                rows = "".join(
                    f"<tr><td>{esc(p.sku)}</td><td>{esc(p.name)}</td><td>{esc(p.spec)}</td><td>{esc(p.unit)}</td>"
                    f'<td class="num">{qty(p.on_hand)}</td><td class="num">{qty(p.available)}</td>'
                    f'<td>{"正常" if p.active else "停用"}</td></tr>' for p in products)
                body += ('<table id="stockTable" class="grid"><thead><tr><th>物料编码</th><th>名称</th><th>规格</th>'
                         '<th>单位</th><th>库存数量</th><th>可用数量</th><th>状态</th></tr></thead>'
                         f"<tbody>{rows}</tbody></table>")
        return _page(ctx, "库存查询", body)

    def entry(self, ctx: PageCtx, entry: Entry, customers: list[Customer], confirm: ConfirmView | None,
              errors: list[str]) -> str:
        labels = ["第 1 步：订单抬头", "第 2 步：订单明细", "第 3 步：确认提交"]
        steps = " → ".join(f"<b>{s}</b>" if i == entry.step else s for i, s in enumerate(labels, 1))
        if entry.step == 1:
            inner = f"""<table class="form">
<tr><td class="lbl"><label for="customer">客户</label></td><td><select id="customer" name="customer">{_customer_options(customers, entry.customer)}</select></td></tr>
<tr><td class="lbl"><label for="cust_po">客户单号</label></td><td><input id="cust_po" name="cust_po" value="{esc(entry.cust_po)}"></td></tr>
<tr><td class="lbl"><label for="order_date">单据日期</label></td><td><input id="order_date" name="order_date" type="date" value="{esc(entry.order_date)}"></td></tr>
<tr><td class="lbl"><label for="delivery_date">交货日期</label></td><td><input id="delivery_date" name="delivery_date" type="date" value="{esc(entry.delivery_date)}"></td></tr>
<tr><td class="lbl"><label for="remark">备注</label></td><td><textarea id="remark" name="remark" rows="2" cols="40">{esc(entry.remark)}</textarea></td></tr>
</table><p><button id="btnNext1" type="submit" name="action" value="next1">下一步</button></p>"""
        elif entry.step == 2:
            rows = "".join(
                f'<tr><td>{i}</td><td><input id="sku_{i}" name="sku_{i}" value="{esc(line.sku)}"></td>'
                f'<td><input id="qty_{i}" name="qty_{i}" size="8" value="{esc(line.qty)}"></td>'
                f'<td><input id="price_{i}" name="price_{i}" size="10" value="{esc(line.price)}"></td></tr>'
                for i, line in enumerate(entry.lines, 1))
            inner = f"""<table id="lineTable" class="grid"><thead><tr><th>行号</th><th>物料编码</th><th>数量</th><th>单价</th></tr></thead>
<tbody>{rows}</tbody></table><p>单价为空时按目录价。　<button id="btnAddLine" type="submit" name="action" value="add_line">增加行</button></p>
<p><button id="btnPrev2" type="submit" name="action" value="prev">上一步</button>
<button id="btnNext2" type="submit" name="action" value="next2">下一步</button></p>"""
        else:
            assert confirm is not None
            lines = "".join(
                f"<tr><td>{line.line_no}</td><td>{esc(line.sku)}</td><td>{esc(line.name)}</td><td>{esc(line.spec)}</td>"
                f'<td>{esc(line.unit)}</td><td class="num">{qty(line.qty)}</td><td class="num">{line.unit_price:.2f}</td>'
                f'<td class="num">{line.amount:.2f}</td></tr>' for line in confirm.lines)
            inner = f"""<table id="confirmHead" class="form">
<tr><td class="lbl">客户</td><td>{esc(confirm.customer.code)} {esc(confirm.customer.name)}</td><td class="lbl">客户单号</td><td>{esc(confirm.cust_po)}</td></tr>
<tr><td class="lbl">单据日期</td><td>{esc(confirm.order_date)}</td><td class="lbl">交货日期</td><td>{esc(confirm.delivery_date or "")}</td></tr>
<tr><td class="lbl">备注</td><td colspan="3">{esc(confirm.remark)}</td></tr></table>
<table id="confirmLines" class="grid"><thead><tr><th>行号</th><th>物料编码</th><th>名称</th><th>规格</th><th>单位</th><th>数量</th>
<th>单价</th><th>金额</th></tr></thead><tbody>{lines}</tbody></table>
<p>合计金额：<b id="confirmTotal">{confirm.total:.2f}</b></p>
<p><button id="btnPrev3" type="submit" name="action" value="prev">上一步</button>
<button id="btnSubmit" type="submit" name="action" value="submit">提 交</button></p>"""
        body = f'<div id="steps">{steps}</div>{_errors(errors)}<form method="post" action="/orders/new">{inner}</form>'
        return _page(ctx, "新建销售订单", body)

    def saved(self, ctx: PageCtx, order_no: str) -> str:
        body = (f'<p id="savedMsg">保存成功！单据编号：<b>{esc(order_no)}</b>（草稿）</p>'
                f'<p><a href="/orders/{esc(order_no)}">查看单据</a>　<a href="/orders/new?reset=1">继续新建</a></p>')
        return _page(ctx, "新建销售订单", body)

    def message(self, ctx: PageCtx, text: str) -> str:
        return _page(ctx, "提示", f'<p class="msg-error">{esc(text)}</p>')

    def parse_login(self, form: FormData) -> tuple[str, str]:
        return form.get("username", ""), form.get("password", "")

    def parse_search(self, params: FormData, customers: list[Customer]) -> SearchRequest:
        form = SearchForm(params.get("order_no", "").strip(), params.get("cust_po", "").strip(),
                          params.get("customer", ""), params.get("date_from", ""), params.get("date_to", ""))
        return SearchRequest(form, page_number(params.get("page", "")), searched=params.get("search") == "1")

    def parse_stock(self, params: FormData) -> str | None:
        return params["sku"].strip() if "sku" in params else None

    def read_entry(self, form: FormData, entry: Entry, customers: list[Customer]) -> str:
        if entry.step == 1:
            entry.customer = form.get("customer", "")
            entry.cust_po = form.get("cust_po", "")
            entry.order_date = normalise_date(form.get("order_date", ""))
            entry.delivery_date = normalise_date(form.get("delivery_date", ""))
            entry.remark = form.get("remark", "")
        elif entry.step == 2:
            entry.lines = [EntryLine(form.get(f"sku_{i}", "").strip(), form.get(f"qty_{i}", "").strip(),
                                     form.get(f"price_{i}", "").strip()) for i in range(1, len(entry.lines) + 1)]
        return form.get("action", "")
