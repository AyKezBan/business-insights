import re
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from insight_agent.agent.analyst import analyze_event
from insight_agent.agent.critic import check, has_errors
from insight_agent.agent.personalizer import personalize
from insight_agent.agent.planner import make_plan, matched_channels
from insight_agent.agent.state import AgentState
from insight_agent.config import Settings
from insight_agent.llm import BudgetExceeded, ChatClient, DailyBudget, LLMError, RunBudget, StructuredLLM
from insight_agent.schemas import (
    Claim,
    ClaimKind,
    CheckIssue,
    EventAnalysis,
    Insight,
    InsightRequest,
    InsightStatus,
    Level,
    PersonalizationDraft,
    SelfCheckReport,
)
from insight_agent.tools import bank as bank_tool
from insight_agent.tools import news as news_tool
from insight_agent.tools.base import Tool, ToolError, ToolRegistry
from insight_agent.tools.profile import CHANNELS, derive_signals, missing_profile_data
from insight_agent.trace import Tracer, new_run_id

_LOCATION = re.compile(r"^(\w+)\[(\d+)\]$")
BROAD_MIN_SCORE = 0.3


class NoArgs(BaseModel):
    pass


@dataclass
class RunRecord:
    state: AgentState
    insight: Insight


class InsightAgent:
    def __init__(
        self,
        client: ChatClient,
        settings: Settings,
        bank: bank_tool.BankIndex,
        news: news_tool.NewsStore,
        daily: DailyBudget | None = None,
    ):
        self.client = client
        self.settings = settings
        self.bank = bank
        self.news = news
        self.daily = daily or DailyBudget(settings.llm_daily_token_budget)
        self.analysis_cache: dict[str, EventAnalysis] = {}
        self.records: dict[str, RunRecord] = {}

    def new_run(self) -> tuple[Tracer, StructuredLLM]:
        tracer = Tracer(new_run_id(), self.settings.trace_dir)
        budget = RunBudget(self.settings.agent_max_tokens_per_run, self.daily)
        llm = StructuredLLM(self.client, budget, tracer, fallback_model=self.settings.llm_model_fallback)
        return tracer, llm

    def tools_for(self, state: AgentState, tracer: Tracer) -> ToolRegistry:
        return ToolRegistry(
            [
                Tool(
                    "search_bank_analytics",
                    "поиск фрагментов аналитики Альфа-Банка по теме.",
                    bank_tool.BankSearchArgs,
                    self.bank.search,
                    bank_tool.summarize,
                ),
                Tool(
                    "search_news",
                    "поиск связанных новостей для недостающего контекста.",
                    news_tool.NewsSearchArgs,
                    self.news.search,
                    news_tool.summarize,
                ),
                Tool(
                    "get_company_signals",
                    "признаки компании из профиля и их каналы влияния.",
                    NoArgs,
                    lambda _: state.signals,
                    lambda r: {"features": [s.feature for s in r]},
                ),
            ],
            tracer,
            self.settings.agent_max_tool_calls,
        )

    @staticmethod
    def absorb(state: AgentState, result) -> None:
        for item in result if isinstance(result, list) else []:
            if isinstance(item, bank_tool.ScoredFragment):
                state.bank[item.fragment.fragment_id] = item.fragment
            elif isinstance(item, news_tool.NewsEvent) and item.event_id != state.event.event_id:
                state.news[item.event_id] = item

    def run(self, request: InsightRequest) -> Insight:
        tracer, llm = self.new_run()
        s = self.settings
        state = AgentState(
            run_id=tracer.run_id,
            request=request,
            signals=derive_signals(request.profile),
            missing_profile=missing_profile_data(request.profile),
        )
        tracer.emit(
            "plan",
            "start",
            {
                "company_id": request.profile.company_id,
                "role": request.role.value,
                "event_id": request.event.event_id,
                "signals": [x.feature for x in state.signals],
                "missing_profile": state.missing_profile,
            },
        )
        tools = self.tools_for(state, tracer)
        issues: list[CheckIssue] = []
        iterations = 0
        try:
            state.analysis = self.analysis_cache.get(request.event.event_id)
            if state.analysis:
                tracer.emit("plan", "analysis_cache_hit", {"event_id": request.event.event_id})
            else:
                state.analysis = analyze_event(llm, s.llm_model_light, request.event)
                self.analysis_cache[request.event.event_id] = state.analysis
            tracer.emit(
                "plan",
                "event_analysis",
                {
                    "facts": len(state.analysis.facts),
                    "impact_channels": state.analysis.impact_channels,
                    "matched_channels": matched_channels(state),
                },
            )

            plan, by_llm = make_plan(llm, s.llm_model_light, state, tools)
            state.plan = [step.model_dump() for step in plan.steps]
            tracer.emit("plan", "plan", {"by_llm": by_llm, "steps": state.plan})
            for step in plan.steps:
                try:
                    self.absorb(state, tools.call(step.tool, step.args, step="execute_plan"))
                except ToolError as exc:
                    tracer.emit("error", "execute_plan", {"tool": step.tool, "error": str(exc)})

            if not state.bank:
                self.broaden_bank_search(state, tools, tracer)

            for iterations in range(1, s.agent_max_iterations + 1):
                state.draft = personalize(llm, s.llm_model_reasoning, state, feedback=issues if iterations > 1 else None)
                issues = check(state, state.draft)
                tracer.emit(
                    "check",
                    f"iteration_{iterations}",
                    {"passed": not has_errors(issues), "issues": [i.model_dump() for i in issues]},
                )
                if not has_errors(issues):
                    break
                if iterations < s.agent_max_iterations:
                    self.adapt(state, issues, tools, tracer)
        except (BudgetExceeded, LLMError) as exc:
            tracer.emit("error", "run", {"error": str(exc)})
            if state.draft is None:
                return self._failed(state, tracer, llm, str(exc))
            issues = check(state, state.draft)

        insight = self._assemble(state, issues, iterations, llm)
        tracer.emit(
            "final",
            "insight",
            {"insight_id": insight.insight_id, "status": insight.status.value, "sources": [x.source_id for x in insight.sources]},
        )
        self.records[insight.insight_id] = RunRecord(state=state, insight=insight)
        return insight

    def broaden_bank_search(self, state: AgentState, tools: ToolRegistry, tracer: Tracer) -> None:
        if state.bank_search_broadened:
            return
        state.bank_search_broadened = True
        channels = matched_channels(state) or (state.analysis.impact_channels if state.analysis else [])
        queries = [CHANNELS[c] for c in channels[:2]] + [state.event.title]
        found_before = len(state.bank)
        for q in queries:
            try:
                self.absorb(state, tools.call("search_bank_analytics", {"query": q, "min_score": BROAD_MIN_SCORE}, step="adapt"))
            except ToolError as exc:
                tracer.emit("error", "adapt", {"error": str(exc)})
                break
        found = len(state.bank) - found_before
        state.adaptations.append(f"broadened_bank_search:+{found}")
        tracer.emit("adapt", "broadened_bank_search", {"queries": queries, "new_fragments": found})

    def adapt(self, state: AgentState, issues: list[CheckIssue], tools: ToolRegistry, tracer: Tracer) -> None:
        codes = {i.code for i in issues if i.severity == "error"}
        if "bank_view_without_bank_source" in codes and not state.bank_search_broadened:
            self.broaden_bank_search(state, tools, tracer)
        state.adaptations.append("revise:" + ",".join(sorted(codes)))
        tracer.emit("adapt", "revise_with_feedback", {"codes": sorted(codes)})

    @staticmethod
    def _drop_invalid(draft: PersonalizationDraft, issues: list[CheckIssue]) -> list[str]:
        bad: dict[str, set[int]] = {}
        for issue in issues:
            m = _LOCATION.match(issue.location or "")
            if issue.severity == "error" and m:
                bad.setdefault(m.group(1), set()).add(int(m.group(2)))
        removed = []
        for section, idx in bad.items():
            items = getattr(draft, section)
            removed += [f"{section}: {items[i].text}" for i in sorted(idx) if i < len(items)]
            setattr(draft, section, [c for i, c in enumerate(items) if i not in idx])
        for section in ("why_it_matters", "consequences", "actions", "bank_view"):
            if not getattr(draft, section):
                getattr(draft, section).append(
                    Claim(text="Недостаточно подтверждённых данных для этого раздела.", kind=ClaimKind.unavailable)
                )
        return removed

    def _assemble(self, state: AgentState, issues: list[CheckIssue], iterations: int, llm: StructuredLLM) -> Insight:
        draft = state.draft
        status = InsightStatus.complete
        missing = list(dict.fromkeys(draft.missing_data + state.missing_profile))
        if has_errors(issues):
            removed = self._drop_invalid(draft, issues)
            status = InsightStatus.limited
            missing.append(f"исключено утверждений без подтверждения: {len(removed)}")
        if all(c.kind == ClaimKind.unavailable for c in draft.bank_view):

            status = InsightStatus.limited
            missing.append("аналитика Альфа-Банка по теме события")

        facts = state.analysis.facts if state.analysis else []
        all_claims = facts + draft.why_it_matters + draft.consequences + draft.risks + draft.opportunities
        all_claims += draft.actions + draft.bank_view
        catalog = state.sources()
        cited = dict.fromkeys(sid for c in all_claims for sid in c.source_ids if sid in catalog)
        cited.update(dict.fromkeys(sid for sc in draft.scenarios for sid in sc.source_ids if sid in catalog))
        e = state.event
        return Insight(
            insight_id=f"{state.request.profile.company_id}:{e.event_id}:{state.run_id[-8:]}",
            run_id=state.run_id,
            event_id=e.event_id,
            company_id=state.request.profile.company_id,
            role=state.request.role,
            status=status,
            headline=draft.headline,
            what_happened=facts,
            why_it_matters=draft.why_it_matters,
            consequences=draft.consequences,
            risks=draft.risks,
            opportunities=draft.opportunities,
            scenarios=draft.scenarios,
            actions=draft.actions,
            bank_view=draft.bank_view,
            model_vs_bank=draft.model_vs_bank,
            impact_level=draft.impact_level,
            impact_rationale=draft.impact_rationale,
            profile_features_used=sorted({f for c in all_claims for f in c.profile_features}),
            sources=[catalog[sid] for sid in cited],
            missing_data=missing,
            self_check=SelfCheckReport(
                passed=not has_errors(issues), issues=issues, iterations=iterations, adaptations=state.adaptations
            ),
            models=llm.models_used,
            generated_at=datetime.now(UTC),
        )

    def _failed(self, state: AgentState, tracer: Tracer, llm: StructuredLLM, reason: str) -> Insight:
        e = state.event
        insight = Insight(
            insight_id=f"{state.request.profile.company_id}:{e.event_id}:{state.run_id[-8:]}",
            run_id=state.run_id,
            event_id=e.event_id,
            company_id=state.request.profile.company_id,
            role=state.request.role,
            status=InsightStatus.failed,
            headline=e.title,
            what_happened=state.analysis.facts if state.analysis else [],
            why_it_matters=[],
            consequences=[],
            risks=[],
            opportunities=[],
            scenarios=[],
            actions=[],
            bank_view=[],
            model_vs_bank=[],
            impact_level=Level.low,
            impact_rationale="Анализ не завершён.",
            profile_features_used=[],
            sources=[e.source],
            missing_data=[f"анализ не выполнен: {reason}"],
            self_check=SelfCheckReport(passed=False, iterations=0, adaptations=state.adaptations),
            models=llm.models_used,
            generated_at=datetime.now(UTC),
        )
        tracer.emit("final", "insight", {"insight_id": insight.insight_id, "status": "failed"})
        return insight
