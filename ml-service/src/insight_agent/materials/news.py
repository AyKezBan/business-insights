"""Public primary-source RSS ingestion. No model calls, cookies or arbitrary URLs."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
import httpx

from insight_agent.materials.models import RawNews

FEED_URL = "https://www.cbr.ru/rss/RssPress"
MAX_BYTES = 2 * 1024 * 1024


def parse_rss(xml: bytes) -> list[dict]:
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("RSS entities/DTD are not allowed")
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("Invalid RSS XML") from exc
    if root.tag != "rss" or root.find("channel") is None:
        raise ValueError("Expected an RSS channel")
    result = []
    for item in root.findall("./channel/item"):
        url, title = (item.findtext("link") or "").strip(), (item.findtext("title") or "").strip()
        if not url or not title:
            continue
        published = None
        published_date = None
        source_offset = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                candidate = parsedate_to_datetime(raw_date)
                if candidate.tzinfo is not None:
                    published = candidate.astimezone(timezone.utc)
                    published_date = candidate.date()
                    source_offset = int(candidate.utcoffset().total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
        result.append({"url": url, "title": title, "published_at": published,
                       "published_date": published_date, "source_utc_offset_seconds": source_offset,
                       "raw_publication": raw_date,
                       "timezone_provenance": "explicit_offset" if published else "unknown"})
    return result


def extract_cbr_article(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.select("script,style,nav,footer,header,form,noscript"):
        tag.decompose()
    # .landing-text confirmed against a real CBR press release during implementation.
    body = soup.select_one(".landing-text, [itemprop='articleBody'], main article, article")
    if body is None:
        raise ValueError("Primary article body is missing")
    text = body.get_text("\n", strip=True)
    if not text:
        raise ValueError("Primary article body is empty")
    return text


def _validate_source_url(url: str) -> None:
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.hostname not in {"cbr.ru", "www.cbr.ru"}
            or parts.username or parts.password or parts.port not in (None, 443)):
        raise ValueError("URL outside CBR source allowlist")


class CbrRssSource:
    def __init__(self, client: httpx.Client, feed_url: str = FEED_URL, *, transport=None):
        _validate_source_url(feed_url)
        self.client, self.feed_url = client, feed_url
        from insight_agent.materials.transport import ControlledTransport
        from insight_agent.materials.registry import get_spec
        self.transport = transport or ControlledTransport(client)
        self.spec = get_spec('cbr_press')

    def _request(self, url: str):
        from insight_agent.materials.transport import TransportError
        try:
            return self.transport.get(url, spec=self.spec)
        except TransportError as exc:
            if isinstance(exc.__cause__, httpx.HTTPError):
                raise exc.__cause__
            raise ValueError(str(exc)) from exc

    def _read(self, url: str) -> bytes:
        return self._request(url).body

    def fetch(self, limit: int) -> list[RawNews]:
        if not 1 <= limit <= 100:
            raise ValueError("News limit must be between 1 and 100")
        fetched_at = datetime.now(timezone.utc)
        result, seen = [], set()
        for item in parse_rss(self._read(self.feed_url)):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            response = self._request(item["url"])
            body = response.body.decode("utf-8-sig")
            result.append(RawNews(**{**item, "url": response.url}, discovered_url=item["url"],
                                  text=extract_cbr_article(body), raw_body=body, retrieved_at=fetched_at))
            if len(result) == limit:
                break
        return result


def read_saved_news(path: Path) -> list[RawNews]:
    text = path.read_text("utf-8")
    records = json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    return [RawNews.model_validate(record) for record in records]


def classify_topics(text: str) -> list[str]:
    import re

    rules = {
        # A rate needs a financial qualifier; tax and utilization rates are not debt.
        "cost_of_debt": (
            r"\b(?:ключев\w*|процентн\w*)\s+ставк\w*|\bкредит\w*"
            r"|\b(?:interest|key|policy|lending|loan|credit|borrowing)[ -]+rates?\b"
            r"|\b(?:borrowing|credit|financing)[ -]+costs?\b"
        ),
        "fx": r"валют|курс|рубл",
        "input_prices": r"бензин|топлив|цен|\b(?:fuel|electricity|input|energy|gas|oil)\s+(?:costs?|prices?)\b",
        "domestic_demand": r"спрос|потреблен|розниц",
        "labor_costs": r"зарплат|труда",
        "regulation": r"регулир|норматив|тариф",
        "counterparty_risk": r"рейтинг|банкрот",
        "commodity_prices": r"нефть|золот|сталь",
        "tax": r"налог|\b(?:ндс|ндфл|tax(?:es|ation)?|vat)\b",
        "reporting": r"отчетност|декларац|\b(?:reporting|filing|tax returns?)\b",
        "deadlines": r"срок\w*\s+(?:уплат|подач|представлен|сдач)|\b(?:deadlines?|due dates?)\b",
        "energy": r"энерг|электр|нефтепереработ|топлив|бензин|\b(?:energy|electricity|refiner(?:y|ies)|fuel|natural gas|oil)\b",
        "production_capacity": (
            r"производственн\w*\s+мощност|загрузк\w*\s+(?:производственн\w*\s+)?мощност"
            r"|\b(?:production capacity|refinery utilization|capacity utilization)\b"
        ),
    }
    lowered = " ".join(text.casefold().replace("ё", "е").split())
    return [topic for topic, pattern in rules.items() if re.search(pattern, lowered)]
