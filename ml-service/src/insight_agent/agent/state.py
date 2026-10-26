from dataclasses import dataclass, field

from insight_agent.schemas import (
    BankFragment,
    EventAnalysis,
    InsightRequest,
    NewsEvent,
    PersonalizationDraft,
    CheckIssue,
    SourceKind,
    SourceRef,
)
from insight_agent.tools.profile import Signal

PROFILE_PREFIX = "profile:"


@dataclass
class AgentState:
    run_id: str
    request: InsightRequest
    signals: list[Signal]
    missing_profile: list[str]
    analysis: EventAnalysis | None = None
    plan: list[dict] = field(default_factory=list)
    bank: dict[str, BankFragment] = field(default_factory=dict)
    news: dict[str, NewsEvent] = field(default_factory=dict)
    draft: PersonalizationDraft | None = None
    adaptations: list[str] = field(default_factory=list)
    bank_search_broadened: bool = False
    resolved_news_texts: dict[str, str] = field(default_factory=dict)
    unresolved_sources: set[str] = field(default_factory=set)
    evidence_issues: list[CheckIssue] = field(default_factory=list)
    limited_reasons: list[str] = field(default_factory=list)

    @property
    def corpus_revision(self) -> str | None:
        return self.request.corpus_revision

    @property
    def event_content_sha256(self) -> str | None:
        return self.request.event_content_sha256

    @property
    def event(self) -> NewsEvent:
        return self.request.event

    @property
    def signal_features(self) -> set[str]:
        return {s.feature for s in self.signals}

    def source_conflicts(self):
        return [c for event in [self.event, *self.news.values()] for c in event.conflicts if not c.resolved]

    def sources(self) -> dict[str, SourceRef]:
        catalog = {self.event.source.source_id: self.event.source}
        for ref in self.event.related_sources:
            catalog[ref.source_id] = ref
        for ev in self.news.values():
            catalog[ev.source.source_id] = ev.source
            for ref in ev.related_sources:
                catalog[ref.source_id] = ref
        for frag in self.bank.values():
            catalog[frag.fragment_id] = SourceRef(
                source_id=frag.fragment_id,
                kind=SourceKind.bank,
                title=frag.doc_title,
                date=frag.doc_date,
                page=frag.page,
                url=frag.source_url,
                doc_id=frag.doc_id,
                content_sha256=frag.content_sha256,
                excerpt=frag.text,
                statement_type=frag.statement_type,
                target_period=frag.target_period,
                valid_until=frag.valid_until,
            )
        return catalog

    def source_texts(self) -> dict[str, str]:
        texts = {self.event.source.source_id: f"{self.event.title}\n{self.event.text}"}
        for ev in self.news.values():
            texts[ev.source.source_id] = f"{ev.title}\n{ev.text}"
        texts.update(self.resolved_news_texts)
        for source_id in self.unresolved_sources:
            texts.pop(source_id, None)
        for frag in self.bank.values():
            texts[frag.fragment_id] = frag.text
        return texts

    def profile_text(self) -> str:
        return "\n".join(f"{s.feature}: {s.value} ({s.note})" for s in self.signals)
