"""Real QTouchEvent delivery via Qt's touchscreen test device, never mouse emulation."""
import io
import pytest
from PySide6.QtCore import Qt,QPoint,QPointF,QSettings,QEvent
from PySide6.QtGui import QInputDevice,QTouchEvent,QNativeGestureEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from book2pdf.gui.viewer_window import ViewerWindow
from test_envelope import wrap
from test_viewer import close_viewer
from scripts.touch_input import Fingers


@pytest.fixture
def touch_window(qtbot,tmp_path):
    import pikepdf
    with pikepdf.Pdf.new() as pdf:
        for i in range(9):
            page=pdf.add_blank_page(page_size=(600+(i%2)*30,840))
            page.Contents=pikepdf.Stream(pdf,b'1 0 0 rg 30 30 80 750 re f 0 0 1 rg 420 50 140 400 re f')
        stream=io.BytesIO();pdf.save(stream)
    source=tmp_path/'touch.book';source.write_bytes(wrap([(b'pdf',stream.getvalue())]))
    settings=QSettings(str(tmp_path/'settings.ini'),QSettings.Format.IniFormat)
    window=ViewerWindow(source,settings=settings,view_mode='SINGLE_PAGE');window.show();window.activateWindow()
    try:
        qtbot.waitUntil(lambda:window.last_displayed==0,timeout=15000)
        yield window
    finally:close_viewer(qtbot,window)



def test_qt_touch_swipe_rtl_thresholds_and_mouse_protection(qtbot,touch_window):
    w=touch_window;viewport=w.area.viewport();finger=Fingers(viewport)
    original=w.area.page_image.pixmap().toImage()
    finger.press(300,200);assert w.touch.phase=='pending'
    finger.move(530,205);finger.release(530,205)
    qtbot.waitUntil(lambda:w.last_displayed==1,timeout=10000)
    finger.swipe((540,205),(290,200));qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    assert w.area.page_image.pixmap().toImage()==original
    assert viewport.layoutDirection()==Qt.LayoutDirection.LeftToRight
    for start,end in [((300,200),(310,202)),((300,200),(500,420))]:
        finger.swipe(start,end);assert w.page_index==0
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=QPoint(300,200))
    QTest.mouseMove(viewport,QPoint(550,200))
    QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=QPoint(550,200))
    assert w.page_index==0
    QTest.mouseDClick(viewport,Qt.MouseButton.LeftButton,pos=QPoint(300,200))
    assert w.fit_mode=='page'


def test_single_pan_beats_swipe_even_at_horizontal_boundary(qtbot,touch_window):
    w=touch_window;w.set_zoom(2)
    qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    horizontal=w.area.horizontalScrollBar();vertical=w.area.verticalScrollBar()
    horizontal.setValue(160);vertical.setValue(250)
    finger=Fingers(w.area.viewport());finger.swipe((500,400),(400,280))
    assert horizontal.value()==min(260,horizontal.maximum()) and vertical.value()==370 and w.page_index==0
    horizontal.setValue(0)
    finger.swipe((300,200),(570,205));assert w.page_index==0 and horizontal.value()==0


@pytest.mark.parametrize('mode',['SINGLE_PAGE','CONTINUOUS_SCROLL'])
def test_pinch_reading_point_and_coalesced_visible_rendering(qtbot,touch_window,mode):
    w=touch_window;w.set_view_mode(mode);w.set_zoom(1.8)
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000)
    if mode=='SINGLE_PAGE':
        area=w.area;area.horizontalScrollBar().setValue(80);area.verticalScrollBar().setValue(300)
    else:
        area=w.continuous_area;area.restore_anchor((4,.65));area.refresh_visible()
    point=QPointF(400,350);anchor=w.reading_position(point);old_zoom=w.zoom
    requests=[]
    if mode=='CONTINUOUS_SCROLL':area.render_requested.connect(lambda values,generation:requests.append(values))
    finger=Fingers(area.viewport());finger.pinch((400,350),80,120,updates=20)
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000);qtbot.wait(250)
    assert w.zoom==pytest.approx(old_zoom*1.5,abs=.002) and w.fit_mode is None
    after=w.reading_position(point)
    assert after[0]==anchor[0] and after[1:]==pytest.approx(anchor[1:],abs=.004)
    if mode=='CONTINUOUS_SCROLL':
        assert all(len(r)<=len(area.visible_pages())+2 for r in requests)
        assert len(requests)<10  # 20 raw updates coalesce, rather than 20 native renders.
        assert set(area.cache)<=set(area.wanted_pages())
        assert area.raster_bytes()<=area.MAX_RASTER_BYTES


def test_continuous_vertical_diagonal_and_horizontal_never_flip(qtbot,touch_window,monkeypatch):
    w=touch_window;w.set_view_mode('CONTINUOUS_SCROLL');w.set_fit('width')
    qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    area=w.continuous_area;start=area.scroll_top();jumps=[]
    monkeypatch.setattr(w,'go_to',lambda p:jumps.append(p))
    finger=Fingers(area.viewport())
    finger.drag((400,600),(470,100));qtbot.wait(30)
    # Qt's initial drag slop is not document motion; subsequent drag is direct.
    assert start+450<=area.scroll_top()<=start+502
    assert w.page_index==area.anchor()[0]
    finger.drag((300,200),(570,205));assert not jumps
    assert w.fit_mode=='width'


@pytest.mark.parametrize('mode',['SINGLE_PAGE','CONTINUOUS_SCROLL'])
def test_double_tap_restores_manual_and_fit_width_state(qtbot,touch_window,mode):
    w=touch_window;w.set_view_mode(mode);w.set_zoom(1.8)
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000)
    area=w.touch.active_area();f=Fingers(area.viewport())
    f.double_tap((420,320));assert w.fit_mode=='width'
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000);qtbot.wait(150)
    f.double_tap((420,320));assert w.zoom==pytest.approx(1.8) and w.fit_mode is None
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000)
    w.set_fit('width');qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000)
    f.double_tap((420,320));assert w.zoom==1 and w.fit_mode is None
    f.double_tap((420,320));assert w.fit_mode=='width'


@pytest.mark.parametrize('reason',['cancel','mode','deactivate','page','disable'])
def test_interrupted_sequence_does_not_resume(qtbot,touch_window,reason):
    w=touch_window;viewport=w.area.viewport();f=Fingers(viewport)
    f.press(300,200);f.move(430,200)
    if reason=='cancel':QApplication.sendEvent(viewport,QTouchEvent(QEvent.Type.TouchCancel,f.device))
    elif reason=='mode':w.set_view_mode('CONTINUOUS_SCROLL')
    elif reason=='deactivate':QApplication.sendEvent(w,QEvent(QEvent.Type.WindowDeactivate))
    elif reason=='page':w.go_to(3)
    else:w.touch_action.setChecked(False)
    target=w.page_index
    f.move(600,200);f.release(600,200)
    assert w.page_index==target
    assert w.touch.phase is None


def test_native_touchpad_zoom_and_touch_setting_persistence(qtbot,touch_window):
    w=touch_window;viewport=w.area.viewport();device=QTest.createTouchDevice(QInputDevice.DeviceType.TouchPad)
    point=QPointF(400,300);global_point=viewport.mapToGlobal(point)
    def send(kind,value=0):
        QApplication.sendEvent(viewport,QNativeGestureEvent(kind,device,2,point,point,global_point,value,QPointF()))
    old=w.zoom
    send(Qt.NativeGestureType.BeginNativeGesture);send(Qt.NativeGestureType.ZoomNativeGesture,.2)
    send(Qt.NativeGestureType.ZoomNativeGesture,.1);send(Qt.NativeGestureType.EndNativeGesture)
    assert w.zoom==pytest.approx(old*1.2*1.1)
    w.touch_action.setChecked(False);old=w.zoom
    send(Qt.NativeGestureType.BeginNativeGesture);send(Qt.NativeGestureType.ZoomNativeGesture,.5);send(Qt.NativeGestureType.EndNativeGesture)
    assert w.zoom==old
    settings=QSettings(w.settings.fileName(),QSettings.Format.IniFormat)
    assert settings.value('viewer/touch_enabled',True,type=bool) is False
    f=Fingers(viewport);f.swipe((300,200),(600,200));assert w.page_index==0
    second=ViewerWindow(w.source,settings=settings,view_mode='SINGLE_PAGE');second.show()
    try:
        assert not second.touch.enabled and not second.touch_action.isChecked()
    finally:close_viewer(qtbot,second)


def test_pinch_limits_and_cancelled_pending_zoom(qtbot,touch_window):
    w=touch_window;viewport=w.area.viewport();f=Fingers(viewport)
    w.set_zoom(3);qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    f.pinch((450,350),30,100);assert w.zoom==w.MAX_ZOOM
    qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    w.set_zoom(.01);qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    # One finger begins on the tiny page and one in the margin: Qt must group them.
    f.pinch((450,350),100,1);assert w.zoom==w.MIN_ZOOM
    qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    w.set_fit('page');qtbot.waitUntil(lambda:w.last_displayed==0,timeout=10000)
    old=w.zoom
    f.sequence.press(0,QPoint(350,300),viewport).press(1,QPoint(450,300),viewport).commit()
    f.sequence.move(0,QPoint(320,300),viewport).move(1,QPoint(480,300),viewport).commit()
    QApplication.sendEvent(viewport,QTouchEvent(QEvent.Type.TouchCancel,f.device))
    qtbot.wait(100);assert w.zoom==old and w.touch.pending_zoom is None
    f.sequence.release(0,QPoint(320,300),viewport).release(1,QPoint(480,300),viewport).commit()


def test_two_fingers_never_degrade_to_swipe_and_raw_touchpad_is_not_touchscreen(qtbot,touch_window):
    w=touch_window;viewport=w.area.viewport();f=Fingers(viewport)
    f.sequence.press(0,QPoint(300,300),viewport).press(1,QPoint(400,300),viewport).commit()
    f.sequence.stationary(0).release(1,QPoint(400,300),viewport).commit()
    f.move(650,300);f.release(650,300);assert w.page_index==0
    pad=Fingers(viewport,QTest.createTouchDevice(QInputDevice.DeviceType.TouchPad))
    pad.swipe((300,300),(650,300));assert w.page_index==0


def test_continuous_pinch_preserves_point_when_horizontal_bar_appears(qtbot,touch_window):
    w=touch_window;w.set_view_mode('CONTINUOUS_SCROLL');w.set_zoom(1.4)
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000);qtbot.wait(100)
    area=w.continuous_area;area.restore_anchor((4,.6));area.refresh_visible()
    assert area.horizontalScrollBar().maximum()==0
    point=QPointF(area.viewport().width()/2,area.viewport().height()/2)
    before=w.reading_position(point)
    Fingers(area.viewport()).pinch((round(point.x()),round(point.y())),80,120)
    qtbot.waitUntil(lambda:w.last_displayed==w.page_index,timeout=10000);qtbot.wait(250)
    assert area.horizontalScrollBar().maximum()>0
    after=w.reading_position(point)
    assert before[0]==after[0] and after[1:]==pytest.approx(before[1:],abs=.001)
