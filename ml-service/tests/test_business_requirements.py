"""Принятые бизнес-решения BR-01, BR-02, BR-06, BR-07, BR-08 (БИЗНЕС_ТРЕБОВАНИЯ_И_ПРИНЯТЫЕ_РЕШЕНИЯ.md), поведение ML."""
from fakes import ScriptedClient, analysis_reply, draft_reply, plan_reply, reviewed_semantics, semantic_contents
from insight_agent.schemas import ClaimKind, Counterparty, InsightStatus, Level, Outcome, Products
from insight_agent.tools.profile import derive_signals


def _script(personalize):
    draft = personalize if isinstance(personalize, dict) else draft_reply()
    return {"analyze_event": [analysis_reply()], "plan": [plan_reply()], "personalize": [personalize], "revise": [personalize],
            "semantic_verify": [reviewed_semantics(semantic_contents(draft))]}


def _run(make_agent, request_, draft, profile=None):
    request = request_ if profile is None else request_.model_copy(update={"profile": profile})
    agent = make_agent(ScriptedClient(_script(draft)))
    return agent.run(request)


def test_br01_event_without_link_to_the_company_is_not_a_personal_insight(make_agent, request_):
    draft = draft_reply()
    draft.update(company_link="none", impact_level="high")
    insight = _run(make_agent, request_, draft)

    assert (insight.status, insight.outcome) == (InsightStatus.limited, Outcome.refused)
    assert insight.impact_level == Level.low
    assert "связь события с этой компанией не установлена" in insight.missing_data


def test_br01_a_clear_link_keeps_a_useful_card(make_agent, request_):
    insight = _run(make_agent, request_, draft_reply())
    assert insight.outcome == Outcome.full and insight.impact_level == Level.high


def test_br02_high_priority_does_not_survive_without_personal_grounds(make_agent, request_):
    draft = draft_reply()
    draft["impact_level"] = "high"
    for section in ("why_it_matters", "consequences", "risks", "opportunities", "actions"):
        for claim in draft[section]:
            claim["profile_features"] = []
    insight = _run(make_agent, request_, draft)

    assert insight.impact_level == Level.low
    assert insight.outcome == Outcome.refused
    assert any("приоритет понижен" in m for m in insight.missing_data)


def test_br02_limited_but_grounded_card_may_keep_its_priority(make_agent, request_):
    insight = _run(make_agent, request_, draft_reply(with_bank=False))
    assert insight.status == InsightStatus.limited and insight.outcome == Outcome.limited
    assert insight.impact_level == Level.high


def test_br06_known_absent_product_is_context_for_the_model_but_not_a_citable_feature(make_agent, request_):
    prompts = []

    def personalize(messages):
        prompts.append(messages[-1]["content"])
        return draft_reply()

    profile = request_.profile.model_copy(update={"products": Products(loans=True, leasing=True, deposits=False)})
    _run(make_agent, request_, personalize, profile)

    prompt = prompts[0]
    assert "В Альфа-Банке известно, что НЕТ: депозиты" in prompt and "не означает отсутствие" in prompt
    features = {s.feature for s in derive_signals(profile)}
    assert "products.deposits" not in features  # backend принимает только подтверждённые продукты


def test_br06_unknown_product_is_not_reported_as_absent(make_agent, request_):
    prompts = []

    def personalize(messages):
        prompts.append(messages[-1]["content"])
        return draft_reply()

    profile = request_.profile.model_copy(update={"products": Products(loans=True)})
    _run(make_agent, request_, personalize, profile)
    assert "Известных отсутствующих продуктов нет" in prompts[0]


def test_br06_and_br08_rules_are_in_the_instruction(make_agent, request_):
    seen = []

    def personalize(messages):
        seen.append(messages[0]["content"])
        return draft_reply()

    _run(make_agent, request_, personalize)
    rules = seen[0]
    assert "company_link" in rules and "не доказывает отсутствие долгов" in rules
    assert "выручку юрлица не называй выручкой группы" in rules and "страны ФТС не доказывают импорт" in rules


def test_br07_counterparty_share_names_its_scope():
    profile = _profile_with_counterparty()
    note = {s.feature: s for s in derive_signals(profile)}["counterparties[0]"].note
    assert "наблюдаемому допустимому потоку" in note and "не ко всем платежам" in note


def _profile_with_counterparty():
    from insight_agent.schemas import CompanyProfile

    return CompanyProfile(company_id="U_1", counterparties=[Counterparty(company_id="U_2", direction="incoming", share_of_flow=0.4)])


def test_br06_claim_kinds_keep_hypothesis_separate_from_facts(request_):
    assert ClaimKind.hypothesis.value == "hypothesis" and ClaimKind.fact.value != ClaimKind.hypothesis.value
