"""Exact source quotations; all evidence here is synthetic and local."""
import hashlib
import importlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from insight_agent import schemas
from insight_agent.schemas import BankFragment, Claim, SourceRef


def api():
    assert hasattr(schemas, "EvidenceSpan"), "The exact evidence-span contract is missing"
    return schemas.EvidenceSpan, importlib.import_module("insight_agent.agent.spans")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def span_for(text, quote=None, sid="opaque:source_v2"):
    Span, _ = api()
    quote = text if quote is None else quote
    start = text.index(quote)
    return Span(source_id=sid, content_sha256=sha(text), start=start, end=start + len(quote))


def news(text, sid="opaque:source_v2", **updates):
    return SourceRef(source_id=sid, kind="news", title="Synthetic", content_sha256=sha(text), **updates)


@pytest.mark.parametrize("updates", [
    {"start": True}, {"start": "0"}, {"start": 0.0}, {"start": -1},
    {"end": False}, {"end": "2"}, {"end": 2.0}, {"end": 0},
    {"start": 2, "end": 1}, {"source_id": ""}, {"content_sha256": "abc"}, {"unknown": 1},
])
def test_span_rejects_invalid_wire_values(updates):
    Span, _ = api()
    data = dict(source_id="a", content_sha256="a" * 64, start=0, end=2)
    with pytest.raises(ValidationError):
        Span(**(data | updates))


def test_span_id_binds_full_opaque_tuple_without_extra_wire_field():
    Span, _ = api()
    span = Span(source_id="opaque:a:b_v7", content_sha256="a" * 64, start=1, end=9)
    assert set(span.model_dump()) == {"source_id", "content_sha256", "start", "end"}
    assert span.span_id == Span(**span.model_dump()).span_id
    changes = [{"source_id": "opaque:a_b:v7"}, {"content_sha256": "b" * 64}, {"start": 2}, {"end": 10}]
    assert all(span.span_id != Span(**(span.model_dump() | change)).span_id for change in changes)
    assert Claim(text="Legacy", kind="fact").evidence_spans == []


@pytest.mark.parametrize("text,quotes", [
    ("ФНС не продлевает срок. Действующий срок сохраняется.", ["ФНС не продлевает срок.", "Действующий срок сохраняется."]),
    ("U.S. natural gas production increased. Demand fell.", ["U.S. natural gas production increased.", "Demand fell."]),
    ("The U.S. Treasury did not approve the payment.", ["The U.S. Treasury did not approve the payment."]),
    ("EIA expects U.S. output to rise by 2.5 percent if assumptions hold.", ["EIA expects U.S. output to rise by 2.5 percent if assumptions hold."]),
    ("В 2026 г. ООО «Маяк» не платило дивиденды. Срок сохранён.", ["В 2026 г. ООО «Маяк» не платило дивиденды.", "Срок сохранён."]),
    ("А. С. Петров сообщил: ставка не изменена.", ["А. С. Петров сообщил: ставка не изменена."]),
    ("«Срок продлён? Нет», сообщила ФНС. Изменений нет.", ["«Срок продлён? Нет», сообщила ФНС.", "Изменений нет."]),
    ("«Срок не продлён.» Дата сохраняется.", ["«Срок не продлён.»", "Дата сохраняется."]),
    ("  📈 Рост составил 5%.\r\nЁмкость выросла.\t", ["📈 Рост составил 5%.", "Ёмкость выросла."]),
    ("Срок не изменён?! Дата прежняя.", ["Срок не изменён?!", "Дата прежняя."]),
    ("Срок не изменён. Незавершённое продолжение", ["Срок не изменён."]),
])
def test_complete_units_preserve_multilingual_sentence_boundaries(text, quotes):
    _, spans = api()
    units = spans.sentence_spans("opaque", text)
    assert [text[s.start:s.end] for s in units] == quotes
    assert all(spans.validate_span(s, text, sha(text)) == q for s, q in zip(units, quotes))


@pytest.mark.parametrize("text,quote", [
    ("Компания не перечислила платёж.", "перечислила платёж."),
    ("При сохранении политики банк ожидает спад.", "банк ожидает спад."),
    ("Банк ожидает спад, если политика сохранится.", "Банк ожидает спад"),
    ("U.S. production increased.", "S. production increased."),
    ("А. С. Петров сообщил: ставка не изменена.", "С. Петров сообщил: ставка не изменена."),
    ("Ставка составила 2.5 процента.", "5 процента."),
    ("Нет изменений...", "Нет изменений..."),
    ("Нет изменений…", "Нет изменений…"),
    ("Нет завершения", "Нет завершения"),
    ("100.", "100."),
    ("?!", "?!"),
    ("«Нет закрывающей кавычки.", "«Нет закрывающей кавычки."),
])
def test_partial_or_incomplete_quotation_is_rejected(text, quote):
    _, spans = api()
    with pytest.raises(ValueError):
        spans.validate_span(span_for(text, quote), text, sha(text))


def test_full_body_fallback_requires_complete_body_and_preserves_whitespace():
    _, spans = api()
    text = "  Стоимость — 100 тыс. руб. Без НДС.\n"
    span = span_for(text)
    assert spans.validate_span(span, text, sha(text)) == text
    partial = "Срок сохранён. Но при условии"
    with pytest.raises(ValueError):
        spans.validate_span(span_for(partial), partial, sha(partial))


def test_hash_owns_full_body_and_codepoint_bounds_are_exact():
    Span, spans = api()
    text = "📈 Рост составил 5%. Срок сохранён."
    s = span_for(text, "Срок сохранён.")
    assert spans.validate_span(s, text, sha(text)) == "Срок сохранён."
    for wrong in [s.model_copy(update={"start": s.start + 1}), s.model_copy(update={"end": len(text) + 1}),
                  s.model_copy(update={"content_sha256": sha("Срок сохранён.")})]:
        with pytest.raises(ValueError):
            spans.validate_span(wrong, text, sha(text))
    with pytest.raises(ValueError):
        spans.validate_span(s, text, "0" * 64)
    with pytest.raises(ValueError):
        spans.validate_span(s, text + " ", sha(text + " "))
    # model_copy/model_construct are not a trusted wire-validation boundary.
    malformed = Span.model_construct(source_id="a", content_sha256=sha(text), start=True, end=len(text))
    with pytest.raises(ValueError):
        spans.validate_span(malformed, text, sha(text))


@pytest.mark.parametrize("text,modified", [
    ("Ёмкость выросла.", "Е\u0308мкость выросла."),
    ("Ставка\u00a0не изменилась.", "Ставка не изменилась."),
])
def test_legacy_quote_does_not_normalize_unicode_or_whitespace(text, modified):
    _, spans = api()
    ref = news(text)
    with pytest.raises(ValueError):
        spans.render_quote(Claim(text=modified, kind="fact", source_ids=[ref.source_id]),
                           {ref.source_id: text}, {ref.source_id: ref}, {})


def test_explicit_span_replaces_false_framing_without_mutating_claim():
    _, spans = api()
    text = "ФНС не продлевает срок. Дата сохранена."
    ref = news(text)
    quote = "ФНС не продлевает срок."
    claim = Claim(text="ФНС продлила срок.", kind="fact", source_ids=[ref.source_id], evidence_spans=[span_for(text, quote)])
    rendered = spans.render_quote(claim, {ref.source_id: text}, {ref.source_id: ref}, {})
    assert rendered.text == quote and claim.text == "ФНС продлила срок."
    assert rendered.evidence_spans == claim.evidence_spans and rendered is not claim


def test_legacy_quote_upgrade_is_exact_unique_and_additive():
    _, spans = api()
    text = "ФНС не продлевает срок. Дата сохранена."
    ref = news(text).model_copy(update={"content_sha256": None})
    claim = Claim(text="Дата сохранена.", kind="fact", source_ids=[ref.source_id])
    rendered = spans.render_quote(claim, {ref.source_id: text}, {ref.source_id: ref}, {})
    assert rendered.text == claim.text and not claim.evidence_spans
    assert rendered.evidence_spans == [span_for(text, claim.text)]
    for invalid in ["Дата осталась прежней.", "продлевает срок.", " Дата сохранена."]:
        with pytest.raises(ValueError):
            spans.render_quote(claim.model_copy(update={"text": invalid}), {ref.source_id: text}, {ref.source_id: ref}, {})


def test_duplicate_occurrence_needs_explicit_offsets():
    _, spans = api()
    text = "Срок сохранён. Срок сохранён."
    ref = news(text)
    claim = Claim(text="Срок сохранён.", kind="fact", source_ids=[ref.source_id])
    with pytest.raises(ValueError):
        spans.render_quote(claim, {ref.source_id: text}, {ref.source_id: ref}, {})
    explicit = claim.model_copy(update={"evidence_spans": [span_for(text, claim.text)]})
    assert spans.render_quote(explicit, {ref.source_id: text}, {ref.source_id: ref}, {}).text == claim.text


def test_reference_ownership_and_explicit_source_sets_are_required():
    _, spans = api()
    text = "Срок сохранён."
    ref = news(text)
    claim = Claim(text=text, kind="fact", source_ids=[ref.source_id], evidence_spans=[span_for(text)])
    for texts, refs, candidate in [
        ({}, {ref.source_id: ref}, claim),
        ({ref.source_id: text}, {}, claim),
        ({ref.source_id: text}, {ref.source_id: ref.model_copy(update={"content_sha256": "0" * 64})}, claim),
        ({ref.source_id: text}, {ref.source_id: ref}, claim.model_copy(update={"source_ids": ["foreign"]})),
        ({ref.source_id: text}, {ref.source_id: ref}, claim.model_copy(update={"source_ids": []})),
        ({ref.source_id: text}, {ref.source_id: ref}, claim.model_copy(update={"evidence_spans": claim.evidence_spans * 2})),
    ]:
        with pytest.raises(ValueError):
            spans.render_quote(candidate, texts, refs, {})


def bank_context(statement_type="forecast", **updates):
    text = "При сохранении политики банк ожидает спад до конца 2026 года. После этой даты оценка не действует."
    frag = BankFragment(fragment_id="bank:opaque", doc_id="doc", doc_title="Synthetic", doc_date="2026-10-01",
                        text=text, review_status="verified", statement_type=statement_type,
                        valid_until="2026-12-31", char_start=900, char_end=900 + len(text), content_sha256=sha(text))
    frag = frag.model_copy(update=updates)
    ref = SourceRef(source_id=frag.fragment_id, kind="bank", title=frag.doc_title, date=frag.doc_date,
                    content_sha256=frag.content_sha256, doc_id=frag.doc_id)
    return frag, ref


def test_bank_assessment_keeps_whole_fragment_and_ignores_page_offsets():
    _, spans = api()
    frag, ref = bank_context()
    claim = Claim(text=frag.text, kind="assessment", source_ids=[ref.source_id])
    out = spans.render_quote(claim, {ref.source_id: frag.text}, {ref.source_id: ref}, {ref.source_id: frag})
    assert out.text == frag.text
    assert (out.evidence_spans[0].start, out.evidence_spans[0].end) == (0, len(frag.text))
    short = frag.text.split(". ")[0] + "."
    for candidate in [claim.model_copy(update={"text": short}), claim.model_copy(update={"evidence_spans": [span_for(frag.text, short, ref.source_id)]})]:
        with pytest.raises(ValueError):
            spans.render_quote(candidate, {ref.source_id: frag.text}, {ref.source_id: ref}, {ref.source_id: frag})


@pytest.mark.parametrize("kind,statement_type,updates", [
    ("assessment", "reported_fact", {}), ("assessment", "mixed", {}),
    ("assessment", "forecast", {"review_status": "pending"}),
    ("assessment", "forecast", {"doc_date": None}),
    ("assessment", "forecast", {"valid_until": None}),
    ("fact", "forecast", {}), ("fact", "bank_assessment", {}),
])
def test_bank_type_and_review_cannot_be_laundered_by_exact_text(kind, statement_type, updates):
    _, spans = api()
    frag, ref = bank_context(statement_type, **updates)
    with pytest.raises(ValueError):
        spans.render_quote(Claim(text=frag.text, kind=kind, source_ids=[ref.source_id]),
                           {ref.source_id: frag.text}, {ref.source_id: ref}, {ref.source_id: frag})


def test_verified_bank_reported_fact_remains_a_fact():
    _, spans = api()
    frag, ref = bank_context("reported_fact")
    out = spans.render_quote(Claim(text=frag.text, kind="fact", source_ids=[ref.source_id]),
                             {ref.source_id: frag.text}, {ref.source_id: ref}, {ref.source_id: frag})
    assert out.kind == "fact" and out.text == frag.text


def test_public_source_cannot_become_bank_assessment():
    _, spans = api()
    text = "Внешнее агентство ожидает спад."
    ref = news(text)
    with pytest.raises(ValueError):
        spans.render_quote(Claim(text=text, kind="assessment", source_ids=[ref.source_id]),
                           {ref.source_id: text}, {ref.source_id: ref}, {})


CASES = [json.loads(line) for line in (Path(__file__).parents[1] / "eval/material-semantic-cases.jsonl").read_text().splitlines() if line]


@pytest.mark.parametrize("case", CASES, ids=[c["case_id"] for c in CASES])
def test_all_faithful_synthetic_controls_render_from_their_exact_spans(case):
    Span, spans = api()
    positive = case["positive_control"]
    source = next(s for s in case["sources"] if s["source_id"] == positive["source_ids"][0])
    sid = source["source_id"]
    text = source["text"]
    ref = SourceRef(source_id=sid, kind=source["kind"], title=source["title"], content_sha256=source["content_sha256"])
    bank = {}
    if source["kind"] == "bank":
        bank[sid] = BankFragment(fragment_id=sid, doc_id=source["doc_id"], doc_title=source["title"],
            doc_date=source["published_at"][:10], text=text, content_sha256=source["content_sha256"],
            review_status=source["review_status"], statement_type=source["statement_type"], valid_until=source.get("valid_until"))
    for explicit in (False, True):
        claim = Claim(text=positive["text"], kind=positive["kind"], source_ids=[sid],
                      evidence_spans=[Span(**positive["evidence_span"])] if explicit else [])
        out = spans.render_quote(claim, {sid: text}, {sid: ref}, bank)
        assert out.text == positive["text"] and out.source_ids == [sid]
        assert out.evidence_spans == [Span(**positive["evidence_span"])]
