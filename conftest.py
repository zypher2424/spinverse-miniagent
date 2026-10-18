"""pytest 根 conftest：把 v1.2/ 加入 sys.path，并提供工作区/确认回调夹具。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import workspace as workspace_module


@pytest.fixture
def ws(tmp_path, monkeypatch):
    """把共享工作区指向临时目录，隔离文件测试。"""
    instance = workspace_module.Workspace(tmp_path)
    monkeypatch.setattr(workspace_module, "WORKSPACE", instance)
    return instance


@pytest.fixture
def make_file(ws):
    """在临时工作区里创建文件，返回其 Path。"""
    def _make(name, text=""):
        path = ws.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path
    return _make


class RecordingConfirm:
    """可注入的确认回调：记录被询问的工具名，按 approve 返回。"""

    def __init__(self, approve=True):
        self.approve = approve
        self.calls = []

    def __call__(self, name, args):
        self.calls.append(name)
        return self.approve


@pytest.fixture
def confirm_cls():
    return RecordingConfirm
