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


def span_hash_normalizer(texts: dict[str, str], whole_text_ids=()):
    """Хеш в evidence_span - производная величина источника, а не утверждение модели.

    Модель часто искажает 64 hex-символа при копировании. Подставляем настоящий хеш текста по source_id; для
    банковских фрагментов (цитата только целиком) так же задаются границы. Совпадение текста с источником по-прежнему
    проверяется отдельно (spans.py), поэтому проверка не ослабляется.
    """
    digests = {sid: hashlib.sha256(text.encode()).hexdigest() for sid, text in texts.items()}
    whole = {sid for sid in whole_text_ids if sid in texts and texts[sid]}

    def walk(node):
        if isinstance(node, dict):
            sid = node.get("source_id")
            if isinstance(sid, str) and "content_sha256" in node and sid in digests:
                node["content_sha256"] = digests[sid]
                if sid in whole and "start" in node and "end" in node:
                    # Банковский фрагмент допустим только целиком (quote_options): границы модель копирует с ошибками.
                    node["start"], node["end"] = 0, len(texts[sid])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    def normalize(data: dict) -> dict:
        walk(data)
        return data

    return normalize


def event_fingerprint(event: NewsEvent, *, source_texts: dict[str, str] | None = None) -> str:
    data = {"event": event.model_dump(mode="json"), "texts": source_texts or {}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
