#!/usr/bin/env python3
"""MiniAgent v1.2 产品入口；可选真实 API 或明确标记的离线演示。"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

from commands import CommandRouter, DEMO_TASK, HELP
from config import Config
from llm import DemoModel, LLM
from permissions import PermissionPolicy
from session import Session, SessionError
from tools import workspace as workspace_module
from ui import TerminalUI

ROOT = Path(__file__).resolve().parent


def parser():
    p = argparse.ArgumentParser(description="MiniAgent v1.2 · 计划、搜索、Patch、验证、会话恢复")
    p.add_argument("--workspace", type=Path, help="代码工作区，默认当前目录")
    p.add_argument("--demo", action="store_true", help="使用隔离的五文件练习和脚本模型，不调用网络")
    view = p.add_mutually_exclusive_group()
    view.add_argument("--tui", action="store_true", help="使用参考图风格的全屏终端界面")
    view.add_argument("--no-tui", action="store_true", help="使用逐行终端输出")
    p.add_argument("--task", help="执行一次任务后退出（非成功 exit=2）")
    p.add_argument("--direct", action="store_true", help="直接工具循环，不生成计划")
    p.add_argument("--yes", action="store_true", help="显式自动确认通过硬策略的行动")
    p.add_argument("--resume", help="恢复会话 ID；不会自动重放行动")
    p.add_argument("--session-dir", type=Path, default=ROOT / ".miniagent" / "sessions")
    p.add_argument("--model", help="覆盖 MODEL_NAME")
    p.add_argument("--check", help="独立 pytest 验收文件，相对工作区；禁止模型修改")
    p.add_argument("--max-requests", type=int, default=30)
    p.add_argument("--max-tool-turns", type=int, default=20)
    p.add_argument("--context-budget", type=int, default=16000)
    p.add_argument("--plain", action="store_true", help="不使用颜色")
    p.add_argument("--json", action="store_true", help="JSONL 事件输出；自动化调用建议配合 --yes")
    p.add_argument("--export-html", type=Path, help="保存本次终端展示，便于查看运行证据")
    return p


def make_demo(root=None):
    parent = ROOT / ".miniagent" / "demos"
    parent.mkdir(parents=True, exist_ok=True)
    target = Path(tempfile.mkdtemp(prefix="arithmetic-", dir=parent))
    shutil.copytree(ROOT / "demo" / "project", target, dirs_exist_ok=True)
    return target


def configuration(args, model):
    policy_files = [*sorted(ROOT.glob("*.py")), *sorted((ROOT / "tools").glob("*.py"))]
    policy_hash = hashlib.sha256(b"".join(p.read_bytes() for p in policy_files)).hexdigest()
    return {"model": model, "base_url": "offline" if args.demo else Config.load(args.model).base_url,
            "yes": args.yes, "max_requests": args.max_requests, "max_tool_turns": args.max_tool_turns,
            "context_budget": args.context_budget, "check": args.check, "policy_hash": policy_hash}


def help_text():
    return HELP


def create_controller(args, ui):
    session = None
    try:
        if args.demo and args.direct:
            raise ValueError("演示包含三步计划，请去掉 --direct")
        if args.resume:
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", args.resume):
                raise ValueError("无效 session id")
            metadata = json.loads((args.session_dir / f"{args.resume}.json").read_text())
            workspace = args.workspace or Path(metadata["workspace"])
            if args.demo:
                args.check = args.check or "test_arithmetic.py"
        else:
            if args.demo and args.workspace:
                raise ValueError("--demo 自行创建隔离目录；真实项目请使用 --workspace")
            workspace = make_demo() if args.demo else args.workspace or Path.cwd()
            if args.demo:
                args.check = "test_arithmetic.py"
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise ValueError("工作区必须是已存在目录")
        workspace_module.WORKSPACE = workspace_module.Workspace(workspace)
        if args.check:
            valid = workspace_module.WORKSPACE.validate_read(args.check)
            if not valid.ok:
                raise ValueError("独立验收文件不可读：" + valid.error)
            # 使用规范相对路径，避免别名绕过保护。
            args.check = valid.value.relative_to(workspace).as_posix()
            workspace_module.WORKSPACE.protected_paths.add(args.check)
        model = DemoModel() if args.demo else LLM(Config.load(args.model))
        config = configuration(args, model.model)
        session = (Session.load(args.session_dir, args.resume, workspace, config) if args.resume
                   else Session.create(args.session_dir, workspace, config))
        policy = PermissionPolicy(confirm=(lambda name, values: True) if args.yes else ui.confirm)
        return CommandRouter(args, model, policy, session, workspace, config, ui)
    except BaseException:
        if session:
            session.close()
        raise


def main(argv=None):
    args = parser().parse_args(argv)
    if args.max_requests < 1 or args.max_tool_turns < 0 or args.context_budget < 4096:
        parser().error("请求预算须 >=1、工具轮预算须 >=0、上下文预算须 >=4096")
    if args.tui and (args.json or args.plain or args.task):
        parser().error("--tui 用于交互界面；--task / --json / --plain 使用逐行输出")
    fullscreen = args.tui or (not args.no_tui and not args.task and not args.json and not args.plain
                              and sys.stdin.isatty() and sys.stdout.isatty())
    if fullscreen:
        from tui import TUIBridge, MiniAgentApp
        ui = TUIBridge()
    else:
        ui = TerminalUI(args.plain, args.json)
    controller = None
    try:
        controller = create_controller(args, ui)
        if fullscreen:
            app = MiniAgentApp(controller, ui)
            app.run()
            return app.exit_code
        controller.announce()
        if args.task:
            result = controller.run(args.task, retry=bool(args.resume and controller.agent.runtime.active))
            return 0 if result.completed else 2
        while True:
            try:
                text = ui.prompt().strip()
            except (EOFError, KeyboardInterrupt):
                ui.info("会话已保存。再见。")
                break
            controller.cancel_event.clear()
            if not controller.handle(text):
                break
        return 0
    except (ValueError, OSError, KeyError, SessionError) as exc:
        message = "启动或记录失败：" + str(exc)
        (TerminalUI().info if fullscreen else ui.info)(message)
        return 3
    finally:
        if controller:
            controller.close()
        if args.export_html:
            ui.export(args.export_html)


if __name__ == "__main__":
    sys.exit(main())
