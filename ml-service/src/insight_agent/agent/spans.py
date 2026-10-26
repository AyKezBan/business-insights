"""Exact quotations from already resolved sources; no retrieval or entailment claims.

Callers supply full hash-owned bodies, never packed/truncated prompt excerpts.
Sentence units deliberately merge ambiguous abbreviations rather than cutting
qualifiers. Bank quotations use the entire reviewed fragment independently of
punctuation. Temporal eligibility remains the pinned runtime's responsibility.
"""
import hashlib
import re
from collections.abc import Mapping

from insight_agent.schemas import BankFragment, Claim, ClaimKind, EvidenceSpan, SourceKind, SourceRef


_ABBREVIATION = re.compile(
    r"(?<!\w)(?:тыс|млн|млрд|трлн|руб|коп|стр|рис|табл|пп|ч|ст|мес|кв|им|обл|проф|доц|"
    r"dr|mr|mrs|ms|prof|inc|ltd|corp|co|no|vs|etc)\.", re.IGNORECASE,
)
_DOTTED = re.compile(r"(?<!\w)(?:[^\W\d_]\.){2,}")
_INITIAL = re.compile(r"(?<!\w)[^\W\d_]\.(?=\s|$)")
_DECIMAL = re.compile(r"(?<=\d)\.(?=\d)")
_OPEN = {"(": ")", "[": "]", "{": "}", "«": "»", "“": "”", "„": "“"}
_CLOSE = set(_OPEN.values()) | {'"'}


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _depths(text: str) -> list[int] | None:
    """Depth after each code point; unmatched quotes/brackets are conservative failures."""
    stack, depths = [], [0]
    for char in text:
        if stack and char == stack[-1]:
            stack.pop()
        elif char == '"':
            stack.append(char)
        elif char in _OPEN:
            stack.append(_OPEN[char])
        elif char in _CLOSE:
            return None
        depths.append(len(stack))
    return depths if not stack else None


def _complete_body(text: str) -> bool:
    """Structural completeness only; the caller owns ingestion/scope completeness."""
    if not any(c.isalpha() for c in text) or _depths(text) is None:
        return False
    tail = text.rstrip()
    while tail and tail[-1] in _CLOSE:
        tail = tail[:-1]
    return bool(tail and tail[-1] in ".!?" and not tail.endswith(".."))


def _sentence_intervals(text: str) -> list[tuple[int, int]]:
    depths = _depths(text)
    if depths is None:
        return []
    protected = set()
    for pattern in (_ABBREVIATION, _DOTTED, _INITIAL, _DECIMAL):
        for match in pattern.finditer(text):
            protected.update(i for i in range(match.start(), match.end()) if text[i] == ".")
    start = 0
    while start < len(text) and text[start].isspace():
        start += 1
    intervals = []
    i = start
    while i < len(text):
        char = text[i]
        if char not in ".!?" or i in protected or (char == "." and
                ((i and text[i - 1] == ".") or text[i:i + 2] == "..")):
            i += 1
            continue
        end = i + 1
        while end < len(text) and text[end] in "!?":
            end += 1
        while end < len(text) and text[end] in _CLOSE:
            end += 1
        if depths[end] or (end < len(text) and not text[end].isspace()):
            i += 1
            continue
        following = end
        while following < len(text) and text[following].isspace():
            following += 1
        # A lowercase continuation or a reporting dash is not a safe split.
        if following < len(text) and not (text[following].isupper() or text[following].isdigit()
                                          or text[following] in _OPEN or text[following] == '"'):
            i += 1
            continue
        if not any(c.isalpha() for c in text[start:end]):
            i = end
            continue
        intervals.append((start, end))
        start = following
        i = following
    # A final abbreviation can be complete only with the whole remaining unit.
    if start < len(text) and _complete_body(text[start:]):
        intervals.append((start, len(text.rstrip())))
    return intervals


def sentence_spans(source_id: str, text: str) -> list[EvidenceSpan]:
    """Propose complete source units, preserving original Unicode/whitespace offsets."""
    digest = _hash(text)
    return [EvidenceSpan(source_id=source_id, content_sha256=digest, start=start, end=end)
            for start, end in _sentence_intervals(text)]


def _validate_identity(span: EvidenceSpan, text: str, expected_sha256: str) -> None:
    # Revalidate even model_copy/model_construct values supplied by internal callers.
    EvidenceSpan.model_validate(span.model_dump())
    actual = _hash(text)
    if actual != expected_sha256 or actual != span.content_sha256:
        raise ValueError("Evidence span does not match the exact source body hash")
    if span.end > len(text):
        raise ValueError("Evidence span exceeds the source body")


def validate_span(span: EvidenceSpan, text: str, expected_sha256: str) -> str:
    """Return an unchanged complete source unit, never a cropped assertion.

    A structurally complete entire body is a conservative fallback for ambiguous
    internal punctuation. Source ownership and bank policy belong to render_quote.
    """
    _validate_identity(span, text, expected_sha256)
    full_body = span.start == 0 and span.end == len(text) and _complete_body(text)
    if not full_body and (span.start, span.end) not in _sentence_intervals(text):
        raise ValueError("Evidence span is not a complete source unit")
    return text[span.start:span.end]


def _owned_source(sid: str, texts: Mapping[str, str], refs: Mapping[str, SourceRef],
                  bank: Mapping[str, BankFragment], kind: ClaimKind) -> tuple[str, bool]:
    if sid not in texts or sid not in refs or refs[sid].source_id != sid or not texts[sid].strip():
        raise ValueError("Evidence source is unknown or unresolved")
    text, ref = texts[sid], refs[sid]
    digest = _hash(text)
    if ref.content_sha256 is not None and ref.content_sha256 != digest:
        raise ValueError("Declared source hash contradicts the resolved body")
    is_bank = ref.kind == SourceKind.bank
    if is_bank:
        fragment = bank.get(sid)
        if (fragment is None or fragment.fragment_id != sid or fragment.text != text
                or fragment.review_status != "verified" or fragment.doc_date is None):
            raise ValueError("Bank evidence is not a reviewed dated fragment")
        if fragment.content_sha256 is not None and fragment.content_sha256 != digest:
            raise ValueError("Bank fragment hash contradicts the resolved body")
        permitted = ("bank_assessment", "forecast") if kind == ClaimKind.assessment else ("reported_fact",)
        if fragment.statement_type not in permitted:
            raise ValueError("Bank statement type does not support this claim kind")
        if fragment.statement_type == "forecast" and fragment.valid_until is None:
            raise ValueError("Bank forecast has no verified validity horizon")
    elif kind == ClaimKind.assessment or ref.kind != SourceKind.news or sid in bank:
        raise ValueError("Source kind cannot support this claim kind")
    return text, is_bank


def render_quote(claim: Claim, texts: Mapping[str, str], refs: Mapping[str, SourceRef],
                 bank: Mapping[str, BankFragment]) -> Claim:
    """Copy a fact/assessment with literal evidence text; never infer a paraphrase.

    The supplied bank mapping must already be eligible for the pinned request's
    publication cutoff, expiry and supersession policy. This pure function checks
    identity/review/type, not the clock or a different corpus revision.
    """
    if claim.kind not in (ClaimKind.fact, ClaimKind.assessment):
        raise ValueError("Only facts and bank assessments can render as quotations")
    if not claim.source_ids or len(set(claim.source_ids)) != len(claim.source_ids):
        raise ValueError("A quotation requires distinct owned source IDs")
    owned = {sid: _owned_source(sid, texts, refs, bank, claim.kind) for sid in claim.source_ids}
    spans = list(claim.evidence_spans)
    if not spans:
        if len(owned) != 1:
            raise ValueError("Legacy quotation has ambiguous source ownership")
        sid, (text, is_bank) = next(iter(owned.items()))
        if is_bank:
            candidates = [EvidenceSpan(source_id=sid, content_sha256=_hash(text), start=0, end=len(text))]
        else:
            candidates = sentence_spans(sid, text)
            if _complete_body(text):
                candidates.append(EvidenceSpan(source_id=sid, content_sha256=_hash(text), start=0, end=len(text)))
        matches = {s.span_id: s for s in candidates if text[s.start:s.end] == claim.text}
        if len(matches) != 1:
            raise ValueError("Legacy quotation is not one unambiguous exact complete unit")
        spans = list(matches.values())
    if {s.source_id for s in spans} != set(claim.source_ids):
        raise ValueError("Quotation spans and cited source IDs disagree")
    if len({s.span_id for s in spans}) != len(spans):
        raise ValueError("Quotation repeats an evidence span")
    quotes = []
    for span in spans:
        text, is_bank = owned[span.source_id]
        if is_bank:
            _validate_identity(span, text, _hash(text))
            if span.start != 0 or span.end != len(text):
                raise ValueError("Bank quotation must preserve the entire reviewed fragment")
            quotes.append(text)
        else:
            quotes.append(validate_span(span, text, _hash(text)))
    return claim.model_copy(deep=True, update={"text": "\n".join(quotes), "evidence_spans": spans})
