from insight_agent.agent.planner import matched_channels
from insight_agent.agent.evidence import pack_sources, quote_options, span_hash_normalizer
from insight_agent.agent.prompts import (
    KIND_RULES,
    LEVEL_DEFINITIONS,
    ROLE_FOCUS,
    UNTRUSTED,
    data_block,
    schema_hint,
)
from insight_agent.agent.state import AgentState
from insight_agent.llm import StructuredLLM
from insight_agent.schemas import CheckIssue, PersonalizationDraft
from insight_agent.tools.profile import CHANNELS

MAX_SOURCE_CHARS = 2500


def _sources_block(state: AgentState) -> str:
    parts = [data_block("source_conflicts", c.model_dump_json()) for c in state.source_conflicts()]
    texts, truncated = pack_sources(state.source_texts(), limit=12000)
    if truncated:
        state.limited_reasons.append("контекст персонализации сокращён; полный текст сохранён: " + ", ".join(truncated))
    for sid, text in texts.items():
        ref = state.sources()[sid]
        quality = state.bank[sid].model_dump_json(include={"review_status", "statement_type", "target_period", "valid_until"}) if sid in state.bank else ""
        parts.append(
            data_block(ref.kind.value, f"source_id: {sid}\nназвание: {ref.title}\nдата: {ref.date}\nстраница: {ref.page}\n{quality}\n{text}")
        )
    parts.append(data_block("quote_options", quote_options(state.source_texts(), bank_ids=state.bank)))
    return "\n".join(parts)


_PRODUCT_NAMES = {"settlement": "расчётно-кассовое обслуживание", "guarantees": "банковские гарантии", "loans": "кредиты",
                  "leasing": "лизинг", "deposits": "депозиты", "payroll": "зарплатный проект", "factoring": "факторинг"}


def _known_negatives(state: AgentState) -> str:
    """Подтверждённое «нет» отличается от «неизвестно». Это контекст, а не признак: на него нельзя ссылаться в profile_features."""
    products = state.request.profile.products
    absent = [label for key, label in _PRODUCT_NAMES.items() if getattr(products, key) is False]
    if not absent:
        return "Известных отсутствующих продуктов нет."
    return ("В Альфа-Банке известно, что НЕТ: " + ", ".join(absent)
            + " (узкий факт про этот банк; не означает отсутствие таких обязательств или средств в других банках).")


def personalize(
    llm: StructuredLLM, model: str, state: AgentState, feedback: list[CheckIssue] | None = None
) -> PersonalizationDraft:
    role = state.request.role
    has_bank = bool(state.bank)
    system = (
        "Ты аналитик Альфа-Банка для руководителей крупного бизнеса. Объясни, как событие влияет именно "
        "на эту компанию, а не на отрасль в целом. Не пересказывай новость: свяжи событие с признаками "
        "компании причинными цепочками, учитывая несколько факторов сразу (продукты, отрасль, "
        "контрагенты, внешняя торговля, группа).\n"
        f"Роль пользователя: {role.value}; фокус роли: {ROLE_FOCUS[role]}.\n"
        f"Шкала impact_level и level: {LEVEL_DEFINITIONS}\n"
        f"{KIND_RULES}\n"
        + (
            "bank_view - что говорит аналитика банка по теме, kind=assessment, ссылки только на фрагменты банка. "
            "Не смешивай прогноз, оценку банка и опубликованный факт. model_vs_bank оставь пустым.\n"
            if has_bank
            else "Материалов банка по теме нет: bank_view - одно утверждение kind=unavailable без источников.\n"
        )
        + "actions - конкретные шаги для этой роли (проверить, пересчитать, обсудить с менеджером банка), "
        "не общие советы. scenarios - 1-2 сценария развития, если есть основания.\n"
        "missing_data - каких данных о компании не хватило для вывода.\n"
        "company_link: clear - связь события с компанией объяснена её признаками; conditional - связь возможна, но часть данных неизвестна, "
        "назови, чего не хватает; none - объяснимой связи нет, тогда не придумывай влияние и не завышай impact_level.\n"
        "Объект вывода - выбранная компания. Группа даёт только дополнительный контекст: влияние через другого участника группы "
        "объясняй конкретной связью, выручку юрлица не называй выручкой группы, неизвестную консолидацию и долю рынка не рассчитывай.\n"
        "Показатель используй только вместе с его периодом, единицами, объектом и ограничениями из примечания к признаку. "
        "Известное отсутствие продукта в Альфа-Банке не доказывает отсутствие долгов или депозитов в других банках; "
        "страны ФТС не доказывают импорт или экспорт, платежи - роль поставщика или покупателя, NEGATIVEMARK - негативный риск. "
        "Косвенный признак может обосновать только условный сценарий (kind=hypothesis) с названной связью и недостающими данными.\n"
        f"{UNTRUSTED}\nОтвет - один JSON-объект по схеме: {schema_hint(PersonalizationDraft)}"
    )
    a = state.analysis
    facts = "\n".join(f"- {f.text} {f.source_ids}" for f in a.facts) if a else ""
    signals = "\n".join(
        f"- {s.feature} = {s.value} ({s.note}; каналы: {', '.join(s.channels) or '-'})" for s in state.signals
    )
    user = (
        f"Разбор события: {a.summary if a else state.event.title}\nФакты:\n{facts}\n"
        f"Каналы влияния события: {', '.join(CHANNELS[c] for c in a.impact_channels) if a else ''}\n"
        f"Совпавшие каналы компании: {matched_channels(state)}\n\n"
        f"Признаки компании (profile_features):\n{data_block('profile', signals)}\n"
        f"Нет данных о компании: {state.missing_profile or 'нет пропусков'}\n"
        f"{_known_negatives(state)}\n\n"
        f"Источники (source_ids):\n{_sources_block(state)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if feedback and state.draft:
        issues = "\n".join(f"- [{i.code}] {i.location or ''}: {i.message}" for i in feedback)
        messages += [
            {"role": "assistant", "content": state.draft.model_dump_json()},
            {"role": "user", "content": f"Проверка нашла проблемы. Исправь их и верни полный JSON заново:\n{issues}"},
        ]
    return llm.generate("personalize" if not feedback else "revise", model, messages, PersonalizationDraft,
                        normalize=span_hash_normalizer(state.source_texts()))
