from datetime import timezone

import httpx
import pytest

from http_fakes import fake_http_transport

RSS=b'''<rss version="2.0"><channel><item><title>Rate decision</title>
<link>https://www.cbr.ru/press/event/123/</link><pubDate>Sat, 24 Oct 2026 12:00:00 GMT</pubDate>
</item></channel></rss>'''
ARTICLE='''<html><nav>Menu</nav><main><article><h1>Rate decision</h1>
<p>The rate was not reduced: 17,5% on 24 October.</p></article></main><footer>Unrelated</footer></html>'''


def test_rss_fetches_primary_article_and_preserves_dates_numbers_negation():
    from insight_agent.materials.news import CbrRssSource
    def response(req):
        return httpx.Response(200,content=RSS if req.url.path=='/rss/RssPress' else ARTICLE.encode())
    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        items=CbrRssSource(client, transport=fake_http_transport(client)).fetch(1)
    assert len(items)==1 and '17,5%' in items[0].text and 'not reduced' in items[0].text
    assert 'Menu' not in items[0].text and 'Unrelated' not in items[0].text
    assert items[0].url=='https://www.cbr.ru/press/event/123/'
    assert items[0].published_at.isoformat()=='2026-10-24T12:00:00+00:00'
    assert items[0].retrieved_at.tzinfo==timezone.utc


def test_absent_publication_date_is_not_replaced_with_fetch_time():
    from insight_agent.materials.news import parse_rss
    xml=b'<rss><channel><item><title>A</title><link>https://www.cbr.ru/a</link></item></channel></rss>'
    assert parse_rss(xml)[0]['published_at'] is None


def test_redirect_outside_source_allowlist_is_rejected():
    from insight_agent.materials.news import CbrRssSource
    with httpx.Client(transport=httpx.MockTransport(lambda req:
        httpx.Response(302,headers={'location':'https://127.0.0.1/admin'}))) as client:
        with pytest.raises(ValueError,match='allowlist'):
            CbrRssSource(client, transport=fake_http_transport(client)).fetch(1)


def test_broken_feed_and_xml_entities_are_not_silently_accepted():
    from insight_agent.materials.news import parse_rss
    with pytest.raises(ValueError):
        parse_rss(b'<!DOCTYPE x [<!ENTITY a "bad">]><rss>&a;</rss>')
    with pytest.raises(ValueError):
        parse_rss(b'not xml')


def test_http_timeout_remains_an_error_instead_of_fabricated_news():
    from insight_agent.materials.news import CbrRssSource
    def fail(req):
        raise httpx.ReadTimeout('source timed out',request=req)
    with httpx.Client(transport=httpx.MockTransport(fail)) as client:
        with pytest.raises(httpx.ReadTimeout):
            CbrRssSource(client, transport=fake_http_transport(client)).fetch(1)


def test_oversize_article_is_rejected():
    from insight_agent.materials.news import CbrRssSource
    with httpx.Client(transport=httpx.MockTransport(lambda req:
        httpx.Response(200,content=RSS if req.url.path=='/rss/RssPress' else b'x'*(2*1024*1024+1)))) as client:
        with pytest.raises(ValueError,match='size'):
            CbrRssSource(client, transport=fake_http_transport(client)).fetch(1)
