"""Synthetic recovery, bounded analysis, policy isolation and real preview tests."""
import io
import json
import os
from pathlib import Path
import random
import zipfile
import zlib

import pikepdf
import pytest

from book2pdf.models import Options
from book2pdf.worker import convert
from book2pdf.state import sha256


def synthetic_mask_book(shift=731):
    from PIL import Image
    from book2pdf.strategies.image_smask import _mask_template
    pixels = random.Random(41).randbytes(128*128*3)
    buf=io.BytesIO(); Image.frombytes('RGB',(128,128),pixels).save(buf,format='JPEG',quality=95)
    jpeg=buf.getvalue(); assert len(jpeg)>4096
    content=b'q 100 0 0 100 22 22 cm /Im Do Q\n'
    bodies={1:b'<< /Type /Catalog /Pages 2 0 R >>',2:b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            3:b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 144 144] /Resources << /XObject << /Im 7 0 R >> >> /Contents 6 0 R >>',
            6:b'<< /Length '+str(len(content)).encode()+b' >>\nstream\n'+content+b'endstream',
            7:b'<< /Type /XObject /Subtype /Image /Width 128 /Height 128 /BitsPerComponent 8 /ColorSpace /DeviceRGB /SMask 8 0 R /Filter /DCTDecode /Length '+str(len(jpeg)).encode()+b' >>\nstream\n'+jpeg+b'\nendstream'}
    for n in range(1,55):bodies.setdefault(n,str(n).encode())
    # Intact sibling masks are referenced by real image dictionaries as well.
    bodies[9]=bodies[7].replace(b'/SMask 8 ',b'/SMask 10 ')
    bodies[11]=bodies[7].replace(b'/SMask 8 ',b'/SMask 12 ')
    data=bytearray(b'%PDF-1.4\n%abcd\n'); positions={}
    order=[8,7]+[n for n in range(1,55) if n not in (8,7)]
    for n in order:
        positions[n]=len(data)
        data.extend(_mask_template(n,0,255) if n in (8,10,12) else f'{n} 0 obj\n'.encode()+bodies[n]+b'\nendobj\n')
    xref=len(data);data.extend(b'xref\n0 55\n0000000000 65535 f \n')
    for n in range(1,55):data.extend(f'{positions[n]:010d} 00000 n \n'.encode())
    data.extend(f'trailer\n<< /Size 55 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    data[:positions[7]+2]=b'!'*(positions[7]+2)
    return b'BKC'+b'Z'*(shift-3)+data,jpeg


@pytest.fixture
def mask_book(tmp_path):
    raw,jpeg=synthetic_mask_book()
    p=tmp_path/'ספר עם רווח.book';p.write_bytes(raw)
    return p,jpeg


def test_large_image_two_byte_repair_and_mask_ambiguity(mask_book,tmp_path):
    import pymupdf
    source,jpeg=mask_book;before=sha256(source);results=[];pixels=[]
    safe=convert(source,tmp_path/'safe.pdf',Options(),tmp_path/'state.sqlite3')
    assert safe.status=='PREVIEW_AVAILABLE_WITH_ASSUMPTIONS' and not (tmp_path/'safe.pdf').exists()
    for policy in ('opaque','transparent'):
        out=tmp_path/(policy+'.pdf')
        r=convert(source,out,Options(recovery='reconstruction-preview-'+policy),tmp_path/'state.sqlite3')
        assert r.status=='RECONSTRUCTED_PREVIEW',r.error
        assert r.page_count==1 and r.affected_pages==[1]
        image=next(x for x in r.reconstructed_objects if x['role']=='surviving-image-header')
        assert image['damaged_byte_range'][1]-image['damaged_byte_range'][0]==2
        assert image['source_end']-image['source_start']>4096
        with pikepdf.open(out) as pdf:assert pdf.get_object((7,0)).read_raw_bytes()==jpeg
        with pymupdf.open(out) as pdf:pixels.append(pdf[0].get_pixmap().samples)
        results.append(r)
    assert pixels[0]!=pixels[1] and sha256(source)==before


def test_preview_cache_and_pdf_provenance(mask_book,tmp_path):
    from book2pdf.export import export_pages
    from book2pdf.provenance import embedded_provenance
    source,_=mask_book;out=tmp_path/'preview.pdf';state=tmp_path/'state.sqlite3'
    options=Options(recovery='reconstruction-preview-opaque')
    r=convert(source,out,options,state);assert r.status=='RECONSTRUCTED_PREVIEW',r.error
    assert convert(source,out,options,state).status=='SKIPPED_EXISTING_VALID'
    assert convert(source,out,Options(recovery='exact'),state).status!='SKIPPED_EXISTING_VALID'
    assert convert(source,out,Options(recovery='reconstruction-preview-transparent'),state).status!='SKIPPED_EXISTING_VALID'
    # No matching sidecar/journal: embedded provenance still cannot become exact.
    copied=tmp_path/'copy.pdf'
    assert convert(out,copied,Options(),tmp_path/'other.sqlite3').status=='PREVIEW_AVAILABLE_WITH_ASSUMPTIONS'
    e=export_pages(out,tmp_path/'selected.pdf','1')
    assert e.status=='RECONSTRUCTED_PREVIEW',e.error
    assert embedded_provenance(tmp_path/'selected.pdf')['preview_policy']=='opaque'
    assert e.preservation['page_streams_byte_identical']


@pytest.mark.parametrize('codec',['gzip','zlib','zip','bzip2','xz'])
def test_safe_container_fallback(codec,tmp_path,pdf_bytes):
    import gzip
    import bz2
    import lzma
    if codec=='zip':
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:archive.writestr('ספר.pdf',pdf_bytes)
        data=buf.getvalue()
    else:data={'gzip':gzip.compress,'zlib':zlib.compress,'bzip2':bz2.compress,'xz':lzma.compress}[codec](pdf_bytes)
    source=tmp_path/'unknown.book';source.write_bytes(data)
    r=convert(source,tmp_path/'out.pdf',Options(analysis_dir=str(tmp_path/'research')),tmp_path/'state.sqlite3')
    assert r.status=='PASS_DECODED',r.to_dict()
    assert r.page_count==3 and source.read_bytes()==data
    assert r.validation_mode=='all_pages'
    assert Path(r.analysis_package,'report.json').is_file()
    with pikepdf.open(io.BytesIO(pdf_bytes)) as original,pikepdf.open(tmp_path/'out.pdf') as output:
        assert original.pages[0].Resources.XObject.Im.read_raw_bytes()==output.pages[0].Resources.XObject.Im.read_raw_bytes()


@pytest.mark.parametrize('kind',['traversal','multi','bomb','encrypted'])
def test_archive_limits_and_no_extraction(kind,tmp_path,pdf_bytes):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('../escape.pdf' if kind=='traversal' else 'one.pdf',b'x'*500000 if kind=='bomb' else pdf_bytes)
        if kind=='multi':archive.writestr('two.pdf',pdf_bytes)
    data=bytearray(buf.getvalue())
    if kind=='encrypted':
        for magic,field in [(b'PK\x03\x04',6),(b'PK\x01\x02',8)]:
            offset=data.find(magic);data[offset+field]|=1
    source=tmp_path/'unsafe.book';source.write_bytes(data)
    r=convert(source,tmp_path/'out.pdf',Options(analysis_dir=str(tmp_path/'research')),tmp_path/'state.sqlite3')
    assert r.status==('KEY_REQUIRED' if kind=='encrypted' else 'NEW_VARIANT_DETECTED'),r.to_dict()
    assert not (tmp_path/'out.pdf').exists() and not (tmp_path.parent/'escape.pdf').exists()


def test_family_comparison_infers_only_proven_equalities(tmp_path):
    from book2pdf.research import compare_family
    paths=[]
    for size in (9000,10000):
        p=tmp_path/f'{size}.book';p.write_bytes(b'BKF'+size.to_bytes(4,'little')+random.Random(size).randbytes(size-7));paths.append(p)
    r=compare_family(paths,root=str(tmp_path/'research'))
    family=r['families']['BKF']
    assert family['sample_count']==2
    assert {'offset':3,'width':4,'endian':'little'} in family['size_field_candidates']
    assert family['version_fields']=='NOT_PROVEN'
    assert r['files'][1]['family_comparison']['sample_count']==2
    assert all(p['status']=='DECODER_REQUIRED' for p in r['files'])


def test_real_625593_decodes_before_preview(tmp_path):
    source=Path(os.environ.get('BOOK2PDF_SAMPLE_625593','private-samples/not-provided.book'))
    if not source.exists():pytest.skip('Private 625593 sample unavailable')
    before=sha256(source)
    r=convert(source,tmp_path/'preview.pdf',Options(recovery='reconstruction-preview-opaque'),tmp_path/'state.sqlite3')
    assert r.status=='PASS_DECODED',r.error
    assert r.detected_wrapper_offset==3674 and r.page_count==281
    assert not r.affected_pages and not r.missing_data_synthesized
    assert r.preservation['full_component_bytes_preserved']
    import pikepdf
    with pikepdf.open(tmp_path/'preview.pdf') as pdf:
        assert pdf.get_object((8,0)).read_raw_bytes()==b'\xff'
    assert sha256(source)==before


def sleeping_research_worker(source,scratch):
    import time
    time.sleep(60)


def test_analysis_timeout_is_bounded_and_reported(monkeypatch,tmp_path):
    import time
    from dataclasses import replace
    from book2pdf import research
    monkeypatch.setattr(research,'_child',sleeping_research_worker)
    monkeypatch.setattr(research,'LIMITS',replace(research.LIMITS,wall_seconds=.2))
    source=tmp_path/'input.book';source.write_bytes(b'opaque')
    start=time.monotonic()
    result=research.analyze_file(source,root=str(tmp_path/'analysis'))
    assert time.monotonic()-start<5
    assert result['status']=='UNSUPPORTED_AFTER_ANALYSIS' and not result['analysis_completed']
    assert Path(result['package'],'report.json').exists()
    assert not list((tmp_path/'analysis').glob('.analysis-work-*'))


def test_decoded_viewer_status_is_truthful(qtbot,tmp_path,pdf_bytes):
    import gzip
    from book2pdf.gui.viewer_window import ViewerWindow
    source=tmp_path/'compressed.book';source.write_bytes(gzip.compress(pdf_bytes))
    w=ViewerWindow(source);qtbot.addWidget(w);w.show()
    try:
        qtbot.waitUntil(lambda:w.open_result is not None or w.worker is None,timeout=30000)
        assert w.open_result['status']=='PASS_DECODED',w.last_error
        assert 'PASS_DECODED' in w.banner.text() and 'PASS_EXACT' not in w.banner.text()
    finally:
        w.close();qtbot.waitUntil(lambda:w.worker is None,timeout=30000)


def test_reverse_print_preserves_affected_page_provenance(qtbot,tmp_path,pdf_bytes):
    from types import SimpleNamespace
    from PySide6.QtPrintSupport import QPrinter
    from book2pdf.provenance import stamp_preview,embedded_provenance
    from book2pdf.gui.printing import PrintThread
    source=tmp_path/'assumed.pdf';source.write_bytes(pdf_bytes)
    stamp_preview(source,SimpleNamespace(recovery_class='RECONSTRUCTED_PREVIEW',preview_policy='opaque',
        fidelity='NOT_PROVEN',missing_data_synthesized=True,assumptions=['Test provenance'],affected_pages=[1]))
    before=sha256(source);output=tmp_path/'reverse.pdf'
    printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(output));printer.setPageOrder(QPrinter.PageOrder.LastPageFirst)
    worker=PrintThread(source,printer,[1,2,3],original_source=source)
    completed=[];failed=[];worker.completed.connect(completed.append);worker.failed.connect(failed.append)
    worker.run()
    assert not failed,failed
    assert completed[0]['pages']==[3,2,1]
    assert embedded_provenance(output)['affected_pages']==[3]
    assert sha256(source)==before
