from datetime import date, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from insight_agent.schemas import BankFragment
from insight_agent.tools.search import BM25


class BankSearchArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300)
    top_k: int = Field(default=4, ge=1, le=8)
    not_before: date | None = None
    not_after: date | None = None
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

    def search(self, args: BankSearchArgs, *, as_of: date | None = None) -> list[ScoredFragment]:
        if not self.fragments:
            return []
        upper = min(as_of, args.not_after) if as_of and args.not_after else as_of or args.not_after
        lower = args.not_before or (as_of - timedelta(days=180) if as_of else None)
        superseded = {old for f in self.fragments
            if f.review_status == "verified" and f.doc_date is not None
            and (as_of is None or f.doc_date <= as_of) for old in f.supersedes}
        ranked = sorted(zip(self.fragments, self._bm25.scores(args.query)), key=lambda x: (-x[1], x[0].fragment_id))
        out = []
        for frag, score in ranked:
            if score <= 0 or score < args.min_score:
                break
            if frag.review_status != "verified" or frag.doc_date is None or frag.fragment_id in superseded:
                continue
            if lower and frag.doc_date < lower or upper and frag.doc_date > upper:
                continue
            if frag.statement_type == "forecast" and frag.valid_until is None:
                continue
            if as_of and frag.valid_until and frag.valid_until < as_of:
                continue
            out.append(ScoredFragment(fragment=frag, score=round(score, 3)))
            if len(out) == args.top_k:
                break
        return out


def summarize(result: list[ScoredFragment]) -> dict:
    return {"fragments": [{"id": r.fragment.fragment_id, "score": r.score,
        "content_sha256": r.fragment.content_sha256, "doc_id": r.fragment.doc_id,
        "page": r.fragment.page, "doc_date": str(r.fragment.doc_date),
        "review_status": r.fragment.review_status, "statement_type": r.fragment.statement_type} for r in result]}
