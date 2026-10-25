from copy import deepcopy
from pydantic import BaseModel, Field
from insight_agent.agent.critic import check_claims

from insight_agent.agent.orchestrator import InsightAgent
from insight_agent.agent.personalizer import _sources_block
from insight_agent.agent.prompts import KIND_RULES, ROLE_FOCUS, UNTRUSTED, schema_hint
from insight_agent.llm import BudgetExceeded, LLMError
from insight_agent.schemas import Answer, Claim, ClaimKind
from insight_agent.tools.base import ToolError


class AnswerDraft(BaseModel):
    answerable: bool
    text: str
    claims: list[Claim]
    missing_data: list[str] = Field(default_factory=list)


def answer_question(agent: InsightAgent, insight_id: str, question: str) -> Answer | None:
    record = agent.records.get(insight_id)
    if record is None:
        return None
    state, insight = deepcopy(record.state), record.insight
    tracer, llm = agent.new_run()
    tracer.emit("plan", "question", {"insight_id": insight_id, "parent_run_id": insight.run_id, "question": question})
    tools = agent.tools_for(state, tracer)
    for name in ("search_bank_analytics", "search_news"):
        try:
            agent.absorb(state, tools.call(name, {"query": question}, step="question_search"))
        except ToolError as exc:
            tracer.emit("error", "question_search", {"error": str(exc)})

    system = (
        "Ответь на уточняющий вопрос руководителя по готовому инсайту. Используй только источники, "
        "признаки компании и сам инсайт. Если ответа нет - answerable=false и объясни, каких данных не хватает.\n"
        f"Роль: {state.request.role.value}; фокус: {ROLE_FOCUS[state.request.role]}.\n{KIND_RULES}\n{UNTRUSTED}\n"
        f"Ответ - JSON по схеме: {schema_hint(AnswerDraft)}"
    )
    user = (
        f"Вопрос: {question}\n\nИнсайт:\n{insight.model_dump_json(include={'headline', 'why_it_matters', 'consequences', 'risks', 'actions', 'bank_view'})}\n\n"
        f"Признаки компании:\n{state.profile_text()}\n\nИсточники:\n{_sources_block(state)}"
    )
    try:
        draft = llm.generate(
            "answer", agent.settings.llm_model_reasoning,
            [{"role": "system", "content": system}, {"role": "user", "content": user}], AnswerDraft,
        )
    except (BudgetExceeded, LLMError) as exc:
        tracer.emit("error", "answer", {"error": str(exc)})
        return Answer(insight_id=insight_id, run_id=tracer.run_id, status="no_answer",
                      text="Не удалось подготовить ответ, попробуйте позже.", missing_data=[str(exc)])

    catalog = state.sources()
    issues = check_claims(draft.claims, state, section="claims")
    bad = {i for i in range(len(draft.claims))
           if any(e.severity == "error" and e.location == f"claims[{i}]" for e in issues)}
    claims = [c for i, c in enumerate(draft.claims) if i not in bad]
    grounded = any(c.source_ids or c.profile_features for c in claims)
    status = "answered" if draft.answerable and grounded else "no_answer"
    tracer.emit("check", "answer", {"status": status, "kept_claims": len(claims), "dropped": len(draft.claims) - len(claims),
                                   "issues": [e.model_dump() for e in issues]})
    cited = dict.fromkeys(s for c in claims for s in c.source_ids)
    cited.update(dict.fromkeys(sid for conflict in state.source_conflicts() for sid in conflict.source_ids if sid in catalog))
    text = "\n".join(c.text for c in claims) if status == "answered" else "В доступных материалах нет подтверждённого ответа на этот вопрос."
    if state.source_conflicts():
        text = "Источники расходятся; требуется проверка, победитель не выбран.\n" + text
    return Answer(
        insight_id=insight_id,
        run_id=tracer.run_id,
        status=status,
        text=text,
        claims=claims,
        sources=[catalog[s] for s in cited],
        missing_data=draft.missing_data + list(dict.fromkeys(state.limited_reasons)),
    )
