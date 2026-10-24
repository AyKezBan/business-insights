"""Сценарии проверки T01, T02, T03, T07, T11, T20 через настоящий агент и фейковую LLM."""
from datetime import date
from decimal import Decimal

from fakes import FIXTURES, ScriptedClient, analysis_reply, draft_reply, plan_reply
from insight_agent.schemas import ClaimKind, InsightStatus, Products
from insight_agent.tools.bank import BankIndex
from insight_agent.trace import load_trace


def _script(personalize=None):
    return {
        "analyze_event": [analysis_reply()],
        "plan": [plan_reply()],
        "personalize": [personalize or draft_reply()],
        "revise": [personalize or draft_reply()],
    }


def _capturing(prompts):
    def reply(messages):
        prompts.append(messages[-1]["content"])
        return draft_reply()

    return reply


def test_t01_brand_new_event_goes_through_the_same_pipeline(make_agent, request_):
    fresh_event = request_.event.model_copy(update={"event_id": "evt_unseen_1", "title": "Новое событие", "text": "Совсем другая новость."})
    client = ScriptedClient(_script())
    agent = make_agent(client)
    insight = agent.run(request_.model_copy(update={"event": fresh_event}))

    assert insight.event_id == "evt_unseen_1" and insight.status != InsightStatus.failed
    assert client.steps().count("analyze_event") == 1


def test_t02_each_company_prompt_contains_only_its_own_signals(make_agent, request_):
    prompts_a, prompts_b = [], []
    with_loans = make_agent(ScriptedClient(_script(_capturing(prompts_a))))
    with_loans.run(request_)

    no_loans_profile = request_.profile.model_copy(
        update={"company_id": "U_OTHER", "products": Products(loans=False, leasing=False, deposits=True)}
    )
    other = make_agent(ScriptedClient(_script(_capturing(prompts_b))))
    other.run(request_.model_copy(update={"profile": no_loans_profile}))

    assert "products.loans" in prompts_a[0] and "products.deposits" not in prompts_a[0]
    assert "products.deposits" in prompts_b[0] and "products.loans" not in prompts_b[0]
    assert "U_OTHER" not in prompts_a[0] and "U_DEMO_001" not in prompts_b[0]


def test_t03_changed_product_flag_is_not_served_from_a_cached_card(make_agent, request_):
    prompts = []
    client = ScriptedClient(_script(_capturing(prompts)))
    agent = make_agent(client)
    agent.run(request_)
    changed = request_.profile.model_copy(update={"products": request_.profile.products.model_copy(update={"loans": False})})
    agent.run(request_.model_copy(update={"profile": changed}))

    assert client.steps().count("personalize") == 2
    assert "products.loans" in prompts[0] and "products.loans" not in prompts[1]


def test_t07_stale_bank_material_is_not_used_and_card_says_so(make_agent, request_):
    old = [f.model_copy(update={"doc_date": date(2025, 1, 10)}) for f in BankIndex.from_jsonl(FIXTURES / "bank_fragments.jsonl").fragments]
    client = ScriptedClient(_script(draft_reply(with_bank=False)))
    agent = make_agent(client, bank=BankIndex(old))
    insight = agent.run(request_)

    assert insight.status == InsightStatus.limited
    assert insight.bank_view[0].kind == ClaimKind.unavailable
    assert all(s.kind.value != "bank" for s in insight.sources)
    assert "аналитика Альфа-Банка по теме события" in insight.missing_data


def test_t11_placeholder_revenue_is_reported_missing_and_never_used(make_agent, request_):
    profile = request_.profile.model_copy(update={"revenue_rub": Decimal(1000)})
    client = ScriptedClient(_script())
    agent = make_agent(client)
    insight = agent.run(request_.model_copy(update={"profile": profile}))

    assert "revenue_rub" in insight.missing_data
    assert not {"revenue_rub", "group_revenue_rub"} & set(insight.profile_features_used)
    start = load_trace(agent.settings.trace_dir, insight.run_id)[0].data
    assert "revenue_rub" not in start["signals"]


def test_t20_every_cited_source_is_listed_and_disclaimer_is_present(make_agent, request_):
    insight = make_agent(ScriptedClient(_script())).run(request_)
    listed = {s.source_id for s in insight.sources}
    sections = [insight.what_happened, insight.why_it_matters, insight.consequences, insight.risks,
                insight.opportunities, insight.actions, insight.bank_view]
    cited = {sid for section in sections for c in section for sid in c.source_ids}

    assert cited <= listed
    assert all(s.title for s in insight.sources)
    bank = next(s for s in insight.sources if s.kind.value == "bank")
    assert bank.page is not None
    assert "не является" in insight.disclaimer and "вывод модели" in insight.disclaimer
