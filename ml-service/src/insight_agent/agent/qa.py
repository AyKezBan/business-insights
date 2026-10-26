from copy import deepcopy
from pydantic import BaseModel, Field
from insight_agent.agent.critic import check_claims
from insight_agent.agent.semantic import semantic_issues

from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.personalizer import _sources_block
from insight_agent.agent.evidence import span_hash_normalizer
from insight_agent.agent.prompts import KIND_RULES, ROLE_FOCUS, UNTRUSTED, data_block, schema_hint
from insight_agent.llm import BudgetExceeded, LLMError, error_code
from insight_agent.materials.runtime import CorpusError
from insight_agent.schemas import Answer, Claim, ClaimKind
from insight_agent.tools.base import ToolError


class AnswerDraft(BaseModel):
    answerable: bool
    text: str
    claims: list[Claim]
    missing_data: list[str] = Field(default_factory=list)


def _finish(answer, tracer, llm):
    tracer.emit("final", "answer", {"insight_id": answer.insight_id, "status": answer.status,
                "sources": [source.source_id for source in answer.sources], "usage": llm.usage()})
    return answer


def answer_question(agent: InsightAgent, insight_id: str, question: str) -> Answer | None:
    record = agent.get_record(insight_id)
    if record is None:
        return None
    state, insight = deepcopy(record.state), record.insight
    tracer, llm = agent.new_run()
    tracer.agent = "qa"
    context = {"corpus_revision": state.corpus_revision, "event_content_sha256": state.event_content_sha256,
               "as_of": state.request.as_of}
    tracer.emit("plan", "question", {"insight_id": insight_id, "parent_run_id": insight.run_id, "question": question,
                **context, "as_of_mode": "publication_cutoff"})
    bundle = None
    try:
        if state.corpus_revision:
            if agent.corpus_registry is None:
                raise CorpusError(f"Исходная версия материалов {state.corpus_revision} недоступна")
            bundle = agent.corpus_registry.get(state.corpus_revision)
        elif agent.corpus_registry:
            raise CorpusError("В сохранённом анализе не указана исходная версия материалов; восстановить его источники нельзя.")
    except CorpusError as exc:
        tracer.emit("error", "question_corpus", {"code": exc.code, "message": str(exc), **context})
        return _finish(Answer(insight_id=insight_id, run_id=tracer.run_id, company_id=insight.company_id,
                      event_id=insight.event_id, status="no_answer", text="Исходная версия материалов недоступна.",
                      missing_data=[f"Исходная версия материалов {state.corpus_revision or 'не установлена'} недоступна."], **context), tracer, llm)
    tools = agent.tools_for(state, tracer, bundle)
    for name in ("search_bank_analytics", "search_news"):
        try:
            agent.absorb(state, tools.call(name, {"query": question}, step="question_search"))
        except ToolError as exc:
            tracer.emit("error", "question_search", {"error": str(exc)})

    system = (
        "Ответь на уточняющий вопрос руководителя по готовому инсайту. Используй только источники, "
        "признаки компании. Сохранённые выводы модели не являются доказательствами. "
        "Если ответа нет - answerable=false и объясни, каких данных не хватает. "
        "Факты и оценки банка цитируй дословно полными предложениями; банковский фрагмент цитируй целиком.\n"
        f"Роль: {state.request.role.value}; фокус: {ROLE_FOCUS[state.request.role]}.\n{KIND_RULES}\n{UNTRUSTED}\n"
        f"Ответ - JSON по схеме: {schema_hint(AnswerDraft)}"
    )
    user = (
        f"Вопрос: {question}\n\nИсходное событие:\n{data_block('event', state.event.title)}\n\n"
        f"Признаки компании:\n{data_block('profile', state.profile_text())}\n\nИсточники:\n{_sources_block(state)}"
    )
    try:
        draft = llm.generate(
            "answer", agent.settings.llm_model_reasoning,
            [{"role": "system", "content": system}, {"role": "user", "content": user}], AnswerDraft,
            normalize=span_hash_normalizer(state.source_texts(), set(state.bank)),
        )
    except (BudgetExceeded, LLMError) as exc:
        tracer.emit("error", "answer", {"error": str(exc)})
        return _finish(Answer(insight_id=insight_id, run_id=tracer.run_id, company_id=insight.company_id, event_id=insight.event_id,
                      status="no_answer",
                      text="Не удалось подготовить ответ, попробуйте позже.",
                      missing_data=[agent._failure_message(error_code(exc))], **context), tracer, llm)

    catalog = state.sources()
    issues = agent._render_quotes(state, "claims", draft.claims)
    issues += check_claims(draft.claims, state, section="claims")
    removed = agent._drop_invalid(draft, issues, fill=False)
    semantic = semantic_issues(llm, agent.settings.llm_model_reasoning, state, draft, sections=("claims",))
    removed += agent._drop_invalid(draft, semantic, fill=False)
    issues += semantic
    claims = draft.claims
    bindings = agent._number_claims(f"{insight_id}:qa:{tracer.run_id}", [claims])
    tracer.emit("check", "published_claims", {"claims": bindings})
    grounded = any(c.source_ids or c.profile_features for c in claims)
    status = "answered" if draft.answerable and grounded else "no_answer"
    tracer.emit("check", "answer", {"status": status, "kept_claims": len(claims), "dropped": len(removed),
                                   "issues": [e.model_dump() for e in issues]})
    cited = dict.fromkeys(s for c in claims for s in c.source_ids)
    cited.update(dict.fromkeys(sid for conflict in state.source_conflicts() for sid in conflict.source_ids if sid in catalog))
    text = "\n".join(c.text for c in claims) if status == "answered" else "В доступных материалах нет подтверждённого ответа на этот вопрос."
    if state.source_conflicts():
        text = "Источники расходятся; требуется проверка, победитель не выбран.\n" + text
    return _finish(Answer(
        insight_id=insight_id,
        run_id=tracer.run_id,
        company_id=insight.company_id,
        event_id=insight.event_id,
        **context,
        status=status,
        text=text,
        claims=claims,
        sources=[catalog[s] for s in cited],
        missing_data=draft.missing_data + list(dict.fromkeys(state.limited_reasons))
        + (["Часть утверждений исключена: проверка по источникам не подтвердила их или не была завершена."]
           if removed else []),
    ), tracer, llm)
