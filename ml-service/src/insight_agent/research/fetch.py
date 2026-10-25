"""Безопасная загрузка из открытых официальных источников: только https, только список разрешённых хостов."""
from urllib.parse import urljoin, urlsplit

import httpx

ALLOWED_HOSTS = frozenset({"cbr.ru", "www.cbr.ru"})
MAX_BYTES = 2 * 1024 * 1024
USER_AGENT = "BusinessInsightsResearch/1.0"


class OpenSourceError(RuntimeError):
    pass


class SafeFetcher:
    def __init__(self, client: httpx.Client, hosts: frozenset[str] = ALLOWED_HOSTS, max_bytes: int = MAX_BYTES,
                 timeout: float = 10.0):
        self.client, self.hosts, self.max_bytes, self.timeout = client, hosts, max_bytes, timeout

    def _check(self, url: str) -> None:
        parts = urlsplit(url)
        if (parts.scheme != "https" or parts.hostname not in self.hosts or parts.username or parts.password
                or parts.port not in (None, 443)):
            raise OpenSourceError(f"URL вне списка разрешённых источников: {parts.scheme}://{parts.hostname}")

    def get(self, url: str, params: dict[str, str] | None = None) -> bytes:
        if params:
            url = str(httpx.URL(url).copy_merge_params(params))
        for _ in range(4):
            self._check(url)
            try:
                with self.client.stream("GET", url, timeout=self.timeout, follow_redirects=False,
                                        headers={"User-Agent": USER_AGENT}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    chunks, total = [], 0
                    for chunk in response.iter_bytes():
                        total += len(chunk)
                        if total > self.max_bytes:
                            raise OpenSourceError("Превышен допустимый размер ответа источника")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except httpx.HTTPError as exc:
                raise OpenSourceError(f"Источник недоступен: {type(exc).__name__}") from exc
        raise OpenSourceError("Слишком много перенаправлений")
