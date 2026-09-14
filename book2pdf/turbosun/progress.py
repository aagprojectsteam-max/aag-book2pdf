"""Keep cancellation responsive without flooding the GUI's progress pipe."""
import time
from ..supervised import checkpoint


def throttled(callback):
    last=0.0;phase=None
    def report(event):
        nonlocal last,phase
        checkpoint()  # Cancellation/visible-page priority on every safe unit.
        now=time.monotonic()
        if event['phase']!=phase or now-last>=.1 or event.get('done')==event.get('total'):
            callback(event);last=now;phase=event['phase']
    return report
