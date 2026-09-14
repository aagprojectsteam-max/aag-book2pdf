import io
from pathlib import Path
import pytest
from PIL import Image
from book2pdf.turbosun import source,backend,TurboSunError
from book2pdf.turbosun.core import key_for
from test_turbosun_core import fixture,encrypt,FIELD


@pytest.fixture
def dataset(tmp_path,monkeypatch):
    root,meta,path,_=fixture(tmp_path)
    im=Image.new('1',(73,59),1);im.putpixel((20,20),0)
    stream=io.BytesIO();im.save(stream,format='TIFF',compression='group4',dpi=(300,300));raw=stream.getvalue()
    cipher=encrypt(raw,key_for(path.name,FIELD));path.write_bytes(cipher*3)
    bundle={'source':str(path),'relative':str(path.relative_to(root)),'group':1,'book_sequence':1,'basis_id':1,'catalogue_pages':2,'field':FIELD,'pages':[{'number':i+1,'offset':i*len(cipher),'length':len(cipher),'index_record':i+1} for i in range(3)]}
    monkeypatch.setattr(source,'catalogue',lambda *args:([bundle],[]))
    return root,path,raw


def test_lazy_metadata_random_access_supplemental(dataset):
    root,path,raw=dataset;d=source.Dataset(root)
    assert len(d.pages)==3 and d.decoded_pages==0
    assert d.decode_page(2)==raw and d.decoded_pages==1
    assert d.get_unplaced_assets()[0]['logical_page_number']==3
    assert d.pages[0]['dpi']==[300,300]
    assert d.pages[0]['page_provenance']=='CATALOGUE_F902_AND_PAGE_INDEX_F1'


@pytest.mark.parametrize('selection,count',[('1',1),('1-3',3),('1,3',2),(' 1-2,2-3 ',3)])
def test_lossless_selection_provenance(dataset,tmp_path,selection,count):
    from book2pdf.provenance import embedded_provenance
    import pymupdf
    root,path,raw=dataset;b=backend.Backend(root,tmp_path/'cache')
    r=b.export(tmp_path/'out.pdf',selection)
    assert r.status=='PASS_DECODED' and r.page_count==count
    record=embedded_provenance(tmp_path/'out.pdf')
    assert len(record['recovered_document']['pages'])==count
    assert all(p['tiff_sha256'] for p in record['recovered_document']['pages'])
    with pymupdf.open(tmp_path/'out.pdf') as pdf:
        page=pdf[0];pix=pymupdf.Pixmap(pdf,page.get_images()[0][0]);assert (pix.width,pix.height)==(73,59)
        assert pix.samples==Image.open(io.BytesIO(raw)).convert('L').tobytes()


def test_partial_does_not_decode_intermediate(dataset,tmp_path):
    root,_,_=dataset;b=backend.Backend(root,tmp_path/'cache',first_page=2)
    b.export(tmp_path/'selected.pdf',[3])
    assert b.dataset.decoded_pages==2 # first requested page + selected export only


def test_source_changed_invalidates_cache(dataset):
    root,path,_=dataset;d=source.Dataset(root);path.write_bytes(path.read_bytes()+b'changed')
    assert not d.unchanged()
    with pytest.raises(TurboSunError):d.decode_page(0)


def test_catalogue_changed_invalidates_cache(dataset):
    root,_,_=dataset;d=source.Dataset(root);p=next((root/'db').iterdir());p.write_bytes(p.read_bytes()+b'changed')
    assert not d.unchanged()
    with pytest.raises(TurboSunError):d.decode_page(0)


def test_no_clobber_atomic_and_source_protection(dataset,tmp_path,monkeypatch):
    root,path,raw=dataset;b=backend.Backend(root,tmp_path/'cache');out=tmp_path/'out.pdf'
    b.export(out,[1]);before=out.read_bytes()
    with pytest.raises(FileExistsError):b.export(out,[2])
    with pytest.raises(ValueError):b.export(root/'forbidden.pdf',[1])
    monkeypatch.setattr(backend,'validate_export',lambda *a:(_ for _ in ()).throw(ValueError('bad PDF')))
    with pytest.raises(ValueError):b.export(out,[2],True)
    assert out.read_bytes()==before and not list(tmp_path.glob('*.tmp.pdf'))
    assert not (root/'forbidden.pdf').exists()


def test_missing_catalogue_specific_error(tmp_path):
    with pytest.raises(TurboSunError) as e:source.Dataset(tmp_path)
    assert e.value.code=='TURBOSUN_CATALOGUE_MISSING'
    assert source.detect_dataset(tmp_path)=='NOT_TURBOSUN'


@pytest.mark.parametrize('start,count',[(-1,2),(0,100000),(31,2)])
def test_encrypted_range_bounds(start,count):
    reader=source.EncryptedReader(io.BytesIO(bytes(32)),0,32,bytes(32))
    with pytest.raises(TurboSunError):reader.read_at(start,count)


def test_cancellation_preserves_output(dataset,tmp_path):
    root,_,_=dataset;b=backend.Backend(root,tmp_path/'cache')
    def cancel(*args):raise InterruptedError('cancel')
    with pytest.raises(InterruptedError):b.export(tmp_path/'cancelled.pdf',[1,2],progress=cancel)
    assert not (tmp_path/'cancelled.pdf').exists()


def test_mixed_batch_discovery(dataset,tmp_path):
    from book2pdf.batch import discover
    root,_,_=dataset;book=tmp_path/'example.book';book.write_bytes(b'BKC')
    paths=list(discover([root,book],True))
    assert [p[0] for p in paths]==[root,book]


def test_resume_identity_and_no_clobber(dataset,tmp_path):
    from book2pdf.models import Options
    root,_,_=dataset;out=tmp_path/'out.pdf';state=tmp_path/'state.sqlite3'
    assert backend.convert(root,out,Options(),state).status=='PASS_DECODED'
    before=out.stat().st_mtime_ns
    assert backend.convert(root,out,Options(),state).status=='SKIPPED_EXISTING_VALID'
    assert out.stat().st_mtime_ns==before


def test_batch_cancel_token(dataset,tmp_path):
    from book2pdf.worker import convert
    from book2pdf.models import Options
    root,_,_=dataset;token=tmp_path/'cancel';token.touch()
    result=convert(root,tmp_path/'cancelled.pdf',Options(),tmp_path/'state.sqlite3',cancel_path=token)
    assert result.status=='CANCELLED'
    assert not (tmp_path/'cancelled.pdf').exists()


def test_content_change_even_with_original_stat_invalidates_identity(dataset):
    import os
    root,path,_=dataset;d=source.Dataset(root);st=path.stat();data=bytearray(path.read_bytes());data[-1]^=1
    path.write_bytes(data);os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns))
    assert not d.unchanged()


def test_cache_inside_source_refused(dataset):
    root,_,_=dataset
    with pytest.raises(ValueError):backend.Backend(root,root/'cache')
    assert not (root/'cache').exists()


def test_exported_pdf_reopens_with_shared_provenance(dataset,tmp_path):
    from book2pdf.export import export_pages
    from book2pdf.worker import convert
    from book2pdf.models import Options
    from book2pdf.provenance import embedded_provenance
    root,_,_=dataset;b=backend.Backend(root,tmp_path/'cache');original=tmp_path/'original.pdf'
    b.export(original,[1,3])
    result=export_pages(original,tmp_path/'selected.pdf',[2])
    assert result.status=='PASS_DECODED',result.error
    assert embedded_provenance(tmp_path/'selected.pdf')['recovered_document']['pages'][0]['source_page']==3
    result=convert(original,tmp_path/'copy.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status=='PASS_DECODED',result.error
    assert result.recovered_document['source_family']=='TURBOSUN'


def test_batch_and_cli_never_create_reports_inside_dataset(dataset,tmp_path):
    from book2pdf.batch import run_batch
    from book2pdf.cli import main
    root,_,_=dataset
    with pytest.raises(ValueError):run_batch([root],root/'out.pdf',explicit_file=True)
    assert main([str(root),'--pages','1','-o',str(root/'out.pdf')])==2
    assert not (root/'out.pdf').exists()
    assert not (root/'.book2pdf-state.sqlite3').exists()
    assert not list(root.glob('book2pdf-*.jsonl'))


def test_corrupt_catalogue_fields_have_turbosun_error(dataset,monkeypatch):
    root,_,_=dataset
    def invalid(*args):raise ValueError('invalid integer field')
    monkeypatch.setattr(source,'catalogue',invalid)
    with pytest.raises(TurboSunError) as e:source.Dataset(root)
    assert e.value.code=='TURBOSUN_UNSUPPORTED_VARIANT'


def test_supplemental_references_follow_selected_pdf_pages(dataset,tmp_path):
    from book2pdf.provenance import embedded_provenance
    from book2pdf.export import export_pages
    root,_,_=dataset;b=backend.Backend(root,tmp_path/'cache')
    full=tmp_path/'full.pdf';b.export(full,[1,2,3])
    result=export_pages(full,tmp_path/'extra.pdf',[3])
    assert result.status=='PASS_DECODED'
    model=embedded_provenance(tmp_path/'extra.pdf')['recovered_document']
    assert model['metadata']['supplemental_pages'][0]['page_index']==0
    assert model['metadata']['supplemental_pages'][0]['source_page']==3
    assert model['pages'][0]['page_index']==0
    b.export(tmp_path/'ordinary.pdf',[1])
    assert not embedded_provenance(tmp_path/'ordinary.pdf')['recovered_document']['metadata']['supplemental_pages']
