"""Real Qt timing, early interaction/export/printing, full acceptance and cancellation.

Run from /tmp with the installed interpreter to prove the installed package.
"""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    parser.add_argument('--memory',action='store_true')
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    from PySide6.QtCore import Qt,QSettings,QTimer
    from PySide6.QtWidgets import QApplication
    from PySide6.QtPrintSupport import QPrinter
    from book2pdf.gui.viewer_window import ViewerWindow
    from book2pdf.state import sha256
    import book2pdf
    app=QApplication(['AAG Book2PDF interactive acceptance']);app.setQuitOnLastWindowClosed(False)
    app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    evidence={'module':str(Path(book2pdf.__file__).resolve()),'version':book2pdf.__version__,
              'platform':app.platformName(),'books':[]}
    def persist():
        (args.evidence/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    def wait(predicate,seconds=300):
        deadline=time.monotonic()+seconds
        while True:
            app.processEvents()
            if predicate():return
            if time.monotonic()>deadline:raise TimeoutError('Acceptance deadline')
            time.sleep(.005)
    for source in args.sources:
        row={'source':str(source),'sha256_before':sha256(source)}
        base=args.evidence/source.stem;base.mkdir()
        started=time.monotonic()
        window=ViewerWindow(source,view_mode='SINGLE_PAGE',settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat))
        window.show();app.processEvents()
        row['time_to_window']=time.monotonic()-started
        progress=[];completed=[]
        window.worker.validation_updated.connect(lambda e:progress.append((time.monotonic()-started,e)) if e['kind']=='progress' else completed.append(time.monotonic()-started))
        ticks=[];timer=QTimer();timer.setInterval(25);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
        try:
            wait(lambda:window.last_displayed==0 or window.last_error,60)
            assert not window.last_error,window.last_error
            row['time_to_first_page_visible']=time.monotonic()-started
            row['status_at_first_page']=window.open_result['status']
            assert window.open_result['status']=='INTERACTIVE_READY'
            assert window.validation_result is None
            row['page_count']=window.page_count
            window.grab().save(str(base/'first-before-validation.png'))
            window.go_to(window.page_count//2)
            wait(lambda:window.last_displayed==window.page_count//2 or window.last_error,30)
            assert not window.last_error,window.last_error
            row['time_to_first_interaction']=time.monotonic()-started
            row['interaction_before_full_acceptance']=window.validation_result is None
            assert row['interaction_before_full_acceptance']
            window.grab().save(str(base/'middle-before-validation.png'))
            window.set_view_mode('CONTINUOUS_SCROLL');window.set_fit('width')
            window.go_to(window.page_count-1)
            wait(lambda:window.last_displayed==window.page_count-1 or window.last_error,30)
            assert not window.last_error,window.last_error
            area=window.continuous_area
            assert set(area.cache)<=set(area.wanted_pages())
            anchor=area.anchor()
            window.set_zoom(window.zoom*1.15)
            wait(lambda:window.last_displayed==window.page_index,30)
            assert area.anchor()[0]==anchor[0]
            window.set_fit('page');wait(lambda:window.last_displayed==window.page_index)
            window.set_fit('width');wait(lambda:window.last_displayed==window.page_index)
            window.grab().save(str(base/'last-continuous.png'))
            row['interactive_navigation_zoom_fits']='PASS'
            # Selection starts while full validation is still outstanding.
            row['selection_started_before_full_acceptance']=window.validation_result is None
            window.export_to(base/'selected.pdf',[1,window.page_count])
            wait(lambda:window.export_result is not None or window.last_error,60)
            assert not window.last_error,window.last_error
            assert window.export_result['status']=='PASS_DECODED',window.export_result
            assert window.export_result['page_count']==2
            row['selection_completed_at']=time.monotonic()-started
            row['selection_completed_before_full_acceptance']=window.validation_result is None
            printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(base/'printed.pdf'));window.start_print(printer,[window.page_count])
            wait(lambda:window.print_worker is None,60)
            assert window.print_result,window.last_error
            row['print_to_pdf']='PASS'
            wait(lambda:window.validation_result is not None,600)
            assert window.validation_result['status']=='PASS_DECODED',window.validation_result
            assert window.validation_result['validation_mode']=='all_pages'
            row['full_validation_time']=completed[0]
            assert row['time_to_first_page_visible'] < row['full_validation_time']/2
            row['final_status']=window.validation_result['status']
            (base/'full-validation.json').write_text(json.dumps(window.validation_result,ensure_ascii=False,indent=2),encoding='utf-8')
            (base/'progress.json').write_text(json.dumps(progress,indent=2))
            window.grab().save(str(base/'validation-completed.png'))
            row['max_gui_heartbeat_gap']=max((b-a for a,b in zip(ticks,ticks[1:])),default=0)
            timer.stop()
            if args.memory and window.page_count==615:
                from continuous_acceptance import rss_tree
                import statistics
                samples=[]
                for index in range(window.page_count):
                    area.restore_anchor((index,.5))
                    wait(lambda:window.page_index==index and set(area.wanted_pages())<=set(area.cache),30)
                    stats=area.stats()
                    assert stats['raster_bytes']<=area.MAX_RASTER_BYTES
                    assert set(stats['rendered_pages'])<=set(stats['allowed_pages'])
                    samples.append({'page':index+1,'stats':stats,'rss':rss_tree()})
                    if (index+1)%50==0:print(f'MEMORY {index+1}/615',flush=True)
                warm=statistics.median(s['rss']['total_bytes'] for s in samples[10:30])
                end=statistics.median(s['rss']['total_bytes'] for s in samples[-20:])
                assert end-warm<max(96*1024**2,warm*.4),(warm,end)
                assert len({s['stats']['scrollbar_maximum'] for s in samples})==1
                row['memory']={'pages':615,'warm_rss':warm,'end_rss':end,
                    'max_raster_bytes':max(s['stats']['raster_bytes'] for s in samples),
                    'max_cached_pages':max(len(s['stats']['rendered_pages']) for s in samples),'bounded':'PASS'}
                (base/'memory.json').write_text(json.dumps(samples,indent=2))
            window.set_view_mode('SINGLE_PAGE');wait(lambda:window.last_displayed==window.page_index)
            assert not area.cache
            row['single_page_regression']='PASS'
            window.save_to(base/'full.pdf');wait(lambda:window.save_result is not None,600)
            assert window.save_result['status']=='PASS_DECODED',window.save_result
            (base/'full-save.json').write_text(json.dumps(window.save_result,ensure_ascii=False,indent=2),encoding='utf-8')
            row['full_export']='PASS'
        finally:
            pids=list(window.worker.process_ids) if window.worker else []
            cache=window.worker.cache_path if window.worker else None
            begin=time.monotonic();window.close();wait(lambda:window.worker is None and window.print_worker is None,5)
            row['close_seconds']=time.monotonic()-begin
            assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
            assert not cache or not cache.exists()
        row['sha256_after']=sha256(source);assert row['sha256_after']==row['sha256_before']
        # Close a second window while its full validation is actually running.
        second=ViewerWindow(source,view_mode='SINGLE_PAGE');second.show()
        wait(lambda:second.last_displayed==0,60)
        wait(lambda:len(second.worker.process_ids)>=2,30)
        assert second.validation_result is None
        pids=list(second.worker.process_ids);cache=second.worker.cache_path
        begin=time.monotonic();second.close();wait(lambda:second.worker is None,5)
        row['cancel_during_validation_seconds']=time.monotonic()-begin
        assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
        assert not cache.exists()
        row['stale_workers_cancelled']='PASS'
        evidence['books'].append(row);persist();print(json.dumps(row,ensure_ascii=False),flush=True)
    evidence['status']='PASS';persist()


if __name__=='__main__':
    multiprocessing.freeze_support();main()
