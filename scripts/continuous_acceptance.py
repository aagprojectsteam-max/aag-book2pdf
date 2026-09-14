"""Real installed Qt acceptance and Linux RSS sampling; sources remain read-only."""
import argparse
import json
import multiprocessing
import os
from pathlib import Path
import statistics
import time


def rss_tree():
    """RSS includes the GUI and live descendants, sampled from Linux procfs."""
    if not Path('/proc/self/status').exists():
        return None
    rows=[];pending=[os.getpid()];seen=set()
    while pending:
        pid=pending.pop()
        if pid in seen:continue
        seen.add(pid)
        try:
            status=Path(f'/proc/{pid}/status').read_text()
            rss=next(int(line.split()[1])*1024 for line in status.splitlines() if line.startswith('VmRSS:'))
            rows.append({'pid':pid,'rss_bytes':rss,'gui':pid==os.getpid()})
            for task in Path(f'/proc/{pid}/task').iterdir():
                pending.extend(int(value) for value in (task/'children').read_text().split())
        except (OSError,StopIteration):pass
    return {'processes':rows,'gui_bytes':sum(r['rss_bytes'] for r in rows if r['gui']),
            'worker_bytes':sum(r['rss_bytes'] for r in rows if not r['gui']),
            'total_bytes':sum(row['rss_bytes'] for row in rows)}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--memory-source',type=Path,action='append',required=True)
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt,QSettings
    from PySide6.QtPrintSupport import QPrinter,QPrintDialog
    from book2pdf.gui.viewer_window import ViewerWindow
    from book2pdf.state import sha256
    import book2pdf
    app=QApplication(['AAG Book2PDF continuous acceptance'])
    app.setQuitOnLastWindowClosed(False);app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    evidence={'platform':app.platformName(),'module':str(Path(book2pdf.__file__).resolve()),
              'version':book2pdf.__version__,'books':[]}
    settings_path=args.evidence/'settings.ini'
    settings=QSettings(str(settings_path),QSettings.Format.IniFormat)
    settings.setValue('viewer/prefetch_pages',1)

    def wait(condition,timeout=300):
        deadline=time.monotonic()+timeout
        while True:
            app.processEvents()
            if condition():return
            if time.monotonic()>deadline:raise TimeoutError('Viewer acceptance timeout')
            time.sleep(.005)

    def record():
        (args.evidence/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')

    for source in args.sources:
        row={'source':str(source),'sha256_before':sha256(source)}
        base=args.evidence/source.stem;base.mkdir();started=time.monotonic()
        window=ViewerWindow(source,settings=settings,view_mode='CONTINUOUS_SCROLL')
        window.show();window.activateWindow()
        cache=None
        try:
            wait(lambda:window.last_displayed==0 or window.worker is None)
            assert window.open_result,window.last_error
            area=window.continuous_area;cache=window.worker.cache_path
            row.update(status=window.open_result['status'],page_count=window.page_count,
                       recovery_level=window.document_model['recovery_level'],open_seconds=time.monotonic()-started)
            assert window.layoutDirection()==Qt.LayoutDirection.RightToLeft
            assert area.layoutDirection()==Qt.LayoutDirection.LeftToRight
            window.set_fit('width')
            def settled():
                return (window.last_displayed==window.page_index
                        and set(area.wanted_pages())<=set(area.cache))
            wait(settled)
            from PySide6.QtWidgets import QToolBar,QToolButton
            assert len(window.findChildren(QToolBar))==1
            assert sum(a.isSeparator() for a in window.toolbar.actions())==4
            row['toolbar']=[]
            for width in (800,1040,1440):
                window.resize(width,900)
                until=time.monotonic()+.4
                wait(lambda:time.monotonic()>until and settled())
                assert window.width()==width
                buttons=[w for w in window.toolbar.findChildren(QToolButton) if w.isVisible() and w.objectName()!='qt_toolbar_ext_button']
                assert max(w.geometry().center().y() for w in buttons)-min(w.geometry().center().y() for w in buttons)<=1
                extension=window.toolbar.findChild(QToolButton,'qt_toolbar_ext_button')
                row['toolbar'].append({'width':width,'height':window.toolbar.height(),'overflow':extension.isVisible()})
                window.grab().save(str(base/f'toolbar-{width}.png'))
            assert len({r['height'] for r in row['toolbar']})==1
            assert row['toolbar'][0]['overflow']
            window.resize(1040,900)
            until=time.monotonic()+.4;wait(lambda:time.monotonic()>until and settled())
            window.toggle_fullscreen();wait(window.isFullScreen)
            window.exit_fullscreen();wait(lambda:not window.isFullScreen())
            until=time.monotonic()+.4;wait(lambda:time.monotonic()>until and settled())
            row['fullscreen']='PASS'
            for label,index in [('first',0),('middle',window.page_count//2),('last',window.page_count-1)]:
                window.page_spin.setValue(index+1);wait(lambda:window.last_displayed==index and settled())
                assert window.page_index==index and index in area.cache
                assert set(area.cache)<=set(area.wanted_pages())
                assert all(w==area.viewport().width()-24 for w,h in area.rects)
                window.grab().save(str(base/(label+'.png')))
                row[label+'_page']='PASS'
            window.go_to(window.page_count//2);wait(settled)
            area.restore_anchor((window.page_index,.67));area.refresh_visible();anchor=area.anchor()
            window.change_zoom(1);wait(settled)
            assert area.anchor()[0]==anchor[0] and abs(area.anchor()[1]-anchor[1])<.003, (anchor,area.anchor(),window.zoom,area.viewport().size())
            row['zoom_anchor_before']=anchor;row['zoom_anchor_after']=area.anchor()
            window.set_fit('page');wait(settled)
            assert area.rects[window.page_index][1]<=area.viewport().height()+1
            row.update(zoom_position='PASS',fit_page='PASS',fit_width='PASS',page_number_jump='PASS')
            window.set_fit('width');wait(settled)
            if source.resolve() in {p.resolve() for p in args.memory_source}:
                samples=[];geometry=area.verticalScrollBar().maximum();begin=time.monotonic()
                for index in range(window.page_count):
                    area.restore_anchor((index,.5))
                    wait(lambda:window.page_index==index and settled(),timeout=60)
                    stats=area.stats();rss=rss_tree()
                    assert set(stats['rendered_pages'])<=set(stats['allowed_pages'])
                    assert stats['raster_bytes']<=area.MAX_RASTER_BYTES
                    assert stats['scrollbar_maximum']==geometry
                    samples.append({'page':index+1,'stats':stats,'rss':rss})
                    if (index+1)%50==0:print(f'MEMORY page={index+1} frames={len(area.cache)} raster={area.raster_bytes()} rss={rss["total_bytes"]}',flush=True)
                (base/'memory-samples.json').write_text(json.dumps(samples,indent=2),encoding='utf-8')
                warm=statistics.median(s['rss']['total_bytes'] for s in samples[10:30])
                end=statistics.median(s['rss']['total_bytes'] for s in samples[-20:])
                peak=max(s['rss']['total_bytes'] for s in samples)
                # Allow allocator/native-store warmup; never accept a document-sized raster cache.
                assert end-warm<max(96*1024**2,warm*.4),(warm,end,peak)
                row['memory']={'pages_traversed':len(samples),'seconds':time.monotonic()-begin,
                               'warm_rss_bytes':warm,'end_rss_bytes':end,'peak_rss_bytes':peak,
                               'peak_gui_rss_bytes':max(s['rss']['gui_bytes'] for s in samples),
                               'peak_worker_rss_bytes':max(s['rss']['worker_bytes'] for s in samples),
                               'max_cached_pages':max(len(s['stats']['rendered_pages']) for s in samples),
                               'max_raster_bytes':max(s['stats']['raster_bytes'] for s in samples),
                               'scrollbar_geometry_stable':True,'bounded':'PASS'}
            current=window.page_index
            window.set_view_mode('SINGLE_PAGE');wait(lambda:window.last_displayed==current)
            assert not area.cache and not window.area.page_image.pixmap().isNull()
            window.set_view_mode('CONTINUOUS_SCROLL');wait(lambda:window.last_displayed==current and settled())
            assert window.area.page_image.pixmap().isNull()
            row['single_page_regression']='PASS'
            restored=QSettings(str(settings_path),QSettings.Format.IniFormat)
            assert restored.value('viewer/view_mode')=='CONTINUOUS_SCROLL'
            row['view_mode_persistence']='PASS'
            window.save_to(base/'full.pdf');wait(lambda:window.save_result is not None)
            wait(lambda:window.validation_result is not None)
            row['interactive_status']=row['status']
            row['status']=window.validation_result['status']
            assert window.save_result['status']==row['status'],window.save_result
            window.export_to(base/'selected.pdf',[1,window.page_count]);wait(lambda:window.export_result is not None)
            assert window.export_result['status']==row['status'],window.export_result
            assert window.export_result['page_count']==2 and window.export_result['preservation']['page_streams_byte_identical']
            row.update(full_export='PASS',partial_export='PASS')
            printer=QPrinter(QPrinter.PrinterMode.HighResolution);printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(base/'printed.pdf'))
            dialog=QPrintDialog(printer,window);dialog.show();wait(dialog.isVisible);dialog.reject()
            window.start_print(printer,[1,window.page_count]);wait(lambda:window.print_worker is None)
            assert window.print_result,window.last_error
            row.update(print_dialog='PASS',qt_pdf_print='PASS',printed_pages=[1,window.page_count],viewer='PASS')
        finally:
            window.close();wait(lambda:window.worker is None and window.print_worker is None)
        if cache:assert not cache.exists()
        row['source_unchanged']=sha256(source)==row['sha256_before'];assert row['source_unchanged']
        evidence['books'].append(row);record();print(json.dumps(row,ensure_ascii=False),flush=True)
    # A new top-level viewer reads the previously chosen mode on a fresh launch path.
    window=ViewerWindow(args.sources[0],settings=QSettings(str(settings_path),QSettings.Format.IniFormat))
    window.show()
    try:
        wait(lambda:window.last_displayed==0)
        assert window.view_mode=='CONTINUOUS_SCROLL' and window.continuous_area.active
        evidence['persisted_reopen']='PASS';record()
    finally:
        window.close();wait(lambda:window.worker is None)
    return 0


if __name__=='__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
