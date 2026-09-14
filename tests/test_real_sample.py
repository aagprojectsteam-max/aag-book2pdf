"""Optional integration: never substitute the reference for the original source."""
import os
from pathlib import Path
import pytest

from book2pdf.models import Options
from book2pdf.worker import convert
from book2pdf.state import sha256


def test_real_original_sample(tmp_path):
    source = Path(os.environ.get('BOOK2PDF_SAMPLE', 'private-samples/not-provided.book'))
    if not source.is_file():
        pytest.skip('Original sample is unavailable; set BOOK2PDF_SAMPLE')
    before = sha256(source)
    exact = convert(source, tmp_path / 'exact.pdf', Options(recovery='exact'), tmp_path / 'state.sqlite3')
    assert exact.status == 'PASS_DECODED', exact.error
    assert exact.page_count == 187 and not exact.missing_data_synthesized
    assert exact.preservation['round_trip']
    import pikepdf
    with pikepdf.open(tmp_path/'exact.pdf',attempt_recovery=False) as pdf:
        state4=pdf.get_object((4,0));state5=pdf.get_object((5,0))
        assert state4.get('/SMask')==pikepdf.Name('/None')
        assert not state4.get('/OP') and state5.get('/OP')
    result = convert(source, tmp_path / 'salvage.pdf', Options(recovery='safe-salvage'), tmp_path / 'state.sqlite3')
    assert result.status == 'PASS_DECODED', result.error
    assert result.page_count == 187 and result.detected_wrapper_offset == 2452
    assert result.validation_mode == 'all_pages'
    assert result.preservation['full_component_bytes_preserved']
    assert not result.reconstructed_objects and not result.assumptions
    assert (tmp_path/'salvage.pdf').read_bytes()==(tmp_path/'exact.pdf').read_bytes()
    assert sha256(source) == before
    strict_resume = convert(source, tmp_path / 'salvage.pdf', Options(recovery='exact'), tmp_path / 'state.sqlite3')
    assert strict_resume.status == 'SKIPPED_EXISTING_VALID'
    import shutil
    shutil.copyfile(tmp_path/'state.sqlite3',tmp_path/'.book2pdf-state.sqlite3')
    reopened=convert(tmp_path/'salvage.pdf',tmp_path/'copy.pdf',Options(),tmp_path/'copy-state.sqlite3')
    assert reopened.status=='PASS_DECODED',reopened.error
    assert (tmp_path/'copy.pdf').read_bytes()==(tmp_path/'exact.pdf').read_bytes()


def test_real_bkf_decodes_and_preserves(tmp_path):
    from book2pdf.bkf.native import library_path
    source = Path(os.environ.get('BOOK2PDF_SAMPLE_58', 'private-samples/not-provided.book'))
    if not source.is_file() or not library_path():
        pytest.skip('Real sample or optional DjVuLibre runtime unavailable')
    before = sha256(source)
    result = convert(source,tmp_path/'book.pdf',Options(),tmp_path/'state.sqlite3')
    assert result.status == 'PASS_DECODED',result.error
    assert result.page_count == 87 and result.validation_mode == 'all_pages'
    assert result.preservation['native_pixels_preserved']
    assert result.preservation['original_djvu_bytes_preserved']
    assert len(result.preservation['pages'])==87
    assert sha256(source)==before


def test_real_bkf_viewer(qtbot):
    from book2pdf.bkf.native import library_path
    from book2pdf.gui.viewer_window import ViewerWindow
    from test_viewer import close_viewer
    source = Path(os.environ.get('BOOK2PDF_SAMPLE_58', 'private-samples/not-provided.book'))
    if not source.is_file() or not library_path():
        pytest.skip('Real sample or optional DjVuLibre runtime unavailable')
    before=sha256(source)
    window=ViewerWindow(source,view_mode='SINGLE_PAGE');qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.last_displayed==0 or bool(window.last_error),timeout=60000)
    assert not window.last_error
    assert window.page_count==87 and window.open_result['status']=='INTERACTIVE_READY'
    assert not window.area.page_image.pixmap().isNull()
    window.last_button.click()
    qtbot.waitUntil(lambda:window.last_displayed==86,timeout=30000)
    qtbot.waitUntil(lambda:window.validation_result is not None,timeout=60000)
    assert window.validation_result['status']=='PASS_DECODED'
    close_viewer(qtbot,window)
    assert sha256(source)==before
