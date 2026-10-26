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


def event_fingerprint(event: NewsEvent, *, source_texts: dict[str, str] | None = None) -> str:
    data = {"event": event.model_dump(mode="json"), "texts": source_texts or {}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
