import hashlib
import json

import pytest

from insight_agent.materials.models import DocumentMetadata, PageText
from test_pdf_extraction import make_pdf


def test_manifest_checks_file_bytes_instead_of_trusting_filename(tmp_path):
    from insight_agent.materials.bank_corpus import load_document_manifest
    path=tmp_path/'source.pdf'; meta=make_pdf(path,'Verified statement')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[meta.model_dump(mode='json')]}))
    docs=load_document_manifest(manifest,tmp_path)
    assert docs[0].sha256==hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='hash'):
        load_document_manifest(manifest,tmp_path)


def test_fragment_offsets_hash_and_ids_survive_repeated_chunking():
    from insight_agent.materials.bank_corpus import chunk_page
    text='The rate remains 14.0 percent.\n\nA loan is not automatically repriced.\n\nDemand may slow.'
    meta=DocumentMetadata(doc_id='d',title='Synthetic report',sha256='a'*64,page_count=1,relative_path='x.pdf')
    page=PageText(doc_id='d',page=1,text=text,method='tesseract',status='ready')
    fragments=chunk_page(page,meta,max_chars=65)
    assert len(fragments)>=2
    assert [f.fragment_id for f in fragments]==[f.fragment_id for f in chunk_page(page,meta,max_chars=65)]
    for f in fragments:
        assert len(f.text)<=65
        assert text[f.char_start:f.char_end]==f.text
        assert f.content_sha256==hashlib.sha256(f.text.encode()).hexdigest()
        assert f.review_status=='pending' and f.page==1 and f.doc_date is None


def test_review_is_bound_to_fragment_content_not_just_id(tmp_path):
    from insight_agent.materials.bank_corpus import chunk_page
    from insight_agent.materials.review import apply_reviews
    meta=DocumentMetadata(doc_id='d',title='Report',sha256='a'*64,page_count=1,relative_path='x.pdf')
    page=PageText(doc_id='d',page=1,text='A verified bank assessment.',method='text',status='ready')
    fragments=chunk_page(page,meta)
    f=fragments[0]; reviews=tmp_path/'reviews.json'
    decision={'content_sha256':f.content_sha256,'review_status':'verified','statement_type':'bank_assessment'}
    reviews.write_text(json.dumps({f.fragment_id:decision}))
    assert apply_reviews(fragments,reviews)[0].review_status=='verified'
    decision['content_sha256']='b'*64
    reviews.write_text(json.dumps({f.fragment_id:decision}))
    assert apply_reviews(fragments,reviews)[0].review_status=='pending'


def test_failed_page_cannot_produce_bank_evidence():
    from insight_agent.materials.bank_corpus import chunk_page
    meta=DocumentMetadata(doc_id='d',title='Report',sha256='a'*64,page_count=1,relative_path='x.pdf')
    page=PageText(doc_id='d',page=1,text='Partial wrong text',method='tesseract',status='failed')
    assert chunk_page(page,meta)==[]


def test_immutable_artifact_cannot_be_overwritten_or_escape_root(tmp_path):
    from insight_agent.materials.artifacts import write_immutable
    write_immutable(tmp_path,'raw/source.pdf',b'old')
    write_immutable(tmp_path,'raw/source.pdf',b'old')
    with pytest.raises(ValueError,match='immutable'):
        write_immutable(tmp_path,'raw/source.pdf',b'new')
    with pytest.raises(ValueError):
        write_immutable(tmp_path,'../escape',b'x')
    assert (tmp_path/'raw/source.pdf').read_bytes()==b'old'


def test_prepare_bank_retains_raw_and_auditable_page_index(tmp_path):
    from insight_agent.materials.bank_corpus import prepare_bank
    from test_pdf_extraction import FakeOCR
    inputs=tmp_path/'input';inputs.mkdir()
    pdf=inputs/'x.pdf';meta=make_pdf(pdf,'The rate remains 14 percent.')
    manifest=tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'documents':[meta.model_dump(mode='json')]}))
    root=tmp_path/'store'
    result=prepare_bank(inputs,manifest,root,FakeOCR())
    assert len(result.pages)==1 and result.pages[0].status=='ready'
    assert (root/result.documents[0].relative_path).read_bytes()==pdf.read_bytes()
    assert (root/result.pages[0].relative_text_path).read_text()=='The rate remains 14 percent.'
    assert result.bank_fragments[0].review_status=='pending'
