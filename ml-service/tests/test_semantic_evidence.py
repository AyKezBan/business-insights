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
from insight_agent.llm import LLMError
from insight_agent.schemas import BankFragment, CompanyProfile, InsightRequest, NewsEvent, SourceRef
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore
from insight_agent.trace import load_trace


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
    # The oracle approves only this separately reviewed useful baseline. Mutated
    # claims below are never added to its expectations, even with valid sources.
    reviewed = []
    for section in ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view"):
        for claim in draft[section]:
            if claim["kind"] not in ("fact", "assessment"):
                reviewed.append({"claim_id": None, "source_ids": [], "profile_features": [],
                                 **deepcopy(claim)})
    reviewed += [{"missing_data": text} for text in draft["missing_data"]]
    def canonical(content):
        return json.dumps({k: v for k, v in content.items() if k not in ("claim_id", "evidence_spans")}, sort_keys=True)
    # Pydantic includes nullable Impact.horizon; bind it explicitly too.
    for content in reviewed:
        if "level" in content:
            content.setdefault("horizon", None)
    expected = {canonical(content) for content in reviewed}
    def semantic_reply(messages):
        payload = json.loads(messages[-1]["content"])
        spans = {s["source"]["source_id"]: s["spans"][0]["span_id"]
                 for s in payload["evidence"] if s["spans"]}
        verdicts = []
        for candidate in payload["candidates"]:
            supported = canonical(candidate["content"]) in expected
            verdicts.append({"claim_id": candidate["claim_id"],
                             "status": "supported" if supported else "unsupported",
                             "reason": "Explicit reviewed synthetic fixture expectation",
                             "span_ids": [spans[s] for s in candidate["content"].get("source_ids", [])]
                             if supported else []})
        return {"verdicts": verdicts}
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
                           "revise": [deepcopy(draft)], "answer": [answer],
                           "semantic_verify": [semantic_reply, semantic_reply]})


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
    for index, issue in enumerate(data.get("self_check", {}).get("issues", [])):
        yield f"self_check.issues[{index}]", issue["message"]


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


def test_verifier_failure_removes_pending_generated_text_but_keeps_source_quote(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request, draft_candidate=candidate, inference=True)
    client.script["semantic_verify"] = [LLMError(candidate["text"]), LLMError(candidate["text"])]
    agent = InsightAgent(client, settings, bank, news)
    result = agent.run(request)
    assert result.outcome == "refused" and not result.self_check.passed
    assert any(c.text == case["positive_control"]["text"] for c in result.what_happened)
    assert all(c.kind == "unavailable" for c in result.why_it_matters + result.consequences)
    _assert_not_published(candidate, result)
    assert client.steps().count("semantic_verify") == 2  # primary plus configured fallback, one batch


def test_revised_draft_does_not_inherit_support_after_verifier_failure(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request, draft_candidate=candidate, inference=True)
    client.script["personalize"][0]["why_it_matters"][0]["profile_features"] = ["unknown_profile_feature"]
    client.script["semantic_verify"] = [LLMError("offline verifier unavailable"), LLMError("offline verifier unavailable")]
    result = InsightAgent(client, settings, bank, news).run(request)
    assert "revise" in client.steps()
    assert result.outcome == "refused"
    _assert_not_published(candidate, result)


def test_qa_verifier_failure_cannot_publish_pending_inference_or_exception(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request)
    client.script["answer"][0]["claims"].append(_claim(candidate, kind="inference"))
    client.script["semantic_verify"] = [client.script["semantic_verify"][0],
                                        LLMError(candidate["text"]), LLMError(candidate["text"])]
    agent = InsightAgent(client, settings, bank, news)
    insight = agent.run(request)
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer.status == "answered"
    assert case["positive_control"]["text"] in answer.text
    assert any("Часть утверждений исключена" in reason for reason in answer.missing_data)
    _assert_not_published(candidate, answer)


def test_verifier_reason_never_leaks_through_public_check_or_missing_data(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request, draft_candidate=candidate, inference=True)
    reviewed = client.script["semantic_verify"][0]
    def poisoned_reason(messages):
        response = reviewed(messages)
        for verdict in response["verdicts"]:
            if verdict["status"] == "unsupported":
                verdict["reason"] = candidate["text"]
        return response
    client.script["semantic_verify"] = [poisoned_reason]
    result = InsightAgent(client, settings, bank, news).run(request)
    _assert_positive_control(case, result)
    _assert_not_published(candidate, result)


def test_generator_schema_failure_does_not_publish_rejected_payload(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request)
    client.script["personalize"] = [{"why_it_matters": candidate["text"]}]
    result = InsightAgent(client, settings, bank, news).run(request)
    assert result.status == "failed"
    _assert_not_published(candidate, result)


def test_qa_uses_sources_not_persisted_false_parent_claims(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request)
    agent = InsightAgent(client, settings, bank, news)
    insight = agent.run(request)
    agent.records[insight.insight_id].insight.why_it_matters[0].text = candidate["text"]
    response = deepcopy(client.script["answer"][0])
    def check_prompt(messages):
        assert candidate["text"] not in messages[-1]["content"]
        return response
    client.script["answer"] = [check_prompt]
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer.status == "answered"
    _assert_not_published(candidate, answer)


@pytest.mark.parametrize("section,kind", [("consequences", "hypothesis"), ("actions", "recommendation"),
                                          ("bank_view", "unavailable")])
def test_claim_label_does_not_exempt_false_external_premise(section, kind, settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request)
    client.script["personalize"][0][section].append(_claim(candidate, kind=kind))
    result = InsightAgent(client, settings, bank, news).run(request)
    _assert_positive_control(case, result)
    _assert_not_published(candidate, result)


def test_risk_horizon_cannot_publish_false_external_assertion(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    candidate = case["unsafe_candidates"][0]
    client = _safe_script(case, request)
    client.script["personalize"][0]["risks"][0]["horizon"] = candidate["text"]
    result = InsightAgent(client, settings, bank, news).run(request)
    _assert_positive_control(case, result)
    _assert_not_published(candidate, result)


def test_qa_final_usage_includes_the_shared_verifier_run(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    client = _safe_script(case, request)
    client.script["answer"][0]["claims"].append(deepcopy(client.script["personalize"][0]["why_it_matters"][0]))
    agent = InsightAgent(client, settings, bank, news)
    insight = agent.run(request)
    answer = answer_question(agent, insight.insight_id, case["question"])
    assert answer.status == "answered" and len(answer.claims) == 2
    events = load_trace(settings.trace_dir, answer.run_id)
    assert events[-1].type == "final" and events[-1].step == "answer"
    assert events[-1].data["usage"]["calls"] == 2
    assert events[-1].data["usage"]["tokens"] == 2400
    assert [e.step for e in events if e.type == "llm_call"] == ["answer", "semantic_verify"]
    bindings = next(e.data["claims"] for e in events if e.step == "published_claims")
    verified = next(e.data for e in events if e.type == "check" and e.step == "semantic_verify")
    assert {b["claim_id"] for b in bindings} == {c.claim_id for c in answer.claims}
    assert bindings[1]["semantic_candidate_id"] in {v["claim_id"] for v in verified["verdicts"]}


def test_public_claims_trace_to_exact_reviewed_candidates_and_quote_spans(settings):
    case = CASES[0]
    request, bank, news = _inputs(case)
    client = _safe_script(case, request)
    client.script["analyze_event"][0]["facts"][0]["claim_id"] = "sv_forged_by_model"
    client.script["personalize"][0]["why_it_matters"][0]["claim_id"] = "sv_also_forged"
    agent = InsightAgent(client, settings, bank, news)
    result = agent.run(request)
    events = load_trace(settings.trace_dir, result.run_id)
    bindings = next(e.data["claims"] for e in events if e.step == "published_claims")
    verified = next(e.data for e in events if e.type == "check" and e.step == "semantic_verify")
    candidates = {v["claim_id"] for v in verified["candidate_bindings"]}
    mapping = {b["claim_id"]: b for b in bindings}
    quote, inference = result.what_happened[0], result.why_it_matters[0]
    assert mapping[quote.claim_id]["semantic_candidate_id"] is None
    assert mapping[quote.claim_id]["evidence_spans"] == [s.model_dump() for s in quote.evidence_spans]
    assert mapping[inference.claim_id]["semantic_candidate_id"] in candidates
    assert mapping[inference.claim_id]["text_sha256"] == hashlib.sha256(inference.text.encode()).hexdigest()
    assert all(b["semantic_candidate_id"] not in ("sv_forged_by_model", "sv_also_forged") for b in bindings)
