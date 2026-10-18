"""终端展示层：Rich 面板、计划表、Diff、行动输出；不包含工具执行逻辑。"""
import json
from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text


class TerminalUI:
    def __init__(self, plain=False, json_mode=False):
        self.console = Console(record=True, no_color=plain, highlight=False)
        self.json_mode = json_mode

    def header(self, workspace, model, session, automatic=False):
        if self.json_mode:
            return self.event("session", {"workspace": str(workspace), "model": model, "session": session})
        details = Table.grid(padding=(0, 2))
        for name, value in (("工作区", workspace), ("模型", model), ("会话", session),
                            ("权限", "显式 --yes：自动确认合法行动" if automatic else "读写/执行按策略确认")):
            details.add_row(Text(name, style="dim"), Text(str(value)))
        self.console.print(Panel(Group(Text("MINIAGENT  /  1.2", style="bold cyan"),
                                       Text("计划 · 代码定位 · 精确修改 · 检查 · 恢复", style="dim"), details),
                                 border_style="cyan", padding=(1, 2)))
        self.console.print("[dim]/help 命令帮助  /plan 计划  /actions 行动  /retry 恢复  /exit 退出[/dim]")

    def event(self, kind, data):
        if self.json_mode:
            print(json.dumps({"event": kind, "data": data}, ensure_ascii=False), flush=True)
            return
        c = self.console
        if kind in ("plan", "plan_detail"):
            table = Table(title="执行计划", expand=True, border_style="dim")
            for col in ("步骤", "状态", "目标", "验收"):
                table.add_column(col)
            styles = {"pending": "dim", "running": "cyan", "done": "green", "failed": "red", "blocked": "yellow"}
            for s in data["steps"]:
                table.add_row(s["id"], Text(s["status"], style=styles[s["status"]]), s["goal"], s["acceptance"])
            c.print(table)
        elif kind == "model":
            c.print(Text(f"◌ 模型请求 {data['request']}  ·  输入估计 {data.get('estimated_tokens', '?')} tokens  ·  裁剪旧轮 {data.get('dropped_turns', 0)}", style="dim"))
        elif kind == "tool_start":
            try:
                args = json.loads(data.get("arguments") or "{}")
            except (ValueError, TypeError):
                args = {}
            label = data["name"] + "  " + str(args.get("path") or args.get("command") or args.get("query") or "")
            c.print(Text("→ " + label, style="bold blue"))
        elif kind == "tool_result":
            success = data["status"] == "success"
            style = "green" if success else "yellow" if data["status"] == "denied" else "red"
            c.print(Text(f"  {data['status']}  {data['message']}", style=style))
            content = data.get("data") or {}
            for field in ("stdout", "stderr"):
                if content.get(field):
                    c.print(Panel(Text(content[field].rstrip()), title=field, border_style="dim"))
            if "returncode" in content:
                c.print(Text(f"  exit={content['returncode']}  timeout={content['timed_out']}", style="dim"))
        elif kind == "result":
            c.print(Panel(Markdown(data["reply"]), title="完成" if data["completed"] else "尚未完成 · " + data["stop_reason"],
                          border_style="green" if data["completed"] else "yellow"))

    def confirm(self, name, args):
        if self.json_mode:
            return False
        from permissions import ASK_DETAILS
        detail = ASK_DETAILS.get(name)
        text = detail(args) if detail else json.dumps(args, ensure_ascii=False, indent=2)
        body = Syntax(text, "diff", word_wrap=True) if name == "apply_patch" else Text(text)
        self.console.print(Panel(body, title="权限确认 · " + name, border_style="yellow"))
        try:
            return self.console.input("允许此次行动？[y/N] ", markup=False).strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def info(self, text):
        if self.json_mode:
            return self.event("info", {"message": text})
        self.console.print(Text(text))

    def prompt(self):
        if self.json_mode:
            return input()
        return self.console.input("\n[bold cyan]你 › [/bold cyan]")

    def export(self, path):
        self.console.save_html(str(path), clear=False)
        self.console.save_svg(str(path.with_suffix(".svg")), title="MiniAgent v1.2 · CLI", clear=False)
