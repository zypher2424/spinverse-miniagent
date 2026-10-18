"""命令策略：同一个 shell 工具，根据 command + args 共同决定 allow / ask / deny。

只接受白名单里的命令形状：
- pwd / ls / git status|diff|log：只读，allow。
- python|python3 项目内 .py、pytest 项目内测试：需确认，ask。
- 其他命令、含命令组合/重定向字符的参数、越界路径：deny。

本模块只做决策，不执行命令，也不询问用户。
依赖 tools.workspace 做路径校验；不 import main / agent / runtime。
"""
import shutil
import sys

from tools import workspace


def _resolve_for_shell(path: str):
    """复用工作区沙箱解析路径；返回 (Path, error)。"""
    result = workspace.WORKSPACE.resolve(path, action="访问")
    if not result.ok:
        return None, result.error
    return result.value, None


def _python_file(path: str):
    """校验目标是已存在的项目内 .py 文件；返回 error 或 None。"""
    target, error = _resolve_for_shell(path)
    if error:
        return error
    if target.suffix != ".py" or not target.is_file():
        return "命令策略拒绝：目标必须是已存在的项目内 .py 文件"
    return None


def _executable(name: str, args: list, decision: str):
    """把命令名解析为绝对路径；本机缺失则拒绝。"""
    resolved = shutil.which(name)
    if not resolved:
        return None, "deny", f"本机没有可用命令：{name}"
    return [resolved, *args], decision, None


def prepare_command(command, args):
    """返回 (实际 argv, 权限策略, 错误)。不执行命令，也不询问用户。

    decision ∈ {allow, ask, deny}；error 非 None 时 argv 为 None。
    """
    if not isinstance(command, str) or not isinstance(args, list):
        return None, "deny", "参数错误：command 必须是字符串，args 必须是字符串列表"
    if len(args) > 20 or any(not isinstance(arg, str) for arg in args):
        return None, "deny", "参数错误：args 最多 20 个字符串"
    if any(len(arg) > 1_024 or any(c in arg for c in "\x00\n\r;|&`<>$") for arg in args):
        return None, "deny", "命令策略拒绝：参数过长或含命令组合/重定向字符"

    if command == "pwd":
        if args:
            return None, "deny", "命令策略拒绝：pwd 不接受参数"
        return _executable("pwd", args, "allow")

    if command == "ls":
        for arg in args:
            if arg in ("-l", "-h", "-lh", "-hl"):
                continue
            if arg.startswith("-"):
                return None, "deny", "命令策略拒绝：ls 只允许 -l / -h；不列隐藏文件"
            target, error = _resolve_for_shell(arg)
            if error:
                return None, "deny", error
            if not target.exists():
                return None, "deny", f"路径不存在：{arg}"
        return _executable("ls", args, "allow")

    if command == "git":
        # 限制参数，防止 -c、--exec、--output 等改变只读命令的含义。
        flags = {
            "status": {"--short", "-s", "--branch", "-b"},
            "diff": {"--stat", "--name-only", "--staged", "--cached"},
            "log": {"--oneline", "-5", "-10"},
        }
        if not args or args[0] not in flags or any(a not in flags[args[0]] for a in args[1:]):
            return None, "deny", "命令策略拒绝：只允许 git status / diff / log 及少量显示参数"
        safe_args = ["--no-pager", "-c", "core.fsmonitor=false", "-c", "core.pager=cat",
                     "-c", "log.showSignature=false", args[0]]
        if args[0] in ("diff", "log"):
            safe_args += ["--no-ext-diff", "--no-textconv"]
        if args[0] == "log":
            safe_args += ["-5"]
        return _executable("git", safe_args + args[1:], "allow")

    if command in ("python", "python3"):
        # 不开放 -c / -m / 任意解释器路径；执行明确的项目内 .py 文件前问人。
        if len(args) != 1 or args[0].startswith("-"):
            return None, "deny", "命令策略拒绝：python 只接受一个项目内 .py 文件"
        error = _python_file(args[0])
        if error:
            return None, "deny", error
        return [sys.executable, *args], "ask", None

    if command == "pytest":
        # pytest 使用当前 Python；禁止模型额外注入插件、配置和命令行选项。
        for arg in args:
            if arg.startswith("-"):
                return None, "deny", "命令策略拒绝：pytest 只接受项目内测试文件路径"
            error = _python_file(arg)
            if error:
                return None, "deny", error
        return [sys.executable, "-m", "pytest", "-q", *args], "ask", None

    return None, "deny", f"命令策略拒绝：未知或禁止的命令 {command}"
