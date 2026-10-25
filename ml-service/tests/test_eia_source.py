"""EIA contracts use synthetic HTML and a real policy transport over fake HTTP."""
from datetime import date
from pathlib import Path

import httpx
import pytest

from http_fakes import fake_http_transport
from insight_agent.materials.adapters import eia
from insight_agent.materials.registry import get_source, get_spec


FIXTURES = Path(__file__).parent / "fixtures" / "materials"
ARTICLE = "https://www.eia.gov/todayinenergy/detail.php?id=68344"
SECOND = "https://www.eia.gov/todayinenergy/detail.php?id=68325"
INDEX = "https://www.eia.gov/todayinenergy/recent.php"


@pytest.fixture
def article_html():
    return (FIXTURES / "eia-article.html").read_bytes()


@pytest.fixture
def index_html():
    return (FIXTURES / "eia-index.html").read_bytes()


def test_eia_refinery_rate_is_not_interest(article_html):
    record = eia.parse_eia_article(article_html, ARTICLE)
    assert record.published_date == date(2026, 10, 22)
    assert record.published_at is None
    assert record.published_precision == "date"
    assert record.source_timezone is None
    assert record.source_utc_offset_seconds is None
    assert record.timezone_provenance == "unknown"
    assert record.material_type == "public_analysis"
    assert "cost_of_debt" not in record.topics
    assert "commodity_prices" in record.topics


def test_eia_headline_inline_markup_keeps_word_boundaries(article_html, index_html):
    html = article_html.replace(b"U.S. refinery utilization rates", b"U.S. <em>refinery</em> utilization rates")
    assert eia.parse_eia_article(html, ARTICLE).title == "U.S. refinery utilization rates"
    html = index_html.replace(b"U.S. refinery utilization rates", b"U.S. <em>refinery</em> utilization rates")
    assert eia.parse_eia_index(html, INDEX)[0].title == "U.S. refinery utilization rates"


def test_eia_preserves_narrative_units_conditions_and_table_once(article_html):
    record = eia.parse_eia_article(article_html, ARTICLE)
    assert record.title == "U.S. refinery utilization rates"
    assert record.text.count("U.S. refinery utilization averaged 95% of capacity.") == 1
    assert "This is not a borrowing interest rate." in record.text
    assert "Oil may cost 70 USD/barrel if demand rises; this is a forecast, not an observed price." in record.text
    assert "Period | Capacity utilization\nMay–September | 95%" in record.text
    assert "руб" not in record.text
    for noise in ("Data source:", "unlicensed chart", "Principal contributor", "Example Author",
                  "Tags:", "More recent articles", "Site teaser", "Energy menu", "legal navigation"):
        assert noise not in record.text


def test_eia_preserves_original_source_date_rights_and_translation_status(article_html):
    record = eia.parse_eia_article(article_html, ARTICLE)
    assert record.url == ARTICLE
    assert record.discovered_url == ARTICLE
    assert record.publisher_id == "eia"
    assert record.adapter_id == "eia_today"
    assert record.raw_publication == "October 22, 2026"
    assert record.language == "en"
    assert record.completeness == "full"
    assert record.rights_url == "https://www.eia.gov/about/copyrights_reuse.php"
    assert record.parser_version == get_spec("eia_today").parser_version
    assert record.original_text == record.text
    assert record.raw_body == article_html.decode("utf-8")
    assert record.translation_attribution == "Original English text from the U.S. Energy Information Administration; no translation."


@pytest.mark.parametrize("canonical", [
    "https://www.eia.gov/", "https://foreign.example/todayinenergy/detail.php?id=68344",
    "https://www.eia.gov/todayinenergy/detail.php?id=68325",
    "https://www.eia.gov/todayinenergy/detail.php", "https://www.eia.gov/todayinenergy/recent.php",
])
def test_eia_canonical_cannot_replace_article_identity(article_html, canonical):
    html = article_html.replace(b'href="https://www.eia.gov/"', f'href="{canonical}"'.encode())
    assert eia.parse_eia_article(html, ARTICLE).url == ARTICLE


@pytest.mark.parametrize("url", [
    "https://www.eia.gov/todayinenergy/detail.php", "https://www.eia.gov/todayinenergy/detail.php?id=",
    "https://www.eia.gov/todayinenergy/detail.php?id=68344&id=68325",
    "https://www.eia.gov/todayinenergy/detail.php?id=../archive",
    "https://www.eia.gov/todayinenergy/recent.php?id=68344",
    "https://www.eia.gov/todayinenergy/archive.php?id=68344",
    "https://foreign.example/todayinenergy/detail.php?id=68344",
])
def test_eia_rejects_nonarticle_or_ambiguous_identity(article_html, url):
    with pytest.raises(ValueError, match="article URL"):
        eia.parse_eia_article(article_html, url)


def test_eia_inline_words_are_not_split_and_hidden_service_content_is_removed(article_html):
    extra = b'''<p>U.S. ref<a href="/petroleum/">in</a>ing may not decline.<br>Next line.</p>
    <p hidden>Hidden archive</p><div style="display: none">Hidden menu</div>
    <p aria-hidden="true">Hidden accessibility duplicate</p><p style="visibility: hidden">Hidden note</p>
    <form>Sign up</form><script>tracking()</script><style>bad-style</style>'''
    record = eia.parse_eia_article(article_html.replace(b'<p data-type="inbrief">', extra + b'<p data-type="inbrief">'), ARTICLE)
    assert "U.S. refining may not decline.\nNext line." in record.text
    assert "Hidden" not in record.text
    assert "Sign up" not in record.text
    assert "tracking()" not in record.text
    assert "bad-style" not in record.text


@pytest.mark.parametrize("old,new", [
    (b'tie-article', b'other-layout'),
    (b'<h1>U.S. refinery utilization rates</h1>', b''),
    (b'October 22, 2026', b'not a publication date'),
])
def test_eia_rejects_missing_article_metadata(article_html, old, new):
    with pytest.raises(ValueError):
        eia.parse_eia_article(article_html.replace(old, new), ARTICLE)


def test_eia_rejects_empty_primary_body():
    with pytest.raises(ValueError, match="body"):
        eia.parse_eia_article(b'<div class="tie-article"><span class="date">October 22, 2026</span><h1>Title</h1></div>', ARTICLE)


def test_eia_index_uses_recent_headlines_and_preserves_query_id(index_html):
    items = eia.parse_eia_index(index_html, INDEX)
    assert [item.url for item in items] == [ARTICLE, SECOND]
    assert items[0].title == "U.S. refinery utilization rates"
    assert items[0].raw_date == "October 22, 2026"


def test_eia_index_rejects_unsafe_nested_links_and_duplicate_identity(index_html):
    extra = b'''<div class="tie-article"><h1><a href="detail.php">Missing ID</a></h1></div>
    <div class="tie-article"><h1><a href="archive.php?id=1">Archive</a></h1></div>
    <div class="tie-article"><h1><a href="/rss/todayinenergy.xml">RSS</a></h1></div>
    <div class="tie-article"><h1><a href="detail.php?id=1&amp;id=2">Ambiguous</a></h1></div>
    <div class="tie-article" hidden><h1><a href="detail.php?id=3">Hidden</a></h1></div>
    <div class="tie-article"><h1><a href="detail.php?id=68344#chart">Duplicate fragment</a></h1></div>'''
    assert [item.url for item in eia.parse_eia_index(index_html + extra, INDEX)] == [ARTICLE, SECOND]


def test_eia_collection_limits_requests_and_uses_fixed_registry(article_html, index_html):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=index_html if str(request.url) == INDEX else article_html)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = get_source("eia_today", fake_http_transport(client))
        batch = source.collect(limit=1)
    assert calls == [INDEX, ARTICLE]
    assert batch.status == "ok"
    assert (batch.attempted, batch.succeeded, batch.rejected) == (1, 1, 0)
    assert batch.records[0].url == ARTICLE
    assert batch.last_success_at is not None
    assert batch.checked_at is not None


def test_eia_collection_reports_partial_article_failure(article_html, index_html):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if str(request.url) == INDEX:
            return httpx.Response(200, content=index_html)
        return httpx.Response(200, content=article_html) if str(request.url) == ARTICLE else httpx.Response(404)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch = eia.EiaTodaySource(fake_http_transport(client), get_spec("eia_today")).collect(2)
    assert calls == [INDEX, ARTICLE, SECOND]
    assert batch.status == "partial"
    assert (batch.attempted, batch.succeeded, batch.rejected) == (2, 1, 1)
    assert len(batch.item_errors) == 1
    assert SECOND in batch.item_errors[0]


@pytest.mark.parametrize("status,body,expected", [(403, b"", "blocked"), (404, b"", "failed"),
    (200, b"<title>Just a moment</title>", "blocked"), (304, b"", "unchanged"),
    (200, b"<html>No recent articles</html>", "empty")])
def test_eia_discovery_outcomes_do_not_fabricate_success(status, body, expected):
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(status, content=body, headers={"etag": '"index"'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch = eia.EiaTodaySource(fake_http_transport(client), get_spec("eia_today")).collect(1)
    assert calls == [INDEX]
    assert batch.status == expected
    assert (batch.attempted, batch.succeeded, batch.rejected) == (0, 0, 0)
    assert batch.records == []
    assert batch.last_success_at is None
    if status == 304:
        assert batch.etag == '"index"'


@pytest.mark.parametrize("destination", [SECOND, INDEX])
def test_eia_rejects_article_redirect_that_changes_identity(article_html, index_html, destination):
    def handler(request):
        if str(request.url) == ARTICLE:
            return httpx.Response(302, headers={"location": destination})
        return httpx.Response(200, content=index_html if str(request.url) == INDEX else article_html)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch = eia.EiaTodaySource(fake_http_transport(client), get_spec("eia_today")).collect(1)
    assert batch.status == "blocked"
    assert (batch.attempted, batch.succeeded, batch.rejected) == (1, 0, 1)
    assert batch.records == []
    assert "identity" in batch.item_errors[0]


@pytest.mark.parametrize("limit", [0, -1, 101])
def test_eia_rejects_invalid_limit_before_network(limit):
    def handler(request):
        pytest.fail("Invalid limit must not access the network")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="limit"):
            eia.EiaTodaySource(fake_http_transport(client), get_spec("eia_today")).collect(limit)
