"""Карточка ML в формате интерфейса d09-d10 (контракт 2.0): блоки fact/open/bank/ai, источники, уточнение.

Соответствие: факты события -> fact; позиция банка -> bank; выводы модели с признаками компании -> ai,
выводы модели без признаков компании (общий разбор события) -> open. Темы интерфейса:
consequences (почему важно, последствия, возможности), risks, recommendations (действия).
"""
from insight_agent.schemas import Answer, Claim, ClaimKind, Insight, SourceRef

VIEWS = ("overview", "consequences", "risks", "recommendations")
THEMES = ("consequences", "risks", "recommendations")
_STRICTNESS = (ClaimKind.hypothesis, ClaimKind.inference, ClaimKind.recommendation, ClaimKind.assessment, ClaimKind.fact)
BANK_ABSENT = "Материал Альфа-Банка по теме не найден: позиция банка не приводится."
BANK_NO_THEME = "Позиция банка по этой теме в материалах не выделена."


def _certainty(claims: list[Claim]) -> str:
    kinds = {c.kind for c in claims}
    return next((k.value for k in _STRICTNESS if k in kinds), ClaimKind.unavailable.value)


def _section(theme: str, claims: list[Claim], empty_text: str | None = None) -> dict | None:
    real = [c for c in claims if c.kind != ClaimKind.unavailable]
    if not real:
        return None if empty_text is None else {"kind": theme, "text": empty_text, "source_ids": [], "certainty": "unavailable"}
    return {
        "kind": theme,
        "text": "\n".join(c.text for c in real),
        "source_ids": sorted({sid for c in real for sid in c.source_ids}),
        "certainty": _certainty(real),
    }


def _themes(insight: Insight, *, with_features: bool) -> dict[str, list[Claim]]:
    own = lambda claims: [c for c in claims if bool(c.profile_features) == with_features]  # noqa: E731
    return {
        "consequences": own(insight.why_it_matters + insight.consequences + insight.opportunities),
        "risks": own(insight.risks),
        "recommendations": own(insight.actions),
    }


def _block(kind: str, sections: list[dict | None], view: str) -> dict | None:
    chosen = [s for s in sections if s and (view == "overview" or s["kind"] == view)]
    if not chosen:
        return None
    return {"kind": kind, "sections": chosen, "source_ids": sorted({sid for s in chosen for sid in s["source_ids"]}), "synthetic": False}


def _bank_block(insight: Insight, view: str) -> dict:
    assessments = [c for c in insight.bank_view if c.kind == ClaimKind.assessment]
    sections = []
    for theme in THEMES:
        if assessments and theme == "consequences":
            sections.append(_section(theme, assessments))
        else:
            sections.append(_section(theme, [], BANK_NO_THEME if assessments else BANK_ABSENT))
    return _block("bank", sections, view)


def source_view(ref: SourceRef) -> dict:
    return {"id": ref.source_id, "title": ref.title, "date": ref.date.isoformat() if ref.date else None,
            "page": ref.page, "kind": ref.kind.value, "url": ref.url}


def to_frontend_card(insight: Insight, view: str = "overview") -> dict:
    if view not in VIEWS:
        raise ValueError(f"unknown view: {view}")
    blocks = []
    if view == "overview" and insight.what_happened:
        facts = insight.what_happened
        blocks.append({"kind": "fact", "text": "\n".join(c.text for c in facts),
                       "source_ids": sorted({sid for c in facts for sid in c.source_ids}), "certainty": "fact"})
    for kind, with_features in (("open", False), ("bank", None), ("ai", True)):
        if kind == "bank":
            block = _bank_block(insight, view)
        else:
            by_theme = _themes(insight, with_features=with_features)
            block = _block(kind, [_section(t, by_theme[t]) for t in THEMES], view)
        if block:
            blocks.append(block)
    return {
        "context": {"company_id": insight.company_id, "role": insight.role.value, "event_id": insight.event_id, "view": view},
        "insight_id": insight.insight_id,
        "title": insight.headline,
        "status": insight.status.value,
        "outcome": insight.outcome.value,
        "error_code": insight.error_code,
        "run_id": insight.run_id,
        "blocks": blocks,
        "sources": [source_view(s) for s in insight.sources],
        "missing_data": insight.missing_data,
        "disclaimer": insight.disclaimer,
    }


def to_frontend_clarification(answer: Answer, insight: Insight, *, request_id: str | None, view: str = "overview") -> dict:
    return {
        "context": {"company_id": insight.company_id, "role": insight.role.value, "event_id": insight.event_id, "view": view},
        "insight_id": insight.insight_id,
        "request_id": request_id,
        "answer_id": answer.run_id,
        "status": answer.status,
        "text": answer.text,
        "sources": [source_view(s) for s in answer.sources],
        "missing_data": answer.missing_data,
    }
