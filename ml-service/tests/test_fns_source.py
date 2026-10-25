"""FNS HTML ingestion preserves evidence and never upgrades HTTP success to health."""
from datetime import date
from pathlib import Path

import httpx
import pytest

from http_fakes import fake_http_transport
from insight_agent.materials.registry import get_spec
from insight_agent.materials.transport import TransportError

FIXTURES = Path(__file__).parent / 'fixtures' / 'materials'
INDEX_URL = 'https://www.nalog.gov.ru/rn77/news/activities_fts/'
ARTICLE_URL = INDEX_URL + '16664106/'
SECOND_URL = INDEX_URL + '16664107/'


@pytest.fixture
def fns_article_html():
    return (FIXTURES / 'fns-article.html').read_bytes()


@pytest.fixture
def fns_index_html():
    return (FIXTURES / 'fns-index.html').read_bytes()


def parse_article(html, url=ARTICLE_URL):
    from insight_agent.materials.adapters.fns import parse_fns_article
    return parse_fns_article(html, url)


def parse_index(html, url=INDEX_URL):
    from insight_agent.materials.adapters.fns import parse_fns_index
    return parse_fns_index(html, url)


def make_source(handler):
    from insight_agent.materials.adapters.fns import FnsNewsSource
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return FnsNewsSource(fake_http_transport(client), get_spec('fns_news'))


def test_fns_preserves_unknown_timezone(fns_article_html):
    record = parse_article(fns_article_html)
    assert record.published_date == date(2026, 10, 23)
    assert record.published_at is None
    assert record.published_precision == 'date'
    assert record.raw_publication == '23.10.2026 09:30'
    assert record.source_timezone is None
    assert record.source_utc_offset_seconds is None
    assert record.timezone_provenance == 'unknown'
    assert 'Это архивная публикация' not in record.text


def test_fns_extraction_preserves_nested_prose_inline_words_and_table(fns_article_html):
    record = parse_article(fns_article_html)
    assert record.title == 'Срок уплаты налога и исключения'
    assert record.text == (
        'Организации должны перечислить авансовый платеж не позднее 28 октября.\n'
        'Исключение: если региональным законом уплата аванса не предусмотрена.\n'
        'Налоговая ставка составляет 2%. Платеж еще не произведен.\n'
        'Организации представляют уведомление об исчисленных суммах.\n'
        'Налог\tСтавка\tСрок\nИмущество\t2%\t28 октября'
    )
    assert record.original_text == record.text


def test_fns_retains_raw_body_and_attribution(fns_article_html):
    record = parse_article(fns_article_html)
    assert record.raw_body == fns_article_html.decode('utf-8')
    assert record.publisher_id == 'fns'
    assert record.adapter_id == 'fns_news'
    assert record.language == 'ru'
    assert record.material_type == 'news_article'
    assert record.completeness == 'full'
    assert record.rights_url == 'https://www.nalog.gov.ru/rn77/copyright/'
    assert record.parser_version == 'fns-news-v1'
    assert record.url == record.discovered_url == ARTICLE_URL
    assert record.retrieved_at.tzinfo is not None
    assert record.translation_attribution is None


def test_fns_tax_rate_and_future_deadline_stay_tax_evidence(fns_article_html):
    record = parse_article(fns_article_html)
    assert 'tax' in record.topics
    assert 'deadlines' in record.topics
    assert 'reporting' in record.topics
    assert 'cost_of_debt' not in record.topics
    assert 'не позднее 28 октября' in record.text
    assert 'Платеж еще не произведен.' in record.text


@pytest.mark.parametrize('canonical', [
    'https://www.nalog.gov.ru/', SECOND_URL,
    'https://nalog.gov.ru/rn77/news/activities_fts/16664106/',
    'https://untrusted.example/article', ARTICLE_URL + '?id=99',
])
def test_fns_canonical_cannot_replace_identity(fns_article_html, canonical):
    html = fns_article_html.replace(b'https://www.nalog.gov.ru/"', (canonical + '"').encode())
    url = ARTICLE_URL + '?id=42'
    assert parse_article(html, url).url == url


@pytest.mark.parametrize('url', [
    INDEX_URL, INDEX_URL + 'archive/', INDEX_URL + '16664106/image.jpg',
    'https://untrusted.example/rn77/news/activities_fts/16664106/',
    'https://www.nalog.gov.ru/rn77/rss_region/',
])
def test_fns_parser_rejects_non_article_identity(fns_article_html, url):
    with pytest.raises((ValueError, TransportError)):
        parse_article(fns_article_html, url)


@pytest.mark.parametrize('raw_date', ['31.02.2026 09:30', 'Дата не указана'])
def test_fns_invalid_date_remains_unknown_without_retrieval_fallback(fns_article_html, raw_date):
    record = parse_article(fns_article_html.replace(b'23.10.2026 09:30', raw_date.encode()))
    assert record.raw_publication == raw_date
    assert record.published_date is record.published_at is None
    assert record.published_precision == 'unknown'


def test_fns_skips_hidden_empty_body_and_selects_first_substantive_body(fns_article_html):
    html = fns_article_html.replace(b'<div class="text_block">',
        b'<div class="text_block"><div hidden>hidden body</div></div><div class="text_block">', 1)
    html = html.replace(b'<div class="news__time', b'<div aria-hidden="true">secret</div><div class="news__time', 1)
    record = parse_article(html)
    assert 'hidden body' not in record.text and 'secret' not in record.text
    assert 'уведомление' in record.text


def test_fns_hidden_publication_never_becomes_known_date(fns_article_html):
    html = fns_article_html.replace(b'class="news__time single-news__time"',
        b'class="news__time single-news__time" style="visibility: hidden !important"')
    record = parse_article(html)
    assert record.raw_publication is record.published_date is record.published_at is None
    assert record.published_precision == 'unknown'


def test_fns_table_preserves_cell_positions_with_paragraphs_and_empty_cells():
    html = ('<h1>Налоговые сведения</h1><div class="text_block">'
        '<table><tr><th>Налог</th><th>Ставка</th><th>Срок</th></tr>'
        '<tr><td><p>Имущество</p><p>организаций</p></td><td><p>2%</p></td><td>28 октября</td></tr>'
        '<tr><td></td><td>3%</td><td></td></tr></table></div>').encode()
    assert parse_article(html).text == (
        'Налог\tСтавка\tСрок\nИмущество организаций\t2%\t28 октября\n\t3%\t'
    )


def test_fns_index_with_utf8_without_declared_charset_has_readable_title():
    html = '<div class="news-block__name"><a href="16664106/">НДС</a></div>'.encode()
    assert parse_index(html)[0].title == 'НДС'


def test_fns_index_survives_site_level_main_form(fns_index_html):
    html = fns_index_html.replace(b'<body>', b'<body><form id="MainForm" class="main-form"><input value="service">')
    html = html.replace(b'</body>', b'</form></body>')
    assert [item.url for item in parse_index(html)] == [ARTICLE_URL, SECOND_URL]


def test_fns_article_survives_site_level_main_form_but_excludes_service_forms(fns_article_html):
    html = fns_article_html.replace(b'<body>', b'<body><form id="MainForm" class="main-form"><input value="service">')
    html = html.replace(b'</body>', b'</form></body>')
    record = parse_article(html)
    assert record.title == 'Срок уплаты налога и исключения'
    assert record.published_date == date(2026, 10, 23)
    assert 'Имущество\t2%\t28 октября' in record.text
    assert 'Оцените страницу' not in record.text


@pytest.mark.parametrize('html', [
    b'<h1>Title</h1><div class="text_block"><form>Only form</form></div>',
    b'<h1>Title</h1><article>Wrong layout</article>',
    b'<div class="text_block">Missing article title</div>',
])
def test_fns_empty_or_unrecognized_article_is_rejected(html):
    with pytest.raises(ValueError):
        parse_article(html)


def test_fns_index_discovers_only_unique_reviewed_article_urls(fns_index_html):
    articles = parse_index(fns_index_html)
    assert [(a.url, a.title, a.raw_date) for a in articles] == [
        (ARTICLE_URL, 'Срок уплаты налога', '23.10.2026'),
        (SECOND_URL, 'Отчетность организаций', '22.10.2026'),
    ]


def test_fns_index_rejects_broad_path_non_articles_and_preserves_query_identity():
    html = ('<div class="news-block__name">'
        '<a href="16664106/?id=42#body">Title</a>'
        '<a href="16664106/?id=42#other">Duplicate fragment</a>'
        '<a href="16664106/?id=43">Different query</a>'
        '<a href="archive/">Archive</a><a href="16664106/image.jpg">Image</a>'
        '<a href="./">Rubric</a><a href="/rn77/rss_region/">RSS</a>'
        '</div>').encode()
    assert [a.url for a in parse_index(html)] == [ARTICLE_URL + '?id=42', ARTICLE_URL + '?id=43']


def test_fns_collect_uses_html_and_preserves_observed_metadata(fns_index_html, fns_article_html):
    seen = []
    def respond(request):
        seen.append(str(request.url))
        if str(request.url) == INDEX_URL:
            return httpx.Response(200, content=fns_index_html, headers={'etag': '"index-v1"'})
        return httpx.Response(200, content=fns_article_html)
    batch = make_source(respond).collect(1)
    assert seen == [INDEX_URL, ARTICLE_URL]
    assert batch.status == 'ok'
    assert (batch.attempted, batch.succeeded, batch.rejected) == (1, 1, 0)
    assert len(batch.records) == 1 and batch.records[0].raw_body == fns_article_html.decode()
    assert batch.checked_at is not None and batch.last_success_at is not None
    assert batch.etag == '"index-v1"' and not batch.item_errors


@pytest.mark.parametrize('body', [b'<rss><channel></channel></rss>', b'<html><body>No articles</body></html>'])
def test_fns_http_200_without_items_is_not_healthy(body):
    seen = []
    batch = make_source(lambda r: seen.append(str(r.url)) or httpx.Response(200, content=body)).collect(2)
    assert seen == [INDEX_URL]
    assert batch.status == 'empty' and batch.reason
    assert not batch.records and batch.last_success_at is None
    assert (batch.attempted, batch.succeeded, batch.rejected) == (0, 0, 0)


def test_fns_collect_reports_partial_and_keeps_successful_article(fns_index_html, fns_article_html):
    def respond(request):
        if str(request.url) == INDEX_URL:
            return httpx.Response(200, content=fns_index_html)
        if str(request.url) == ARTICLE_URL:
            return httpx.Response(200, content=fns_article_html)
        return httpx.Response(404)
    batch = make_source(respond).collect(2)
    assert batch.status == 'partial'
    assert (batch.attempted, batch.succeeded, batch.rejected) == (2, 1, 1)
    assert [r.url for r in batch.records] == [ARTICLE_URL]
    assert len(batch.item_errors) == 1 and SECOND_URL in batch.item_errors[0]
    assert batch.last_success_at is not None


@pytest.mark.parametrize('status,body,expected', [
    (403, b'', 'blocked'), (503, b'<title>Just a moment</title>', 'blocked'),
    (404, b'', 'failed'),
])
def test_fns_discovery_failure_keeps_truthful_health(status, body, expected):
    batch = make_source(lambda r: httpx.Response(status, content=body)).collect(1)
    assert batch.status == expected and batch.reason and batch.item_errors
    assert batch.last_success_at is None and batch.attempted == 0


def test_fns_all_article_failures_do_not_count_as_success(fns_index_html):
    def respond(request):
        return httpx.Response(200, content=fns_index_html if str(request.url) == INDEX_URL else b'<h1>Broken</h1>')
    batch = make_source(respond).collect(2)
    assert batch.status == 'failed' and not batch.records
    assert (batch.attempted, batch.succeeded, batch.rejected) == (2, 0, 2)
    assert batch.last_success_at is None and len(batch.item_errors) == 2


@pytest.mark.parametrize('redirect', [INDEX_URL, SECOND_URL, ARTICLE_URL + '?id=99'])
def test_fns_allowed_redirect_cannot_substitute_article_identity(fns_index_html, fns_article_html, redirect):
    def respond(request):
        if str(request.url) == INDEX_URL:
            return httpx.Response(200, content=fns_index_html)
        if str(request.url) == ARTICLE_URL:
            return httpx.Response(302, headers={'location': redirect})
        return httpx.Response(200, content=fns_article_html)
    batch = make_source(respond).collect(1)
    assert not batch.records
    assert batch.status in {'blocked', 'failed'}
    assert batch.attempted == batch.rejected == 1


def test_fns_304_discovery_is_unchanged_without_invented_success():
    batch = make_source(lambda r: httpx.Response(304, headers={'etag': '"v1"'})).collect(1)
    assert batch.status == 'unchanged' and batch.etag == '"v1"'
    assert batch.attempted == 0 and batch.last_success_at is None and not batch.records


def test_fns_article_304_is_unchanged_without_invented_record(fns_index_html):
    batch = make_source(lambda r: httpx.Response(200, content=fns_index_html)
        if str(r.url) == INDEX_URL else httpx.Response(304)).collect(1)
    assert batch.status == 'unchanged' and not batch.records
    assert (batch.attempted, batch.succeeded, batch.rejected) == (1, 0, 0)
    assert batch.last_success_at is None


@pytest.mark.parametrize('limit', [0, -1, 101])
def test_fns_invalid_limit_is_rejected_before_network(limit):
    seen = []
    source = make_source(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    with pytest.raises(ValueError):
        source.collect(limit)
    assert not seen
