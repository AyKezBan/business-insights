"""Collect outside the writer transaction; publish once with an independent run report."""
from datetime import UTC, datetime
import json
from pathlib import Path
import re
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import AwareDatetime, BaseModel, Field, TypeAdapter

from insight_agent.materials.artifacts import digest, safe_path, write_immutable
from insight_agent.materials.dedup import merge_news
from insight_agent.materials.http_cache import ConditionalTransport
from insight_agent.materials.models import CatalogToken, CorpusSnapshot
from insight_agent.materials.registry import get_source, get_spec
from insight_agent.materials.sources import FetchBatch, SourceSpec
from insight_agent.materials import store as storage
from insight_agent.materials.transport import ControlledTransport, TransportError, check_url

SyncStatus = Literal['complete', 'unchanged', 'partial', 'failed', 'needs_review']
Health = Literal['healthy', 'degraded', 'failed']


class SourceReport(BaseModel):
    source_id: str
    publisher_id: str
    status: str
    health: Health
    checked_at: AwareDatetime
    last_success_at: AwareDatetime | None = None
    attempted: int = 0
    succeeded: int = 0
    rejected: int = 0
    item_errors: list[str] = Field(default_factory=list)
    reason: str | None = None
    active_records: int = 0
    source_version_ids: list[str] = Field(default_factory=list)
    content_hashes: list[str] = Field(default_factory=list)


class SyncResult(BaseModel):
    status: SyncStatus
    exit_code: int
    health: Health
    requested_sources: list[str] = Field(default_factory=list)
    sources: dict[str, SourceReport] = Field(default_factory=dict)
    published_revision: str | None = None
    previous_revision: str | None = None
    current_revision: str | None = None
    catalog_token: CatalogToken | None = None
    previous_token: CatalogToken | None = None
    report_path: Path | None = None
    run_id: str
    fatal_errors: list[str] = Field(default_factory=list)


class RunReport(SyncResult):
    phase: Literal['prepared', 'final'] = 'prepared'
    observed_at: AwareDatetime
    related_run_id: str | None = None


def _diagnostic(value: str) -> str:
    # Adapter errors can contain URLs. Never copy response bodies or credential URLs.
    return re.sub(r'https?://\S+', '[source URL]', ' '.join(value.split()))[:300]


def collect_sources(specs: list[SourceSpec], limit_per_source: int, *,
                    transport: ControlledTransport | None = None,
                    cache_dir: Path | None = None) -> list[FetchBatch]:
    """One shared transport; fixed registry policies and independent source failures."""
    if isinstance(limit_per_source, bool) or not isinstance(limit_per_source, int) or not 1 <= limit_per_source <= 100:
        raise ValueError('News limit must be between 1 and 100')
    if not specs or len({spec.source_id for spec in specs}) != len(specs):
        raise ValueError('Select unique registry sources')
    for spec in specs:
        if spec != get_spec(spec.source_id):
            raise ValueError('Source policy must match the fixed registry')
    if transport is None:
        with httpx.Client() as client:
            return collect_sources(specs, limit_per_source,
                                   transport=ControlledTransport(client), cache_dir=cache_dir)
    cached = ConditionalTransport(transport, cache_dir)
    batches = []
    for spec in specs:
        try:
            batch = get_source(spec.source_id, cached).collect(limit_per_source)
            for record in batch.records:
                observation = cached.observations.get((spec.source_id, record.discovered_url or record.url))
                if observation is not None:
                    record.retrieved_at = observation.retrieved_at
            observations = [value for (source, _), value in cached.observations.items() if source == spec.source_id]
            if batch.status == 'ok' and observations and all(value.unchanged for value in observations):
                batch.status = 'unchanged'
        except (OSError, ValueError, TransportError, httpx.HTTPError) as exc:
            batch = FetchBatch(source_id=spec.source_id,
                               status=exc.status if isinstance(exc, TransportError) else 'failed',
                               checked_at=datetime.now(UTC), reason=f'Collection failed: {type(exc).__name__}')
        batch.reason = _diagnostic(batch.reason) if batch.reason else None
        batch.item_errors = [_diagnostic(error) for error in batch.item_errors]
        batches.append(batch)
    return batches


def _last_sources(root: Path) -> dict[str, SourceReport]:
    pointer = root / 'last_attempt.json'
    if not pointer.exists():
        return {}
    value = json.loads(pointer.read_text('utf-8'))
    if value['report_path'] != f'runs/{value["run_id"]}.json':
        raise ValueError('Invalid last-attempt report path')
    data = safe_path(root, value['report_path']).read_bytes()
    if digest(data) != value['sha256']:
        raise ValueError('Run report checksum mismatch')
    report = RunReport.model_validate_json(data)
    if report.run_id != value['run_id'] or report.phase != 'final':
        raise ValueError('Invalid last-attempt report')
    return report.sources


def _stage_report(root: Path, report: RunReport) -> Path:
    path = root / 'runs' / f'.{report.run_id}.pending'
    storage.atomic_json(path, report.model_dump(mode='json'))
    return path


def _finish_report(root: Path, report: RunReport) -> Path:
    report.phase = 'final'
    path = root / 'runs' / f'{report.run_id}.json'
    report.report_path = path
    data = storage._json_bytes(report.model_dump(mode='json'))
    write_immutable(root, f'runs/{report.run_id}.json', data)
    storage._fsync_directory(path.parent)
    storage.atomic_json(root / 'last_attempt.json', {
        'run_id': report.run_id, 'report_path': f'runs/{report.run_id}.json', 'sha256': digest(data),
    })
    return path


def _prepare_batches(batches: list[FetchBatch]) -> list[FetchBatch]:
    result, seen = [], set()
    for original in batches:
        batch = FetchBatch.model_validate(original.model_dump())
        spec = get_spec(batch.source_id)
        if batch.source_id in seen:
            raise ValueError('Duplicate source batch')
        seen.add(batch.source_id)
        if batch.status in ('failed', 'blocked', 'empty') and batch.records:
            raise ValueError('Source batch status contradicts its records')
        accepted = []
        for record in batch.records:
            try:
                check_url(record.url, spec)
                if not record.text.strip() or record.publisher_id != spec.publisher_id or record.adapter_id != spec.source_id:
                    raise ValueError('Missing body or inconsistent source provenance')
                accepted.append(record)
            except (ValueError, TransportError):
                batch.rejected += 1
                batch.item_errors.append('Rejected record: body, URL or provenance validation failed')
        batch.records = accepted
        batch.succeeded = len(accepted)
        if batch.status == 'ok' and not accepted:
            batch.status = 'partial' if batch.rejected else 'empty'
        elif batch.rejected and accepted:
            batch.status = 'partial'
        result.append(batch)
    return result


def _outcome(batches: list[FetchBatch], current: CorpusSnapshot, changed: bool) -> tuple[SyncStatus, int, Health]:
    if batches and all(batch.status in ('failed', 'blocked') for batch in batches):
        return 'failed', 1, 'failed'
    records = any(batch.records for batch in batches)
    usable = any(v.active and v.adapter_id in {b.source_id for b in batches} for v in current.source_versions)
    if not batches or (not records and (not usable or all(b.status == 'empty' for b in batches))):
        return 'needs_review', 2, 'degraded'
    if any(batch.status not in ('ok', 'unchanged') or batch.rejected or batch.item_errors for batch in batches):
        return 'partial', 3, 'degraded'
    return ('complete', 0, 'healthy') if changed else ('unchanged', 0, 'healthy')


def sync_sources(store: Path, batches: list[FetchBatch], observed_at: AwareDatetime) -> SyncResult:
    """Merge against a fresh locked head, publish once, persist an independent report.

    Result publication fields reflect the actual head even after a postcommit I/O error.
    """
    root = Path(store).resolve()
    observed_at = TypeAdapter(AwareDatetime).validate_python(observed_at)
    report = RunReport(run_id=str(uuid4()), observed_at=observed_at,
                       status='failed', exit_code=1, health='failed')
    stage = None
    with storage.corpus_lock(root):
        phase = 'load_corpus'
        try:
            head = storage.load_head(root)
            if head:
                report.previous_revision = report.current_revision = head.snapshot.revision
                report.previous_token = report.catalog_token = head.token
            current = head.snapshot if head else CorpusSnapshot()
            phase = 'load_previous_report'
            report.sources = _last_sources(root)
            phase = 'validate_batches'
            batches = _prepare_batches(batches)
            report.requested_sources = [batch.source_id for batch in batches]
            for batch in batches:
                # An unsubstantiated 304 is no evidence of existing usable data.
                known = any(v.active and v.adapter_id == batch.source_id for v in current.source_versions)
                if batch.status == 'unchanged' and not batch.records and not known:
                    batch.status, batch.reason = 'empty', 'No prior source data for unchanged response'
            candidate = current.model_copy(deep=True)
            records = [record for batch in batches for record in batch.records]
            if records:
                phase = 'merge_sources'
                merged = merge_news(current.news, current.source_versions, records, observed_at=observed_at)
                candidate.news, candidate.source_versions = merged.events, merged.versions
                candidate.report.news = len(candidate.news)
            changed = bool(records) and (head is None or storage.snapshot_revision(candidate) != head.snapshot.revision
                                         or head.token.generation == 0)
            report.status, report.exit_code, report.health = _outcome(batches, current, changed)
            for batch in batches:
                previous = report.sources.get(batch.source_id)
                versions = [v for v in candidate.source_versions if v.adapter_id == batch.source_id and v.active]
                healthy = batch.status in ('ok', 'unchanged') and not batch.rejected and not batch.item_errors
                last_success = previous.last_success_at if previous else None
                if batch.records or (batch.status == 'unchanged' and versions):
                    observed_success = batch.last_success_at or batch.checked_at or observed_at
                    last_success = max(last_success, observed_success) if last_success else observed_success
                report.sources[batch.source_id] = SourceReport(
                    source_id=batch.source_id, publisher_id=get_spec(batch.source_id).publisher_id,
                    status=batch.status, health='healthy' if healthy else 'failed' if batch.status in ('failed', 'blocked') else 'degraded',
                    checked_at=batch.checked_at or observed_at, last_success_at=last_success,
                    attempted=batch.attempted, succeeded=batch.succeeded, rejected=batch.rejected,
                    item_errors=[_diagnostic(e) for e in batch.item_errors],
                    reason=_diagnostic(batch.reason) if batch.reason else None, active_records=len(versions),
                    source_version_ids=sorted(v.source_id for v in versions),
                    content_hashes=sorted(v.content_sha256 for v in versions))
            if changed and report.exit_code in (0, 3):
                phase = 'validate_corpus'
                storage.validate_snapshot(root, candidate)
            phase = 'prepare_report'
            stage = _stage_report(root, report)
            if changed and report.exit_code in (0, 3):
                phase = 'publish_corpus'
                storage.publish_snapshot(root, candidate)
                actual = storage.load_head(root)
                report.published_revision = report.current_revision = actual.snapshot.revision
                report.catalog_token = actual.token
            phase = 'finalize_report'
            _finish_report(root, report)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            report.status, report.exit_code, report.health = 'failed', 1, 'failed'
            report.fatal_errors.append(f'{phase}: {type(exc).__name__}')
            # A pointer replace can succeed before fsync/report failure. Never roll it back.
            try:
                actual = storage.load_head(root)
                if actual:
                    report.current_revision, report.catalog_token = actual.snapshot.revision, actual.token
                    if actual.token != report.previous_token:
                        report.published_revision = report.current_revision
                for source_id, source in report.sources.items():
                    versions = [v for v in actual.snapshot.source_versions if v.active and v.adapter_id == source_id] if actual else []
                    source.active_records = len(versions)
                    source.source_version_ids = sorted(v.source_id for v in versions)
                    source.content_hashes = sorted(v.content_sha256 for v in versions)
            except (OSError, ValueError, KeyError, TypeError):
                pass
            previous_path = root / 'runs' / f'{report.run_id}.json'
            if previous_path.exists():
                report.related_run_id, report.run_id = report.run_id, str(uuid4())
            report.report_path = None
            try:
                _finish_report(root, report)
            except (OSError, ValueError, RuntimeError, TypeError):
                path = root / 'runs' / f'{report.run_id}.json'
                report.report_path = path if path.exists() else None
        finally:
            if stage is not None:
                try:
                    stage.unlink(missing_ok=True)
                except OSError:
                    pass
    return SyncResult.model_validate(report.model_dump(include=set(SyncResult.model_fields)))
