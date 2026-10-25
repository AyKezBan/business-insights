"""Release gates for public, hash-bound review metadata; no private text in Git."""
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_physical_page_has_an_explicit_hash_bound_visual_disposition():
    path = ROOT / "eval/bank-page-review.json"
    assert path.exists(), "64-page visual review has not been published"
    audit = json.loads(path.read_text())
    docs = json.loads((ROOT / "config/bank-documents.json").read_text())["documents"]
    expected = {(d["doc_id"], p) for d in docs for p in range(1, d["page_count"] + 1)}
    pages = audit["pages"]
    assert len(pages) == len(expected) == 64
    assert {(p["doc_id"], p["page"]) for p in pages} == expected
    documents = {d["doc_id"]: d for d in docs}
    for page in pages:
        assert page["pdf_sha256"] == documents[page["doc_id"]]["sha256"]
        for key in ("text_sha256", "extraction_revision", "image_sha256"):
            assert len(page[key]) == 64 and int(page[key], 16) >= 0
        assert page["reason"] and page["reviewer"] and page["disposition"]
        assert page["reviewed_at"] == "2026-10-26"


def test_verified_claims_have_page_witness_and_current_six_document_six_topic_coverage():
    path = ROOT / "eval/bank-page-review.json"
    assert path.exists(), "No visual witness for the previous 11 decisions"
    audit = json.loads(path.read_text())
    decisions = json.loads((ROOT / "eval/bank-review.json").read_text())
    docs = {d["doc_id"]: d for d in json.loads((ROOT / "config/bank-documents.json").read_text())["documents"]}
    pages = {(p["doc_id"], p["page"]): p for p in audit["pages"]}
    current = []
    superseded = {old for d in decisions.values() for old in d.get("supersedes", [])}
    for identifier, decision in decisions.items():
        if decision["review_status"] != "verified":
            continue
        witness = audit["fragments"][identifier]
        page = pages[(witness["doc_id"], witness["page"])]
        assert witness["content_sha256"] == decision["content_sha256"]
        assert witness["text_sha256"] == page["text_sha256"]
        assert witness["extraction_revision"] == page["extraction_revision"]
        assert witness["image_sha256"] == page["image_sha256"]
        assert witness["char_end"] > witness["char_start"] >= 0
        assert witness["reason"] and witness["reviewer"]
        assert decision["statement_type"] != "mixed"
        doc = docs[witness["doc_id"]]
        assert doc["doc_date"] is not None
        if decision["statement_type"] == "forecast":
            assert decision["valid_until"] and decision["target_period"]
        if ("2026-04-29" <= doc["doc_date"] <= "2026-10-26"
                and (not decision["valid_until"] or decision["valid_until"] >= "2026-10-26")
                and identifier not in superseded):
            current.append(witness)
    assert len(current) >= 30
    assert len({w["doc_id"] for w in current}) >= 6
    assert len({topic for w in current for topic in w["verified_topics"]}) >= 6


def test_retrieval_holdout_is_frozen_and_independent_of_unchanged_original_gold():
    freeze_path = ROOT / "eval/bank-gold-freeze.json"
    assert freeze_path.exists(), "Independent gold has not been frozen"
    freeze = json.loads(freeze_path.read_text())
    for filename, expected_hash in freeze["sha256"].items():
        assert hashlib.sha256((ROOT / "eval" / filename).read_bytes()).hexdigest() == expected_hash
    old = [json.loads(s) for s in (ROOT / "eval/bank-retrieval-gold.jsonl").read_text().splitlines()]
    extended = [json.loads(s) for s in (ROOT / "eval/bank-retrieval-extended.jsonl").read_text().splitlines()]
    negatives = [json.loads(s) for s in (ROOT / "eval/bank-retrieval-negatives.jsonl").read_text().splitlines()]
    assert len(old) == 10 and len(extended) == 12 and len(negatives) == 6
    assert not {c["query"] for c in old} & {c["query"] for c in extended}
    for case in extended:
        assert case["expected_fragment_ids"] and case["annotated_by"]
        assert case["page_checked"] and case["reason"] and date.fromisoformat(case["as_of"])
    assert {c["category"] for c in negatives} == {"tax_rate", "refinery_rate", "corporate", "future", "expired", "undated"}
    assert all(c["expected_fragment_ids"] == [] for c in negatives)
