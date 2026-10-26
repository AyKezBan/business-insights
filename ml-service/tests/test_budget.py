import json
from datetime import datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from pydantic import BaseModel

from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply, reviewed_semantics, semantic_contents
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


def test_state_survives_restart_and_midnight_until_0300_moscow(tmp_path):
    path = tmp_path / "budget.json"
    now = {"t": datetime(2026, 10, 25, 23, 50, tzinfo=MSK)}
    first = RubleLedger(state_path=path, clock=lambda: now["t"])
    first.settle(None, D("12.5"))
    assert RubleLedger(state_path=path, clock=lambda: now["t"]).spent == D("12.5")

    now["t"] = datetime(2026, 10, 26, 0, 5, tzinfo=MSK)
    restarted = RubleLedger(state_path=path, clock=lambda: now["t"])
    assert (restarted.spent, restarted.calls) == (D("12.5"), 1)

    now["t"] = datetime(2026, 10, 26, 2, 59, 59, tzinfo=MSK)
    assert (restarted.spent, restarted.calls) == (D("12.5"), 1)
    now["t"] = datetime(2026, 10, 26, 3, 0, tzinfo=MSK)
    assert (restarted.spent, restarted.calls) == (D(0), 0)
    restarted.settle(None, D("2.5"))
    after_reset = RubleLedger(state_path=path, clock=lambda: now["t"])
    assert (after_reset.spent, after_reset.calls) == (D("2.5"), 1)


@pytest.mark.parametrize("clock_timezone", [MSK, timezone.utc, ZoneInfo("Asia/Yekaterinburg")])
def test_daily_limit_reopens_at_0300_moscow_in_any_clock_timezone(clock_timezone):
    now = {"t": datetime(2026, 10, 25, 23, 50, tzinfo=MSK)}
    ledger = RubleLedger(limit=D(10), clock=lambda: now["t"].astimezone(clock_timezone))
    ledger.settle(None, D(9))

    now["t"] = datetime(2026, 10, 26, 2, 59, 59, tzinfo=MSK)
    assert (ledger.spent, ledger.calls) == (D(9), 1)
    with pytest.raises(DailyBudgetExceeded):
        ledger.reserve(D(2))

    now["t"] = datetime(2026, 10, 26, 3, 0, tzinfo=MSK)
    assert (ledger.spent, ledger.calls) == (D(0), 0)
    reservation = ledger.reserve(D(10))
    ledger.settle(reservation, D(10))
    assert (ledger.spent, ledger.calls) == (D(10), 1)
    with pytest.raises(DailyBudgetExceeded):
        ledger.reserve(D("0.0001"))


@pytest.mark.parametrize(
    ("legacy_day", "upgrade_time", "next_reset"),
    [
        ("2026-10-25", "2026-10-26T02:59:00+03:00", "2026-10-26T03:00:00+03:00"),
        ("2026-10-26", "2026-10-26T02:59:00+03:00", "2026-10-26T03:00:00+03:00"),
        ("2026-10-26", "2026-10-26T03:01:00+03:00", "2026-10-27T03:00:00+03:00"),
        ("2026-10-20", "2026-10-26T03:01:00+03:00", "2026-10-27T03:00:00+03:00"),
    ],
)
def test_legacy_ledger_preserves_spend_once_then_resets_across_restart(
    tmp_path, legacy_day, upgrade_time, next_reset
):
    path = tmp_path / "budget.json"
    path.write_text(json.dumps({"day": legacy_day, "spent": "499", "calls": 3}), encoding="utf-8")
    now = {"t": datetime.fromisoformat(upgrade_time)}
    upgraded = RubleLedger(state_path=path, clock=lambda: now["t"])
    assert (upgraded.spent, upgraded.calls) == (D(499), 3)
    with pytest.raises(DailyBudgetExceeded):
        upgraded.reserve(D(2))

    # No settlement: migration itself must survive restart and expire only once.
    now["t"] = datetime.fromisoformat(next_reset)
    restarted = RubleLedger(state_path=path, clock=lambda: now["t"])
    assert (restarted.spent, restarted.calls) == (D(0), 0)
    reservation = restarted.reserve(D(500))
    restarted.settle(reservation, D(500))
    final = RubleLedger(state_path=path, clock=lambda: now["t"])
    assert (final.spent, final.calls) == (D(500), 1)


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
        "semantic_verify": [reviewed_semantics(semantic_contents(draft_reply()))],
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
    agent = make_agent(ScriptedClient({"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()], "semantic_verify": [reviewed_semantics(semantic_contents(draft_reply()))]}))
    insight = agent.run(request_)
    events = agent_events(agent, insight)
    assert events[0].data["budget"]["level"] == "ok"
    usage = events[-1].data["usage"]
    assert usage["calls"] == 4 and D(usage["cost_rub"]) > 0


def test_cost_reported_by_the_gateway_beats_the_tariff_estimate():
    ledger = RubleLedger()
    daily = DailyBudget(1_000_000, ledger, PriceTable(default=Price(D("0.02"), D("0.04"))))
    run = RunBudget(40_000, daily)
    run.check(2000, "gemma-4-26b-a4b")
    cost = run.add(1000, model="gemma-4-26b-a4b", prompt_tokens=800, completion_tokens=200, reported_cost=D("0.0123"))
    assert cost == D("0.0123") and ledger.spent == D("0.0123")


def test_key_budget_reported_by_the_gateway_stops_calls_that_would_exceed_it():
    from insight_agent.llm import KeyStatus

    daily = DailyBudget(1_000_000, RubleLedger(), PriceTable(default=Price(D("0.02"), D("0.04"))),
                        key_gauge=lambda: KeyStatus(D("499.99"), D("500")))
    with pytest.raises(DailyBudgetExceeded):
        RunBudget(40_000, daily).check(10_000, "gemma-4-26b-a4b")
    ok = DailyBudget(1_000_000, RubleLedger(), PriceTable(default=Price(D("0.02"), D("0.04"))),
                     key_gauge=lambda: KeyStatus(D("7.02"), D("500")))
    RunBudget(40_000, ok).check(10_000, "gemma-4-26b-a4b")


def test_client_reads_cost_and_key_status_from_the_gateway():
    import json

    import httpx

    from insight_agent.llm import OpenAICompatibleClient

    def handler(request):
        if request.url.path.endswith("/key/info"):
            return httpx.Response(200, json={"info": {"spend": 7.02, "max_budget": 500.0, "budget_reset_at": "2026-10-27T00:00:00+00:00"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": "gemma-4-26b-a4b",
                                         "usage": {"prompt_tokens": 17, "completion_tokens": 3, "cost": 0.0002544}})

    client = OpenAICompatibleClient("https://gateway.example/v1", "k", 5, 100)
    client._http = httpx.Client(base_url="https://gateway.example/v1", transport=httpx.MockTransport(handler))
    assert client.chat("gemma-4-26b-a4b", [{"role": "user", "content": "x"}], json_mode=True).cost == D("0.0002544")
    status = client.key_status()
    assert (status.spend, status.max_budget, status.reset_at) == (D("7.02"), D("500.0"), "2026-10-27T00:00:00+00:00")


def test_missing_gateway_cost_falls_back_to_the_tariff():
    from insight_agent.llm import _reported_cost

    assert _reported_cost({"prompt_tokens": 1}) is None and _reported_cost({"cost": -1}) is None and _reported_cost({"cost": True}) is None


def test_token_reserve_for_semantic_verification_stops_further_revisions(make_agent, request_):
    script = {"analyze_event": [analysis_reply()], "plan": [plan_reply()],
              "personalize": [draft_reply(bad_number=True)], "revise": [draft_reply(bad_number=True)]}
    client = ScriptedClient(script)
    agent = make_agent(client)
    agent.settings.agent_max_tokens_per_run = 36_000  # после первого черновика остаётся меньше резерва на проверку
    insight = agent.run(request_)

    assert "revise" not in client.steps()
    assert any("запас на смысловую проверку" in m for m in insight.missing_data)
