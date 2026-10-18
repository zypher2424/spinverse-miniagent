"""Registry / Schema / Workspace 校验与执行测试（离线，临时工作区）。"""
import tools
from results import ToolResult


# ---------- Registry 一致性 ----------

def test_registry_consistency():
    assert tools.get_tool_names() == sorted(tools.registry._SCHEMAS)
    for name in tools.get_tool_names():
        assert tools.get_tool(name) is not None


def test_get_tools_returns_nine_schemas():
    names = {t["function"]["name"] for t in tools.get_tools()}
    assert names == {"shell", "calculator", "read_file", "system_info", "write_file", "list_files", "search_text", "read_file_range", "apply_patch"}


def test_shell_schema_structured():
    schema = next(t for t in tools.get_tools() if t["function"]["name"] == "shell")
    params = schema["function"]["parameters"]
    assert params["required"] == ["command", "args"]
    assert params["properties"]["args"]["type"] == "array"
    assert params["additionalProperties"] is False


# ---------- validate_call：无效参数先拒绝 ----------

def test_validate_unknown_tool_denied():
    result = tools.validate_call("nope", {})
    assert result.status == "denied"
    assert result.error_code == "UNKNOWN_TOOL"


def test_validate_args_not_dict():
    result = tools.validate_call("calculator", [1, 2])
    assert result.status == "error"
    assert result.error_code == "INVALID_ARGUMENTS"


def test_validate_missing_required():
    result = tools.validate_call("calculator", {"a": 1})
    assert result.status == "error"
    assert "缺少必需参数" in result.message


def test_validate_rejects_unknown_shell_field():
    result = tools.validate_call("shell", {"command": "pwd", "args": [], "extra": 1})
    assert result.status == "error"
    assert "未知参数" in result.message


def test_validate_calculator_bool_and_infinite():
    assert tools.validate_call("calculator", {"a": True, "b": 2, "operation": "add"}) is not None
    result = tools.validate_call("calculator", {"a": float("inf"), "b": 2, "operation": "add"})
    assert "有限" in result.message
    result = tools.validate_call("calculator", {"a": 1, "b": 2, "operation": "pow"})
    assert "取值非法" in result.message


def test_validate_read_escape_and_hidden_denied(ws):
    for bad in ("../outside.txt", ".env", ".git/config", "/etc/passwd"):
        result = tools.validate_call("read_file", {"path": bad})
        assert result.status == "denied", bad


def test_validate_read_missing_is_error(ws):
    result = tools.validate_call("read_file", {"path": "nope.txt"})
    assert result.status == "error"
    assert result.error_code == "FILE_NOT_FOUND"


def test_validate_read_existing_ok(ws, make_file):
    make_file("a.txt", "hello")
    assert tools.validate_call("read_file", {"path": "a.txt"}) is None


def test_validate_write_nul_and_oversize(ws):
    nul = tools.validate_call("write_file", {"path": "a.txt", "content": "x\x00y"})
    assert nul.status == "denied" and nul.error_code == "WRITE_REJECTED"
    big = tools.validate_call("write_file", {"path": "a.txt", "content": "x" * (64 * 1024 + 1)})
    assert big.status == "denied" and big.error_code == "WRITE_REJECTED"


def test_validate_write_empty_content_ok(ws):
    assert tools.validate_call("write_file", {"path": "a.txt", "content": ""}) is None


def test_validate_write_missing_parent_is_error(ws):
    result = tools.validate_call("write_file", {"path": "sub/a.txt", "content": "hi"})
    assert result.status == "error"
    assert result.error_code == "PARENT_MISSING"


def test_validate_shell_metachars_and_unknown(ws):
    assert "拒绝" in tools.validate_call("shell", {"command": "ls", "args": ["a;rm -rf /"]}).message
    assert tools.validate_call("shell", {"command": "rm", "args": ["-rf", "x"]}).status == "denied"
    assert tools.validate_call("shell", {"command": "pwd", "args": []}) is None


# ---------- prepare_action ----------

def test_prepare_action_shell_returns_argv(ws):
    prepared, error = tools.prepare_action("shell", {"command": "pwd", "args": []})
    assert error is None
    assert prepared["decision"] == "allow"
    assert prepared["argv"][0].endswith("pwd")


def test_prepare_action_shell_deny_returns_result(ws):
    prepared, error = tools.prepare_action("shell", {"command": "curl", "args": ["x"]})
    assert prepared is None
    assert error.status == "denied"


# ---------- execute_call：Recheck + Execute ----------

def test_execute_calculator_success():
    result = tools.execute_call("calculator", {"a": 1, "b": 2, "operation": "add"})
    assert result.status == "success"
    assert result.data["result"] == 3.0


def test_execute_rechecks_invalid_args():
    # 即使绕过 Validate 直接调用 execute_call，Recheck 也会拒绝。
    result = tools.execute_call("calculator", {"a": "bad", "b": 2, "operation": "add"})
    assert result.status == "error"
    assert "应为数字" in result.message


def test_execute_write_then_read_roundtrip(ws):
    written = tools.execute_call("write_file", {"path": "note.txt", "content": "你好"})
    assert written.status == "success"
    assert written.data["bytes_written"] == len("你好".encode("utf-8"))
    read = tools.execute_call("read_file", {"path": "note.txt"})
    assert read.status == "success"
    assert read.data["content"] == "你好"


def test_execute_read_truncates_long_content(ws, make_file):
    make_file("long.txt", "x" * (ws.MAX_READ_CHARS + 500))
    result = tools.execute_call("read_file", {"path": "long.txt"})
    assert result.status == "success"
    assert result.data["content"].endswith("...(已截断)")


def test_execute_shell_pwd_success(ws):
    result = tools.execute_call("shell", {"command": "pwd", "args": []})
    assert result.status == "success"
    assert result.data["returncode"] == 0
    assert result.data["timed_out"] is False
    assert result.data["stdout"].strip() == str(ws.root)


def test_execute_system_info_success():
    result = tools.execute_call("system_info", {})
    assert result.status == "success"
    assert "hostname" in result.data


def test_execute_never_returns_plain_string(ws):
    # 所有分支都必须是 ToolResult。
    for result in (
        tools.execute_call("calculator", {"a": 1, "b": 0, "operation": "divide"}),
        tools.execute_call("read_file", {"path": "nope.txt"}),
    ):
        assert isinstance(result, ToolResult)
