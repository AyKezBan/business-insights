from pathlib import Path

from pydantic import BaseModel, Field

from insight_agent.schemas import NewsEvent
from insight_agent.tools.search import BM25


class NewsSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=3, ge=1, le=6)
    exclude_event_id: str | None = None
    min_score: float = Field(default=1.0, ge=0)


class NewsStore:
    def __init__(self, events: list[NewsEvent]):
        self.events = events
        self._bm25 = BM25([f"{e.title} {e.text}" for e in events])

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
        return [
            e for e, s in ranked if s >= args.min_score and e.event_id != args.exclude_event_id
        ][: args.top_k]


def summarize(result: list[NewsEvent]) -> dict:
    return {"events": [e.event_id for e in result]}
