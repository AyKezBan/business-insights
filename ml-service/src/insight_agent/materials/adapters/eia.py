"""Read EIA Today in Energy HTML without following media, archive or RSS links."""
from datetime import UTC, date, datetime
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from insight_agent.materials.models import RawNews
from insight_agent.materials.registry import get_spec
from insight_agent.materials.sources import DiscoveredArticle, FetchBatch, SourceSpec
from insight_agent.materials.transport import ControlledTransport, TransportError, check_url


_TRANSLATION = "Original English text from the U.S. Energy Information Administration; no translation."
_BLOCKS = {"p", "div", "section", "article", "blockquote", "ul", "ol", "li", "h2", "h3", "h4", "table", "thead", "tbody", "tfoot"}
_MONTHS = {name: number for number, name in enumerate(
    "January February March April May June July August September October November December".split(), 1)}


def _article_identity(url: str) -> str:
    try:
        check_url(url, get_spec("eia_today"))
        parts = urlsplit(url)
        identifiers = parse_qs(parts.query, keep_blank_values=True).get("id", [])
        if (parts.path != "/todayinenergy/detail.php" or len(identifiers) != 1
                or not re.fullmatch(r"[1-9][0-9]*", identifiers[0])):
            raise ValueError("Missing or ambiguous article ID")
        return identifiers[0]
    except (TransportError, ValueError) as exc:
        raise ValueError("Invalid EIA article URL") from exc


def _check_index(url: str) -> None:
    check_url(url, get_spec("eia_today"))
    parts = urlsplit(url)
    if parts.path != urlsplit(get_spec("eia_today").discovery_url).path or parts.query:
        raise TransportError("EIA recent index identity changed", status="blocked")


def _remove_service_content(soup: Tag) -> None:
    for node in soup.select("script, style, nav, footer, header, form, noscript, iframe, svg, figure, img"):
        node.decompose()
    for node in list(soup.find_all(True)):
        if node.parent is None:
            continue
        style = node.get("style", "")
        if (node.has_attr("hidden") or str(node.get("aria-hidden", "")).lower() == "true"
                or re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", style, re.I)):
            node.decompose()


def _article_text(node: Tag) -> str:
    """Walk once: block separators preserve tables without splitting inline words."""
    def render(current) -> str:
        if isinstance(current, Comment):
            return ""
        if isinstance(current, NavigableString):
            return re.sub(r"\s+", " ", str(current))
        if not isinstance(current, Tag):
            return ""
        if current.name == "br":
            return "\n"
        if current.name == "tr":
            cells = [render(cell).strip() for cell in current.find_all(["th", "td"], recursive=False)]
            return "\n" + " | ".join(cells) + "\n"
        value = "".join(render(child) for child in current.children)
        return "\n" + value + "\n" if current.name in _BLOCKS else value

    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in render(node).splitlines()]
    return "\n".join(line for line in lines if line)


def parse_eia_index(html: bytes, url: str) -> list[DiscoveredArticle]:
    _check_index(url)
    soup = BeautifulSoup(html.decode("utf-8-sig"), "html.parser")
    _remove_service_content(soup)
    result, seen = [], set()
    for card in soup.select(".tie-article"):
        link = card.select_one("h1 a[href]")
        if link is None:
            continue
        article_url = urljoin(url, link["href"])
        try:
            identity = _article_identity(article_url)
        except ValueError:
            continue
        title = _article_text(link).replace("\n", " ")
        if not title or identity in seen:
            continue
        seen.add(identity)
        publication = card.select_one(".date")
        result.append(DiscoveredArticle(url=urlsplit(article_url)._replace(fragment="").geturl(),
            title=title, raw_date=publication.get_text(" ", strip=True) if publication else None))
    return result


def parse_eia_article(html: bytes, url: str) -> RawNews:
    _article_identity(url)
    spec = get_spec("eia_today")
    raw_body = html.decode("utf-8-sig")
    soup = BeautifulSoup(raw_body, "html.parser")
    _remove_service_content(soup)
    articles = soup.select(".tie-article")
    if len(articles) != 1:
        raise ValueError("Expected one EIA primary article body")
    article = articles[0]
    heading, publication = article.select_one("h1"), article.select_one(".date")
    if heading is None or not heading.get_text(strip=True) or publication is None:
        raise ValueError("EIA article title or publication date is missing")
    title = _article_text(heading).replace("\n", " ")
    raw_date = publication.get_text(" ", strip=True)
    match = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2}),\s+(\d{4})", raw_date)
    if match is None or match[1] not in _MONTHS:
        raise ValueError("Invalid EIA publication date")
    published_date = date(int(match[3]), _MONTHS[match[1]], int(match[2]))
    tag_text = " ".join(node.get_text(" ", strip=True) for node in article.select(".tags"))
    for node in article.select("h1, .date, .article-type, .source, .tags, .more-recent-articles, .share, .social, .contributor"):
        node.decompose()
    for node in list(article.find_all("p")):
        if node.parent is not None and re.match(r"(?:Principal contributors?|Contributors?|Tags)\s*:", node.get_text(" ", strip=True), re.I):
            node.decompose()
    text = _article_text(article)
    if not text:
        raise ValueError("EIA primary article body is empty")
    topic_text = " ".join((title, text, tag_text)).casefold()
    topics = []
    if re.search(r"\b(?:oil|petroleum|natural gas|coal|crude)\b", topic_text):
        topics.append("commodity_prices")
    if re.search(r"\b(?:refiner\w*|refining|fuel|gasoline|diesel|electricity)\b", topic_text):
        topics.append("input_prices")
    if re.search(r"\b(?:energy|electricity|refiner\w*|refining|fuel|oil|petroleum|natural gas|coal|crude|gasoline|diesel)\b", topic_text):
        topics.append("energy")
    if re.search(r"\b(?:(?:refinery|refining|production|capacity)\s+utilization|(?:production|refinery|refining)\s+capacity)\b", topic_text):
        topics.append("production_capacity")
    # A page canonical may name the site home or a different article. The fetched
    # article ID is authoritative; metadata never replaces that verified identity.
    article_url = urlsplit(url)._replace(fragment="").geturl()
    return RawNews(url=article_url, discovered_url=article_url, title=title, text=text,
        original_text=text, raw_body=raw_body, retrieved_at=datetime.now(UTC),
        published_date=published_date, published_precision="date", raw_publication=raw_date,
        publisher_id=spec.publisher_id, adapter_id=spec.source_id, language=spec.language,
        material_type=spec.material_type, completeness="full", parser_version=spec.parser_version,
        rights_url=spec.rights_url, translation_attribution=_TRANSLATION, topics=topics)


class EiaTodaySource:
    def __init__(self, transport: ControlledTransport, spec: SourceSpec):
        if spec.source_id != "eia_today":
            raise ValueError("Expected the EIA registry source")
        _check_index(spec.discovery_url)
        self.transport, self.spec = transport, spec

    def collect(self, limit: int) -> FetchBatch:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("EIA article limit must be between 1 and 100")
        now = datetime.now(UTC)
        batch = FetchBatch(source_id=self.spec.source_id, status="empty", checked_at=now)
        try:
            response = self.transport.get(self.spec.discovery_url, spec=self.spec)
            _check_index(response.url)
            batch.etag, batch.last_modified = response.headers.get("etag"), response.headers.get("last-modified")
            if response.status_code == 304:
                batch.status = "unchanged"
                return batch
            articles = parse_eia_index(response.body, response.url)
        except (TransportError, ValueError) as exc:
            batch.status = exc.status if isinstance(exc, TransportError) else "failed"
            batch.reason = str(exc)
            return batch
        failures, unchanged = [], 0
        for article in articles[:limit]:
            batch.attempted += 1
            try:
                response = self.transport.get(article.url, spec=self.spec)
                try:
                    same_article = _article_identity(response.url) == _article_identity(article.url)
                except ValueError:
                    same_article = False
                if not same_article:
                    raise TransportError("EIA article identity changed during redirect", status="blocked")
                if response.status_code == 304:
                    unchanged += 1
                    continue
                record = parse_eia_article(response.body, response.url)
                record.discovered_url = article.url
                record.retrieved_at = now
                record.parser_version = self.spec.parser_version
                batch.records.append(record)
                batch.succeeded += 1
            except (TransportError, ValueError) as exc:
                batch.rejected += 1
                batch.item_errors.append(f"{article.url}: {exc}")
                failures.append(exc.status if isinstance(exc, TransportError) else "failed")
        if batch.records:
            batch.status = "partial" if failures else "ok"
            batch.last_success_at = now
        elif failures:
            batch.status = "blocked" if all(status == "blocked" for status in failures) else "failed"
            batch.reason = "No EIA articles could be collected"
        elif unchanged:
            batch.status = "unchanged"
        return batch
