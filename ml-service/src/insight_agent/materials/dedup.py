"""Conservative exact deduplication; revisions never erase prior source texts."""
from collections import defaultdict
from datetime import datetime
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from insight_agent.materials.artifacts import digest
from insight_agent.materials.models import NewsImportResult, RawNews, SourceMetadata, SourceVersion
from insight_agent.materials.news import classify_topics
from insight_agent.schemas import NewsEvent, SourceConflict, SourceKind, SourceRef


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Invalid public source URL")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(query), ""))


def normalize_news_text(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def source_ref(version: SourceVersion) -> SourceRef:
    return SourceRef(source_id=version.source_id, kind=SourceKind.news, title=version.title,
        date=version.published_date,
        url=version.url, content_sha256=version.content_sha256,
        **{key: getattr(version, key) for key in (
            "publisher_id", "adapter_id", "material_type", "completeness", "language",
            "published_date", "published_precision", "first_seen_at", "retrieved_at",
            "rights_url", "translation_attribution")})


def events_from_versions(versions: list[SourceVersion], previous: list[NewsEvent] = ()) -> list[NewsEvent]:
    groups = defaultdict(list)
    for version in versions:
        if version.active:
            groups[version.event_id].append(version)
    previous_by_id = {e.event_id: e for e in previous}
    events = []
    for event_id, members in sorted(groups.items()):
        ordered = sorted(members, key=lambda v: (-v.first_seen_at.timestamp(), v.url))
        primary = ordered[0]
        conflicts = []
        if len({v.content_sha256 for v in members}) > 1:
            ids = sorted(v.source_id for v in members)
            old = previous_by_id.get(event_id)
            matching = next((c for c in old.conflicts if sorted(c.source_ids) == ids), None) if old else None
            conflicts = [matching or SourceConflict(reason="previously_equal_sources_diverged", source_ids=ids)]
        events.append(NewsEvent(event_id=event_id, title=primary.title, text=primary.text,
            published_at=primary.published_at, published_date=primary.published_date,
            published_precision=primary.published_precision, source=source_ref(primary),
            related_sources=[source_ref(v) for v in ordered[1:]], conflicts=conflicts,
            topics=primary.topics or classify_topics(primary.title + " " + primary.text)))
    return events


def _semantic_metadata(value: SourceMetadata) -> dict:
    # Parser/raw rendering/observation changes alone do not create new source versions.
    return value.model_dump(mode="json", include={
        "published_at", "published_date", "published_precision", "publisher_id", "adapter_id",
        "language", "material_type", "completeness", "rights_url", "translation_attribution", "topics"})


def merge_news(existing: list[NewsEvent], versions: list[SourceVersion], incoming: list[RawNews],
               *, observed_at: datetime) -> NewsImportResult:
    if observed_at.tzinfo is None:
        raise ValueError("Batch observation requires timezone")
    result = [v.model_copy(deep=True) for v in versions]
    incoming_by_url = {}
    for raw in incoming:
        url = canonicalize_url(raw.url)
        if url in incoming_by_url:
            earlier = incoming_by_url[url]
            if (normalize_news_text(earlier.text), earlier.title, _semantic_metadata(earlier)) != (normalize_news_text(raw.text), raw.title, _semantic_metadata(raw)):
                raise ValueError("Two unordered versions of the same URL in one batch")
            if earlier.retrieved_at >= raw.retrieved_at:
                continue
        incoming_by_url[url] = raw
    for url, raw in sorted(incoming_by_url.items()):
        text = normalize_news_text(raw.text)
        text_hash = digest(text.encode())
        url_versions = [v for v in result if v.url == url]
        active = next((v for v in url_versions if v.active), None)
        if active and (text_hash, raw.title, _semantic_metadata(raw)) == (active.content_sha256, active.title, _semantic_metadata(active)):
            active.retrieved_at = max(active.retrieved_at, raw.retrieved_at)
            continue
        stale = active is not None and raw.retrieved_at < active.retrieved_at
        if stale and any(v.content_sha256 == text_hash for v in url_versions):
            continue
        if active:
            event_id = active.event_id
        else:
            matches = [v for v in result if v.active and v.content_sha256 == text_hash]
            event_id = min((v.event_id for v in matches), default="evt-" + digest(url.encode())[:24])
        revision = max((v.revision for v in url_versions), default=0) + 1
        if not stale:
            for old in url_versions:
                old.active = False
        result.append(SourceVersion(source_id=f"news:{digest(url.encode())[:20]}:r{revision}:{text_hash[:16]}",
            event_id=event_id, url=url, revision=revision, active=not stale,
            content_sha256=text_hash, text=text, title=raw.title,
            **{**raw.model_dump(include=set(SourceMetadata.model_fields)), "original_text": raw.original_text if raw.original_text is not None else raw.text},
            first_seen_at=raw.retrieved_at if stale else observed_at, retrieved_at=raw.retrieved_at))
    result.sort(key=lambda v: (v.url, v.revision))
    return NewsImportResult(events=events_from_versions(result, existing), versions=result)


def link_source(events: list[NewsEvent], versions: list[SourceVersion], event_id: str, source_id: str) -> NewsImportResult:
    if not any(event.event_id == event_id for event in events):
        raise ValueError("Target event not found")
    selected = next((version for version in versions if version.source_id == source_id and version.active), None)
    if selected is None:
        raise ValueError("Active source version not found")
    moved = [version.model_copy(update={"event_id": event_id}) if version.url == selected.url
             else version.model_copy() for version in versions]
    return NewsImportResult(events=events_from_versions(moved, events), versions=moved)
