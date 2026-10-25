"""Read-only, revision-pinned views over the material store's immutable snapshots."""
from collections import OrderedDict
from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
from urllib.parse import quote, urlsplit

from insight_agent.materials.artifacts import digest, safe_path
from insight_agent.materials.models import CatalogToken, CorpusSnapshot
from insight_agent.materials.store import load_head, load_snapshot
from insight_agent.tools.bank import BankIndex
from insight_agent.tools.news import NewsStore


class CorpusError(ValueError):
    def __init__(self, message: str, code: str = "corpus_unavailable"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class _BankView:
    _index: BankIndex

    @property
    def fragments(self):
        return tuple(fragment.model_copy(deep=True) for fragment in self._index.fragments)

    def search(self, args, *, as_of=None):
        return [hit.model_copy(deep=True) for hit in self._index.search(args, as_of=as_of)]


@dataclass(frozen=True)
class _NewsView:
    _store: NewsStore

    @property
    def events(self):
        return tuple(event.model_copy(deep=True) for event in self._store.events)

    def search(self, args):
        return [event.model_copy(deep=True) for event in self._store.search(args)]

    def resolve_source_text(self, source_id):
        return self._store.resolve_source_text(source_id)


@dataclass(frozen=True)
class CorpusBundle:
    _snapshot: CorpusSnapshot
    bank: _BankView
    news: _NewsView
    # Publication identity is optional: old snapshots remain usable without token history.
    token: CatalogToken | None = None

    @property
    def snapshot(self) -> CorpusSnapshot:
        return self._snapshot.model_copy(deep=True)

    @property
    def revision(self) -> str:
        return self._snapshot.revision


class CorpusRegistry:
    def __init__(self, root: Path, public_base_url: str, max_cached_bundles: int = 4):
        parsed = urlsplit(public_base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment
                or parsed.hostname in {"ml", "ml-service"}):
            raise ValueError("materials_public_base_url must be an absolute browser-facing HTTP(S) base")
        if max_cached_bundles < 1:
            raise ValueError("Corpus cache size must be positive")
        self.root = Path(root)
        self.public_base_url = public_base_url.rstrip("/")
        self.max_cached_bundles = max_cached_bundles
        self._cache: OrderedDict[str, CorpusBundle] = OrderedDict()
        self._lock = threading.RLock()
        self._serving: CorpusBundle | None = None
        self.last_refresh_error: str | None = None

    def _bundle(self, snapshot: CorpusSnapshot) -> CorpusBundle:
        revision = snapshot.revision
        if revision not in self._cache:
            snapshot = snapshot.model_copy(deep=True)
            versions = {version.source_id: version for version in snapshot.source_versions}
            for event in snapshot.news:
                for ref in [event.source, *event.related_sources]:
                    version = versions[ref.source_id]
                    ref.first_seen_at, ref.retrieved_at = version.first_seen_at, version.retrieved_at
            fragments = [fragment.model_copy(update={"source_url":
                f"{self.public_base_url}/v1/materials/documents/{quote(fragment.doc_id, safe='')}/file"
                f"?revision={revision}#page={fragment.page}"}, deep=True)
                for fragment in snapshot.bank_fragments]
            self._cache[revision] = CorpusBundle(
                snapshot, _BankView(BankIndex(fragments)),
                _NewsView(NewsStore(snapshot.news, snapshot.source_versions)),
            )
        self._cache.move_to_end(revision)
        bundle = self._cache[revision]
        while len(self._cache) > self.max_cached_bundles:
            self._cache.popitem(last=False)
        return bundle

    def current(self) -> CorpusBundle:
        with self._lock:
            try:
                # One pointer read supplies both snapshot and token; do not rebind the token later.
                head = load_head(self.root)
                if head is None:
                    raise FileNotFoundError("No published corpus")
                bundle = replace(self._bundle(head.snapshot), token=head.token)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.last_refresh_error = f"Corpus refresh failed: {type(exc).__name__}"
                raise CorpusError(self.last_refresh_error) from exc
            self._serving = bundle
            self.last_refresh_error = None
            return bundle

    def get(self, revision: str) -> CorpusBundle:
        with self._lock:
            try:
                # Validate even on a cache hit; cache retains indexes, not an integrity bypass.
                snapshot = load_snapshot(self.root, revision)
                return self._bundle(snapshot)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise CorpusError(f"Исходная версия материалов {revision} недоступна") from exc

    @property
    def serving(self) -> CorpusBundle | None:
        with self._lock:
            return self._serving

    def health(self) -> dict:
        try:
            current = self.current()
        except CorpusError:
            current = None
        serving = self.serving
        token = current.token if current else None
        initialized = bool(token and token.generation > 0 and token.store_id != "legacy")
        result = {
            "corpus_revision": serving.revision if serving else None,
            "serving_revision": serving.revision if serving else None,
            "current_revision": current.revision if current else None,
            "catalog_initialized": initialized,
            "catalog_token": token.model_dump(mode="json") if initialized else None,
            "last_refresh_error": self.last_refresh_error,
            "bank_fragments": len(serving.bank.fragments) if serving else 0,
            "news": len(serving.news.events) if serving else 0,
            "verified_bank_fragments": sum(f.review_status == "verified" for f in serving.bank.fragments) if serving else 0,
            "last_attempt": None,
            "source_health": "unknown",
        }
        if not (self.root / "last_attempt.json").exists():
            return result
        try:
            from insight_agent.materials.collector import RunReport
            pointer = json.loads((self.root / "last_attempt.json").read_text("utf-8"))
            if pointer["report_path"] != f'runs/{pointer["run_id"]}.json':
                raise ValueError("Invalid report path")
            raw = safe_path(self.root, pointer["report_path"]).read_bytes()
            if digest(raw) != pointer["sha256"]:
                raise ValueError("Invalid report checksum")
            report = RunReport.model_validate_json(raw)
            if report.run_id != pointer["run_id"] or report.phase != "final":
                raise ValueError("Invalid report identity")
            result["last_attempt"] = report.model_dump(mode="json", exclude={"report_path"})
            result["source_health"] = report.health
        except (OSError, ValueError, KeyError, TypeError):
            result["source_health"] = "failed"
            result["report_error"] = "Last attempt report is unavailable or corrupt"
        return result
