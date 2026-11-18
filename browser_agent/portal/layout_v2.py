"""Layout v2 「新版界面」: sidebar menu, renamed labels, div grids, buttons moved to a top toolbar, new field names.

Same data and rules as v1; everything a v1 selector relies on (ids, names, table structure, wording) changes.
"""
from __future__ import annotations

from urllib.parse import urlencode

from .db import Customer, Order, Product, SearchPage
from .entry import Entry, EntryLine, normalise_date
from .views import ConfirmView, FormData, PageCtx, SearchForm, SearchRequest, esc, page_number, qty

CSS = """
*{box-sizing:border-box}body{margin:0;font:14px 'Microsoft YaHei',sans-serif;background:#f3f5f8;color:#222}
.app{display:flex;min-height:100vh}.side{width:190px;background:#1f2d3d;color:#cfd8e3}
.side .logo{padding:18px;font-weight:bold;color:#fff;font-size:16px}.side nav a{display:block;padding:10px 18px;color:#cfd8e3;text-decoration:none}
.side nav a:hover{background:#2b3d52}main{flex:1;display:flex;flex-direction:column}
.top{display:flex;justify-content:space-between;background:#fff;padding:12px 20px;border-bottom:1px solid #e3e6ea}
.content{padding:20px;flex:1}footer{padding:10px 20px;color:#888;font-size:12px}
.card{background:#fff;border-radius:6px;padding:16px;margin-bottom:16px;box-shadow:0 1px 2px rgba(0,0,0,.06)}
.toolbar{display:flex;justify-content:flex-end;gap:8px;margin-bottom:12px}
.btn{border:1px solid #c8cdd3;background:#fff;border-radius:4px;padding:6px 14px;cursor:pointer;font-size:14px;text-decoration:none;color:#222}
.btn-primary{background:#1677ff;border-color:#1677ff;color:#fff}
.fields{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.field label{display:block;color:#666;font-size:12px;margin-bottom:4px}
.field input,.field select,.field textarea{width:100%;padding:6px;border:1px solid #c8cdd3;border-radius:4px}
.grid{margin-top:12px}.grid-head,.grid-row{display:grid;grid-template-columns:1.1fr 1.2fr 2fr 1fr 1fr 1fr .8fr;gap:8px;padding:8px}
.grid-head{background:#fafbfc;color:#666;font-size:12px}.grid-row{border-top:1px solid #eef0f3}
.pager{margin-top:10px;display:flex;gap:6px;align-items:center}.page-link{padding:2px 8px;border:1px solid #c8cdd3;border-radius:3px;text-decoration:none;color:#222}
.page-link.active{background:#1677ff;color:#fff;border-color:#1677ff}
.alert{padding:10px 14px;border-radius:4px;margin-bottom:12px}.alert-error{background:#fff1f0;border:1px solid #ffa39e;color:#cf1322}
.alert-warn{background:#fffbe6;border:1px solid #ffe58f}.alert-success{background:#f6ffed;border:1px solid #b7eb8f;color:#237804}
dl.kv{display:grid;grid-template-columns:120px 1fr 120px 1fr;gap:6px 12px}dl.kv dt{color:#666}dl.kv dd{margin:0}
table.lines{width:100%;border-collapse:collapse;margin-top:12px}table.lines th,table.lines td{padding:6px;border-bottom:1px solid #eef0f3;text-align:left}
.tabs{display:flex;gap:20px;margin-bottom:14px;color:#999}.tabs .on{color:#1677ff;font-weight:bold}
.line-row{display:flex;gap:10px;align-items:center;margin-bottom:8px}.line-row .no{width:60px;color:#666}
.line-row input{padding:6px;border:1px solid #c8cdd3;border-radius:4px}
.stock-card{background:#fff;border:1px solid #e3e6ea;border-radius:6px;padding:12px;margin-top:12px;width:360px}
.login-card{width:340px;margin:100px auto;background:#fff;padding:30px;border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.1)}
.login-card input{width:100%;padding:8px;margin-bottom:12px;border:1px solid #c8cdd3;border-radius:4px}
"""


def _page(ctx: PageCtx, title: str, body: str) -> str:
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>{esc(title)} · 瑞丰 ERP</title><style>{CSS}</style></head>
<body><div class="app"><aside class="side"><div class="logo">瑞丰 ERP（虚构）</div><nav>
<a href="/">工作台</a><a href="/orders">订单查询</a><a href="/orders/new?reset=1">录入销售单</a><a href="/stock">存货查询</a></nav></aside>
<main><header class="top"><span class="crumb">{esc(title)}</span><span class="user">{esc(ctx.user)} · 业务日期 {esc(ctx.system_date)} ·
<a href="/logout">注销</a></span></header><section class="content">{body}</section>
<footer>演示数据均为虚构 · 新版界面 v2 · <a href="/admin/settings">演示控制</a></footer></main></div></body></html>"""


def _alert(errors: list[str]) -> str:
    return f'<div class="alert alert-error">{"<br>".join(esc(e) for e in errors)}</div>' if errors else ""


def _cid(customers: list[Customer], code: str) -> str:
    """v2 identifies customers by an internal key instead of the customer code."""
    return next((f"k{101 + i}" for i, c in enumerate(customers) if c.code == code), "")


def _code(customers: list[Customer], cid: str) -> str:
    return next((c.code for i, c in enumerate(customers) if f"k{101 + i}" == cid), "")


def _customer_select(name: str, customers: list[Customer], selected_code: str) -> str:
    options = ['<option value="">请选择客户</option>'] + [
        f'<option value="k{101 + i}"{" selected" if c.code == selected_code else ""}>{esc(c.name)}（{esc(c.code)}）</option>'
        for i, c in enumerate(customers)]
    return f'<select name="{name}" id="sel-{name}">{"".join(options)}</select>'


def _date(iso: str | None) -> str:
    return (iso or "").replace("-", "/")


def _money(value: float) -> str:
    return f"¥{value:,.2f}"


class LayoutV2:
    name = "v2"
    label = "新版界面"
    single_page_entry = False

    def login(self, error: str | None) -> str:
        message = f'<div class="alert alert-error">{esc(error)}</div>' if error else ""
        return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>登录 · 瑞丰 ERP</title><style>{CSS}</style></head><body>
<div class="login-card"><h1>瑞丰 ERP 云平台</h1>{message}<form method="post" action="/login">
<input name="uid" id="f-uid" placeholder="工号"><input name="pwd" id="f-pwd" type="password" placeholder="登录口令">
<button class="btn btn-primary" type="submit">进入系统</button></form><p style="color:#999;font-size:12px">演示数据均为虚构 · 新版界面 v2</p></div>
</body></html>"""

    def home(self, ctx: PageCtx) -> str:
        return _page(ctx, "工作台", '<div class="card">欢迎回来。左侧菜单进入各业务模块。</div>')

    def search(self, ctx: PageCtx, request: SearchRequest, customers: list[Customer],
               result: SearchPage | None) -> str:
        f = request.form
        form = f"""<form method="get" action="/orders" class="card"><div class="toolbar">
<a class="btn" href="/orders">重置</a><button class="btn btn-primary" type="submit" name="go" value="1">检索</button></div>
<div class="fields">
<div class="field"><label for="f-no">销售单号</label><input id="f-no" name="no" value="{esc(f.order_no)}"></div>
<div class="field"><label for="f-po">采购单号</label><input id="f-po" name="po" value="{esc(f.cust_po)}"></div>
<div class="field"><label for="sel-cid">客户名称</label>{_customer_select("cid", customers, f.customer)}</div>
<div class="field"><label for="f-d1">下单日期 起</label><input id="f-d1" name="d1" class="datepicker" placeholder="年-月-日" value="{esc(f.date_from)}"></div>
<div class="field"><label for="f-d2">下单日期 止</label><input id="f-d2" name="d2" class="datepicker" placeholder="年-月-日" value="{esc(f.date_to)}"></div>
</div></form>"""
        body = form + _alert(request.errors)
        if result is not None:
            rows = "".join(
                f'<div class="grid-row"><span><a href="/orders/{esc(o.order_no)}">{esc(o.order_no)}</a></span>'
                f"<span>{esc(o.cust_po)}</span><span>{esc(o.customer.name)}</span><span>{_date(o.order_date)}</span>"
                f"<span>{_date(o.delivery_date)}</span><span>{_money(o.total)}</span><span>{esc(o.status)}</span></div>"
                for o in result.rows) or '<div class="grid-row"><span>暂无数据</span></div>'
            body += ('<div class="card grid"><div class="grid-head"><span>销售单号</span><span>采购单号</span>'
                     '<span>客户名称</span><span>下单日期</span><span>要求到货日</span><span>价税合计</span>'
                     f"<span>单据状态</span></div>{rows}{self._pager(f, customers, result)}</div>")
        return _page(ctx, "订单查询", body)

    @staticmethod
    def _pager(f: SearchForm, customers: list[Customer], result: SearchPage) -> str:
        def link(page: int) -> str:
            query = {"no": f.order_no, "po": f.cust_po, "cid": _cid(customers, f.customer), "d1": f.date_from,
                     "d2": f.date_to, "go": "1", "p": str(page)}
            return esc("/orders?" + urlencode(query))

        parts = [f'<span class="page-link active">{p}</span>' if p == result.page
                 else f'<a class="page-link" href="{link(p)}">{p}</a>' for p in range(1, result.pages + 1)]
        if result.page < result.pages:
            parts.append(f'<a class="page-link" href="{link(result.page + 1)}">›</a>')
        return f'<div class="pager"><span>共 {result.total} 条</span>{"".join(parts)}</div>'

    def detail(self, ctx: PageCtx, order: Order) -> str:
        invoice = (f'<a class="btn" href="/orders/{esc(order.order_no)}/invoice.pdf">电子发票 PDF</a>' if order.invoice
                   else '<span class="tag">尚未开票</span>')
        lines = "".join(
            f"<tr><td>{esc(line.sku)}</td><td>{esc(line.name)}</td><td>{esc(line.spec)}</td><td>{esc(line.unit)}</td>"
            f"<td>{qty(line.qty)}</td><td>{_money(line.unit_price)}</td><td>{_money(line.amount)}</td></tr>"
            for line in order.lines)
        body = f"""<div class="card"><div class="toolbar">{invoice}<a class="btn" href="/orders">返回列表</a></div><dl class="kv">
<dt>销售单号</dt><dd>{esc(order.order_no)}</dd><dt>单据状态</dt><dd>{esc(order.status)}</dd>
<dt>客户名称</dt><dd>{esc(order.customer.name)}</dd><dt>采购单号</dt><dd>{esc(order.cust_po)}</dd>
<dt>下单日期</dt><dd>{_date(order.order_date)}</dd><dt>要求到货日</dt><dd>{_date(order.delivery_date)}</dd>
<dt>价税合计</dt><dd>{_money(order.total)}</dd><dt>备注</dt><dd>{esc(order.remark)}</dd></dl>
<table class="lines"><thead><tr><th>存货编码</th><th>存货名称</th><th>规格型号</th><th>计量单位</th><th>订货数量</th><th>含税单价</th>
<th>价税合计</th></tr></thead><tbody>{lines}</tbody></table></div>"""
        return _page(ctx, f"销售单 {order.order_no}", body)

    def stock(self, ctx: PageCtx, query: str, products: list[Product] | None) -> str:
        body = f"""<form method="get" action="/stock" class="card"><div class="fields">
<div class="field"><label for="f-kw">存货编码/名称</label><input id="f-kw" name="kw" value="{esc(query)}"></div></div>
<div class="toolbar"><button class="btn btn-primary" type="submit">查询库存</button></div></form>"""
        if products is not None:
            if not products:
                body += '<div class="alert alert-warn">没有找到相关存货</div>'
            for p in products or []:
                body += (f'<div class="stock-card"><h3>{esc(p.name)} {esc(p.spec)}</h3><p>存货编码：{esc(p.sku)}</p>'
                         f"<p>计量单位：{esc(p.unit)}</p><p>现存量：<b>{qty(p.on_hand)}</b></p>"
                         f'<p>可用量：<b>{qty(p.available)}</b></p><p>状态：{"启用" if p.active else "停用"}</p></div>')
        return _page(ctx, "存货查询", body)

    def entry(self, ctx: PageCtx, entry: Entry, customers: list[Customer], confirm: ConfirmView | None,
              errors: list[str]) -> str:
        tabs = "".join(f'<span class="{"on" if i == entry.step else ""}">{s}</span>'
                       for i, s in enumerate(["① 基本信息", "② 商品明细", "③ 核对提交"], 1))
        if entry.step == 1:
            toolbar = '<button class="btn btn-primary" type="submit" name="op" value="next1">继续 →</button>'
            inner = f"""<div class="fields">
<div class="field"><label for="sel-cid">客户名称</label>{_customer_select("cid", customers, entry.customer)}</div>
<div class="field"><label for="f-po">采购单号</label><input id="f-po" name="po" value="{esc(entry.cust_po)}"></div>
<div class="field"><label for="f-dorder">下单日期</label><input id="f-dorder" name="d_order" class="datepicker" placeholder="年-月-日" value="{esc(entry.order_date)}"></div>
<div class="field"><label for="f-dneed">要求到货日</label><input id="f-dneed" name="d_need" class="datepicker" placeholder="年-月-日" value="{esc(entry.delivery_date)}"></div>
<div class="field"><label for="f-memo">备注</label><textarea id="f-memo" name="memo" rows="2">{esc(entry.remark)}</textarea></div></div>"""
        elif entry.step == 2:
            toolbar = ('<button class="btn" type="submit" name="op" value="prev">← 返回</button>'
                       '<button class="btn" type="submit" name="op" value="add_line">+ 新增一行</button>'
                       '<button class="btn btn-primary" type="submit" name="op" value="next2">继续 →</button>')
            inner = "".join(
                f'<div class="line-row"><span class="no">第 {i} 行</span>'
                f'<input name="ln{i}_code" placeholder="存货编码" value="{esc(line.sku)}">'
                f'<input name="ln{i}_qty" placeholder="订货数量" value="{esc(line.qty)}">'
                f'<input name="ln{i}_price" placeholder="含税单价（空=目录价）" value="{esc(line.price)}"></div>'
                for i, line in enumerate(entry.lines, 1))
        else:
            assert confirm is not None
            toolbar = ('<button class="btn" type="submit" name="op" value="prev">← 返回修改</button>'
                       '<button class="btn btn-primary" type="submit" name="op" value="submit">确认保存</button>')
            lines = "".join(
                f"<tr><td>{esc(line.sku)}</td><td>{esc(line.name)} {esc(line.spec)}</td><td>{esc(line.unit)}</td>"
                f"<td>{qty(line.qty)}</td><td>{_money(line.unit_price)}</td><td>{_money(line.amount)}</td></tr>"
                for line in confirm.lines)
            inner = f"""<dl class="kv"><dt>客户名称</dt><dd>{esc(confirm.customer.name)}</dd><dt>采购单号</dt><dd>{esc(confirm.cust_po)}</dd>
<dt>下单日期</dt><dd>{_date(confirm.order_date)}</dd><dt>要求到货日</dt><dd>{_date(confirm.delivery_date)}</dd>
<dt>备注</dt><dd>{esc(confirm.remark)}</dd></dl><table class="lines"><thead><tr><th>存货编码</th><th>存货</th><th>计量单位</th>
<th>订货数量</th><th>含税单价</th><th>价税合计</th></tr></thead><tbody>{lines}</tbody></table>
<p>价税合计：<b>{_money(confirm.total)}</b></p>"""
        body = (f'<div class="card"><div class="tabs">{tabs}</div>{_alert(errors)}<form method="post" action="/orders/new">'
                f'<div class="toolbar">{toolbar}</div>{inner}</form></div>')
        return _page(ctx, "录入销售单", body)

    def saved(self, ctx: PageCtx, order_no: str) -> str:
        body = (f'<div class="alert alert-success">销售单 <b>{esc(order_no)}</b> 已保存（草稿）</div>'
                f'<a class="btn" href="/orders/{esc(order_no)}">打开单据</a>')
        return _page(ctx, "录入销售单", body)

    def message(self, ctx: PageCtx, text: str) -> str:
        return _page(ctx, "提示", f'<div class="alert alert-warn">{esc(text)}</div>')

    def parse_login(self, form: FormData) -> tuple[str, str]:
        return form.get("uid", ""), form.get("pwd", "")

    def parse_search(self, params: FormData, customers: list[Customer]) -> SearchRequest:
        return SearchRequest(SearchForm(params.get("no", "").strip(), params.get("po", "").strip(),
                                        _code(customers, params.get("cid", "")), params.get("d1", "").strip(),
                                        params.get("d2", "").strip()),
                             page_number(params.get("p", "")), searched=params.get("go") == "1")

    def parse_stock(self, params: FormData) -> str | None:
        return params["kw"].strip() if "kw" in params else None

    def read_entry(self, form: FormData, entry: Entry, customers: list[Customer]) -> str:
        if entry.step == 1:
            entry.customer = _code(customers, form.get("cid", ""))
            entry.cust_po = form.get("po", "")
            entry.order_date = normalise_date(form.get("d_order", ""))
            entry.delivery_date = normalise_date(form.get("d_need", ""))
            entry.remark = form.get("memo", "")
        elif entry.step == 2:
            entry.lines = [EntryLine(form.get(f"ln{i}_code", "").strip(), form.get(f"ln{i}_qty", "").strip(),
                                     form.get(f"ln{i}_price", "").strip()) for i in range(1, len(entry.lines) + 1)]
        return form.get("op", "")
