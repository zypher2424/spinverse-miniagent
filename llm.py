"""模型传输边界：SDK 对象在此转成普通数据；禁用隐式重试。"""
import json
import uuid
from dataclasses import dataclass, field


class ModelError(Exception):
    pass


@dataclass
class ChatResult:
    content: str | None = None
    tool_calls: list = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)


def validate_result(result):
    ids = [c.get("id") for c in result.tool_calls]
    if len(ids) > 8 or len(set(ids)) != len(ids) or any(not isinstance(i, str) or not i for i in ids):
        raise ModelError("模型返回无效/重复的 tool_call_id 或单批调用过多；未执行任何工具")
    for c in result.tool_calls:
        if not isinstance(c.get("name"), str) or not isinstance(c.get("arguments"), str):
            raise ModelError("模型工具调用协议错误")
    return result


class LLM:
    def __init__(self, config):
        from openai import OpenAI
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key,
                             max_retries=0, timeout=60)
        self.model = config.model

    def chat(self, messages, tools=None, tool_choice=None, purpose=None):
        params = {"model": self.model, "messages": messages, "max_tokens": 2048}
        if tools:
            params["tools"] = tools
        if tool_choice:
            params["tool_choice"] = tool_choice
        try:
            response = self.client.chat.completions.create(**params)
            choice = response.choices[0]
            return validate_result(ChatResult(choice.message.content,
                [{"id": t.id, "name": t.function.name, "arguments": t.function.arguments}
                 for t in (choice.message.tool_calls or [])], choice.finish_reason,
                response.usage.model_dump() if response.usage else {}))
        except ModelError:
            raise
        except Exception as exc:
            # 网关错误正文可能包含请求信息；UI 不直接打印正文或请求头。
            status = getattr(exc, "status_code", None)
            raise ModelError(f"模型请求失败：{type(exc).__name__}" + (f" HTTP {status}" if status else "")) from None


class DemoModel:
    """明确标记的离线脚本模型。只模拟决策，工具、Patch、Shell、Session 都真实执行。"""
    model = "offline-demo (scripted)"

    def chat(self, messages, tools=None, tool_choice=None, purpose=None):
        if purpose == "plan":
            return ChatResult(json.dumps({"task": "修复演示项目算术错误并验证", "steps": [
                {"id": "s1", "kind": "read", "goal": "定位并读取算术函数", "files": ["arithmetic.py"], "acceptance": "获得源码和内容哈希"},
                {"id": "s2", "kind": "edit", "goal": "修正加法实现", "files": ["arithmetic.py"], "acceptance": "有真实 Patch 应用结果"},
                {"id": "s3", "kind": "check", "goal": "运行独立测试", "files": [], "acceptance": "pytest 退出码为零"}]}))
        latest = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=0)
        task = messages[latest].get("content", "")
        recent = messages[latest + 1:]
        observed = []
        all_results = []
        for i, m in enumerate(messages):
            if m["role"] == "tool":
                value = json.loads(m["content"])
                all_results.append(value)
                if i > latest:
                    observed.append(value)
        if any(v["status"] != "success" for v in observed):
            return ChatResult("工具未成功，保留真实结果，任务尚未验证。")
        if "kind=read" in task:
            if not observed:
                return self.calls([("list_files", {}), ("search_text", {"query": "def add"})])
            if not any((r.get("data") or {}).get("sha256") for r in observed):
                return self.calls([("read_file_range", {"path": "arithmetic.py", "start_line": 1, "end_line": 30})])
            return ChatResult("已定位 arithmetic.py，并取得最新哈希。")
        if "kind=edit" in task:
            if observed:
                return ChatResult("已应用单文件 Patch；下一步运行检查。")
            read = next((r["data"] for r in reversed(all_results)
                         if (r.get("data") or {}).get("sha256")), None)
            if not read:
                return self.calls([("read_file_range", {"path": "arithmetic.py"})])
            if "return a - b" not in read["content"]:
                return ChatResult("文件已正确，无需重复修改。")
            return self.calls([("apply_patch", {"path": "arithmetic.py", "old_text": "return a - b", "new_text": "return a + b", "expected_sha256": read["sha256"]})])
        if "kind=check" in task:
            if observed:
                return ChatResult("真实 pytest 已通过。")
            return self.calls([("shell", {"command": "pytest", "args": ["test_arithmetic.py"]})])
        return ChatResult("离线演示请使用 --demo --plan；真实自由任务请配置 API。")

    @staticmethod
    def calls(items):
        return ChatResult(None, [{"id": "demo_" + uuid.uuid4().hex[:12], "name": n,
                                  "arguments": json.dumps(a)} for n, a in items], "tool_calls")
