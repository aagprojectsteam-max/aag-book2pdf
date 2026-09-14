"""Known synthetic native pixels plus strict framing/provenance regression."""
import hashlib
import random
import struct
from pathlib import Path

import pikepdf
import pytest

from book2pdf.bkf.format import parse, decode_record, iff, BKFError
from book2pdf.bkf.format import transform
from book2pdf.bkf.native import Decoder, library_path
from book2pdf.models import Options
from book2pdf.worker import convert, _cache_recovery_compatible
from book2pdf.export import export_pages
from book2pdf.provenance import embedded_provenance

FIXTURE=Path(__file__).parent/'fixtures/bkf-noise.djvu'


def wrap(items):
    directory=bytearray();encoded=[];offset=0
    for name,plain in items:
        sentinel=plain==b'\0'
        raw=plain if sentinel else transform(plain[:200],encode=True)+plain[200:]
        directory+=name.encode()+b'\0'+struct.pack('<II',0 if sentinel else offset,len(raw))
        encoded.append(raw);offset+=len(raw)
    prefix=struct.pack('<I',len(directory))+b'metadata!!'+directory
    return b'BKF'+b'head'+transform(prefix,encode=True)+b''.join(encoded)


def test_prefix_boundary_preserves_unencoded_tail():
    plain=FIXTURE.read_bytes();raw=transform(plain[:200],encode=True)+plain[200:]
    assert len(plain)>200 and decode_record(raw)==plain
    assert transform(raw)!=plain  # Regression: never transform the already plain tail.
    b=parse(wrap([('long-name',plain),('opaque',b'\0'),('last',plain)]))
    assert len(b.pages)==2 and b.entries[1].kind=='UNKNOWN'
    assert b.entries[1].raw==b'\0' and b.entries[1].stored_offset==0
    assert b.entries[-1].offset+len(b.entries[-1].raw)==b.base+sum(len(e.raw) for e in b.entries)


@pytest.mark.parametrize('mutate',[lambda d:d[:-1],lambda d:d+b'\0',lambda d:b'BKC'+d[3:],lambda d:d[:30]])
def test_reject_incomplete_or_wrong_family(mutate):
    with pytest.raises(BKFError):parse(mutate(wrap([('scan',FIXTURE.read_bytes())])))


@pytest.mark.parametrize('name',['../escape','a/b','a\\b','C:escape','.','..','CON','NUL.txt','LPT1','bad?name','trailing.'])
def test_paths_rejected(name):
    with pytest.raises(BKFError):parse(wrap([(name,FIXTURE.read_bytes())]))


def test_duplicate_names_and_corrupt_payload_extent():
    with pytest.raises(BKFError):parse(wrap([('A',FIXTURE.read_bytes()),('a',FIXTURE.read_bytes())]))
    data=bytearray(FIXTURE.read_bytes());data[38:42]=(1000000).to_bytes(4,'big')
    with pytest.raises(BKFError):parse(wrap([('scan',data)]))


def form(kind,children):
    body=kind+b''.join(k+len(v).to_bytes(4,'big')+v+(b'\0' if len(v)%2 and i<len(children)-1 else b'') for i,(k,v) in enumerate(children))
    return b'AT&TFORM'+len(body).to_bytes(4,'big')+body


def test_recursive_iff_unknown_chunks_padding_and_dependencies():
    data=form(b'DJVI',[(b'Xxxx',b'a'),(b'INCL',b'dictionary')])
    kind,chunks,deps=iff(data)
    assert kind=='DJVI' and deps==('dictionary',) and chunks[2]['offset']%2==0
    with pytest.raises(BKFError,match='Missing'):parse(wrap([('scan',FIXTURE.read_bytes()),('res',data)]))
    cyclic=form(b'DJVI',[(b'INCL',b'res')])
    with pytest.raises(BKFError,match='Cyclic'):parse(wrap([('scan',FIXTURE.read_bytes()),('res',cyclic)]))


@pytest.mark.skipif(not library_path(),reason='DjVuLibre optional runtime unavailable')
def test_native_pixels_are_known_original_bits():
    decoder=Decoder()
    try:result=decoder.render(FIXTURE)
    finally:decoder.close()
    assert result['width']==40 and result['height']==40 and result['bitonal']
    assert result['pixels']==random.Random(4701).randbytes(200)


@pytest.mark.skipif(not library_path(),reason='DjVuLibre optional runtime unavailable')
def test_conversion_export_provenance_resume_and_source_unchanged(tmp_path):
    source=tmp_path/'unrelated-name.book';data=wrap([('a',FIXTURE.read_bytes()),('unknown',b'\0'),('b',FIXTURE.read_bytes()),('shared',form(b'DJVI',[]))]);source.write_bytes(data)
    out=tmp_path/'book.pdf';state=tmp_path/'state.sqlite3'
    result=convert(source,out,Options(),state)
    assert result.status=='PASS_DECODED',result.error
    assert result.page_count==2 and result.preservation['native_pixels_preserved']
    assert result.preservation['opaque_entries'][0]['opaque_hex']=='00'
    assert embedded_provenance(out)['recovery_class']=='DECODED_ORIGINAL_CONTENT'
    assert convert(source,out,Options(),state).status=='SKIPPED_EXISTING_VALID'
    old=result.to_dict();old['preservation']=dict(old['preservation'],decoder_version=-1)
    assert not _cache_recovery_compatible('safe-salvage',old)
    for field in ('assembly_version', 'assumptions'):
        old=result.to_dict();old['preservation']=dict(old['preservation'], **{field: None})
        assert not _cache_recovery_compatible('safe-salvage',old)
    selected=tmp_path/'part.pdf';partial=export_pages(out,selected,'2')
    assert partial.status=='PASS_DECODED' and partial.page_count==1,partial.error
    with pikepdf.open(selected) as pdf:
        assert pdf.pages[0].obj.AF[0].EF.F.read_bytes()==FIXTURE.read_bytes()
        assert pdf.pages[0].obj.AF[1].EF.F.read_bytes()==form(b'DJVI',[])
        assert str(pdf.pages[0].obj.AF[1].F)=='shared'
        assert pdf.pages[0].Resources.XObject.Scan.read_bytes()==random.Random(4701).randbytes(200)
    assert len(embedded_provenance(selected)['preservation']['pages'])==1
    assert export_pages(out,selected,'1').status=='FAIL_REPAIR'
    reopened=convert(selected,tmp_path/'reopen.pdf',Options(),tmp_path/'new-state.sqlite3')
    assert reopened.status=='PASS_DECODED'
    assert source.read_bytes()==data and not list(tmp_path.glob('*.tmp.pdf'))


def test_unknown_bkf_still_runs_fallback(tmp_path):
    source=tmp_path/'unknown.book';source.write_bytes(b'BKF'+random.Random(9).randbytes(2000))
    result=convert(source,tmp_path/'out.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status=='DECODER_REQUIRED' and result.analysis_package
    assert not (tmp_path/'out.pdf').exists()


def test_output_budget_before_write():
    from io import BytesIO
    from book2pdf.bkf.pipeline import BoundedOutput
    stream=BytesIO();bounded=BoundedOutput(stream,4)
    bounded.write(b'ab')
    with pytest.raises(BKFError,match='disk budget'):bounded.write(b'cde')
    assert stream.getvalue()==b'ab'


def test_missing_native_runtime_is_not_an_unsupported_book(tmp_path,monkeypatch):
    source=tmp_path/'any-name.book';data=wrap([('page',FIXTURE.read_bytes())]);source.write_bytes(data)
    monkeypatch.setenv('BOOK2PDF_DJVU_LIBRARY',str(tmp_path/'absent-native-library'))
    result=convert(source,tmp_path/'out.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status=='RUNTIME_LOAD_FAILED',result.error
    assert not result.analysis_package and not (tmp_path/'out.pdf').exists()
    assert source.read_bytes()==data
