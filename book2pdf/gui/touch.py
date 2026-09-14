"""Shared Qt touch recognizer. Mouse/wheel input never enters swipe recognition."""
import math
import time

from PySide6.QtCore import QObject, QEvent, QPointF, Qt, QTimer
from PySide6.QtGui import QEventPoint, QInputDevice
from PySide6.QtWidgets import QApplication


class TouchController(QObject):
    MOVE_THRESHOLD = 12.0  # Qt device-independent pixels
    DIRECTION_DOMINANCE = 1.8
    SWIPE_SECONDS = .9
    TAP_SECONDS = .3
    ZOOM_INTERVAL_MS = 50

    def __init__(self, window):
        super().__init__(window)
        self.window=window
        self.enabled=True
        self.applying=False
        self.phase=None
        self.last_tap=None
        self.double_restore=None
        self.double_target=None
        self.pending_zoom=None
        self.pending_native_start=True
        self.zoom_timer=QTimer(self)
        self.zoom_timer.setSingleShot(True);self.zoom_timer.setInterval(self.ZOOM_INTERVAL_MS)
        self.zoom_timer.timeout.connect(self.flush_zoom)
        self.surfaces=(window.area.viewport(),window.area.page_image,window.continuous_area.viewport(),
                       window.area,window.continuous_area)
        # One target for the whole viewport: otherwise Qt splits a pinch into
        # two independent sequences when one finger starts in the page margin.
        window.area.page_image.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        for surface in self.surfaces:surface.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents,True)
        QApplication.instance().installEventFilter(self)

    def cancel(self):
        self.window.continuous_area.kinetic.stop()
        self.phase=None;self.last_tap=None;self.double_restore=None;self.double_target=None
        self.pending_zoom=None;self.zoom_timer.stop()
        self.window.touch_reading_position=None

    def set_enabled(self, enabled):
        self.cancel();self.enabled=enabled

    def active_area(self):
        return self.window.continuous_area if self.window.view_mode=='CONTINUOUS_SCROLL' else self.window.area

    def available(self):
        return (self.enabled and self.window.page_count>0 and not self.window.pending_close
                and self.window.isVisible() and QApplication.activeModalWidget() is None
                and QApplication.activePopupWidget() is None)

    def local(self, global_position):
        return self.active_area().viewport().mapFromGlobal(global_position)

    def begin(self, point):
        self.phase='pending';self.origin=point;self.last=point
        self.started=time.monotonic();self.max_x=self.max_y=0.0
        self.multi=False
        self.horizontal_pan=self.active_area().horizontalScrollBar().maximum()>2
        self.origin_scroll=(self.active_area().horizontalScrollBar().value(),
                            self.active_area().verticalScrollBar().value())

    def pan(self, delta):
        area=self.active_area()
        unit=area.scroll_unit if self.window.view_mode=='CONTINUOUS_SCROLL' else 1
        area.horizontalScrollBar().setValue(round(self.origin_scroll[0]-delta.x()))
        area.verticalScrollBar().setValue(round(self.origin_scroll[1]-delta.y()/unit))
        # A late single-page raster must not undo a more recent drag.
        self.window.touch_reading_position=None

    def queue_zoom(self, zoom, anchor, point):
        if not math.isfinite(zoom) or zoom<=0:return
        self.pending_zoom=(zoom,anchor,point)
        if not self.zoom_timer.isActive():self.zoom_timer.start()

    def flush_zoom(self):
        request=self.pending_zoom;self.pending_zoom=None;self.zoom_timer.stop()
        if request and self.available():
            self.applying=True
            try:self.window.apply_touch_zoom(*request)
            finally:self.applying=False

    def double_tap(self, point):
        self.window.continuous_area.kinetic.stop()
        w=self.window;anchor=w.reading_position(point)
        state=(w.zoom,w.fit_mode)
        if self.double_restore is not None and self.double_target==state:
            target=self.double_restore;self.double_restore=self.double_target=None
        else:
            self.double_restore=state
            target=(1.0,None) if w.fit_mode=='width' else (w.zoom,'width')
        self.applying=True
        try:w.apply_touch_view(target,anchor,point)
        finally:self.applying=False
        if self.double_restore is not None:self.double_target=(w.zoom,w.fit_mode)

    def touch_event(self, event):
        kind=event.type()
        kinetic=self.window.continuous_area.kinetic
        continuous=self.window.view_mode=='CONTINUOUS_SCROLL'
        timestamp=event.timestamp()
        if kind==QEvent.Type.TouchCancel:
            self.cancel();return
        points=event.points()
        active=[p for p in points if p.state()!=QEventPoint.State.Released]
        if kind==QEvent.Type.TouchBegin:
            self.pending_zoom=None;self.zoom_timer.stop()
            if not active:return
            self.begin(self.local(active[0].globalPosition()))
            if continuous:
                self.window.touch_reading_position=None
                kinetic.press(self.origin,timestamp)
        if self.phase is None:return  # A cancelled sequence must not restart on Update.
        if len(active)>2:
            self.cancel();return
        if len(active)==2:
            a,b=(self.local(p.globalPosition()) for p in active)
            center=(a+b)/2;distance=math.hypot(a.x()-b.x(),a.y()-b.y())
            if self.phase!='pinch':
                kinetic.stop()
                self.multi=True;self.phase='pinch';self.last_tap=None
                self.double_restore=self.double_target=None
                self.pinch_span=max(self.MOVE_THRESHOLD,distance)
                self.pinch_zoom=self.window.zoom;self.pinch_anchor=self.window.reading_position(center)
            else:self.queue_zoom(self.pinch_zoom*distance/self.pinch_span,self.pinch_anchor,center)
            return
        if self.multi:
            self.flush_zoom()
            # Once a second finger participated, lifting it cannot become a
            # one-finger swipe or tap. Require a fresh sequence.
            if not active:self.phase=None
            return
        if not points:return
        point=self.local(points[0].globalPosition());delta=point-self.origin
        self.max_x=max(self.max_x,abs(delta.x()));self.max_y=max(self.max_y,abs(delta.y()))
        if self.phase=='pending' and max(self.max_x,self.max_y)>=self.MOVE_THRESHOLD:
            self.last_tap=None
            if self.window.view_mode=='CONTINUOUS_SCROLL' or self.horizontal_pan:
                self.phase='pan'
            elif abs(delta.x())>self.DIRECTION_DOMINANCE*abs(delta.y()):self.phase='swipe'
            elif abs(delta.y())>self.DIRECTION_DOMINANCE*abs(delta.x()):self.phase='pan'
        if self.phase=='pan':
            if continuous:
                self.window.touch_reading_position=None
                if kind==QEvent.Type.TouchUpdate:kinetic.move(point,timestamp)
            else:self.pan(delta)
        if kind==QEvent.Type.TouchEnd:
            if continuous:
                if self.phase=='pan':kinetic.release(point,timestamp)
                else:kinetic.stop()
            phase=self.phase;self.phase=None;elapsed=time.monotonic()-self.started
            threshold=max(60,min(140,self.active_area().viewport().width()*.12))
            if (phase=='swipe' and elapsed<=self.SWIPE_SECONDS and abs(delta.x())>=threshold
                    and abs(delta.x())>=self.DIRECTION_DOMINANCE*self.max_y):
                # RTL books advance toward the left: dragging the current scan
                # right reveals the next page. Page pixels are never transformed.
                self.window.go_to(self.window.page_index+(1 if delta.x()>0 else -1))
            elif phase=='pending' and elapsed<=self.TAP_SECONDS and max(self.max_x,self.max_y)<self.MOVE_THRESHOLD:
                now=time.monotonic();prior=self.last_tap
                interval=QApplication.styleHints().mouseDoubleClickInterval()/1000
                if prior and now-prior[0]<=interval and (point-prior[1]).manhattanLength()<=32:
                    self.last_tap=None;self.double_tap(point)
                else:self.last_tap=(now,point)
            else:self.last_tap=None

    def native_event(self, event):
        kind=event.gestureType()
        if kind==Qt.NativeGestureType.BeginNativeGesture:
            self.cancel();self.phase='native';return True
        if kind==Qt.NativeGestureType.EndNativeGesture:
            self.flush_zoom();self.phase=None;return True
        if kind!=Qt.NativeGestureType.ZoomNativeGesture or self.phase!='native':return False
        if event.fingerCount() not in (0,2):return False
        point=self.local(event.globalPosition())
        if not hasattr(self,'native_anchor') or self.pending_native_start:
            self.native_anchor=self.window.reading_position(point);self.native_zoom=self.window.zoom
            self.pending_native_start=False
        factor=1+event.value()
        if not math.isfinite(factor) or factor<=0:return True
        self.native_zoom=max(self.window.MIN_ZOOM,min(self.window.MAX_ZOOM,self.native_zoom*factor))
        self.queue_zoom(self.native_zoom,self.native_anchor,point)
        return True

    def eventFilter(self, watched, event):
        kind=event.type()
        if (watched is self.window and kind in (QEvent.Type.WindowDeactivate,QEvent.Type.Hide,QEvent.Type.Close)) or kind==QEvent.Type.ApplicationDeactivate:
            self.cancel()
        if watched in self.surfaces and kind in (QEvent.Type.Hide,QEvent.Type.FocusOut):self.cancel()
        if kind in (QEvent.Type.MouseButtonPress,QEvent.Type.KeyPress,QEvent.Type.Wheel):
            if watched is self.window or watched in self.surfaces or (hasattr(watched,'window') and watched.window() is self.window):
                self.cancel()
        if watched not in self.surfaces:return False
        if kind not in (QEvent.Type.TouchBegin,QEvent.Type.TouchUpdate,QEvent.Type.TouchEnd,QEvent.Type.TouchCancel,QEvent.Type.NativeGesture):return False
        area=self.active_area()
        if watched not in (area,area.viewport(),self.window.area.page_image if area is self.window.area else None):return False
        if not self.available():self.cancel();return False
        if kind==QEvent.Type.NativeGesture:
            if event.pointingDevice().type() not in (QInputDevice.DeviceType.TouchPad,QInputDevice.DeviceType.TouchScreen):return False
            if event.gestureType()==Qt.NativeGestureType.BeginNativeGesture:self.pending_native_start=True
            handled=self.native_event(event)
            if handled:event.accept()
            return handled
        # Qt can synthesize touch from a mouse if another component enables that
        # application flag. Device classification still prevents mouse swipes.
        if event.pointingDevice().type()!=QInputDevice.DeviceType.TouchScreen:return False
        self.touch_event(event);event.accept();return True
