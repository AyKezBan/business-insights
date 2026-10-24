import json
import re
from importlib.metadata import version
from pathlib import Path

from pypdf import PdfReader

from insight_agent.materials.artifacts import digest, safe_path, write_immutable
from insight_agent.materials.models import CorpusSnapshot, DocumentMetadata, ImportReport, PageArtifact, PageText
from insight_agent.materials.ocr import OCRBackend
from insight_agent.materials.pdf import extract_pdf
from insight_agent.materials.review import apply_reviews
from insight_agent.schemas import BankFragment


def load_document_manifest(path: Path, pdf_root: Path) -> list[DocumentMetadata]:
    documents = [DocumentMetadata.model_validate(entry) for entry in json.loads(path.read_text("utf-8"))["documents"]]
    if len({d.doc_id for d in documents}) != len(documents):
        raise ValueError("Duplicate document ID")
    for doc in documents:
        source = safe_path(pdf_root, doc.relative_path)
        if digest(source.read_bytes()) != doc.sha256 or doc.doc_id != "bank-" + doc.sha256:
            raise ValueError(f"PDF hash mismatch: {doc.title}")
        if len(PdfReader(source).pages) != doc.page_count:
            raise ValueError(f"Page count mismatch: {doc.title}")
    return documents


def chunk_page(page: PageText, metadata: DocumentMetadata, max_chars: int = 2000) -> list[BankFragment]:
    if page.status not in ("ready", "needs_review") or not page.text.strip():
        return []
    if max_chars < 20:
        raise ValueError("Chunk limit must be at least 20 characters")
    text = page.text
    out, start = [], 0
    while start < len(text):
        while start < len(text) and text[start].isspace():
            start += 1
        end = min(start + max_chars, len(text))
        paragraph = re.search(r"\n\s*\n|(?<=[.!?])\s+(?=[А-ЯЁA-Z])", text[start:end])
        if paragraph:
            end = start + paragraph.start()
        elif end < len(text):
            boundaries = list(re.finditer(r"(?<=[.!?])\s+|\n\s*\n", text[start:end]))
            if boundaries:
                end = start + boundaries[-1].start()
            else:
                space = text.rfind(" ", start, end)
                end = space if space > start else end
        while end > start and text[end - 1].isspace():
            end -= 1
        if end <= start:
            break
        body = text[start:end]
        text_hash = digest(body.encode())
        out.append(BankFragment(fragment_id=f"{metadata.doc_id}:p{page.page}:s{start}:{text_hash[:16]}",
            doc_id=metadata.doc_id, doc_title=metadata.title, doc_date=metadata.doc_date,
            page=page.page, text=body, topics=metadata.topics, content_sha256=text_hash,
            char_start=start, char_end=end, extraction_method=page.method))
        start = end
    return out


def prepare_bank(pdf_root: Path, manifest_path: Path, store_root: Path, ocr: OCRBackend,
                 reviews: Path | None = None, *, text_overrides: Path | None = None) -> CorpusSnapshot:
    documents = load_document_manifest(manifest_path, pdf_root)
    snapshot = CorpusSnapshot(tool_versions={"pypdf": version("pypdf"), "ocr": getattr(ocr, "version", type(ocr).__name__),
                                            "render": "pdftoppm:300dpi:max3000px"})
    snapshot.tool_versions.update(getattr(ocr, "tool_versions", {}))
    extraction_tools = dict(snapshot.tool_versions)
    for metadata in documents:
        source = safe_path(pdf_root, metadata.relative_path)
        raw_path = f"raw/{metadata.doc_id}/source.pdf"
        write_immutable(store_root, raw_path, source.read_bytes())
        snapshot.documents.append(metadata.model_copy(update={"relative_path": raw_path}))
        cache_id = digest(json.dumps({"doc": metadata.sha256, "tools": extraction_tools,
                                     "pipeline": snapshot.pipeline_version}, sort_keys=True).encode())
        cache_path = safe_path(store_root, f"staging/{cache_id}.json")
        pages = []
        if cache_path.exists():
            pages = [PageText.model_validate(p) for p in json.loads(cache_path.read_text("utf-8"))]
        if len(pages) != metadata.page_count or any(p.status == "failed" for p in pages):
            pages = extract_pdf(source, metadata, ocr)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps([p.model_dump(mode="json") for p in pages], ensure_ascii=False), "utf-8")
        corrected = set()
        if text_overrides is not None:
            for i, page in enumerate(pages):
                override = safe_path(text_overrides, f"{metadata.doc_id}/{page.page:04}.txt")
                if override.exists():
                    text = override.read_text("utf-8")
                    if not text.strip():
                        raise ValueError("Manual page correction cannot be empty")
                    pages[i] = page.model_copy(update={"text": text, "status": "needs_review", "errors": []})
                    corrected.add(page.page)
                    snapshot.tool_versions[f"override:{metadata.doc_id}:p{page.page}"] = digest(text.encode())
        extraction_revision = digest(json.dumps([p.model_dump(mode="json") for p in pages],
                                                sort_keys=True, ensure_ascii=False).encode())
        for page in pages:
            relative, text_hash = None, None
            if page.status != "failed":
                relative = f"pages/{metadata.doc_id}/{extraction_revision}/{page.page:04}.txt"
                text_hash = digest(page.text.encode())
                write_immutable(store_root, relative, page.text.encode())
            snapshot.pages.append(PageArtifact(doc_id=metadata.doc_id, page=page.page,
                extraction_revision=extraction_revision, relative_text_path=relative,
                text_sha256=text_hash, method=page.method, status=page.status, errors=page.errors))
            fragments = chunk_page(page, metadata)
            if page.page in corrected:
                for fragment in fragments:
                    fragment.extraction_method = page.method + "+manual"
            snapshot.bank_fragments.extend(fragments)
    snapshot.bank_fragments = apply_reviews(snapshot.bank_fragments, reviews)
    snapshot.report = ImportReport(documents=len(snapshot.documents), pages=len(snapshot.pages),
        fragments=len(snapshot.bank_fragments), errors=[f"{p.doc_id} page {p.page}: {e}" for p in snapshot.pages for e in p.errors])
    candidates = safe_path(store_root, "staging/bank-candidates.jsonl")
    candidates.parent.mkdir(parents=True, exist_ok=True)
    candidates.write_text("".join(f.model_dump_json() + "\n" for f in snapshot.bank_fragments), "utf-8")
    return snapshot
