from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.app.config import Settings
from backend.app.memory.repository import Repository
from backend.app.research.documents import document_record, retain_document
from backend.app.research.pdf_extract import extract_pdf
from backend.app.schemas import ImportRequest


def pdf(*, text=True, encrypted=False, blank_second=False):
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    if text:
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 30 740 Td (EXMP FY2025 revenue USD 42 million.) Tj 0 -20 Td (Debt USD 5 million at 2025-12-31.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
    if blank_second:
        writer.add_blank_page(width=612,height=792)
    if encrypted:
        writer.encrypt('fixture-password')
    buffer=BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_extracts_page_line_provenance_and_flags_unread_pages():
    result=extract_pdf(pdf(blank_second=True))
    assert '[PDF page 1]' in result['content'] and 'EXMP FY2025 revenue USD 42 million.' in result['content']
    metadata=result['metadata']
    assert metadata['page_count'] == 2
    assert metadata['pages'][0]['start_line'] == 4
    assert metadata['pages'][0]['readable'] and not metadata['pages'][1]['readable']
    assert 'OCR was not performed' in metadata['warnings'][0]


@pytest.mark.parametrize('raw',[b'%PDF-corrupt',pdf(text=False),pdf(encrypted=True),b'not a pdf'])
def test_unreadable_documents_fail_explicitly(raw):
    with pytest.raises(ValueError):
        extract_pdf(raw)


def test_original_is_immutable_hash_bound_and_namespace_scoped(tmp_path):
    repo=Repository(config=Settings(project_root=Path(__file__).resolve().parents[2],data_dir=tmp_path,enable_market_connectors=False,enable_reddit_intake=False))
    raw=pdf()
    result=extract_pdf(raw)
    source=repo.import_evidence(ImportRequest(namespace='real',kind='evidence',title='Synthetic filing',content=result['content'],idempotency_key='pdf-fixture'))['source_id']
    retain_document(repo,'real',source,raw,result['metadata'])
    assert document_record(repo,'real',source,include_bytes=True)['bytes'] == raw
    assert 'bytes' not in document_record(repo,'real',source)
    assert document_record(repo,'demo',source) is None
    with pytest.raises(ValueError,match='hash'):
        retain_document(repo,'real',source,b'changed',result['metadata'])
    with pytest.raises(ValueError,match='belong'):
        retain_document(repo,'demo',source,raw,result['metadata'])


def test_compressed_stream_limit_is_enforced_during_decoding():
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    stream=DecodedStreamObject()
    stream.set_data(b' ' * 2_100_000)
    page[NameObject('/Contents')]=writer._add_object(stream.flate_encode())
    buffer=BytesIO()
    writer.write(buffer)
    assert len(buffer.getvalue()) < 10_000
    with pytest.raises(ValueError,match='extraction unavailable'):
        extract_pdf(buffer.getvalue())
