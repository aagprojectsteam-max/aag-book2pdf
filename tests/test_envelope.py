"""Generic envelope metamorphic and application tests, with real PDF parsers."""
import io
import struct
import copy
import hashlib
import pytest
import pikepdf
from book2pdf.envelope import transform, parse_directory, decode_record, EnvelopeError
from book2pdf.container_decode import extract_pdf
from book2pdf.models import Options
from book2pdf.worker import convert, _cache_recovery_compatible
from book2pdf.export import export_pages, page_fingerprint
from book2pdf.provenance import embedded_provenance


def wrap(components, aliases=(), padding=b''):
    directory = bytearray(); content=bytearray(padding); ranges=[]
    for name, payload in components:
        ranges.append((len(content),len(payload)))
        directory += name + b'\0' + struct.pack('<II',len(content),len(payload))
        content += transform(payload[:200],encode=True) + payload[200:]
    for name,index in aliases:
        directory += name + b'\0' + struct.pack('<II',*ranges[index])
    return b'BKCopaque'[:7]+transform(struct.pack('<I',len(directory))+b'opaqueHEAD'+directory,encode=True)+content


@pytest.mark.parametrize('name',[b'a',b'long'*101,'ספר סרוק'.encode(),b'../../not-a-path'])
@pytest.mark.parametrize('policy',['exact','safe-salvage','reconstruction-preview-opaque'])
def test_decode_before_salvage_and_exact_full_component(tmp_path,pdf_bytes,monkeypatch,name,policy):
    raw=wrap([(name,pdf_bytes)],[(b'alias',0)],b'padding')
    source=tmp_path/'arbitrary.book';source.write_bytes(raw)
    monkeypatch.setattr('book2pdf.worker.detect',lambda *a,**k:pytest.fail('legacy detector must not run'))
    target=tmp_path/'full.pdf';state=tmp_path/'.book2pdf-state.sqlite3'
    r=convert(source,target,Options(recovery=policy),state)
    assert r.status=='PASS_DECODED',r.error
    assert r.page_count==3 and r.recovery_class=='DECODED_CONTAINER_CONTENT'
    assert not r.reconstructed_objects and not r.assumptions
    assert target.read_bytes()==pdf_bytes and source.read_bytes()==raw
    assert r.preservation['round_trip'] and r.preservation['origin']==parse_directory(raw).base
    assert convert(source,target,Options(recovery='exact'),state).status=='SKIPPED_EXISTING_VALID'
    reopened=convert(target,tmp_path/'reopened.pdf',Options(),state)
    assert reopened.status=='PASS_DECODED' and reopened.recovery_class==r.recovery_class,reopened.error
    assert (tmp_path/'reopened.pdf').read_bytes()==pdf_bytes


def test_multicomponent_select_by_content_not_name_order_or_size(tmp_path,pdf_bytes):
    raw=wrap([(b'book.pdf',b'sidecar'*99),(b'resource',pdf_bytes),(b'tail',b'unknown')],[(b'other',1)])
    source=tmp_path/'m.book';source.write_bytes(raw)
    path,e=extract_pdf(source,tmp_path)
    assert path.read_bytes()==pdf_bytes and len(e['components'])==3
    assert len(e['components'][1]['aliases_hex'])==2


@pytest.mark.parametrize('case',['two','none','overlap','truncated','length'])
def test_ambiguity_and_invalid_ranges_never_publish(tmp_path,pdf_bytes,case):
    data=wrap([(b'one',pdf_bytes),(b'two',pdf_bytes if case=='two' else b'unknown')])
    if case=='none':data=wrap([(b'none',b'unknown')])
    if case in ('overlap','length'):
        env=parse_directory(data);plain=bytearray(transform(data[7:env.base]));pos=14+4
        if case=='length':struct.pack_into('<I',plain,pos+4,len(data)*2)
        else:
            pos=plain.index(b'two\0')+4;struct.pack_into('<I',plain,pos,1)
        data=data[:7]+transform(plain,encode=True)+data[env.base:]
    if case=='truncated':data=data[:-100]
    source=tmp_path/'source.book';source.write_bytes(data)
    result=convert(source,tmp_path/'no.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status=='NEW_VARIANT_DETECTED',result.error
    assert not (tmp_path/'no.pdf').exists() and source.read_bytes()==data


def test_xref_stream_object_stream_incremental_and_tail(tmp_path,pdf_bytes):
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        out=io.BytesIO();pdf.save(out,object_stream_mode=pikepdf.ObjectStreamMode.generate)
    source=tmp_path/'input.book';raw=out.getvalue();source.write_bytes(wrap([(b'pdf',raw)]))
    r=convert(source,tmp_path/'packed.pdf',Options(),tmp_path/'.book2pdf-state.sqlite3')
    assert r.status=='PASS_DECODED',r.error
    assert (tmp_path/'packed.pdf').read_bytes()==raw
    from book2pdf.provenance import _stamp
    _stamp(tmp_path/'packed.pdf',{'test':'incremental'})
    raw=(tmp_path/'packed.pdf').read_bytes();source.write_bytes(wrap([(b'changed',raw)]))
    r=convert(source,tmp_path/'revision.pdf',Options(),tmp_path/'state2.sqlite3')
    assert r.status=='PASS_DECODED',r.error
    assert (tmp_path/'revision.pdf').read_bytes()==raw


def test_subset_lineage_cache_upgrade_and_no_clobber(tmp_path,pdf_bytes):
    source=tmp_path/'input.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    full=tmp_path/'full.pdf';state=tmp_path/'.book2pdf-state.sqlite3'
    result=convert(source,full,Options(),state);assert result.status=='PASS_DECODED',result.error
    for field in ('profile_version','round_trip','document_relation','decoded_component_sha256'):
        old=copy.deepcopy(result.to_dict());old['preservation'][field]=None
        assert not _cache_recovery_compatible('exact',old)
    old=copy.deepcopy(result.to_dict());old['engine_version']='1.4.0'
    assert not _cache_recovery_compatible('safe-salvage',old)
    for spec,count in [('1',1),('1-3',3),('1,3',2),('1-2,2-3',3)]:
        target=tmp_path/(spec+'.pdf');r=export_pages(full,target,spec,recovery_record=result.to_dict())
        assert r.status=='PASS_DECODED' and r.page_count==count,r.error
        model=embedded_provenance(target)['recovered_document']
        assert model['provenance']['document_relation']=='PAGE_SUBSET_REBUILT'
        assert not model['provenance']['full_component_bytes_preserved']
        assert r.fidelity!='DECODED_COMPONENT_BYTES_PRESERVED_NO_SYNTHESIS'
        reopened=convert(target,tmp_path/('re-'+spec+'.pdf'),Options(),state)
        assert reopened.status=='PASS_DECODED',reopened.error
        assert export_pages(full,target,'1',recovery_record=result.to_dict()).status=='FAIL_REPAIR'


def test_transform_reset_and_passthrough_mutations(tmp_path,pdf_bytes):
    raw=wrap([(b'one',b'opaque'),(b'pdf',pdf_bytes)])
    env=parse_directory(raw);start=env.base+env.entries[1].offset
    assert decode_record(raw[start:])==pdf_bytes
    # Continuing directory state into component cannot produce its PDF header.
    continued=transform(raw[7:]);assert not continued[start-7:].startswith(b'%PDF-')
    corrupt=bytearray(raw);corrupt[start]^=1
    source=tmp_path/'bad.book';source.write_bytes(corrupt)
    with pytest.raises(EnvelopeError,match='NO_UNIQUE'):extract_pdf(source,tmp_path)


def test_runtime_failure_is_not_format_failure(tmp_path,pdf_bytes,monkeypatch):
    source=tmp_path/'input.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    def missing(*a,**k):raise ModuleNotFoundError('missing native module')
    monkeypatch.setattr('book2pdf.worker.validate',missing)
    result=convert(source,tmp_path/'no.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status=='DEPENDENCY_MISSING' and not (tmp_path/'no.pdf').exists()


def test_font_warning_policy_does_not_hide_other_errors():
    from book2pdf.render_diagnostics import classify
    assert classify('freetype could not find any cmaps\n... repeated 8 times...','report-fonts')
    assert not classify('freetype could not find any cmaps\ninvalid image','report-fonts')
    assert not classify('... repeated 8 times...','report-fonts')
    assert not classify('freetype could not find any cmaps','strict')


def test_killed_worker_resumes_without_publishing_partial_pdf(tmp_path,pdf_bytes):
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path
    source=tmp_path/'input.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    target=tmp_path/'out.pdf';marker=tmp_path/'staging-ready'
    code='''import sys,time
from pathlib import Path
import book2pdf.worker as worker
from book2pdf.models import Options
def pause(*args,**kwargs):
 Path(sys.argv[3]).write_text('staged')
 time.sleep(60)
worker.validate=pause
worker.convert(Path(sys.argv[1]),Path(sys.argv[2]),Options(),Path(sys.argv[2]).parent/'state.sqlite3')
'''
    process=subprocess.Popen([sys.executable,'-c',code,str(source),str(target),str(marker)])
    try:
        deadline=time.monotonic()+15
        while not marker.exists() and time.monotonic()<deadline:
            time.sleep(.01)
        assert marker.exists()
        process.kill();process.wait(timeout=10)
        assert not target.exists() and list(tmp_path.glob('*.tmp.pdf'))
    finally:
        if process.poll() is None:process.kill();process.wait()
    result=convert(source,target,Options(),tmp_path/'state.sqlite3')
    assert result.status=='PASS_DECODED',result.error
    assert target.read_bytes()==pdf_bytes and not list(tmp_path.glob('*.tmp.pdf'))


def test_font_diagnostics_after_prior_viewer_renders(tmp_path,pdf_bytes,monkeypatch):
    import pymupdf
    from book2pdf.validator import validate,ValidationError
    path=tmp_path/'pdf.pdf';path.write_bytes(pdf_bytes)
    original=pymupdf.Page.get_pixmap
    def render(page,*args,**kwargs):
        pymupdf.mupdf.fz_warn('freetype could not find any cmaps')
        return original(page,*args,**kwargs)
    monkeypatch.setattr(pymupdf.Page,'get_pixmap',render)
    # Earlier rendering leaves both an initial warning and native repeat count.
    for _ in range(7):pymupdf.mupdf.fz_warn('freetype could not find any cmaps')
    for _ in range(2):
        count,warning=validate(path,True,font_policy='report-fonts')
        assert count==3 and 'freetype could not find any cmaps' in warning
    with pytest.raises(ValidationError):validate(path,True)  # strict is still strict
