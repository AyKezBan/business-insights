from dataclasses import dataclass, field

from insight_agent.schemas import (
    BankFragment,
    EventAnalysis,
    InsightRequest,
    NewsEvent,
    PersonalizationDraft,
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

    @property
    def event(self) -> NewsEvent:
        return self.request.event

    @property
    def signal_features(self) -> set[str]:
        return {s.feature for s in self.signals}

    def sources(self) -> dict[str, SourceRef]:
        catalog = {self.event.source.source_id: self.event.source}
        for ref in self.event.related_sources:
            catalog[ref.source_id] = ref
        for ev in self.news.values():
            catalog[ev.source.source_id] = ev.source
        for frag in self.bank.values():
            catalog[frag.fragment_id] = SourceRef(
                source_id=frag.fragment_id,
                kind=SourceKind.bank,
                title=frag.doc_title,
                date=frag.doc_date,
                page=frag.page,
            )
        return catalog

    def source_texts(self) -> dict[str, str]:
        texts = {self.event.source.source_id: f"{self.event.title}\n{self.event.text}"}
        for ev in self.news.values():
            texts[ev.source.source_id] = f"{ev.title}\n{ev.text}"
        for frag in self.bank.values():
            texts[frag.fragment_id] = frag.text
        return texts

    def profile_text(self) -> str:
        return "\n".join(f"{s.feature}: {s.value}" for s in self.signals)
