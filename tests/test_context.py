import json
import pytest
from context import Context, ContextBudgetExceeded
from llm import ChatResult
from results import ToolResult


def test_trim_complete_turns_and_pin_task():
    c = Context("rules", input_budget=1200, output_reserve=0, safety_reserve=0, counter=lambda x: len(json.dumps(x)))
    for i in range(8):
        c.append_user("old task " + str(i) + "x" * 100)
        call = {"id": str(i), "name": "calculator", "arguments": "{}"}
        c.append_tool_batch(ChatResult(None, [call]), [(call, ToolResult("success", "x" * 100))])
        c.append_assistant("done")
    c.append_user("current")
    c.pinned_task = "must preserve original constraint"
    sent = c.build_model_context([])
    assert c.last_usage["dropped_turns"] > 0
    assert "must preserve" in str(sent)
    Context.validate(sent)
    assert len(c.messages) == 34


def test_current_oversize_is_explicit_stop():
    c = Context("rules", input_budget=100, output_reserve=0, safety_reserve=0)
    c.append_user("x" * 1000)
    with pytest.raises(ContextBudgetExceeded):
        c.build_model_context([{"schema": "x" * 1000}])


@pytest.mark.parametrize("messages", [
    [{"role": "tool", "tool_call_id": "x", "content": "x"}],
    [{"role": "assistant", "tool_calls": [{"id": "x"}]}],
    [{"role": "assistant", "tool_calls": [{"id": "x"}, {"id": "x"}]}],
])
def test_invalid_pairs_rejected(messages):
    with pytest.raises(ValueError):
        Context.validate(messages)
