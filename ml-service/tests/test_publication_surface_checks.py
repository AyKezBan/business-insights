"""Adversarial publication checks and bounded complete quote choices; offline only."""

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import runpy
import shutil

import pytest

from insight_agent.agent import versions
from insight_agent.agent.evidence import quote_options
from insight_agent.agent.spans import validate_span
from insight_agent.evaluation import Expect, ManifestRow, _card_text, automatic_checks
from insight_agent.schemas import Answer, EvidenceSpan, Insight


POISON = "Банк подтвердил безусловный рост прибыли компании на 99%."


def card(**changes):
    return Insight.model_validate({
        "insight_id": "synthetic-card", "run_id": "run_synthetic", "event_id": "event", "company_id": "company",
        "role": "cfo", "status": "complete", "outcome": "full", "headline": "Проверенный заголовок",
        "what_happened": [], "why_it_matters": [], "consequences": [], "risks": [], "opportunities": [],
        "actions": [], "bank_view": [], "scenarios": [], "model_vs_bank": [], "impact_level": "low",
        "impact_rationale": "Условия договоров требуют проверки.", "profile_features_used": [], "sources": [],
        "missing_data": [], "self_check": {"passed": True, "issues": []}, "models": [],
        "generated_at": datetime(2026, 10, 26, tzinfo=UTC), **changes,
    })


def manifest(request_):
    return ManifestRow(test_id="publication-prose", fixture_id="synthetic", request=request_,
                       expect=Expect(forbid_text=[POISON.swapcase()]))


@pytest.mark.parametrize("surface", [
    "risks.horizon", "opportunities.horizon", "scenarios.name", "scenarios.trigger", "scenarios.implication",
    "missing_data", "model_vs_bank", "self_check.issues",
])
def test_forbidden_assertion_in_nonheadline_surface_fails_automatic_checks(request_, surface):
    baseline = card()
    row = manifest(request_)
    assert all(automatic_checks(row, baseline).values())
    field, _, nested = surface.partition(".")
    if field in ("risks", "opportunities"):
        changes = {field: [{"text": "Условия требуют проверки.", "kind": "hypothesis", "level": "low", "horizon": POISON}]}
    elif field == "scenarios":
        scenario = {"name": "Условный сценарий", "trigger": "Если условия изменятся", "implication": "Проверить договор.", "plausibility": "low"}
        scenario[nested] = POISON
        changes = {field: [scenario]}
    elif field == "self_check":
        changes = {field: {"passed": False, "issues": [{"code": "semantic_unsupported", "severity": "error", "message": POISON}]}}
    else:
        changes = {field: [POISON]}
    checks = automatic_checks(row, card(**changes))
    assert checks["forbidden_text_absent"] is False, surface
    assert all(value for name, value in checks.items() if name != "forbidden_text_absent")


@pytest.mark.parametrize("surface", ["text", "claims", "missing_data"])
def test_answer_text_enumerator_exposes_poison_on_every_published_surface(surface):
    data = {"insight_id": "synthetic-card", "run_id": "run_answer", "status": "answered",
            "text": "Условия требуют проверки.", "claims": [], "missing_data": []}
    data[surface] = ([{"text": POISON, "kind": "inference"}] if surface == "claims"
                     else [POISON] if surface == "missing_data" else POISON)
    assert POISON.lower() in _card_text(Answer.model_validate(data))


def test_source_excerpt_does_not_impersonate_generated_card_or_answer_prose(request_):
    source = {"source_id": "source", "kind": "news", "title": "Synthetic source", "excerpt": POISON}
    assert automatic_checks(manifest(request_), card(sources=[source]))["forbidden_text_absent"] is True
    answer = Answer(insight_id="synthetic-card", run_id="run_answer", status="answered",
                    text="Условия требуют проверки.", sources=[source])
    assert POISON.lower() not in _card_text(answer)
    assert automatic_checks(manifest(request_), card(sources=[source], missing_data=[POISON]))["forbidden_text_absent"] is False


def assert_exact_choices(serialized, texts):
    choices = json.loads(serialized)
    for choice in choices:
        span = EvidenceSpan.model_validate(choice["evidence_span"])
        full = texts[span.source_id]
        digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
        assert span.content_sha256 == digest
        assert validate_span(span, full, digest) == choice["text"]
    return choices


def test_quote_options_keep_negation_unicode_offsets_and_full_source_hash():
    source = " \t📈 ФНС не продлевает срок.\r\nЁмкость выросла на 5%.\n"
    texts = {"opaque:source_v2": source}
    choices = assert_exact_choices(quote_options(texts), texts)
    assert [c["text"] for c in choices] == ["📈 ФНС не продлевает срок.", "Ёмкость выросла на 5%."]
    for choice in choices:
        start = source.index(choice["text"])
        assert (choice["evidence_span"]["start"], choice["evidence_span"]["end"]) == (start, start + len(choice["text"]))
    second_start = choices[1]["evidence_span"]["start"]
    assert second_start != len(source[:second_start].encode("utf-8"))
    assert "продлевает срок." not in [c["text"] for c in choices], "Negation must not be cropped away"


def test_quote_options_apply_one_32_choice_cap_across_sources_without_cropping():
    texts = {f"source:{i}": f"ФНС не продлевает срок для сценария {i}." for i in range(40)}
    serialized = quote_options(texts, limit=100_000)
    choices = assert_exact_choices(serialized, texts)
    assert len(choices) == 32
    assert len({c["evidence_span"]["source_id"] for c in choices}) == 32
    assert all(c["text"] == texts[c["evidence_span"]["source_id"]] for c in choices)


def test_serialized_quote_options_never_exceed_the_requested_total_bound():
    texts = {"source": " ".join(f"ФНС не продлевает срок для сценария {i}." for i in range(5))}
    full = quote_options(texts, limit=100_000)
    assert len(json.loads(full)) == 5
    limit = len(full) - 1
    bounded = quote_options(texts, limit=limit)
    assert len(bounded) <= limit, "JSON separators count toward the complete serialized context bound"
    assert len(assert_exact_choices(bounded, texts)) < 5


def test_oversize_news_unit_is_omitted_not_shortened_and_later_complete_unit_survives():
    oversized = "ФНС не продлевает срок " + "при сохранении условий " * 80 + "для компании."
    short = "Дата остаётся прежней."
    source = oversized + " " + short
    limit = 512  # Includes the later unit's larger decimal offsets and JSON metadata.
    texts = {"source": source}
    choices = assert_exact_choices(quote_options(texts, limit=limit), texts)
    assert [c["text"] for c in choices] == [short]
    assert choices[0]["evidence_span"]["start"] == len(oversized) + 1


def test_bank_quote_option_keeps_full_condition_and_expiry_or_is_omitted():
    first = "При сохранении политики банк не ожидает снижения ставки до конца года."
    bank_text = first + " После этой даты оценка недействительна."
    texts = {"bank:opaque": bank_text}
    complete = assert_exact_choices(quote_options(texts, bank_ids=texts), texts)
    assert len(complete) == 1 and complete[0]["text"] == bank_text
    assert (complete[0]["evidence_span"]["start"], complete[0]["evidence_span"]["end"]) == (0, len(bank_text))
    # There is room for the attractive first sentence, but not for its expiry qualifier.
    limit = len(quote_options({"bank:opaque": first})) + 5
    assert limit < len(quote_options(texts, bank_ids=texts))
    assert json.loads(quote_options(texts, bank_ids=texts, limit=limit)) == []


@pytest.mark.parametrize("relative,before,after", [
    ("agent/semantic.py", "MAX_CANDIDATES = 48", "MAX_CANDIDATES = 47"),
    ("agent/spans.py", "if kind == ClaimKind.assessment", "if kind != ClaimKind.assessment"),
    ("schemas.py", "question: str = Field(min_length=1, max_length=1000)", "question: str = Field(min_length=1, max_length=999)"),
    ("llm.py", "if self.used > self.limit:", "if self.used >= self.limit:"),
])
def test_default_policy_version_invalidates_when_acceptance_policy_changes(tmp_path, relative, before, after):
    # Execute the real default dependency selection in a temporary copy; editing an
    # omitted policy module would leave this version unchanged and stale caches valid.
    source = Path(versions.__file__).resolve().parents[1]
    copied = tmp_path / "insight_agent"
    shutil.copytree(source, copied, ignore=shutil.ignore_patterns("__pycache__"))
    version_file = copied / "agent/versions.py"
    baseline = runpy.run_path(str(version_file))["PROMPT_VERSION"]
    policy = copied / relative
    text = policy.read_text("utf-8")
    assert before in text, "The intended acceptance-rule mutation must remain meaningful"
    policy.write_text(text.replace(before, after, 1), encoding="utf-8")
    assert runpy.run_path(str(version_file))["PROMPT_VERSION"] != baseline
