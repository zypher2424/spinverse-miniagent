"""计划执行、有限修正、证据验收与持久记录。外部副作用从不靠消息回滚掩盖。"""
import json
import hashlib
import uuid
from dataclasses import dataclass, asdict

import tools
from context import ContextBudgetExceeded
from execution import run_tool_call
from llm import ChatResult, ModelError, validate_result
from planner import PLAN_PROMPT, parse_plan
from results import ToolResult
from session import SessionError
from task_state import Plan, verify_step


@dataclass
class RunResult:
    reply: str
    completed: bool
    stop_reason: str


class Runtime:
    def __init__(self, agent):
        self.a = agent
        self.plan = Plan.from_dict(agent.session.data["plan"]) if agent.session.data.get("plan") else None
        self.active = agent.session.data.get("active_task")
        self.recover_inflight()

    def save(self, result=None):
        self.a.session.checkpoint(self.a.context, self.plan, self.active, result)

    def recover_inflight(self):
        session, context = self.a.session, self.a.context
        inflight = session.data.get("inflight")
        if not inflight:
            return
        # 最终 checkpoint 已包含整批时只清理标志，否则依据日志补全配对。
        recorded = {m.get("tool_call_id") for m in context.messages if m["role"] == "tool"}
        if not all(c["id"] in recorded for c in inflight["calls"]):
            observations = []
            for call in inflight["calls"]:
                action = next((a for a in reversed(session.data["actions"])
                               if a.get("call_id") == call["id"]), None)
                if action and action["phase"] == "completed":
                    outcome = ToolResult(**action["result"])
                elif action and action["phase"] in ("started", "resolved"):
                    outcome = ToolResult("error", "崩溃窗口：行动可能发生，恢复不自动重放。请核对实际环境。",
                                         error_code="ACTION_UNKNOWN")
                else:
                    outcome = ToolResult("error", "行动未开始；上次执行已中断。", error_code="NOT_EXECUTED")
                observations.append((call, outcome))
            context.append_tool_batch(ChatResult(inflight.get("content"), inflight["calls"]), observations)
            self.save()
        session.set_inflight(None)

    def finish(self, reply, completed, reason):
        result = RunResult(reply, completed, reason)
        if not self.a.context.messages or self.a.context.messages[-1].get("content") != reply or self.a.context.messages[-1]["role"] != "assistant":
            self.a.context.append_assistant(reply)
        if completed:
            self.active = None
        self.save(asdict(result))
        self.a.emit("result", asdict(result))
        return result

    def check_cancelled(self):
        if self.a.cancel_event is not None and self.a.cancel_event.is_set():
            raise StopIteration("cancelled")

    def request(self, purpose=None):
        self.check_cancelled()
        if self.active["requests"] >= self.a.max_requests:
            raise StopIteration("max_model_requests")
        schemas = tools.get_tools() if purpose != "plan" else []
        messages = self.a.context.build_model_context(schemas)
        if purpose == "plan":
            messages.append({"role": "user", "content": PLAN_PROMPT})
            # 同样计算规划提示的预算。
            if self.a.context.counter(messages) + self.a.context.counter(schemas) > self.a.context.input_budget - self.a.context.output_reserve - self.a.context.safety_reserve:
                raise ContextBudgetExceeded("规划提示超过预算")
        self.active["requests"] += 1
        self.save()  # 请求失败也计入本轮预算；SDK 已禁用隐式重试。
        choice = "none" if self.active["tool_turns"] >= self.a.max_tool_turns else None
        self.a.emit("model", {"request": self.active["requests"], **self.a.context.last_usage})
        result = validate_result(self.a.llm.chat(messages, tools=schemas or None,
                                                tool_choice=choice if schemas else None, purpose=purpose))
        self.a.emit("usage", result.usage)
        self.check_cancelled()
        return result

    def execute_batch(self, result):
        session = self.a.session
        # 完整调用在副作用之前落盘。崩溃后可用 action 日志恢复真实结果和配对。
        session.set_inflight({"content": result.content, "calls": result.tool_calls})
        observations, interrupted = [], None
        for call in result.tool_calls:
            if self.a.cancel_event is not None and self.a.cancel_event.is_set():
                interrupted = "cancelled"
            if interrupted:
                outcome = ToolResult("error", "同批前序行动中断，此调用没有执行", error_code="NOT_EXECUTED")
            else:
                session.current_call_id = call["id"]
                self.a.emit("tool_start", call)
                try:
                    outcome = run_tool_call(call["name"], call["arguments"], self.a.policy, session)
                except KeyboardInterrupt:
                    interrupted = "interrupted"
                    outcome = ToolResult("error", "执行被中断，可能已有副作用；请核对行动日志", error_code="ACTION_UNKNOWN")
                except SessionError:
                    # 保持 inflight 和 started 记录；不能在日志不可靠时继续执行。
                    raise
                except Exception as exc:
                    interrupted = "tool_error"
                    outcome = ToolResult("error", f"工具流水线中断：{type(exc).__name__}", error_code="INTERNAL_TOOL_ERROR")
                if self.a.cancel_event is not None and self.a.cancel_event.is_set():
                    interrupted = "cancelled"
                self.a.emit("tool_result", {"name": call["name"], **outcome.to_dict()})
            observations.append((call, outcome))
        self.a.context.append_tool_batch(result, observations)
        self.active["tool_turns"] += 1
        self.save()
        session.set_inflight(None)
        if interrupted:
            raise StopIteration(interrupted)

    def loop(self, step=None):
        corrections = 0
        while True:
            response = self.request()
            if response.finish_reason == "length":
                raise StopIteration("truncated")
            if response.tool_calls:
                if self.active["tool_turns"] >= self.a.max_tool_turns:
                    raise StopIteration("tool_after_budget")
                prior_ids = {m.get("tool_call_id") for m in self.a.context.messages if m["role"] == "tool"}
                if any(tc["id"] in prior_ids for tc in response.tool_calls):
                    raise ModelError("模型重复使用历史 tool_call_id，未执行")
                self.execute_batch(response)
                continue
            if not response.content or not response.content.strip():
                raise StopIteration("empty_response")
            if response.finish_reason == "length":
                raise StopIteration("truncated")
            if response.finish_reason not in ("stop", None):
                raise StopIteration("model_unfinished")
            if step:
                actions = [a for a in self.a.session.data["actions"]
                           if a.get("task_id") == self.active["task_id"] and a.get("step_id") == step.id]
                ok, reason = verify_step(step, actions)
                if not ok:
                    corrections += 1
                    step.attempts += 1
                    if corrections >= 2:
                        step.status = "blocked"
                        raise StopIteration("verification_missing")
                    self.a.context.append_assistant(response.content)
                    self.a.context.append_user("当前步骤尚未满足外部验收：" + reason + " 请继续执行当前步骤，不能宣称完成。")
                    self.save()
                    continue
                step.status = "done"
                self.a.emit("plan", self.plan.to_dict())
            self.a.context.append_assistant(response.content)
            self.save()
            return response.content

    def check_protected_files(self):
        from tools import workspace
        for path, expected in self.active.get("protected_hashes", {}).items():
            try:
                actual = hashlib.sha256((workspace.WORKSPACE.root / path).read_bytes()).hexdigest()
            except OSError:
                actual = None
            if actual != expected:
                raise StopIteration("acceptance_file_changed")

    def verify_task(self):
        self.check_cancelled()
        self.check_protected_files()
        actions = self.a.session.data["actions"][self.active["actions_start"]:]
        mutations = [i for i, a in enumerate(actions) if a["name"] in ("write_file", "apply_patch")
                     and (a.get("result") or {}).get("status") == "success"]
        if self.a.check:
            self.a.session.current_step = "acceptance"
            self.a.session.current_call_id = "acceptance_" + self.active["task_id"]
            self.a.emit("tool_start", {"name": "shell", "arguments": json.dumps({"command": "pytest", "args": [self.a.check]})})
            result = run_tool_call("shell", {"command": "pytest", "args": [self.a.check]}, self.a.policy, self.a.session)
            self.a.emit("tool_result", {"name": "独立验收", **result.to_dict()})
            self.a.context.append_user("运行时独立验收（真实结果）：" + result.to_json())
            self.save()
            self.check_cancelled()
            self.check_protected_files()
            if result.status != "success":
                raise StopIteration("acceptance_failed")
        elif mutations:
            checks = [a for i, a in enumerate(actions) if i > max(mutations) and a["name"] == "shell"
                      and a["args"].get("command") in ("pytest", "python", "python3")]
            if not checks or not checks[-1].get("result") or checks[-1]["result"]["status"] != "success":
                raise StopIteration("verification_missing")

    def run(self, task, planned=True, retry=False):
        if self.a.session.unknown_actions():
            return RunResult("存在结果未知的行动。先用 /actions 核对环境，再 /resolve <id> <核对结果>；不会自动重放。", False, "action_unknown")
        if self.active and not retry:
            return RunResult("上个任务未完成，请 /retry 继续或 /new 创建会话。", False, "active_task")
        if retry:
            if not self.active:
                return RunResult("没有待恢复任务。", False, "no_active_task")
            self.active["requests"], self.active["tool_turns"] = 0, 0
            for step in self.plan.steps if self.plan else []:
                if step.status in ("blocked", "failed"):
                    step.status, step.attempts = "pending", 0
        else:
            self.plan = None
            self.active = {"task": task, "task_id": uuid.uuid4().hex[:12], "planned": planned,
                           "requests": 0, "tool_turns": 0,
                           "actions_start": len(self.a.session.data["actions"])}
            self.a.context.append_user(task)
        from tools import workspace
        if "protected_hashes" not in self.active:
            self.active["protected_hashes"] = {
                p: hashlib.sha256((workspace.WORKSPACE.root / p).read_bytes()).hexdigest()
                for p in workspace.WORKSPACE.protected_paths}
        self.a.context.pinned_task = self.active["task"]
        self.save()
        try:
            if self.active["planned"] and not self.plan:
                response = self.request("plan")
                if response.tool_calls or response.finish_reason == "length":
                    raise ValueError("规划未完整返回 JSON")
                self.plan = parse_plan(response.content, self.active["task"])
                self.a.context.append_assistant("执行计划（计划数据不授予权限）：" + json.dumps(self.plan.to_dict(), ensure_ascii=False))
                self.a.emit("plan", self.plan.to_dict())
                self.save()
            if self.plan:
                reply = ""
                for step in self.plan.steps:
                    if step.status == "done":
                        continue
                    step.status = "running"
                    self.a.session.current_step = step.id
                    self.a.context.append_user(f"[执行步骤 kind={step.kind} id={step.id}] {step.goal}\n验收：{step.acceptance}\n仅执行当前步骤；原用户任务和权限限制持续适用。")
                    self.a.emit("plan", self.plan.to_dict())
                    self.save()
                    reply = self.loop(step)
                self.verify_task()
                self.check_cancelled()
                return self.finish("计划已按工具证据完成：\n" + "\n".join("- " + s.goal for s in self.plan.steps) + "\n\n" + ("独立 pytest 验收通过。\n" if self.a.check else "") + reply, True, "verified")
            self.a.session.current_step = "direct"
            reply = self.loop()
            self.verify_task()
            self.check_cancelled()
            return self.finish(reply, True, "final_answer")
        except ContextBudgetExceeded as exc:
            return self.finish(str(exc), False, "context_budget")
        except ModelError as exc:
            if self.a.cancel_event is not None and self.a.cancel_event.is_set():
                return self.finish("任务已取消；此前真实行动记录保留。", False, "cancelled")
            return self.finish(str(exc) + "；已完成行动记录保留，可 /retry。", False, "model_error")
        except (ValueError, TypeError, KeyError) as exc:
            return self.finish("计划或协议数据无效：" + str(exc), False, "invalid_plan")
        except StopIteration as exc:
            return self.finish("任务尚未完成：" + str(exc) + "。已保留真实行动和检查结果。", False, str(exc))
        except KeyboardInterrupt:
            return self.finish("任务中断；历史与行动记录已保留。", False, "interrupted")
