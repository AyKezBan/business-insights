"""T05 collection/publication contracts; no real HTTP requests are permitted."""
from datetime import date, datetime, timedelta, timezone
import fcntl
import importlib
import json
import os
from pathlib import Path

import httpx
import pytest

T = datetime(2026, 10, 24, 10, tzinfo=timezone.utc)
SOURCE_IDS = ("cbr_press", "fns_news", "eia_today")
SOURCE_TEXTS = {
    "cbr_press": "The key rate remains 17 percent.",
    "fns_news": "The tax reporting deadline is 28 October.",
    "eia_today": "Refinery utilization reached 90 percent in September.",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden_request(*args, **kwargs):
        raise AssertionError("Multisource sync tests must not access the network")

    monkeypatch.setattr(httpx.Client, "send", forbidden_request)


@pytest.fixture
def sync_api():
    return importlib.import_module("insight_agent.materials.collector")


@pytest.fixture
def record(sync_api):
    from insight_agent.materials.models import RawNews
    from insight_agent.materials.registry import get_spec

    def make(source_id, article_id="101", *, text=None, retrieved_at=T):
        spec = get_spec(source_id)
        urls = {
            "cbr_press": f"https://www.cbr.ru/press/event/{article_id}/",
            "fns_news": f"https://www.nalog.gov.ru/rn77/news/activities_fts/{article_id}/",
            "eia_today": f"https://www.eia.gov/todayinenergy/detail.php?id={article_id}",
        }
        publication = (
            {"published_at": T, "published_precision": "timestamp"}
            if source_id == "cbr_press"
            else {"published_date": date(2026, 10, 24), "published_precision": "date"}
        )
        return RawNews(
            url=urls[source_id], title=f"{source_id} publication {article_id}",
            text=SOURCE_TEXTS[source_id] if text is None else text,
            retrieved_at=retrieved_at, publisher_id=spec.publisher_id,
            adapter_id=source_id, discovered_url=urls[source_id],
            language=spec.language, material_type=spec.material_type,
            completeness="full", parser_version=spec.parser_version,
            rights_url=spec.rights_url, **publication,
        )

    return make


@pytest.fixture
def batch(sync_api):
    from insight_agent.materials.sources import FetchBatch

    def make(source_id, records=(), *, status="ok", errors=(), checked_at=T, reason=None):
        return FetchBatch(
            source_id=source_id, records=list(records), status=status,
            attempted=len(records) + len(errors), succeeded=len(records),
            rejected=len(errors), item_errors=list(errors), checked_at=checked_at,
            last_success_at=checked_at if records else None, reason=reason,
        )

    return make


@pytest.fixture
def seeded_store(tmp_path, record):
    from insight_agent.materials.dedup import merge_news
    from insight_agent.materials.models import CorpusSnapshot, ImportReport
    from insight_agent.materials.store import publish_snapshot

    root = tmp_path / "store"
    merged = merge_news([], [], [record(source) for source in SOURCE_IDS], observed_at=T)
    snapshot = CorpusSnapshot(
        news=merged.events, source_versions=merged.versions, report=ImportReport(news=3),
    )
    publish_snapshot(root, snapshot)
    return root


def pointer_bytes(root):
    path = root / "current.json"
    return path.read_bytes() if path.exists() else None


def revision_files(root, revision):
    folder = root / "snapshots" / revision
    return {path.relative_to(folder): path.read_bytes() for path in folder.rglob("*") if path.is_file()}


def read_report(result):
    report_path = Path(result.report_path)
    payload = json.loads(report_path.read_text("utf-8"))
    assert isinstance(payload, dict)
    return report_path, payload


def test_three_source_partial_sync_preserves_old_news(sync_api, seeded_store, record, batch):
    from insight_agent.materials.store import load_snapshot

    before = load_snapshot(seeded_store)
    previous_ids = {event.event_id for event in before.news}
    incoming = [
        batch("cbr_press", status="failed", reason="request timeout"),
        batch("fns_news", [record("fns_news", "202", text="VAT filing closes on 30 October.")],
              status="partial", errors=["Article 203: HTTP 404"]),
        batch("eia_today", [record("eia_today", "302", text="Oil production increased by 3 percent.")]),
    ]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    current = load_snapshot(seeded_store)
    assert (result.status, result.exit_code) == ("partial", 3)
    assert result.previous_revision == before.revision
    assert result.published_revision == current.revision != before.revision
    assert previous_ids <= {event.event_id for event in current.news}
    assert len(current.news) == len(current.source_versions) == 5
    assert {version.publisher_id for version in current.source_versions} == {"cbr", "fns", "eia"}
    assert not current.report.errors
    assert not current.report.rejected
    assert current.documents == before.documents
    read_report(result)


@pytest.mark.parametrize("has_previous", [False, True])
def test_all_failed_sources_do_not_clear_or_create_corpus(
    sync_api, seeded_store, tmp_path, batch, has_previous,
):
    root = seeded_store if has_previous else tmp_path / "empty-store"
    before = pointer_bytes(root)
    incoming = [
        batch("cbr_press", status="failed", reason="request timeout"),
        batch("fns_news", status="blocked", reason="HTTP 403"),
        batch("eia_today", status="failed", reason="HTTP 503"),
    ]

    result = sync_api.sync_sources(root, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("failed", 1)
    assert pointer_bytes(root) == before
    read_report(result)


@pytest.mark.parametrize("has_previous", [False, True])
def test_empty_discovery_requires_data_without_publication(
    sync_api, seeded_store, tmp_path, batch, has_previous,
):
    root = seeded_store if has_previous else tmp_path / "empty-store"
    before = pointer_bytes(root)
    incoming = [batch(source, status="empty", reason="No article links") for source in SOURCE_IDS]

    result = sync_api.sync_sources(root, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("needs_review", 2)
    assert pointer_bytes(root) == before
    read_report(result)


def test_unchanged_sources_keep_pointer_exactly(sync_api, seeded_store, batch):
    before = pointer_bytes(seeded_store)
    incoming = [batch(source, status="unchanged", checked_at=T + timedelta(hours=1)) for source in SOURCE_IDS]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("unchanged", 0)
    assert pointer_bytes(seeded_store) == before
    read_report(result)


def test_failed_source_with_unchanged_usable_news_is_partial(sync_api, seeded_store, batch):
    before = pointer_bytes(seeded_store)
    incoming = [
        batch("cbr_press", status="failed", reason="request timeout"),
        batch("fns_news", status="unchanged"), batch("eia_today", status="unchanged"),
    ]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("partial", 3)
    assert pointer_bytes(seeded_store) == before


def test_accepted_records_with_empty_publisher_are_partial(sync_api, seeded_store, record, batch):
    from insight_agent.materials.store import load_snapshot

    incoming = [
        batch("cbr_press", [record("cbr_press", "102", text="The key rate is now 16 percent.")]),
        batch("fns_news", status="empty"), batch("eia_today", status="unchanged"),
    ]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("partial", 3)
    assert len(load_snapshot(seeded_store).news) == 4


def test_exact_batch_replay_keeps_snapshot_and_immutable_prior_report(sync_api, tmp_path, record, batch):
    from insight_agent.materials.store import load_snapshot

    root = tmp_path / "store"
    incoming = [batch(source, [record(source)]) for source in SOURCE_IDS]
    first = sync_api.sync_sources(root, incoming, T)
    assert (first.status, first.exit_code) == ("complete", 0)
    before = load_snapshot(root)
    old_pointer = pointer_bytes(root)
    first_path, _ = read_report(first)
    first_bytes = first_path.read_bytes()

    second = sync_api.sync_sources(root, incoming, T + timedelta(days=1))

    assert (second.status, second.exit_code) == ("unchanged", 0)
    assert load_snapshot(root) == before
    assert pointer_bytes(root) == old_pointer
    second_path, _ = read_report(second)
    assert second_path != first_path
    assert first_path.read_bytes() == first_bytes


def test_new_observation_keeps_event_and_source_identity(sync_api, seeded_store, record, batch):
    from insight_agent.materials.identity import event_content_sha256
    from insight_agent.materials.store import load_snapshot

    before = load_snapshot(seeded_store)
    later = T + timedelta(hours=2)
    incoming = [batch(source, [record(source, retrieved_at=later)], checked_at=later) for source in SOURCE_IDS]

    result = sync_api.sync_sources(seeded_store, incoming, later)

    after = load_snapshot(seeded_store)
    assert result.exit_code == 0
    assert {event.event_id for event in after.news} == {event.event_id for event in before.news}
    assert {version.source_id for version in after.source_versions} == {version.source_id for version in before.source_versions}
    assert len(after.source_versions) == 3
    assert all(version.retrieved_at == later for version in after.source_versions)
    assert {event.event_id: event_content_sha256(event) for event in after.news} == {
        event.event_id: event_content_sha256(event) for event in before.news
    }


@pytest.mark.parametrize("corrected_text", [
    "The key rate remains 18 percent.",
    "The key rate does not remain 17 percent.",
])
def test_source_correction_versions_text_without_replacing_event_id(
    sync_api, seeded_store, record, batch, corrected_text,
):
    from insight_agent.materials.store import load_snapshot

    before = load_snapshot(seeded_store)
    old = next(version for version in before.source_versions if version.publisher_id == "cbr")
    old_files = revision_files(seeded_store, before.revision)
    later = T + timedelta(hours=1)

    result = sync_api.sync_sources(
        seeded_store, [batch("cbr_press", [record("cbr_press", text=corrected_text, retrieved_at=later)])], later,
    )

    after = load_snapshot(seeded_store)
    versions = [version for version in after.source_versions if version.url == old.url]
    assert result.exit_code == 0
    assert len(after.news) == 3
    assert len(versions) == 2
    assert {version.event_id for version in versions} == {old.event_id}
    assert {version.text for version in versions} == {old.text, corrected_text}
    assert sum(version.active for version in versions) == 1
    active = next(version for version in versions if version.active)
    assert active.source_id != old.source_id
    assert active.text == corrected_text
    assert next(event for event in after.news if event.event_id == old.event_id).text == corrected_text
    assert revision_files(seeded_store, before.revision) == old_files


def test_delayed_observation_and_its_replay_do_not_roll_back_current_text(
    sync_api, seeded_store, record, batch,
):
    from insight_agent.materials.store import load_snapshot

    latest = T + timedelta(hours=2)
    sync_api.sync_sources(seeded_store, [batch("cbr_press", [record(
        "cbr_press", text="The key rate remains 18 percent.", retrieved_at=latest,
    )])], latest)
    delayed = [batch("cbr_press", [record(
        "cbr_press", text="The key rate remains 16 percent.", retrieved_at=T + timedelta(hours=1),
    )])]

    first = sync_api.sync_sources(seeded_store, delayed, T + timedelta(hours=3))
    after_delayed = load_snapshot(seeded_store)
    second = sync_api.sync_sources(seeded_store, delayed, T + timedelta(hours=4))

    current = load_snapshot(seeded_store)
    assert first.exit_code == second.exit_code == 0
    assert (second.status, second.exit_code) == ("unchanged", 0)
    assert current == after_delayed
    versions = [version for version in current.source_versions if version.publisher_id == "cbr"]
    assert len(versions) == 3
    assert next(version for version in versions if version.active).text == "The key rate remains 18 percent."
    assert len(current.news) == 3


def test_sync_reloads_bank_publication_made_before_its_lock(
    sync_api, seeded_store, tmp_path, monkeypatch, record, batch,
):
    from insight_agent.materials.bank_corpus import prepare_bank
    from insight_agent.materials.cli import corpus_lock
    from insight_agent.materials.store import load_snapshot, publish_snapshot
    from test_pdf_extraction import FakeOCR, make_pdf

    inputs = tmp_path / "bank-inputs"
    inputs.mkdir()
    metadata = make_pdf(inputs / "bank.pdf", "Bank expects financing costs to remain high.")
    manifest = tmp_path / "bank-manifest.json"
    manifest.write_text(json.dumps({"documents": [metadata.model_dump(mode="json")]}), encoding="utf-8")
    bank = prepare_bank(inputs, manifest, seeded_store, FakeOCR())
    assert bank.documents and bank.pages and bank.bank_fragments
    real_flock = fcntl.flock
    inserted = []

    def competing_writer_before_acquire(lock, operation):
        if operation == fcntl.LOCK_EX and not inserted:
            # Enter a real competing writer before the sync obtains its lock.
            # Mark first so the legacy lock's nested flock call does not recurse.
            inserted.append(None)
            with corpus_lock(seeded_store):
                current = load_snapshot(seeded_store)
                candidate = bank.model_copy(deep=True)
                candidate.news = current.news
                candidate.source_versions = current.source_versions
                candidate.report.news = len(current.news)
                inserted[0] = publish_snapshot(seeded_store, candidate)
        return real_flock(lock, operation)

    monkeypatch.setattr(fcntl, "flock", competing_writer_before_acquire)
    incoming = [batch("eia_today", [record("eia_today", "302", text="Oil production increased by 3 percent.")])]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    current = load_snapshot(seeded_store)
    assert len(inserted) == 1 and inserted[0]
    assert result.previous_revision == inserted[0]
    assert result.exit_code == 0
    assert current.documents == bank.documents
    assert current.pages == bank.pages
    assert current.bank_fragments == bank.bank_fragments
    assert len(current.news) == 4


def test_failed_pointer_swap_retains_previous_complete_snapshot(
    sync_api, seeded_store, monkeypatch, record, batch,
):
    from insight_agent.materials.store import load_snapshot

    before = load_snapshot(seeded_store)
    old_pointer = pointer_bytes(seeded_store)
    old_files = revision_files(seeded_store, before.revision)
    real_replace = os.replace

    def fail_current_pointer(source, destination, *args, **kwargs):
        if Path(destination) == seeded_store / "current.json":
            raise OSError("Injected pointer replacement failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_current_pointer)
    incoming = [batch("cbr_press", [record("cbr_press", "102", text="The key rate is now 16 percent.")])]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("failed", 1)
    assert pointer_bytes(seeded_store) == old_pointer
    assert load_snapshot(seeded_store) == before
    assert revision_files(seeded_store, before.revision) == old_files


def test_final_report_pointer_failure_reports_committed_revision_without_rollback(
    sync_api, seeded_store, monkeypatch, record, batch,
):
    from insight_agent.materials.store import load_snapshot

    unchanged = [batch(source, status="unchanged") for source in SOURCE_IDS]
    previous_run = sync_api.sync_sources(seeded_store, unchanged, T)
    old_report, _ = read_report(previous_run)
    old_report_bytes = old_report.read_bytes()
    last_attempt = seeded_store / "last_attempt.json"
    previous_attempt = last_attempt.read_bytes()
    before = load_snapshot(seeded_store)
    old_files = revision_files(seeded_store, before.revision)
    real_replace = os.replace

    def fail_attempt_pointer(source, destination, *args, **kwargs):
        if Path(destination) == last_attempt:
            raise OSError("Injected last-attempt replacement failure")
        return real_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_attempt_pointer)
    incoming = [batch("cbr_press", [record("cbr_press", "102", text="The key rate is now 16 percent.")])]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    current = load_snapshot(seeded_store)
    assert (result.status, result.exit_code) == ("failed", 1)
    assert current.revision != before.revision
    assert result.published_revision == current.revision
    assert result.previous_revision == before.revision
    assert len(current.news) == 4
    assert last_attempt.read_bytes() == previous_attempt
    assert old_report.read_bytes() == old_report_bytes
    assert revision_files(seeded_store, before.revision) == old_files


def test_corrupt_current_corpus_is_fatal_even_with_good_new_records(
    sync_api, seeded_store, record, batch,
):
    from insight_agent.materials.store import load_snapshot

    before = load_snapshot(seeded_store)
    old_pointer = pointer_bytes(seeded_store)
    (seeded_store / "snapshots" / before.revision / "news.jsonl").write_text("corrupt", encoding="utf-8")
    incoming = [batch("eia_today", [record("eia_today", "302", text="Oil production increased by 3 percent.")])]

    result = sync_api.sync_sources(seeded_store, incoming, T + timedelta(hours=1))

    assert (result.status, result.exit_code) == ("failed", 1)
    assert pointer_bytes(seeded_store) == old_pointer


def test_collect_cbr_preserves_good_items_after_an_article_404(sync_api, monkeypatch):
    from insight_agent.materials.registry import get_spec
    from insight_agent.materials.transport import ControlledTransport, FetchResponse, TransportError

    spec = get_spec("cbr_press")
    missing_url = "https://www.cbr.ru/press/event/201/"
    good_url = "https://www.cbr.ru/press/event/202/"
    rss = (
        f"<rss><channel><item><title>Missing release</title><link>{missing_url}</link></item>"
        f"<item><title>Rate decision</title><link>{good_url}</link></item></channel></rss>"
    ).encode()

    def fake_get(self, url, *, spec, headers=None, params=None):
        if url == spec.discovery_url:
            return FetchResponse(rss, url, 200, {})
        if url == missing_url:
            raise TransportError("HTTP 404")
        assert url == good_url
        return FetchResponse(b'<div class="landing-text">The rate was not reduced: 17 percent.</div>', url, 200, {})

    monkeypatch.setattr(ControlledTransport, "get", fake_get)

    batches = sync_api.collect_sources([spec], 2)

    assert len(batches) == 1
    collected = batches[0]
    assert collected.source_id == "cbr_press"
    assert collected.status == "partial"
    assert [item.url for item in collected.records] == [good_url]
    assert "not reduced: 17" in collected.records[0].text
    assert (collected.attempted, collected.succeeded, collected.rejected) == (2, 1, 1)
    assert collected.item_errors


def test_collect_isolates_each_source_failure(sync_api, monkeypatch):
    from insight_agent.materials.registry import get_spec
    from insight_agent.materials.transport import ControlledTransport, TransportError

    def fake_get(self, url, *, spec, headers=None, params=None):
        if spec.source_id == "fns_news":
            raise TransportError("HTTP 403", status="blocked")
        raise TransportError("request timeout")

    monkeypatch.setattr(ControlledTransport, "get", fake_get)

    batches = sync_api.collect_sources([get_spec(source) for source in SOURCE_IDS], 2)

    assert {item.source_id: item.status for item in batches} == {
        "cbr_press": "failed", "fns_news": "blocked", "eia_today": "failed",
    }
    assert all(not item.records for item in batches)
    assert all(item.reason or item.item_errors for item in batches)


def test_report_health_counts_last_success_and_fatal_errors_are_independent(sync_api, tmp_path, record, batch):
    from insight_agent.materials.store import load_snapshot
    root = tmp_path / 'store'
    first = sync_api.sync_sources(root, [batch(source, [record(source)]) for source in SOURCE_IDS], T)
    _, previous = read_report(first)
    later = T + timedelta(hours=1)
    incoming = [batch('cbr_press', status='failed', checked_at=later, reason='timeout'),
                batch('fns_news', [record('fns_news', '202', text='The new deadline is 30 October.')],
                      status='partial', errors=['https://example.test/private?token=secret: HTTP 404'], checked_at=later),
                batch('eia_today', status='unchanged', checked_at=later)]
    result = sync_api.sync_sources(root, incoming, later)
    _, report = read_report(result)
    assert report['health'] == 'degraded' and report['fatal_errors'] == []
    assert report['sources']['cbr_press']['health'] == 'failed'
    assert report['sources']['cbr_press']['last_success_at'] == previous['sources']['cbr_press']['last_success_at']
    fns = report['sources']['fns_news']
    assert (fns['attempted'], fns['succeeded'], fns['rejected'], fns['active_records']) == (2, 1, 1, 2)
    assert fns['health'] == 'degraded' and fns['item_errors']
    assert 'secret' not in json.dumps(report)
    assert report['sources']['eia_today']['health'] == 'healthy'
    assert load_snapshot(root).report.errors == []


def test_report_preparation_failure_blocks_publication(sync_api, seeded_store, monkeypatch, record, batch):
    from insight_agent.materials.store import load_snapshot
    before = pointer_bytes(seeded_store)
    def fail_stage(*args):
        raise OSError('report serialization/staging failed')
    monkeypatch.setattr(sync_api, '_stage_report', fail_stage)
    result = sync_api.sync_sources(seeded_store, [batch('cbr_press', [record('cbr_press', '102')])], T)
    assert (result.status, result.exit_code) == ('failed', 1)
    assert result.published_revision is None
    assert pointer_bytes(seeded_store) == before
    assert result.current_revision == load_snapshot(seeded_store).revision


def test_postcommit_directory_fsync_failure_reports_the_new_head(sync_api, seeded_store, monkeypatch, record, batch):
    from insight_agent.materials import store
    before = pointer_bytes(seeded_store)
    original = store._fsync_directory
    triggered = []
    def fail_after_pointer(path):
        if Path(path) == seeded_store.resolve() and pointer_bytes(seeded_store) != before and not triggered:
            triggered.append(True)
            raise OSError('directory fsync failed after replace')
        return original(path)
    monkeypatch.setattr(store, '_fsync_directory', fail_after_pointer)
    result = sync_api.sync_sources(seeded_store, [batch('cbr_press', [record('cbr_press', '102')])], T)
    assert triggered and (result.status, result.exit_code) == ('failed', 1)
    assert result.published_revision == store.load_snapshot(seeded_store).revision
    assert result.catalog_token == store.load_catalog_token(seeded_store)
    assert result.published_revision != result.previous_revision


def test_cli_collects_before_lock_and_prints_source_health(sync_api, seeded_store, monkeypatch, capsys, record, batch):
    from insight_agent.materials.cli import main
    incoming = [batch('cbr_press', [record('cbr_press', '102')]), batch('fns_news', status='failed', reason='timeout')]
    def collect_without_lock(specs, limit_per_source, *, cache_dir):
        assert [spec.source_id for spec in specs] == ['cbr_press', 'fns_news']
        assert limit_per_source == 2 and cache_dir == seeded_store / 'http-cache'
        with (seeded_store / '.writer.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_UN)
        return incoming
    monkeypatch.setattr(sync_api, 'collect_sources', collect_without_lock)
    code = main(['sync', '--sources', 'cbr_press,fns_news', '--limit-per-source', '2', '--store', str(seeded_store)])
    output = json.loads(capsys.readouterr().out)
    assert code == 3 and output['status'] == 'partial'
    assert output['health'] == 'degraded'
    assert output['sources']['fns_news']['health'] == 'failed'
    assert output['published_revision'] != output['previous_revision']


@pytest.mark.parametrize('sources,limit', [('unknown', '1'), ('cbr_press', '0'), ('cbr_press,cbr_press', '1')])
def test_cli_rejects_unknown_sources_and_invalid_limits_without_publication(sync_api, tmp_path, sources, limit):
    from insight_agent.materials.cli import main
    assert main(['sync', '--sources', sources, '--limit-per-source', limit, '--store', str(tmp_path)]) == 1
    assert not (tmp_path / 'current.json').exists()


def test_unchanged_without_prior_source_data_needs_review(sync_api, tmp_path, batch):
    result = sync_api.sync_sources(tmp_path, [batch('cbr_press', status='unchanged')], T)
    assert (result.status, result.exit_code) == ('needs_review', 2)
    _, report = read_report(result)
    assert report['sources']['cbr_press']['health'] == 'degraded'
    assert not (tmp_path / 'current.json').exists()


def test_delayed_metadata_correction_preserves_history_without_rollback(sync_api, seeded_store, record, batch):
    from insight_agent.materials.store import load_snapshot
    latest = record('cbr_press', retrieved_at=T + timedelta(hours=2)).model_copy(update={'title': 'Latest title'})
    sync_api.sync_sources(seeded_store, [batch('cbr_press', [latest])], T + timedelta(hours=2))
    delayed = record('cbr_press', retrieved_at=T + timedelta(hours=1)).model_copy(update={'title': 'Intermediate title'})
    for hour in (3, 4):
        result = sync_api.sync_sources(seeded_store, [batch('cbr_press', [delayed])], T + timedelta(hours=hour))
        assert result.exit_code == 0
    versions = [v for v in load_snapshot(seeded_store).source_versions if v.adapter_id == 'cbr_press']
    assert len(versions) == 3
    assert {v.title for v in versions} == {'cbr_press publication 101', 'Intermediate title', 'Latest title'}
    assert next(v for v in versions if v.active).title == 'Latest title'


def test_failed_final_report_file_reports_committed_head(sync_api, seeded_store, monkeypatch, record, batch):
    from insight_agent.materials.store import load_catalog_token
    before = load_catalog_token(seeded_store)
    def fail_report_file(*args):
        raise OSError('cannot write final report')
    monkeypatch.setattr(sync_api, 'write_immutable', fail_report_file)
    result = sync_api.sync_sources(seeded_store, [batch('cbr_press', [record('cbr_press', '102')])], T)
    assert (result.status, result.exit_code) == ('failed', 1)
    assert result.catalog_token == load_catalog_token(seeded_store)
    assert result.catalog_token.generation == before.generation + 1
    assert result.published_revision == result.catalog_token.revision
    assert result.report_path is None


@pytest.mark.parametrize('status', ['empty', 'partial'])
def test_no_usable_records_never_publish_a_review_only_corpus(sync_api, tmp_path, batch, status):
    batches = [batch('cbr_press', status=status, errors=['selector missing'] if status == 'partial' else [])]
    result = sync_api.sync_sources(tmp_path, batches, T)
    assert (result.status, result.exit_code) == ('needs_review', 2)
    assert result.sources['cbr_press'].health == 'degraded'
    assert not (tmp_path / 'current.json').exists()


def test_failed_first_publication_reports_zero_available_records(sync_api, tmp_path, monkeypatch, record, batch):
    replace = os.replace
    def fail_current(source, destination):
        if Path(destination) == tmp_path / 'current.json':
            raise OSError('first pointer cannot be written')
        return replace(source, destination)
    monkeypatch.setattr(os, 'replace', fail_current)
    result = sync_api.sync_sources(tmp_path, [batch('cbr_press', [record('cbr_press')])], T)
    assert result.exit_code == 1 and result.current_revision is None
    assert result.sources['cbr_press'].succeeded == 1
    assert result.sources['cbr_press'].active_records == 0
    assert result.sources['cbr_press'].source_version_ids == []


def test_postcommit_legacy_token_initialization_reports_publication_even_at_same_revision(
    sync_api, seeded_store, monkeypatch, record, batch,
):
    from insight_agent.materials import store
    revision = store.load_snapshot(seeded_store).revision
    (seeded_store / 'store.json').unlink()
    (seeded_store / 'current.json').write_text(json.dumps({'revision': revision}))
    original = store._fsync_directory
    failed = []
    def fail_after_initialization(path):
        pointer = json.loads((seeded_store / 'current.json').read_text())
        if Path(path) == seeded_store.resolve() and pointer.get('generation') == 1 and not failed:
            failed.append(True)
            raise OSError('fsync after token initialization')
        return original(path)
    monkeypatch.setattr(store, '_fsync_directory', fail_after_initialization)
    incoming = [batch(source, [record(source)]) for source in SOURCE_IDS]
    result = sync_api.sync_sources(seeded_store, incoming, T)
    assert failed and result.exit_code == 1
    assert result.current_revision == result.previous_revision == revision
    assert result.catalog_token.generation == 1 and result.previous_token.generation == 0
    assert result.published_revision == revision
