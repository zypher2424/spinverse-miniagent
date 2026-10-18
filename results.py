"""统一工具结果协议（从零重建，v0.8 机制的叶子模块）。

设计要点：
- 程序按 status 字段判断状态，绝不搜索 message 里的「错误」「拒绝」等文字来猜。
- data 只允许 JSON 可表示的值：不能放 Path、Exception、非有限浮点数或 SDK 对象。
- tool_call_id 由外层调用消息负责关联，不放进 ToolResult，避免出现两份可能不一致的副本。

本模块不 import 任何上层模块（tools / permissions / runtime / agent / main），是最底层。
"""
import json
import math
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

ToolStatus = Literal["success", "error", "denied", "timeout"]

VALID_STATUSES: tuple[str, ...] = ("success", "error", "denied", "timeout")


def _is_json_value(value: Any) -> bool:
    """判断一个值能否由 JSON 无损表示（拒绝 NaN / Infinity 与非基础类型）。"""
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_value(v) for k, v in value.items())
    return False


@dataclass
class ToolResult:
    """一次工具调用发生了什么。

    字段：
    - status: success / error / denied / timeout
    - message: 给模型看的人类可读说明（成功也可以是简短描述）
    - data: 真实结构化数据（Shell 的 stdout/stderr/returncode 等），必须 JSON 可表示
    - error_code: 非成功结果必须有；成功结果必须为 None
    """

    status: ToolStatus
    message: str
    data: Optional[dict] = None
    error_code: Optional[str] = None

    def __post_init__(self) -> None:
        if self.status not in VALID_STATUSES:
            raise ValueError(f"非法 status：{self.status!r}（可选 {VALID_STATUSES}）")
        if not isinstance(self.message, str):
            raise ValueError("message 必须是字符串")
        if self.status == "success":
            if self.error_code is not None:
                raise ValueError("success 结果的 error_code 必须为 None")
        else:
            if not self.error_code:
                raise ValueError(f"{self.status} 结果必须有非空 error_code")
        if self.data is not None:
            if not isinstance(self.data, dict):
                raise ValueError("data 必须是 dict 或 None")
            if not _is_json_value(self.data):
                raise ValueError("data 含有不可 JSON 序列化的值（Path/Exception/非有限数/其他对象）")

    def to_dict(self) -> dict:
        """转为普通 dict，便于记录或断言。"""
        return {
            "status": self.status,
            "message": self.message,
            "data": self.data,
            "error_code": self.error_code,
        }

    def to_json(self) -> str:
        """转为发给模型/写入日志的 JSON 文本。"""
        return json.dumps(self.to_dict(), ensure_ascii=False)
