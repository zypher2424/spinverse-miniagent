#!/usr/bin/env python3
"""Render a reproducible public UI screenshot with real offline tool execution.

Only temporary fixture files are used. No .env or real API is loaded.
"""
import argparse
import asyncio
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from commands import CommandRouter
from llm import DemoModel
from main import parser
from permissions import PermissionPolicy
from session import Session
from tools import workspace as workspace_module
from tui import MiniAgentApp, TUIBridge


async def capture(output):
    # A generic temporary root keeps developer home directories out of the image.
    with tempfile.TemporaryDirectory(prefix="spinverse-demo-", dir="/tmp") as directory:
        base = Path(directory).resolve()
        project = base / "project"
        shutil.copytree(ROOT / "demo" / "project", project)
        args = parser().parse_args(["--demo", "--yes", "--session-dir", str(base / "sessions")])
        args.check = "test_arithmetic.py"
        workspace_module.WORKSPACE = workspace_module.Workspace(project)
        workspace_module.WORKSPACE.protected_paths.add(args.check)
        bridge = TUIBridge()
        session = Session.create(args.session_dir, project, {"mode": "public-offline-demo"})
        controller = CommandRouter(args, DemoModel(), PermissionPolicy(confirm=lambda n, a: True),
                                   session, project, {"mode": "public-offline-demo"}, bridge)
        app = MiniAgentApp(controller, bridge)
        try:
            async with app.run_test(size=(140, 52)) as pilot:
                await pilot.pause(.1)
                await app.submit("定位并修复 arithmetic.py 的加法错误，保留验收测试，并运行真实检查。")
                for _ in range(200):
                    await pilot.pause(.05)
                    if not app.busy:
                        break
                assert controller.last_result and controller.last_result.completed
                assert all(a["phase"] == "completed" and a["result"]["status"] == "success"
                           for a in controller.session.data["actions"])
                app.query_one("#conversation").scroll_home(animate=False)
                await pilot.pause(.1)
                output.parent.mkdir(parents=True, exist_ok=True)
                svg = app.export_screenshot(title="MiniAgent × Spinverse")
                output.write_text("\n".join(line.rstrip() for line in svg.splitlines()) + "\n", encoding="utf-8")
                print(f"Saved actual offline demo UI: {output}")
        finally:
            controller.close()


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--output", type=Path, default=ROOT / "docs" / "assets" / "terminal.svg")
    options = cli.parse_args()
    os.environ.pop("NO_COLOR", None)  # Color is intentional in the exported asset.
    asyncio.run(capture(options.output))
