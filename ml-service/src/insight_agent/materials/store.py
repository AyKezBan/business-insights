"""Validated immutable snapshots and one atomic current pointer (no hot reload)."""
from collections import Counter
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile, mkdtemp

from insight_agent.materials.artifacts import digest, safe_path
from insight_agent.materials.models import CorpusSnapshot, SourceVersion
from insight_agent.schemas import BankFragment, NewsEvent

COLLECTIONS = {"bank_fragments": BankFragment, "news": NewsEvent, "source_versions": SourceVersion}


def validate_snapshot(root: Path, snapshot: CorpusSnapshot) -> None:
    if snapshot.report.errors:
        raise ValueError("Corpus has extraction errors; previous snapshot retained")
    documents = {d.doc_id: d for d in snapshot.documents}
    if len(documents) != len(snapshot.documents):
        raise ValueError("Duplicate document IDs")
    for doc in documents.values():
        if doc.doc_id != "bank-" + doc.sha256 or digest(safe_path(root, doc.relative_path).read_bytes()) != doc.sha256:
            raise ValueError("Document hash mismatch")
    pages = {}
    for page in snapshot.pages:
        key = (page.doc_id, page.page)
        if key in pages or page.doc_id not in documents or page.page > documents[page.doc_id].page_count:
            raise ValueError("Invalid or duplicate page reference")
        text = None
        if page.relative_text_path is not None:
            data = safe_path(root, page.relative_text_path).read_bytes()
            if digest(data) != page.text_sha256:
                raise ValueError("Page text hash mismatch")
            text = data.decode("utf-8")
        pages[key] = (page, text)
    for doc in documents.values():
        if {n for d, n in pages if d == doc.doc_id} != set(range(1, doc.page_count + 1)):
            raise ValueError("Page index is incomplete")
    ids = set()
    for fragment in snapshot.bank_fragments:
        if fragment.fragment_id in ids:
            raise ValueError("Duplicate fragment ID")
        ids.add(fragment.fragment_id)
        entry = pages.get((fragment.doc_id, fragment.page))
        if not entry or entry[1] is None or fragment.char_start is None or fragment.char_end is None:
            raise ValueError("Fragment page/offset is unresolved")
        if entry[1][fragment.char_start:fragment.char_end] != fragment.text or digest(fragment.text.encode()) != fragment.content_sha256:
            raise ValueError("Fragment hash or page offsets mismatch")
    versions = {v.source_id: v for v in snapshot.source_versions}
    if len(versions) != len(snapshot.source_versions):
        raise ValueError("Duplicate source version ID")
    active = Counter(v.url for v in versions.values() if v.active)
    if any(active[url] != 1 for url in {v.url for v in versions.values()}):
        raise ValueError("Each URL requires exactly one active version")
    for version in versions.values():
        if digest(version.text.encode()) != version.content_sha256:
            raise ValueError("Source text hash mismatch")
    event_ids = {e.event_id for e in snapshot.news}
    if len(event_ids) != len(snapshot.news):
        raise ValueError("Duplicate event IDs")
    referenced = set()
    for event in snapshot.news:
        refs = [event.source] + event.related_sources
        for ref in refs:
            v = versions.get(ref.source_id)
            if not v or not v.active or v.event_id != event.event_id or ref.content_sha256 != v.content_sha256:
                raise ValueError("Event source version is unresolved")
            referenced.add(v.source_id)
        if event.text != versions[event.source.source_id].text:
            raise ValueError("Primary text does not match source version")
        for conflict in event.conflicts:
            if not set(conflict.source_ids).issubset({r.source_id for r in refs}):
                raise ValueError("Conflict cites a missing source")
    if referenced != {v.source_id for v in versions.values() if v.active}:
        raise ValueError("Active source is not attached to an event")


def _json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _write_fsync(path: Path, data: bytes) -> None:
    with path.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def publish_snapshot(root: Path, snapshot: CorpusSnapshot) -> str:
    snapshot = snapshot.model_copy(update={"schema_version": 2, "pipeline_version": "materials-v2"})
    root.mkdir(parents=True, exist_ok=True)
    validate_snapshot(root, snapshot)
    payload = snapshot.model_dump(mode="json", exclude={"revision"})
    revision = digest(_json_bytes(payload))
    folder = safe_path(root, f"snapshots/{revision}")
    folder.parent.mkdir(parents=True, exist_ok=True)
    if not folder.exists():
        temporary = Path(mkdtemp(prefix=".prepare-", dir=folder.parent))
        try:
            manifest = snapshot.model_dump(mode="json", exclude=set(COLLECTIONS))
            manifest["revision"] = revision
            manifest["files"] = {}
            for key in COLLECTIONS:
                data = b"".join(_json_bytes(x.model_dump(mode="json")) for x in getattr(snapshot, key))
                name = key + ".jsonl"
                _write_fsync(temporary / name, data)
                manifest["files"][name] = digest(data)
            _write_fsync(temporary / "manifest.json", _json_bytes(manifest))
            try:
                os.rename(temporary, folder)
            except OSError:
                if not folder.exists():
                    raise
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    # Also checks an existing directory: never switch to a damaged prior revision.
    _load_revision(root, revision)
    pointer = None
    try:
        with NamedTemporaryFile(dir=root, prefix=".current-", delete=False) as stream:
            pointer = Path(stream.name)
            stream.write(_json_bytes({"revision": revision}))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pointer, root / "current.json")
    finally:
        if pointer is not None:
            pointer.unlink(missing_ok=True)
    return revision


def _load_revision(root: Path, revision: str) -> CorpusSnapshot:
    if re.fullmatch(r"[a-f0-9]{64}", revision) is None:
        raise ValueError("Invalid corpus revision")
    folder = safe_path(root, f"snapshots/{revision}")
    manifest = json.loads((folder / "manifest.json").read_text("utf-8"))
    files = manifest.pop("files")
    if set(files) != {k + ".jsonl" for k in COLLECTIONS}:
        raise ValueError("Invalid snapshot file registry")
    for key in COLLECTIONS:
        name = key + ".jsonl"
        data = (folder / name).read_bytes()
        if digest(data) != files[name]:
            raise ValueError(f"Collection hash mismatch: {name}")
        manifest[key] = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
    # Verify the stored representation before Pydantic adds v2 defaults to v1.
    payload = {key: value for key, value in manifest.items() if key != "revision"}
    if manifest.get("revision") != revision or digest(_json_bytes(payload)) != revision:
        raise ValueError("Snapshot manifest hash mismatch")
    from insight_agent.materials.legacy import migrate_snapshot
    snapshot = CorpusSnapshot.model_validate(migrate_snapshot(manifest))
    validate_snapshot(root, snapshot)
    return snapshot


def load_snapshot(root: Path) -> CorpusSnapshot:
    pointer = json.loads((root / "current.json").read_text("utf-8"))
    return _load_revision(root, pointer["revision"])


def resolve_pdf(root: Path, snapshot: CorpusSnapshot, doc_id: str) -> Path:
    doc = next((d for d in snapshot.documents if d.doc_id == doc_id), None)
    if doc is None:
        raise KeyError("Document not found")
    path = safe_path(root, doc.relative_path)
    if digest(path.read_bytes()) != doc.sha256:
        raise ValueError("PDF hash mismatch")
    return path
