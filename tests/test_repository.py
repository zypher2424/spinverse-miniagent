import hashlib
import pytest
from execution import run_tool_call
from permissions import PermissionPolicy
from tools.repository import digest


def call(name, args, confirm=lambda n, a: True):
    return run_tool_call(name, args, PermissionPolicy(confirm=confirm))


def test_search_range_patch_pipeline(ws, make_file):
    file = make_file("src/math.py", "def add(a, b):\n    return a - b\n")
    assert call("list_files", {}) .data["files"] == ["src/math.py"]
    assert call("search_text", {"query": "def add"}).data["matches"][0]["line"] == 1
    read = call("read_file_range", {"path": "src/math.py", "start_line": 1, "end_line": 2})
    before = file.read_text()
    result = call("apply_patch", {"path": "src/math.py", "old_text": "a - b", "new_text": "a + b", "expected_sha256": read.data["sha256"]})
    assert result.status == "success"
    assert file.read_text() == before.replace("a - b", "a + b")
    assert "-    return a - b" in result.data["diff"]


@pytest.mark.parametrize("content,old,expected", [("same same", "same", "PATCH_NOT_UNIQUE"), ("changed", "old", "STALE_CONTENT")])
def test_patch_refuses_ambiguous_and_stale_without_confirmation(ws, make_file, content, old, expected):
    file = make_file("file.txt", content)
    confirms = []
    result = call("apply_patch", {"path": "file.txt", "old_text": old, "new_text": "new", "expected_sha256": digest(content if expected != "STALE_CONTENT" else "old")}, lambda n,a: confirms.append(n) or True)
    assert result.error_code == expected
    assert file.read_text() == content and not confirms


def test_patch_rechecks_after_preview(ws, make_file):
    file = make_file("file.txt", "old")
    def confirm(n, a):
        file.write_text("external edit")
        return True
    result = call("apply_patch", {"path": "file.txt", "old_text": "old", "new_text": "new", "expected_sha256": digest("old")}, confirm)
    assert result.error_code == "STALE_CONTENT"
    assert file.read_text() == "external edit"


@pytest.mark.parametrize("name", ["secret/.hidden/file.txt", ".config/settings.py", ".env", "hidden.env/file.txt"])
def test_nested_hidden_content_is_not_exposed(ws, make_file, name):
    make_file(name, "private")
    assert call("read_file_range", {"path": name}).status == "denied"
    assert call("search_text", {"query": "private"}).data["matches"] == []


def test_symlink_and_protected_aliases(ws, make_file, tmp_path):
    file = make_file("test_check.py", "old")
    ws.protected_paths.add("test_check.py")
    (ws.root / "alias.py").symlink_to(file)
    for path in ("test_check.py", "./test_check.py", "alias.py"):
        assert call("write_file", {"path": path, "content": "new"}).error_code == "PROTECTED_FILE"
        assert call("apply_patch", {"path": path, "old_text": "old", "new_text": "new", "expected_sha256": digest("old")}).error_code == "PROTECTED_FILE"
    assert file.read_text() == "old"


def test_range_and_search_limits(ws, make_file):
    make_file("lines.txt", "\n".join("hit " + str(i) for i in range(300)))
    result = call("search_text", {"query": "hit", "limit": 2})
    assert len(result.data["matches"]) == 2 and result.data["truncated"]
    assert call("read_file_range", {"path": "lines.txt", "end_line": 300}).status == "error"
    assert call("read_file_range", {"path": "lines.txt", "start_line": True}).status == "error"
