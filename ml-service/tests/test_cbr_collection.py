"""CBR sync must not combine RSS metadata with another allowed article's body."""
from datetime import date
from xml.sax.saxutils import escape

import httpx
import pytest

from http_fakes import fake_http_transport
from insight_agent.materials.collector import collect_sources
from insight_agent.materials.registry import get_spec

ORIGINAL = 'https://www.cbr.ru/press/PR/?file=23102026_133000key.htm'
GOOD = 'https://www.cbr.ru/press/event/good/'


def rss(url, *, include_good=False):
    good = f'<item><title>Good release</title><link>{GOOD}</link></item>' if include_good else ''
    return (
        '<rss><channel><item><title>Original rate decision</title>'
        f'<link>{escape(url)}</link><pubDate>Fri, 23 Oct 2026 13:30:00 +0300</pubDate>'
        f'</item>{good}</channel></rss>'
    ).encode()


@pytest.mark.parametrize('destination', [
    pytest.param('https://www.cbr.ru/press/PR/?file=24102026_other.htm', id='changed-file'),
    pytest.param('https://www.cbr.ru/press/other/?file=23102026_133000key.htm', id='changed-path'),
])
def test_changed_article_redirect_is_rejected_while_good_item_continues(destination):
    spec = get_spec('cbr_press')
    def handler(request):
        url = str(request.url)
        if url == spec.discovery_url:
            return httpx.Response(200, content=rss(ORIGINAL, include_good=True))
        if url == ORIGINAL:
            return httpx.Response(302, headers={'location': destination})
        if url == destination:
            return httpx.Response(200, text='<div class="landing-text">Unrelated next-day publication body.</div>')
        assert url == GOOD
        return httpx.Response(200, text='<div class="landing-text">The good publication remains available.</div>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch, = collect_sources([spec], 2, transport=fake_http_transport(client))
    assert batch.status == 'partial'
    assert (batch.attempted, batch.succeeded, batch.rejected) == (2, 1, 1)
    assert [record.url for record in batch.records] == [GOOD]
    assert batch.records[0].title == 'Good release'
    assert batch.records[0].text == 'The good publication remains available.'
    assert any('identity' in error for error in batch.item_errors)


def test_cbr_host_alias_and_existing_tracking_normalization_preserve_identity():
    spec = get_spec('cbr_press')
    discovered = ORIGINAL + '&utm_source=rss'
    destination = ORIGINAL.replace('www.cbr.ru', 'cbr.ru') + '&utm_campaign=redirect'
    def handler(request):
        url = str(request.url)
        if url == spec.discovery_url:
            return httpx.Response(200, content=rss(discovered))
        if url == discovered:
            return httpx.Response(302, headers={'location': destination})
        assert url == destination
        return httpx.Response(200, text='<div class="landing-text">The original rate remains 17 percent.</div>')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        batch, = collect_sources([spec], 1, transport=fake_http_transport(client))
    assert batch.status == 'ok' and batch.rejected == 0
    record, = batch.records
    assert record.url == destination and record.discovered_url == discovered
    assert record.title == 'Original rate decision'
    assert record.published_date == date(2026, 10, 23)
    assert record.text == 'The original rate remains 17 percent.'
