"""Regression contracts: v1 bytes precede migration; metadata preserves source meaning."""
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from pydantic import ValidationError

from insight_agent.materials.models import CorpusSnapshot, RawNews
from insight_agent.materials.dedup import merge_news
from insight_agent.materials.news import read_saved_news
from insight_agent.materials.store import load_snapshot, publish_snapshot

FIXTURES = Path(__file__).parent / 'fixtures/materials'
NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


def test_v1_hash_and_date_precision():
    old = load_snapshot(FIXTURES / 'v1')
    assert old.revision == '90c4a7b7b178db0bbc598b7962e5d1f8796b698c61329c504325c7d7a2486383'
    assert old.news[0].event_id == 'evt-c4e9b2e2a197f7511c5c76cb'
    assert old.source_versions[0].content_sha256 == 'fcafe7a8737337650938feeae63bc9f29419e8a25db2d4999ad18715dc264fd5'
    assert old.source_versions[0].published_precision == 'timestamp'
    assert old.source_versions[0].published_date == date(2026, 10, 24)


def test_old_saved_batch_loads_without_new_fields():
    record, = read_saved_news(FIXTURES / 'saved-v1.json')
    assert record.published_precision == 'timestamp'
    assert record.publisher_id is None


def test_source_day_and_original_body_survive_merge_and_roundtrip(tmp_path):
    raw = RawNews(url='https://www.eia.gov/todayinenergy/detail.php?id=123', title='Refinery update',
        text='Original  body\nwith  spacing.', retrieved_at=NOW, published_date=date(2026, 10, 24),
        publisher_id='eia', adapter_id='eia_today', language='en', material_type='public_analysis',
        parser_version='eia-v1', rights_url='https://www.eia.gov/about/copyrights_reuse.php',
        raw_publication='October 24, 2026', topics=['energy', 'production_capacity'])
    merged = merge_news([], [], [raw], observed_at=NOW)
    version, = merged.versions
    event, = merged.events
    assert version.original_text == raw.text
    assert version.text == 'Original body with spacing.'
    assert event.published_at is None
    assert event.published_date == event.source.date == date(2026, 10, 24)
    assert event.published_precision == 'date'
    assert event.source.publisher_id == 'eia'
    assert event.source.material_type == 'public_analysis'
    assert event.source.retrieved_at == NOW
    assert event.topics == ['energy', 'production_capacity']
    snapshot = CorpusSnapshot(news=merged.events, source_versions=merged.versions)
    publish_snapshot(tmp_path, snapshot)
    restored = load_snapshot(tmp_path)
    assert restored.schema_version == 2
    assert restored.news == merged.events
    assert restored.source_versions == merged.versions


def test_publication_precision_rejects_invented_time_and_mismatch():
    base = dict(url='https://www.cbr.ru/press/x', title='x', text='body', retrieved_at=NOW)
    with pytest.raises(ValidationError):
        RawNews(**base, published_at='2026-10-26T12:00:00')
    with pytest.raises(ValidationError):
        RawNews(**base, published_at=NOW, published_precision='date')
    with pytest.raises(ValidationError):
        RawNews(**base, published_at=NOW, published_date='2026-10-25')
    raw = RawNews(**base, published_at='2026-10-26T00:30:00+03:00')
    assert raw.published_date == date(2026, 10, 26)
    assert raw.published_precision == 'timestamp'
    assert RawNews(**base).published_precision == 'unknown'


def test_metadata_correction_creates_version_but_fresh_observation_does_not():
    raw = RawNews(url='https://www.eia.gov/todayinenergy/detail.php?id=1', title='x', text='body',
                  retrieved_at=NOW, published_date=date(2026, 10, 24), topics=['energy'])
    first = merge_news([], [], [raw], observed_at=NOW)
    fresh = merge_news(first.events, first.versions, [raw.model_copy(update={'retrieved_at': NOW + timedelta(hours=1)})], observed_at=NOW)
    assert len(fresh.versions) == 1
    fixed = merge_news(fresh.events, fresh.versions, [raw.model_copy(update={'published_date': date(2026,10,23), 'retrieved_at': NOW+timedelta(hours=2)})], observed_at=NOW)
    assert len(fixed.versions) == 2
    assert fixed.events[0].published_date == date(2026,10,23)
    assert fixed.events[0].event_id == first.events[0].event_id


def test_legacy_hash_verified_before_defaults_and_v2_publish_preserves_v1(tmp_path):
    root = tmp_path / 'store'
    shutil.copytree(FIXTURES / 'v1', root)
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    old = load_snapshot(root)
    new_revision = publish_snapshot(root, old)
    assert new_revision != old.revision
    assert load_snapshot(root).schema_version == 2
    for name, content in before.items():
        if name != 'current.json':
            assert (root / name).read_bytes() == content
    # An attacker can recompute a collection checksum, but not the revision address.
    folder = root / 'snapshots' / old.revision
    collection = folder / 'news.jsonl'
    collection.write_bytes(collection.read_bytes().replace(b'Legacy release', b'Altered title'))
    manifest = json.loads((folder / 'manifest.json').read_text())
    manifest['files']['news.jsonl'] = hashlib.sha256(collection.read_bytes()).hexdigest()
    (folder / 'manifest.json').write_text(json.dumps(manifest))
    (root / 'current.json').write_text(json.dumps({'revision': old.revision}))
    with pytest.raises(ValueError, match='manifest hash'):
        load_snapshot(root)


def test_event_identity_excludes_observation_but_tracks_publication_and_conflicts():
    from insight_agent.materials.identity import event_content_sha256
    raw = RawNews(url='https://www.cbr.ru/press/a', title='x', text='body', retrieved_at=NOW)
    event = merge_news([], [], [raw], observed_at=NOW).events[0]
    same = event.model_copy(deep=True)
    same.source.retrieved_at = NOW + timedelta(days=1)
    same.source.first_seen_at = NOW + timedelta(days=1)
    assert event_content_sha256(same) == event_content_sha256(event)
    same.published_date = date(2026,10,25)
    assert event_content_sha256(same) != event_content_sha256(event)


def test_catalog_token_rejects_invalid_revision_and_negative_generation():
    from insight_agent.materials.models import CatalogToken
    with pytest.raises(ValidationError):
        CatalogToken(store_id='store', generation=-1, revision='a'*64)
    with pytest.raises(ValidationError):
        CatalogToken(store_id='store', generation=0, revision='invalid')


def test_event_identity_tracks_source_versions_topics_and_conflicts():
    from insight_agent.materials.identity import event_content_sha256
    from insight_agent.schemas import SourceConflict
    raw = RawNews(url='https://www.cbr.ru/press/a', title='x', text='body', retrieved_at=NOW)
    event = merge_news([], [], [raw], observed_at=NOW).events[0]
    original_hash = event_content_sha256(event)
    for field, value in [('title', 'Corrected title'), ('text', 'Corrected body'), ('topics', ['tax'])]:
        assert event_content_sha256(event.model_copy(update={field:value})) != original_hash
    changed = event.model_copy(deep=True)
    changed.source.content_sha256 = 'a'*64
    assert event_content_sha256(changed) != original_hash
    changed = event.model_copy(update={'conflicts':[SourceConflict(reason='disagreement', source_ids=[event.source.source_id])]})
    assert event_content_sha256(changed) != original_hash


def test_exact_saved_batch_replay_keeps_v2_revision_and_ids(tmp_path):
    record, = read_saved_news(FIXTURES / 'saved-v1.json')
    first = merge_news([], [], [record], observed_at=NOW)
    revision = publish_snapshot(tmp_path, CorpusSnapshot(news=first.events, source_versions=first.versions))
    current = load_snapshot(tmp_path)
    repeated = merge_news(current.news, current.source_versions, [record], observed_at=NOW + timedelta(days=1))
    again = publish_snapshot(tmp_path, CorpusSnapshot(news=repeated.events, source_versions=repeated.versions))
    assert again == revision
    assert repeated.versions[0].source_id == first.versions[0].source_id
    assert repeated.events[0].event_id == first.events[0].event_id


def test_event_cannot_claim_day_precision_while_carrying_exact_timestamp():
    from insight_agent.schemas import NewsEvent, SourceRef
    base = dict(event_id='event', title='x', text='body', source=SourceRef(source_id='s', kind='news', title='x'))
    with pytest.raises(ValidationError, match='precision'):
        NewsEvent(**base, published_at=NOW, published_date=NOW.date(), published_precision='date')
    with pytest.raises(ValidationError, match='precision'):
        NewsEvent(**base, published_precision='timestamp')
    # Old callers do not need to send precision.
    assert NewsEvent(**base, published_at=NOW).published_at == NOW


def test_source_timezone_provenance_requires_named_timezone():
    with pytest.raises(ValidationError, match='timezone'):
        RawNews(url='https://www.cbr.ru/press/a', title='x', text='body', retrieved_at=NOW,
                published_at=NOW, timezone_provenance='source_policy')
