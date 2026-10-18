"""参考图风格全屏终端：固定输入区、会话流、真实工具状态和权限弹窗。

UI 在主事件循环中更新；同步 Agent 在单个后台线程运行，通过 Queue 传递事件。
权限回调等待显式确认；取消是协作式的，不丢弃已经发生的行动记录。
"""
import copy
import json
import queue
import threading
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

GREEN = "#3cca79"
MUTED = "#a8a8a8"
BLUE = "#4989ed"


@dataclass
class Confirmation:
    name: str
    args: dict
    description: str
    answered: threading.Event = field(default_factory=threading.Event)
    approved: bool = False

    def answer(self, approved):
        if not self.answered.is_set():
            self.approved = approved
            self.answered.set()


class TUIBridge:
    """后台可调用的展示接口，与 TerminalUI 保持同一事件/确认协议。"""
    def __init__(self):
        self.events = queue.Queue()
        self.controller = None
        self.latest_svg = None

    def event(self, kind, data):
        self.events.put((kind, copy.deepcopy(data)))

    def header(self, workspace, model, session, automatic=False):
        self.event("session", {"workspace": str(workspace), "model": model,
                               "session": session, "automatic": automatic})

    def info(self, text):
        self.event("info", {"message": text})

    def confirm(self, name, args):
        from permissions import ASK_DETAILS
        render = ASK_DETAILS.get(name)
        description = render(args) if render else json.dumps(args, ensure_ascii=False, indent=2)
        request = Confirmation(name, args, description)
        self.events.put(("permission", request))
        while not request.answered.wait(0.05):
            if self.controller is not None and self.controller.cancel_event.is_set():
                request.answer(False)
        return request.approved

    def export(self, path):
        if self.latest_svg:
            Path(path).write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
                                 '<title>MiniAgent CLI</title><body style="margin:0;background:#0e0e0e">'
                                 + self.latest_svg + '</body></html>', encoding="utf-8")
            Path(path).with_suffix(".svg").write_text(self.latest_svg, encoding="utf-8")


class ToolLine(Static, can_focus=True):
    BINDINGS = [Binding("enter", "toggle", show=False)]

    def __init__(self, call):
        self.call, self.outcome, self.expanded = call, None, False
        super().__init__(classes="tool-line")
        self.tooltip = "点击或 Enter 展开真实工具结果"
        self.refresh_content()

    def label(self):
        try:
            args = json.loads(self.call.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        value = args.get("path", args.get("query", "." if self.call["name"] == "list_files" else ""))
        if "command" in args:
            value = " ".join([args["command"], *args.get("args", [])])
        return self.call["name"] + "(" + (json.dumps(value, ensure_ascii=False) if value else "") + ")"

    def refresh_content(self):
        status = self.outcome["status"] if self.outcome else "running"
        color = {"success": GREEN, "running": BLUE, "denied": "#d7ac65",
                 "timeout": "#d7ac65", "error": "#e98585"}[status]
        badge = {"success": "Done", "running": "Running", "denied": "Denied",
                 "timeout": "Timeout", "error": "Error"}[status]
        if self.outcome and self.outcome.get("error_code") == "CANCELLED":
            badge, color = "Cancelled", "#d7ac65"
        text = Text("✓ " if status == "success" else "◌ " if status == "running" else "! ", style=color)
        text.append(self.label(), style="#dedede")
        text.append("  ")
        text.append(" " + badge + " ", style=f"{color} on #12291b" if status == "success" else color)
        if self.expanded and self.outcome:
            data = self.outcome.get("data")
            details = self.outcome["message"] + ("\n" + json.dumps(data, ensure_ascii=False, indent=2) if data else "")
            self.update(Group(text, Text(details, style=MUTED)))
        else:
            self.update(text)

    def finish(self, outcome):
        self.outcome = outcome
        self.refresh_content()

    def action_toggle(self):
        self.expanded = not self.expanded
        self.refresh_content()

    def on_click(self):
        self.action_toggle()


class ChatMessage(Vertical):
    def __init__(self, role, body=None):
        self.role, self.body = role, body
        super().__init__(classes="chat-message")

    def compose(self):
        label = Text("○  You", style="#dcdcdc") if self.role == "user" else Text("›_ ", style=GREEN)
        if self.role != "user":
            label.append(" MiniAgent", style="#dedede")
        yield Static(label, classes="role-label")
        if self.body is not None:
            yield Static(Text(self.body) if self.role == "user" else Markdown(self.body), classes="message-body")


class PermissionDialog(ModalScreen[bool]):
    BINDINGS = [Binding("escape,n", "deny", priority=True),
                Binding("y", "allow", priority=True), Binding("ctrl+c", "cancel", priority=True)]

    def __init__(self, request):
        super().__init__()
        self.request = request

    def compose(self):
        with Vertical(id="permission-dialog"):
            yield Static("Permission required", id="permission-title")
            yield Static(Text(self.request.name, style=GREEN), id="permission-tool")
            with VerticalScroll(id="permission-content"):
                yield Static(Syntax(self.request.description, "diff", theme="ansi_dark", word_wrap=True)
                             if self.request.name == "apply_patch" else Text(self.request.description))
            with Horizontal(id="permission-buttons"):
                yield Button("Deny  [Esc]", id="deny")
                yield Button("Allow  [Y]", id="allow")

    def on_mount(self):
        self.query_one("#deny", Button).focus()

    def action_deny(self):
        self.dismiss(False)

    def action_allow(self):
        self.dismiss(True)

    def action_cancel(self):
        self.app.action_cancel()
        self.dismiss(False)

    @on(Button.Pressed)
    def choose(self, event):
        self.dismiss(event.button.id == "allow")


class MiniAgentApp(App):
    TITLE = "MiniAgent v1.2"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [Binding("ctrl+c", "cancel", "Cancel", priority=True),
                Binding("ctrl+q", "quit_safely", "Quit", priority=True)]
    CSS_PATH = "tui.tcss"

    def __init__(self, controller, bridge):
        super().__init__()
        self.controller, self.bridge = controller, bridge
        bridge.controller = controller
        self.busy = False
        self.fatal = False
        self.exit_code = 0
        self.turn = None
        self.intro = None
        self.tool_lines = []
        self.pending_tool = None
        self.confirmation = None
        self.spinner_index = 0
        self.activity = ""
        self.state = "Ready"
        self.quit_after_cancel = False
        self.session_id = controller.session.id
        self._ever_started = False

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            with Horizontal(id="brand-row"):
                yield Static(Text.assemble(("›_  ", GREEN), ("MINI AGENT", "bold #e8e8e8"), ("  v1.2", MUTED), ("  /  SPINVERSE", "#6b9d7d")), id="brand")
                yield Static("", id="state")
            with Horizontal(id="metadata-row"):
                yield Static("", id="workspace")
                yield Static("", id="session")
            with VerticalScroll(id="conversation"):
                yield Static("描述一个任务，MiniAgent 会在执行前检查权限。", id="empty-state")
            yield Static("", id="activity")
            with Vertical(id="composer"):
                yield Input(placeholder="Ask MiniAgent anything...", id="prompt")
                with Horizontal(id="composer-actions"):
                    yield Static("/help · /status · /resume", id="command-hints")
                    yield Button("↵ Send", id="send")
            with Horizontal(id="bottom-row"):
                yield Static("", id="model")
                yield Static("Ctrl+C Cancel · /exit Quit", id="shortcuts")

    async def on_mount(self):
        self.theme = "textual-dark"
        self._metadata()
        self._status("Ready", GREEN)
        self.set_interval(0.05, self.drain_events)
        self.set_interval(0.15, self.animate)
        self.query_one("#prompt", Input).focus()
        if self.controller.args.demo:
            self.query_one("#empty-state", Static).update("演示工作区已准备好。输入 /demo，查看真实搜索、Patch 和检查。")
        self.fit_layout()
        self.controller.announce()
        await self.load_history()
        if self.controller.session.unknown_actions():
            self._status("Needs review", "#d7ac65")

    async def load_history(self):
        panel = self.query_one("#conversation", VerticalScroll)
        for child in list(panel.children):
            if child.id != "empty-state":
                await child.remove()
        self.query_one("#empty-state").display = True
        self.turn, self.intro, self.pending_tool = None, None, None
        self.tool_lines.clear()
        messages = self.controller.session.data["messages"]
        internal = ("[执行步骤", "执行计划（", "运行时独立验收", "当前步骤尚未")
        users = [i for i, m in enumerate(messages) if m["role"] == "user"
                 and not (m.get("content") or "").startswith(internal)]
        start = users[-1] if users else max(0, len(messages) - 14)
        for message in messages[start:]:
            text = message.get("content") or ""
            if message["role"] in ("user", "assistant") and not message.get("tool_calls") and text and not text.startswith(internal):
                await self.add_message(message["role"], text)

    def _metadata(self):
        metadata = self.controller.metadata()
        path = str(metadata["workspace"])
        home = str(Path.home())
        if path.startswith(home + "/"):
            path = "~" + path[len(home):]
        self.query_one("#workspace", Static).update(Text("Workspace: " + path, style="#bdbdbd", overflow="ellipsis", no_wrap=True))
        session_label = metadata["session"] if self.size.width >= 100 else metadata["session"][-6:]
        self.query_one("#session", Static).update(Text("Session: #" + session_label, style="#bdbdbd", no_wrap=True))
        self.query_one("#model", Static).update(Text("Model: " + metadata["model"], style=MUTED, no_wrap=True))

    def fit_layout(self):
        shells = self.query("#shell")
        if shells:
            shells.first().set_class(self.size.width < 100, "compact")
            shells.first().set_class(self.size.height < 32, "short")
            self._metadata()

    def on_resize(self):
        self.fit_layout()

    def _status(self, name, color=GREEN):
        if self.busy and self.controller.cancel_event.is_set() and name in ("Working", "Thinking", "Executing", "Permission"):
            name, color = "Cancelling", "#d7ac65"
            self.activity = "Cancelling... waiting for the current request to return."
        self.state = name
        self.query_one("#state", Static).update(Text("● " + name, style=color))

    async def add_message(self, role, text):
        self.query_one("#empty-state").display = False
        message = ChatMessage(role, text)
        await self.query_one("#conversation", VerticalScroll).mount(message)
        self.scroll_bottom()
        return message

    def scroll_bottom(self):
        self.query_one("#conversation", VerticalScroll).scroll_end(animate=False, immediate=False)

    async def submit(self, text):
        text = text.strip()
        if not text or self.busy or self.fatal:
            return
        if text in ("/exit", "exit", "quit"):
            self.action_quit_safely()
            return
        self.busy = True
        self._ever_started = True
        self.controller.cancel_event.clear()
        self.query_one("#prompt", Input).value = ""
        self.query_one("#prompt", Input).disabled = True
        self.query_one("#send", Button).disabled = True
        await self.add_message("user", text)
        self.turn, self.intro = None, None
        if not text.startswith("/") or text in ("/demo", "/retry"):
            self.turn = ChatMessage("assistant")
            await self.query_one("#conversation", VerticalScroll).mount(self.turn)
            self.intro = Static("正在分析项目文件和模块依赖……", classes="message-body")
            await self.turn.mount(self.intro)
        self._status("Working", BLUE)
        self.activity = "Analyzing dependencies..."
        self.run_worker(lambda: self._dispatch(text), thread=True, exclusive=True, name="agent-command")

    def _dispatch(self, text):
        try:
            keep_open = self.controller.handle(text)
            self.bridge.events.put(("command_done", {"keep_open": keep_open}))
        except Exception as exc:
            self.bridge.event("fatal", {"message": "执行或记录失败，已停止后续行动：" + str(exc)})

    @on(Input.Submitted, "#prompt")
    async def input_submitted(self, event):
        await self.submit(event.value)

    @on(Button.Pressed, "#send")
    async def send_pressed(self):
        await self.submit(self.query_one("#prompt", Input).value)

    async def drain_events(self):
        # 只在 UI 主线程修改 Widget。同步 Agent 与确认回调不阻塞输入/重绘。
        for _ in range(40):
            try:
                kind, data = self.bridge.events.get_nowait()
            except queue.Empty:
                break
            await self.receive(kind, data)
        if self.confirmation and self.confirmation.answered.is_set() and isinstance(self.screen, PermissionDialog):
            self.screen.dismiss(False)

    async def receive(self, kind, data):
        if kind == "session":
            self._metadata()
            if self.session_id != data["session"]:
                self.session_id = data["session"]
                await self.load_history()
                if self.controller.session.unknown_actions():
                    self._status("Needs review", "#d7ac65")
                elif self.controller.agent.runtime.active:
                    self._status("Incomplete", "#d7ac65")
                else:
                    self._status("Ready")
        elif kind == "info":
            await self.add_message("assistant", data["message"])
        elif kind == "model":
            self.activity = "Analyzing dependencies..."
            self._status("Thinking", BLUE)
        elif kind == "plan":
            running = next((s for s in data["steps"] if s["status"] == "running"), None)
            if running and self.intro:
                self.intro.update("正在执行：" + running["goal"])
        elif kind == "plan_detail":
            table = Table(expand=True, box=None, padding=(0, 1))
            for name in ("Step", "Status", "Goal", "Evidence"):
                table.add_column(name)
            for s in data["steps"]:
                table.add_row(s["id"], s["status"], s["goal"], ", ".join(s["evidence"]))
            await self.add_message("assistant", "当前计划")
            await self.query_one("#conversation", VerticalScroll).mount(Static(table, classes="plan-detail"))
        elif kind == "tool_start":
            if not self.turn:
                self.turn = ChatMessage("assistant")
                await self.query_one("#conversation", VerticalScroll).mount(self.turn)
            tool = ToolLine(data)
            await self.turn.mount(tool)
            self.tool_lines.append(tool)
            self.pending_tool = tool
            self.activity = "Executing " + data["name"] + "..."
            self._status("Executing", BLUE)
        elif kind == "tool_result":
            if self.pending_tool:
                self.pending_tool.finish(data)
                self.pending_tool = None
        elif kind == "permission":
            if self.controller.cancel_event.is_set():
                data.answer(False)
                return
            self.confirmation = data
            self._status("Permission", "#d7ac65")
            self.activity = "Waiting for your approval..."
            self.push_screen(PermissionDialog(data), self.permission_answered)
        elif kind == "result":
            if self.intro:
                self.intro.display = False
            if self.turn:
                await self.turn.mount(Static(Markdown(data["reply"]), classes="message-body final-answer"))
            else:
                await self.add_message("assistant", data["reply"])
            self._status("Ready" if data["completed"] else "Cancelled" if data["stop_reason"] == "cancelled" else "Incomplete",
                         GREEN if data["completed"] else "#d7ac65")
        elif kind in ("command_done", "fatal"):
            self.busy = False
            self.activity = ""
            self.query_one("#activity", Static).update("")
            self.confirmation = None
            self.fatal = kind == "fatal"
            if self.fatal:
                self.exit_code = 3
                await self.add_message("assistant", data["message"])
                self._status("Stopped", "#e98585")
            else:
                self.query_one("#prompt", Input).disabled = False
                self.query_one("#send", Button).disabled = False
                self.query_one("#prompt", Input).focus()
                if self.state == "Cancelling":
                    self._status("Cancelled", "#d7ac65")
                elif self.state in ("Working", "Thinking", "Executing", "Permission"):
                    self._status("Ready")
            if kind == "command_done" and (not data["keep_open"] or self.quit_after_cancel):
                self.action_quit_safely()
            else:
                self.set_timer(0.1, self.capture)
        self.scroll_bottom()

    def permission_answered(self, approved):
        if self.confirmation:
            self.confirmation.answer(bool(approved))
            self.confirmation = None
        if self.busy:
            self._status("Working", BLUE)

    def animate(self):
        if self.busy:
            spinner = "◐◓◑◒"[self.spinner_index % 4]
            self.spinner_index += 1
            self.query_one("#activity", Static).update(Text(spinner + "  " + self.activity, style=BLUE))

    def action_cancel(self):
        if not self.busy:
            self.query_one("#prompt", Input).value = ""
            return
        self.controller.cancel_event.set()
        if self.confirmation:
            self.confirmation.answer(False)
        self._status("Cancelling", "#d7ac65")
        self.activity = "Cancelling... waiting for the current request to return."

    def capture(self):
        if not self.is_running or not self.screen_stack:
            return
        self.bridge.latest_svg = self.export_screenshot(title="MiniAgent v1.2")

    def action_quit_safely(self):
        if self.busy:
            self.quit_after_cancel = True
            self.action_cancel()
            return
        self.capture()
        self.exit()
