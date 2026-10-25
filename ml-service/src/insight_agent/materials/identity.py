"""Semantic event identity, independent from corpus publication and observation times."""
import hashlib
import json

from insight_agent.schemas import NewsEvent, Sha256


def event_content_sha256(event: NewsEvent) -> Sha256:
    payload = event.model_dump(mode='json', include={
        'title', 'text', 'published_at', 'published_date', 'published_precision', 'topics', 'conflicts'})
    payload['topics'] = sorted(set(event.topics))
    payload['sources'] = sorted([
        {'source_id': ref.source_id, 'content_sha256': ref.content_sha256, 'url': ref.url}
        for ref in [event.source, *event.related_sources]
    ], key=lambda ref: ref['source_id'])
    payload['conflicts'] = sorted([
        {'reason': c.reason, 'source_ids': sorted(c.source_ids), 'resolved': c.resolved}
        for c in event.conflicts
    ], key=lambda c: (c['reason'], c['source_ids'], c['resolved']))
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()
