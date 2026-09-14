"""Qt touchscreen event sequences for tests and real-document acceptance."""
from PySide6.QtCore import QPoint
from PySide6.QtGui import QInputDevice
from PySide6.QtTest import QTest


class Fingers:
    def __init__(self,widget,device=None):
        self.widget=widget
        self.device=device or QTest.createTouchDevice(QInputDevice.DeviceType.TouchScreen)
        self.sequence=QTest.touchEvent(widget,self.device,False)

    def press(self,x,y,identifier=0):
        self.sequence.press(identifier,QPoint(x,y),self.widget).commit()

    def move(self,x,y,identifier=0):
        self.sequence.move(identifier,QPoint(x,y),self.widget).commit()

    def release(self,x,y,identifier=0):
        self.sequence.release(identifier,QPoint(x,y),self.widget).commit()

    def swipe(self,start,end):
        self.press(*start);self.move(*end);self.release(*end)

    def drag(self,start,end,*,duration_ms=600,steps=20,hold_ms=200):
        """Timed touchscreen motion. Hold before lifting for a deliberate stop."""
        self.press(*start)
        for step in range(1,steps+1):
            QTest.qWait(max(1,round(duration_ms/steps)))
            self.move(*(round(a+(b-a)*step/steps) for a,b in zip(start,end)))
        if hold_ms:QTest.qWait(hold_ms)
        self.release(*end)

    def flick(self,start,end,*,duration_ms=100,steps=8):
        self.drag(start,end,duration_ms=duration_ms,steps=steps,hold_ms=0)

    def pinch(self,center,first,last,updates=1):
        x,y=center
        self.sequence.press(0,QPoint(x-first,y),self.widget).press(1,QPoint(x+first,y),self.widget).commit()
        for step in range(1,updates+1):
            radius=round(first+(last-first)*step/updates)
            self.sequence.move(0,QPoint(x-radius,y),self.widget).move(1,QPoint(x+radius,y),self.widget).commit()
        self.sequence.release(0,QPoint(x-last,y),self.widget).release(1,QPoint(x+last,y),self.widget).commit()

    def double_tap(self,point):
        self.press(*point);self.release(*point)
        self.press(*point);self.release(*point)
