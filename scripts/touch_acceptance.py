"""Qt touchscreen events on real books; never claims physical-finger acceptance.

Run with PYTHONPATH=. against source, or from /tmp with PYTHONPATH empty and
the installed interpreter. --memory traverses every page of the 615-page book.
"""
import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path
import statistics
import time


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--memory',action='store_true')
    parser.add_argument('--kinetic',action='store_true')
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    from PySide6.QtCore import Qt,QSettings,QPoint,QPointF,QTimer
    from PySide6.QtGui import QInputDevice
    from PySide6.QtWidgets import QApplication
    from PySide6.QtPrintSupport import QPrinter,QPrintDialog
    from PySide6.QtTest import QTest
    from book2pdf.gui.viewer_window import ViewerWindow
    from book2pdf.state import sha256
    from touch_input import Fingers
    from continuous_acceptance import rss_tree
    import book2pdf
    app=QApplication(['AAG Book2PDF touch acceptance'])
    app.setQuitOnLastWindowClosed(False);app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.processEvents()
    evidence={'module':str(Path(book2pdf.__file__).resolve()),'version':book2pdf.__version__,
              'platform':app.platformName(),'input':'Qt QTest touchscreen QTouchEvents',
              'physical_finger_gestures':'NOT_PROVEN','native_windows':'NOT_PROVEN',
              'native_devices_before_test_device':[{'name':d.name(),'type':d.type().name,
                    'capabilities':str(d.capabilities())} for d in QInputDevice.devices()], 'books':[]}
    def persist():
        (args.evidence/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    def wait(predicate,seconds=60):
        deadline=time.monotonic()+seconds
        while True:
            app.processEvents()
            if predicate():return
            if time.monotonic()>deadline:raise TimeoutError('Touch acceptance deadline')
            time.sleep(.005)
    def pause(seconds=.25):
        until=time.monotonic()+seconds;wait(lambda:time.monotonic()>=until)
    def frame_hash(window):
        image=window.area.page_image.pixmap().toImage()
        return [image.width(),image.height(),hashlib.sha256(bytes(image.constBits())).hexdigest()]
    persist()
    for source in args.sources:
        base=args.evidence/source.stem;base.mkdir()
        row={'source':str(source),'sha256_before':sha256(source),'checks':{}}
        evidence['books'].append(row);persist();checks=row['checks']
        settings=QSettings(str(base/'settings.ini'),QSettings.Format.IniFormat)
        started=time.monotonic();window=ViewerWindow(source,view_mode='SINGLE_PAGE',settings=settings)
        window.setWindowTitle(f'בדיקה אוטומטית — נא לא לגעת — {source.name}')
        window.show();window.activateWindow();area=window.continuous_area
        ticks=[];timer=QTimer();timer.setInterval(25);timer.timeout.connect(lambda:ticks.append(time.monotonic()));timer.start()
        def settled():
            assert not window.last_error,window.last_error
            return (window.last_displayed==window.page_index and
                    (window.view_mode=='SINGLE_PAGE' or set(area.wanted_pages())<=set(area.cache)))
        def anchored_zoom(mode):
            window.set_view_mode(mode);window.set_zoom(2)
            wait(settled);pause()
            viewport=window.touch.active_area().viewport()
            if mode=='SINGLE_PAGE':
                window.area.horizontalScrollBar().setValue(100)
                window.area.verticalScrollBar().setValue(200)
            else:
                area.restore_anchor((window.page_count//2,.6));wait(settled)
            point=QPointF(viewport.width()/2,viewport.height()/2)
            anchor=window.reading_position(point);zoom=window.zoom
            requests=[]
            def record(values,generation):requests.append(values)
            area.render_requested.connect(record)
            f=Fingers(viewport);center=(round(point.x()),round(point.y()))
            try:f.pinch(center,80,110,updates=24)
            finally:area.render_requested.disconnect(record)
            wait(settled);pause()
            after=window.reading_position(point)
            assert abs(window.zoom-zoom*110/80)<.002 and window.fit_mode is None
            assert after[0]==anchor[0] and max(abs(x-y) for x,y in zip(after[1:],anchor[1:]))<.0015,(mode,anchor,after)
            row.setdefault('pinch',{})[mode]={'before':anchor,'after':after,'raw_updates':24,
                                           'visible_requests':len(requests),'zoom':window.zoom}
            if mode=='CONTINUOUS_SCROLL':
                assert len(requests)<24
                assert all(len(values)<10 for values in requests)
            manual=window.zoom
            f.double_tap(center);wait(settled);pause();assert window.fit_mode=='width'
            f.double_tap(center);wait(settled);pause()
            assert abs(window.zoom-manual)<.002 and window.fit_mode is None
            checks['TOUCH_DOUBLE_TAP_'+mode]='PASS'
        try:
            wait(lambda:window.last_displayed==0 or window.last_error)
            assert not window.last_error,window.last_error
            row.update(first_page_seconds=time.monotonic()-started,page_count=window.page_count,
                       initial_status=window.open_result['status'],validation_pending_at_first_page=window.validation_result is None)
            persist()
            assert row['initial_status']=='INTERACTIVE_READY' and row['validation_pending_at_first_page']
            assert window.touch.enabled
            pause();before=frame_hash(window);viewport=window.area.viewport();f=Fingers(viewport)
            f.press(300,220)
            input_state={'press_phase':window.touch.phase,'horizontal_maximum':window.area.horizontalScrollBar().maximum(),
                         'active_window':app.activeWindow() is window,'viewport_visible':viewport.isVisible()}
            f.move(580,225);input_state['move_phase']=window.touch.phase
            f.release(580,225);input_state['page_after_release']=window.page_index
            row['first_touch_delivery']=input_state
            assert window.page_index==1,input_state
            wait(lambda:window.last_displayed==1);checks['TOUCH_SINGLE_PAGE_NEXT']='PASS'
            f.swipe((580,220),(300,225));wait(lambda:window.last_displayed==0);pause()
            assert frame_hash(window)==before,(before,frame_hash(window))
            assert window.layoutDirection()==Qt.LayoutDirection.RightToLeft and viewport.layoutDirection()==Qt.LayoutDirection.LeftToRight
            checks['TOUCH_SINGLE_PAGE_PREVIOUS']=checks['RTL_PAGE_IMAGE_NOT_MIRRORED']='PASS'
            row['first_page_render_hash']=before
            QTest.mousePress(viewport,Qt.MouseButton.LeftButton,pos=QPoint(300,220))
            QTest.mouseMove(viewport,QPoint(580,225));QTest.mouseRelease(viewport,Qt.MouseButton.LeftButton,pos=QPoint(580,225))
            assert window.page_index==0;checks['MOUSE_DRAG_DOES_NOT_NAVIGATE']='PASS'
            window.grab().save(str(base/'single-touch.png'))
            for mode in ('SINGLE_PAGE','CONTINUOUS_SCROLL'):anchored_zoom(mode)
            checks['TOUCH_PINCH_ZOOM']=checks['READING_POSITION_PRESERVED']='PASS'
            checks['TOUCH_DOUBLE_TAP']='PASS'
            window.set_view_mode('SINGLE_PAGE');window.set_zoom(3);wait(settled);pause()
            viewport=window.area.viewport();h=window.area.horizontalScrollBar();v=window.area.verticalScrollBar()
            h.setValue(80);v.setValue(150);old=(h.value(),v.value());index=window.page_index
            Fingers(viewport).swipe((500,400),(410,270))
            assert h.value()==min(old[0]+90,h.maximum()) and v.value()==min(old[1]+130,v.maximum())
            assert window.page_index==index;checks['TOUCH_PAN']='PASS'
            window.set_view_mode('CONTINUOUS_SCROLL');window.set_fit('width');wait(settled);pause()
            area.restore_anchor((window.page_count//2,.5));wait(settled)
            finger=Fingers(area.viewport());jumps=[];original_go=window.go_to
            window.go_to=lambda p:jumps.append(p)
            try:
                before=area.scroll_top();finger.drag((400,600),(460,100));pause(.05)
                assert 450<=area.scroll_top()-before<=502
                assert window.page_index==area.anchor()[0]
                before=area.scroll_top();finger.drag((300,220),(580,220));pause(.05)
                assert abs(area.scroll_top()-before)<2 and not jumps
            finally:window.go_to=original_go
            checks['TOUCH_CONTINUOUS_VERTICAL_SCROLL']=checks['CONTINUOUS_HORIZONTAL_NO_NAVIGATION']='PASS'
            checks['CURRENT_PAGE_AUTO_UPDATE']='PASS'
            if args.kinetic:
                from kinetic_acceptance import exercise_kinetic
                row['kinetic']=exercise_kinetic(window,app,base)
            samples=[];maximum=area.verticalScrollBar().maximum()
            indices=range(window.page_count) if args.memory and window.page_count==615 else sorted({0,window.page_count//4,window.page_count//2,3*window.page_count//4,window.page_count-1})
            for index in indices:
                area.restore_anchor((index,.5));wait(lambda:window.page_index==index and settled())
                # Exercise scrolling at each anchor while retaining variable-height layout.
                finger.drag((400,400),(410,360),duration_ms=150,steps=4);wait(settled)
                stats=area.stats();assert stats['raster_bytes']<=area.MAX_RASTER_BYTES
                assert set(stats['rendered_pages'])<=set(stats['allowed_pages'])
                assert stats['scrollbar_maximum']==maximum
                samples.append({'page':index+1,'stats':stats,'rss':rss_tree()})
                if (index+1)%50==0:print(f'TOUCH MEMORY {source.name}: {index+1}/{window.page_count}',flush=True)
            row['memory']={'sampled_pages':len(samples),'max_cached_pages':max(len(s['stats']['rendered_pages']) for s in samples),
                           'max_raster_bytes':max(s['stats']['raster_bytes'] for s in samples),'raster_limit':area.MAX_RASTER_BYTES}
            if len(samples)==615:
                warm=statistics.median(s['rss']['total_bytes'] for s in samples[10:30])
                end=statistics.median(s['rss']['total_bytes'] for s in samples[-20:])
                # Concurrent validation may finish during traversal; both child processes are included.
                assert end-warm<max(96*1024**2,warm*.4),(warm,end)
                row['memory'].update(warm_rss=warm,end_rss=end,bounded='PASS')
            (base/'memory.json').write_text(json.dumps(samples,indent=2))
            checks['LAZY_RENDERING_REGRESSION']='PASS'
            window.grab().save(str(base/'continuous-touch.png'))
            window.export_to(base/'selected.pdf',[1,window.page_count]);wait(lambda:window.export_result is not None,90)
            assert window.export_result['status']=='PASS_DECODED' and window.export_result['page_count']==2,window.export_result
            assert window.export_result['preservation']['page_streams_byte_identical']
            checks['PARTIAL_EXPORT_REGRESSION']='PASS'
            printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(base/'printed.pdf'))
            dialog=QPrintDialog(printer,window);dialog.show();wait(dialog.isVisible);dialog.reject();pause(.05)
            window.start_print(printer,[1,window.page_count]);wait(lambda:window.print_worker is None,90)
            assert window.print_result,window.last_error
            checks['QT_PRINT_DIALOG']=checks['QT_PRINT_TO_PDF']='PASS'
            wait(lambda:window.validation_result is not None,600)
            assert window.validation_result['status']=='PASS_DECODED',window.validation_result
            assert window.validation_result['validation_mode']=='all_pages'
            row['final_status']=window.validation_result['status']
            checks['BACKGROUND_VALIDATION_REGRESSION']='PASS'
            (base/'full-validation.json').write_text(json.dumps(window.validation_result,ensure_ascii=False,indent=2),encoding='utf-8')
            row['max_gui_heartbeat_gap']=max((b-a for a,b in zip(ticks,ticks[1:])),default=0)
            window.set_view_mode('SINGLE_PAGE');wait(settled)
            assert not area.cache;checks['SINGLE_PAGE_REGRESSION']='PASS'
        except BaseException as exc:
            row['failure']=repr(exc);persist();raise
        finally:
            timer.stop();pids=list(window.worker.process_ids) if window.worker else []
            cache=window.worker.cache_path if window.worker else None
            window.close();wait(lambda:window.worker is None and window.print_worker is None,10)
            assert not cache or not cache.exists()
            if Path('/proc').is_dir():assert all(not Path(f'/proc/{pid}').exists() for pid in pids)
            checks['STALE_WORKERS_CANCELLED']='PASS'
            row['sha256_after']=sha256(source);assert row['sha256_after']==row['sha256_before']
            checks['SOURCE_BOOK_UNCHANGED']='PASS';persist()
        print(json.dumps(row,ensure_ascii=False),flush=True)
    evidence['status']='PASS';persist()


if __name__=='__main__':
    multiprocessing.freeze_support();main()
