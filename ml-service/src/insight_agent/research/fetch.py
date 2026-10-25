"""Backward-compatible CBR byte API over the shared controlled transport."""
import httpx

from insight_agent.materials.sources import SourceLimits, SourceSpec
from insight_agent.materials.transport import ControlledTransport, TransportError, USER_AGENT, check_url

ALLOWED_HOSTS = frozenset({'cbr.ru', 'www.cbr.ru'})
MAX_BYTES = 2 * 1024 * 1024


class OpenSourceError(RuntimeError):
    pass


class SafeFetcher:
    def __init__(self, client: httpx.Client, hosts: frozenset[str] = ALLOWED_HOSTS, max_bytes: int = MAX_BYTES,
                 timeout: float = 10.0):
        self.client, self.hosts, self.max_bytes, self.timeout = client, hosts, max_bytes, timeout
        self.transport = ControlledTransport(client)
        self.spec = SourceSpec(source_id='cbr_research', publisher_id='cbr', hosts=tuple(sorted(hosts)),
            allowed_paths=('/',), discovery_url='https://www.cbr.ru/rss/RssPress',
            parser_version='cbr-research-v1', language='ru', material_type='indicator',
            date_policy='day_only', rights_url='https://www.cbr.ru/about/',
            limits=SourceLimits(max_bytes=max_bytes, timeout=timeout))

    def _check(self, url: str) -> None:
        try:
            check_url(url, self.spec)
        except TransportError as exc:
            raise OpenSourceError(str(exc)) from exc

    def get(self, url: str, params: dict[str, str] | None = None) -> bytes:
        try:
            return self.transport.get(url, spec=self.spec, params=params).body
        except TransportError as exc:
            raise OpenSourceError(str(exc)) from exc
