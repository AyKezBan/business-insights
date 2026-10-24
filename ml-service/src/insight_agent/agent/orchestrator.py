import hashlib
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

from pydantic import BaseModel

from insight_agent.agent.analyst import analyze_event
from insight_agent.agent.critic import check, check_event_analysis, has_errors
from insight_agent.agent.evidence import event_fingerprint, pack_sources
from insight_agent.agent.personalizer import personalize
from insight_agent.agent.planner import make_plan, matched_channels
from insight_agent.agent.prompts import injection_markers
from insight_agent.agent.state import AgentState
from insight_agent.agent.store import InsightStore
from insight_agent.agent.versions import PROMPT_VERSION, git_sha
from insight_agent.budget import Price, PriceTable, RubleLedger
from insight_agent.config import Settings
from insight_agent.materials.models import CorpusSnapshot
from insight_agent.materials.artifacts import digest
from insight_agent.materials.policy import bank_policy, restrict_bank_args
from insight_agent.llm import BudgetExceeded, ChatClient, DailyBudget, LLMError, RunBudget, StructuredLLM, error_code
from insight_agent.schemas import (
    Claim,
    ClaimKind,
    CheckIssue,
    EventAnalysis,
    Insight,
    InsightRequest,
    InsightStatus,
    Level,
    Outcome,
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


MAX_REMEMBERED = 500


class RequestIdConflict(ValueError):
    pass


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
        corpus_snapshot: CorpusSnapshot | None = None,
    ):
        self.client = client
        self.settings = settings
        self.bank = bank
        self.news = news
        self.corpus_snapshot = corpus_snapshot
        self.daily = daily or self._build_daily_budget(settings)
        self.analysis_cache: dict[str, tuple[EventAnalysis, list[CheckIssue]]] = {}
        self.records: OrderedDict[str, RunRecord] = OrderedDict()
        self.requests: OrderedDict[str, tuple[str, Insight]] = OrderedDict()
        self.store = InsightStore(settings.trace_dir / "insights")
        self._request_locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    @staticmethod
    def _build_daily_budget(settings: Settings) -> DailyBudget:
        prices = PriceTable.from_json(
            settings.llm_prices_json,
            Price(settings.llm_price_default_in_rub_per_1k, settings.llm_price_default_out_rub_per_1k),
        )
        ledger = RubleLedger(
            limit=settings.llm_daily_budget_rub,
            warn=settings.llm_budget_warn_rub,
            stop_optional=settings.llm_budget_stop_optional_rub,
            state_path=settings.budget_state_path or settings.trace_dir / "budget_state.json",
            timezone=settings.budget_timezone,
        )
        return DailyBudget(settings.llm_daily_token_budget, ledger, prices)

    def new_run(self) -> tuple[Tracer, StructuredLLM]:
        tracer = Tracer(new_run_id(), self.settings.trace_dir)
        deadline = time.monotonic() + self.settings.agent_max_seconds
        budget = RunBudget(self.settings.agent_max_tokens_per_run, self.daily, deadline)
        llm = StructuredLLM(self.client, budget, tracer, fallback_model=self.settings.llm_model_fallback)
        return tracer, llm

    def tools_for(self, state: AgentState, tracer: Tracer) -> ToolRegistry:
        as_of = state.request.as_of
        bounds = bank_policy(state.event, today=(as_of or datetime.now(UTC)).date(), max_age_days=self.settings.bank_max_age_days)
        tracer.emit("plan", "bank_policy", {"not_before": str(bounds[0]), "as_of": str(bounds[1]),
            "event_date_unknown": state.event.published_at is None,
            "request_as_of": as_of.isoformat() if as_of else None})

        def search_bank(args):
            restricted = restrict_bank_args(args, bounds)
            return [] if restricted is None else self.bank.search(restricted, as_of=bounds[1])

        def search_news(args):
            if as_of is not None:
                limit = as_of if args.not_after is None else min(as_of, args.not_after)
                args = args.model_copy(update={"not_after": limit})
            return self.news.search(args)

        return ToolRegistry(
            [
                Tool(
                    "search_bank_analytics",
                    "поиск фрагментов аналитики Альфа-Банка по теме.",
                    bank_tool.BankSearchArgs,
                    search_bank,
                    bank_tool.summarize,
                ),
                Tool(
                    "search_news",
                    "поиск связанных новостей для недостающего контекста.",
                    news_tool.NewsSearchArgs,
                    search_news,
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

    def resolve_news(self, state: AgentState, event) -> None:
        if any(not c.resolved for c in event.conflicts):
            state.limited_reasons.append(f"источники события {event.event_id} расходятся; требуется проверка, победитель не выбран")
        for ref in [event.source] + event.related_sources:
            text = self.news.resolve_source_text(ref.source_id)
            if ref.source_id == event.source.source_id:
                # The request may carry a newer version than this process's pinned corpus.
                text = event.text
            if text is None or (ref.content_sha256 and digest(text.encode()) != ref.content_sha256):
                state.unresolved_sources.add(ref.source_id)
                state.limited_reasons.append(f"нет подтверждённого текста версии источника: {ref.source_id}")
            else:
                state.resolved_news_texts[ref.source_id] = text

    def absorb(self, state: AgentState, result) -> None:
        for item in result if isinstance(result, list) else []:
            if isinstance(item, bank_tool.ScoredFragment):
                state.bank[item.fragment.fragment_id] = item.fragment
            elif isinstance(item, news_tool.NewsEvent) and item.event_id != state.event.event_id:
                state.news[item.event_id] = item
                self.resolve_news(state, item)

    def get_record(self, insight_id: str) -> RunRecord | None:
        record = self.records.get(insight_id)
        if record is None and (loaded := self.store.load(insight_id)):
            record = RunRecord(state=loaded[0], insight=loaded[1])
            self._remember(self.records, insight_id, record)
        return record

    @staticmethod
    def _remember(cache: OrderedDict, key: str, value) -> None:
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > MAX_REMEMBERED:
            cache.popitem(last=False)

    def run(self, request: InsightRequest) -> Insight:
        """Повтор запроса с тем же request_id и тем же содержимым отдаёт готовый инсайт без новых вызовов LLM.

        Неудачный запуск (failed) не запоминается: повтор после сбоя считается новым запуском.
        """
        rid = request.request_id
        if not rid:
            return self._run(request)
        fingerprint = hashlib.sha256(
            request.model_dump_json(exclude={"request_id", "parent_run_id"}).encode()
        ).hexdigest()
        with self._locks_guard:
            if len(self._request_locks) > 5 * MAX_REMEMBERED:
                self._request_locks = {k: v for k, v in self._request_locks.items() if v.locked()}
            lock = self._request_locks.setdefault(rid, threading.Lock())
        with lock:
            known = self.requests.get(rid)
            if known:
                if known[0] != fingerprint:
                    raise RequestIdConflict(rid)
                return known[1].model_copy(update={"cache_hit": True})
            insight = self._run(request)
            if insight.status != InsightStatus.failed:
                self._remember(self.requests, rid, (fingerprint, insight))
            return insight

    def _run(self, request: InsightRequest) -> Insight:
        tracer, llm = self.new_run()
        s = self.settings
        state = AgentState(
            run_id=tracer.run_id,
            request=request,
            signals=derive_signals(request.profile),
            missing_profile=missing_profile_data(request.profile),
        )
        self.resolve_news(state, request.event)
        if request.event.published_at is None:
            state.limited_reasons.append("дата события неизвестна; окно аналитики рассчитано на текущую дату UTC")
        tracer.emit(
            "plan",
            "start",
            {
                "company_id": request.profile.company_id,
                "role": request.role.value,
                "event_id": request.event.event_id,
                "signals": [x.feature for x in state.signals],
                "missing_profile": state.missing_profile,
                "corpus_revision": self.corpus_snapshot.revision if self.corpus_snapshot else None,
                "tool_versions": self.corpus_snapshot.tool_versions if self.corpus_snapshot else {},
                "source_hashes": {sid: digest(text.encode()) for sid, text in state.source_texts().items()},
                "budget": self._budget_snapshot(),
                "request_id": request.request_id,
                "as_of": request.as_of.isoformat() if request.as_of else None,
                "parent_run_id": request.parent_run_id,
                "prompt_version": PROMPT_VERSION,
                "git_sha": git_sha(),
                "models": {"light": s.llm_model_light, "reasoning": s.llm_model_reasoning, "fallback": s.llm_model_fallback},
            },
        )
        scanned: set[str] = set()
        self._scan_injection(state, tracer, scanned)
        tools = self.tools_for(state, tracer)
        issues: list[CheckIssue] = []
        iterations = 0
        try:
            texts = state.source_texts()
            _, truncated = pack_sources(texts)
            if truncated:
                state.limited_reasons.append("контекст разбора сокращён; полный текст сохранён в корпусе: " + ", ".join(truncated))
            key = f"{event_fingerprint(request.event, source_texts=texts)}|{PROMPT_VERSION}|{s.llm_model_light}"
            cached = self.analysis_cache.get(key)
            if cached:
                state.analysis = cached[0].model_copy(deep=True)
                state.evidence_issues = list(cached[1])
                tracer.emit("plan", "analysis_cache_hit", {"event_id": request.event.event_id})
            else:
                state.analysis = analyze_event(llm, s.llm_model_light, request.event, source_texts=texts)
                state.evidence_issues = check_event_analysis(request.event, state.analysis, texts)
                bad = {int(m.group(2)) for issue in state.evidence_issues
                       if issue.severity == "error" and (m := _LOCATION.match(issue.location or ""))}
                state.analysis.facts = [c for i, c in enumerate(state.analysis.facts) if i not in bad]
                state.analysis.summary = " ".join(c.text for c in state.analysis.facts) or request.event.title
                self.analysis_cache[key] = (state.analysis.model_copy(deep=True), list(state.evidence_issues))
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
            self._scan_injection(state, tracer, scanned)

            for iterations in range(1, s.agent_max_iterations + 1):
                if iterations > 1 and not self.daily.optional_allowed():
                    self._skip_optional(state, tracer, "revise")
                    iterations -= 1
                    break
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
                return self._failed(state, tracer, llm, str(exc), error_code(exc))
            issues = check(state, state.draft)

        insight = self._assemble(state, issues, iterations, llm)
        tracer.emit(
            "final",
            "insight",
            {"insight_id": insight.insight_id, "status": insight.status.value, "outcome": insight.outcome.value,
             "error_code": insight.error_code, "sources": [x.source_id for x in insight.sources],
             "usage": llm.usage()},
        )
        self._remember(self.records, insight.insight_id, RunRecord(state=state, insight=insight))
        self.store.save(state, insight)
        return insight

    def broaden_bank_search(self, state: AgentState, tools: ToolRegistry, tracer: Tracer) -> None:
        if state.bank_search_broadened:
            return
        if not self.daily.optional_allowed():
            self._skip_optional(state, tracer, "broaden_bank_search")
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

    @staticmethod
    def _scan_injection(state: AgentState, tracer: Tracer, scanned: set[str]) -> None:
        for source_id, text in state.source_texts().items():
            if source_id in scanned:
                continue
            scanned.add(source_id)
            if markers := injection_markers(text):
                tracer.emit("plan", "injection_suspected", {"source_id": source_id, "markers": markers})

    def _budget_snapshot(self) -> dict | None:
        ledger = self.daily.ledger
        if ledger is None:
            return None
        return {"spent_rub": str(ledger.spent), "limit_rub": str(ledger.limit), "level": ledger.level()}

    @staticmethod
    def _skip_optional(state: AgentState, tracer: Tracer, step: str) -> None:
        reason = "дневной бюджет близок к лимиту: повторные шаги пропущены"
        if reason not in state.limited_reasons:
            state.limited_reasons.append(reason)
        tracer.emit("adapt", "budget_soft_stop", {"skipped": step})

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
            if not hasattr(draft, section):
                continue
            items = getattr(draft, section)
            removed += [f"{section}: {i}" for i in sorted(idx) if i < len(items)]
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
        outcome = Outcome.full
        missing = list(dict.fromkeys(draft.missing_data + state.missing_profile))
        if has_errors(issues):
            removed = self._drop_invalid(draft, issues)
            status = InsightStatus.limited
            missing.append(f"исключено утверждений без подтверждения: {len(removed)}")
        if all(c.kind == ClaimKind.unavailable for c in draft.bank_view):

            status = InsightStatus.limited
            missing.append("аналитика Альфа-Банка по теме события")
        issues = state.evidence_issues + issues
        if has_errors(state.evidence_issues) or state.limited_reasons:
            status = InsightStatus.limited
        missing.extend(dict.fromkeys(state.limited_reasons))
        if status == InsightStatus.limited:
            grounded = any(c.kind != ClaimKind.unavailable for c in draft.why_it_matters + draft.consequences)
            outcome = Outcome.limited if grounded else Outcome.refused

        facts = [c.model_copy(deep=True) for c in (state.analysis.facts if state.analysis else [])]
        all_claims = facts + draft.why_it_matters + draft.consequences + draft.risks + draft.opportunities
        all_claims += draft.actions + draft.bank_view
        catalog = state.sources()
        cited = dict.fromkeys(sid for c in all_claims for sid in c.source_ids if sid in catalog)
        cited.update(dict.fromkeys(sid for sc in draft.scenarios for sid in sc.source_ids if sid in catalog))
        e = state.event
        cited.update(dict.fromkeys(sid for c in state.source_conflicts() for sid in c.source_ids if sid in catalog))
        insight_id = f"{state.request.profile.company_id}:{e.event_id}:{state.run_id[-8:]}"
        self._number_claims(insight_id, [facts, draft.why_it_matters, draft.consequences, draft.risks,
                                         draft.opportunities, draft.actions, draft.bank_view])
        return Insight(
            insight_id=insight_id,
            run_id=state.run_id,
            event_id=e.event_id,
            company_id=state.request.profile.company_id,
            role=state.request.role,
            status=status,
            outcome=outcome,
            request_id=state.request.request_id,
            parent_run_id=state.request.parent_run_id,
            headline=e.title,
            what_happened=facts,
            why_it_matters=draft.why_it_matters,
            consequences=draft.consequences,
            risks=draft.risks,
            opportunities=draft.opportunities,
            scenarios=draft.scenarios,
            actions=draft.actions,
            bank_view=draft.bank_view,
            model_vs_bank=[],
            impact_level=draft.impact_level,
            impact_rationale=" ".join(c.text for c in draft.why_it_matters),
            profile_features_used=sorted({f for c in all_claims for f in c.profile_features}),
            sources=[catalog[sid] for sid in cited],
            missing_data=missing,
            self_check=SelfCheckReport(
                passed=not has_errors(issues), issues=issues, iterations=iterations, adaptations=state.adaptations
            ),
            models=llm.models_used,
            generated_at=datetime.now(UTC),
        )

    @staticmethod
    def _number_claims(insight_id: str, sections: list[list[Claim]]) -> None:
        n = 0
        for section in sections:
            for claim in section:
                n += 1
                claim.claim_id = f"{insight_id}:c{n}"

    def _failed(self, state: AgentState, tracer: Tracer, llm: StructuredLLM, reason: str, code: str) -> Insight:
        e = state.event
        insight = Insight(
            insight_id=f"{state.request.profile.company_id}:{e.event_id}:{state.run_id[-8:]}",
            run_id=state.run_id,
            event_id=e.event_id,
            company_id=state.request.profile.company_id,
            role=state.request.role,
            status=InsightStatus.failed,
            outcome=Outcome.none,
            error_code=code,
            request_id=state.request.request_id,
            parent_run_id=state.request.parent_run_id,
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
        tracer.emit("final", "insight", {"insight_id": insight.insight_id, "status": "failed", "outcome": "none", "error_code": code,
                                                  "usage": llm.usage()})
        return insight
