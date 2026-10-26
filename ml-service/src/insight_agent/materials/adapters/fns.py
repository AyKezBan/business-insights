"""Reviewed FNS HTML news only, with source text and unknown timezones intact."""
from datetime import UTC, date, datetime
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from insight_agent.materials.models import RawNews
from insight_agent.materials.registry import get_spec
from insight_agent.materials.sources import DiscoveredArticle, FetchBatch, SourceSpec
from insight_agent.materials.transport import ControlledTransport, TransportError, check_url

_ARTICLE_PATH = re.compile(r'/rn77/news/activities_fts/[0-9]+/?\Z')
_BLOCKS = frozenset({'address', 'article', 'blockquote', 'div', 'dl', 'dt', 'dd',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ol', 'p', 'pre', 'section',
    'table', 'thead', 'tbody', 'tfoot', 'tr', 'ul'})
_HIDDEN_STYLE = re.compile(r'(?:^|;)\s*(?:display\s*:\s*none|visibility\s*:\s*hidden)\s*(?:!important\s*)?(?:;|$)', re.I)


def _article_url(url: str) -> str:
    check_url(url, get_spec('fns_news'))
    if not _ARTICLE_PATH.fullmatch(urlsplit(url).path):
        raise ValueError('FNS article identity is missing')
    # Fragment anchors are navigation; every query parameter remains source identity.
    return urldefrag(url)[0]


def _identity(url: str) -> tuple[str, str, str]:
    parts = urlsplit(_article_url(url))
    return parts.netloc.casefold(), parts.path.rstrip('/'), parts.query


def _clean(soup: BeautifulSoup) -> None:
    for node in list(soup.find_all(True)):
        # A parent may already have removed this descendant.
        if node.attrs is None:
            continue
        site_form = node.name == 'form' and node.get('id') == 'MainForm'
        if (node.name in {'script', 'style', 'nav', 'footer', 'header',
                'noscript', 'template', 'button', 'input', 'select', 'textarea',
                'iframe', 'object', 'embed', 'svg', 'img'}
                or (node.name == 'form' and not site_form)
                or 'hidden' in node.attrs
                or str(node.get('aria-hidden', '')).casefold() == 'true'
                or _HIDDEN_STYLE.search(str(node.get('style', '')))
                or 'share' in node.get('class', [])):
            node.decompose()
        elif site_form:
            # The FNS ASP.NET page wraps its news content in MainForm. Only
            # discard that wrapper; feedback forms and controls still go above.
            node.unwrap()


def _text(node: Tag) -> str:
    """Visit each text node once; insert boundaries only at HTML block/cell edges."""
    chunks: list[str] = []

    def visit(child):
        if isinstance(child, Comment):
            return
        if isinstance(child, NavigableString):
            chunks.append(re.sub(r'\s+', ' ', str(child)))
            return
        if not isinstance(child, Tag):
            return
        if child.name == 'tr':
            cells = child.find_all(['th', 'td'], recursive=False)
            # Paragraphs inside a cell do not create extra table rows. Keep empty
            # cells, including edge cells, so values cannot move under a header.
            chunks.append('\n' + '\t'.join(' '.join(_text(cell).splitlines()) for cell in cells) + '\n')
            return
        if child.name in _BLOCKS or child.name == 'br':
            chunks.append('\n')
        for descendant in child.children:
            visit(descendant)
        if child.name in _BLOCKS:
            chunks.append('\n')

    visit(node)
    lines = []
    for line in ''.join(chunks).splitlines():
        line = '\t'.join(re.sub(r' +', ' ', cell).strip() for cell in line.split('\t'))
        if line.strip():
            lines.append(line)
    return '\n'.join(lines)


def _publication(raw: str | None) -> date | None:
    match = re.search(r'(?<!\d)(\d{2})\.(\d{2})\.(\d{4})(?!\d)', raw or '')
    if match:
        day, month, year = map(int, match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            pass
    return None


def parse_fns_index(html: bytes, base_url: str) -> list[DiscoveredArticle]:
    spec = get_spec('fns_news')
    check_url(base_url, spec)
    if urlsplit(base_url).path.rstrip('/') != urlsplit(spec.discovery_url).path.rstrip('/'):
        raise ValueError('FNS discovery is not the reviewed news rubric')
    soup = BeautifulSoup(html, 'html.parser')
    _clean(soup)
    articles, seen = [], set()
    for link in soup.select('.news-block__name a[href]'):
        try:
            url = _article_url(urljoin(base_url, link['href']))
        except (ValueError, TransportError):
            continue
        title = _text(link)
        if url in seen or not title:
            continue
        item = link.find_parent(class_='news-block__item')
        time = item.select_one('.news__time') if item else None
        articles.append(DiscoveredArticle(url=url, title=title, raw_date=_text(time) if time else None))
        seen.add(url)
    return articles


def parse_fns_article(html: bytes, url: str) -> RawNews:
    url = _article_url(url)
    # FNS publishes UTF-8 HTML. Strict decoding keeps corrupted source bytes from
    # silently becoming replacement characters in apparently complete evidence.
    raw_body = html.decode('utf-8-sig')
    soup = BeautifulSoup(raw_body, 'html.parser')
    _clean(soup)
    heading = soup.select_one('h1')
    title = _text(heading) if heading else ''
    if not title:
        raise ValueError('FNS article title is missing')
    text, raw_date = '', None
    for body in soup.select('div.text_block'):
        time = body.select_one('.single-news__time')
        candidate_date = re.sub(r'^Дата публикации\s*:\s*', '', _text(time), flags=re.I) if time else None
        for node in body.select('.single-news__time'):
            node.decompose()
        candidate_text = _text(body)
        if candidate_text:
            text, raw_date = candidate_text, candidate_date
            break
    if not text:
        raise ValueError('FNS article body is missing or empty')
    published_date = _publication(raw_date)
    lowered = (title + '\n' + text).casefold()
    topics = []
    for topic, terms in {
        'tax': ('налог', 'ндс', 'ндфл', 'страховы', 'страховых'),
        'deadlines': ('срок', 'не позднее'),
        'reporting': ('отчет', 'отчёт', 'декларац', 'уведомлен'),
    }.items():
        if any(term in lowered for term in terms):
            topics.append(topic)
    spec = get_spec('fns_news')
    return RawNews(url=url, title=title, text=text, original_text=text, raw_body=raw_body,
        retrieved_at=datetime.now(UTC), published_date=published_date,
        published_precision='date' if published_date else 'unknown', raw_publication=raw_date,
        publisher_id=spec.publisher_id, adapter_id=spec.source_id, discovered_url=url,
        language=spec.language, material_type=spec.material_type, parser_version=spec.parser_version,
        rights_url=spec.rights_url, topics=topics)


class FnsNewsSource:
    def __init__(self, transport: ControlledTransport, spec: SourceSpec):
        self.transport, self.spec = transport, spec

    def collect(self, limit: int) -> FetchBatch:
        if not 1 <= limit <= 100:
            raise ValueError('News limit must be between 1 and 100')
        checked_at = datetime.now(UTC)
        batch = FetchBatch(source_id=self.spec.source_id, status='empty', checked_at=checked_at)
        try:
            response = self.transport.get(self.spec.discovery_url, spec=self.spec)
            batch.etag = response.headers.get('etag')
            batch.last_modified = response.headers.get('last-modified')
            if response.status_code == 304:
                batch.status = 'unchanged'
                return batch
            articles = parse_fns_index(response.body, response.url)
        except (TransportError, ValueError) as exc:
            batch.status = exc.status if isinstance(exc, TransportError) else 'failed'
            batch.reason = str(exc)
            batch.item_errors = [str(exc)]
            return batch
        if not articles:
            batch.reason = 'FNS rubric contains no eligible articles'
            return batch
        blocked, unchanged = False, 0
        for article in articles[:limit]:
            batch.attempted += 1
            try:
                response = self.transport.get(article.url, spec=self.spec)
                if _identity(response.url) != _identity(article.url):
                    raise ValueError('FNS redirect changed article identity')
                if response.status_code == 304:
                    unchanged += 1
                    continue
                record = parse_fns_article(response.body, response.url)
                record.discovered_url = article.url
                record.publisher_id = self.spec.publisher_id
                record.adapter_id = self.spec.source_id
                record.language = self.spec.language
                record.material_type = self.spec.material_type
                record.parser_version = self.spec.parser_version
                record.rights_url = self.spec.rights_url
                batch.records.append(record)
            except (TransportError, ValueError) as exc:
                blocked |= isinstance(exc, TransportError) and exc.status == 'blocked'
                batch.rejected += 1
                batch.item_errors.append(f'{article.url}: {exc}')
        batch.succeeded = len(batch.records)
        if batch.succeeded:
            batch.status = 'partial' if batch.rejected or unchanged else 'ok'
            batch.last_success_at = datetime.now(UTC)
        elif batch.rejected:
            batch.status = 'blocked' if blocked else 'failed'
        else:
            batch.status = 'unchanged'
        if batch.item_errors:
            batch.reason = 'One or more FNS articles could not be collected'
        return batch
