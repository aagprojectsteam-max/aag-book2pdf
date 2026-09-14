"""Owned, cancellable process sessions; no native work runs on Qt's thread."""
import multiprocessing
import os
import signal
import time

_connection = _cancel = _busy = None
_background = False


def checkpoint(event=None):
    if _cancel is not None:
        while True:
            if _cancel.is_set():raise InterruptedError('Document work cancelled')
            if not _background or not _busy.is_set():break
            time.sleep(.02)
    if event is not None and _connection is not None:
        _connection.send(('progress', event))


def _serve(connection, cancel, busy, background):
    global _connection, _cancel, _busy, _background
    _connection, _cancel, _busy, _background = connection, cancel, busy, background
    from .worker import initialize_process
    initialize_process()
    if hasattr(os, 'setsid'):os.setsid()
    if background and hasattr(os, 'nice'):os.nice(10)
    try:
        from .research_limits import constrain
        # Reuse the existing cross-platform memory/job boundary. A viewer may
        # remain open all day; each individual request also has a wall deadline.
        constrain(3*1024*1024*1024,86400)
        while not cancel.is_set():
            if not connection.poll(.05):continue
            task=connection.recv()
            if task is None:break
            function,args,kwargs=task
            try:
                checkpoint()
                value=function(*args, **kwargs)
                connection.send(('result',value))
            except Exception as exc:
                code=getattr(exc,'code','CANCELLED' if isinstance(exc,InterruptedError) else 'DEPENDENCY_MISSING' if isinstance(exc,ModuleNotFoundError)
                             else 'RUNTIME_LOAD_FAILED' if isinstance(exc,ImportError) else 'DOCUMENT_INVALID')
                connection.send(('error',{'code':code,'error':f'{type(exc).__name__}: {exc}'}))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        for child in multiprocessing.active_children():
            child.terminate();child.join(1)
            if child.is_alive():child.kill();child.join()
        connection.close()


class Session:
    def __init__(self, *, busy=None, background=False):
        context=multiprocessing.get_context('spawn')
        self.cancel=context.Event()
        self.busy=busy if busy is not None else context.Event()
        self.connection, child=context.Pipe()
        self.process=context.Process(target=_serve,args=(child,self.cancel,self.busy,background),
                                     name='Book2PDF-validation' if background else 'Book2PDF-interactive')
        self.process.start();child.close()
        self.pending=False
        self.started=0

    def submit(self,function,*args,**kwargs):
        if self.pending:raise RuntimeError('Session already has an active task')
        self.pending=True;self.started=time.monotonic()
        self.connection.send((function,args,kwargs))

    def poll(self):
        if not self.pending:return None
        if self.connection.poll():
            try:kind,value=self.connection.recv()
            except EOFError:kind,value='error',{'code':'RUNTIME_LOAD_FAILED','error':'Document worker exited unexpectedly'}
            if kind!='progress':self.pending=False
            return kind,value
        if not self.process.is_alive():
            self.pending=False
            return 'error',{'code':'RUNTIME_LOAD_FAILED','error':'Document worker exited unexpectedly'}
        if time.monotonic()-self.started>1800:
            self.pending=False
            return 'error',{'code':'LIMIT_EXCEEDED','error':'Document worker exceeded wall-time budget'}
        return None

    def call(self,function,*args,cancelled=None,**kwargs):
        self.submit(function,*args,**kwargs)
        while True:
            if cancelled is not None and cancelled():raise InterruptedError('Document work cancelled')
            event=self.poll()
            if event:
                kind,value=event
                if kind=='result':return value
                if kind=='error':raise ValueError(value['code']+': '+value['error'])
            time.sleep(.01)

    def close(self):
        self.cancel.set()
        # Drain results while the process exits (a large raster may be in flight).
        deadline=time.monotonic()+.6
        while self.process.is_alive() and time.monotonic()<deadline:
            if self.connection.poll():
                try:self.connection.recv()
                except (EOFError,OSError):break
            self.process.join(.02)
        if self.process.is_alive():
            # On POSIX include native helper descendants. Known interactive BKF
            # tasks decode in this process, so there is no nested Python decoder.
            if hasattr(os,'killpg'):
                try:
                    if os.getpgid(self.process.pid)==self.process.pid:os.killpg(self.process.pid,signal.SIGKILL)
                    else:self.process.kill()
                except ProcessLookupError:pass
            else:self.process.kill()
            self.process.join(2)
        self.connection.close()

    def __enter__(self):return self
    def __exit__(self,*args):self.close()
