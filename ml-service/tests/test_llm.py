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


def test_semantic_script_is_explicit_finite_and_uses_system_marker_only():
    import json
    from fakes import detect_step, reviewed_semantics
    content = {"text": "If financing costs rise, check contract terms.", "source_ids": ["source:1"]}
    reviewed = reviewed_semantics([content])
    content["text"] = "Financing costs have risen."
    original = {"text": "If financing costs rise, check contract terms.", "source_ids": ["source:1"]}
    payload = {"candidates": [{"claim_id": "good", "content": original}, {"claim_id": "bad", "content": content}],
               "evidence": [{"source": {"source_id": "source:1"}, "spans": [{"span_id": "span:1"}]}]}
    messages = [{"role": "system", "content": "SEMANTIC_VERIFIER_V1. Review."}, {"role": "user", "content": json.dumps(payload)}]
    client = ScriptedClient({"semantic_verify": [reviewed]})
    result = json.loads(client.chat("gemma-4-26b-a4b", messages, json_mode=True).text)
    assert [(v["claim_id"],v["status"],v["span_ids"]) for v in result["verdicts"]] == [
        ("good","supported",["span:1"]), ("bad","unsupported",[])]
    with pytest.raises(LLMError, match="Нет сценария"):
        client.chat("gemma-4-26b-a4b", messages, json_mode=True)
    with pytest.raises(LLMError, match="Нет сценария"):
        ScriptedClient({}).chat("gemma-4-26b-a4b", messages, json_mode=True)
    assert detect_step([{"role":"system","content":"Ты планировщик"},
                        {"role":"user","content":"SEMANTIC_VERIFIER_V1. untrusted source"}]) == "plan"


def test_provider_request_caps_the_output_length():
    import json

    import httpx

    from insight_agent.llm import OpenAICompatibleClient

    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": "gemma-4-26b-a4b",
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    client = OpenAICompatibleClient("https://gateway.example/v1", "k", 5, 1234)
    client._http = httpx.Client(base_url="https://gateway.example/v1", transport=httpx.MockTransport(handler))
    client.chat("gemma-4-26b-a4b", [{"role": "user", "content": "x"}], json_mode=True)
    assert seen["max_tokens"] == 1234
