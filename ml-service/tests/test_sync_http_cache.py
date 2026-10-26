"""Conditional HTTP over MockTransport, with virtual pacing and persisted validators."""
import json
from pathlib import Path

import httpx
import pytest

from http_fakes import fake_http_transport
from insight_agent.materials.collector import collect_sources, sync_sources
from insight_agent.materials.registry import get_spec
from insight_agent.materials.store import load_catalog_token, load_snapshot
from test_multisource_sync import T

FIXTURES = Path(__file__).parent / 'fixtures/materials'


def responses(source):
    spec = get_spec(source)
    if source == 'cbr_press':
        url = 'https://www.cbr.ru/press/event/101/'
        index = f'<rss><channel><item><title>Rate decision</title><link>{url}</link></item></channel></rss>'.encode()
        article = b'<div class="landing-text">The key rate remains 17 percent.</div>'
    elif source == 'fns_news':
        url = 'https://www.nalog.gov.ru/rn77/news/activities_fts/16664106/'
        index, article = [(FIXTURES / name).read_bytes() for name in ('fns-index.html', 'fns-article.html')]
    else:
        url = 'https://www.eia.gov/todayinenergy/detail.php?id=68344'
        index, article = [(FIXTURES / name).read_bytes() for name in ('eia-index.html', 'eia-article.html')]
    return spec, {spec.discovery_url: index, url: article}


@pytest.mark.parametrize('source', ['cbr_press', 'fns_news', 'eia_today'])
def test_conditional_discovery_rechecks_articles_and_304_preserves_publication(tmp_path, source):
    spec, bodies = responses(source)
    requests = []
    def handler(request):
        requests.append(request)
        assert str(request.url) in bodies
        headers = {'etag': '"v1"', 'last-modified': 'Sat, 24 Oct 2026 10:00:00 GMT'}
        if request.headers.get('if-none-match') == '"v1"':
            return httpx.Response(304, headers=headers)
        return httpx.Response(200, content=bodies[str(request.url)], headers=headers)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = fake_http_transport(client)
        first = collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache')
        assert sync_sources(tmp_path, first, T).exit_code == 0
        token, saved = load_catalog_token(tmp_path), load_snapshot(tmp_path)
        second = collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache')
        result = sync_sources(tmp_path, second, T)
    assert len(requests) == 4
    assert {str(request.url) for request in requests[2:]} == set(bodies)
    assert all(request.headers['if-none-match'] == '"v1"' for request in requests[2:])
    assert all(request.headers['if-modified-since'] == 'Sat, 24 Oct 2026 10:00:00 GMT' for request in requests[2:])
    assert second[0].status == result.status == 'unchanged'
    assert len(second[0].records) == 1 and result.exit_code == 0
    assert load_catalog_token(tmp_path) == token and load_snapshot(tmp_path) == saved


def test_cached_discovery_does_not_hide_changed_article(tmp_path):
    spec, bodies = responses('cbr_press')
    iteration = 0
    def handler(request):
        url = str(request.url)
        if request.headers.get('if-none-match') and url == spec.discovery_url:
            return httpx.Response(304, headers={'etag': '"index"'})
        content = bodies[url]
        if iteration and url != spec.discovery_url:
            content = content.replace(b'17 percent', b'18 percent')
        return httpx.Response(200, content=content, headers={'etag': '"index"' if url == spec.discovery_url else f'"article-{iteration}"'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = fake_http_transport(client)
        sync_sources(tmp_path, collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache'), T)
        old = load_snapshot(tmp_path)
        iteration = 1
        result = sync_sources(tmp_path, collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache'), T)
    current = load_snapshot(tmp_path)
    assert result.status == 'complete'
    assert current.news[0].event_id == old.news[0].event_id
    assert current.news[0].text == 'The key rate remains 18 percent.'
    assert len(current.source_versions) == 2


def test_no_cached_fallback_after_access_denial(tmp_path):
    spec, bodies = responses('cbr_press')
    denied = False
    def handler(request):
        return httpx.Response(403) if denied else httpx.Response(200, content=bodies[str(request.url)], headers={'etag': '"v1"'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = fake_http_transport(client)
        sync_sources(tmp_path, collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache'), T)
        token = load_catalog_token(tmp_path)
        denied = True
        batches = collect_sources([spec], 1, transport=transport, cache_dir=tmp_path / 'http-cache')
        result = sync_sources(tmp_path, batches, T)
    assert batches[0].status == 'blocked' and not batches[0].records
    assert result.status == 'failed' and result.exit_code == 1
    assert load_catalog_token(tmp_path) == token


def test_304_without_cache_never_claims_healthy_or_publishes(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(304))) as client:
        batches = collect_sources([get_spec('cbr_press')], 1, transport=fake_http_transport(client), cache_dir=tmp_path / 'http-cache')
    result = sync_sources(tmp_path, batches, T)
    assert batches[0].status == 'failed' and not batches[0].records
    assert result.exit_code == 1 and not (tmp_path / 'current.json').exists()


@pytest.mark.parametrize('limit', [0, 101, True, 1.5])
def test_invalid_limits_are_rejected_before_any_request(limit):
    with pytest.raises(ValueError, match='limit'):
        collect_sources([get_spec('cbr_press')], limit)


@pytest.mark.parametrize('update', [{'source_id': 'unknown'}, {'hosts': ('untrusted.example',)}])
def test_nonregistry_policies_are_rejected_before_any_request(update):
    with pytest.raises(ValueError):
        collect_sources([get_spec('cbr_press').model_copy(update=update)], 1)


def test_cache_corruption_is_rejected_on_conditional_read(tmp_path):
    spec, bodies = responses('cbr_press')
    def handler(request):
        if 'if-none-match' in request.headers:
            return httpx.Response(304)
        return httpx.Response(200, content=bodies[str(request.url)], headers={'etag': '"v1"'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        transport = fake_http_transport(client)
        collect_sources([spec], 1, transport=transport, cache_dir=tmp_path)
        for path in tmp_path.glob('*.json'):
            cached = json.loads(path.read_text())
            cached['body'] = 'Y29ycnVwdA=='
            path.write_text(json.dumps(cached))
        batches = collect_sources([spec], 1, transport=transport, cache_dir=tmp_path)
    assert batches[0].status == 'failed' and not batches[0].records
