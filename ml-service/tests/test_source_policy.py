"""Controlled transport: denied destinations are never sent; limits apply to decoded bytes."""
import gzip

import httpx
import pytest

from insight_agent.materials import sources


class Clock:
    now = 0.0
    def __call__(self):
        return self.now
    def sleep(self, seconds):
        self.now += seconds


def setup_transport(handler):
    from insight_agent.materials.transport import ControlledTransport
    from insight_agent.materials.registry import get_spec
    clock = Clock()
    transport = ControlledTransport(httpx.Client(transport=httpx.MockTransport(handler)), clock=clock, sleep=clock.sleep)
    return transport, get_spec('eia_today'), clock


def test_fixed_registry_rejects_unregistered_ids_without_fetch():
    from insight_agent.materials.registry import get_source
    with pytest.raises(ValueError, match='Unknown source'):
        get_source('https://evil.test', None)


@pytest.mark.parametrize('url', ['http://www.eia.gov/todayinenergy/', 'https://user:pw@www.eia.gov/todayinenergy/',
    'https://www.eia.gov:444/todayinenergy/', 'https://127.0.0.1/todayinenergy/',
    'https://www.eia.gov/rss', 'https://www.eia.gov/todayinenergy/../rss',
    'https://www.eia.gov/todayinenergy/%2e%2e/rss'])
def test_forbidden_destination_never_sent(url):
    from insight_agent.materials.transport import TransportError
    seen = []
    transport, spec, _ = setup_transport(lambda r: seen.append(str(r.url)) or httpx.Response(200))
    with pytest.raises(TransportError):
        transport.get(url, spec=spec)
    assert seen == []


@pytest.mark.parametrize('target', ['https://evil.test/article', 'https://10.0.0.1/x', 'https://www.eia.gov/rss'])
def test_redirect_is_checked_before_next_request(target):
    from insight_agent.materials.transport import TransportError
    seen = []
    def respond(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'location': target})
    transport, spec, _ = setup_transport(respond)
    with pytest.raises(TransportError):
        transport.get(spec.discovery_url, spec=spec)
    assert seen == [spec.discovery_url]


def test_decoded_limit_cannot_be_bypassed_by_compression():
    from insight_agent.materials.transport import TransportError
    transport, spec, _ = setup_transport(lambda r: httpx.Response(200, content=gzip.compress(b'x'*(2*1024*1024+1)), headers={'content-encoding':'gzip'}))
    with pytest.raises(TransportError, match='size'):
        transport.get(spec.discovery_url, spec=spec)


def test_retry_after_is_capped_one_retry_and_host_interval_applies():
    from insight_agent.materials.transport import TransportError
    seen = []
    def respond(r):
        seen.append((clock(), r.extensions['timeout']))
        return httpx.Response(429, headers={'retry-after':'9999'})
    transport, spec, clock = setup_transport(respond)
    with pytest.raises(TransportError):
        transport.get(spec.discovery_url, spec=spec)
    assert len(seen) == 2
    assert seen[1][0] - seen[0][0] == 30
    assert seen[0][1]['read'] == 10


def test_conditional_get_and_unchanged_have_no_body():
    seen = []
    def respond(r):
        seen.append(r.headers.get('if-none-match'))
        return httpx.Response(304, headers={'etag':'"v1"'})
    transport, spec, clock = setup_transport(respond)
    result = transport.get(spec.discovery_url, spec=spec, headers={'If-None-Match':'"v1"'})
    assert result.status_code == 304 and result.body == b''
    transport.get(spec.discovery_url, spec=spec)
    assert clock() == 2
    assert seen == ['"v1"', None]


@pytest.mark.parametrize('status', [401,403])
def test_denial_is_blocked_and_not_retried(status):
    from insight_agent.materials.transport import TransportError
    seen=[]
    transport, spec, _ = setup_transport(lambda r: seen.append(1) or httpx.Response(status))
    with pytest.raises(TransportError) as exc:
        transport.get(spec.discovery_url, spec=spec)
    assert exc.value.status == 'blocked'
    assert len(seen)==1


def test_contract_exposes_batch_and_discovery_and_counts():
    assert hasattr(sources, 'DiscoveredArticle')
    assert hasattr(sources, 'NewsSourceAdapter')
    batch = sources.FetchBatch(source_id='fns_news', status='empty', attempted=0, succeeded=0, rejected=0)
    assert batch.records == []


def test_eia_archive_path_is_not_part_of_reviewed_policy():
    from insight_agent.materials.transport import TransportError
    seen=[]
    transport, spec, _ = setup_transport(lambda r: seen.append(1) or httpx.Response(200))
    with pytest.raises(TransportError):
        transport.get('https://www.eia.gov/todayinenergy/archive.php', spec=spec)
    assert seen == []


def test_redirect_budget_allows_four_hops_and_stops_fifth():
    from insight_agent.materials.transport import TransportError
    seen=[]
    transport, spec, clock = setup_transport(lambda r: seen.append(str(r.url)) or httpx.Response(302, headers={'location':'detail.php?id=2'}))
    with pytest.raises(TransportError, match='redirect'):
        transport.get(spec.discovery_url, spec=spec)
    assert len(seen) == 5 and clock() == 8


def test_timeout_retry_is_bounded_and_404_is_not_retried():
    from insight_agent.materials.transport import TransportError
    seen=[]
    def timeout(r):
        seen.append(1)
        raise httpx.ReadTimeout('timeout', request=r)
    transport, spec, _ = setup_transport(timeout)
    with pytest.raises(TransportError):
        transport.get(spec.discovery_url, spec=spec)
    assert len(seen)==2
    seen.clear()
    transport, spec, _ = setup_transport(lambda r: seen.append(1) or httpx.Response(404))
    with pytest.raises(TransportError):
        transport.get(spec.discovery_url, spec=spec)
    assert len(seen)==1


def test_cbr_legacy_batch_preserves_blocked_status():
    from insight_agent.materials.registry import get_source
    transport, _, _ = setup_transport(lambda r: httpx.Response(403))
    batch = get_source('cbr_press', transport).collect(1)
    assert batch.status == 'blocked'
    assert batch.records == [] and batch.item_errors


def test_cbr_new_adapter_keeps_final_url_raw_body_and_publication_provenance():
    from insight_agent.materials.registry import get_source
    rss = b'<rss><channel><item><title>Release</title><link>https://www.cbr.ru/press/old/</link><pubDate>Sat, 24 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'
    body = b'<article><p>Original  text.</p></article>'
    def respond(r):
        if r.url.path == '/rss/RssPress':
            return httpx.Response(200, content=rss)
        if r.url.path == '/press/old/':
            return httpx.Response(302, headers={'location':'/press/new/?file=one'})
        return httpx.Response(200, content=body)
    transport, _, _ = setup_transport(respond)
    batch = get_source('cbr_press', transport).collect(1)
    record, = batch.records
    assert record.url == 'https://www.cbr.ru/press/new/?file=one'
    assert record.discovered_url == 'https://www.cbr.ru/press/old/'
    assert record.raw_body == body.decode()
    assert record.raw_publication == 'Sat, 24 Oct 2026 12:00:00 GMT'
    assert record.timezone_provenance == 'explicit_offset'
    assert record.publisher_id == 'cbr'


def test_challenge_is_blocked_without_retry():
    from insight_agent.materials.transport import TransportError
    seen=[]
    transport, spec, _ = setup_transport(lambda r: seen.append(1) or httpx.Response(200, content=b'<title>Just a moment...</title><script src="cf-chl-test"></script>'))
    with pytest.raises(TransportError) as exc:
        transport.get(spec.discovery_url, spec=spec)
    assert exc.value.status == 'blocked' and len(seen) == 1


def test_retry_after_http_date_is_bounded_and_success_is_returned():
    from datetime import UTC, datetime
    seen=[]
    def respond(r):
        seen.append(clock())
        return httpx.Response(503, headers={'Retry-After':'Mon, 26 Oct 2026 12:01:00 GMT'}) if len(seen)==1 else httpx.Response(200, content=b'ok')
    transport, spec, clock = setup_transport(respond)
    transport.wall_clock = lambda: datetime(2026,10,26,12,tzinfo=UTC)
    assert transport.get(spec.discovery_url, spec=spec).body == b'ok'
    assert seen == [0,30]


def test_single_transport_serializes_requests_to_same_host():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    first_entered, release_first, second_entered, second_started = Event(), Event(), Event(), Event()
    def respond(r):
        if r.url.params.get('id') == '1':
            first_entered.set()
            assert release_first.wait(2)
        else:
            second_entered.set()
        return httpx.Response(200, content=b'ok')
    transport, spec, _ = setup_transport(respond)
    def second_request():
        second_started.set()
        return transport.get('https://www.eia.gov/todayinenergy/detail.php?id=2', spec=spec)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(transport.get, 'https://www.eia.gov/todayinenergy/detail.php?id=1', spec=spec)
        assert first_entered.wait(2)
        second = executor.submit(second_request)
        assert second_started.wait(2)
        try:
            assert not second_entered.wait(.05)
        finally:
            release_first.set()
        assert first.result().body == second.result().body == b'ok'
