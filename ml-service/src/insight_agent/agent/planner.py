from pydantic import BaseModel, Field

from insight_agent.agent.prompts import schema_hint
from insight_agent.agent.state import AgentState
from insight_agent.llm import LLMError, StructuredLLM
from insight_agent.tools.base import ToolRegistry
from insight_agent.tools.profile import CHANNELS

MAX_PLAN_STEPS = 6


class PlanStep(BaseModel):
    tool: str
    args: dict
    purpose: str


class Plan(BaseModel):
    steps: list[PlanStep] = Field(max_length=MAX_PLAN_STEPS)


def matched_channels(state: AgentState) -> list[str]:
    company = {c for s in state.signals for c in s.channels}
    return [c for c in (state.analysis.impact_channels if state.analysis else []) if c in company]


def fallback_plan(state: AgentState) -> Plan:
    a = state.analysis
    queries = (a.bank_queries if a else []) or [state.event.title]
    steps = [PlanStep(tool="search_bank_analytics", args={"query": q}, purpose="позиция банка") for q in queries[:3]]
    if a and a.open_questions:
        steps.append(
            PlanStep(
                tool="search_news",
                args={"query": a.open_questions[0], "exclude_event_id": state.event.event_id},
                purpose="дополнить недостающий контекст",
            )
        )
    return Plan(steps=steps)


OPEN_TOOL = "search_open_sources"


def _with_open_research(plan: Plan, tools: ToolRegistry) -> Plan:
    """Официальные данные по профилю клиента нужны всегда; что именно искать, решает код (research/plan.py)."""
    if OPEN_TOOL in tools.names and not any(s.tool == OPEN_TOOL for s in plan.steps):
        plan.steps.append(PlanStep(tool=OPEN_TOOL, args={}, purpose="официальные показатели и публикации по профилю клиента"))
    return plan


def make_plan(llm: StructuredLLM, model: str, state: AgentState, tools: ToolRegistry) -> tuple[Plan, bool]:
    a = state.analysis
    system = (
        "Ты планировщик анализа. Реши, какие данные нужны, чтобы объяснить влияние события на компанию, "
        "и составь последовательность вызовов инструментов. Обязательно найди позицию аналитики банка. "
        "Связанные новости ищи, только если в событии не хватает контекста.\n"
        f"Инструменты:\n{tools.describe()}\n"
        f"Не более {MAX_PLAN_STEPS} шагов. Ответ - JSON по схеме: {schema_hint(Plan)}"
    )
    user = (
        f"Событие: {state.event.title} (event_id={state.event.event_id})\n"
        f"Каналы влияния события: {[CHANNELS[c] for c in a.impact_channels] if a else []}\n"
        f"Совпадение с каналами компании: {matched_channels(state)}\n"
        f"Вопросы без ответа: {a.open_questions if a else []}\n"
        f"Подсказки запросов к банку: {a.bank_queries if a else []}\n"
        f"Признаки компании:\n{state.profile_text()}"
    )
    try:
        plan = llm.generate(
            "plan", model, [{"role": "system", "content": system}, {"role": "user", "content": user}], Plan
        )
    except LLMError:
        return _with_open_research(fallback_plan(state), tools), False
    plan.steps = [s for s in plan.steps if s.tool in tools.names]
    if not any(s.tool == "search_bank_analytics" for s in plan.steps):
        plan.steps = fallback_plan(state).steps[:1] + plan.steps
    return _with_open_research(plan, tools), True
