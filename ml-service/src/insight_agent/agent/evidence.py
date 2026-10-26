"""Bounded, explicit model context. Full source text remains in the corpus/state."""
import hashlib
import json
import re

from insight_agent.schemas import NewsEvent


def fit_text(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    boundaries = list(re.finditer(r"\n\s*\n|(?<=[.!?])\s+", text[:limit]))
    end = boundaries[-1].start() if boundaries else text.rfind(" ", 0, limit)
    if end < limit // 2:
        end = limit
    return text[:end].rstrip(), True


def pack_sources(texts: dict[str, str], limit: int = 6000) -> tuple[dict[str, str], list[str]]:
    if not texts:
        return {}, []
    allowance = max(1, limit // len(texts))
    out, truncated = {}, []
    for source_id, text in texts.items():
        out[source_id], cut = fit_text(text, allowance)
        if cut:
            truncated.append(source_id)
    return out, truncated


def quote_options(texts: dict[str, str], *, bank_ids=(), limit=6000) -> str:
    """Bounded complete quote choices; omitted units do not become shortened evidence."""
    from insight_agent.agent.spans import sentence_spans
    from insight_agent.schemas import EvidenceSpan
    options, used = [], 2
    for source_id, text in texts.items():
        spans = ([EvidenceSpan(source_id=source_id, content_sha256=hashlib.sha256(text.encode()).hexdigest(),
                               start=0, end=len(text))] if source_id in bank_ids and text
                 else sentence_spans(source_id, text))
        for span in spans:
            choice = {"evidence_span": span.model_dump(), "text": text[span.start:span.end]}
            size = len(json.dumps(choice, ensure_ascii=False)) + (2 if options else 0)
            if len(options) >= 32 or used + size > limit:
                continue
            options.append(choice)
            used += size
    return json.dumps(options, ensure_ascii=False)


def event_fingerprint(event: NewsEvent, *, source_texts: dict[str, str] | None = None) -> str:
    data = {"event": event.model_dump(mode="json"), "texts": source_texts or {}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
