"""Temporal safety: readiness is not acceptance; rendering cannot queue behind validation."""
import time
from pathlib import Path
import pytest

from book2pdf.interactive import Backend, export_selection
from book2pdf.state import sha256
from book2pdf.gui.viewer_window import ViewerWindow
from test_envelope import wrap
from test_viewer import close_viewer


def held_validation(source,target,recovery,expected_hash=None,overwrite=False):
    from book2pdf.supervised import checkpoint
    root=Path(target).parent
    (root/'validation-started').touch()
    while not (root/'release-validation').exists():
        checkpoint({'phase':'render_validation','done':1,'total':3})
        time.sleep(.03)
    from book2pdf.interactive import full_conversion
    return full_conversion(source,target,recovery,expected_hash,overwrite)


def late_failure(source,target,recovery,expected_hash=None,overwrite=False):
    from book2pdf.supervised import checkpoint
    from book2pdf.models import Result
    root=Path(target).parent
    while not (root/'release-validation').exists():
        checkpoint({'phase':'render_validation','done':1,'total':3});time.sleep(.03)
    return Result(str(source),str(target),status='FAIL_VALIDATION',validation_status='FAIL',
                  error='Page 3: deliberate late validation failure',page_count=3).to_dict()


@pytest.mark.parametrize('family',['BKC','BKF'])
def test_ready_before_full_acceptance_navigation_selection_and_cancel(qtbot,tmp_path,pdf_bytes,monkeypatch,family):
    from book2pdf import interactive
    monkeypatch.setattr(interactive,'full_conversion',held_validation)
    source=tmp_path/'source.book'
    if family=='BKC':source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    else:
        from test_bkf_production import wrap as wrap_bkf, FIXTURE
        source.write_bytes(wrap_bkf([(str(i),FIXTURE.read_bytes()) for i in range(3)]))
    before=sha256(source)
    window=ViewerWindow(source,view_mode='SINGLE_PAGE');qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=10000)
        cache=window.worker.cache_path
        qtbot.waitUntil(lambda:(cache/'validation-started').exists(),timeout=10000)
        assert window.open_result['status']=='INTERACTIVE_READY'
        assert window.validation_result is None
        assert not (cache/'validated.pdf').exists()
        assert window.open_result['recovered_document']['validation']['full_document_accepted'] is False
        window.last_button.click();qtbot.waitUntil(lambda:window.last_displayed==2,timeout=5000)
        window.set_view_mode('CONTINUOUS_SCROLL');window.set_fit('width')
        qtbot.waitUntil(lambda:window.last_displayed==2,timeout=5000)
        assert set(window.continuous_area.cache)<=set(window.continuous_area.wanted_pages())
        window.export_to(tmp_path/'part.pdf',[1,3])
        qtbot.waitUntil(lambda:window.export_result is not None,timeout=10000)
        assert window.export_result['status']=='PASS_DECODED',window.export_result
        assert window.export_result['page_count']==2
        assert window.validation_result is None  # Selection does not wait for unrelated work.
        assert window.export_result['recovered_document']['validation']['status']=='PASS'
        from PySide6.QtPrintSupport import QPrinter
        printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path/'printed.pdf'))
        window.start_print(printer,[3])
        qtbot.waitUntil(lambda:window.print_worker is None,timeout=10000)
        assert window.print_result['pages']==[3],window.last_error
        assert window.validation_result is None
        pids=list(window.worker.process_ids)
        begin=time.monotonic();close_viewer(qtbot,window)
        assert time.monotonic()-begin<3
        if Path('/proc').exists():assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
        assert not cache.exists() and sha256(source)==before
    finally:
        if window.worker:close_viewer(qtbot,window)


def test_late_failure_remains_failure_and_has_durable_report(qtbot,tmp_path,pdf_bytes,monkeypatch):
    from book2pdf import interactive
    monkeypatch.setattr(interactive,'full_conversion',late_failure)
    source=tmp_path/'source.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    window=ViewerWindow(source,view_mode='SINGLE_PAGE');qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=10000)
        (window.worker.cache_path/'release-validation').touch()
        qtbot.waitUntil(lambda:window.validation_result is not None,timeout=10000)
        assert window.open_result['status']=='FAIL_VALIDATION'
        assert 'Page 3' in window.validation_label.toolTip()
        report=Path(window.validation_result['analysis_package'])/'validation-result.json'
        assert 'deliberate late validation failure' in report.read_text()
        window.go_to(1);qtbot.waitUntil(lambda:window.last_displayed==1,timeout=5000)
    finally:close_viewer(qtbot,window)
    assert report.exists()


def test_bkf_prepare_and_subset_only_native_render_selected_components(tmp_path,monkeypatch):
    from test_bkf_production import wrap as wrap_bkf,FIXTURE
    from book2pdf.bkf.native import Decoder
    source=tmp_path/'source.book';source.write_bytes(wrap_bkf([(f'page{i}',FIXTURE.read_bytes()) for i in range(25)]))
    original=Decoder.render;calls=[]
    def observed(self,path):
        calls.append(Path(path).name);return original(self,path)
    monkeypatch.setattr(Decoder,'render',observed)
    backend=Backend(source,tmp_path/'cache','safe-salvage')
    assert backend.result.status=='INTERACTIVE_READY' and backend.result.page_count==25
    assert calls==['page0']
    backend.render(24,1,1);assert calls==['page0','page24']
    result=backend.export(tmp_path/'selected.pdf',[2,24])
    assert result.status=='PASS_DECODED'
    assert calls==['page0','page24','page1','page23']
    assert result.preservation['selected_source_pages']==[2,24]


def test_source_change_rejected_before_selected_output(tmp_path,pdf_bytes):
    source=tmp_path/'source.book';source.write_bytes(wrap([(b'pdf',pdf_bytes)]))
    before=sha256(source);source.write_bytes(source.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='Source changed'):
        export_selection(source,tmp_path/'cache','safe-salvage',tmp_path/'out.pdf',[1],expected_hash=before)
    assert not (tmp_path/'out.pdf').exists()


def test_corrupt_unrequested_stream_does_not_become_full_acceptance(tmp_path,pdf_bytes):
    import pikepdf
    from book2pdf.worker import convert
    from book2pdf.models import Options
    source=tmp_path/'late-damage.pdf'
    import io
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        broken=pikepdf.Stream(pdf,b'not a compressed image')
        broken.Type=pikepdf.Name.XObject;broken.Subtype=pikepdf.Name.Image
        broken.Width=4;broken.Height=4;broken.ColorSpace=pikepdf.Name.DeviceRGB
        broken.BitsPerComponent=8;broken.Filter=pikepdf.Name.FlateDecode
        pdf.pages[2].Resources.XObject.Im=broken
        pdf.save(source,compress_streams=False,stream_decode_level=pikepdf.StreamDecodeLevel.none)
    backend=Backend(source,tmp_path/'cache','safe-salvage')
    assert backend.result.status=='INTERACTIVE_READY'
    selected=backend.export(tmp_path/'first.pdf',[1])
    assert selected.status=='PASS_EXACT' and selected.validation_status=='PASS'
    result=convert(source,tmp_path/'full.pdf',Options(validate_all_pages=True),tmp_path/'state.sqlite3')
    assert result.status not in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED')
    assert not (tmp_path/'full.pdf').exists()


def busy_loop():
    # A native call may not reach a cooperative checkpoint. Supervisor must
    # enforce close even for CPU work that never returns.
    while True:pass


def test_supervisor_cancels_cpu_bound_worker():
    from book2pdf.supervised import Session
    session=Session();pid=session.process.pid
    session.submit(busy_loop)
    time.sleep(.15)
    started=time.monotonic();session.close()
    assert time.monotonic()-started<2
    assert not session.process.is_alive()
    if Path('/proc').exists():assert not Path(f'/proc/{pid}').exists()
