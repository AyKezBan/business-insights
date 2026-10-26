"""Generic schema-repair hints with synthetic replies; no live evaluation data."""

import json
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError, field_validator
from pydantic_core import PydanticCustomError

from insight_agent.budget import RubleLedger
from insight_agent.llm import DailyBudget, LLMError, LLMResponse, RunBudget, StructuredLLM
from insight_agent.trace import Tracer, load_trace

MODEL = "gemma-4-26b-a4b"
PRIVATE = "synthetic-private-map-key-and-value"
MESSAGES = [{"role": "user", "content": "Return the synthetic structure."}]


class Opportunity(BaseModel):
    level: str


class Draft(BaseModel):
    opportunities: list[Opportunity]


class ReplyClient:
    def __init__(self, reply):
        self.reply = reply
        self.messages = []

    def chat(self, model, messages, *, json_mode):
        self.messages.append(messages)
        return LLMResponse(json.dumps(self.reply(messages)), model, 80, 40)


def make_llm(tmp_path, reply):
    ledger = RubleLedger(state_path=tmp_path / "ledger.json")
    budget = RunBudget(40_000, DailyBudget(1_000_000, ledger))
    tracer = Tracer("run_saferepair", tmp_path)
    client = ReplyClient(reply)
    return StructuredLLM(client, budget, tracer), client, ledger


def test_missing_nested_field_is_repaired_only_after_safe_location_and_code_hint(tmp_path):
    def reply(messages):
        feedback = messages[-1]["content"]
        if "opportunities[0].level:missing" in feedback:
            return {"opportunities": [{"level": "synthetic-level"}]}
        return {"opportunities": [{}]}

    llm, client, ledger = make_llm(tmp_path, reply)
    result = llm.generate("check", MODEL, MESSAGES, Draft, retries=1)
    assert result.opportunities[0].level == "synthetic-level"
    assert len(client.messages) == 2
    assert llm.budget.used == 240
    assert ledger.spent == Decimal("0.2000")
    assert ledger._inflight == 0


def test_map_keys_and_invalid_values_are_not_schema_hints(tmp_path):
    class MapAnswer(BaseModel):
        values: dict[str, int]

    llm, _, _ = make_llm(tmp_path, lambda _: {"values": {PRIVATE: PRIVATE}})
    with pytest.raises(LLMError) as caught:
        llm.generate("check", MODEL, MESSAGES, MapAnswer, retries=0)
    error = llm.tracer.events[0].data["error"]
    assert "values.[field]:int_parsing" in error
    assert PRIVATE not in str(caught.value) + error
    persisted = json.dumps([e.model_dump(mode="json") for e in load_trace(tmp_path, "run_saferepair")])
    assert PRIVATE not in persisted


def test_dynamic_map_key_is_hidden_even_when_equal_to_another_static_field(tmp_path):
    class MapAnswer(BaseModel):
        level: str
        values: dict[str, int]

    llm, _, _ = make_llm(tmp_path, lambda _: {"level": "ok", "values": {"level": PRIVATE}})
    with pytest.raises(LLMError):
        llm.generate("check", MODEL, MESSAGES, MapAnswer, retries=0)
    error = llm.tracer.events[0].data["error"]
    assert "values.[field]:int_parsing" in error
    assert "values.level" not in error


def test_numeric_map_keys_are_hidden_instead_of_reported_as_list_indices(tmp_path):
    class MapAnswer(BaseModel):
        values: dict[int, int]

        @classmethod
        def model_validate(cls, obj):
            raise ValidationError.from_exception_data("Synthetic", [
                {"type": "int_parsing", "loc": ("values", 42), "input": PRIVATE},
            ])

    llm, _, _ = make_llm(tmp_path, lambda _: {"values": {42: PRIVATE}})
    with pytest.raises(LLMError):
        llm.generate("check", MODEL, MESSAGES, MapAnswer, retries=0)
    error = llm.tracer.events[0].data["error"]
    assert "values.[field]:int_parsing" in error
    assert "42" not in error


def test_custom_error_code_message_and_context_never_enter_hints(tmp_path):
    class CustomAnswer(BaseModel):
        value: str

        @field_validator("value")
        @classmethod
        def reject(cls, value):
            raise PydanticCustomError(PRIVATE, "private custom message {raw}", {"raw": value})

    llm, client, _ = make_llm(tmp_path, lambda _: {"value": PRIVATE})
    with pytest.raises(LLMError) as caught:
        llm.generate("check", MODEL, MESSAGES, CustomAnswer, retries=1)
    diagnostic = llm.tracer.events[0].data["error"]
    repair_feedback = client.messages[1][-1]["content"]
    assert "value:invalid" in diagnostic
    assert PRIVATE not in diagnostic + repair_feedback + str(caught.value)
    assert "private custom message" not in diagnostic + repair_feedback


@pytest.mark.parametrize("index,expected_path", [
    (-1, "values[?].[field]"), (10000, "values[?].[field]"),
    # Pydantic converts integers outside its location range to strings.
    (10**30, "values.[field].[field]"),
])
def test_unknown_locations_and_large_numeric_indices_are_bounded(tmp_path, index, expected_path):
    class CustomLocations(BaseModel):
        values: list[dict[str, int]]

        @classmethod
        def model_validate(cls, obj):
            raise ValidationError.from_exception_data("Synthetic", [
                {"type": "missing", "loc": ("values", index, PRIVATE), "input": PRIVATE},
            ])

    llm, _, _ = make_llm(tmp_path, lambda _: {"values": []})
    with pytest.raises(LLMError):
        llm.generate("check", MODEL, MESSAGES, CustomLocations, retries=0)
    error = llm.tracer.events[0].data["error"]
    assert expected_path + ":missing" in error
    assert PRIVATE not in error and str(index) not in error


def test_many_errors_keep_count_but_bound_hint_count(tmp_path):
    llm, _, _ = make_llm(tmp_path, lambda _: {"opportunities": [{}] * 100})
    with pytest.raises(LLMError):
        llm.generate("check", MODEL, MESSAGES, Draft, retries=0)
    error = llm.tracer.events[0].data["error"]
    assert "100" in error
    assert 1 <= error.count(":missing") <= 5
    assert len(error) < 1000


def test_healthy_result_and_accounting_are_unchanged(tmp_path):
    llm, client, ledger = make_llm(tmp_path, lambda _: {"opportunities": [{"level": "synthetic-level"}]})
    result = llm.generate("check", MODEL, MESSAGES, Draft)
    assert result.model_dump() == {"opportunities": [{"level": "synthetic-level"}]}
    assert len(client.messages) == 1
    assert llm.tracer.events[0].data["error"] is None
    assert llm.budget.used == 120
    assert ledger.spent == Decimal("0.1000")
