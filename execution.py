"""统一工具调用流水线（本里程碑的可执行入口）。

六步：
  1. Parse           解析参数（JSON 字符串或已解析 dict）
  2. Validate        校验参数与资源（registry.validate_call）——无效参数不询问权限
  3. Prepare         准备行动（registry.prepare_action）
  4. Permission     权限判断（PermissionPolicy.check，确认回调可注入）
  5. Recheck+Execute 执行前复检并执行（registry.execute_call）
  6. Result          返回 ToolResult

本模块只编排 tools 与 permissions，不 import main / agent / runtime。
"""
import json

from permissions import PermissionPolicy
from results import ToolResult
import tools


def parse_arguments(arguments) -> "tuple[dict | None, ToolResult | None]":
    """把工具调用参数解析为 dict。接受已解析的 dict 或 JSON 字符串。"""
    if isinstance(arguments, dict):
        return arguments, None
    if arguments is None or arguments == "":
        return {}, None
    if not isinstance(arguments, str):
        return None, ToolResult("error", "工具参数必须是 JSON 对象或 JSON 字符串",
                                error_code="INVALID_ARGUMENTS")
    try:
        parsed = json.loads(arguments)
    except json.JSONDecodeError:
        return None, ToolResult("error", "工具参数无法解析为 JSON",
                                error_code="INVALID_ARGUMENTS")
    if not isinstance(parsed, dict):
        return None, ToolResult("error", "工具参数必须是一个对象",
                                error_code="INVALID_ARGUMENTS")
    return parsed, None


def run_tool_call(name, arguments, policy=None, journal=None) -> ToolResult:
    """按六步流水线执行一次工具调用，返回 ToolResult。

    arguments 可以是 JSON 字符串（模型线协议）或已解析的 dict（手写 ToolCall）。
    policy 为 None 时使用默认终端确认策略；测试应注入带有确认回调的 PermissionPolicy。
    """
    if policy is None:
        policy = PermissionPolicy()

    def rejected(result, args):
        if journal:
            aid = journal.prepare(name, args, {"rejected_before_permission": True})
            journal.completed(aid, result)
        return result

    # 1. Parse
    args, error = parse_arguments(arguments)
    if error is not None:
        return rejected(error, {"raw_arguments": arguments})

    # 2. Validate 参数与资源（无效参数不询问权限）
    error = tools.validate_call(name, args)
    if error is not None:
        return rejected(error, args)

    # 3. Prepare 准备行动
    _prepared, error = tools.prepare_action(name, args)
    if error is not None:
        return rejected(error, args)

    action_id = journal.prepare(name, args, _prepared) if journal else None

    # 4. Permission
    allowed, denial = policy.check(name, args)
    if not allowed:
        if journal:
            journal.completed(action_id, denial)
        return denial

    if journal:
        journal.started(action_id)
    # 5. Recheck + Execute
    result = tools.execute_call(name, args)
    if journal:
        journal.completed(action_id, result)
    return result
