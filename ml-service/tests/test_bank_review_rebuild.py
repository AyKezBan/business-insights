import importlib.util
import json
from pathlib import Path

import pytest

from insight_agent.materials.bank_corpus import prepare_bank
from insight_agent.materials.store import load_snapshot, publish_snapshot
from test_pdf_extraction import FakeOCR, make_pdf


def rebuild_module():
    path = Path(__file__).resolve().parents[1] / "eval/rebuild_bank_review.py"
    assert path.exists(), "No reproducible, hash-checked private review rebuild"
    spec = importlib.util.spec_from_file_location("bank_rebuild", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reviewed_source(tmp_path):
    inputs = tmp_path / "input"
    inputs.mkdir()
    meta = make_pdf(inputs / "source.pdf", "Demand supports lending.")
    manifest = tmp_path / "docs.json"
    manifest.write_text(json.dumps({"documents": [meta.model_dump(mode="json")]}))
    source = tmp_path / "source"
    snapshot = prepare_bank(inputs, manifest, source, FakeOCR())
    publish_snapshot(source, snapshot)
    page = snapshot.pages[0]
    fragment = snapshot.bank_fragments[0]
    witness = dict(doc_id=fragment.doc_id, page=1, content_sha256=fragment.content_sha256,
                   text_sha256=page.text_sha256, extraction_revision=page.extraction_revision,
                   image_sha256="a" * 64, char_start=fragment.char_start, char_end=fragment.char_end)
    audit = {"pages": [dict(doc_id=meta.doc_id, page=1, text_sha256=page.text_sha256,
              extraction_revision=page.extraction_revision, pdf_sha256=meta.sha256, image_sha256="a" * 64)],
             "fragments": {fragment.fragment_id: witness}}
    reviews = tmp_path / "reviews.json"
    reviews.write_text(json.dumps({fragment.fragment_id: dict(content_sha256=fragment.content_sha256,
        review_status="verified", statement_type="bank_assessment")}))
    page_review = tmp_path / "pages.json"
    page_review.write_text(json.dumps(audit))
    return source, reviews, page_review


def test_rebuild_preserves_original_revision_and_applies_only_exact_witnesses(tmp_path):
    rebuild = rebuild_module()
    source, reviews, audit = reviewed_source(tmp_path)
    old_pointer = (source / "current.json").read_bytes()
    target = tmp_path / "reviewed"
    revision = rebuild.rebuild_review(source, target, reviews, audit)
    assert (source / "current.json").read_bytes() == old_pointer
    assert load_snapshot(source).bank_fragments[0].review_status == "pending"
    assert load_snapshot(target).bank_fragments[0].review_status == "verified"
    assert load_snapshot(target).revision == revision
    assert (target / "snapshots" / load_snapshot(source).revision).is_dir()


@pytest.mark.parametrize("mutation", ["text_sha256", "extraction_revision", "char_end", "content_sha256", "missing_page", "missing_fragment"])
def test_rebuild_rejects_changed_or_unwitnessed_evidence_without_creating_target(tmp_path, mutation):
    rebuild = rebuild_module()
    source, reviews, path = reviewed_source(tmp_path)
    audit = json.loads(path.read_text())
    witness = next(iter(audit["fragments"].values()))
    if mutation == "missing_page":
        audit["pages"] = []
    elif mutation == "missing_fragment":
        audit["fragments"] = {}
    else:
        witness[mutation] = witness[mutation] + 1 if mutation == "char_end" else "b" * 64
    path.write_text(json.dumps(audit))
    target = tmp_path / "reviewed"
    with pytest.raises(ValueError):
        rebuild.rebuild_review(source, target, reviews, path)
    assert not target.exists()


def test_rebuild_refuses_existing_destination(tmp_path):
    rebuild = rebuild_module()
    source, reviews, audit = reviewed_source(tmp_path)
    with pytest.raises(ValueError, match="destination"):
        rebuild.rebuild_review(source, source, reviews, audit)


def test_rebuild_uses_one_frozen_review_decision_even_if_path_changes(tmp_path, monkeypatch):
    rebuild = rebuild_module()
    source, reviews, audit = reviewed_source(tmp_path)
    original = json.loads(reviews.read_text())
    next(iter(original.values()))["review_status"] = "rejected"
    reviews.write_text(json.dumps(original))
    data = json.loads(audit.read_text())
    data["fragments"] = {}
    audit.write_text(json.dumps(data))
    validate = rebuild.validate_witnesses

    def swap_after_validation(*args):
        validate(*args)
        next(iter(original.values()))["review_status"] = "verified"
        reviews.write_text(json.dumps(original))

    monkeypatch.setattr(rebuild, "validate_witnesses", swap_after_validation)
    target = tmp_path / "reviewed"
    rebuild.rebuild_review(source, target, reviews, audit)
    assert load_snapshot(target).bank_fragments[0].review_status == "rejected"


def test_matching_missing_image_hashes_do_not_authorize_verified_fragments(tmp_path):
    rebuild = rebuild_module()
    source, reviews, path = reviewed_source(tmp_path)
    audit = json.loads(path.read_text())
    audit["pages"][0]["image_sha256"] = None
    next(iter(audit["fragments"].values()))["image_sha256"] = None
    path.write_text(json.dumps(audit))
    with pytest.raises(ValueError):
        rebuild.rebuild_review(source, tmp_path / "reviewed", reviews, path)
