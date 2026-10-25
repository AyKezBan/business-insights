"""Поиск по открытым официальным источникам: показатели Банка России и его пресс-релизы."""
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Callable

from insight_agent.materials.news import extract_cbr_article, parse_rss
from insight_agent.research.fetch import OpenSourceError, SafeFetcher
from insight_agent.research.indicators import (
    FX_DAILY_URL,
    KEY_RATE_URL,
    OpenDocument,
    ParseError,
    fmt_date,
    fx_document,
    key_rate_document,
    parse_fx_daily,
    parse_key_rate,
)
from insight_agent.research.plan import ResearchPlan

PRESS_FEED_URL = "https://www.cbr.ru/rss/RssPress"
KEY_RATE_WINDOW_DAYS = 180
FX_WINDOW_DAYS = 30
LIVE_TTL, FEED_TTL, ARCHIVE_TTL = 900, 1800, 30 * 24 * 3600


class TtlCache:
    """Кеш ответов источников: на диске, если задана папка, иначе в памяти процесса."""

    def __init__(self, directory: Path | None = None, clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.directory, self.clock = directory, clock
        self._memory: dict[str, tuple[datetime, bytes]] = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(url: str, params: dict | None) -> str:
        return hashlib.sha256(json.dumps([url, sorted((params or {}).items())]).encode()).hexdigest()

    def get(self, key: str, ttl: int) -> bytes | None:
        now = self.clock()
        if self.directory:
            path = self.directory / key
            if path.exists() and now - datetime.fromtimestamp(path.stat().st_mtime, UTC) < timedelta(seconds=ttl):
                return path.read_bytes()
            return None
        hit = self._memory.get(key)
        return hit[1] if hit and now - hit[0] < timedelta(seconds=ttl) else None

    def set(self, key: str, data: bytes) -> None:
        if self.directory:
            tmp = self.directory / f"{key}.tmp"
            tmp.write_bytes(data)
            tmp.replace(self.directory / key)
        else:
            self._memory[key] = (self.clock(), data)


@dataclass
class ResearchResult:
    docs: list[OpenDocument] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class OpenResearch:
    def __init__(self, fetcher: SafeFetcher, cache: TtlCache | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.fetcher, self.cache, self.clock = fetcher, cache or TtlCache(), clock

    def _get(self, url: str, params: dict | None, ttl: int) -> bytes:
        key = self.cache.key(url, params)
        if (hit := self.cache.get(key, ttl)) is not None:
            return hit
        data = self.fetcher.get(url, params)
        self.cache.set(key, data)
        return data

    def _ttl(self, as_of: date) -> int:
        return LIVE_TTL if as_of >= self.clock().date() else ARCHIVE_TTL

    def search(self, plan: ResearchPlan, *, as_of: date, top_k: int = 3) -> ResearchResult:
        result = ResearchResult()
        retrieved = self.clock()
        steps = []
        if plan.key_rate:
            steps.append(("ключевая ставка", lambda: [self._key_rate(as_of, retrieved)]))
        if plan.currencies:
            steps.append(("курсы валют", lambda: self._fx(plan.currencies, as_of, retrieved)))
        if plan.keywords:
            steps.append(("пресс-релизы", lambda: self._press(plan.keywords, as_of, top_k, retrieved, result)))
        for label, run in steps:
            try:
                result.docs += run()
            except (OpenSourceError, ParseError, ValueError) as exc:
                result.errors.append(f"{label}: {exc}")
        result.docs = list({d.source_id: d for d in result.docs}.values())
        return result

    def _key_rate(self, as_of: date, retrieved: datetime) -> OpenDocument:
        params = {"UniDbQuery.Posted": "True", "UniDbQuery.From": fmt_date(as_of - timedelta(days=KEY_RATE_WINDOW_DAYS)),
                  "UniDbQuery.To": fmt_date(as_of)}
        return key_rate_document(parse_key_rate(self._get(KEY_RATE_URL, params, self._ttl(as_of))), as_of, retrieved)

    def _fx(self, codes: list[str], as_of: date, retrieved: datetime) -> list[OpenDocument]:
        ttl = self._ttl(as_of)
        now = parse_fx_daily(self._get(FX_DAILY_URL, {"date_req": as_of.strftime("%d/%m/%Y")}, ttl))
        before = parse_fx_daily(self._get(FX_DAILY_URL, {"date_req": (as_of - timedelta(days=FX_WINDOW_DAYS)).strftime("%d/%m/%Y")}, ttl))
        docs = []
        for code in codes:
            try:
                docs.append(fx_document(code, now, before, retrieved))
            except ParseError:
                continue  # валюты нет в официальном списке на эту дату: ничего не придумываем
        return docs

    def _press(self, keywords: list[str], as_of: date, top_k: int, retrieved: datetime, result: ResearchResult) -> list[OpenDocument]:
        limit = datetime.combine(as_of, datetime.max.time(), UTC)
        scored = []
        for item in parse_rss(self._get(PRESS_FEED_URL, None, FEED_TTL)):
            published = item["published_at"]
            if published is None or published > limit:
                continue
            words = re.findall(r"[а-яёa-z0-9]+", item["title"].casefold())
            # основа должна совпасть с началом слова: «курс» не находит «конкурс»
            score = sum(1 for k in keywords if any(w.startswith(k) for w in words))
            if score:
                scored.append((score, published, item))
        scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
        docs = []
        for _, published, item in scored[:top_k]:
            try:
                text = extract_cbr_article(self._get(item["url"], None, ARCHIVE_TTL).decode("utf-8-sig"))
            except (OpenSourceError, ValueError) as exc:
                result.errors.append(f"пресс-релиз {item['url']}: {exc}")
                continue
            digest = hashlib.sha256(item["url"].encode()).hexdigest()[:12]
            docs.append(OpenDocument(source_id=f"cbr_press_{digest}", title=item["title"], text=text, url=item["url"],
                                     published=published, retrieved_at=retrieved, kind="press"))
        return docs
