"""Evaluation: 20 scripted tasks (T01–T10 on layout v1, T11–T20 on v2), plus T11–T20 again on the hold-out v3.

Each configuration gets a fresh portal database on a free port, a fresh ledger, approval store and selector cache.
Success is judged against data/eval_tasks.json AND an independent look into the portal database (not the agent's
own report): created orders must exist exactly once with the right lines; refused ones must not exist at all.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import shutil
import statistics
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .approval import auto_decider, run_approver
from .config import PROJECT_ROOT, Settings
from .control import PortalControl
from .flows import OrderQuery
from .llm import build_gateway
from .models import CreateResult, OrderDraft
from .portal import PortalDB, build_portal, free_port, serve_in_background
from .service import PortalAgent

HANDOVER = {"needs_human", "unverified", "approval_timeout", "portal_rejected"}


@dataclass(frozen=True)
class EvalConfig:
    name: str
    title: str
    model: bool
    task_ids: tuple[str, ...] | None = None    # None = all tasks
    layout: str | None = None                  # override the task's own layout
    verify_before_retry: bool = True


CONFIGS = {
    "scripted": EvalConfig("scripted", "仅脚本（选择器按 v1 写）", model=False),
    "model": EvalConfig("model", "脚本 + 模型兜底", model=True),
    "holdout_scripted": EvalConfig("holdout_scripted", "hold-out v3 · 仅脚本", model=False,
                                   task_ids=tuple(f"T{i}" for i in range(11, 21)), layout="v3"),
    "holdout_model": EvalConfig("holdout_model", "hold-out v3 · 脚本 + 模型兜底", model=True,
                                task_ids=tuple(f"T{i}" for i in range(11, 21)), layout="v3"),
    "naive_retry": EvalConfig("naive_retry", "对照：超时后不核实直接重试", model=False, task_ids=("T08",),
                              verify_before_retry=False),
}


def load_tasks(settings: Settings) -> list[dict]:
    return json.loads((settings.data_dir / "eval_tasks.json").read_text(encoding="utf-8"))["tasks"]


def _orders_with_key(db: PortalDB, key: str) -> list:
    return [o for o in db.orders_created_by("op01") if key in o.remark]


def _portal_problems(db: PortalDB, draft: OrderDraft, key: str, should_exist: bool) -> list[str]:
    orders = _orders_with_key(db, key)
    if not should_exist:
        return [f"门户里出现了不该有的订单 {[o.order_no for o in orders]}"] if orders else []
    if len(orders) != 1:
        return [f"门户里带此幂等键的订单有 {len(orders)} 张（应为 1）"]
    order = orders[0]
    got = sorted((line.sku, line.qty, round(line.unit_price, 2)) for line in order.lines)
    want = sorted((line.sku, line.qty, round(line.unit_price, 2)) for line in draft.lines)
    problems = [] if got == want else ["门户订单明细与订单不一致"]
    if order.customer.code != draft.customer_id or order.cust_po != draft.customer_po:
        problems.append("门户订单客户或客户单号不一致")
    return problems


def judge(task: dict, results: list, db: PortalDB) -> list[str]:
    """Reasons the task failed (empty = success)."""
    kind, expect, first = task["kind"], task["expect"], results[0]
    if kind in ("search_po", "search_paged"):
        if first.status != "ok":
            return [f"status={first.status}: {first.message}"]
        got = sorted(o.order_no for o in first.orders)
        return [] if got == sorted(expect["order_nos"]) else [f"单号 {got} ≠ {sorted(expect['order_nos'])}"]
    if kind == "stock":
        if first.status != "ok":
            return [f"status={first.status}: {first.message}"]
        got = {i.sku: i.available for i in first.items}
        problems = [] if got == expect["found"] else [f"可用量 {got} ≠ {expect['found']}"]
        if sorted(first.not_found) != sorted(expect["not_found"]):
            problems.append(f"not_found {first.not_found} ≠ {expect['not_found']}")
        return problems
    if kind in ("invoice", "invoice_none"):
        if first.status != expect["status"]:
            return [f"status={first.status} ≠ {expect['status']}: {first.message}"]
        if kind == "invoice" and (first.invoice_no != expect["invoice_no"] or abs((first.amount or 0) - expect["amount"]) > 0.01):
            return [f"发票 {first.invoice_no}/{first.amount} ≠ {expect['invoice_no']}/{expect['amount']}"]
        return []
    draft = OrderDraft.model_validate(task["args"]["order"])
    key = first.idempotency_key
    problems = [] if first.status == expect["status"] else [f"status={first.status} ≠ {expect['status']}: {first.message}"]
    if expect["status"] == "created" and not first.verified:
        problems.append("未完成提交后核实")
    if kind == "create_twice":
        second = results[1]
        if second.status != expect["second_status"] or second.order_no != first.order_no:
            problems.append(f"第二次调用 status={second.status} 单号 {second.order_no}")
    return problems + _portal_problems(db, draft, key, should_exist=expect["status"] == "created")


async def _run_task(agent: PortalAgent, task: dict) -> list:
    args = task["args"]
    if task["tool"] == "search_order":
        query = OrderQuery(customer_po=args.get("customer_po", ""), customer_id=args.get("customer_id", ""),
                           date_from=_date(args.get("date_from")), date_to=_date(args.get("date_to")))
        return [await agent.search_order(query)]
    if task["tool"] == "check_stock":
        return [await agent.check_stock(args["skus"])]
    if task["tool"] == "download_invoice":
        return [await agent.download_invoice(args["order_no"])]
    draft = OrderDraft.model_validate(args["order"])
    results: list[CreateResult] = [await agent.create_sales_order_draft(draft)]
    if task["kind"] == "create_twice":
        results.append(await agent.create_sales_order_draft(draft))
    return results


def _date(text: str | None) -> dt.date | None:
    return dt.date.fromisoformat(text) if text else None


async def _run_config(config: EvalConfig, settings: Settings, tasks: list[dict], url: str, db: PortalDB) -> list[dict]:
    gateway = build_gateway(settings) if config.model else None
    control = PortalControl(url)
    reject = frozenset(OrderDraft.model_validate(t["args"]["order"]).default_key() for t in tasks
                       if t["expect"].get("approver") == "reject")
    records = []
    async with PortalAgent(settings, gateway, verify_before_retry=config.verify_before_retry) as agent:
        stop = asyncio.Event()
        approver = asyncio.create_task(run_approver(agent.approvals, auto_decider(reject, "eval 审批人（按任务设定）"), stop))
        try:
            for task in tasks:
                layout = config.layout or task["layout"]
                control.set(layout=layout, submit_delay_once=task["expect"].get("inject_submit_delay", 0))
                started = time.monotonic()
                results = await _run_task(agent, task)
                seconds = round(time.monotonic() - started, 2)
                control.set(submit_delay_once=0)
                problems = judge(task, results, db)
                runs = [r.run for r in results if r.run is not None]
                record = {
                    "id": task["id"], "layout": layout, "kind": task["kind"], "title": task["title"],
                    "success": not problems, "problems": problems, "statuses": [r.status for r in results],
                    "seconds": seconds, "needs_human": any(r.status in HANDOVER for r in results),
                    "expected_handover": task["expect"].get("status") in HANDOVER,
                    "model_steps": sum(r.model_steps for r in runs), "model_calls": sum(r.model_calls for r in runs),
                    "cache_steps": sum(r.cache_steps for r in runs), "scripted_steps": sum(r.scripted_steps for r in runs),
                    "messages": [r.message for r in results if r.message],
                    "traces": [str(Path(r.trace).relative_to(settings.out_dir)) for r in runs],
                }
                records.append(record)
                mark = "OK " if record["success"] else "FAIL"
                print(f"  {mark} {task['id']} [{layout}] {task['title']}  {seconds}s  模型调用 {record['model_calls']}"
                      + ("" if record["success"] else f"  ← {'; '.join(problems)[:160]}"), flush=True)
        finally:
            stop.set()
            await approver
    if gateway:
        records.append({"usage": gateway.usage.as_dict()})
    return records


def _summary(records: list[dict], duplicates: int) -> dict:
    tasks = [r for r in records if "id" in r]
    usage = next((r["usage"] for r in records if "usage" in r), None)

    def block(rows: list[dict]) -> dict:
        times = [r["seconds"] for r in rows]
        return {"tasks": len(rows), "success": sum(r["success"] for r in rows),
                "mean_seconds": round(statistics.mean(times), 1) if times else 0,
                "median_seconds": round(statistics.median(times), 1) if times else 0,
                "needs_human": sum(r["needs_human"] for r in rows),
                "needs_human_unexpected": sum(r["needs_human"] and not r["expected_handover"] for r in rows),
                "model_steps": sum(r["model_steps"] for r in rows), "model_calls": sum(r["model_calls"] for r in rows),
                "tasks_using_model": sum(r["model_calls"] > 0 for r in rows)}

    by_layout = {layout: block([r for r in tasks if r["layout"] == layout])
                 for layout in sorted({r["layout"] for r in tasks})}
    return {"all": block(tasks), "by_layout": by_layout, "duplicate_orders": duplicates, "usage": usage}


def _duplicates(db: PortalDB) -> int:
    keys = Counter(o.remark.split("]")[0] for o in db.orders_created_by("op01") if o.remark.startswith("[ik-"))
    return sum(n - 1 for n in keys.values() if n > 1)


def run_eval(settings: Settings, names: list[str]) -> list[dict]:
    tasks_all = load_tasks(settings)
    model_mode = settings.llm_mode
    if model_mode == "off" and any(CONFIGS[n].model for n in names):
        if any(settings.replay_dir.glob("*.json")):
            model_mode = "replay"
            print("未设置 LLM_API_KEY：模型兜底列使用 data/replay/ 中录下的真实调用回放（不产生费用）。")
        else:
            print("未设置 LLM_API_KEY 且没有回放记录：跳过模型兜底配置。")
            names = [n for n in names if not CONFIGS[n].model]
    reports = []
    for name in names:
        config = CONFIGS[name]
        tasks = [t for t in tasks_all if config.task_ids is None or t["id"] in config.task_ids]
        out = settings.out_dir / "eval" / name
        shutil.rmtree(out, ignore_errors=True)
        app = build_portal(out / "portal.db", settings.data_dir, fresh=True)
        db: PortalDB = app.state.portal.db
        mode = model_mode if config.model else "off"
        run_settings = settings.with_changes(out_dir=out, llm_mode=mode, approval_timeout_s=60.0, approval_web=False)
        print(f"\n== {name}：{config.title}（{run_settings.mode_label}）", flush=True)
        with serve_in_background(app, free_port()) as url:
            run_settings = run_settings.with_changes(portal_url=url)
            records = asyncio.run(_run_config(config, run_settings, tasks, url, db))
        report = {"config": name, "title": config.title, "mode": run_settings.mode_label, "llm_mode": mode,
                  "model": settings.llm_model if config.model else None,
                  "summary": _summary(records, _duplicates(db)), "tasks": [r for r in records if "id" in r]}
        reports.append(report)
        write_report(report, settings)
    return reports


def report_stem(report: dict) -> str:
    return report["config"] + (f"_{report['llm_mode']}" if report["llm_mode"] != "off" else "")


def render_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [f"# 评测：{report['title']}", "", f"- 配置：`{report['config']}`，模式：`{report['mode']}`",
             f"- 重复提交（门户中同一幂等键多于 1 张单）：**{s['duplicate_orders']}**"]
    if s["usage"]:
        u = s["usage"]
        lines.append(f"- 模型用量（接口返回的 usage）：{u['calls']} 次调用，输入 {u['prompt_tokens']:,} tokens，"
                     f"输出 {u['completion_tokens']:,} tokens")
    lines += ["", "| 版式 | 成功 | 平均耗时 | 中位耗时 | 转人工（其中非预期） | 用到模型的任务 | 模型兜底步骤 | 模型调用 |",
              "|---|---|---|---|---|---|---|---|"]
    for layout, b in [*s["by_layout"].items(), ("合计", s["all"])]:
        lines.append(f"| {layout} | {b['success']}/{b['tasks']} | {b['mean_seconds']}s | {b['median_seconds']}s | "
                     f"{b['needs_human']}（{b['needs_human_unexpected']}） | {b['tasks_using_model']} | "
                     f"{b['model_steps']} | {b['model_calls']} |")
    lines += ["", "| 任务 | 版式 | 说明 | 结果 | 状态 | 耗时 | 模型调用 | 备注 |", "|---|---|---|---|---|---|---|---|"]
    for t in report["tasks"]:
        note = "；".join(t["problems"]) if t["problems"] else "；".join(t["messages"])[:80]
        lines.append(f"| {t['id']} | {t['layout']} | {t['title']} | {'✅' if t['success'] else '❌'} | "
                     f"{'/'.join(t['statuses'])} | {t['seconds']}s | {t['model_calls']} | {note} |")
    return "\n".join(lines) + "\n"


def write_report(report: dict, settings: Settings) -> Path:
    folder = PROJECT_ROOT / "evaluation"
    folder.mkdir(exist_ok=True)
    stem = report_stem(report)
    (folder / f"{stem}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    path = folder / f"{stem}.md"
    path.write_text(render_markdown(report), encoding="utf-8")
    return path


def print_overview(reports: list[dict]) -> None:
    print("\n| 配置 | 成功 | 平均耗时 | 转人工（非预期） | 重复提交 | 模型调用 |")
    print("|---|---|---|---|---|---|")
    for r in reports:
        a, s = r["summary"]["all"], r["summary"]
        print(f"| {r['title']} | {a['success']}/{a['tasks']} | {a['mean_seconds']}s | "
              f"{a['needs_human']}（{a['needs_human_unexpected']}） | {s['duplicate_orders']} | {a['model_calls']} |")


def config_names(selection: str) -> list[str]:
    if selection == "all":
        return list(CONFIGS)
    names = [n.strip() for n in selection.split(",") if n.strip()]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise ValueError(f"unknown eval config(s) {unknown}; choose from {list(CONFIGS)}")
    return names
