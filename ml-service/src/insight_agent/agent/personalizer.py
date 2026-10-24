from insight_agent.agent.planner import matched_channels
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
    parts = []
    for sid, text in state.source_texts().items():
        ref = state.sources()[sid]
        parts.append(
            data_block(ref.kind.value, f"source_id: {sid}\nназвание: {ref.title}\nдата: {ref.date}\n{text[:MAX_SOURCE_CHARS]}")
        )
    return "\n".join(parts)


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
            "Если твой вывод расходится с позицией банка, опиши это в model_vs_bank.\n"
            if has_bank
            else "Материалов банка по теме нет: bank_view - одно утверждение kind=unavailable без источников.\n"
        )
        + "actions - конкретные шаги для этой роли (проверить, пересчитать, обсудить с менеджером банка), "
        "не общие советы. scenarios - 1-2 сценария развития, если есть основания.\n"
        "missing_data - каких данных о компании не хватило для вывода.\n"
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
        f"Признаки компании (profile_features):\n{signals}\n"
        f"Нет данных о компании: {state.missing_profile or 'нет пропусков'}\n\n"
        f"Источники (source_ids):\n{_sources_block(state)}"
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    if feedback and state.draft:
        issues = "\n".join(f"- [{i.code}] {i.location or ''}: {i.message}" for i in feedback)
        messages += [
            {"role": "assistant", "content": state.draft.model_dump_json()},
            {"role": "user", "content": f"Проверка нашла проблемы. Исправь их и верни полный JSON заново:\n{issues}"},
        ]
    return llm.generate("personalize" if not feedback else "revise", model, messages, PersonalizationDraft)
