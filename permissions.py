"""PermissionPolicy：工具执行前的权限判定，确认回调可注入。

策略类型：
- "allow"：自动执行。
- "ask"  ：执行前调用确认回调（终端或注入的 callable）。
- "deny" ：永远禁止。
- "resource"：按工具参数做资源级决策（目前仅 shell，经 shell_policy.prepare_command）。

关键规则：
- 未配置策略的工具默认 deny（DEFAULT_POLICY）。
- 启动时校验 registry ⊆ policies；声明 resource 的工具必须注册 resource 决策。
- check() 必须在 Validate 之后调用——无效参数不会走到这里，也就不会被询问。

确认回调注入：PermissionPolicy(confirm=callable(name, args) -> bool)。
不传时使用终端确认；非交互 / EOF 视为拒绝。
本模块不 import main / agent / runtime。
"""
import json
import sys

from results import ToolResult
from tools import get_tool_names
from tools.implementations import describe_write
from tools.shell import describe_shell
from tools.shell_policy import prepare_command

DEFAULT_POLICY = "deny"

POLICIES = {
    "calculator": "allow",
    "list_files": "allow", "search_text": "allow",
    "read_file_range": "allow", "apply_patch": "ask",
    "system_info": "allow",
    "read_file": "ask",
    "write_file": "ask",
    "shell": "resource",  # 资源级：allow（pwd/ls/git 只读）/ ask（python/pytest）/ deny
}

# 按工具名注册「ask 确认详情」渲染器：输入 args，返回要展示的详情行。
def describe_patch(args):
    from tools.repository import preview_patch
    preview, error = preview_patch(**args)
    return error.message if error else preview["diff"]


ASK_DETAILS = {
    "apply_patch": describe_patch,
    "write_file": describe_write,
    "shell": describe_shell,
}


def _shell_resource(args):
    """shell 的资源级决策：返回 (decision, error)。不执行命令，不询问用户。"""
    _argv, decision, error = prepare_command(args.get("command"), args.get("args"))
    return decision, error


RESOURCE_POLICIES = {
    "shell": _shell_resource,
}


class PermissionPolicy:
    """权限判定器。policies / resource_policies / confirm 均可注入，便于离线测试。"""

    def __init__(self, policies=None, resource_policies=None, confirm=None):
        self.policies = dict(POLICIES if policies is None else policies)
        self.resource_policies = dict(
            RESOURCE_POLICIES if resource_policies is None else resource_policies)
        self._confirm = confirm
        self._audit()

    def _audit(self) -> None:
        missing = sorted(set(get_tool_names()) - set(self.policies))
        if missing:
            raise RuntimeError(
                f"以下已注册工具缺少权限策略，请在 policies 中显式注册：{missing}")
        for name, policy in self.policies.items():
            if policy not in ("allow", "ask", "deny", "resource"):
                raise RuntimeError(f"无效权限策略：{name}={policy}")
            if policy == "resource" and name not in self.resource_policies:
                raise RuntimeError(f"工具 {name} 声明 resource 策略，但未注册 resource 决策")

    def get_policy(self, name: str) -> str:
        """返回工具策略；未配置的工具用默认 deny。"""
        return self.policies.get(name, DEFAULT_POLICY)

    def check(self, name, args) -> "tuple[bool, ToolResult | None]":
        """判定是否允许执行。

        返回 (allowed, denial)：
        - allowed=True 时 denial 为 None；
        - allowed=False 时 denial 是回填给模型的 denied ToolResult。
        """
        policy = self.get_policy(name)

        if policy == "resource":
            resource_policy = self.resource_policies.get(name)
            if resource_policy is None:
                return False, ToolResult("denied", f"权限拒绝：工具 {name} 未注册资源级策略",
                                         error_code="POLICY_DENIED")
            decision, error = resource_policy(args)
            if error:
                return False, ToolResult("denied", error, error_code="POLICY_DENIED")
            if decision == "allow":
                return True, None
            if decision == "deny":
                return False, ToolResult("denied", "权限拒绝：命令策略拒绝本次执行",
                                         error_code="POLICY_DENIED")
            if decision != "ask":
                return False, ToolResult("denied", "资源策略无效", error_code="POLICY_DENIED")

        elif policy == "allow":
            return True, None

        elif policy == "deny":
            return False, ToolResult("denied", "权限拒绝：该工具被策略禁止",
                                     error_code="POLICY_DENIED")

        return self._ask(name, args)

    # ---------- ask ----------

    def _ask(self, name, args) -> "tuple[bool, ToolResult | None]":
        if self._confirm is not None:
            approved = bool(self._confirm(name, args))
        else:
            approved = self._terminal_confirm(name, args)
        if approved:
            return True, None
        return False, ToolResult("denied", "权限拒绝：用户拒绝本次执行。",
                                 error_code="USER_DENIED")

    def _terminal_confirm(self, name, args) -> bool:
        """默认确认：打印详情后读取终端输入。EOF / 非交互视为拒绝。"""
        print(f"[permission] Agent wants to call: {name}")
        detail = ASK_DETAILS.get(name)
        if detail:
            print(detail(args))
        else:
            print("[permission] arguments: " + json.dumps(args, ensure_ascii=False))
        try:
            answer = input("Allow? [y/N]: ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().lower() in ("y", "yes")


# 模块级默认策略实例（使用终端确认）。
def default_policy() -> PermissionPolicy:
    return PermissionPolicy()
