"""完整历史持久化；工作上下文按完整用户轮分组裁剪，不拆工具配对。"""
import copy
import json


class ContextBudgetExceeded(Exception):
    pass


def estimate_tokens(value):
    """无模型 tokenizer 时的保守估计；不是精确 token 计数。"""
    return (len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2) // 3 + 16


class Context:
    def __init__(self, system_prompt, messages=None, input_budget=16000, output_reserve=2048,
                 safety_reserve=1024, counter=estimate_tokens):
        self.messages = copy.deepcopy(messages) if messages is not None else [
            {"role": "system", "content": system_prompt}]
        self.input_budget = input_budget
        self.output_reserve = output_reserve
        self.safety_reserve = safety_reserve
        self.counter = counter
        self.last_usage = {}
        self.pinned_task = None

    def append_user(self, text):
        self.messages.append({"role": "user", "content": text})

    def append_assistant(self, text):
        self.messages.append({"role": "assistant", "content": text})

    def append_tool_batch(self, result, observations):
        self.messages.append({"role": "assistant", "content": result.content,
            "tool_calls": [{"id": tc["id"], "type": "function", "function": {
                "name": tc["name"], "arguments": tc["arguments"]}} for tc in result.tool_calls]})
        for tc, outcome in observations:
            self.messages.append({"role": "tool", "tool_call_id": tc["id"],
                                  "content": outcome.to_json()})

    def build_model_context(self, schemas):
        system = self.messages[:1]
        if self.pinned_task:
            system = system + [{"role": "user", "content": "当前原始任务（保留约束）：" + self.pinned_task}]
        groups = []
        for message in self.messages[1:]:
            if message["role"] == "user" or not groups:
                groups.append([])
            groups[-1].append(message)
        selected = groups[:]
        available = self.input_budget - self.output_reserve - self.safety_reserve
        cost = lambda items: self.counter(items) + self.counter(schemas)
        flattened = lambda: system + [m for group in selected for m in group]
        while len(selected) > 1 and cost(flattened()) > available:
            selected.pop(0)
        messages = flattened()
        self.last_usage = {"estimated_tokens": cost(messages), "available": available,
                           "dropped_turns": len(groups) - len(selected), "estimate": True}
        if cost(messages) > available:
            raise ContextBudgetExceeded("当前任务、工具证据和 Schema 已超过输入预算；请缩小任务或开启新会话。")
        self.validate(messages)
        return copy.deepcopy(messages)

    @staticmethod
    def validate(messages):
        pending, seen = set(), set()
        for message in messages:
            calls = message.get("tool_calls", [])
            if pending and message["role"] != "tool":
                raise ValueError("工具结果缺失")
            if calls:
                ids = [tc["id"] for tc in calls]
                if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                    raise ValueError("无效或重复的 tool_call_id")
                if seen.intersection(ids):
                    raise ValueError("重复使用 tool_call_id")
                seen.update(ids)
                pending.update(ids)
            if message["role"] == "tool":
                if message.get("tool_call_id") not in pending:
                    raise ValueError("孤立工具消息")
                pending.remove(message["tool_call_id"])
        if pending:
            raise ValueError("工具结果缺失")
