"""Apply visually reviewed metadata to a NEW private store, reusing immutable OCR.

No extraction, network calls or source text output. The original store and its
old snapshots remain unchanged. Run from ml-service with PYTHONPATH=src.
"""
import argparse
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from insight_agent.materials.review import apply_reviews
from insight_agent.materials.store import load_snapshot, publish_snapshot


def validate_witnesses(snapshot, decisions, audit):
    pages = {(p.doc_id, p.page): p for p in snapshot.pages}
    reviewed = {(p["doc_id"], p["page"]): p for p in audit["pages"]}
    if len(reviewed) != len(audit["pages"]) or reviewed.keys() != pages.keys():
        raise ValueError("Review must cover every physical page exactly once")
    docs = {d.doc_id: d for d in snapshot.documents}
    for key, page in pages.items():
        witness = reviewed[key]
        if not isinstance(witness.get("image_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", witness["image_sha256"]):
            raise ValueError("Page requires a nonempty SHA256 of the inspected image")
        if (witness["text_sha256"] != page.text_sha256
                or witness["extraction_revision"] != page.extraction_revision
                or witness["pdf_sha256"] != docs[page.doc_id].sha256):
            raise ValueError("Page witness does not match immutable source")
    fragments = {f.fragment_id: f for f in snapshot.bank_fragments}
    for identifier, decision in decisions.items():
        fragment = fragments.get(identifier)
        if fragment is None or fragment.content_sha256 != decision["content_sha256"]:
            raise ValueError("Review fragment does not exist with the exact content hash")
        if decision["review_status"] != "verified":
            continue
        witness = audit["fragments"].get(identifier)
        if witness is None:
            raise ValueError("Verified fragment has no visual witness")
        page = reviewed[(fragment.doc_id, fragment.page)]
        for key in ("doc_id", "page", "content_sha256", "char_start", "char_end"):
            if witness[key] != getattr(fragment, key):
                raise ValueError(f"Fragment witness mismatch: {key}")
        for key in ("text_sha256", "extraction_revision", "image_sha256"):
            if witness[key] != page[key]:
                raise ValueError(f"Fragment/page witness mismatch: {key}")


def rebuild_review(source, destination, reviews, page_review):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists() or source == destination or source in destination.parents:
        raise ValueError("Review destination must be a new directory outside the source store")
    snapshot = load_snapshot(source)
    decisions = json.loads(Path(reviews).read_text("utf-8"))
    audit = json.loads(Path(page_review).read_text("utf-8"))
    validate_witnesses(snapshot, decisions, audit)
    # The reviewed path may be edited concurrently. Apply only the exact object
    # validated above through the existing strict ReviewDecision loader.
    with TemporaryDirectory(prefix="bank-review-") as temporary:
        frozen = Path(temporary) / "reviews.json"
        frozen.write_text(json.dumps(decisions), "utf-8")
        snapshot.bank_fragments = apply_reviews(snapshot.bank_fragments, frozen)
    shutil.copytree(source, destination)
    return publish_snapshot(destination, snapshot)


def main():
    folder = Path(__file__).parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--reviews", type=Path, default=folder / "bank-review.json")
    parser.add_argument("--page-review", type=Path, default=folder / "bank-page-review.json")
    args = parser.parse_args()
    revision = rebuild_review(args.source, args.destination, args.reviews, args.page_review)
    print(json.dumps({"store": str(args.destination), "revision": revision}))


if __name__ == "__main__":
    main()
