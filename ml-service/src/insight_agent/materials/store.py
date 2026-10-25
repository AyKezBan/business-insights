"""Immutable snapshots and committed publication tokens behind one writer lock."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import NamedTemporaryFile, mkdtemp
import threading
from uuid import uuid4

from insight_agent.materials.artifacts import digest, safe_path, write_immutable
from insight_agent.materials.models import CatalogToken, CorpusSnapshot, SourceVersion
from insight_agent.schemas import BankFragment, NewsEvent

COLLECTIONS = {"bank_fragments": BankFragment, "news": NewsEvent, "source_versions": SourceVersion}
_locks_guard = threading.Lock()
_locks = {}
_held = threading.local()


@contextmanager
def corpus_lock(root: Path):
    """Serialize processes and threads; nested calls in the owner thread are safe."""
    root = Path(root).resolve()
    key = (os.getpid(), str(root))
    with _locks_guard:
        lock = _locks.setdefault(key, threading.RLock())
    with lock:
        held = getattr(_held, 'roots', None)
        if held is None:
            held = _held.roots = set()
        if key in held:
            yield
            return
        root.mkdir(parents=True, exist_ok=True)
        with (root / '.writer.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            held.add(key)
            try:
                yield
            finally:
                held.remove(key)
                fcntl.flock(stream, fcntl.LOCK_UN)


@dataclass(frozen=True)
class StoreHead:
    snapshot: CorpusSnapshot
    token: CatalogToken


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


def snapshot_revision(snapshot: CorpusSnapshot) -> str:
    """Predict a v2 content address without publishing or mutating the input."""
    snapshot = snapshot.model_copy(update={"schema_version": 2, "pipeline_version": "materials-v2"})
    return digest(_json_bytes(snapshot.model_dump(mode="json", exclude={"revision"})))


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_json(path: Path, value) -> None:
    """Replace one complete JSON file; failure after replace may already be committed."""
    data = _json_bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, prefix='.' + path.stem + '-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _prepare_snapshot(root: Path, snapshot: CorpusSnapshot) -> str:
    snapshot = snapshot.model_copy(update={"schema_version": 2, "pipeline_version": "materials-v2"})
    root.mkdir(parents=True, exist_ok=True)
    validate_snapshot(root, snapshot)
    revision = snapshot_revision(snapshot)
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
            _fsync_directory(temporary)
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
    # A prior attempt may have renamed this directory but failed its durability
    # barrier. Re-establish it even when reusing an existing candidate snapshot.
    _fsync_directory(folder.parent)
    return revision


def publish_snapshot(root: Path, snapshot: CorpusSnapshot) -> str:
    """Publish v2 under the shared lock, retaining the legacy revision-string result.

    Read/merge/write callers must hold corpus_lock around the complete transaction.
    Only already committed old tokens enter publication history.
    """
    root = Path(root)
    with corpus_lock(root):
        head = load_head(root)
        revision = _prepare_snapshot(root, snapshot)
        if head and head.token.generation and head.token.revision == revision:
            return revision
        identity = root / 'store.json'
        if identity.exists():
            metadata = json.loads(identity.read_text('utf-8'))
            if set(metadata) != {'store_id'} or not isinstance(metadata['store_id'], str) or not metadata['store_id'] or metadata['store_id'] == 'legacy':
                raise ValueError('Invalid store identity')
            store_id = metadata['store_id']
        else:
            store_id = head.token.store_id if head and head.token.generation else str(uuid4())
            atomic_json(identity, {'store_id': store_id})
        if head and head.token.generation and head.token.store_id != store_id:
            raise ValueError('Current token belongs to another store')
        generation = (head.token.generation if head else 0) + 1
        if head and head.token.generation:
            write_immutable(root, f'publications/{head.token.generation:020d}.json',
                            _json_bytes(head.token.model_dump(mode='json')))
            _fsync_directory(root / 'publications')
        token = CatalogToken(store_id=store_id, generation=generation, revision=revision)
        atomic_json(root / 'current.json', token.model_dump(mode='json'))
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


def load_head(root: Path) -> StoreHead | None:
    """Read exactly one pointer; legacy reads return a nonpersistent generation-0 sentinel."""
    root = Path(root)
    try:
        pointer = json.loads((root / 'current.json').read_text('utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(pointer, dict):
        raise ValueError('Invalid current pointer')
    if set(pointer) == {'revision'}:
        token = CatalogToken(store_id='legacy', generation=0, revision=pointer['revision'])
    else:
        token = CatalogToken.model_validate(pointer)
        if token.generation == 0 or token.store_id == 'legacy':
            raise ValueError('Invalid committed publication token')
        identity = json.loads((root / 'store.json').read_text('utf-8'))
        if identity != {'store_id': token.store_id}:
            raise ValueError('Current token belongs to another store')
    return StoreHead(_load_revision(root, token.revision), token)


def load_snapshot(root: Path, revision: str | None = None) -> CorpusSnapshot:
    if revision is not None:
        return _load_revision(Path(root), revision)
    head = load_head(root)
    if head is None:
        raise FileNotFoundError('No published snapshot')
    return head.snapshot


def load_catalog_token(root: Path, revision: str | None = None) -> CatalogToken:
    """Return the latest committed publication for a revision, without moving current."""
    root = Path(root)
    head = load_head(root)
    if head is None:
        raise FileNotFoundError('No published snapshot')
    if revision is None or revision == head.token.revision:
        return head.token
    snapshot = _load_revision(root, revision)
    for path in sorted((root / 'publications').glob('*.json'), reverse=True):
        token = CatalogToken.model_validate_json(path.read_bytes())
        if (path.name != f'{token.generation:020d}.json' or token.generation <= 0
                or token.store_id != head.token.store_id
                or (token.generation == head.token.generation and token != head.token)):
            raise ValueError('Invalid committed publication history')
        # Another writer may advance after our single head read. Those publications
        # are outside this read's committed horizon, not evidence of corruption.
        if token.generation > head.token.generation:
            continue
        if token.revision == revision:
            return token
    if snapshot.schema_version == 1:
        return CatalogToken(store_id='legacy', generation=0, revision=revision)
    raise KeyError('Revision was not published in this store')


def resolve_pdf(root: Path, snapshot: CorpusSnapshot, doc_id: str) -> Path:
    doc = next((d for d in snapshot.documents if d.doc_id == doc_id), None)
    if doc is None:
        raise KeyError("Document not found")
    path = safe_path(root, doc.relative_path)
    if digest(path.read_bytes()) != doc.sha256:
        raise ValueError("PDF hash mismatch")
    return path
