"""Qt's native kinetic physics, driven only by the shared touchscreen recognizer."""
from PySide6.QtCore import QEasingCurve
from PySide6.QtWidgets import QScroller, QScrollerProperties


class KineticScroll:
    def __init__(self, area):
        self.area=area
        self.scroller=QScroller.scroller(area.viewport())
        properties=self.scroller.scrollerProperties()
        metric=QScrollerProperties.ScrollMetric
        for key,value in (
            (metric.DragStartDistance,0.0),  # TouchController owns the jitter threshold.
            (metric.MinimumVelocity,.12),
            # Physical touchscreen trials release around .4-.6 m/s. The
            # lower friction lets those flicks cross several scanned pages;
            # cap very fast input so it cannot coast indefinitely.
            (metric.MaximumVelocity,.85),
            (metric.DecelerationFactor,.10),
            (metric.DragVelocitySmoothingFactor,.65),
            (metric.AxisLockThreshold,.25),
            (metric.ScrollingCurve,QEasingCurve(QEasingCurve.Type.OutQuad)),
            (metric.AcceleratingFlickMaximumTime,0.0),
            (metric.HorizontalOvershootPolicy,QScrollerProperties.OvershootPolicy.OvershootAlwaysOff),
            (metric.VerticalOvershootPolicy,QScrollerProperties.OvershootPolicy.OvershootAlwaysOff),
            (metric.FrameRate,QScrollerProperties.FrameRates.Fps60),
        ):
            properties.setScrollMetric(key,value)
        self.scroller.setScrollerProperties(properties)
        self.scroller.stateChanged.connect(self.state_changed)

    def state_changed(self, state):
        # During inertia, spend rendering time on visible pages. Restore the
        # user's bounded prefetch window once motion ends.
        self.area.kinetic_scrolling=state==QScroller.State.Scrolling
        self.area.refresh_visible()

    def stop(self):
        self.scroller.stop()

    def press(self, point, timestamp):
        self.stop()  # A fresh touch catches motion without accelerated flicks.
        self.scroller.handleInput(QScroller.Input.InputPress,point,timestamp)

    def move(self, point, timestamp):
        self.scroller.handleInput(QScroller.Input.InputMove,point,timestamp)

    def release(self, point, timestamp):
        self.scroller.handleInput(QScroller.Input.InputRelease,point,timestamp)
