from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from insight_agent.schemas import NewsEvent
from insight_agent.materials.models import SourceVersion
from insight_agent.materials.artifacts import digest
from insight_agent.tools.search import BM25


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class NewsSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=3, ge=1, le=6)
    exclude_event_id: str | None = None
    not_after: datetime | None = None
    min_score: float = Field(default=1.0, ge=0)


class NewsStore:
    def __init__(self, events: list[NewsEvent], source_versions: list[SourceVersion] | None = None):
        self.events = events
        self._source_texts = {}
        for version in source_versions or []:
            if digest(version.text.encode()) != version.content_sha256:
                raise ValueError("Source version hash mismatch")
            self._source_texts[version.source_id] = version.text
        for event in events:
            self._source_texts.setdefault(event.source.source_id, event.text)
        self._bm25 = BM25([f"{e.title} {e.text}" for e in events])

    def resolve_source_text(self, source_id: str) -> str | None:
        return self._source_texts.get(source_id)

    def register_source_text(self, source_id: str, text: str) -> None:
        """Текст документа, найденного в открытом источнике во время запуска (в поиск по корпусу не попадает)."""
        self._source_texts[source_id] = text

    @classmethod
    def from_jsonl(cls, path: Path) -> "NewsStore":
        if not path.exists():
            return cls([])
        lines = path.read_text("utf-8").splitlines()
        return cls([NewsEvent.model_validate_json(line) for line in lines if line.strip()])

    def search(self, args: NewsSearchArgs) -> list[NewsEvent]:
        if not self.events:
            return []
        ranked = sorted(zip(self.events, self._bm25.scores(args.query)), key=lambda x: x[1], reverse=True)
        limit = _aware(args.not_after) if args.not_after else None
        return [
            e for e, s in ranked
            if s >= args.min_score and e.event_id != args.exclude_event_id
            and (limit is None or (e.published_at is not None and _aware(e.published_at) <= limit))
        ][: args.top_k]


def summarize(result: list[NewsEvent]) -> dict:
    return {"events": [e.event_id for e in result]}
