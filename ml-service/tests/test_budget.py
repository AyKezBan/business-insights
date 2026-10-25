from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.budget import (
    DailyBudgetExceeded,
    Price,
    PriceTable,
    RubleLedger,
    RunTimeout,
)
from insight_agent.llm import DailyBudget, LLMError, RunBudget, StructuredLLM, error_code
from insight_agent.schemas import InsightStatus, Outcome
from insight_agent.trace import Tracer

MSK = ZoneInfo("Europe/Moscow")
MESSAGES = [{"role": "system", "content": "Ты планировщик"}, {"role": "user", "content": "x"}]


class Answer(BaseModel):
    value: int


def D(x) -> Decimal:
    return Decimal(str(x))


def test_cost_is_exact_decimal_and_rounded_up():
    prices = PriceTable({"gemma-4-26b-a4b": Price(D("0.5"), D("1.5"))})
    assert prices.cost("google/gemma-4-26b-a4b-it", 1000, 500) == D("1.25")
    assert prices.cost("google/gemma-4-26b-a4b-it", 1, 0) == D("0.0005")
    assert prices.cost("google/gemma-4-26b-a4b-it", 1, 1) == D("0.0020")


def test_unknown_model_uses_conservative_default_and_upper_bound():
    prices = PriceTable(default=Price(D("0.5"), D("1.5")))
    assert prices.upper_bound("some-model", 2000) == D("3.0000")


def test_many_small_amounts_do_not_drift():
    ledger = RubleLedger()
    for _ in range(10_000):
        ledger.settle(None, D("0.0003"))
    assert ledger.spent == D("3.0000")


def test_ledger_blocks_what_would_cross_the_limit():
    ledger = RubleLedger(limit=D(500))
    ledger.settle(None, D("499"))
    with pytest.raises(DailyBudgetExceeded):
        ledger.reserve(D("2"))
    ledger.reserve(D("1"))


def test_in_flight_reservations_count_against_the_limit():
    ledger = RubleLedger(limit=D(10))
    ledger.reserve(D("6"))
    with pytest.raises(DailyBudgetExceeded):
        ledger.reserve(D("6"))


def test_state_survives_restart_and_resets_on_new_moscow_day(tmp_path):
    path = tmp_path / "budget.json"
    now = {"t": datetime(2026, 10, 25, 23, 50, tzinfo=MSK)}
    first = RubleLedger(state_path=path, clock=lambda: now["t"])
    first.settle(None, D("12.5"))
    assert RubleLedger(state_path=path, clock=lambda: now["t"]).spent == D("12.5")

    now["t"] = datetime(2026, 10, 26, 0, 5, tzinfo=MSK)
    assert RubleLedger(state_path=path, clock=lambda: now["t"]).spent == D(0)


def test_levels_follow_thresholds():
    ledger = RubleLedger(limit=D(500), warn=D(350), stop_optional=D(450))
    assert ledger.level() == "ok"
    ledger.settle(None, D("350"))
    assert ledger.level() == "warn"
    ledger.settle(None, D("100"))
    assert ledger.level() == "stop"


def _llm(client, ledger, run_limit=40_000):
    daily = DailyBudget(1_000_000, ledger, PriceTable(default=Price(D("0.5"), D("1.5"))))
    return StructuredLLM(client, RunBudget(run_limit, daily), Tracer("run_budget"), None), daily


def test_actual_cost_is_charged_and_traced_not_the_estimate():
    ledger = RubleLedger()
    client = ScriptedClient({"plan": [{"value": 1}]})
    llm, _ = _llm(client, ledger)
    llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer)

    # фейковый клиент отдаёт 800 токенов запроса и 400 ответа: 0.4 + 0.6 руб.
    assert ledger.spent == D("1.0000")
    event = llm.tracer.events[0].data
    assert event["cost_rub"] == "1.0000" and len(event["call_id"]) == 32
    usage = llm.usage()
    assert (usage["tokens"], usage["calls"], usage["cost_rub"]) == (1200, 1, "1.0000")


def test_reservation_is_released_when_provider_fails():
    ledger = RubleLedger(limit=D(500))
    client = ScriptedClient({"plan": [LLMError("503")]})
    llm, _ = _llm(client, ledger)
    with pytest.raises(LLMError):
        llm.generate("plan", "gemma-4-26b-a4b", MESSAGES, Answer)
    assert ledger.spent == D(0)
    ledger.reserve(D(500))


def test_run_past_deadline_stops_before_the_call():
    daily = DailyBudget(1_000_000)
    budget = RunBudget(40_000, daily, deadline=-1.0)
    with pytest.raises(RunTimeout) as caught:
        budget.check(100)
    assert error_code(caught.value) == "timeout"


def test_run_timeout_gives_failed_insight(make_agent, request_):
    agent = make_agent(ScriptedClient({"analyze_event": [analysis_reply()]}))
    agent.settings.agent_max_seconds = -1
    insight = agent.run(request_)
    assert (insight.status, insight.outcome, insight.error_code) == (InsightStatus.failed, Outcome.none, "timeout")


def test_soft_stop_skips_revision_and_marks_limited(make_agent, request_):
    script = {
        "analyze_event": [analysis_reply()],
        "plan": [plan_reply()],
        "personalize": [draft_reply(bad_number=True)],
        "revise": [draft_reply()],
    }
    client = ScriptedClient(script)
    agent = make_agent(client)
    agent.daily.ledger.settle(None, D("460"))
    insight = agent.run(request_)

    assert "revise" not in client.steps()
    assert insight.status == InsightStatus.limited
    assert any("бюджет" in m for m in insight.missing_data)
    skipped = [e for e in agent_events(agent, insight) if e.step == "budget_soft_stop"]
    assert skipped and skipped[0].data["skipped"] == "revise"


def agent_events(agent, insight):
    from insight_agent.trace import load_trace

    return load_trace(agent.settings.trace_dir, insight.run_id)


def test_final_event_and_start_event_carry_budget_and_usage(make_agent, request_):
    agent = make_agent(ScriptedClient({"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()]}))
    insight = agent.run(request_)
    events = agent_events(agent, insight)
    assert events[0].data["budget"]["level"] == "ok"
    usage = events[-1].data["usage"]
    assert usage["calls"] == 3 and D(usage["cost_rub"]) > 0
