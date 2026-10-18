"""Workspace：文件工具共享的工作区边界与读写检查。

所有文件相关工具（read_file / write_file / search 等）都通过同一个 Workspace
实例解析路径、校验边界、执行读写，避免各处各自实现一套沙箱规则。

安全边界（硬规则，用户同意也不能绕过）：
- 只接受相对工作区根的路径，拒绝绝对路径与任何 '..' 段。
- 解析后必须仍位于工作区根内部（防符号链接逃逸）。
- 隐藏文件/目录、.env、.git/ 一律拒绝。

模块级单例 WORKSPACE 指向本版本目录；测试可 monkeypatch 成临时目录。

本模块是最底层，不 import 其他项目模块。
"""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class WsResult:
    """Workspace 检查/读写的结果。

    - value: 解析后的 Path / 读到的文本 / 写入字节数；失败时为 None。
    - error: 失败说明；成功为 None。
    - error_code: 失败时的机器可读错误码。
    - denied: True 表示硬边界拒绝（权限层应报 denied），False 表示普通执行错误（报 error）。
    """

    value: object = None
    error: Optional[str] = None
    error_code: Optional[str] = None
    denied: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


class Workspace:
    """工作区：根目录 + 路径边界 + 读写限制。"""

    MAX_READ_CHARS = 2_000
    MAX_WRITE_BYTES = 64 * 1024

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.protected_paths = set()

    # ---------- 路径解析 ----------

    def resolve(self, path, action: str = "访问") -> WsResult:
        """把相对路径解析为工作区内的 Path，越界/隐藏/敏感路径返回 denied。"""
        if not isinstance(path, str) or not path.strip():
            return WsResult(error="参数错误：path 必须是非空字符串",
                            error_code="INVALID_ARGUMENTS")
        raw = Path(path)
        if raw.is_absolute():
            return WsResult(error=f"拒绝{action}：只允许相对工作区的路径",
                            error_code="PATH_DENIED", denied=True)
        if any(part == ".." for part in raw.parts):
            return WsResult(error=f"拒绝{action}：路径不允许包含 '..'",
                            error_code="PATH_DENIED", denied=True)

        try:
            target = (self.root / raw).resolve()
        except OSError:
            return WsResult(error=f"路径无效：{path}", error_code="PATH_DENIED", denied=True)

        if target != self.root and self.root not in target.parents:
            return WsResult(error=f"拒绝{action}：只允许访问工作区 {self.root} 内的文件",
                            error_code="PATH_DENIED", denied=True)

        if any(p.startswith(".") or ".env" in p for p in (*raw.parts, *target.relative_to(self.root).parts) if p != "."):
            return WsResult(error=f"拒绝{action}：不允许访问隐藏/敏感文件 {target.name}",
                            error_code="PATH_DENIED", denied=True)

        if ".git" in {p.name for p in target.parents}:
            return WsResult(error=f"拒绝{action}：不允许访问 .git/ 下的文件",
                            error_code="PATH_DENIED", denied=True)

        return WsResult(value=target)

    # ---------- 读取 ----------

    def validate_read(self, path) -> WsResult:
        """读取前的资源检查：边界 + 存在性 + 文本性。"""
        resolved = self.resolve(path, action="读取")
        if not resolved.ok:
            return resolved
        target: Path = resolved.value
        if not target.exists():
            return WsResult(error=f"文件不存在：{path}", error_code="FILE_NOT_FOUND")
        if not target.is_file():
            return WsResult(error=f"路径不是文件：{path}", error_code="NOT_A_FILE")
        try:
            if target.stat().st_size > 2 * 1024 * 1024:
                return WsResult(error="文件超过 2 MiB 读取上限", error_code="FILE_TOO_LARGE")
            data = target.read_bytes()
        except OSError as exc:
            return WsResult(error=f"读取失败：{exc}", error_code="READ_FAILED")
        if b"\x00" in data:
            return WsResult(error=f"拒绝读取：{path} 不是文本文件",
                            error_code="BINARY_FILE", denied=True)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return WsResult(error=f"拒绝读取：{path} 不是 UTF-8 文本文件",
                            error_code="NOT_UTF8", denied=True)
        return WsResult(value=target)

    def read_text(self, path) -> WsResult:
        """读取文本（超长截断），返回文本内容。"""
        checked = self.validate_read(path)
        if not checked.ok:
            return checked
        target: Path = checked.value
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            return WsResult(error=f"读取失败：{exc}", error_code="READ_FAILED")
        if len(text) > self.MAX_READ_CHARS:
            text = text[: self.MAX_READ_CHARS] + "\n...(已截断)"
        return WsResult(value=text)

    # ---------- 写入 ----------

    def validate_write(self, path, content) -> WsResult:
        """写入前的资源检查：边界 + 内容（NUL / UTF-8 / 大小）+ 父目录。"""
        resolved = self.resolve(path, action="写入")
        if not resolved.ok:
            return resolved
        target: Path = resolved.value
        if target.relative_to(self.root).as_posix() in self.protected_paths:
            return WsResult(error="验收文件禁止修改", error_code="PROTECTED_FILE", denied=True)

        if not isinstance(content, str):
            return WsResult(error="参数错误：content 必须是字符串",
                            error_code="INVALID_ARGUMENTS")
        if "\0" in content:
            return WsResult(error=f"拒绝写入：{path} 内容包含 NUL 字符",
                            error_code="WRITE_REJECTED", denied=True)
        try:
            size = len(content.encode("utf-8"))
        except UnicodeError:
            return WsResult(error="参数错误：content 必须能编码为 UTF-8",
                            error_code="INVALID_ARGUMENTS")
        if size > self.MAX_WRITE_BYTES:
            return WsResult(
                error=f"写入策略拒绝：最多写入 {self.MAX_WRITE_BYTES} 字节 UTF-8 文本",
                error_code="WRITE_REJECTED", denied=True)
        if target.exists() and not target.is_file():
            return WsResult(error=f"写入失败：目标不是文件 {path}", error_code="NOT_A_FILE")
        if not target.parent.exists():
            return WsResult(error=f"写入失败：父目录不存在 {target.parent}（不会自动创建）",
                            error_code="PARENT_MISSING")
        return WsResult(value=target)

    def write_text(self, path, content) -> WsResult:
        """写入文本，返回写入的字节数。"""
        checked = self.validate_write(path, content)
        if not checked.ok:
            return checked
        target: Path = checked.value
        try:
            target.write_text(content, encoding="utf-8")
        except OSError as exc:
            return WsResult(error=f"写入失败：{exc}", error_code="WRITE_FAILED")
        return WsResult(value=len(content.encode("utf-8")))


# 默认工作区指向本版本目录（tools/workspace.py → parents[1] = v1.2/）。
WORKSPACE = Workspace(Path(__file__).resolve().parents[1])
