"""Terminal budget enforcement uses real budgets/ledger and finite offline replies."""

import json
from collections import deque
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from pydantic import BaseModel

from insight_agent import llm as llm_module
from insight_agent.budget import (
    BudgetExceeded, DailyBudgetExceeded, Price, PriceTable, RubleLedger,
    RunBudgetExceeded, RunTimeout,
)
from insight_agent.llm import (
    DailyBudget, LLMError, LLMResponse, OpenAICompatibleClient, RunBudget,
    StructuredLLM, error_code,
)
from insight_agent.trace import Tracer

PRIMARY = "gemma-4-26b-a4b"
FALLBACK = "glm-5.3-flash"
MESSAGES = [{"role": "user", "content": "Verify this synthetic claim."}]


class Verdict(BaseModel):
    supported: bool


class FiniteClient:
    def __init__(self, replies, clock):
        self.replies = deque(replies)
        self.clock = clock
        self.attempts = []

    def chat(self, model, messages, *, json_mode):
        self.attempts.append((model, json_mode))
        assert self.replies, "Unexpected additional provider attempt"
        reply = self.replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        text, prompt, completion, completed_at = reply
        self.clock.now = completed_at
        return LLMResponse(text, model, prompt, completion)


@pytest.fixture
def harness(tmp_path, monkeypatch):
    def make(replies=(), *, run_limit=40_000, daily_limit=1_000_000, prior_tokens=0, deadline=110,
             ledger_limit=Decimal(500)):
        clock = SimpleNamespace(now=100.0)
        monkeypatch.setattr(llm_module, "time", SimpleNamespace(monotonic=lambda: clock.now))
        ledger_path = tmp_path / "ledger.json"
        ledger = RubleLedger(state_path=ledger_path, limit=ledger_limit)
        prices = PriceTable(default=Price(Decimal("0.5"), Decimal("1.5")))
        daily = DailyBudget(daily_limit, ledger, prices)
        if prior_tokens:
            daily.add(prior_tokens)
        reservations = []
        reserve = daily.reserve

        def record_reservation(tokens, model=None):
            reservations.append((tokens, model))
            return reserve(tokens, model)

        monkeypatch.setattr(daily, "reserve", record_reservation)
        budget = RunBudget(run_limit, daily, deadline=deadline)
        tracer = Tracer("run_terminal", tmp_path / "traces")
        client = FiniteClient(replies, clock)
        llm = StructuredLLM(client, budget, tracer, FALLBACK)
        return SimpleNamespace(llm=llm, client=client, clock=clock, ledger=ledger,
                               ledger_path=ledger_path, prices=prices, daily=daily,
                               budget=budget, tracer=tracer, reservations=reservations)

    return make


def assert_accounted(env, *, prompt, completion, prior_tokens=0, model=PRIMARY, calls=1):
    cost = env.prices.cost(model, prompt, completion)
    assert env.budget.used == prompt + completion
    assert env.daily.used == prior_tokens + prompt + completion
    assert env.llm.usage()["tokens"] == prompt + completion
    assert env.budget.calls == env.llm.usage()["calls"] == calls
    assert env.budget.cost == env.ledger.spent == cost
    assert Decimal(env.llm.usage()["cost_rub"]) == cost
    assert env.ledger.calls == calls
    assert RubleLedger(state_path=env.ledger_path).spent == cost
    assert env.llm.models_used == [model]
    event = env.tracer.events[-1]
    assert (event.type, event.step, event.data["model"]) == ("llm_call", "semantic_verdict", model)
    assert (event.data["prompt_tokens"], event.data["completion_tokens"]) == (prompt, completion)
    assert Decimal(event.data["cost_rub"]) == cost
    assert len(event.data["call_id"]) == 32
    assert env.ledger._inflight == 0, "Settled response must not leave a reservation"
    return event


@pytest.mark.parametrize("text", ['{"supported": true}', 'not JSON'])
@pytest.mark.parametrize("kind,run_limit,daily_limit,prior,completion,finished,error,code", [
    ("deadline", 40_000, 1_000_000, 0, 400, 111, RunTimeout, "timeout"),
    ("run", 2_000, 1_000_000, 0, 1_400, 101, RunBudgetExceeded, "budget_run"),
    ("daily", 40_000, 3_000, 1_400, 1_000, 101, DailyBudgetExceeded, "budget_daily"),
])
def test_terminal_response_fails_closed_after_accounting(
    harness, text, kind, run_limit, daily_limit, prior, completion, finished, error, code,
):
    env = harness([(text, 800, completion, finished)], run_limit=run_limit,
                  daily_limit=daily_limit, prior_tokens=prior)
    with pytest.raises(error) as caught:
        env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict, retries=2)
    assert isinstance(caught.value, BudgetExceeded)
    assert error_code(caught.value) == code
    event = assert_accounted(env, prompt=800, completion=completion, prior_tokens=prior)
    assert event.data["error"] == str(caught.value)
    assert event.data["latency_ms"] == int((finished - 100) * 1000)
    assert env.client.attempts == [(PRIMARY, True)]
    assert len(env.tracer.events) == len(env.reservations) == 1


@pytest.mark.parametrize("finished", [101, 110])
def test_faithful_response_at_token_limit_and_not_past_deadline_returns(harness, finished):
    env = harness([('{"supported": true}', 800, 1_200, finished)], run_limit=2_000, daily_limit=2_000)
    assert env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict).supported is True
    event = assert_accounted(env, prompt=800, completion=1_200)
    assert event.data["error"] is None
    assert len(env.reservations) == 1, "Terminal validation must not reserve another budget"
    assert env.client.attempts == [(PRIMARY, True)]


def test_fallback_response_overrun_stops_without_another_schema_or_provider_attempt(harness):
    env = harness([LLMError("synthetic unavailable"), ('{"supported": true}', 800, 1_400, 101)], run_limit=2_000)
    with pytest.raises(RunBudgetExceeded):
        env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict, retries=2)
    event = assert_accounted(env, prompt=800, completion=1_400, model=FALLBACK)
    assert event.data["error"]
    assert env.client.attempts == [(PRIMARY, True), (FALLBACK, True)]
    assert len(env.tracer.events) == len(env.reservations) == 2
    assert env.tracer.events[0].data["prompt_tokens"] is None
    assert env.tracer.events[0].data["cost_rub"] is None


def test_actual_ruble_charge_above_reservation_cannot_authorize_terminal_reply(harness):
    env = harness([('{"supported": true}', 800, 20_000, 101)], ledger_limit=Decimal(20))
    with pytest.raises(DailyBudgetExceeded):
        env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict, retries=2)
    assert_accounted(env, prompt=800, completion=20_000)
    assert env.ledger.spent == Decimal("30.4000") > env.ledger.limit
    assert env.client.attempts == [(PRIMARY, True)]
    assert len(env.reservations) == 1


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_schema_retries_and_fallback_remain_bounded(harness, retries):
    attempts = 2 * (retries + 1)
    env = harness([('not JSON', 80, 40, 101)] * attempts)
    with pytest.raises(LLMError, match="не удалось получить корректный ответ"):
        env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict, retries=retries)
    assert env.client.attempts == [(model, True) for model in (PRIMARY, FALLBACK) for _ in range(retries + 1)]
    assert len(env.tracer.events) == len(env.reservations) == attempts
    assert env.budget.used == env.daily.used == 120 * attempts
    assert env.budget.calls == env.ledger.calls == attempts
    assert env.ledger.spent == Decimal("0.1000") * attempts
    assert env.llm.models_used == [PRIMARY, FALLBACK]
    assert all(event.data["error"] for event in env.tracer.events)


@pytest.mark.parametrize("run_limit", [1_999, 2_000])
def test_transport_json_400_retry_accounts_one_returned_response(harness, run_limit):
    env = harness(run_limit=run_limit)
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(400, json={"error": "synthetic JSON mode unsupported"})
        assert len(requests) == 2, "Transport must not create another attempt after the returned response"
        return httpx.Response(200, json={"model": PRIMARY, "choices": [{"message": {"content": '{"supported": true}'}}],
                                         "usage": {"prompt_tokens": 800, "completion_tokens": 1_200}})

    client = OpenAICompatibleClient("https://provider.invalid", "synthetic-unused-key")
    client._http.close()
    with httpx.Client(base_url="https://provider.invalid", transport=httpx.MockTransport(respond)) as transport:
        client._http = transport
        env.llm.client = client
        if run_limit < 2_000:
            with pytest.raises(RunBudgetExceeded):
                env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict)
        else:
            assert env.llm.generate("semantic_verdict", PRIMARY, MESSAGES, Verdict).supported is True
    assert_accounted(env, prompt=800, completion=1_200)
    assert len(requests) == 2
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    assert len(env.tracer.events) == len(env.reservations) == 1
