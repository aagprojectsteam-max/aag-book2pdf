"""Kinetic scrolling uses timed Qt touchscreen input and native QScroller physics."""
import pytest
from PySide6.QtCore import QPoint,QPointF,Qt,QEvent
from PySide6.QtGui import QInputDevice,QTouchEvent,QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScroller,QApplication

from scripts.touch_input import Fingers
from test_touch import touch_window


def continuous(qtbot,window):
    window.set_view_mode('CONTINUOUS_SCROLL');window.set_fit('width')
    qtbot.waitUntil(lambda:window.last_displayed==window.page_index,timeout=10000)
    qtbot.wait(100)
    return window.continuous_area


@pytest.mark.parametrize('distance',[300,550])
def test_native_fling_multi_page_deceleration_tracking_and_lazy_layout(qtbot,touch_window,distance):
    w=touch_window;area=continuous(qtbot,w);area.go_to(1);qtbot.wait(100)
    finger=Fingers(area.viewport());scroller=area.kinetic.scroller
    begin=area.scroll_top();finger.flick((400,650),(420,650-distance))
    assert scroller.state()==QScroller.State.Scrolling
    release=area.scroll_top();maximum=area.verticalScrollBar().maximum();generation=area.generation
    samples=[]
    for i in range(500):
        qtbot.wait(20);samples.append((area.scroll_top(),scroller.velocity().y(),w.page_index))
        assert area.verticalScrollBar().maximum()==maximum and area.generation==generation
        assert set(area.cache)<=set(area.wanted_pages()) and area.raster_bytes()<=area.MAX_RASTER_BYTES
        assert w.page_index==area.anchor()[0] or abs(w.page_index-area.anchor()[0])==1
        assert w.worker.scheduling_stats()['queued']<=4
        if scroller.state()==QScroller.State.Inactive:break
    assert scroller.state()==QScroller.State.Inactive
    assert samples[-1][0]-release>area.rects[1][1]*2
    assert samples[-1][0]-begin>area.viewport().height()*3
    assert all(a[0]<=b[0] for a,b in zip(samples,samples[1:])), [(a,b) for a,b in zip(samples,samples[1:]) if a[0]>b[0]]
    # Allow small native velocity-query jitter observed near document bounds,
    # as in the real-book acceptance. Position must remain monotonic and
    # overall speed must fall to zero.
    assert all(b[1]<=a[1]+.01 for a,b in zip(samples,samples[1:])), [(a,b) for a,b in zip(samples,samples[1:]) if b[1]>a[1]+.01]
    assert samples[0][1]>samples[len(samples)//2][1]>samples[-1][1]==0
    assert len({s[2] for s in samples})>=3 and w.page_index==area.anchor()[0]
    assert area.wanted_pages()!=area.visible_pages()  # Bounded prefetch restored on stop.


def test_slow_drag_is_direct_then_stops_without_inertia(qtbot,touch_window):
    w=touch_window;area=continuous(qtbot,w);area.go_to(2);qtbot.wait(100)
    f=Fingers(area.viewport());s=area.kinetic.scroller
    f.press(400,650);qtbot.wait(60);f.move(400,620);qtbot.wait(100)
    assert s.state()==QScroller.State.Dragging
    before=area.scroll_top();f.move(400,570);qtbot.wait(300)
    assert abs(area.scroll_top()-before-50)<=1
    end=area.scroll_top();f.release(400,570);qtbot.wait(150)
    assert s.state()==QScroller.State.Inactive and area.scroll_top()==end


def test_touch_catches_fling_then_opposite_motion_reverses(qtbot,touch_window):
    w=touch_window;area=continuous(qtbot,w);area.go_to(3);qtbot.wait(100)
    f=Fingers(area.viewport());s=area.kinetic.scroller
    f.flick((400,650),(420,100));qtbot.wait(70)
    assert s.state()==QScroller.State.Scrolling
    f.press(400,200);position=area.scroll_top();qtbot.wait(150)
    assert area.scroll_top()==position and s.state()==QScroller.State.Pressed
    for step in range(1,9):
        qtbot.wait(15);f.move(400,200+50*step)
    f.release(400,600);assert s.state()==QScroller.State.Scrolling
    release=area.scroll_top();qtbot.wait(120)
    assert area.scroll_top()<release<position and s.velocity().y()<0


@pytest.mark.parametrize('reason',['pinch','double_tap','mode','disable','wheel','scrollbar','key','cancel','deactivate','resize'])
def test_other_interaction_cancels_fling(qtbot,touch_window,reason):
    w=touch_window;area=continuous(qtbot,w);area.go_to(2);qtbot.wait(100)
    f=Fingers(area.viewport());s=area.kinetic.scroller
    f.flick((400,650),(420,100));qtbot.wait(60)
    assert s.state()==QScroller.State.Scrolling
    zoom=w.zoom
    if reason=='pinch':f.pinch((420,350),80,110)
    elif reason=='double_tap':f.double_tap((420,350))
    elif reason=='mode':w.set_view_mode('SINGLE_PAGE')
    elif reason=='disable':w.touch_action.setChecked(False)
    elif reason=='wheel':
        QApplication.sendEvent(area.viewport(),QWheelEvent(QPointF(400,300),QPointF(400,300),QPoint(0,-40),QPoint(),
            Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.ScrollUpdate,False))
    elif reason=='scrollbar':area.verticalScrollBar().setSliderDown(True);area.verticalScrollBar().setSliderDown(False)
    elif reason=='key':QTest.keyClick(area.viewport(),Qt.Key.Key_Control)
    elif reason=='cancel':QApplication.sendEvent(area.viewport(),QTouchEvent(QEvent.Type.TouchCancel,f.device))
    elif reason=='deactivate':QApplication.sendEvent(w,QEvent(QEvent.Type.WindowDeactivate))
    else:w.resize(1100,900)
    assert s.state()==QScroller.State.Inactive
    qtbot.wait(300);position=area.scroll_top();qtbot.wait(100)
    assert area.scroll_top()==position
    if reason=='pinch':assert w.zoom>zoom and w.fit_mode is None
    if reason=='double_tap':assert w.zoom==1 and w.fit_mode is None


def test_mouse_drag_and_raw_touchpad_do_not_activate_fling(qtbot,touch_window):
    w=touch_window;area=continuous(qtbot,w);viewport=area.viewport()
    position=area.scroll_top()
    QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=QPoint(400,650))
    QTest.mouseMove(viewport,QPoint(400,100));QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=QPoint(400,100))
    f=Fingers(viewport,QTest.createTouchDevice(QInputDevice.DeviceType.TouchPad))
    f.flick((400,650),(420,100));qtbot.wait(100)
    assert area.scroll_top()==position and area.kinetic.scroller.state()==QScroller.State.Inactive


def test_obsolete_queue_cancellation_is_counted_and_bounded(tmp_path):
    from book2pdf.gui.viewer_worker import ViewerThread
    worker=ViewerThread(tmp_path/'unused.book')
    worker.request_visible([(0,1,1),(1,1,1),(2,1,1)],1)
    worker.request_visible([(600,1,1),(601,1,1)],1)
    assert worker.scheduling_stats()['queued_cancelled']==3
    worker.request_visible([(600,1,1),(601,1,1)],1)
    assert worker.scheduling_stats()['queued_cancelled']==3
    worker.request_render(5,1,1)
    assert worker.scheduling_stats()=={'queued':0,'max_queued':3,'inflight':None,
                                      'queued_cancelled':5,'inflight_results_discarded':0}
