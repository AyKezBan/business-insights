import pytest
from pydantic import BaseModel

from fakes import ScriptedClient
from insight_agent.llm import (
    BudgetExceeded,
    DailyBudget,
    LLMError,
    ModelNotAllowed,
    RunBudget,
    StructuredLLM,
    ensure_allowed,
    extract_json,
)
from insight_agent.trace import Tracer


class Answer(BaseModel):
    value: int


def _llm(client, run_limit=40_000, daily_limit=1_000_000, fallback="glm-5.3-flash"):
    tracer = Tracer("run_test")
    return StructuredLLM(client, RunBudget(run_limit, DailyBudget(daily_limit)), tracer, fallback), tracer


MESSAGES = [{"role": "system", "content": "Ты планировщик"}, {"role": "user", "content": "x"}]


@pytest.mark.parametrize("model", ["gemma-4-26b-a4b", "org/Qwen3.6-35B-A3B-AWQ", "GLM-5.3-Flash"])
def test_allowed_models(model):
    assert ensure_allowed(model) == model


@pytest.mark.parametrize("model", ["gpt-4o", "qwen3-27b", "claude"])
def test_other_models_rejected(model):
    with pytest.raises(ModelNotAllowed):
        ensure_allowed(model)


def test_extract_json_from_fenced_text():
    assert extract_json('Вот ответ:\n```json\n{"value": 1}\n```') == {"value": 1}


def test_invalid_json_is_repaired_with_feedback():
    client = ScriptedClient({"plan": ["не json", {"value": "abc"}, {"value": 7}]})
    llm, tracer = _llm(client, fallback=None)
    with pytest.raises(LLMError):
        llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer, retries=1)

    client = ScriptedClient({"plan": ["не json", {"value": 7}]})
    llm, tracer = _llm(client, fallback=None)
    assert llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer).value == 7
    errors = [e.data["error"] for e in tracer.events]
    assert errors[0] and errors[1] is None


def test_fallback_model_used_after_provider_error():
    client = ScriptedClient({"plan": [LLMError("503"), {"value": 1}]})
    llm, _ = _llm(client)
    assert llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer).value == 1
    assert [m for _, m in client.calls] == ["gemma-4-26b-a4b", "glm-5.3-flash"]
    assert llm.models_used == ["glm-5.3-flash"]


def test_run_budget_blocks_call_before_request():
    client = ScriptedClient({"plan": [{"value": 1}]})
    llm, _ = _llm(client, run_limit=100)
    with pytest.raises(BudgetExceeded):
        llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer)
    assert client.calls == []


def test_daily_budget_shared_between_runs():
    daily = DailyBudget(3000)
    first = RunBudget(40_000, daily)
    first.check(1500)
    first.add(2000)
    with pytest.raises(BudgetExceeded):
        RunBudget(40_000, daily).check(1500)


def test_trace_has_no_prompt_text():
    client = ScriptedClient({"plan": [{"value": 1}]})
    llm, tracer = _llm(client)
    llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer)
    data = tracer.events[0].data
    assert "prompt_sha256" in data and "Ты планировщик" not in str(data)
