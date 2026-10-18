"""PermissionPolicy 测试：策略表、默认拒绝、resource 决策、可注入确认回调。"""
import pytest

import permissions
from permissions import DEFAULT_POLICY, PermissionPolicy
from tools import get_tool_names


def test_every_registered_tool_has_policy():
    for name in get_tool_names():
        assert name in permissions.POLICIES


def test_policies_table():
    assert permissions.POLICIES == {
        "calculator": "allow",
        "list_files": "allow", "search_text": "allow", "read_file_range": "allow", "apply_patch": "ask",
        "system_info": "allow",
        "read_file": "ask",
        "write_file": "ask",
        "shell": "resource",
    }


def test_default_policy_is_deny():
    assert DEFAULT_POLICY == "deny"
    assert PermissionPolicy().get_policy("nope") == "deny"


def test_unknown_tool_defaults_to_deny():
    policy = PermissionPolicy(confirm=lambda n, a: True)
    allowed, denial = policy.check("nope", {})
    assert allowed is False
    assert denial.status == "denied"
    assert denial.error_code == "POLICY_DENIED"


def test_audit_rejects_missing_policy():
    with pytest.raises(RuntimeError):
        PermissionPolicy(policies={"calculator": "allow"})


def test_audit_rejects_resource_without_decision():
    with pytest.raises(RuntimeError):
        PermissionPolicy(policies={
            "calculator": "allow",
        "list_files": "allow", "search_text": "allow", "read_file_range": "allow", "apply_patch": "ask", "system_info": "allow",
            "read_file": "ask", "write_file": "ask", "shell": "resource",
        }, resource_policies={})


def test_allow_tool_passes_without_confirm():
    calls = []
    policy = PermissionPolicy(confirm=lambda n, a: calls.append(n) or True)
    allowed, denial = policy.check("calculator", {"a": 1, "b": 2, "operation": "add"})
    assert allowed is True and denial is None
    assert calls == []  # allow 不触发确认


def test_shell_resource_allow_pwd_no_confirm():
    calls = []
    policy = PermissionPolicy(confirm=lambda n, a: calls.append(n) or True)
    allowed, denial = policy.check("shell", {"command": "pwd", "args": []})
    assert allowed is True and denial is None
    assert calls == []


def test_shell_resource_deny_destructive():
    policy = PermissionPolicy(confirm=lambda n, a: True)
    allowed, denial = policy.check("shell", {"command": "rm", "args": ["-rf", "x"]})
    assert allowed is False
    assert "拒绝" in denial.message


def test_shell_resource_ask_python_invokes_confirm(ws, make_file):
    make_file("script.py", "print('hi')")
    calls = []
    approve = PermissionPolicy(confirm=lambda n, a: calls.append(n) or True)
    allowed, denial = approve.check("shell", {"command": "python", "args": ["script.py"]})
    assert allowed is True and denial is None
    assert calls == ["shell"]  # python 是 ask，触发确认

    reject = PermissionPolicy(confirm=lambda n, a: False)
    allowed, denial = reject.check("shell", {"command": "python", "args": ["script.py"]})
    assert allowed is False
    assert denial.error_code == "USER_DENIED"


def test_ask_write_confirm_approve_and_reject():
    approve = PermissionPolicy(confirm=lambda n, a: True)
    allowed, denial = approve.check("write_file", {"path": "a.txt", "content": "hi"})
    assert allowed is True and denial is None

    reject = PermissionPolicy(confirm=lambda n, a: False)
    allowed, denial = reject.check("write_file", {"path": "a.txt", "content": "hi"})
    assert allowed is False
    assert denial.error_code == "USER_DENIED"


def test_terminal_confirm_eof_denies(monkeypatch):
    def _eof(prompt):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    policy = PermissionPolicy()  # 无注入 → 终端确认
    allowed, denial = policy.check("read_file", {"path": "a.txt"})
    assert allowed is False
    assert denial.error_code == "USER_DENIED"
