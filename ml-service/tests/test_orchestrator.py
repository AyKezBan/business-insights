from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.llm import LLMError
from insight_agent.schemas import ClaimKind, InsightStatus, UserRole
from insight_agent.tools.bank import BankIndex
from insight_agent.trace import load_trace


def record_events(agent, insight):
    return load_trace(agent.settings.trace_dir, insight.run_id)


def test_enough_data_gives_complete_personal_insight(make_agent, request_):
    client = ScriptedClient(
        {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()]}
    )
    agent = make_agent(client)
    insight = agent.run(request_)

    assert insight.status == InsightStatus.complete, insight.self_check.issues
    assert client.steps() == ["analyze_event", "plan", "personalize"]
    assert {"products.loans", "products.leasing", "okved_code"} <= set(insight.profile_features_used)
    assert {s.source_id for s in insight.sources} >= {"news_demo_rate", "bank_demo_rate_p3"}
    assert insight.bank_view[0].kind == ClaimKind.assessment
    assert insight.what_happened and all(c.kind == ClaimKind.fact for c in insight.what_happened)
    assert insight.models == ["gemma-4-26b-a4b", "qwen3.6-35b-a3b"]
    assert "не является инвестиционной рекомендацией" in insight.disclaimer

    events = record_events(agent, insight)
    plan = next(e for e in events if e.type == "plan" and e.step == "plan")
    assert plan.data["by_llm"] is True
    assert all(s["tool"] != "delete_everything" for s in plan.data["steps"])
    tools_called = [e.data["tool"] for e in events if e.type == "tool_call"]
    assert tools_called == ["search_bank_analytics", "search_news"]
    assert "not_a_channel" not in next(e for e in events if e.step == "event_analysis").data["impact_channels"]
    assert events[-1].type == "final"


def test_no_bank_material_broadens_search_then_limits_output(make_agent, request_):
    client = ScriptedClient(
        {
            "analyze_event": [analysis_reply()],
            "plan": [plan_reply()],
            "personalize": [draft_reply(with_bank=False)],
        }
    )
    agent = make_agent(client, bank=BankIndex([]))
    insight = agent.run(request_)

    assert insight.status == InsightStatus.limited
    assert insight.self_check.adaptations == ["broadened_bank_search:+0"]
    assert insight.bank_view[0].kind == ClaimKind.unavailable
    assert "аналитика Альфа-Банка по теме события" in insight.missing_data
    adapt = [e for e in record_events(agent, insight) if e.type == "adapt"]
    assert adapt and adapt[0].data["new_fragments"] == 0


def test_invented_number_is_revised_with_feedback(make_agent, request_):
    client = ScriptedClient(
        {
            "analyze_event": [analysis_reply()],
            "plan": [plan_reply()],
            "personalize": [draft_reply(bad_number=True)],
            "revise": [draft_reply()],
        }
    )
    agent = make_agent(client)
    insight = agent.run(request_)

    assert client.steps() == ["analyze_event", "plan", "personalize", "revise"]
    assert insight.status == InsightStatus.complete
    assert insight.self_check.iterations == 2
    assert insight.self_check.adaptations == ["revise:unsupported_number"]
    assert all("21%" not in c.text for c in insight.why_it_matters)


def test_persistent_error_drops_unsupported_claim(make_agent, request_):
    client = ScriptedClient(
        {
            "analyze_event": [analysis_reply()],
            "plan": [plan_reply()],
            "personalize": [draft_reply(bad_number=True)],
            "revise": [draft_reply(bad_number=True)],
        }
    )
    agent = make_agent(client)
    insight = agent.run(request_)

    assert insight.status == InsightStatus.limited
    assert insight.self_check.iterations == agent.settings.agent_max_iterations
    assert all("21%" not in c.text for c in insight.why_it_matters)
    assert any("исключено утверждений без подтверждения: 1" == m for m in insight.missing_data)


def test_planner_failure_falls_back_to_rule_plan(make_agent, request_):
    client = ScriptedClient(
        {
            "analyze_event": [analysis_reply()],
            "plan": [LLMError("503")],
            "personalize": [draft_reply()],
        }
    )
    agent = make_agent(client)
    insight = agent.run(request_)

    plan = next(e for e in record_events(agent, insight) if e.type == "plan" and e.step == "plan")
    assert plan.data["by_llm"] is False
    assert plan.data["steps"][0]["tool"] == "search_bank_analytics"
    assert insight.status == InsightStatus.complete


def test_event_analysis_is_shared_between_roles(make_agent, request_):
    client = ScriptedClient(
        {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [draft_reply()]}
    )
    agent = make_agent(client)
    agent.run(request_)
    agent.run(request_.model_copy(update={"role": UserRole.ceo}))

    assert client.steps().count("analyze_event") == 1
    assert client.steps().count("personalize") == 2


def test_budget_exhausted_returns_failed_insight(make_agent, request_):
    client = ScriptedClient({"analyze_event": [analysis_reply()]})
    agent = make_agent(client)
    agent.settings.agent_max_tokens_per_run = 100
    insight = agent.run(request_)

    assert insight.status == InsightStatus.failed
    assert client.calls == []
    assert "лимит" in insight.missing_data[0]
