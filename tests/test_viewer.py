from pathlib import Path
import pytest
from PySide6.QtCore import Qt
from PySide6.QtPrintSupport import QPrinter,QPrintDialog

from book2pdf.gui.viewer_window import ViewerWindow
from book2pdf.viewer import ViewingCache
from book2pdf.state import sha256


def close_viewer(qtbot,window):
    cache=window.worker.cache_path if window.worker else None
    window.close()
    qtbot.waitUntil(lambda:window.worker is None,timeout=30000)
    assert not window.isVisible()
    if cache:
        assert not cache.exists()


@pytest.mark.parametrize("view_mode", ["SINGLE_PAGE", "CONTINUOUS_SCROLL"])
def test_viewer_navigation_zoom_save_export_print(qtbot,book,tmp_path,view_mode):
    before=sha256(book)
    window=ViewerWindow(book,view_mode=view_mode);qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.last_displayed == 0,timeout=30000)
    assert window.page_count == 3
    assert (not window.area.page_image.pixmap().isNull() if view_mode=="SINGLE_PAGE"
            else 0 in window.continuous_area.cache)
    window.last_button.click()
    qtbot.waitUntil(lambda:window.last_displayed == 2,timeout=30000)
    window.page_spin.setValue(2)
    qtbot.waitUntil(lambda:window.last_displayed == 1,timeout=30000)
    window.actual_button.click()
    assert window.zoom == 1.0
    window.zoom_in_button.click(); assert window.zoom > 1.0
    window.zoom_out_button.click(); assert abs(window.zoom-1)<.001
    window.fit_page_button.click(); assert window.fit_mode=='page'
    qtbot.waitUntil(lambda: (window.area.page_image.height() <= window.area.viewport().height() if view_mode=="SINGLE_PAGE"
                             else window.continuous_area.rects[window.page_index][1] <= window.continuous_area.viewport().height()),timeout=30000)
    window.fit_width_button.click(); assert window.fit_mode=='width'
    target=tmp_path/'saved.pdf';window.save_to(target)
    qtbot.waitUntil(lambda:window.save_result is not None,timeout=30000)
    assert window.save_result['status']=='PASS_EXACT' and target.exists()
    exported=tmp_path/'selected.pdf';window.export_to(exported,'1,3')
    qtbot.waitUntil(lambda:window.export_result is not None,timeout=30000)
    assert window.export_result['page_count']==2
    printer=QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printed=tmp_path/'printed.pdf';printer.setOutputFileName(str(printed))
    dialog=QPrintDialog(printer,window);dialog.show()
    qtbot.waitUntil(dialog.isVisible)
    dialog.reject()
    window.start_print(printer,[2])
    qtbot.waitUntil(lambda:window.print_worker is None,timeout=30000)
    assert window.print_result and window.print_result['pages']==[2],window.last_error
    assert printed.exists()
    assert sha256(book)==before
    close_viewer(qtbot,window)


def test_viewer_bad_book_friendly_error(qtbot,tmp_path):
    source=tmp_path/'bad.book';source.write_bytes(b'invalid')
    window=ViewerWindow(source,view_mode="SINGLE_PAGE");qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.worker is None,timeout=30000)
    assert window.last_error
    assert 'לא ניתן' in window.banner.text()
    assert not window.save_button.isEnabled()


def test_private_cache_cleanup():
    cache=ViewingCache()
    ViewingCache.cleanup_orphans()
    assert cache.path.exists()
    cache.lock.close()  # Simulate process death releasing its advisory lock.
    ViewingCache.cleanup_orphans()
    assert not cache.path.exists()


def test_reconstructed_pdf_open(qtbot,tmp_path,pdf_bytes):
    source=tmp_path/'reconstructed.pdf';source.write_bytes(pdf_bytes)
    window=ViewerWindow(source,view_mode="SINGLE_PAGE");qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    assert window.page_count==3
    close_viewer(qtbot,window)


def test_viewer_keyboard_wheel_and_fullscreen(qtbot,book):
    from PySide6.QtCore import QPoint,QPointF
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QApplication
    from PySide6.QtTest import QTest
    window=ViewerWindow(book,view_mode="SINGLE_PAGE");qtbot.addWidget(window);window.show();window.activateWindow()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    window.area.setFocus()
    QTest.keyClick(window.area,Qt.Key.Key_End)
    qtbot.waitUntil(lambda:window.last_displayed==2,timeout=30000)
    QTest.keyClick(window.area,Qt.Key.Key_Home)
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    window.set_zoom(1)
    wheel=QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(0,0),QPoint(0,120),
                     Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
    QApplication.sendEvent(window.area.viewport(),wheel)
    assert window.zoom>1
    window.toggle_fullscreen();assert window.isFullScreen()
    window.exit_fullscreen();assert not window.isFullScreen()
    close_viewer(qtbot,window)


def test_very_large_page_really_fits(qtbot,tmp_path):
    import pikepdf
    source=tmp_path/'large.pdf'
    with pikepdf.Pdf.new() as pdf:
        page=pdf.add_blank_page(page_size=(10000,14000))
        page.Contents=pikepdf.Stream(pdf,b'q Q\n')
        pdf.save(source)
    window=ViewerWindow(source,view_mode="SINGLE_PAGE");qtbot.addWidget(window);window.show()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    window.fit_page_button.click()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    assert window.zoom<.1
    assert window.area.page_image.height()<=window.area.viewport().height()
    assert window.area.page_image.width()<=window.area.viewport().width()
    window.fit_width_button.click()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    assert window.area.page_image.width()<=window.area.viewport().width()
    close_viewer(qtbot,window)


def test_installed_and_frozen_gui_acceptance_checks_both_modes(qtbot,book,tmp_path):
    from book2pdf.selftest import check_gui
    evidence={}
    check_gui(book,tmp_path,evidence)
    assert evidence['gui']=='PASS' and evidence['continuous_scroll']=='PASS'
    assert evidence['viewer_partial_export']=='PASS' and evidence['qt_pdf_printing']=='PASS'


def test_python_module_gui_acceptance_entrypoint(tmp_path):
    import json,subprocess,sys
    output=tmp_path/'python-gui'
    subprocess.run([sys.executable,'-m','book2pdf.selftest',str(output),'--gui'],check=True,
                   capture_output=True,text=True,timeout=60)
    evidence=json.loads((output/'evidence.json').read_text())
    assert evidence['status']=='PASS' and not evidence['frozen']
    assert evidence['gui']=='PASS' and evidence['continuous_scroll']=='PASS'
    assert evidence['python']=='.'.join(map(str,sys.version_info[:3]))
