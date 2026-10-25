"""Publication tokens order corpus updates, independently of event content."""
import json
from pathlib import Path
import shutil

import pytest

from insight_agent.materials import store
from insight_agent.materials.models import CorpusSnapshot


def test_every_publication_has_persistent_identity_and_monotonic_generation(tmp_path):
    first = store.publish_snapshot(tmp_path, CorpusSnapshot())
    pointer = json.loads((tmp_path / 'current.json').read_text())
    assert pointer.get('generation') == 1
    assert pointer.get('store_id') and pointer['store_id'] != 'legacy'
    assert pointer['revision'] == first
    assert store.publish_snapshot(tmp_path, CorpusSnapshot()) == first
    assert json.loads((tmp_path / 'current.json').read_text()) == pointer
    candidate = store.load_snapshot(tmp_path)
    candidate.tool_versions['bank-review'] = 'second-review'
    second = store.publish_snapshot(tmp_path, candidate)
    newer = json.loads((tmp_path / 'current.json').read_text())
    assert newer['generation'] == 2 and newer['store_id'] == pointer['store_id']
    assert newer['revision'] == second != first


def test_bank_only_change_advances_token_while_event_hashes_stay_same(tmp_path):
    from test_material_store import corpus
    from insight_agent.materials.identity import event_content_sha256
    from insight_agent.materials.dedup import merge_news
    from insight_agent.materials.models import RawNews
    from test_multisource_sync import T
    root, original = corpus(tmp_path)
    merged = merge_news([], [], [RawNews(url='https://www.cbr.ru/press/1/', title='Decision',
                                         text='Key rate is 17 percent.', retrieved_at=T)], observed_at=T)
    original.news, original.source_versions = merged.events, merged.versions
    first = store.publish_snapshot(root, original)
    old_token = store.load_catalog_token(root)
    old = store.load_snapshot(root)
    changed = old.model_copy(deep=True)
    changed.documents[0].title = 'Reviewed bank document title'
    second = store.publish_snapshot(root, changed)
    token = store.load_catalog_token(root)
    assert token.store_id == old_token.store_id
    assert token.generation == old_token.generation + 1
    assert token.revision == second != first
    assert [event_content_sha256(e) for e in old.news] == [event_content_sha256(e) for e in changed.news]
    assert store.publish_snapshot(root, changed) == second
    assert store.load_catalog_token(root) == token


def test_concurrent_thread_writers_reload_under_shared_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store.publish_snapshot(tmp_path, CorpusSnapshot())
    barrier = Barrier(4)
    def update(number):
        barrier.wait(timeout=5)
        with store.corpus_lock(tmp_path):
            current = store.load_snapshot(tmp_path)
            current.tool_versions[f'writer-{number}'] = str(number)
            store.publish_snapshot(tmp_path, current)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(update, range(4)))
    assert store.load_snapshot(tmp_path).tool_versions == {f'writer-{i}': str(i) for i in range(4)}
    assert store.load_catalog_token(tmp_path).generation == 5


def test_historical_read_and_republication_do_not_confuse_generation(tmp_path):
    first = store.publish_snapshot(tmp_path, CorpusSnapshot())
    token1 = store.load_catalog_token(tmp_path)
    second = store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'test': '2'}))
    token2 = store.load_catalog_token(tmp_path)
    before = (tmp_path / 'current.json').read_bytes()
    assert store.load_snapshot(tmp_path, revision=first).revision == first
    assert store.load_catalog_token(tmp_path, revision=first) == token1
    assert (tmp_path / 'current.json').read_bytes() == before
    store.publish_snapshot(tmp_path, store.load_snapshot(tmp_path, revision=first))
    token3 = store.load_catalog_token(tmp_path)
    assert token3.revision == first and token3.generation == token2.generation + 1
    assert store.load_catalog_token(tmp_path, revision=second) == token2


def test_failed_future_pointer_is_never_advertised_as_committed_history(tmp_path, monkeypatch):
    store.publish_snapshot(tmp_path, CorpusSnapshot())
    old_token = store.load_catalog_token(tmp_path)
    replace = store.os.replace
    def fail_current(source, destination):
        if Path(destination) == tmp_path / 'current.json':
            raise OSError('pointer failure')
        return replace(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(store.os, 'replace', fail_current)
        with pytest.raises(OSError):
            store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'uncommitted': 'A'}))
    unpublished = next(p.name for p in (tmp_path / 'snapshots').iterdir() if p.name != old_token.revision)
    store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'committed': 'B'}))
    assert store.load_catalog_token(tmp_path).generation == old_token.generation + 1
    with pytest.raises(KeyError, match='published'):
        store.load_catalog_token(tmp_path, revision=unpublished)
    assert store.load_catalog_token(tmp_path, revision=old_token.revision) == old_token


def test_legacy_read_is_read_only_and_explicit_writer_upgrades_once(tmp_path):
    fixtures = Path(__file__).parent / 'fixtures/materials/v1'
    root = tmp_path / 'store'
    shutil.copytree(fixtures, root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    old = store.load_snapshot(root)
    sentinel = store.load_catalog_token(root)
    assert sentinel.store_id == 'legacy' and sentinel.generation == 0
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    store.publish_snapshot(root, old)
    token = store.load_catalog_token(root)
    assert token.store_id != 'legacy' and token.generation == 1
    assert token.revision != old.revision
    assert store.load_catalog_token(root, revision=old.revision) == sentinel
    for name, data in before.items():
        if name != Path('current.json'):
            assert (root / name).read_bytes() == data


def test_public_publish_is_reentrant_inside_legacy_lock(tmp_path):
    from insight_agent.materials.cli import corpus_lock
    with corpus_lock(tmp_path):
        with corpus_lock(tmp_path):
            revision = store.publish_snapshot(tmp_path, CorpusSnapshot())
    assert store.load_head(tmp_path).token.revision == revision


def test_partial_collection_write_keeps_pointer_and_token(tmp_path, monkeypatch):
    store.publish_snapshot(tmp_path, CorpusSnapshot())
    before = (tmp_path / 'current.json').read_bytes()
    write = store._write_fsync
    def fail_manifest(path, data):
        if path.name == 'manifest.json':
            raise OSError('partial snapshot write')
        write(path, data)
    monkeypatch.setattr(store, '_write_fsync', fail_manifest)
    with pytest.raises(OSError):
        store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'test': '2'}))
    assert (tmp_path / 'current.json').read_bytes() == before
    assert store.load_head(tmp_path).snapshot.revision == json.loads(before)['revision']


def test_historical_lookup_tolerates_new_publications_after_its_head_read(tmp_path, monkeypatch):
    first = store.publish_snapshot(tmp_path, CorpusSnapshot())
    token1 = store.load_catalog_token(tmp_path)
    store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'test': '2'}))
    glob = Path.glob
    advanced = []
    def publish_while_listing(path, pattern):
        if path == tmp_path / 'publications' and not advanced:
            advanced.append(True)
            store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'test': '3'}))
            store.publish_snapshot(tmp_path, CorpusSnapshot(tool_versions={'test': '4'}))
        return glob(path, pattern)
    monkeypatch.setattr(Path, 'glob', publish_while_listing)
    assert store.load_catalog_token(tmp_path, revision=first) == token1
    assert store.load_catalog_token(tmp_path).generation == 4


@pytest.mark.parametrize('pointer', [None, 1, [], 'not a pointer'])
def test_malformed_pointer_is_an_explicit_validation_error(tmp_path, pointer):
    (tmp_path / 'current.json').write_text(json.dumps(pointer))
    with pytest.raises(ValueError, match='pointer'):
        store.load_head(tmp_path)


def test_snapshot_parent_fsync_failure_blocks_publication_and_existing_directory_retry(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    store.publish_snapshot(root, CorpusSnapshot())
    before = (root / 'current.json').read_bytes()
    old_token = store.load_catalog_token(root)
    candidate = CorpusSnapshot(tool_versions={'review-probe': 'new'})
    revision = store.snapshot_revision(candidate)
    fsync = store._fsync_directory
    failures = []
    def fail_snapshot_parent(path):
        if path == root / 'snapshots':
            failures.append(path)
            raise OSError('snapshot parent durability barrier failed')
        return fsync(path)
    with monkeypatch.context() as patch:
        patch.setattr(store, '_fsync_directory', fail_snapshot_parent)
        for _ in range(2):
            with pytest.raises(OSError, match='durability barrier'):
                store.publish_snapshot(root, candidate)
            assert (root / 'snapshots' / revision).is_dir()
            assert (root / 'current.json').read_bytes() == before
            assert store.load_catalog_token(root) == old_token
            with pytest.raises(KeyError, match='published'):
                store.load_catalog_token(root, revision=revision)
    assert failures == [root / 'snapshots', root / 'snapshots']
    assert store.publish_snapshot(root, candidate) == revision
    token = store.load_catalog_token(root)
    assert token.generation == old_token.generation + 1
    assert token.store_id == old_token.store_id
