"""原子 JSON Checkpoint + 行动日志。started 无 result 表示结果未知，恢复不自动重放。"""
import copy
import fcntl
import hashlib
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

VERSION = "1.2"


class SessionError(Exception):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class Session:
    def __init__(self, directory, session_id, data):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", session_id):
            raise SessionError("无效 session id")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.id, self.data = session_id, data
        self.current_call_id = None
        self.current_step = None
        self.path = self.directory / f"{session_id}.json"
        self.lock = (self.directory / f"{session_id}.lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.lock.close()
            raise SessionError("该会话已在另一个进程打开") from exc

    @classmethod
    def create(cls, directory, workspace, config):
        sid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        data = {"version": VERSION, "workspace": str(Path(workspace).resolve()),
                "config_fingerprint": fingerprint(config), "messages": [], "plan": None,
                "active_task": None, "actions": [], "result": None, "created_at": time.time()}
        session = cls(directory, sid, data)
        session.commit(data)
        return session

    @classmethod
    def load(cls, directory, sid, workspace, config):
        # 先加锁再读取，避免与正在保存的进程竞争。
        session = cls(directory, sid, {})
        try:
            data = json.loads(session.path.read_text(encoding="utf-8"))
            if data["version"] != VERSION or data["workspace"] != str(Path(workspace).resolve()):
                raise SessionError("会话版本或工作区变化，拒绝恢复")
            if data["config_fingerprint"] != fingerprint(config):
                raise SessionError("模型/预算/权限/验收配置变化，拒绝静默恢复；请使用原配置或新建会话")
            from context import Context
            Context.validate(data["messages"])
            if not isinstance(data["actions"], list):
                raise ValueError("actions 非列表")
            session.data = data
            return session
        except (OSError, ValueError, KeyError, TypeError) as exc:
            session.close()
            raise SessionError(f"会话损坏或读取失败，原文件保留：{exc}") from exc
        except SessionError:
            session.close()
            raise

    def commit(self, data):
        candidate = copy.deepcopy(data)
        temp = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self.directory,
                                             delete=False) as f:
                temp = f.name
                os.chmod(temp, 0o600)
                json.dump(candidate, f, ensure_ascii=False, allow_nan=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp, self.path)
            temp = None
            descriptor = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except (OSError, ValueError) as exc:
            raise SessionError(f"Checkpoint 保存失败；停止继续执行：{exc}") from exc
        finally:
            if temp:
                os.unlink(temp)
        self.data = candidate

    def checkpoint(self, context, plan=None, active_task=None, result=None):
        data = copy.deepcopy(self.data)
        data.update(messages=context.messages, plan=plan.to_dict() if plan else None,
                    active_task=active_task, result=result)
        self.commit(data)

    def set_inflight(self, value):
        data = copy.deepcopy(self.data)
        data["inflight"] = value
        self.commit(data)

    def prepare(self, name, args, prepared):
        data = copy.deepcopy(self.data)
        aid = uuid.uuid4().hex[:12]
        data["actions"].append({"id": aid, "name": name, "args": args, "prepared": prepared,
                                "phase": "prepared", "result": None, "time": time.time(),
                                "call_id": self.current_call_id, "step_id": self.current_step,
                                "task_id": (self.data.get("active_task") or {}).get("task_id")})
        self.commit(data)
        return aid

    def transition(self, aid, phase, result=None, note=None):
        data = copy.deepcopy(self.data)
        action = next(a for a in data["actions"] if a["id"] == aid)
        action.update(phase=phase, result=result, note=note)
        self.commit(data)

    def started(self, aid):
        self.transition(aid, "started")

    def completed(self, aid, result):
        self.transition(aid, "completed", result.to_dict())

    def unknown_actions(self):
        return [a for a in self.data["actions"] if a["phase"] == "started"]

    def resolve(self, aid, note):
        if not note.strip() or aid not in {a["id"] for a in self.unknown_actions()}:
            raise SessionError("请指定结果未知的 action id，并填写实际核对结果")
        self.transition(aid, "resolved", note=note)

    def close(self):
        if not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()
