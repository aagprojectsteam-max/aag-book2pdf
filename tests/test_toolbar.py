from PySide6.QtCore import Qt,QSettings
from PySide6.QtWidgets import QToolBar, QToolButton
from book2pdf.gui.viewer_window import ViewerWindow
from test_viewer import close_viewer
from test_envelope import wrap


def test_shared_book_one_toolbar_overflow_actions(qtbot,tmp_path,pdf_bytes):
    source=tmp_path/'doc.book';source.write_bytes(wrap([(b'arbitrary',pdf_bytes)]))
    window=ViewerWindow(source,view_mode='CONTINUOUS_SCROLL',settings=QSettings(str(tmp_path/'viewer.ini'),QSettings.Format.IniFormat));qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
        assert window.open_result['status']=='INTERACTIVE_READY'
        assert window.open_result['validation_status']=='PENDING_FULL_VALIDATION'
        qtbot.waitUntil(lambda:window.validation_result is not None,timeout=30000)
        assert window.open_result['status']=='PASS_DECODED'
        assert len(window.findChildren(QToolBar))==1
        assert sum(a.isSeparator() for a in window.toolbar.actions())==4
        heights=[]
        for width in (800,1040,1440):
            window.resize(width,900);qtbot.wait(200)
            assert window.width()==width
            heights.append(window.toolbar.height())
            assert window.layoutDirection()==Qt.LayoutDirection.RightToLeft
            buttons=[w for w in window.toolbar.findChildren(QToolButton) if w.isVisible() and w.objectName()!='qt_toolbar_ext_button']
            assert max(w.geometry().center().y() for w in buttons)-min(w.geometry().center().y() for w in buttons)<=1
        assert len(set(heights))==1
        window.resize(800,900);qtbot.wait(200)
        extension=window.toolbar.findChild(QToolButton,'qt_toolbar_ext_button')
        assert extension.isVisible()
        # Overflow uses the same QAction as the permanent toolbar control.
        window.actual_button.defaultAction().trigger();assert window.zoom==1
        window.zoom_in_button.defaultAction().trigger();assert window.zoom>1
        window.last_button.defaultAction().trigger()
        qtbot.waitUntil(lambda:window.last_displayed==2,timeout=30000)
        window.set_view_mode('SINGLE_PAGE');qtbot.waitUntil(lambda:window.last_displayed==2)
    finally:
        close_viewer(qtbot,window)


import pytest
@pytest.mark.parametrize('selected',[[2],[1,2,3],[1,3]])
def test_decoded_container_print_lineage(qtbot,tmp_path,pdf_bytes,selected):
    from PySide6.QtPrintSupport import QPrinter
    from book2pdf.provenance import embedded_provenance
    source=tmp_path/'doc.book';source.write_bytes(wrap([(b'arbitrary',pdf_bytes)]))
    window=ViewerWindow(source,view_mode='SINGLE_PAGE');qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
        output=tmp_path/'printed.pdf'
        printer=QPrinter(QPrinter.PrinterMode.HighResolution)
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat);printer.setOutputFileName(str(output))
        window.start_print(printer,selected)
        qtbot.waitUntil(lambda:window.print_worker is None,timeout=30000)
        assert window.print_result,window.last_error
        record=embedded_provenance(output)
        assert record['fidelity']=='PRINT_RENDERING'
        model=record['recovered_document']
        assert model['provenance']['document_relation']=='PRINT_RASTERIZED'
        assert [p['source_page'] for p in model['pages']]==selected
        assert model['resources']['original_streams_preserved'] is False
    finally:
        close_viewer(qtbot,window)
