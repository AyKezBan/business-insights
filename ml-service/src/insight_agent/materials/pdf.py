"""Page-aware text extraction with explicit failure accounting for scanned PDFs."""
import hashlib
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from pypdf import PdfReader

from insight_agent.materials.models import DocumentMetadata, PageText
from insight_agent.materials.ocr import OCRBackend


def render_page(path: Path, page: int, output: Path) -> Path:
    prefix = output / f"page-{page}"
    subprocess.run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", "300",
        "-scale-to", "3000", "-png", "-singlefile", str(path), str(prefix)],
        check=True, capture_output=True, timeout=120)
    image = prefix.with_suffix(".png")
    if not image.is_file():
        raise RuntimeError("PDF renderer did not produce an image")
    return image


def extract_pdf(path: Path, metadata: DocumentMetadata, ocr: OCRBackend) -> list[PageText]:
    def failed(page, exc):
        return PageText(doc_id=metadata.doc_id, page=page, text="", method="text",
                        status="failed", errors=[f"{type(exc).__name__}: {exc}"])
    try:
        if hashlib.sha256(path.read_bytes()).hexdigest() != metadata.sha256:
            raise ValueError("PDF hash does not match manifest")
        reader = PdfReader(path)
        if len(reader.pages) != metadata.page_count:
            raise ValueError("PDF page count does not match manifest")
    except Exception as exc:
        # A corrupt input still gets one audit record per expected physical page.
        return [failed(i, exc) for i in range(1, metadata.page_count + 1)]
    pages = []
    with TemporaryDirectory(prefix="insight-pdf-") as temporary:
        for number, page in enumerate(reader.pages, 1):
            method = "text"
            try:
                text = (page.extract_text() or "").strip()
                if not text:
                    method = "tesseract"
                    text = ocr.recognize(render_page(path, number, Path(temporary)), metadata.language).strip()
                status = "empty" if not text else "needs_review" if "\ufffd" in text or "\x00" in text else "ready"
                pages.append(PageText(doc_id=metadata.doc_id, page=number, text=text, method=method, status=status))
            except Exception as exc:
                result = failed(number, exc)
                result.method = method
                pages.append(result)
    return pages
