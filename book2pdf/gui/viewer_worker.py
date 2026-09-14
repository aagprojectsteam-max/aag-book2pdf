import multiprocessing
from pathlib import Path
import threading
import uuid

from PySide6.QtCore import QThread, Signal

from ..models import Options
from ..report import Report
from ..viewer import ViewingCache, render_page, document_layout
from ..supervised import Session
from .. import interactive
import time


class ViewerThread(QThread):
    loaded = Signal(dict)
    rendered = Signal(dict)
    saved = Signal(dict)
    exported = Signal(dict)
    failed = Signal(str)
    analysis_ready = Signal(dict)
    validation_updated = Signal(dict)

    def __init__(self, source, recovery='safe-salvage', parent=None):
        super().__init__(parent)
        self.source = Path(source).resolve()
        self.recovery = recovery
        self.condition = threading.Condition()
        self.stopping = False
        self.render_request = None
        self.render_queue = []
        self.render_generation = None
        self.render_desired = set()
        self.render_inflight = None
        self.render_requests_cancelled = 0
        self.render_results_discarded = 0
        self.max_render_queue = 0
        self.save_request = None
        self.export_request = None
        self.output_cancel_requested = False
        self.cache_path = None
        self.process_ids = []

    def request_render(self, page, zoom, ratio):
        with self.condition:
            self.render_requests_cancelled+=len(self.render_queue)
            self.render_queue=[]
            self.render_generation=None
            self.render_request = (page, zoom, ratio)
            self.condition.notify()

    def request_visible(self, requests, generation):
        with self.condition:
            self.render_request=None
            self.render_generation=generation
            self.render_desired={r[0] for r in requests}
            replacement=[(*r,generation) for r in requests if (*r,generation)!=self.render_inflight]
            self.render_requests_cancelled+=len(set(self.render_queue)-set(replacement))
            self.render_queue=replacement
            self.max_render_queue=max(self.max_render_queue,len(replacement))
            self.condition.notify()

    def scheduling_stats(self):
        with self.condition:
            return {'queued':len(self.render_queue),'max_queued':self.max_render_queue,
                    'inflight':self.render_inflight,'queued_cancelled':self.render_requests_cancelled,
                    'inflight_results_discarded':self.render_results_discarded}

    def request_save(self, path, overwrite):
        with self.condition:
            self.save_request = (Path(path), overwrite)
            self.condition.notify()

    def cancel_output(self):
        with self.condition:
            self.output_cancel_requested=True
            self.condition.notify()

    def stop(self):
        with self.condition:
            self.stopping = True
            self.condition.notify()

    def request_export(self, path, pages, overwrite=False):
        with self.condition:
            self.export_request = (Path(path), pages, overwrite)
            self.condition.notify()

    def run(self):
        cache = None
        sessions = []
        foreground = background = output = None
        try:
            ViewingCache.cleanup_orphans()
            cache = ViewingCache()
            self.cache_path = cache.path
            busy = multiprocessing.get_context('spawn').Event()
            foreground = Session(busy=busy)
            sessions.append(foreground)
            self.process_ids.append(foreground.process.pid)
            result = foreground.call(interactive.prepare, self.source, cache.path, self.recovery,
                                     cancelled=lambda: self.stopping)
            if result['status'] not in (*interactive.ACCEPTED, 'INTERACTIVE_READY'):
                prefix=result['status']+': ' if result['status'] in ('DEPENDENCY_MISSING','RUNTIME_LOAD_FAILED','LIMIT_EXCEEDED') else ''
                self.failed.emit(prefix + result['error'])
                self.analysis_ready.emit(result)
                return
            self.loaded.emit(result)
            full_result = result if result['status'] in interactive.ACCEPTED else None
            if full_result:self.validation_updated.emit({'kind':'result','result':full_result})
            render_active = None
            operation = None
            while not self.stopping:
                # Interactive tasks have their own process and also pause the
                # background decoder at its next page checkpoint.
                with self.condition:
                    if not foreground.pending:
                        request = self.render_request
                        self.render_request = None
                        continuous = None
                        if request is None and self.render_queue:
                            continuous = self.render_queue.pop(0)
                            request = continuous[:3]
                            self.render_inflight = continuous
                        if request:
                            busy.set()
                            render_active = continuous
                            foreground.submit(interactive.render, *request, 2048 if continuous else 4096)
                    if not foreground.pending and not self.render_request and not self.render_queue:
                        busy.clear()
                event = foreground.poll()
                if event:
                    kind, value = event
                    if kind == 'result':
                        continuous = render_active
                        with self.condition:
                            wanted = not continuous or (continuous[3] == self.render_generation and continuous[0] in self.render_desired)
                            self.render_inflight = None
                            if not wanted:self.render_results_discarded+=1
                        if continuous:value['generation'] = continuous[3]
                        if wanted and not self.stopping:self.rendered.emit(value)
                        # Start expensive full conversion only after the first
                        # requested raster has been delivered to the GUI.
                        if background is None and full_result is None:
                            background = Session(busy=busy, background=True)
                            sessions.append(background);self.process_ids.append(background.process.pid)
                            background.submit(interactive.background_validation if interactive.is_dataset_source(self.source) else interactive.full_conversion, self.source, cache.path/'validated.pdf',
                                              self.recovery, result['source_hash'])
                    elif kind == 'error':self.failed.emit(value['code'] + ': ' + value['error'])
                if background:
                    event = background.poll()
                    if event:
                        kind,value=event
                        if kind=='progress':self.validation_updated.emit({'kind':'progress', **value})
                        else:
                            full_result = {**result, **value, 'recovered_document': value.get('recovered_document') or result['recovered_document']} if kind=='result' else {**result,'status':value['code'],
                                'validation_status':'FAIL','error':value['error']}
                            if full_result['status'] in interactive.ACCEPTED and full_result['page_count'] != result['page_count']:
                                full_result.update(status='FAIL_VALIDATION',validation_status='FAIL',error='Interactive and validated page counts differ')
                            if full_result['status'] not in interactive.ACCEPTED:
                                import json
                                from ..platforms import cache_root
                                import tempfile
                                report_dir=Path(tempfile.mkdtemp(prefix='aag-book2pdf-validation-',dir=cache_root()))
                                full_result['analysis_package']=str(report_dir)
                                (report_dir/'validation-result.json').write_text(json.dumps(full_result,ensure_ascii=False,indent=2),encoding='utf-8')
                            self.validation_updated.emit({'kind':'result','result':full_result})
                            background.close();sessions.remove(background);background=None
                # Export/printing never occupies the interactive render process.
                if self.output_cancel_requested:
                    with self.condition:
                        self.output_cancel_requested=False
                        queued_save=self.save_request;queued_export=self.export_request
                        self.save_request=None;self.export_request=None
                    if operation:output.cancel.set()
                    # The child reports PASS if publication already completed;
                    # never falsely report cancellation after an atomic commit.
                    for name,queued in (('save',queued_save),('export',queued_export)):
                        if queued:
                            from ..models import Result
                            cancelled=Result(str(self.source),str(queued[0]),status='CANCELLED',error='השמירה בוטלה.').to_dict()
                            (self.saved if name=='save' else self.exported).emit(cancelled)
                if operation is None:
                    with self.condition:
                        export = self.export_request;self.export_request = None
                        save = self.save_request;self.save_request = None
                    if export or save:
                        output = Session(busy=busy, background=True)
                        sessions.append(output);self.process_ids.append(output.process.pid)
                        if export:
                            path,pages,overwrite=export
                            output.submit(interactive.export_selection,self.source,cache.path/('export-'+uuid.uuid4().hex),
                                          self.recovery,path,pages,overwrite,result['source_hash'])
                            operation=('export',path)
                            if save:
                                with self.condition:self.save_request=save
                        else:
                            path,overwrite=save
                            output.submit(interactive.full_conversion,self.source,path,self.recovery,result['source_hash'],overwrite)
                            operation=('save',path)
                if operation:
                    event=output.poll()
                    if event and event[0]=='progress':
                        self.validation_updated.emit({'kind':'progress',**event[1]})
                    if event and event[0]!='progress':
                        kind,value=event
                        name,path=operation
                        if kind=='error':
                            from ..models import Result
                            value=Result(str(self.source),str(path),status=value['code'],error=value['error']).to_dict()
                        if value['status'] in (*interactive.ACCEPTED,'SKIPPED_EXISTING_VALID'):
                            from ..models import Result
                            report=Report(path.parent/f'book2pdf-{name}-{uuid.uuid4().hex[:12]}.jsonl')
                            try:report.write(Result(**value))
                            finally:report.close()
                        (self.saved if name=='save' else self.exported).emit(value)
                        output.close();sessions.remove(output);output=None;operation=None
                with self.condition:
                    if not self.stopping:self.condition.wait(.01)
        except InterruptedError:
            pass
        except Exception as exc:
            if not self.stopping:self.failed.emit(str(exc))
        finally:
            # Cancellation signals all owned processes before waiting for any.
            for session in sessions:session.cancel.set()
            for session in sessions:session.close()
            if cache:cache.close()
