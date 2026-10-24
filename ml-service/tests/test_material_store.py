import json

import pytest

from insight_agent.materials.bank_corpus import prepare_bank
from test_pdf_extraction import FakeOCR,make_pdf


def corpus(tmp_path,text='Rate remains 17 percent.'):
    inputs=tmp_path/'inputs';inputs.mkdir(exist_ok=True)
    meta=make_pdf(inputs/'x.pdf',text)
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[meta.model_dump(mode='json')]}))
    root=tmp_path/'store'
    return root,prepare_bank(inputs,manifest,root,FakeOCR())


def test_snapshot_is_idempotent_and_restores_every_page(tmp_path):
    from insight_agent.materials.store import publish_snapshot,load_snapshot
    root,snapshot=corpus(tmp_path)
    revision=publish_snapshot(root,snapshot)
    loaded=load_snapshot(root)
    assert loaded.revision==revision and len(revision)==64
    assert loaded.pages[0].status=='ready'
    assert publish_snapshot(root,snapshot)==revision


def test_failed_pointer_swap_preserves_previous_pdf_text_and_page_status(tmp_path,monkeypatch):
    from insight_agent.materials import store
    root,old=corpus(tmp_path)
    revision=store.publish_snapshot(root,old)
    original=(root/old.documents[0].relative_path).read_bytes()
    root,new=corpus(tmp_path,'Changed rate 18 percent.')
    replace=store.os.replace
    def fail_pointer(src,dst):
        if str(dst).endswith('current.json'):
            raise OSError('interrupted publication')
        return replace(src,dst)
    monkeypatch.setattr(store.os,'replace',fail_pointer)
    with pytest.raises(OSError):
        store.publish_snapshot(root,new)
    loaded=store.load_snapshot(root)
    assert loaded.revision==revision and loaded.pages==old.pages
    assert (root/loaded.documents[0].relative_path).read_bytes()==original


def test_corrupted_collection_is_detected_on_restart(tmp_path):
    from insight_agent.materials.store import publish_snapshot,load_snapshot
    root,snapshot=corpus(tmp_path)
    revision=publish_snapshot(root,snapshot)
    (root/'snapshots'/revision/'bank_fragments.jsonl').write_text('broken')
    with pytest.raises(ValueError,match='hash'):
        load_snapshot(root)


def test_missing_page_artifact_blocks_publication(tmp_path):
    from insight_agent.materials.store import publish_snapshot,load_snapshot
    root,snapshot=corpus(tmp_path)
    revision=publish_snapshot(root,snapshot)
    candidate=snapshot.model_copy(deep=True)
    candidate.pages[0].relative_text_path='pages/not-present.txt'
    with pytest.raises((ValueError,FileNotFoundError)):
        publish_snapshot(root,candidate)
    assert load_snapshot(root).revision==revision


def test_pdf_resolver_cannot_escape_manifest_root(tmp_path):
    from insight_agent.materials.store import resolve_pdf
    root,snapshot=corpus(tmp_path)
    with pytest.raises(KeyError):
        resolve_pdf(root,snapshot,'unknown')
    snapshot.documents[0].relative_path='../outside.pdf'
    with pytest.raises(ValueError):
        resolve_pdf(root,snapshot,snapshot.documents[0].doc_id)
