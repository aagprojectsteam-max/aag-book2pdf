"""Real dataset acceptance through the unchanged shared Qt viewer surfaces."""
import argparse,json,multiprocessing,time
from pathlib import Path


def main():
    ap=argparse.ArgumentParser();ap.add_argument('source',type=Path);ap.add_argument('--evidence',type=Path,required=True);ap.add_argument('--full-export',action='store_true');args=ap.parse_args()
    args.evidence.mkdir(parents=True,exist_ok=True)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QSettings,Qt
    from PySide6.QtPrintSupport import QPrinter
    from book2pdf.gui.viewer_window import ViewerWindow
    from continuous_acceptance import rss_tree
    app=QApplication(['TurboSun acceptance']);app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    app.setQuitOnLastWindowClosed(False)
    evidence={};window=ViewerWindow(args.source,settings=QSettings(str(args.evidence/'settings.ini'),QSettings.Format.IniFormat),view_mode='SINGLE_PAGE')
    def save(): (args.evidence/'acceptance.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
    def wait(predicate,seconds=180):
        end=time.monotonic()+seconds
        while True:
            app.processEvents()
            if window.last_error:raise AssertionError(window.last_error)
            if predicate():return
            if time.monotonic()>end:raise TimeoutError('Acceptance timeout')
            time.sleep(.005)
    def progress(event):
        if event.get('kind')=='progress' and event.get('done',0)//1000 != evidence.get('last_progress_thousand',-1):
            evidence['last_progress_thousand']=event.get('done',0)//1000
            print(json.dumps(event),flush=True)
    window.worker.validation_updated.connect(progress)
    start=time.monotonic();window.show()
    try:
        wait(lambda:window.last_displayed==0)
        evidence.update(time_to_first_page=time.monotonic()-start,page_count=window.page_count,
                        dataset_ready_seconds=window.document_model['metadata']['dataset_ready_seconds'],first_status=window.open_result['status'])
        assert window.page_count>0
        window.grab().save(str(args.evidence/'first.png'))
        jumps=[]
        for number in sorted({p for p in (1,100,1000,5000,10000,20000,30000,window.page_count//2+1,window.page_count) if p<=window.page_count}):
            start=time.monotonic();window.go_to(number-1);wait(lambda:window.last_displayed==number-1)
            jumps.append({'page':number,'seconds':time.monotonic()-start})
        evidence['random_jumps']=jumps;window.grab().save(str(args.evidence/'last.png'));save()
        start=time.monotonic();window.set_view_mode('CONTINUOUS_SCROLL');window.set_fit('width');area=window.continuous_area
        wait(lambda:set(area.wanted_pages())<=set(area.cache));evidence['continuous_ready_seconds']=time.monotonic()-start
        samples=[]
        for index in range(0,window.page_count,max(1,window.page_count//50)):
            window.go_to(index);wait(lambda:window.page_index==index and set(area.wanted_pages())<=set(area.cache))
            stats=area.stats();assert stats['raster_bytes']<=area.MAX_RASTER_BYTES
            assert set(stats['rendered_pages'])<=set(stats['allowed_pages'])
            samples.append({'page':index+1,'stats':stats,'rss':rss_tree()})
        evidence['memory_samples']=samples
        if all(sample['rss'] is not None for sample in samples):
            warm=samples[5:15];end=samples[-10:]
            import statistics
            warm_rss=statistics.median(s['rss']['total_bytes'] for s in warm)
            end_rss=statistics.median(s['rss']['total_bytes'] for s in end)
            assert end_rss-warm_rss<max(96*1024**2,warm_rss*.25),(warm_rss,end_rss)
            evidence['bounded_rss']='PASS'
        else:evidence['bounded_rss']='NOT_PROVEN'
        evidence['max_rendered_images']=max(len(s['stats']['rendered_pages']) for s in samples)
        evidence['max_raster_mib']=max(s['stats']['raster_bytes'] for s in samples)/1024**2
        evidence['max_rss']=max((s['rss']['total_bytes'] for s in samples if s['rss']),default=None)
        assert len({s['stats']['scrollbar_maximum'] for s in samples})==1
        anchor=area.anchor();window.set_zoom(window.zoom*1.1);wait(lambda:window.last_displayed==window.page_index)
        assert area.anchor()[0]==anchor[0]
        window.set_fit('page');window.set_fit('width');wait(lambda:set(area.wanted_pages())<=set(area.cache))
        evidence['continuous_zoom_position']='PASS'
        # Native Qt touch input injection exercises shared semantics; not physical proof.
        from kinetic_acceptance import exercise_kinetic
        evidence['synthetic_kinetic']=exercise_kinetic(window,app,args.evidence)
        evidence['physical_touch']='NOT_PROVEN'
        for i,spec in enumerate(([1],list(range(1,min(10,window.page_count)+1)),[1,3,5],[1000,5000,10000])):
            if max(spec)>window.page_count:continue
            window.export_result=None;window.export_to(args.evidence/f'partial-{i}.pdf',spec)
            wait(lambda:window.export_result is not None)
            assert window.export_result['status']=='PASS_DECODED',window.export_result
            assert window.export_result['page_count']==len(spec)
        evidence['partial_export']='PASS'
        printer=QPrinter();printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat);printer.setOutputFileName(str(args.evidence/'printed.pdf'))
        window.start_print(printer,[window.page_count]);wait(lambda:window.print_worker is None)
        assert window.print_result,window.last_error;evidence['print_to_pdf']='PASS'
        window.set_view_mode('SINGLE_PAGE');wait(lambda:window.last_displayed==window.page_index)
        assert not area.cache;evidence['single_page']='PASS';save()
        if args.full_export:window.save_to(args.evidence/'full.pdf')
        wait(lambda:window.validation_result is not None,1500)
        assert window.validation_result['status']=='PASS_DECODED',window.validation_result
        evidence['background_validation']='PASS'
        if args.full_export:
            wait(lambda:window.save_result is not None,1500)
            assert window.save_result['status']=='PASS_DECODED',window.save_result
            assert window.save_result['page_count']==window.page_count
            evidence['full_export']='PASS'
            (args.evidence/'full-export.json').write_text(json.dumps(window.save_result,ensure_ascii=False))
        evidence['status']='PASS';save()
    finally:
        window.close();end=time.monotonic()+10
        while window.worker is not None and time.monotonic()<end:app.processEvents();time.sleep(.01)
        assert window.worker is None
    print(json.dumps({k:v for k,v in evidence.items() if k not in ('memory_samples','synthetic_kinetic')},ensure_ascii=False))

if __name__=='__main__':multiprocessing.freeze_support();main()
