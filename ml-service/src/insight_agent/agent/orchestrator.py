import hashlib
import json
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from pydantic import BaseModel, Field

from insight_agent.agent.analyst import analyze_event
from insight_agent.agent.critic import check, check_event_analysis, has_errors
from insight_agent.agent.evidence import pack_sources
from insight_agent.agent.personalizer import personalize
from insight_agent.agent.planner import make_plan, matched_channels
from insight_agent.agent.prompts import injection_markers
from insight_agent.agent.state import AgentState
from insight_agent.agent.store import InsightStore
from insight_agent.agent.versions import PROMPT_VERSION, git_sha
from insight_agent.budget import Price, PriceTable, RubleLedger
from insight_agent.config import Settings
from insight_agent.research.fetch import SafeFetcher
from insight_agent.research.indicators import OpenDocument
from insight_agent.research.plan import build_plan
from insight_agent.research.service import OpenResearch, TtlCache
from insight_agent.materials.models import CorpusSnapshot
from insight_agent.materials.artifacts import digest
from insight_agent.materials.policy import bank_policy, publication_day, published_by, restrict_bank_args
from insight_agent.materials.identity import event_content_sha256
from insight_agent.materials.runtime import CorpusBundle, CorpusError, CorpusRegistry
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
    NewsEvent,
    Outcome,
    PersonalizationDraft,
    SourceKind,
    SourceRef,
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
# Токенов, которые надо оставить на смысловую проверку после исправлений: без неё все утверждения считаются непроверенными.
VERIFY_RESERVE_TOKENS = 35_000


class RequestIdConflict(ValueError):
    pass


class NoArgs(BaseModel):
    pass


class OpenSearchArgs(BaseModel):
    query: str | None = Field(default=None, max_length=200)
    top_k: int = Field(default=3, ge=1, le=5)


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
        research: OpenResearch | None = None,
        corpus_registry: CorpusRegistry | None = None,
    ):
        self.client = client
        self.settings = settings
        self.bank = bank
        self.news = news
        self.corpus_snapshot = corpus_snapshot
        self.corpus_registry = corpus_registry
        self.daily = daily or self._build_daily_budget(settings)
        self.research = research if research is not None else self._build_research(settings)
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

    @staticmethod
    def _build_research(settings: Settings) -> OpenResearch | None:
        if not settings.open_research_enabled:
            return None
        cache_dir = settings.open_research_cache_dir or settings.trace_dir / "open_cache"
        return OpenResearch(SafeFetcher(httpx.Client()), TtlCache(cache_dir))

    @staticmethod
    def _as_news_event(doc: OpenDocument) -> NewsEvent:
        indicator = doc.kind == "indicator"
        published = doc.published if not indicator and isinstance(doc.published, datetime) else None
        day = None if indicator else doc.published_date or (doc.published.date() if published else doc.published)
        precision = "timestamp" if published else "date" if day else "unknown"
        ref = SourceRef(source_id=doc.source_id, kind=SourceKind.news, title=doc.title, date=day, url=doc.url,
                        content_sha256=digest(doc.text.encode()), published_date=day, published_precision=precision,
                        material_type="indicator" if indicator else "news_article", retrieved_at=doc.retrieved_at,
                        raw_publication=doc.raw_publication, source_utc_offset_seconds=doc.source_utc_offset_seconds,
                        timezone_provenance=doc.timezone_provenance,
                        target_period=doc.published.isoformat() if indicator else None)
        return NewsEvent(event_id=doc.source_id, title=doc.title, text=doc.text, published_at=published,
                         published_date=day, published_precision=precision, source=ref, topics=doc.topics)

    def new_run(self) -> tuple[Tracer, StructuredLLM]:
        tracer = Tracer(new_run_id(), self.settings.trace_dir)
        deadline = time.monotonic() + self.settings.agent_max_seconds
        budget = RunBudget(self.settings.agent_max_tokens_per_run, self.daily, deadline)
        llm = StructuredLLM(self.client, budget, tracer, fallback_model=self.settings.llm_model_fallback)
        return tracer, llm

    def tools_for(self, state: AgentState, tracer: Tracer, bundle: CorpusBundle | None = None) -> ToolRegistry:
        if bundle is None and self.corpus_registry:
            bundle = (self.corpus_registry.get(state.request.corpus_revision) if state.request.corpus_revision
                      else self.corpus_registry.current())
        bank, news = (bundle.bank, bundle.news) if bundle else (self.bank, self.news)
        as_of = state.request.as_of
        bounds = bank_policy(state.event, today=(as_of or datetime.now(UTC)).date(),
                             cutoff=as_of.date() if as_of else None, max_age_days=self.settings.bank_max_age_days)
        tracer.emit("plan", "bank_policy", {"not_before": str(bounds[0]), "as_of": str(bounds[1]),
            "event_date_unknown": publication_day(state.event) is None,
            "request_as_of": as_of.isoformat() if as_of else None})

        def search_bank(args):
            restricted = restrict_bank_args(args, bounds)
            return [] if restricted is None else bank.search(restricted, as_of=bounds[1])

        def search_news(args):
            if as_of is not None:
                requested = args.not_after
                if requested is not None and requested.tzinfo is None:
                    requested = requested.replace(tzinfo=UTC)
                limit = as_of if requested is None else min(as_of, requested)
                args = args.model_copy(update={"not_after": limit})
            found = news.search(args)
            for event in found:
                self.resolve_news(state, event, news=news)
            return found

        def search_open(args: OpenSearchArgs):
            plan = build_plan(state.request.profile, matched_channels(state), args.query)
            if plan.empty:
                return []
            event_day = publication_day(state.event)
            when = (as_of.date() if as_of else event_day) or datetime.now(UTC).date()
            tracer.emit("plan", "open_research_plan", {"as_of": when.isoformat(), "reasons": plan.reasons})
            found = self.research.search(plan, as_of=when, top_k=args.top_k)
            for message in found.errors:
                tracer.emit("error", "open_research", {"error": message})
            events = []
            for doc in found.docs:
                event = self._as_news_event(doc)
                # Research selects an effective day for indicators; press still has to
                # satisfy the full publication instant before it becomes run evidence.
                if as_of is not None and doc.kind != "indicator" and not published_by(event, as_of):
                    continue
                events.append(event)
                state.resolved_news_texts[doc.source_id] = doc.text
            return events

        open_tools = [Tool(
            "search_open_sources",
            "официальные открытые данные по профилю клиента: ключевая ставка и курсы валют Банка России, его пресс-релизы; что искать, определяется профилем.",
            OpenSearchArgs,
            search_open,
            lambda r: {"documents": [{"id": e.event_id, "url": e.source.url, "date": str(e.source.date)} for e in r]},
        )] if self.research is not None else []

        return ToolRegistry(
            [
                *open_tools,
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

    def resolve_news(self, state: AgentState, event, *, news=None) -> None:
        if any(not c.resolved for c in event.conflicts):
            state.limited_reasons.append(f"источники события {event.event_id} расходятся; требуется проверка, победитель не выбран")
        for ref in [event.source] + event.related_sources:
            text = news.resolve_source_text(ref.source_id) if news is not None else state.resolved_news_texts.get(ref.source_id)
            if text is None and news is None and state.request.corpus_revision is None:
                text = self.news.resolve_source_text(ref.source_id)
            if ref.source_id == event.source.source_id:
                # The request may carry a newer version than this process's pinned corpus.
                text = event.text
            if text is None or (ref.content_sha256 and digest(text.encode()) != ref.content_sha256):
                state.unresolved_sources.add(ref.source_id)
                state.limited_reasons.append(f"нет подтверждённого текста версии источника: {ref.source_id}")
            else:
                state.resolved_news_texts[ref.source_id] = text
            if state.request.as_of and (ref.first_seen_at is None or ref.first_seen_at > state.request.as_of):
                timing = "получена после выбранной даты" if ref.first_seen_at else "не имеет известного времени первого получения"
                reason = (f"Эта версия источника «{ref.title}» {timing}; "
                          "восстановить, что было известно на тот момент, нельзя.")
                if reason not in state.limited_reasons:
                    state.limited_reasons.append(reason)
        if state.request.as_of and event.published_at is None:
            day = publication_day(event)
            if day is None or day == state.request.as_of.date():
                reason = ("Точное время публикации неизвестно, поэтому нельзя подтвердить, "
                          "что материал был доступен к выбранному времени.")
                if reason not in state.limited_reasons:
                    state.limited_reasons.append(reason)

    def _pin_request(self, request: InsightRequest) -> tuple[InsightRequest, CorpusBundle | None]:
        request = request.model_copy(deep=True)
        expected = event_content_sha256(request.event)
        if request.event_content_sha256 and request.event_content_sha256 != expected:
            raise CorpusError("Event semantic hash does not match request", "corpus_event_mismatch")
        bundle = None
        if request.corpus_revision and self.corpus_registry is None:
            raise CorpusError(f"Исходная версия материалов {request.corpus_revision} недоступна")
        if self.corpus_registry:
            bundle = (self.corpus_registry.get(request.corpus_revision) if request.corpus_revision
                      else self.corpus_registry.current())
            canonical = next((event for event in bundle.news.events if event.event_id == request.event.event_id), None)
            if (request.corpus_revision and canonical is None) or (
                    canonical is not None and event_content_sha256(canonical) != expected):
                raise CorpusError("Event does not match the requested corpus revision", "corpus_event_mismatch")
            if canonical is not None:
                request.event = canonical  # Same semantic body; trusted observation provenance from the snapshot.
            for ref in [request.event.source, *request.event.related_sources]:
                text = request.event.text if ref.source_id == request.event.source.source_id else bundle.news.resolve_source_text(ref.source_id)
                if text is None or (ref.content_sha256 and digest(text.encode()) != ref.content_sha256):
                    raise CorpusError("Source does not match the requested corpus revision", "corpus_event_mismatch")
            request.corpus_revision = bundle.revision
        request.event_content_sha256 = expected
        return request, bundle

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
        request, bundle = self._pin_request(request)
        tracer, llm = self.new_run()
        s = self.settings
        state = AgentState(
            run_id=tracer.run_id,
            request=request,
            signals=derive_signals(request.profile),
            missing_profile=missing_profile_data(request.profile),
        )
        self.resolve_news(state, request.event, news=bundle.news if bundle else None)
        if publication_day(request.event) is None:
            state.limited_reasons.append("Дата публикации события неизвестна; окно аналитики рассчитано "
                                         + ("на выбранную дату." if request.as_of else "на текущую дату UTC."))
        tracer.emit(
            "plan",
            "start",
            {
                "company_id": request.profile.company_id,
                "role": request.role.value,
                "event_id": request.event.event_id,
                "signals": [x.feature for x in state.signals],
                "missing_profile": state.missing_profile,
                "corpus_revision": request.corpus_revision,
                "event_content_sha256": request.event_content_sha256,
                "tool_versions": bundle.snapshot.tool_versions if bundle else {},
                "source_hashes": {sid: digest(text.encode()) for sid, text in state.source_texts().items()},
                "budget": self._budget_snapshot(),
                "request_id": request.request_id,
                "as_of": request.as_of.isoformat() if request.as_of else None,
                "as_of_mode": "publication_cutoff",
                "parent_run_id": request.parent_run_id,
                "prompt_version": PROMPT_VERSION,
                "git_sha": git_sha(),
                "models": {"light": s.llm_model_light, "reasoning": s.llm_model_reasoning, "fallback": s.llm_model_fallback},
            },
        )
        scanned: set[str] = set()
        self._scan_injection(state, tracer, scanned)
        tools = self.tools_for(state, tracer, bundle)
        issues: list[CheckIssue] = []
        iterations = 0
        try:
            tracer.agent = "analyst"
            texts = state.source_texts()
            _, truncated = pack_sources(texts)
            if truncated:
                state.limited_reasons.append("контекст разбора сокращён; полный текст сохранён в корпусе: " + ", ".join(truncated))
            effective_day = bank_policy(state.event, today=(request.as_of or datetime.now(UTC)).date(),
                cutoff=request.as_of.date() if request.as_of else None, max_age_days=s.bank_max_age_days)[1]
            key = hashlib.sha256(json.dumps({"event": request.event_content_sha256,
                "sources": {sid: digest(text.encode()) for sid, text in texts.items()},
                "as_of": request.as_of.astimezone(UTC).isoformat() if request.as_of else effective_day.isoformat(),
                "policy": PROMPT_VERSION, "model": s.llm_model_light}, sort_keys=True).encode()).hexdigest()
            cached = self.analysis_cache.get(key)
            if cached:
                state.analysis = cached[0].model_copy(deep=True)
                state.evidence_issues = list(cached[1])
                tracer.emit("plan", "analysis_cache_hit", {"event_id": request.event.event_id})
            else:
                state.analysis = self._retry_once(tracer, "analyze_event",
                    lambda: analyze_event(llm, s.llm_model_light, request.event, source_texts=texts))
                state.evidence_issues = self._render_quotes(state, "facts", state.analysis.facts)
                state.evidence_issues += check_event_analysis(request.event, state.analysis, texts)
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

            tracer.agent = "planner"
            plan, by_llm = make_plan(llm, s.llm_model_light, state, tools)
            state.plan = [step.model_dump() for step in plan.steps]
            tracer.emit("plan", "plan", {"by_llm": by_llm, "steps": state.plan})
            tracer.agent = "researcher"
            for step in plan.steps:
                try:
                    self.absorb(state, tools.call(step.tool, step.args, step="execute_plan"))
                except ToolError as exc:
                    tracer.emit("error", "execute_plan", {"tool": step.tool, "error": str(exc)})

            if not state.bank:
                self.broaden_bank_search(state, tools, tracer)
            self._scan_injection(state, tracer, scanned)

            for iterations in range(1, s.agent_max_iterations + 1):
                tracer.agent = "personalizer"
                if iterations > 1 and not self.daily.optional_allowed():
                    self._skip_optional(state, tracer, "revise")
                    iterations -= 1
                    break
                if iterations > 1 and s.agent_max_tokens_per_run - llm.budget.used < VERIFY_RESERVE_TOKENS:
                    self._skip_optional(state, tracer, "revise",
                                        "лимит токенов запуска: оставлен запас на смысловую проверку, дальнейшие исправления пропущены")
                    iterations -= 1
                    break
                state.draft = self._retry_once(tracer, "personalize",
                    lambda: personalize(llm, s.llm_model_reasoning, state, feedback=issues if iterations > 1 else None))
                tracer.agent = "verifier"
                issues = []
                for section in ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view"):
                    issues += self._render_quotes(state, section, getattr(state.draft, section))
                issues += check(state, state.draft)
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
                return self._failed(state, tracer, llm, self._failure_message(error_code(exc)), error_code(exc))
            issues = []
            for section in ("why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view"):
                issues += self._render_quotes(state, section, getattr(state.draft, section))
            issues += check(state, state.draft)

        # Remove deterministic failures first; verdicts bind the remaining exact snapshot.
        # Every final draft, including one retained after a failed revision, passes this gate.
        removed = self._drop_invalid(state.draft, issues, fill=False)
        from insight_agent.agent.semantic import semantic_issues
        semantic = semantic_issues(llm, s.llm_model_reasoning, state, state.draft)
        removed += self._drop_invalid(state.draft, semantic, fill=False)
        issues += semantic
        tracer.agent = "orchestrator"
        insight = self._assemble(state, issues, iterations, llm, removed_count=len(removed))
        tracer.emit(
            "final",
            "insight",
            {"insight_id": insight.insight_id, "status": insight.status.value, "outcome": insight.outcome.value,
             "corpus_revision": request.corpus_revision, "event_content_sha256": request.event_content_sha256,
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
        tracer.agent = "researcher"
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
    def _retry_once(tracer: Tracer, step: str, call):
        """Шлюз иногда не отвечает до таймаута. Один повтор шага; расход неудавшейся попытки уже учтён оценкой.

        Бюджетные исключения (BudgetExceeded) не повторяются."""
        try:
            return call()
        except LLMError as exc:
            tracer.emit("adapt", "retry_after_provider_failure", {"step": step, "error": str(exc)[:200]})
            return call()

    @staticmethod
    def _skip_optional(state: AgentState, tracer: Tracer, step: str,
                       reason: str = "дневной бюджет близок к лимиту: повторные шаги пропущены") -> None:
        if reason not in state.limited_reasons:
            state.limited_reasons.append(reason)
        tracer.emit("adapt", "budget_soft_stop", {"skipped": step})

    def adapt(self, state: AgentState, issues: list[CheckIssue], tools: ToolRegistry, tracer: Tracer) -> None:
        codes = {i.code for i in issues if i.severity == "error"}
        if "bank_view_without_bank_source" in codes and not state.bank_search_broadened:
            self.broaden_bank_search(state, tools, tracer)
        tracer.agent = "verifier"
        state.adaptations.append("revise:" + ",".join(sorted(codes)))
        tracer.emit("adapt", "revise_with_feedback", {"codes": sorted(codes)})

    @staticmethod
    def _drop_invalid(draft: PersonalizationDraft, issues: list[CheckIssue], *, fill=True) -> list[str]:
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
        for section in ("why_it_matters", "consequences", "actions", "bank_view") if fill else ():
            if not getattr(draft, section):
                getattr(draft, section).append(
                    Claim(text="Недостаточно подтверждённых данных для этого раздела.", kind=ClaimKind.unavailable)
                )
        return removed

    @staticmethod
    def _render_quotes(state, section, claims) -> list[CheckIssue]:
        from insight_agent.agent.spans import render_quote
        issues = []
        for index, claim in enumerate(claims):
            if claim.kind not in (ClaimKind.fact, ClaimKind.assessment):
                continue
            claim.claim_id = None
            try:
                claims[index] = render_quote(claim, state.source_texts(), state.sources(), state.bank)
            except ValueError:
                issues.append(CheckIssue(code="invalid_evidence_span", severity="error", location=f"{section}[{index}]",
                                         message="Для цитаты нет полного проверенного фрагмента источника."))
        return issues

    @staticmethod
    def _failure_message(code):
        return {"budget_run": "Исчерпан лимит одного анализа.", "budget_daily": "Исчерпан дневной лимит.",
                "timeout": "Истекло время анализа."}.get(code, "Не удалось получить проверенный ответ модели.")

    def _assemble(self, state: AgentState, issues: list[CheckIssue], iterations: int, llm: StructuredLLM,
                  *, removed_count=0) -> Insight:
        draft = state.draft
        status = InsightStatus.complete
        outcome = Outcome.full
        # Pending model text has already been sanitized; only controlled placeholders are added here.
        self._drop_invalid(draft, [])
        missing = list(dict.fromkeys(draft.missing_data + state.missing_profile))
        if has_errors(issues):
            status = InsightStatus.limited
            missing.append(f"исключено утверждений без подтверждения: {removed_count}")
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
        # BR-01 и BR-02: событие без связи с компанией не становится персональным инсайтом, а приоритет
        # подтверждается оставшимися персональными основаниями, а не исходной оценкой модели.
        personal = any(c.kind != ClaimKind.unavailable and c.profile_features
                       for c in draft.why_it_matters + draft.consequences + draft.risks + draft.opportunities)
        if draft.company_link == "none" or not personal:
            status, outcome = InsightStatus.limited, Outcome.refused
            missing.append("связь события с этой компанией не установлена" if draft.company_link == "none"
                           else "после проверки не осталось персональных оснований")
        if (draft.company_link == "none" or not personal) and draft.impact_level != Level.low:
            draft.impact_level = Level.low
            missing.append("приоритет понижен до низкого: нет подтверждённых персональных оснований")

        facts = [c.model_copy(deep=True) for c in (state.analysis.facts if state.analysis else [])]
        all_claims = facts + draft.why_it_matters + draft.consequences + draft.risks + draft.opportunities
        all_claims += draft.actions + draft.bank_view
        catalog = state.sources()
        cited = dict.fromkeys(sid for c in all_claims for sid in c.source_ids if sid in catalog)
        cited.update(dict.fromkeys(sid for sc in draft.scenarios for sid in sc.source_ids if sid in catalog))
        e = state.event
        cited.update(dict.fromkeys(sid for c in state.source_conflicts() for sid in c.source_ids if sid in catalog))
        insight_id = f"{state.request.profile.company_id}:{e.event_id}:{state.run_id[-8:]}"
        bindings = self._number_claims(insight_id, [facts, draft.why_it_matters, draft.consequences, draft.risks,
                                                  draft.opportunities, draft.actions, draft.bank_view])
        llm.tracer.emit("check", "published_claims", {"claims": bindings})
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
            corpus_revision=state.request.corpus_revision,
            event_content_sha256=state.request.event_content_sha256,
            as_of=state.request.as_of,
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
                passed=not has_errors(issues),
                issues=[i.model_copy(update={"message": "Проверка выявила ограничение в основаниях утверждения."}) for i in issues],
                iterations=iterations, adaptations=state.adaptations
            ),
            models=llm.models_used,
            generated_at=datetime.now(UTC),
        )

    @staticmethod
    def _number_claims(insight_id: str, sections: list[list[Claim]]) -> list[dict]:
        n, bindings = 0, []
        for section in sections:
            for claim in section:
                n += 1
                reviewed_id = claim.claim_id
                claim.claim_id = f"{insight_id}:c{n}"
                bindings.append({"claim_id": claim.claim_id, "semantic_candidate_id": reviewed_id,
                                 "text_sha256": hashlib.sha256(claim.text.encode()).hexdigest(),
                                 "source_ids": list(claim.source_ids),
                                 "span_ids": [span.span_id for span in claim.evidence_spans],
                                 "evidence_spans": [span.model_dump() for span in claim.evidence_spans]})
        return bindings

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
            corpus_revision=state.request.corpus_revision,
            event_content_sha256=state.request.event_content_sha256,
            as_of=state.request.as_of,
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
