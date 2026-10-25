"""CBR collection isolates item failures; the old fetch API remains all-or-error."""
from datetime import UTC, datetime

from insight_agent.materials.models import RawNews
from insight_agent.materials.news import extract_cbr_article, parse_rss
from insight_agent.materials.sources import FetchBatch, SourceSpec
from insight_agent.materials.transport import TransportError, check_url


class CbrNewsSource:
    def __init__(self, transport, spec: SourceSpec):
        self.transport, self.spec = transport, spec

    def collect(self, limit: int) -> FetchBatch:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError('News limit must be between 1 and 100')
        now = datetime.now(UTC)
        batch = FetchBatch(source_id=self.spec.source_id, status='empty', checked_at=now)
        try:
            response = self.transport.get(self.spec.discovery_url, spec=self.spec)
            batch.etag, batch.last_modified = response.headers.get('etag'), response.headers.get('last-modified')
            if response.status_code == 304:
                batch.status = 'unchanged'
                return batch
            articles = parse_rss(response.body)
        except (TransportError, ValueError) as exc:
            batch.status = exc.status if isinstance(exc, TransportError) else 'failed'
            batch.reason = str(exc)
            batch.item_errors = [str(exc)]
            return batch
        seen, failures = set(), []
        for item in articles:
            if item['url'] in seen:
                continue
            if batch.attempted == limit:
                break
            seen.add(item['url'])
            batch.attempted += 1
            try:
                check_url(item['url'], self.spec)
                response = self.transport.get(item['url'], spec=self.spec)
                check_url(response.url, self.spec)
                if response.status_code == 304:
                    continue
                body = response.body.decode('utf-8-sig')
                batch.records.append(RawNews(**{**item, 'url': response.url},
                    text=extract_cbr_article(body), raw_body=body, retrieved_at=now,
                    publisher_id=self.spec.publisher_id, adapter_id=self.spec.source_id,
                    discovered_url=item['url'], parser_version=self.spec.parser_version,
                    language=self.spec.language, material_type=self.spec.material_type,
                    rights_url=self.spec.rights_url))
            except (TransportError, ValueError) as exc:
                batch.rejected += 1
                batch.item_errors.append(f'{item["url"]}: {exc}')
                failures.append(exc.status if isinstance(exc, TransportError) else 'failed')
        batch.succeeded = len(batch.records)
        if batch.records:
            batch.status = 'partial' if failures else 'ok'
            batch.last_success_at = now
        elif failures:
            batch.status = 'blocked' if all(x == 'blocked' for x in failures) else 'failed'
        elif batch.attempted:
            batch.status = 'unchanged'
        else:
            batch.reason = 'CBR feed contains no eligible articles'
        return batch
