import copy
import json
import subprocess
import sys
from pathlib import Path
import pytest

from agent import Agent
from context import Context
from llm import ChatResult, DemoModel, ModelError
from permissions import PermissionPolicy
from session import Session, SessionError
from tools.repository import digest


class Scripted:
    model = "fake"
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0
    def chat(self, *a, **kw):
        self.calls += 1
        item = next(self.responses)
        if isinstance(item, BaseException):
            raise item
        return item


def tc(id, name, args):
    return {"id": id, "name": name, "arguments": json.dumps(args)}


def app(ws, tmp_path, model, **kwargs):
    session = Session.create(tmp_path / "sessions", ws.root, {"config": "test"})
    agent = Agent(model, PermissionPolicy(confirm=lambda n,a: True), session, **kwargs)
    return agent, session


def test_full_demo_with_real_patch_pytest_and_checkpoint(ws, make_file, tmp_path):
    make_file("arithmetic.py", "def add(a,b):\n    return a - b\n")
    test = make_file("test_arithmetic.py", "from arithmetic import add\ndef test_add():\n    assert add(2,3)==5\n")
    original = test.read_bytes()
    ws.protected_paths.add("test_arithmetic.py")
    agent, session = app(ws, tmp_path, DemoModel(), check="test_arithmetic.py")
    try:
        result = agent.run("fix add")
        assert result.completed, result
        assert all(s.status == "done" and s.evidence for s in agent.runtime.plan.steps)
        assert all(a["phase"] == "completed" for a in session.data["actions"])
        assert test.read_bytes() == original
        sid, count = session.id, len(session.data["actions"])
        session.close()
        restored = Session.load(tmp_path / "sessions", sid, ws.root, {"config": "test"})
        try:
            resumed = Agent(Scripted(), PermissionPolicy(), restored)
            assert resumed.runtime.active is None
            assert len(restored.data["actions"]) == count
            assert restored.data["result"]["completed"]
        finally:
            restored.close()
    finally:
        session.close()


def test_model_failure_keeps_successful_side_effect_and_pairs(ws, make_file, tmp_path):
    model = Scripted(ChatResult(None, [tc("write1", "write_file", {"path": "a.txt", "content": "written"})]), ModelError("offline"))
    agent, session = app(ws, tmp_path, model)
    try:
        result = agent.run("write", planned=False)
        assert not result.completed and result.stop_reason == "model_error"
        assert (ws.root / "a.txt").read_text() == "written"
        Context.validate(agent.context.messages)
        assert session.data["actions"][0]["result"]["status"] == "success"
    finally:
        session.close()


def test_step_cannot_finish_on_model_claim_without_evidence(ws, tmp_path):
    plan = json.dumps({"task": "read", "steps": [{"id": "1", "goal": "read", "kind": "read", "acceptance": "actual read"}]})
    agent, session = app(ws, tmp_path, Scripted(ChatResult(plan), ChatResult("done"), ChatResult("done")))
    try:
        result = agent.run("read")
        assert not result.completed and result.stop_reason == "verification_missing"
        assert agent.runtime.plan.steps[0].status == "blocked"
    finally:
        session.close()


def test_independent_failure_cannot_be_overridden_by_model(ws, make_file, tmp_path):
    make_file("test_check.py", "def test_no():\n    assert False\n")
    agent, session = app(ws, tmp_path, Scripted(ChatResult("everything passed")), check="test_check.py")
    try:
        result = agent.run("check", planned=False)
        assert not result.completed and result.stop_reason == "acceptance_failed"
        assert session.data["actions"][-1]["result"]["data"]["returncode"] == 1
    finally:
        session.close()


@pytest.mark.parametrize("response,reason", [(ChatResult(""), "empty_response"), (ChatResult("partial", finish_reason="length"), "truncated")])
def test_unfinished_model_output(ws, tmp_path, response, reason):
    agent, session = app(ws, tmp_path, Scripted(response))
    try:
        result = agent.run("hi", planned=False)
        assert result.stop_reason == reason and not result.completed
    finally:
        session.close()


def test_model_budget_forces_close_and_does_not_execute_more(ws, tmp_path):
    call = ChatResult(None, [tc("1", "calculator", {"a": 1, "b": 2, "operation": "add"})])
    agent, session = app(ws, tmp_path, Scripted(call), max_requests=1)
    try:
        result = agent.run("calc", planned=False)
        assert result.stop_reason == "max_model_requests"
        assert len(session.data["actions"]) == 1
    finally:
        session.close()


def test_crash_after_effect_before_record_is_unknown_and_not_replayed(ws, make_file, tmp_path):
    agent, session = app(ws, tmp_path, Scripted())
    active = {"task": "write", "task_id": "t", "planned": False, "actions_start": 0, "requests": 0, "tool_turns": 0}
    agent.runtime.active = active
    agent.context.append_user("write")
    agent.runtime.save()
    call = tc("c", "write_file", {"path": "count.txt", "content": "1"})
    session.set_inflight({"content": None, "calls": [call]})
    session.current_call_id = "c"
    aid = session.prepare("write_file", {"path": "count.txt", "content": "1"}, {})
    session.started(aid)
    make_file("count.txt", "1")  # 模拟外部操作已发生，但完成记录尚未落盘。
    sid = session.id
    session.close()
    restored = Session.load(tmp_path / "sessions", sid, ws.root, {"config": "test"})
    try:
        resumed = Agent(Scripted(), PermissionPolicy(), restored)
        result = resumed.run("", retry=True)
        assert result.stop_reason == "action_unknown"
        assert (ws.root / "count.txt").read_text() == "1"
        assert len(restored.data["actions"]) == 1
        Context.validate(resumed.context.messages)
        restored.resolve(aid, "已读取文件，内容为 1，写入实际发生")
        assert not restored.unknown_actions()
    finally:
        restored.close()


def test_completed_action_recovered_without_reexecution(ws, tmp_path):
    agent, session = app(ws, tmp_path, Scripted())
    agent.context.append_user("calc")
    agent.runtime.save()
    call = tc("c", "calculator", {"a": 1, "b": 2, "operation": "add"})
    session.set_inflight({"content": None, "calls": [call]})
    session.current_call_id = "c"
    from execution import run_tool_call
    run_tool_call("calculator", {"a": 1, "b": 2, "operation": "add"}, PermissionPolicy(), session)
    sid = session.id
    session.close()
    restored = Session.load(tmp_path / "sessions", sid, ws.root, {"config": "test"})
    try:
        resumed = Agent(Scripted(), PermissionPolicy(), restored)
        assert len(restored.data["actions"]) == 1
        assert json.loads(resumed.context.messages[-1]["content"])["data"]["result"] == 3
        Context.validate(resumed.context.messages)
    finally:
        restored.close()


def test_logging_failure_prevents_execution(ws, tmp_path, monkeypatch):
    from execution import run_tool_call
    agent, session = app(ws, tmp_path, Scripted())
    def broken(data):
        raise SessionError("disk unavailable")
    monkeypatch.setattr(session, "commit", broken)
    try:
        with pytest.raises(SessionError):
            run_tool_call("write_file", {"path": "a.txt", "content": "x"}, PermissionPolicy(confirm=lambda n,a: True), session)
        assert not (ws.root / "a.txt").exists()
    finally:
        session.close()


def test_session_lock_config_and_corruption(ws, tmp_path):
    session = Session.create(tmp_path / "sessions", ws.root, {"config": "one"})
    sid = session.id
    with pytest.raises(SessionError):
        Session.load(tmp_path / "sessions", sid, ws.root, {"config": "one"})
    session.close()
    with pytest.raises(SessionError, match="配置变化"):
        Session.load(tmp_path / "sessions", sid, ws.root, {"config": "two"})
    session.path.write_text('{broken')
    with pytest.raises(SessionError, match="损坏"):
        Session.load(tmp_path / "sessions", sid, ws.root, {"config": "one"})


def test_cli_demo_subprocess_and_resume(tmp_path):
    root = Path(__file__).resolve().parents[1]
    cmd = [sys.executable, str(root / "main.py"), "--demo", "--yes", "--session-dir", str(tmp_path / "sessions"), "--json"]
    first = subprocess.run(cmd + ["--task", "fix add"], capture_output=True, text=True, timeout=30)
    assert first.returncode == 0, first.stdout + first.stderr
    events = [json.loads(line) for line in first.stdout.splitlines()]
    sid = events[0]["data"]["session"]
    before = json.loads((tmp_path / "sessions" / (sid + ".json")).read_text())
    second = subprocess.run(cmd + ["--resume", sid], input="/status\n/actions\n/exit\n", capture_output=True, text=True, timeout=10)
    assert second.returncode == 0, second.stdout + second.stderr
    after = json.loads((tmp_path / "sessions" / (sid + ".json")).read_text())
    assert before["actions"] == after["actions"]


def test_shell_cannot_silently_rewrite_acceptance(ws, make_file, tmp_path):
    make_file("test_check.py", "def test_no():\n    assert False\n")
    make_file("cheat.py", "from pathlib import Path\nPath('test_check.py').write_text('def test_yes():\\n    assert True\\n')\n")
    ws.protected_paths.add("test_check.py")
    model = Scripted(ChatResult(None, [tc("1", "shell", {"command": "python", "args": ["cheat.py"]})]), ChatResult("done"))
    agent, session = app(ws, tmp_path, model, check="test_check.py")
    try:
        result = agent.run("check", planned=False)
        assert not result.completed and result.stop_reason == "acceptance_file_changed"
    finally:
        session.close()


def test_truncated_tool_response_does_not_execute(ws, tmp_path):
    model = Scripted(ChatResult(None, [tc("1", "write_file", {"path": "a.txt", "content": "x"})], "length"))
    agent, session = app(ws, tmp_path, model)
    try:
        result = agent.run("write", planned=False)
        assert not result.completed and result.stop_reason == "truncated"
        assert not (ws.root / "a.txt").exists()
    finally:
        session.close()


def test_crash_before_started_has_paired_not_executed_result(ws, tmp_path):
    agent, session = app(ws, tmp_path, Scripted())
    agent.context.append_user("write")
    agent.runtime.save()
    call = tc("c", "write_file", {"path": "never.txt", "content": "x"})
    session.set_inflight({"content": None, "calls": [call]})
    session.current_call_id = "c"
    session.prepare("write_file", {"path": "never.txt", "content": "x"}, {})
    sid = session.id
    session.close()
    restored = Session.load(tmp_path / "sessions", sid, ws.root, {"config": "test"})
    try:
        resumed = Agent(Scripted(), PermissionPolicy(), restored)
        assert not (ws.root / "never.txt").exists()
        assert json.loads(resumed.context.messages[-1]["content"])["error_code"] == "NOT_EXECUTED"
        assert not restored.unknown_actions()
        Context.validate(resumed.context.messages)
    finally:
        restored.close()


def test_partial_multi_call_interrupt_preserves_first_result(ws, make_file, tmp_path, monkeypatch):
    from tools import registry
    make_file("a.txt", "old")
    original = registry._IMPLEMENTATIONS["calculator"]
    def interrupt(**kwargs):
        raise KeyboardInterrupt
    monkeypatch.setitem(registry._IMPLEMENTATIONS, "calculator", interrupt)
    calls = [tc("1", "write_file", {"path": "a.txt", "content": "new"}),
             tc("2", "calculator", {"operation": "add", "a": 1, "b": 2}),
             tc("3", "write_file", {"path": "never.txt", "content": "x"})]
    agent, session = app(ws, tmp_path, Scripted(ChatResult(None, calls)))
    try:
        result = agent.run("batch", planned=False)
        assert not result.completed and result.stop_reason == "interrupted"
        assert (ws.root / "a.txt").read_text() == "new"
        assert not (ws.root / "never.txt").exists()
        messages = [m for m in agent.context.messages if m["role"] == "tool"]
        assert [json.loads(m["content"])["status"] for m in messages] == ["success", "error", "error"]
        assert session.unknown_actions()
        Context.validate(agent.context.messages)
    finally:
        session.close()
