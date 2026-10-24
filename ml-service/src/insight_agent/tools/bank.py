from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from insight_agent.schemas import BankFragment
from insight_agent.tools.search import BM25


class BankSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=4, ge=1, le=8)
    not_before: date | None = None
    min_score: float = Field(default=1.0, ge=0)


class ScoredFragment(BaseModel):
    fragment: BankFragment
    score: float


class BankIndex:
    def __init__(self, fragments: list[BankFragment]):
        self.fragments = fragments
        self._bm25 = BM25([f"{f.doc_title} {' '.join(f.topics)} {f.text}" for f in fragments])

    @classmethod
    def from_jsonl(cls, path: Path) -> "BankIndex":
        if not path.exists():
            return cls([])
        lines = path.read_text("utf-8").splitlines()
        return cls([BankFragment.model_validate_json(line) for line in lines if line.strip()])

    def search(self, args: BankSearchArgs) -> list[ScoredFragment]:
        if not self.fragments:
            return []
        ranked = sorted(zip(self.fragments, self._bm25.scores(args.query)), key=lambda x: x[1], reverse=True)
        out = []
        for frag, score in ranked:
            if score < args.min_score:
                break
            if args.not_before and frag.doc_date and frag.doc_date < args.not_before:
                continue
            out.append(ScoredFragment(fragment=frag, score=round(score, 3)))
            if len(out) == args.top_k:
                break
        return out


def summarize(result: list[ScoredFragment]) -> dict:
    return {"fragments": [{"id": r.fragment.fragment_id, "score": r.score} for r in result]}
