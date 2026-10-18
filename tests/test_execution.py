"""统一流水线测试：手写 ToolCall + 可注入确认回调。

覆盖用户要求的五种场景：正常执行、拒绝、非法路径、非零退出、超时；
并验证「无效参数不询问权限」和「未配置策略默认禁止」。
"""
import pytest

import tools.shell as shell_mod
from execution import run_tool_call
from permissions import PermissionPolicy
from results import ToolResult


def _policy(confirm):
    return PermissionPolicy(confirm=confirm)


# ---------- 1. 正常执行 ----------

def test_normal_execution_calculator():
    result = run_tool_call("calculator", {"a": 2, "b": 3, "operation": "multiply"},
                           _policy(lambda n, a: True))
    assert result.status == "success"
    assert result.data["result"] == 6.0


def test_normal_execution_accepts_json_string_arguments():
    # 模型线协议：arguments 是 JSON 字符串。
    result = run_tool_call("calculator", '{"a": 1, "b": 2, "operation": "add"}',
                           _policy(lambda n, a: True))
    assert result.status == "success"
    assert result.data["result"] == 3.0


def test_shell_ask_path_executes_when_approved(ws, make_file, confirm_cls):
    make_file("ok.py", "print('done')")
    confirm = confirm_cls(approve=True)
    result = run_tool_call("shell", {"command": "python", "args": ["ok.py"]},
                           _policy(confirm))
    assert result.status == "success"
    assert confirm.calls == ["shell"]  # python 需确认
    assert "done" in result.data["stdout"]


# ---------- 2. 拒绝 ----------

def test_write_denied_leaves_no_file(ws, confirm_cls):
    confirm = confirm_cls(approve=False)
    result = run_tool_call("write_file", {"path": "blocked.txt", "content": "x"},
                           _policy(confirm))
    assert result.status == "denied"
    assert result.error_code == "USER_DENIED"
    assert confirm.calls == ["write_file"]
    assert not (ws.root / "blocked.txt").exists()


def test_shell_denied_destructive(ws, confirm_cls):
    confirm = confirm_cls(approve=True)
    result = run_tool_call("shell", {"command": "rm", "args": ["-rf", "x"]},
                           _policy(confirm))
    assert result.status == "denied"
    assert confirm.calls == []  # 硬策略拒绝不询问用户


# ---------- 3. 非法路径（不询问权限） ----------

@pytest.mark.parametrize("bad", ["../outside.txt", ".env", ".git/config", "/etc/passwd"])
def test_illegal_path_denied_without_confirm(ws, confirm_cls, bad):
    confirm = confirm_cls(approve=True)
    result = run_tool_call("read_file", {"path": bad}, _policy(confirm))
    assert result.status == "denied"
    assert confirm.calls == []  # 无效资源先拒绝，不问人


def test_invalid_arguments_skip_permission(ws, confirm_cls):
    confirm = confirm_cls(approve=True)
    result = run_tool_call("calculator", {"a": "x", "b": 2, "operation": "add"},
                           _policy(confirm))
    assert result.status == "error"
    assert confirm.calls == []


def test_unknown_tool_denied_without_confirm(confirm_cls):
    confirm = confirm_cls(approve=True)
    result = run_tool_call("nope", {}, _policy(confirm))
    assert result.status == "denied"
    assert result.error_code == "UNKNOWN_TOOL"
    assert confirm.calls == []


def test_bad_json_arguments_rejected():
    result = run_tool_call("calculator", "{not json", _policy(lambda n, a: True))
    assert result.status == "error"
    assert result.error_code == "INVALID_ARGUMENTS"


# ---------- 4. 非零退出 ----------

def test_shell_nonzero_exit_is_error_with_evidence(ws, make_file, confirm_cls):
    make_file("fail.py", "import sys; sys.stderr.write('assert-failed'); sys.exit(1)")
    confirm = confirm_cls(approve=True)
    result = run_tool_call("shell", {"command": "python", "args": ["fail.py"]},
                           _policy(confirm))
    assert result.status == "error"
    assert result.error_code == "PROCESS_FAILED"
    assert result.data["returncode"] == 1
    assert "assert-failed" in result.data["stderr"]


# ---------- 5. 超时 ----------

def test_shell_timeout_status(ws, make_file, confirm_cls, monkeypatch):
    make_file("slow.py", "import time; time.sleep(5)")
    monkeypatch.setattr(shell_mod, "TIMEOUT_SECONDS", 1)
    confirm = confirm_cls(approve=True)
    result = run_tool_call("shell", {"command": "python", "args": ["slow.py"]},
                           _policy(confirm))
    assert result.status == "timeout"
    assert result.error_code == "EXECUTION_TIMEOUT"
    assert result.data["timed_out"] is True


# ---------- 结果类型 ----------

def test_pipeline_always_returns_tool_result(ws, confirm_cls):
    confirm = confirm_cls(approve=True)
    cases = [
        ("calculator", {"a": 1, "b": 1, "operation": "add"}),
        ("read_file", {"path": "nope.txt"}),
        ("write_file", {"path": ".env", "content": "x"}),
        ("unknown", {}),
    ]
    for name, args in cases:
        assert isinstance(run_tool_call(name, args, _policy(confirm)), ToolResult)
