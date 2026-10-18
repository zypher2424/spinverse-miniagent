"""shell 测试：命令策略决策表 + 真实执行机制（成功/非零/超时/截断/环境剥离）。"""
import sys

import pytest

import tools.shell as shell_mod
from tools.shell import run_command, shell
from tools.shell_policy import prepare_command


# ---------- prepare_command 决策表 ----------

@pytest.mark.parametrize("command,args,expected", [
    ("pwd", [], "allow"),
    ("pwd", ["-l"], "deny"),
    ("ls", [], "allow"),
    ("ls", ["-l"], "allow"),
    ("ls", ["-a"], "deny"),
    ("git", ["status"], "allow"),
    ("git", ["push"], "deny"),
    ("git", ["-c", "x=1", "status"], "deny"),
    ("python", ["-c", "print(1)"], "deny"),
    ("python", ["-m", "pip"], "deny"),
    ("rm", ["-rf", "x"], "deny"),
    ("curl", ["http://x"], "deny"),
])
def test_prepare_command_decisions(command, args, expected):
    argv, decision, error = prepare_command(command, args)
    assert decision == expected, f"{command} {args}: {error}"
    if expected != "deny":
        assert error is None and argv


def test_prepare_command_python_requires_existing_py(ws, make_file):
    make_file("demo/script.py", "print('hi')")
    _argv, decision, error = prepare_command("python", ["demo/script.py"])
    assert decision == "ask" and error is None
    _argv, decision, error = prepare_command("python", ["demo/missing.py"])
    assert decision == "deny" and ".py" in error


def test_prepare_command_pytest_requires_existing_py(ws, make_file):
    make_file("demo/test_x.py", "def test_x(): pass")
    _argv, decision, error = prepare_command("pytest", ["demo/test_x.py"])
    assert decision == "ask" and error is None
    assert prepare_command("pytest", ["-q", "demo/test_x.py"])[1] == "deny"


def test_prepare_command_rejects_meta_characters():
    for bad in ["a;b", "a|b", "a`b`", "a>b", "a$(b)", "a\nb"]:
        assert prepare_command("ls", [bad])[1] == "deny"


def test_prepare_command_rejects_shape_violations():
    assert prepare_command("ls", "not-a-list")[1] == "deny"
    assert prepare_command(None, [])[1] == "deny"
    assert prepare_command("ls", [1, 2])[1] == "deny"
    assert prepare_command("ls", [str(i) for i in range(21)])[1] == "deny"


# ---------- shell() 状态映射 ----------

def test_shell_pwd_success_with_workspace_cwd(ws):
    result = shell("pwd", [])
    assert result.status == "success"
    assert result.data["returncode"] == 0
    assert result.data["stdout"].strip() == str(ws.root)


def test_shell_policy_denied_returns_denied(ws):
    result = shell("rm", ["-rf", "x"])
    assert result.status == "denied"
    assert result.error_code == "COMMAND_DENIED"


def test_shell_nonzero_exit_is_error(ws, make_file):
    make_file("fail.py", "import sys; sys.stderr.write('boom'); sys.exit(3)")
    result = shell("python", ["fail.py"])
    assert result.status == "error"
    assert result.error_code == "PROCESS_FAILED"
    assert result.data["returncode"] == 3
    assert "boom" in result.data["stderr"]


def test_shell_timeout_is_timeout(ws, make_file, monkeypatch):
    make_file("slow.py", "import time; time.sleep(5)")
    monkeypatch.setattr(shell_mod, "TIMEOUT_SECONDS", 1)
    result = shell("python", ["slow.py"])
    assert result.status == "timeout"
    assert result.error_code == "EXECUTION_TIMEOUT"
    assert result.data["timed_out"] is True


# ---------- run_command 机制 ----------

def test_run_command_timeout_kills_process():
    outcome = run_command(["/bin/sleep", "5"], timeout=1)
    assert outcome["timed_out"] is True


def test_run_command_truncates_output():
    script = "print('x' * 5000); import sys; sys.stderr.write('e' * 3000)"
    outcome = run_command([sys.executable, "-c", script], timeout=10)
    assert len(outcome["stdout"]) == shell_mod.MAX_OUTPUT_CHARS
    assert outcome["stdout_truncated"] is True
    assert outcome["stderr_truncated"] is True


def test_run_command_strips_environment():
    script = ("import os, json; print(json.dumps({"
              "'has_key': 'OPENAI_API_KEY' in os.environ, "
              "'path': 'PATH' in os.environ, "
              "'dontwrite': os.environ.get('PYTHONDONTWRITEBYTECODE')}))")
    outcome = run_command([sys.executable, "-c", script], timeout=10)
    import json
    payload = json.loads(outcome["stdout"])
    assert payload["has_key"] is False
    assert payload["path"] is True
    assert payload["dontwrite"] == "1"
