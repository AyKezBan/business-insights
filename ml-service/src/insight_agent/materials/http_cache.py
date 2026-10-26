"""Private conditional representations; never serve cached content on HTTP failure."""
import base64
from dataclasses import dataclass
from datetime import UTC, datetime
import json
from pathlib import Path

from insight_agent.materials.artifacts import digest, safe_path
from insight_agent.materials.sources import SourceSpec
from insight_agent.materials.store import atomic_json
from insight_agent.materials.transport import ControlledTransport, FetchResponse, TransportError, check_url


@dataclass(frozen=True)
class Observation:
    response: FetchResponse
    retrieved_at: datetime
    unchanged: bool


class ConditionalTransport:
    """Expose cached 304 bodies as 200 so adapters still check every article.

    Transport policy, retries and pacing stay in the shared ControlledTransport.
    Cached observation time stays unchanged; check time belongs to the run report.
    """
    def __init__(self, transport: ControlledTransport, cache_dir: Path | None = None):
        self.transport, self.client = transport, transport.client
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        self.entries = {}
        self.observations: dict[tuple[str, str], Observation] = {}

    def _path(self, key: str) -> Path:
        return safe_path(self.cache_dir, digest(key.encode()) + '.json')

    def get(self, url: str, *, spec: SourceSpec, headers=None, params=None) -> FetchResponse:
        if params:
            import httpx
            url = str(httpx.URL(url).copy_merge_params(params))
        check_url(url, spec)
        key = spec.source_id + ' ' + url
        entry = self.entries.get(key)
        if entry is None and self.cache_dir is not None and self._path(key).exists():
            entry = json.loads(self._path(key).read_text('utf-8'))
        if entry and (entry.get('request_url') != url or entry.get('source_id') != spec.source_id
                      or entry.get('parser_version') != spec.parser_version):
            entry = None
        request_headers = dict(headers or {})
        if entry:
            if entry.get('etag'):
                request_headers['If-None-Match'] = entry['etag']
            if entry.get('last_modified'):
                request_headers['If-Modified-Since'] = entry['last_modified']
        response = self.transport.get(url, spec=spec, headers=request_headers)
        check_url(response.url, spec)
        unchanged = response.status_code == 304
        if unchanged:
            if not entry or not (entry.get('etag') or entry.get('last_modified')) or response.url != entry['url']:
                raise TransportError('304 without a matching cached representation')
            try:
                body = base64.b64decode(entry['body'], validate=True)
                retrieved_at = datetime.fromisoformat(entry['retrieved_at'])
                if (digest(body) != entry['sha256'] or len(body) > spec.limits.max_bytes
                        or retrieved_at.tzinfo is None):
                    raise ValueError('cache integrity')
            except (KeyError, TypeError, ValueError) as exc:
                raise TransportError('Invalid cached representation') from exc
            response = FetchResponse(body, entry['url'], 200, {
                key: value for key, value in {'etag': entry.get('etag'),
                                             'last-modified': entry.get('last_modified')}.items() if value
            })
        else:
            if response.status_code != 200:
                raise TransportError('Unexpected source response status')
            retrieved_at = datetime.now(UTC)
            entry = dict(source_id=spec.source_id, request_url=url, url=response.url,
                         parser_version=spec.parser_version, retrieved_at=retrieved_at.isoformat(),
                         etag=response.headers.get('etag'), last_modified=response.headers.get('last-modified'),
                         sha256=digest(response.body), body=base64.b64encode(response.body).decode('ascii'))
            self.entries[key] = entry
            if self.cache_dir is not None:
                atomic_json(self._path(key), entry)
        observation = Observation(response, retrieved_at, unchanged)
        self.observations[(spec.source_id, url)] = observation
        self.observations[(spec.source_id, response.url)] = observation
        return response
