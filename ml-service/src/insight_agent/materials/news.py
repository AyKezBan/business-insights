"""Public primary-source RSS ingestion. No model calls, cookies or arbitrary URLs."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
from urllib.parse import urljoin, urlsplit
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
        if raw_date := item.findtext("pubDate"):
            try:
                candidate = parsedate_to_datetime(raw_date)
                if candidate.tzinfo is not None:
                    published = candidate.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                pass
        result.append({"url": url, "title": title, "published_at": published})
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
    def __init__(self, client: httpx.Client, feed_url: str = FEED_URL):
        _validate_source_url(feed_url)
        self.client, self.feed_url = client, feed_url

    def _read(self, url: str) -> bytes:
        for _ in range(5):
            _validate_source_url(url)
            with self.client.stream("GET", url, timeout=10, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    _validate_source_url(url)
                    continue
                response.raise_for_status()
                chunks, total = [], 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise ValueError("Source size limit exceeded")
                    chunks.append(chunk)
                return b"".join(chunks)
        raise ValueError("Too many source redirects")

    def fetch(self, limit: int) -> list[RawNews]:
        if not 1 <= limit <= 100:
            raise ValueError("News limit must be between 1 and 100")
        fetched_at = datetime.now(timezone.utc)
        result, seen = [], set()
        for item in parse_rss(self._read(self.feed_url)):
            if item["url"] in seen:
                continue
            seen.add(item["url"])
            body = self._read(item["url"])
            result.append(RawNews(**item, text=extract_cbr_article(body.decode("utf-8-sig")), retrieved_at=fetched_at))
            if len(result) == limit:
                break
        return result


def read_saved_news(path: Path) -> list[RawNews]:
    text = path.read_text("utf-8")
    records = json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]
    return [RawNews.model_validate(record) for record in records]


def classify_topics(text: str) -> list[str]:
    rules = {"cost_of_debt": ("ставк", "кредит", "rate"), "fx": ("валют", "курс", "рубл"),
        "input_prices": ("бензин", "топлив", "цен"), "domestic_demand": ("спрос", "потреблен", "розниц"),
        "labor_costs": ("зарплат", "труда"), "regulation": ("регулир", "норматив", "тариф"),
        "counterparty_risk": ("рейтинг", "банкрот"), "commodity_prices": ("нефть", "золот", "сталь")}
    lowered = text.casefold()
    return [topic for topic, terms in rules.items() if any(term in lowered for term in terms)]
