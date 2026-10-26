"""Integrity of the bounded semantic gate, independent of model entailment quality."""
from copy import deepcopy
import json

import pytest

from insight_agent.agent.semantic import SemanticBatch, semantic_issues
from insight_agent.agent.state import AgentState
from insight_agent.llm import LLMError
from insight_agent.schemas import EvidenceSpan, PersonalizationDraft
from insight_agent.tools.profile import derive_signals, missing_profile_data
from fakes import draft_reply


class Verifier:
    def __init__(self, mutation=None, failure=None):
        self.calls = []
        self.mutation = mutation
        self.failure = failure
        self.tracer = type("Trace", (), {"emit": lambda *a, **kw: None})()

    def generate(self, step, model, messages, schema, retries):
        payload = json.loads(messages[-1]["content"])
        self.calls.append((step, payload, retries))
        if self.failure:
            raise self.failure
        available = {s["source"]["source_id"]: s["spans"][0]["span_id"] for s in payload["evidence"] if s["spans"]}
        verdicts = [{"claim_id": c["claim_id"], "status": "supported", "reason": "Reviewed synthetic expectation",
                     "span_ids": [available[sid] for sid in c["content"].get("source_ids", [])]} for c in payload["candidates"]]
        if self.mutation:
            self.mutation(verdicts, payload)
        return schema.model_validate({"verdicts": verdicts})


def state_and_draft(request_):
    signals, missing = derive_signals(request_.profile), missing_profile_data(request_.profile)
    state = AgentState("synthetic-run", request_, signals, missing)
    state.resolved_news_texts[request_.event.source.source_id] = request_.event.text
    draft = PersonalizationDraft.model_validate(draft_reply(with_bank=False))
    return state, draft


def test_every_published_generated_surface_is_in_one_batch(request_):
    state, draft = state_and_draft(request_)
    verifier = Verifier()
    assert semantic_issues(verifier, "model", state, draft) == []
    assert len(verifier.calls) == 1 and verifier.calls[0][2] == 1
    candidates = verifier.calls[0][1]["candidates"]
    paths = {c["location"] for c in candidates}
    assert {"why_it_matters[0]", "consequences[0]", "risks[0]", "opportunities[0]",
            "actions[0]", "bank_view[0]", "scenarios[0]", "missing_data[0]"} <= paths
    risk = next(c for c in candidates if c["location"] == "risks[0]")
    assert risk["content"]["horizon"] == draft.risks[0].horizon
    scenario = next(c for c in candidates if c["location"] == "scenarios[0]")
    assert set(scenario["content"]) >= {"name", "trigger", "implication"}


@pytest.mark.parametrize("mutation", [
    lambda v, p: v.pop(),
    lambda v, p: v.append(deepcopy(v[0])),
    lambda v, p: v[0].update(claim_id="foreign-candidate"),
    lambda v, p: v[0].update(span_ids=["foreign-span"]),
    lambda v, p: v[0].update(span_ids=[]),
])
def test_malformed_verdict_rejects_only_its_own_claim(request_, mutation):
    """Fail closed per claim: a claim passes only with exactly one well-formed verdict, and one malformed
    verdict does not discard the correctly verified others (live runs lost whole cards to this)."""
    state, draft = state_and_draft(request_)
    verifier = Verifier(mutation)
    issues = semantic_issues(verifier, "model", state, draft)
    locations = {c["location"] for c in verifier.calls[0][1]["candidates"]}
    assert len(issues) == 1 and issues[0].location in locations and issues[0].severity == "error"
    assert issues[0].code == "semantic_unverified"


def test_unreadable_verdict_schema_still_rejects_the_whole_batch(request_):
    state, draft = state_and_draft(request_)
    verifier = Verifier(lambda v, p: v[0].update(status="maybe"))
    issues = semantic_issues(verifier, "model", state, draft)
    assert {x.location for x in issues} == {c["location"] for c in verifier.calls[0][1]["candidates"]}
    assert all(x.severity == "error" for x in issues)


def test_unresolved_claims_are_named_in_the_trace_with_a_reason(request_):
    state, draft = state_and_draft(request_)
    verifier = Verifier(lambda v, p: v[0].update(span_ids=["foreign-span"]))
    events = []
    verifier.tracer = type("Trace", (), {"emit": lambda self, kind, step, data=None: events.append((step, data))})()
    semantic_issues(verifier, "model", state, draft)
    data = next(d for step, d in events if step == "semantic_verify")
    assert [u["reason"] for u in data["unresolved"]] == ["foreign_evidence"]


def test_unsupported_reason_and_provider_text_never_become_visible_issues(request_):
    state, draft = state_and_draft(request_)
    poison = "UNSUPPORTED_ACTOR_ASSERTION"
    verifier = Verifier(lambda v, p: v[0].update(status="unsupported", reason=poison))
    issues = semantic_issues(verifier, "model", state, draft)
    assert len(issues) == 1 and poison not in issues[0].model_dump_json()
    failed = semantic_issues(Verifier(failure=LLMError(poison)), "model", state, draft)
    assert failed and poison not in json.dumps([x.model_dump() for x in failed])


def test_verdict_ids_bind_exact_candidate_and_context(request_):
    state, draft = state_and_draft(request_)
    one, two = Verifier(), Verifier()
    semantic_issues(one, "model", state, draft)
    draft.why_it_matters[0].text += " Changed."
    semantic_issues(two, "model", state, draft)
    before = {c["location"]: c["claim_id"] for c in one.calls[0][1]["candidates"]}
    after = {c["location"]: c["claim_id"] for c in two.calls[0][1]["candidates"]}
    assert before["why_it_matters[0]"] != after["why_it_matters[0]"]


def test_generated_proof_is_replaced_only_with_verifier_owned_spans(request_):
    state, draft = state_and_draft(request_)
    target = draft.why_it_matters[0]
    target.evidence_spans = [EvidenceSpan(source_id="foreign-source", content_sha256="a" * 64, start=0, end=1)]
    verifier = Verifier()
    assert semantic_issues(verifier, "model", state, draft) == []
    assert target.evidence_spans and all(s.source_id in target.source_ids for s in target.evidence_spans)
    assert all(s.source_id != "foreign-source" for s in target.evidence_spans)
    target.evidence_spans = [EvidenceSpan(source_id="foreign-source", content_sha256="a" * 64, start=0, end=1)]
    assert semantic_issues(Verifier(failure=LLMError("unavailable")), "model", state, draft)
    assert target.evidence_spans == []


def test_overflow_does_not_authorize_unchecked_tail(request_):
    state, draft = state_and_draft(request_)
    draft.missing_data = [f"Unknown synthetic field {i}" for i in range(100)]
    verifier = Verifier()
    issues = semantic_issues(verifier, "model", state, draft)
    assert not verifier.calls
    assert {f"missing_data[{i}]" for i in range(100)} <= {x.location for x in issues}


def test_verdict_schema_forbids_extra_fields_and_boolean_status():
    for verdict in ({"claim_id": "a", "status": True, "reason": "x", "span_ids": []},
                    {"claim_id": "a", "status": "supported", "reason": "x", "span_ids": [], "trust_me": True}):
        with pytest.raises(ValueError):
            SemanticBatch.model_validate({"verdicts": [verdict]})
