"""可序列化线性计划；完成状态须由外部工具证据支持。"""
from dataclasses import dataclass, field, asdict


@dataclass
class Step:
    id: str
    goal: str
    kind: str
    files: list = field(default_factory=list)
    acceptance: str = ""
    status: str = "pending"
    evidence: list = field(default_factory=list)
    attempts: int = 0


@dataclass
class Plan:
    task: str
    steps: list[Step]

    def to_dict(self):
        return {"task": self.task, "steps": [asdict(s) for s in self.steps]}

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or not isinstance(value.get("task"), str):
            raise ValueError("计划缺少 task")
        raw = value.get("steps")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 6:
            raise ValueError("计划必须有 1–6 步")
        steps, ids = [], set()
        for item in raw:
            if not isinstance(item, dict):
                raise ValueError("计划步骤必须是对象")
            step = Step(**item)
            if (not isinstance(step.id, str) or not step.id or step.id in ids
                    or step.kind not in ("read", "edit", "check")
                    or not isinstance(step.goal, str) or not step.goal.strip()
                    or not isinstance(step.acceptance, str) or not step.acceptance.strip()
                    or not isinstance(step.files, list) or not all(isinstance(f, str) for f in step.files)
                    or step.status not in ("pending", "running", "done", "failed", "blocked")
                    or not isinstance(step.evidence, list) or not isinstance(step.attempts, int)):
                raise ValueError("无效计划步骤")
            ids.add(step.id)
            steps.append(step)
        return cls(value["task"], steps)


def verify_step(step, actions):
    names = {"read": {"read_file", "read_file_range", "list_files", "search_text"},
             "edit": {"write_file", "apply_patch"}, "check": {"shell"}}[step.kind]
    relevant = [a for a in actions if a["name"] in names and a["phase"] == "completed"]
    if step.kind == "check":
        relevant = [a for a in relevant if a["args"].get("command") in ("python", "python3", "pytest")]
    if not relevant or relevant[-1]["result"]["status"] != "success":
        return False, f"步骤需要真实的 {step.kind} 成功证据，最后相关行动尚未成功。"
    # 文件列表是可检查约束，不允许只读一个无关文件便完成明确的多文件步骤。
    if step.files and step.kind in ("read", "edit"):
        paths = {a["args"].get("path") for a in relevant if a["result"]["status"] == "success"}
        if not set(step.files).issubset(paths):
            return False, "尚未取得计划列出文件的全部证据。"
    step.evidence = [a["id"] for a in relevant if a["result"]["status"] == "success"]
    return True, "外部行动证据符合步骤类型"
