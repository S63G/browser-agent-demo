"""Command line: python -m browser_agent {portal,mcp,demo,eval,approvals}."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from .config import Settings


def cmd_portal(settings: Settings, port: int, fresh: bool, layout: str) -> None:
    import uvicorn

    from .portal import build_portal

    app = build_portal(settings.portal_db, settings.data_dir, fresh=fresh)
    app.state.portal.settings.layout = layout
    if fresh and settings.agent_db.exists():
        print(f"注意：门户数据已重建，但代理的幂等台账 {settings.agent_db} 还在；台账里核实过的订单会直接返回 already_exists。"
              "要从头演示，请先删除整个 out/ 目录。")
    print(f"模拟老系统门户：http://127.0.0.1:{port}    演示账号见 .env.example（PORTAL_USER / PORTAL_PASSWORD）")
    print(f"界面版本 {layout}；切换版式与故障注入：http://127.0.0.1:{port}/admin/settings    数据库：{settings.portal_db}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


def cmd_approvals(settings: Settings, port: int) -> None:
    import uvicorn

    from .approval import ApprovalStore
    from .approval_web import create_approval_app

    print(f"写操作审批页：http://127.0.0.1:{port}")
    uvicorn.run(create_approval_app(ApprovalStore(settings.agent_db)), host="127.0.0.1", port=port, log_level="warning")


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m browser_agent",
                                     description="浏览器 Agent + MCP：没有 API 的老系统也能录单/查单（演示数据均为虚构）")
    sub = parser.add_subparsers(dest="command", required=True)
    portal = sub.add_parser("portal", help="启动模拟老系统门户（127.0.0.1:8770）")
    portal.add_argument("--port", type=int, default=8770)
    portal.add_argument("--fresh", action="store_true", help="用种子数据重建门户数据库")
    portal.add_argument("--layout", choices=["v1", "v2", "v3"], default="v1")
    sub.add_parser("mcp", help="以 stdio 方式运行 MCP server（供任意 MCP 客户端调用）")
    demo = sub.add_parser("demo", help="端到端演示：Demo A 的订单 → MCP 工具 → 审批 → 提交 → 核实")
    demo.add_argument("--approve", choices=["cli", "web", "auto"], default="cli",
                      help="审批方式：cli = 命令行逐单确认（默认），web = 在审批页 127.0.0.1:8771 上批准，auto = 自动批准")
    demo.add_argument("--auto-approve", dest="approve", action="store_const", const="auto",
                      help="演示用，等同 --approve auto")
    demo.add_argument("--layout", choices=["v1", "v2", "v3"], help="先把门户切换到这个界面版本")
    demo.add_argument("--limit", type=int, default=3, help="录入前几张订单（默认 3）")
    demo.add_argument("--orders", type=Path, help="订单 JSON（默认 data/demo_a_orders.json）")
    evaluate = sub.add_parser("eval", help="运行评测（20 个任务 × 各配置）")
    evaluate.add_argument("--configs", default="all",
                          help="all 或逗号分隔：scripted,model,holdout_scripted,holdout_model,naive_retry")
    approvals = sub.add_parser("approvals", help="单独启动写操作审批页（MCP server 默认会自动启动）")
    approvals.add_argument("--port", type=int, default=8771)
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "portal":
        cmd_portal(settings, args.port, args.fresh, args.layout)
    elif args.command == "mcp":
        from .mcp_server import create_mcp_server

        create_mcp_server(settings).run("stdio")
    elif args.command == "demo":
        from .orchestrator import run_demo

        orders = args.orders or settings.data_dir / "demo_a_orders.json"
        asyncio.run(run_demo(settings, orders, args.limit, args.approve, args.layout))
    elif args.command == "eval":
        from .evaluate import config_names, print_overview, run_eval

        print_overview(run_eval(settings, config_names(args.configs)))
        print("报告：evaluation/*.md（逐任务明细见同名 .json）")
    elif args.command == "approvals":
        cmd_approvals(settings, args.port)


if __name__ == "__main__":
    main()
