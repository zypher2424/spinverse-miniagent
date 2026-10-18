"""真实 Shell 执行：把 stdout / stderr / returncode 回填为 ToolResult.data。

执行约束：
- 结构化 argv + shell=False，不经过 shell 解析。
- 固定 cwd = 工作区根；环境白名单（不带 API key / PYTHONPATH / pytest 插件）。
- 15 秒超时、stdout/stderr 各自限长 2000 字符并保留截断标记。
- start_new_session=True + 进程组 SIGKILL 清理，避免留下后台子进程。
- 非零退出码、超时都是 observation（ToolResult），不向调用方抛异常。

ToolResult.status 映射：timed_out→timeout；returncode!=0→error(PROCESS_FAILED)；否则 success。
截断不等于失败：returncode=0 且输出被截断仍是 success，但保留截断标记。
本模块不 import main / agent / runtime。
"""
import json
import os
import selectors
import signal
import subprocess
import time

from results import ToolResult
from tools import workspace
from tools.shell_policy import prepare_command

TIMEOUT_SECONDS = 15
MAX_OUTPUT_CHARS = 2_000

_ENV_WHITELIST = ("PATH", "LANG", "LC_ALL", "TMPDIR")


def shell(command, args) -> ToolResult:
    """执行命令，返回带真实输出的 ToolResult。权限确认发生在权限层，不在这里。"""
    argv, decision, error = prepare_command(command, args)
    if error:
        return ToolResult("denied" if decision == "deny" else "error",
                          error, error_code="COMMAND_DENIED")
    if os.name != "posix":
        return ToolResult("error",
                          "执行失败：本课的进程组/输出读取需要 macOS 或 Linux",
                          error_code="UNSUPPORTED_PLATFORM")
    try:
        outcome = run_command(argv, TIMEOUT_SECONDS)
    except OSError as exc:
        return ToolResult("error", f"执行失败：{exc}", error_code="EXECUTION_FAILED")

    if outcome.get("cancelled"):
        return ToolResult("error", "命令已取消；已知输出保留，可能已有副作用。",
                          data=outcome, error_code="CANCELLED")
    if outcome["timed_out"]:
        return ToolResult("timeout", "命令超过时限，已终止进程组。",
                          data=outcome, error_code="EXECUTION_TIMEOUT")
    if outcome["returncode"] != 0:
        return ToolResult("error", f"命令退出码非零：{outcome['returncode']}",
                          data=outcome, error_code="PROCESS_FAILED")
    return ToolResult("success", "命令执行成功。", data=outcome)


def describe_shell(args) -> str:
    """生成权限确认用的 shell 详情（命令 / argv / 工作目录 / 风险提示）。"""
    command = args.get("command", "") if isinstance(args, dict) else ""
    tool_args = args.get("args", []) if isinstance(args, dict) else []
    lines = [
        f"[permission] command: {command}",
        "[permission] args: " + json.dumps(tool_args, ensure_ascii=False),
    ]
    argv, _decision, error = prepare_command(command, tool_args)
    if error:
        lines.append(f"[permission] policy check: {error}")
    else:
        lines.append("[permission] argv: " + json.dumps(argv, ensure_ascii=False))
        lines.append(f"[permission] cwd: {workspace.WORKSPACE.root}")
        lines.append("WARNING: 执行项目代码可能修改文件；15 秒超时，输出截断。")
    return "\n".join(lines)


def run_command(argv, timeout) -> dict:
    """真实执行 argv，返回结构化结果（不抛业务异常）。"""
    env = {k: os.environ[k] for k in _ENV_WHITELIST if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1")

    process = subprocess.Popen(
        argv, cwd=workspace.WORKSPACE.root, shell=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    # UTF-8 一个字符至多 4 字节；只保留有限前缀，其余仍读取并丢弃。
    byte_limit = MAX_OUTPUT_CHARS * 4
    deadline = time.monotonic() + timeout
    timed_out = False
    cancelled = False
    cancel_event = getattr(workspace.WORKSPACE, "cancel_event", None)
    cleanup_deadline = None
    for stream, name in ((process.stdout, "stdout"), (process.stderr, "stderr")):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)

    try:
        while selector.get_map() or process.poll() is None:
            now = time.monotonic()
            if not cancelled and cancel_event is not None and cancel_event.is_set():
                cancelled = True
                _kill_group(process)
                cleanup_deadline = now + 0.5
            if not cancelled and not timed_out and now >= deadline:
                timed_out = True
                _kill_group(process)
                cleanup_deadline = now + 0.5
            if (timed_out or cancelled) and now >= cleanup_deadline:
                break
            for key, _ in selector.select(timeout=0.05):
                try:
                    chunk = os.read(key.fileobj.fileno(), 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                name = key.data
                remaining = byte_limit - len(buffers[name])
                buffers[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    truncated[name] = True
        process.wait(timeout=1)
    finally:
        # 已退出父进程可能留下继承输出管道的子进程；本课不保留后台任务。
        _kill_group(process)
        process.wait(timeout=1)
        selector.close()
        process.stdout.close()
        process.stderr.close()

    outcome = {"returncode": process.returncode, "timed_out": timed_out, "cancelled": cancelled}
    for name, data in buffers.items():
        text = data.decode("utf-8", errors="replace")
        outcome[name] = text[:MAX_OUTPUT_CHARS]
        outcome[f"{name}_truncated"] = truncated[name] or len(text) > MAX_OUTPUT_CHARS
    return outcome


def _kill_group(process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
