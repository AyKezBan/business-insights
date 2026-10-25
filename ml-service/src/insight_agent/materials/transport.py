"""One bounded HTTP engine for registry adapters and legacy CBR research.

Share one transport across adapters to serialize requests to each host. Static source
policies allow only reviewed paths, never URLs supplied by users or model output.
"""
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
import ipaddress
import threading
import time
from typing import Callable, Mapping
from urllib.parse import unquote, urljoin, urlsplit

import httpx

from insight_agent.materials.sources import SourceSpec

USER_AGENT = 'BusinessInsightsResearch/1.0'


class TransportError(RuntimeError):
    def __init__(self, reason: str, *, status: str = 'failed'):
        super().__init__(reason)
        self.reason, self.status = reason, status


@dataclass(frozen=True)
class FetchResponse:
    body: bytes
    url: str
    status_code: int
    headers: Mapping[str, str]


def check_url(url: str, spec: SourceSpec) -> None:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        if (parts.scheme != 'https' or host not in spec.hosts or parts.username is not None
                or parts.password is not None or parts.port not in (None, 443)
                or any(ord(c) < 32 for c in url) or '\\' in url):
            raise ValueError('origin')
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError('IP address')
        path = parts.path or '/'
        for _ in range(4):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if ('%' in path or '\\' in path or any(p in {'.', '..'} for p in path.split('/'))
                or any(ord(c) < 32 for c in path)):
            raise ValueError('ambiguous path')
        if not any(path.startswith(p) if p.endswith('/') else path == p for p in spec.allowed_paths):
            raise ValueError('path')
    except (ValueError, TypeError) as exc:
        raise TransportError('URL outside source allowlist', status='blocked') from exc


class ControlledTransport:
    def __init__(self, client: httpx.Client, *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.client, self.clock, self.sleep, self.wall_clock = client, clock, sleep, wall_clock
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}

    def _retry_delay(self, value: str | None, spec: SourceSpec) -> float:
        delay = spec.limits.min_interval
        if value:
            try:
                delay = float(value)
            except ValueError:
                try:
                    parsed = parsedate_to_datetime(value)
                    delay = (parsed - self.wall_clock()).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    pass
        return max(0.0, min(delay, spec.limits.retry_after_cap))

    def get(self, url: str, *, spec: SourceSpec, headers: Mapping[str, str] | None = None,
            params: dict[str, str] | None = None) -> FetchResponse:
        check_url(url, spec)
        if params:
            url = str(httpx.URL(url).copy_merge_params(params))
        redirects, retries = 0, 0
        request_headers = {'User-Agent': USER_AGENT, **(headers or {})}
        while True:
            check_url(url, spec)
            host = urlsplit(url).hostname
            with self._guard:
                lock = self._locks.setdefault(host, threading.Lock())
            retry_delay = None
            with lock:
                if host in self._last:
                    remaining = self._last[host] + spec.limits.min_interval - self.clock()
                    if remaining > 0:
                        self.sleep(remaining)
                self._last[host] = self.clock()
                try:
                    with self.client.stream('GET', url, timeout=spec.limits.timeout, follow_redirects=False,
                                            headers=request_headers) as response:
                        if response.status_code == 304:
                            return FetchResponse(b'', str(response.url), 304, dict(response.headers))
                        if response.is_redirect:
                            if redirects >= spec.limits.max_redirects or not response.headers.get('location'):
                                raise TransportError('Too many or invalid source redirects')
                            url = urljoin(url, response.headers['location'])
                            check_url(url, spec)
                            redirects += 1
                            continue
                        if response.status_code in (401, 403):
                            raise TransportError(f'Source access denied: {response.status_code}', status='blocked')
                        if response.status_code in (408, 429, 500, 502, 503, 504) and retries < spec.limits.retries:
                            retry_delay = self._retry_delay(response.headers.get('retry-after'), spec)
                        else:
                            response.raise_for_status()
                            chunks, total = [], 0
                            for chunk in response.iter_bytes(chunk_size=65536):
                                total += len(chunk)
                                if total > spec.limits.max_bytes:
                                    raise TransportError('Source decoded size limit exceeded')
                                chunks.append(chunk)
                            body = b''.join(chunks)
                            head = body[:65536].lower()
                            if any(marker in head for marker in (b'cf-chl-', b'<title>just a moment', b'<title>access denied', b'g-recaptcha', b'hcaptcha.com/1/api')):
                                raise TransportError('Source challenge denied', status='blocked')
                            return FetchResponse(body, str(response.url), response.status_code, dict(response.headers))
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                    if retries >= spec.limits.retries:
                        raise TransportError(f'Source unavailable: {type(exc).__name__}') from exc
                    retry_delay = self._retry_delay(None, spec)
                except httpx.HTTPError as exc:
                    raise TransportError(f'Source unavailable: {type(exc).__name__}') from exc
            if retry_delay is not None:
                retries += 1
                self.sleep(retry_delay)
