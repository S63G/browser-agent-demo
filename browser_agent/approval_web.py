"""Small local approval page (127.0.0.1): pending order drafts with the portal's confirm-page screenshot."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

from .approval import ApprovalRequest, ApprovalStore
from .portal.views import esc

CSS = """body{font:14px 'Microsoft YaHei',sans-serif;margin:24px;background:#f6f7f9;color:#222}
.req{background:#fff;border:1px solid #dde1e6;border-radius:6px;padding:14px;margin-bottom:16px}
table{border-collapse:collapse}td,th{border-bottom:1px solid #eee;padding:4px 10px;text-align:left}
button{padding:6px 18px;margin-right:8px;border-radius:4px;border:1px solid #bbb;cursor:pointer}
.ok{background:#1677ff;color:#fff;border-color:#1677ff}.no{background:#fff1f0;color:#cf1322;border-color:#ffa39e}
img{max-width:640px;border:1px solid #ccc;margin-top:8px}.muted{color:#888}"""


def _card(request: ApprovalRequest, actionable: bool) -> str:
    p = request.preview
    lines = "".join(f"<tr><td>{esc(line['sku'])}</td><td>{line['qty']:g}</td><td>{line['unit_price']:.2f}</td></tr>"
                    for line in p["lines"])
    buttons = (f'<form method="post" action="/decide/{esc(request.key)}"><button class="ok" name="approve" value="1">批准提交</button>'
               f'<button class="no" name="approve" value="0">驳回</button></form>' if actionable
               else f'<p class="muted">{esc(request.status)} · {esc(request.decided_by or "")} · {esc(request.decided_at or "")}</p>')
    shot = f'<br><img src="/screenshot/{esc(request.key)}" alt="门户确认页截图">' if request.screenshot else ""
    return (f'<div class="req"><b>{esc(p["customer"])}</b> · 客户单号 {esc(p["customer_po"])} · 幂等键 {esc(request.key)}'
            f'<p>单据日期 {esc(p["order_date"])} · 交货日期 {esc(p.get("delivery_date") or "无")} · 合计 {p["total"]:.2f}'
            f'（门户确认页 {p["portal_total"]:.2f}）· 来源 {esc(p.get("source") or "未注明")}</p>'
            f"<table><tr><th>物料编码</th><th>数量</th><th>含税单价</th></tr>{lines}</table>{buttons}{shot}</div>")


def create_approval_app(store: ApprovalStore) -> FastAPI:
    app = FastAPI(title="Approvals", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/")
    def index() -> HTMLResponse:
        pending = store.pending()
        decided = [r for r in store.recent() if r.status != "pending"]
        body = "".join(_card(r, True) for r in pending) or '<p class="muted">没有待审批的写操作。</p>'
        history = "".join(_card(r, False) for r in decided)
        return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>写操作审批</title>
<meta http-equiv="refresh" content="5"><style>{CSS}</style></head><body><h2>写操作审批（浏览器 Agent）</h2>
<p class="muted">每一张销售订单草稿提交到门户之前都要在这里或命令行里人工批准。演示数据均为虚构。</p>{body}
<h3>最近处理</h3>{history or '<p class="muted">暂无</p>'}</body></html>""")

    @app.post("/decide/{key}")
    def decide(key: str, approve: str = Form(...)) -> RedirectResponse:
        if store.get(key) is None:
            raise HTTPException(404, "no such approval request")
        store.decide(key, approve == "1", "web")
        return RedirectResponse("/", status_code=303)

    @app.get("/screenshot/{key}")
    def screenshot(key: str) -> FileResponse:
        request = store.get(key)
        if request is None or not request.screenshot or not Path(request.screenshot).exists():
            raise HTTPException(404, "no screenshot")
        return FileResponse(request.screenshot, media_type="image/png")

    return app
