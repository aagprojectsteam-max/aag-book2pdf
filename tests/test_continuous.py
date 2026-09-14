"""Behavioral checks of virtual layout, cancellation, and the common viewer path."""
import pytest
from PySide6.QtCore import Qt, QSettings, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from book2pdf.gui.continuous import ContinuousArea
from book2pdf.gui.viewer_window import ViewerWindow
from book2pdf.gui.viewer_worker import ViewerThread
from test_viewer import close_viewer


def settings_at(tmp_path):
    return QSettings(str(tmp_path/'viewer.ini'), QSettings.Format.IniFormat)


def model(count=615):
    return {'pages': [{'width': 600 + (i % 3)*30, 'height': 840} for i in range(count)]}


def frame(area, page, generation=None):
    return {'page': page, 'generation': area.generation if generation is None else generation,
            'width': 40, 'height': 60, 'stride': 120, 'pixels': b'\xff\0\0'*2400, 'ratio': 1}


def test_virtual_layout_release_stable_geometry_and_stale_results(qtbot):
    area=ContinuousArea(prefetch=1);qtbot.addWidget(area);area.resize(850,600);area.show()
    requests=[];area.render_requested.connect(lambda pages,generation: requests.append((pages,generation)))
    area.set_document(model());area.activate(1,None,0)
    qtbot.wait(25)
    assert len(area.rects)==615 and not area.cache
    maximum=area.verticalScrollBar().maximum()
    for index in range(615):
        area.go_to(index)
        for page in area.wanted_pages(): assert area.accept_render(frame(area,page))
        assert set(area.cache)<=set(area.wanted_pages())
        assert len(area.cache)<=len(area.visible_pages())+2
        assert area.verticalScrollBar().maximum()==maximum
        assert area.anchor()[0]==index
    assert len(requests[0][0])<6  # Opening does not queue the whole document.
    old_generation=area.generation
    area.configure(1.3,None)
    assert not area.accept_render(frame(area,614,old_generation))
    area.deactivate()
    assert not area.cache and requests[-1][0]==[]


def test_zoom_anchor_and_heterogeneous_fit_width(qtbot):
    area=ContinuousArea();qtbot.addWidget(area);area.resize(850,600);area.show()
    area.set_document(model());area.activate(1,None,308);qtbot.wait(25)
    area.restore_anchor((308,.73));before=area.anchor()
    area.configure(1.8,None)
    qtbot.wait(30)  # Includes the horizontal scrollbar's asynchronous resize.
    assert area.anchor()[0]==before[0]
    assert abs(area.anchor()[1]-before[1])<.002
    area.configure(.7,None);area.go_to(401);qtbot.wait(30)
    assert area.anchor()[0]==401  # A pending resize must not undo a newer jump.
    area.go_to(308)
    area.configure(1,'width')
    assert len({w for w,h in area.rects})==1
    assert area.rects[0][0]==area.viewport().width()-24
    assert area.anchor()[0]==308
    area.resize(1000,650);qtbot.wait(30)
    assert area.anchor()[0]==308
    assert area.rects[0][0]==area.viewport().width()-24
    assert area.layoutDirection()==Qt.LayoutDirection.LeftToRight


def test_render_queue_replaces_obsolete_requests(tmp_path):
    thread=ViewerThread(tmp_path/'unused.book')
    thread.request_visible([(0,1,1),(1,1,1)],10)
    thread.render_inflight=(0,1,1,10)
    thread.request_visible([(0,1,1),(1,1,1)],10)
    assert thread.render_queue==[(1,1,1,10)]
    thread.request_visible([(599,2,1),(600,2,1)],11)
    assert thread.render_queue==[(599,2,1,11),(600,2,1,11)]
    assert thread.render_desired=={599,600}
    thread.request_render(5,1,1)
    assert thread.render_queue==[] and thread.render_generation is None


def test_continuous_viewer_navigation_zoom_mode_persistence(qtbot,book,tmp_path):
    settings=settings_at(tmp_path)
    window=ViewerWindow(book,settings=settings,view_mode='CONTINUOUS_SCROLL')
    qtbot.addWidget(window);window.show();window.activateWindow()
    qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
    try:
        area=window.continuous_area
        assert window.page_count==3
        assert window.layoutDirection()==Qt.LayoutDirection.RightToLeft
        assert area.active and area.cache
        window.page_spin.setValue(3)
        qtbot.waitUntil(lambda:window.last_displayed==2,timeout=30000)
        assert window.page_label.text()=='עמוד 3 מתוך 3'
        window.previous_button.click()
        qtbot.waitUntil(lambda:window.last_displayed==1,timeout=30000)
        window.set_zoom(2)
        area.restore_anchor((1,.6));area.refresh_visible();anchor=area.anchor()
        window.change_zoom(1)
        assert area.anchor()[0]==anchor[0]
        assert abs(area.anchor()[1]-anchor[1])<.004
        window.fit_width_button.click()
        assert area.fit_mode=='width'
        assert all(w==area.viewport().width()-24 for w,h in area.rects)
        window.fit_page_button.click()
        assert area.rects[1][1]<=area.viewport().height()
        area.setFocus()
        QTest.keyClick(area,Qt.Key.Key_End)
        qtbot.waitUntil(lambda:window.last_displayed==2,timeout=30000)
        QTest.keyClick(area,Qt.Key.Key_Home)
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=30000)
        # Touchpad scrolling must update the toolbar from the viewport itself.
        delta=round(area.tops[1]-area.scroll_top())
        event=QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(0,-delta),QPoint(),
                         Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False)
        QApplication.sendEvent(area.viewport(),event)
        qtbot.waitUntil(lambda:window.page_index==1,timeout=30000)
        previous_zoom=window.zoom
        wheel=QWheelEvent(QPointF(20,20),QPointF(20,20),QPoint(),QPoint(0,-120),
                         Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
        QApplication.sendEvent(area.viewport(),wheel)
        assert window.zoom<previous_zoom
        QTest.keyClick(area,Qt.Key.Key_PageDown)
        qtbot.waitUntil(lambda:window.last_displayed==2,timeout=30000)
        QTest.keyClick(area,Qt.Key.Key_PageUp)
        qtbot.waitUntil(lambda:window.last_displayed==1,timeout=30000)
        window.set_view_mode('SINGLE_PAGE')
        qtbot.waitUntil(lambda:window.last_displayed==1,timeout=30000)
        assert not area.cache and not area.active
        assert not window.area.page_image.pixmap().isNull()
        window.set_view_mode('CONTINUOUS_SCROLL')
        qtbot.waitUntil(lambda:window.last_displayed==1,timeout=30000)
        assert window.area.page_image.pixmap().isNull()
        assert settings.value('viewer/view_mode')=='CONTINUOUS_SCROLL'
    finally:
        close_viewer(qtbot,window)
    reopened=ViewerWindow(book,settings=settings_at(tmp_path))
    qtbot.addWidget(reopened);reopened.show()
    qtbot.waitUntil(lambda:reopened.last_displayed==0,timeout=30000)
    assert reopened.view_mode=='CONTINUOUS_SCROLL'
    close_viewer(qtbot,reopened)


@pytest.mark.parametrize('level', ['STRUCTURAL_REPAIR','RECONSTRUCTED_PREVIEW','PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY','DECODED'])
def test_common_model_is_family_independent(qtbot,level):
    area=ContinuousArea();qtbot.addWidget(area);area.show()
    recovered=model(7);recovered['recovery_level']=level
    area.set_document(recovered);area.activate(1,'width',3)
    assert area.model['recovery_level']==level and area.anchor()[0]==3
    assert area.accept_render(frame(area,3))
    assert len(area.pages)==7


def test_layout_reads_rotated_crop_geometry_without_rendering(tmp_path,monkeypatch):
    import pikepdf
    import pymupdf
    from book2pdf.viewer import document_layout
    source=tmp_path/'rotated.pdf'
    with pikepdf.Pdf.new() as pdf:
        page=pdf.add_blank_page(page_size=(400,800))
        page.CropBox=[10,20,310,720];page.Rotate=90
        pdf.save(source)
    def forbidden(*args,**kwargs):raise AssertionError('Layout must not rasterize')
    monkeypatch.setattr(pymupdf.Page,'get_pixmap',forbidden)
    result=document_layout(source,{'pages':[{'source_page':1}]})
    assert result['pages'][0]=={'source_page':1,'width':700.0,'height':300.0}


@pytest.mark.parametrize('level', ['PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY','STRUCTURAL_REPAIR'])
def test_recovered_content_opens_through_common_continuous_viewer(qtbot,tmp_path,pdf_bytes,level):
    import io,zipfile
    from test_generic_recovery import without_xref,jpeg
    from book2pdf.recovery.evidence import collect
    from book2pdf.state import sha256
    if level=='IMAGE_LEVEL_RECOVERY':
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as archive:
            archive.writestr('2.jpg',jpeg(90));archive.writestr('1.jpg',jpeg(30))
        raw=buffer.getvalue();count=2
    else:
        raw=without_xref(pdf_bytes);count=3
        if level=='PAGE_LEVEL_RECOVERY':
            records,_,_,_=collect(raw)
            catalog=next(r for r in records if isinstance(r.value,dict) and r.value.get('/Type')=='/Catalog')
            raw=raw[:catalog.start]+raw[catalog.end:]
    source=tmp_path/'recovered.book';source.write_bytes(raw);before=sha256(source)
    window=ViewerWindow(source,settings=settings_at(tmp_path),view_mode='CONTINUOUS_SCROLL')
    qtbot.addWidget(window);window.show()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0 or window.worker is None,timeout=30000)
        assert window.open_result, window.last_error
        assert window.document_model['recovery_level']==level
        assert window.page_count==count
        window.go_to(count-1)
        qtbot.waitUntil(lambda:window.last_displayed==count-1,timeout=30000)
        assert count-1 in window.continuous_area.cache
        window.export_to(tmp_path/'last.pdf',[count])
        qtbot.waitUntil(lambda:window.export_result is not None or bool(window.last_error),timeout=30000)
        assert window.export_result, window.last_error
        assert window.export_result['page_count']==1
        assert window.export_result['preservation']['page_streams_byte_identical']
        assert sha256(source)==before
    finally:
        close_viewer(qtbot,window)
