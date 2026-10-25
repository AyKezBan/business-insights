from datetime import date, timedelta
from pathlib import Path
import re

from pydantic import BaseModel, Field

from insight_agent.schemas import BankFragment
from insight_agent.tools.search import BM25, tokenize

# These words/year labels alone identify neither a bank topic nor a relevant statement.
_GENERIC = set(tokenize("банк банки взгляд оценка аналитика анализ обзор отчет статистика данные риск риски рынок рынки прогноз уровень динамика фактор ситуация текущий год квартал месяц компания ООО ПАО АО РФ"))

# Shared words such as rate, demand and financial results cannot establish these
# distinct subjects. Require a matching concept in the actual evidence, not its
# document title or inherited broad topic labels. These are positive constraints:
# a reviewed statement about tax/refining/etc. remains searchable.
_RATING_CONCEPT = re.compile(r"\b(?:рейтинг\w*|rating\w*|кредитоспособност\w*|акра)\b", re.I)
_CONCEPTS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(?:налог\w*|ндс|tax\w*|vat)\b",
    r"\b(?:нефтепереработ\w*|нпз|refiner\w*)\b",
    r"\b(?:манипул\w*|фальсифик\w*|мошеннич\w*|искаж\w*|fraud\w*|falsif\w*|manipulat\w*)\b",
    r"\b(?:золот\w*|gold)\b",
)) + (_RATING_CONCEPT,)
_FORECAST_QUERY = re.compile(
    r"\b(?:прогноз\w*|forecast\w*|ожида(?:л[аи]?|лось|ется|ют|ть|ем|ете|ет)|expect(?:ed|s)?)\b", re.I
)
_WEEKLY = re.compile(r"\b(?:недел\w*|weekly|week)\b", re.I)
_SECURITY = re.compile(r"\bофз(?:-пд)?\s*(\d{5})\b", re.I)
_DAY_RANGE = re.compile(
    r"\b(\d{1,2})\s*(?:[-–—]|по)\s*(\d{1,2})\s+"
    r"(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)"
    r"(?:\s+(20\d{2}))?", re.I
)


def _query_aliases(query: str) -> str:
    """Use the same half-year notation as reports, preserving other query terms."""
    for ordinal, number in (("перв", "1"), ("втор", "2")):
        query = re.sub(rf"\b{ordinal}\w*\s+полугоди\w*\s+(20\d{{2}})(?:\s+года)?",
                       lambda m: number + "П" + m[1][-2:], query, flags=re.I)
    return re.sub(r"\bчастн\w*\s+инвестор\w*", "физлиц", query, flags=re.I)


def _matches_day_ranges(fragment: BankFragment, requested: list[tuple]) -> bool:
    if not requested:
        return True
    text = fragment.text + " " + (fragment.target_period or "")
    available = _DAY_RANGE.findall(text.lower())
    for start, end, month, year in requested:
        if not any(int(start) == int(a) and int(end) == int(b) and month == m
                   and (not year or year == (y or (str(fragment.doc_date.year) if fragment.doc_date else "")))
                   for a, b, m, y in available):
            return False
    return True


def _specific_terms(text: str) -> set[str]:
    return {term for term in tokenize(text) if term not in _GENERIC and any(c.isalpha() for c in term)}


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
        originals = [f"{f.doc_title} {' '.join(f.topics)} {f.text}" for f in fragments]
        texts = [text + " " + normalized if normalized != text else text
                 for text in originals for normalized in [_query_aliases(text)]]
        self._bm25 = BM25(texts)
        self._terms = {f.fragment_id: _specific_terms(text) for f, text in zip(fragments, texts)}

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
        query = _query_aliases(args.query)
        ranked = sorted(zip(self.fragments, self._bm25.scores(query)), key=lambda x: (-x[1], x[0].fragment_id))
        out = []
        query_terms = _specific_terms(query)
        required_concepts = [concept for concept in _CONCEPTS if concept.search(args.query)]
        # A rating outlook or a general request for bank views is not a request
        # for a time-bounded weekly macro forecast.
        forecast_requested = bool(_FORECAST_QUERY.search(args.query) and _WEEKLY.search(args.query)
                                  and not _RATING_CONCEPT.search(args.query))
        securities = _SECURITY.findall(args.query)
        day_ranges = _DAY_RANGE.findall(args.query.lower())
        for frag, score in ranked:
            if score <= 0 or score < args.min_score:
                break
            if frag.review_status != "verified" or frag.doc_date is None or frag.fragment_id in superseded:
                continue
            if not query_terms.intersection(self._terms[frag.fragment_id]):
                continue
            if any(not concept.search(frag.text) for concept in required_concepts):
                continue
            if forecast_requested:
                if frag.statement_type != "forecast":
                    continue
                period_text = frag.text + " " + (frag.target_period or "")
                week_range = any(1 <= int(end) - int(start) + 1 <= 7
                                 for start, end, _, _ in _DAY_RANGE.findall(period_text))
                if not (_WEEKLY.search(period_text) or week_range):
                    continue
            if any(not re.search(rf"\b{identifier}\b", frag.text) for identifier in securities):
                continue
            if not _matches_day_ranges(frag, day_ranges):
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
