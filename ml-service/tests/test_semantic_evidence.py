"""Published-output semantic regressions; all source text and LLM replies are synthetic.

The fixture format is deliberately adapted to the existing agent API. No T07
registry, future verifier reply, or EvidenceSpan wire shape is assumed here.
Only the currently active source versions enter the real NewsStore/BankIndex.
The added user-provided analysis goal is an explicit request preference, not an
invented company exposure; the fixture's unknown business facts remain unknown.
"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path

import pytest

from fakes import ScriptedClient
from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.qa import answer_question
from insight_agent.schemas import BankFragment, CompanyProfile, InsightRequest, NewsEvent, SourceRef
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore


CASES_PATH = Path(__file__).parents[1] / "eval" / "material-semantic-cases.jsonl"
CASES = [json.loads(line) for line in CASES_PATH.read_text("utf-8").splitlines() if line.strip()]
CANDIDATES = [(case, candidate) for case in CASES for candidate in case["unsafe_candidates"]]
PUBLIC_CANDIDATES = [(case, candidate) for case, candidate in CANDIDATES
                     if all(source["kind"] == "news" for source in case["sources"])]
CLAIM_SECTIONS = ("what_happened", "why_it_matters", "consequences", "risks",
                  "opportunities", "actions", "bank_view")
GOAL_FEATURE = "user_provided.analysis_goal"


def _candidate_ids(values):
    return [candidate["candidate_id"] for _, candidate in values]


def _claim(value, *, kind=None):
    # Spans get their own tests after the actual span contract is integrated.
    return {"text": value["text"], "kind": kind or value["kind"],
            "source_ids": list(value["source_ids"]), "profile_features": []}


def _inputs(case):
    active = [source for source in case["sources"] if source["source_id"] in case["active_source_ids"]]
    public = [source for source in active if source["kind"] == "news"]
    fragments = [BankFragment(
        fragment_id=source["source_id"], doc_id=source["doc_id"], doc_title=source["title"],
        doc_date=source["published_at"][:10], page=source["page"], text=source["text"],
        source_url=source["url"], content_sha256=source["content_sha256"],
        review_status=source["review_status"], statement_type=source["statement_type"],
        target_period=source.get("target_period"), valid_until=source.get("valid_until"),
    ) for source in active if source["kind"] == "bank"]
    if not public:
        # A neutral request trigger is news-shaped; the bank evidence remains a
        # separate verified fragment retrieved through the real bank tool.
        text = "Запрошен анализ спроса по опубликованным материалам."
        public = [{"source_id": "synthetic:SEM11:request-trigger", "kind": "news",
                   "title": "Спрос: синтетический повод для анализа", "text": text,
                   "published_at": "2026-10-20T09:00:00Z", "url": "https://example.invalid/semantic/SEM11-trigger",
                   "content_sha256": hashlib.sha256(text.encode()).hexdigest()}]
    refs = [SourceRef(source_id=source["source_id"], kind="news", title=source["title"],
                      date=source["published_at"][:10], url=source["url"],
                      content_sha256=source["content_sha256"]) for source in public]
    event = NewsEvent(event_id="synthetic-event-" + case["case_id"], title=public[0]["title"],
                      text=public[0]["text"], published_at=public[0]["published_at"],
                      source=refs[0], related_sources=refs[1:])
    profile = CompanyProfile(
        company_id=case["profile_context"]["company_id"],
        industry=case["profile_context"]["known"].get("sector"),
        user_provided={"analysis_goal": "Проверить применимость опубликованного события к компании"},
    )
    request = InsightRequest(profile=profile, role="cfo", event=event,
                             as_of=datetime.fromisoformat(case["as_of"].replace("Z", "+00:00")))
    news = NewsStore([event])
    for source in public:
        news.register_source_text(source["source_id"], source["text"])
    return request, BankIndex(fragments), news


def _safe_script(case, request, *, analyst_candidate=None, draft_candidate=None, inference=False,
                 free_field=None, answer_candidate=None, answer_field=None):
    positive = _claim(case["positive_control"])
    has_bank = positive["kind"] == "assessment"
    source_id = request.event.source.source_id
    analysis = {
        "event_id": request.event.event_id, "summary": request.event.text,
        "facts": ([{"text": request.event.text, "kind": "fact", "source_ids": [source_id]}]
                  if has_bank else [positive]),
        "actors": [], "impact_channels": [],
        "bank_queries": ["замедления спроса" if has_bank else request.event.title], "open_questions": [],
    }
    if analyst_candidate is not None:
        analysis["facts"].append(_claim(analyst_candidate))
    draft = {
        "headline": "Проверка применимости сообщения",
        "why_it_matters": [{"text": "Для указанной цели анализа необходимо проверить применимость сообщения к деятельности компании.",
                            "kind": "inference", "source_ids": [source_id], "profile_features": [GOAL_FEATURE]}],
        "consequences": [{"text": "Если обстоятельства сообщения затрагивают компанию, возможно потребуется уточнить план действий.",
                          "kind": "hypothesis", "profile_features": [GOAL_FEATURE]}],
        "risks": [{"text": "Применимость сообщения к компании требует дополнительной проверки.",
                   "kind": "hypothesis", "profile_features": [GOAL_FEATURE], "level": "low"}],
        "opportunities": [], "scenarios": [],
        "actions": [{"text": "Проверить применимость сообщения к заявленной цели анализа.", "kind": "recommendation",
                     "source_ids": [source_id], "profile_features": [GOAL_FEATURE]}],
        "bank_view": ([positive] if has_bank else [{"text": "Подтверждённой позиции банка в этих материалах нет.", "kind": "unavailable"}]),
        "model_vs_bank": [], "impact_level": "low", "impact_rationale": "Влияние на компанию требует подтверждения.",
        "missing_data": ["применимость сообщения к деятельности компании"],
    }
    if draft_candidate is not None:
        if inference:
            draft["why_it_matters"].append(_claim(draft_candidate, kind="inference"))
        elif has_bank:
            # Valid bank-view envelope prevents a kind-only rejection from
            # masking a lost condition, forecast modality, or horizon.
            draft["bank_view"].append(_claim(draft_candidate, kind="assessment"))
        else:
            draft["why_it_matters"].append(_claim(draft_candidate))
    if free_field is not None:
        field, text = free_field
        if field.startswith("scenarios."):
            draft["scenarios"] = [{"name": "Проверка применимости", "trigger": "Если сведения относятся к компании",
                                    "implication": "Потребуется уточнить план действий", "plausibility": "medium",
                                    "source_ids": [source_id]}]
            draft["scenarios"][0][field.split(".")[1]] = text
        elif field in ("model_vs_bank", "missing_data"):
            draft[field] = [text]
        else:
            draft[field] = text
    answer = {"answerable": True, "text": positive["text"], "claims": [positive], "missing_data": []}
    if answer_candidate is not None:
        answer["claims"].append(_claim(answer_candidate, kind="assessment" if has_bank else None))
    if answer_field is not None:
        field, text = answer_field
        answer[field] = [text] if field == "missing_data" else text
    plan = {"steps": [{"tool": "search_bank_analytics", "args": {"query": analysis["bank_queries"][0], "min_score": 0},
                        "purpose": "Проверить наличие банковского источника"}]}
    return ScriptedClient({"analyze_event": [analysis], "plan": [plan], "personalize": [draft],
                           "revise": [deepcopy(draft)], "answer": [answer]})


def _run(case, settings, **mutation):
    request, bank, news = _inputs(case)
    client = _safe_script(case, request, **mutation)
    agent = InsightAgent(client, settings, bank, news)
    result = agent.run(request)
    return agent, result


def _published_texts(result):
    """Generated/renderable prose, excluding original source excerpts and debug traces."""
    data = result.model_dump(mode="json")
    for field in ("headline", "impact_rationale", "text"):
        if data.get(field):
            yield field, data[field]
    for field in (*CLAIM_SECTIONS, "claims"):
        for index, claim in enumerate(data.get(field, [])):
            yield f"{field}[{index}].text", claim["text"]
            if claim.get("horizon"):
                yield f"{field}[{index}].horizon", claim["horizon"]
    for field in ("model_vs_bank", "missing_data"):
        for index, text in enumerate(data.get(field, [])):
            yield f"{field}[{index}]", text
    for index, scenario in enumerate(data.get("scenarios", [])):
        for field in ("name", "trigger", "implication"):
            yield f"scenarios[{index}].{field}", scenario[field]


def _assert_positive_control(case, insight):
    assert insight.status != "failed", insight.missing_data
    assert insight.outcome in ("full", "limited"), insight.missing_data
    positive = case["positive_control"]
    section = insight.bank_view if positive["kind"] == "assessment" else insight.what_happened
    assert any(claim.text == positive["text"] and claim.source_ids == positive["source_ids"] for claim in section), (
        "A supported source quotation must survive; blanket refusal is not a semantic fix", insight.model_dump())
    assert any(claim.kind != "unavailable" for claim in insight.why_it_matters + insight.consequences)


def _assert_not_published(candidate, result):
    leaked = [(path, text) for path, text in _published_texts(result) if candidate["text"] in text]
    assert not leaked, f"{candidate['candidate_id']} published an unsupported assertion: {leaked}"


@pytest.mark.parametrize("case", CASES, ids=[case["case_id"] for case in CASES])
def test_faithful_source_control_remains_useful(case, settings):
    _, insight = _run(case, settings)
    _assert_positive_control(case, insight)
    for candidate in case["unsafe_candidates"]:
        _assert_not_published(candidate, insight)


@pytest.mark.parametrize("case,candidate", PUBLIC_CANDIDATES, ids=_candidate_ids(PUBLIC_CANDIDATES))
def test_analyst_semantic_substitution_is_absent_from_published_insight(case, candidate, settings):
    _, insight = _run(case, settings, analyst_candidate=candidate)
    _assert_positive_control(case, insight)
    _assert_not_published(candidate, insight)


@pytest.mark.parametrize("case,candidate", CANDIDATES, ids=_candidate_ids(CANDIDATES))
def test_personalizer_semantic_substitution_is_absent_from_published_insight(case, candidate, settings):
    _, insight = _run(case, settings, draft_candidate=candidate)
    _assert_positive_control(case, insight)
    _assert_not_published(candidate, insight)


@pytest.mark.parametrize("case,candidate", CANDIDATES, ids=_candidate_ids(CANDIDATES))
def test_correct_quote_does_not_license_false_external_premise_in_inference(case, candidate, settings):
    _, insight = _run(case, settings, draft_candidate=candidate, inference=True)
    _assert_positive_control(case, insight)
    _assert_not_published(candidate, insight)


@pytest.mark.parametrize("case,candidate", CANDIDATES, ids=_candidate_ids(CANDIDATES))
def test_qa_semantic_substitution_is_absent_from_published_answer(case, candidate, settings):
    agent, insight = _run(case, settings, answer_candidate=candidate)
    _assert_positive_control(case, insight)
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer is not None
    _assert_not_published(candidate, answer)


@pytest.mark.parametrize("field", ["headline", "impact_rationale", "model_vs_bank", "missing_data",
                                   "scenarios.name", "scenarios.trigger", "scenarios.implication"])
def test_unchecked_free_field_cannot_reintroduce_actor_substitution(field, settings):
    case = CASES[0]
    candidate = case["unsafe_candidates"][0]
    _, insight = _run(case, settings, free_field=(field, candidate["text"]))
    _assert_positive_control(case, insight)
    _assert_not_published(candidate, insight)


@pytest.mark.parametrize("field", ["text", "missing_data"])
def test_qa_free_field_cannot_reintroduce_actor_substitution(field, settings):
    case = CASES[0]
    candidate = case["unsafe_candidates"][0]
    agent, insight = _run(case, settings, answer_field=(field, candidate["text"]))
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer is not None
    _assert_not_published(candidate, answer)


def test_qa_faithful_actor_answer_retains_original_source_and_useful_text(settings):
    case = CASES[0]
    agent, insight = _run(case, settings)
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer is not None and answer.status == "answered"
    positive = case["positive_control"]
    assert positive["text"] in answer.text
    assert any(claim.text == positive["text"] and claim.source_ids == positive["source_ids"] for claim in answer.claims)
