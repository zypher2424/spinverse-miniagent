"""工具具体实现：calculator / read_file / write_file / system_info。

每个函数接收已解析的参数，返回 ToolResult；所有错误都作为 ToolResult 返回，不抛异常。
文件工具通过共享的 tools.workspace.WORKSPACE 做路径与读写检查。

本模块只 import results 与 tools.workspace，不 import main / agent / runtime / permissions。
"""
import os
import platform
import shutil
import socket
import subprocess
import sys

from results import ToolResult
from tools import workspace


# ---------- calculator ----------

def calculator(operation: str, a: float, b: float) -> ToolResult:
    """对两个数做四则运算 a <op> b（不使用 eval）。"""
    try:
        a = float(a)
        b = float(b)
    except (TypeError, ValueError):
        return ToolResult("error", "参数错误：a 和 b 必须是数字",
                          error_code="INVALID_ARGUMENTS")
    if operation == "add":
        value = a + b
    elif operation == "subtract":
        value = a - b
    elif operation == "multiply":
        value = a * b
    elif operation == "divide":
        if b == 0:
            return ToolResult("error", "错误：除数不能为 0", error_code="DIVISION_BY_ZERO")
        value = a / b
    else:
        return ToolResult("error", f"不支持的运算：{operation}",
                          error_code="INVALID_ARGUMENTS")
    return ToolResult("success", str(value),
                      data={"operation": operation, "a": a, "b": b, "result": value})


# ---------- 文件工具（共享 Workspace） ----------

def read_file(path: str) -> ToolResult:
    """读取工作区内的 UTF-8 文本文件（超长截断）。"""
    outcome = workspace.WORKSPACE.read_text(path)
    if not outcome.ok:
        return _from_workspace(outcome)
    return ToolResult("success", f"已读取 {path}",
                      data={"path": path, "content": outcome.value})


def write_file(path: str, content: str) -> ToolResult:
    """写入工作区内的 UTF-8 文本文件（不隐式创建父目录）。"""
    outcome = workspace.WORKSPACE.write_text(path, content)
    if not outcome.ok:
        return _from_workspace(outcome)
    return ToolResult("success", f"已写入 {outcome.value} 字节到 {path}",
                      data={"path": path, "bytes_written": outcome.value})


def _from_workspace(outcome) -> ToolResult:
    """把 Workspace 的失败结果转成 ToolResult（边界拒绝→denied，其余→error）。"""
    return ToolResult("denied" if outcome.denied else "error",
                      outcome.error, error_code=outcome.error_code or "WORKSPACE_ERROR")


def describe_write(args) -> str:
    """生成权限确认用的 write_file 详情（目标 / 是否覆盖 / 内容预览）。"""
    path = args.get("path", "") if isinstance(args, dict) else ""
    content = args.get("content", "") if isinstance(args, dict) else ""
    resolved = workspace.WORKSPACE.resolve(path, action="写入")
    if not resolved.ok:
        return resolved.error or "路径无效"
    target = resolved.value
    existed = target.exists()
    exists = "是（将被覆盖）" if existed else "否（将新建）"
    preview = content[:60].replace("\n", "\\n")
    if len(content) > 60:
        preview += "..."
    lines = []
    if existed:
        lines.append("WARNING: existing file will be overwritten")
    lines.append(f"[permission] file: {path}")
    lines.append(f"[permission] exists: {exists}")
    lines.append(f"[permission] preview: {preview}")
    return "\n".join(lines)


# ---------- system_info ----------

def _sh(*cmd: str):
    """执行只读系统命令，返回去空白输出；失败返回 None，不抛异常。"""
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return None


def _sysctl(keys) -> dict:
    """用一次 sysctl 调用读取多个键（仅 macOS）；失败返回空 dict。"""
    data: dict = {}
    if sys.platform.startswith("darwin"):
        try:
            out = subprocess.run(["/usr/sbin/sysctl", "-n", *keys],
                                 capture_output=True, text=True, timeout=5, check=False)
            if out.returncode == 0:
                values = out.stdout.splitlines()
                if len(values) == len(keys):
                    data = dict(zip(keys, values))
        except Exception:
            pass
    return data


def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} PB"


def _accelerator() -> str:
    try:
        import torch
    except Exception:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def system_info() -> ToolResult:
    """返回本机系统与硬件环境信息。"""
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = "N/A"
    cores = os.cpu_count() or 0
    try:
        load = " / ".join(f"{x:.2f}" for x in os.getloadavg())
    except Exception:
        load = "N/A"
    try:
        disk = shutil.disk_usage("/")
        disk_text = (f"total {_fmt_bytes(disk.total)}, used {_fmt_bytes(disk.used)}, "
                     f"free {_fmt_bytes(disk.free)}")
    except Exception:
        disk_text = "N/A"
    mac = _sysctl(["hw.model", "machdep.cpu.brand_string"])
    data = {
        "hostname": hostname,
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "model": mac.get("hw.model", "N/A"),
        "chip": mac.get("machdep.cpu.brand_string", platform.machine()),
        "cpu_logical": cores,
        "loadavg": load,
        "disk": disk_text,
        "python": sys.version.split()[0],
        "accelerator": _accelerator(),
    }
    text = "\n".join(f"{k}: {v}" for k, v in data.items())
    return ToolResult("success", text, data=data)
