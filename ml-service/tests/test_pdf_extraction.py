import hashlib

import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from insight_agent.materials.models import DocumentMetadata


def make_pdf(path, text=None):
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=140)
    if text:
        font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),
            NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):
            DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(f'BT /F1 12 Tf 10 80 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(content)
    writer.write(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return DocumentMetadata(doc_id='bank-'+digest, title='Synthetic PDF', sha256=digest,
                            page_count=1, relative_path=path.name)


class FakeOCR:
    def __init__(self, text='OCR rate 17.5 percent', error=None):
        self.text, self.error = text, error

    def recognize(self, image_path, languages='rus+eng'):
        assert image_path.exists()
        if self.error:
            raise self.error
        return self.text


def test_text_layer_does_not_require_ocr(tmp_path):
    from insight_agent.materials.pdf import extract_pdf
    path = tmp_path/'native.pdf'
    metadata = make_pdf(path, 'Rate 17.5 percent; not reduced')
    pages = extract_pdf(path, metadata, FakeOCR(error=AssertionError('Unexpected OCR')))
    assert pages[0].page == 1
    assert pages[0].method == 'text'
    assert pages[0].status == 'ready'
    assert '17.5' in pages[0].text and 'not reduced' in pages[0].text


@pytest.mark.parametrize('text,status', [('OCR rate 17.5 percent','ready'), ('','empty')])
def test_scan_uses_ocr_without_inventing_empty_page_text(tmp_path, monkeypatch, text, status):
    from insight_agent.materials import pdf
    path = tmp_path/'scan.pdf'; metadata = make_pdf(path)
    image = tmp_path/'page.png'; image.write_bytes(b'synthetic-render')
    monkeypatch.setattr(pdf, 'render_page', lambda *args, **kwargs: image)
    pages = pdf.extract_pdf(path, metadata, FakeOCR(text))
    assert [(p.page,p.method,p.status,p.text) for p in pages] == [(1,'tesseract',status,text)]


def test_ocr_timeout_is_a_failed_page_not_success(tmp_path, monkeypatch):
    from insight_agent.materials import pdf
    path = tmp_path/'scan.pdf'; metadata = make_pdf(path)
    image = tmp_path/'page.png'; image.touch()
    monkeypatch.setattr(pdf, 'render_page', lambda *args, **kwargs: image)
    pages = pdf.extract_pdf(path, metadata, FakeOCR(error=TimeoutError('OCR timeout')))
    assert len(pages) == 1
    assert pages[0].status == 'failed'
    assert 'timeout' in pages[0].errors[0]


def test_corrupted_pdf_accounts_for_every_expected_page(tmp_path):
    from insight_agent.materials.pdf import extract_pdf
    path = tmp_path/'bad.pdf'; path.write_bytes(b'not a pdf')
    metadata = DocumentMetadata(doc_id='bad', title='Bad PDF', page_count=2,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(), relative_path='bad.pdf')
    pages = extract_pdf(path, metadata, FakeOCR())
    assert [p.page for p in pages] == [1,2]
    assert all(p.status == 'failed' for p in pages)
