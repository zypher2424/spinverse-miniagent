"""模型生成的计划必须解析和验证；计划数据不授予执行权限。"""
import json
from task_state import Plan

PLAN_PROMPT = '''请为当前用户任务生成一个线性计划，只输出 JSON，不要工具调用或 Markdown。
格式：{"task":"原任务", "steps":[{"id":"s1","goal":"具体目标","kind":"read|edit|check",
"files":["明确已知的相对路径，未知则为空"],"acceptance":"可观察的完成条件"}]}。
1–6 步，按依赖排序。只读任务只需 read；修改代码须 read→edit→check。
check 必须运行真实 Python 验证脚本或 pytest，不允许仅声称通过。不要为测试通过篡改验收文件。'''


def parse_plan(text, task):
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    plan = Plan.from_dict(json.loads(cleaned))
    # 原用户任务是权威；模型不能在计划里改写它。
    plan.task = task
    for step in plan.steps:
        step.status, step.evidence, step.attempts = "pending", [], 0
    return plan
