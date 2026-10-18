"""有界文件发现、逐行搜索、分段读取和带哈希前置条件的单文件 Patch。"""
import difflib
import hashlib
import os
import tempfile
from pathlib import Path

from results import ToolResult
from tools import workspace

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_SCAN_FILES = 1000
IGNORED = {"node_modules", "__pycache__", "venv", "dist", "build"}


def fail(message, code="INVALID_ARGUMENTS", denied=False):
    return ToolResult("denied" if denied else "error", message, error_code=code)


def load_text(path):
    checked = workspace.WORKSPACE.validate_read(path)
    if not checked.ok:
        return None, None, fail(checked.error, checked.error_code, checked.denied)
    target = checked.value
    try:
        raw = target.read_bytes()
        return target, raw.decode("utf-8"), None
    except (OSError, UnicodeError) as exc:
        return None, None, fail(str(exc), "READ_FAILED")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _files(path="."):
    resolved = workspace.WORKSPACE.resolve(path)
    if not resolved.ok:
        return None, fail(resolved.error, resolved.error_code, resolved.denied)
    if not resolved.value.is_dir():
        return None, fail("搜索起点必须是已有目录", "NOT_A_DIRECTORY")
    return resolved.value, None


def walk_files(root):
    count = 0
    for parent, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in IGNORED
                         and not Path(parent, d).is_symlink())
        for name in sorted(files):
            rel = Path(parent, name).relative_to(workspace.WORKSPACE.root).as_posix()
            if not workspace.WORKSPACE.resolve(rel).ok:
                continue
            count += 1
            if count > MAX_SCAN_FILES:
                return
            yield rel


def list_files(path=".", limit=100):
    root, error = _files(path)
    if error:
        return error
    files = []
    for rel in walk_files(root):
        files.append(rel)
        if len(files) > limit:
            break
    return ToolResult("success", "文件发现完成（扫描最多 1000 个文件）", data={
        "files": files[:limit], "truncated": len(files) > limit or len(files) >= MAX_SCAN_FILES,
        "scan_limit": MAX_SCAN_FILES})


def search_text(query, path=".", limit=50):
    root, error = _files(path)
    if error:
        return error
    hits, scanned, truncated = [], 0, False
    for rel in walk_files(root):
        scanned += 1
        _target, text, error = load_text(rel)
        if error:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if query in line:
                if len(hits) >= limit:
                    truncated = True
                    break
                hits.append({"path": rel, "line": n, "text": line[:500]})
        if truncated:
            break
    return ToolResult("success", "文本搜索完成（字面量匹配）", data={
        "matches": hits, "scanned_files": scanned,
        "truncated": truncated or scanned >= MAX_SCAN_FILES, "scan_limit": MAX_SCAN_FILES})


def read_file_range(path, start_line=1, end_line=120):
    _target, text, error = load_text(path)
    if error:
        return error
    lines = text.splitlines(keepends=True)
    if start_line > max(1, len(lines)):
        return fail("起始行超出文件范围")
    content = "".join(lines[start_line - 1:end_line])
    truncated = len(content) > 12000
    return ToolResult("success", f"已读取 {path}:{start_line}-{min(end_line, len(lines))}", data={
        "path": path, "start_line": start_line, "end_line": min(end_line, len(lines)),
        "total_lines": len(lines), "content": content[:12000],
        "sha256": digest(text), "truncated": truncated})


def preview_patch(path, old_text, new_text, expected_sha256):
    target, text, error = load_text(path)
    if error:
        return None, error
    if target.relative_to(workspace.WORKSPACE.root).as_posix() in workspace.WORKSPACE.protected_paths:
        return None, fail("该文件是独立验收文件，禁止修改", "PROTECTED_FILE", True)
    if digest(text) != expected_sha256:
        return None, fail("文件已变化：请重新读取哈希并定位", "STALE_CONTENT")
    if not old_text or text.count(old_text) != 1:
        return None, fail("old_text 必须非空且在文件中恰好匹配一处", "PATCH_NOT_UNIQUE")
    updated = text.replace(old_text, new_text, 1)
    if "\0" in new_text or len(updated.encode("utf-8")) > MAX_FILE_BYTES:
        return None, fail("修改超出文本大小限制", "WRITE_REJECTED", True)
    diff = "".join(difflib.unified_diff(text.splitlines(True), updated.splitlines(True),
                                     fromfile=path, tofile=path))
    return {"path": path, "sha256_before": digest(text), "sha256_after": digest(updated),
            "diff": diff[:12000], "diff_truncated": len(diff) > 12000,
            "updated": updated, "target": target}, None


def apply_patch(path, old_text, new_text, expected_sha256):
    # 确认后重新检查文件哈希；不把预览当成应用成功。
    prepared, error = preview_patch(path, old_text, new_text, expected_sha256)
    if error:
        return error
    target = prepared.pop("target")
    updated = prepared.pop("updated")
    temp = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=target.parent, delete=False) as f:
            temp = f.name
            f.write(updated.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, target.stat().st_mode & 0o777)
        # 最后再比较一次，降低预览到替换间的陈旧内容风险；非 OS 级锁。
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected_sha256:
            return fail("替换前文件再次变化，请重新读取", "STALE_CONTENT")
        os.replace(temp, target)
        temp = None
    except OSError as exc:
        return fail(str(exc), "WRITE_FAILED")
    finally:
        if temp is not None:
            os.unlink(temp)
    return ToolResult("success", f"Patch 已应用：{path}", data=prepared)


def validate_repository_call(name, args):
    if name in ("list_files", "search_text"):
        _root, error = _files(args.get("path", "."))
        if error:
            return error
        limit = args.get("limit", 50 if name == "search_text" else 100)
        if not 1 <= limit <= 200:
            return fail("limit 必须为 1–200")
        if name == "search_text" and not 1 <= len(args["query"]) <= 500:
            return fail("query 必须为 1–500 字符")
    elif name == "read_file_range":
        start, end = args.get("start_line", 1), args.get("end_line", 120)
        if start < 1 or end < start or end - start >= 200:
            return fail("行号从 1 开始，每次最多读取 200 行")
        return _workspace_error(workspace.WORKSPACE.validate_read(args["path"]))
    elif name == "apply_patch":
        try:
            if any(len(args[k].encode("utf-8")) > 65536 for k in ("old_text", "new_text")):
                return fail("Patch 片段最多 64 KiB")
            return preview_patch(**args)[1]
        except UnicodeError:
            return fail("Patch 必须是 UTF-8 文本")
    return None


def _workspace_error(result):
    return None if result.ok else fail(result.error, result.error_code, result.denied)
