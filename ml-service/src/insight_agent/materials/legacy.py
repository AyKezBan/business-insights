"""Read-only v1 migration and a batch adapter for the original fetch protocol."""
from copy import deepcopy
from datetime import UTC, datetime
from typing import Callable

from insight_agent.materials.sources import FetchBatch, NewsSource, SourceSpec
from insight_agent.materials.transport import TransportError


def migrate_snapshot(payload: dict) -> dict:
    """Called only after the original canonical JSON has passed integrity checks."""
    if payload.get('schema_version') != 1:
        return payload
    migrated = deepcopy(payload)
    for event in migrated.get('news', []):
        if event.get('published_at'):
            event.setdefault('published_date', datetime.fromisoformat(event['published_at']).date().isoformat())
            event.setdefault('published_precision', 'timestamp')
    return migrated


class LegacyNewsSourceAdapter:
    def __init__(self, source: NewsSource, spec: SourceSpec, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.source, self.spec, self.clock = source, spec, clock

    def collect(self, limit: int) -> FetchBatch:
        if not 1 <= limit <= 100:
            raise ValueError('News limit must be between 1 and 100')
        checked_at = self.clock()
        try:
            records = self.source.fetch(limit)
        except (TransportError, ValueError) as exc:
            cause = exc if isinstance(exc, TransportError) else exc.__cause__
            return FetchBatch(source_id=self.spec.source_id, status=getattr(cause, 'status', 'failed'),
                checked_at=checked_at, item_errors=[str(exc)], reason=type(exc).__name__)
        # Legacy transports can expose httpx exceptions; preserve failure, never turn it into empty.
        except Exception as exc:
            import httpx
            if not isinstance(exc, httpx.HTTPError):
                raise
            return FetchBatch(source_id=self.spec.source_id, status='failed', checked_at=checked_at,
                              item_errors=[str(exc)], reason=type(exc).__name__)
        enriched = [raw.model_copy(update={
            'publisher_id': raw.publisher_id or self.spec.publisher_id,
            'adapter_id': raw.adapter_id or self.spec.source_id,
            'discovered_url': raw.discovered_url or raw.url,
            'parser_version': raw.parser_version or self.spec.parser_version,
            'language': raw.language or self.spec.language,
            'rights_url': raw.rights_url or self.spec.rights_url,
        }) for raw in records]
        return FetchBatch(source_id=self.spec.source_id, records=enriched,
                          status='ok' if enriched else 'empty', checked_at=checked_at,
                          last_success_at=checked_at if enriched else None,
                          attempted=len(enriched), succeeded=len(enriched))
