"""CLI 与全屏 TUI 共用的命令路由；界面不复制 Agent 执行逻辑。"""
import json
import re
import threading
from pathlib import Path

from agent import Agent
from session import Session, SessionError
from tools import workspace as workspace_module

DEMO_TASK = "定位并修复 arithmetic.py 的 add 加法错误，只修改实现，保留测试，再运行独立 pytest 验证。"
HELP = """输入任务即执行；Enter 发送，Ctrl+C 取消当前任务。
/help            命令帮助
/community       自旋宇宙介绍与共创入口
/status          工作区、会话、任务和预算
/plan            计划与证据
/actions         真实行动记录
/history         最近对话
/sessions        保存的会话
/resume ID       恢复会话，不自动执行历史行动
/retry           显式继续未完成任务
/resolve ID 结论  核对结果未知的行动后记录结论
/new             新建会话
/demo            在隔离演示工作区运行练习
/exit            保存退出"""


class CommandRouter:
    def __init__(self, args, model, policy, session, workspace, config, ui):
        self.args, self.model, self.policy, self.session = args, model, policy, session
        self.workspace, self.config, self.ui = workspace, config, ui
        self.cancel_event = threading.Event()
        self.last_result = None
        self.agent = self._agent()

    def _agent(self):
        workspace_module.WORKSPACE.cancel_event = self.cancel_event
        return Agent(self.model, self.policy, self.session, self.ui.event,
                     self.args.max_requests, self.args.max_tool_turns,
                     self.args.context_budget, self.args.check, cancel_event=self.cancel_event)

    def metadata(self):
        return {"workspace": str(self.workspace), "model": self.model.model,
                "session": self.session.id, "automatic": self.args.yes,
                "demo": self.args.demo, "unknown_actions": len(self.session.unknown_actions())}

    def announce(self):
        self.ui.header(self.workspace, self.model.model, self.session.id, self.args.yes)
        if self.args.demo:
            self.ui.info("演示模式 · 脚本模型，工具与检查真实执行。输入 /demo 开始。")
        if self.session.unknown_actions():
            self.ui.info("有结果未知的行动，请先 /actions 核对，再 /resolve ID 结论。")

    def run(self, task, retry=False):
        result = self.agent.run(task, not self.args.direct, retry=retry)
        self.last_result = result
        if result.stop_reason in ("action_unknown", "active_task", "no_active_task"):
            self.ui.event("result", vars(result))
        return result

    def resume(self, sid):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", sid):
            raise SessionError("无效 session id")
        metadata = json.loads((self.args.session_dir / f"{sid}.json").read_text())
        target = Path(metadata["workspace"])
        if not target.is_dir():
            raise SessionError("原工作区已不存在")
        candidate_workspace = workspace_module.Workspace(target)
        if self.args.check:
            checked = candidate_workspace.validate_read(self.args.check)
            if not checked.ok:
                raise SessionError("原验收文件不可读")
            candidate_workspace.protected_paths.add(self.args.check)
        candidate = Session.load(self.args.session_dir, sid, target, self.config)
        previous = workspace_module.WORKSPACE
        workspace_module.WORKSPACE = candidate_workspace
        old_session = self.session
        try:
            self.session = candidate
            agent = self._agent()
        except Exception:
            self.session = old_session
            workspace_module.WORKSPACE = previous
            candidate.close()
            raise
        old_session.close()
        self.workspace, self.agent = target, agent
        self.announce()
        self.ui.info("会话已恢复；没有重放历史行动。未完成任务可 /retry。")

    def handle(self, text):
        text = text.strip()
        if not text:
            return True
        if text in ("/exit", "exit", "quit"):
            return False
        if text == "/help":
            self.ui.info(HELP)
        elif text == "/community":
            self.ui.info("自旋宇宙 · Spinverse\n自旋不息，宇宙无界\nSpin Up Your Universe\n\nAI 落地实战 · 工具与范式 · 前沿雷达 · 项目共创\n官网：https://spinverse.cn/\n合作 / 共创 / 入群：TC_bjtu\nSpin Your Intelligence.")
        elif text == "/plan":
            if self.agent.runtime.plan:
                self.ui.event("plan_detail", self.agent.runtime.plan.to_dict())
            else:
                self.ui.info("尚无计划")
        elif text == "/status":
            self.ui.info(json.dumps({**self.metadata(), "task": self.agent.runtime.active,
                                     "context": self.agent.context.last_usage}, ensure_ascii=False, indent=2))
        elif text == "/actions":
            if not self.session.data["actions"]:
                self.ui.info("尚无行动")
            for action in self.session.data["actions"]:
                result = action.get("result") or {}
                self.ui.info(f"{action['id']}  {action['phase']}  {action['name']}  {result.get('status', '?')}  {result.get('error_code') or ''}  {action.get('note') or ''}")
        elif text == "/history":
            for message in self.agent.context.messages[-12:]:
                self.ui.info(message["role"] + ": " + (message.get("content") or "[tool_calls]")[:1500])
        elif text in ("/sessions", "/resume"):
            paths = sorted(self.args.session_dir.glob("*.json"))[-20:]
            self.ui.info("使用 /resume ID 恢复会话：\n" + "\n".join(p.stem for p in paths))
        elif text.startswith("/resume "):
            try:
                self.resume(text.split(maxsplit=1)[1].strip())
            except (ValueError, OSError, KeyError, SessionError) as exc:
                self.ui.info("恢复失败：" + str(exc))
        elif text == "/new":
            candidate = Session.create(self.args.session_dir, self.workspace, self.config)
            self.session.close()
            self.session = candidate
            self.agent = self._agent()
            self.last_result = None
            self.announce()
        elif text.startswith("/resolve "):
            parts = text.split(maxsplit=2)
            if len(parts) != 3:
                self.ui.info("用法：/resolve ID 实际核对结果")
            else:
                try:
                    self.session.resolve(parts[1], parts[2])
                    self.agent.context.append_user("用户核对未知行动 " + parts[1] + "：" + parts[2] + "。不可自动重放。")
                    self.agent.runtime.save()
                    self.ui.info("已记录核对结论。可 /retry 继续。")
                except SessionError as exc:
                    self.ui.info(str(exc))
        elif text == "/retry":
            self.run("", retry=True)
        elif text == "/demo":
            if self.args.demo:
                self.run(DEMO_TASK)
            else:
                self.ui.info("请使用 ./miniagent --demo 启动隔离演示。")
        elif text.startswith("/"):
            self.ui.info("未知命令，输入 /help 查看帮助。")
        else:
            self.run(text)
        return True

    def close(self):
        self.session.close()
