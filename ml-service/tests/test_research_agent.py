"""Мультиагентная схема: аналитик, планировщик, исследователь, персонализатор, верификатор."""
import httpx

from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.agent.critic import check_claims
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.state import AgentState
from insight_agent.research.fetch import SafeFetcher
from insight_agent.research.service import OpenResearch, TtlCache
from insight_agent.schemas import Claim, ClaimKind, InsightStatus, Products
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.tools.profile import derive_signals
from insight_agent.trace import load_trace
from test_open_research import NOW, Site, research


def _profile(request_):
    return request_.profile.model_copy(update={
        "products": Products(loans=True, leasing=True), "foreign_trade": True, "foreign_trade_countries": ["КИТАЙ", "ТУРЦИЯ"]})


def _agent(make_agent, client, service):
    agent = make_agent(client)
    agent.research = service
    return agent


def _script(personalize=None):
    return {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [personalize or draft_reply()]}


def test_researcher_adds_official_indicators_chosen_by_the_profile(make_agent, request_):
    prompts = []

    def personalize(messages):
        prompts.append(messages[-1]["content"])
        return draft_reply()

    service, site = research()
    agent = _agent(make_agent, ScriptedClient(_script(personalize)), service)
    insight = agent.run(request_.model_copy(update={"profile": _profile(request_)}))

    events = load_trace(agent.settings.trace_dir, insight.run_id)
    called = [e for e in events if e.type == "tool_call" and e.data.get("tool") == "search_open_sources"]
    assert called and {d["id"] for d in called[0].data["result"]["documents"]} >= {
        "cbr_keyrate_2026-10-23", "cbr_fx_cny_2026-10-24", "cbr_fx_try_2026-10-24"} - {"cbr_fx_try_2026-10-24"}
    plan = next(e for e in events if e.step == "open_research_plan")
    assert any("products.loans" in r for r in plan.data["reasons"]) and any("CNY" in r for r in plan.data["reasons"])
    assert "Ключевая ставка Банка России на 23.10.2026: 14,00% годовых" in prompts[0]
    assert "<data kind=\"news\">" in prompts[0] and insight.status != InsightStatus.failed


def test_company_without_financing_or_foreign_trade_triggers_no_open_requests(make_agent, request_):
    service, site = research()
    plain = request_.profile.model_copy(update={"products": Products(loans=False, leasing=False, deposits=False, factoring=False),
                                                "foreign_trade": False, "okved_name": None,
                                                "group_member_okveds": [], "counterparties": []})
    agent = _agent(make_agent, ScriptedClient(_script(draft_reply())), service)
    agent.run(request_.model_copy(update={"profile": plain}))
    assert site.requests == []


def test_failing_open_source_degrades_without_failing_the_card(make_agent, request_):
    service, _ = research(Site(fail_key_rate=True))
    agent = _agent(make_agent, ScriptedClient(_script()), service)
    insight = agent.run(request_.model_copy(update={"profile": _profile(request_)}))
    events = load_trace(agent.settings.trace_dir, insight.run_id)
    assert insight.status != InsightStatus.failed
    assert any(e.type == "error" and e.step == "open_research" and "ключевая ставка" in e.data["error"] for e in events)


def test_trace_names_the_acting_agent_in_pipeline_order(make_agent, request_):
    service, _ = research()
    agent = _agent(make_agent, ScriptedClient(_script()), service)
    insight = agent.run(request_.model_copy(update={"profile": _profile(request_)}))
    seen = list(dict.fromkeys(e.data.get("agent") for e in load_trace(agent.settings.trace_dir, insight.run_id)))
    assert seen == [None, "analyst", "planner", "researcher", "personalizer", "verifier", "orchestrator"]
    calls = [e for e in load_trace(agent.settings.trace_dir, insight.run_id) if e.type == "llm_call"]
    assert {c.data["agent"] for c in calls} == {"analyst", "planner", "personalizer"}


def test_disabled_research_removes_the_tool(make_agent, request_):
    client = ScriptedClient(_script())
    agent = make_agent(client)
    assert agent.research is None
    agent.run(request_)
    assert all(e.data.get("tool") != "search_open_sources" for e in load_trace(agent.settings.trace_dir, agent.records and next(iter(agent.records.values())).insight.run_id))


def _state_with_indicator(request_):
    service, _ = research()
    state = AgentState(run_id="run_x", request=request_, signals=derive_signals(request_.profile), missing_profile=[])
    agent = InsightAgent.__new__(InsightAgent)
    doc = service.search(__import__("insight_agent.research.plan", fromlist=["x"]).build_plan(_profile(request_), ["cost_of_debt"]),
                         as_of=__import__("datetime").date(2026, 10, 23)).docs
    key = next(d for d in doc if d.source_id.startswith("cbr_keyrate"))
    event = InsightAgent._as_news_event(key)
    state.news[event.event_id] = event
    state.resolved_news_texts[event.source.source_id] = key.text
    return state


def test_rate_quoted_from_an_official_source_passes_and_the_previous_one_is_not_taken_for_current(request_):
    state = _state_with_indicator(request_)
    good = Claim(text="Ключевая ставка Банка России на 23.10.2026: 14,00% годовых.", kind=ClaimKind.fact,
                 source_ids=["cbr_keyrate_2026-10-23"])
    bad = Claim(text="Ключевая ставка Банка России на 23.10.2026: 15,00% годовых.", kind=ClaimKind.fact,
                source_ids=["cbr_keyrate_2026-10-23"])
    assert [i for i in check_claims([good], state, section="why_it_matters") if i.severity == "error"] == []
    assert "keyrate_value_mismatch" in {i.code for i in check_claims([bad], state, section="why_it_matters")}
    past = Claim(text="Ранее, на 10.09.2026, ключевая ставка составляла 15,00% годовых.", kind=ClaimKind.fact,
                 source_ids=["cbr_keyrate_2026-10-23"])
    assert [i for i in check_claims([past], state, section="why_it_matters") if i.severity == "error"] == []
