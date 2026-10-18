"""Tool Registry：注册、Schema、参数与资源校验、Recheck 执行。

对外接口：
- get_tools() -> 所有工具 Schema（给模型）
- get_tool(name) / get_tool_names()
- validate_call(name, args) -> ToolResult | None
    未知工具→denied；参数/Schema/资源非法→error 或 denied；合法→None。
    **必须在任何权限询问之前调用**：无效参数先拒绝，不问人。
- prepare_action(name, args) -> (prepared, ToolResult | None)
    「准备行动」：把参数归一化为可执行形态（shell 的 argv / 文件工具的解析路径）。
- execute_call(name, args) -> ToolResult
    **执行前复检**：再次完整校验（防 TOCTOU），然后分发到实现函数。

执行顺序契约（与 execution.py 对齐）：
  Parse → validate_call（参数+资源）→ prepare_action → Permission →
  execute_call（Recheck + Execute）→ ToolResult

本模块不 import main / agent / runtime / permissions。
"""
import json
import math

from results import ToolResult
from tools import workspace
from tools.implementations import (
    calculator,
    read_file,
    system_info,
    write_file,
)
# 不用 `from tools.shell import shell`——同名函数会遮蔽 tools.shell 子模块属性。
import tools.shell as _shell_module
from tools.shell_policy import prepare_command
from tools.schemas import TOOLS
from tools.repository import list_files, search_text, read_file_range, apply_patch, validate_repository_call, preview_patch

_IMPLEMENTATIONS = {
    "shell": _shell_module.shell,
    "list_files": list_files, "search_text": search_text,
    "read_file_range": read_file_range, "apply_patch": apply_patch,
    "calculator": calculator,
    "read_file": read_file,
    "system_info": system_info,
    "write_file": write_file,
}

_SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}
_SCHEMA_NAMES = set(_SCHEMAS)
_REGISTRY_NAMES = set(_IMPLEMENTATIONS)
if _SCHEMA_NAMES != _REGISTRY_NAMES:
    raise RuntimeError(
        "Tool schema 与 Registry 不一致："
        f"schema={sorted(_SCHEMA_NAMES)} registry={sorted(_REGISTRY_NAMES)}"
    )


def get_tools() -> list:
    """返回所有工具的 JSON Schema 列表。"""
    return TOOLS


def get_tool(name: str):
    """按名称查找工具实现函数；未注册返回 None。"""
    return _IMPLEMENTATIONS.get(name)


def get_tool_names() -> list:
    """返回全部已注册工具名（排序后）。"""
    return sorted(_IMPLEMENTATIONS)


# ---------- Schema 校验 ----------

def _validate_schema(name: str, args: dict):
    """按 schema 校验：必填项、类型（number/string/array）、枚举。返回错误文案或 None。"""
    params = _SCHEMAS[name]
    properties = params.get("properties", {})

    for req in params.get("required", []):
        if req not in args:
            return f"工具 {name} 缺少必需参数：{req}（需要 {params.get('required')}）"

    if params.get("additionalProperties") is False:
        unknown = set(args) - set(properties)
        if unknown:
            return f"工具 {name} 收到未知参数：{sorted(unknown)}"

    for key, value in args.items():
        spec = properties.get(key)
        if not spec:
            continue
        if "enum" in spec and value not in spec["enum"]:
            return f"工具 {name} 参数 {key} 取值非法：{value}（可选 {spec['enum']}）"
        expected = spec.get("type")
        if expected == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return f"工具 {name} 参数 {key} 应为数字，实际为 {type(value).__name__}"
        elif expected == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"参数 {key} 必须是整数"
        elif expected == "string" and not isinstance(value, str):
            return f"工具 {name} 参数 {key} 应为字符串，实际为 {type(value).__name__}"
        elif expected == "array":
            items = spec.get("items", {})
            if not isinstance(value, list):
                return f"工具 {name} 参数 {key} 应为数组，实际为 {type(value).__name__}"
            if items.get("type") == "string" and any(not isinstance(v, str) for v in value):
                return f"工具 {name} 参数 {key} 必须是字符串数组"
    return None


# ---------- 每工具语义 / 资源校验 ----------

def _validate_calculator(args: dict):
    for field in ("a", "b"):
        value = args[field]
        try:
            if not math.isfinite(value):
                return ToolResult("error", f"参数错误：{field} 必须是有限数字。",
                                  error_code="INVALID_ARGUMENTS")
        except (OverflowError, TypeError):
            return ToolResult("error", f"参数错误：{field} 超出数字范围。",
                              error_code="INVALID_ARGUMENTS")
    return None


def _validate_read(args: dict):
    return _check_workspace(workspace.WORKSPACE.validate_read(args["path"]))


def _validate_write(args: dict):
    return _check_workspace(
        workspace.WORKSPACE.validate_write(args["path"], args["content"]))


def _validate_shell(args: dict):
    # 命令策略是 shell 的形状+资源校验：命令与参数共同参与。
    _argv, _decision, error = prepare_command(args["command"], args["args"])
    if error is None:
        return None
    if error.startswith("参数错误"):
        return ToolResult("error", error, error_code="INVALID_ARGUMENTS")
    return ToolResult("denied", error, error_code="COMMAND_DENIED")


_VALIDATORS = {
    "calculator": _validate_calculator,
    "read_file": _validate_read,
    "write_file": _validate_write,
    "shell": _validate_shell,
    **{n: (lambda args, n=n: validate_repository_call(n, args)) for n in ("list_files", "search_text", "read_file_range", "apply_patch")},
}


def _check_workspace(outcome) -> ToolResult | None:
    """把 Workspace 结果转为校验结果：通过→None，失败→ToolResult。"""
    if outcome.ok:
        return None
    return ToolResult("denied" if outcome.denied else "error",
                      outcome.error, error_code=outcome.error_code or "WORKSPACE_ERROR")


def validate_call(name, args) -> ToolResult | None:
    """硬校验：未知工具 / 参数形状 / Schema / 资源。合法返回 None。

    必须在权限询问之前调用；未知工具默认拒绝。
    """
    if not isinstance(name, str) or name not in _IMPLEMENTATIONS:
        return ToolResult("denied", f"未知工具，拒绝执行：{name}", error_code="UNKNOWN_TOOL")
    if not isinstance(args, dict):
        return ToolResult("error", "参数错误：工具参数必须是 JSON 对象",
                          error_code="INVALID_ARGUMENTS")
    error = _validate_schema(name, args)
    if error:
        return ToolResult("error", error, error_code="INVALID_ARGUMENTS")
    validator = _VALIDATORS.get(name)
    if validator:
        return validator(args)
    return None


# ---------- 准备行动 ----------

def prepare_action(name, args):
    """把参数归一化为可执行形态，返回 (prepared, ToolResult | None)。

    - shell: {"argv": [...], "decision": allow|ask|deny}
    - read_file/write_file: {"path": <解析后的绝对路径字符串>}
    - calculator/system_info: {}
    失败时返回 (None, ToolResult)。
    """
    if name == "apply_patch":
        preview, error = preview_patch(**args)
        if error:
            return None, error
        return {k: v for k, v in preview.items() if k not in ("target", "updated")}, None

    if name == "shell":
        argv, decision, error = prepare_command(args["command"], args["args"])
        if error:
            return None, ToolResult("denied" if decision == "deny" else "error",
                                    error, error_code="COMMAND_DENIED")
        return {"argv": argv, "decision": decision}, None

    if name == "read_file":
        outcome = workspace.WORKSPACE.validate_read(args["path"])
        if not outcome.ok:
            return None, _check_workspace(outcome)
        return {"path": str(outcome.value)}, None

    if name == "write_file":
        outcome = workspace.WORKSPACE.validate_write(args["path"], args["content"])
        if not outcome.ok:
            return None, _check_workspace(outcome)
        return {"path": str(outcome.value)}, None

    return {}, None


# ---------- 执行 ----------

def execute_call(name, args) -> ToolResult:
    """Recheck + Execute：执行前再完整校验一次，然后分发到实现函数。"""
    error = validate_call(name, args)
    if error is not None:
        return error
    implementation = _IMPLEMENTATIONS.get(name)
    if implementation is None:  # validate 已保证存在，此处为防御
        return ToolResult("denied", f"未知工具，拒绝执行：{name}", error_code="UNKNOWN_TOOL")
    try:
        result = implementation(**args)
    except TypeError as exc:
        return ToolResult("error", f"工具 {name} 参数错误：{exc}",
                          error_code="INVALID_ARGUMENTS")
    except Exception as exc:  # 未预期异常也转成结果，不向调用方抛
        return ToolResult("error", f"工具 {name} 执行失败：{exc}",
                          error_code="INTERNAL_TOOL_ERROR")
    if not isinstance(result, ToolResult):
        return ToolResult("error", f"工具 {name} 未返回 ToolResult",
                          error_code="INTERNAL_TOOL_ERROR")
    return result
