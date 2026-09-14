"""Real Qt viewer acceptance, usable with repo or installed Python (no private fixtures bundled)."""
import argparse
import json
from pathlib import Path
import time
import multiprocessing


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--evidence',required=True,type=Path)
    args=parser.parse_args()
    args.evidence.mkdir(parents=True,exist_ok=False)
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from PySide6.QtPrintSupport import QPrinter
    from book2pdf.gui.viewer_window import ViewerWindow
    from book2pdf.gui.main_window import MainWindow
    from book2pdf.state import sha256
    import book2pdf
    app=QApplication(['AAG Book2PDF Universal acceptance'])
    app.setQuitOnLastWindowClosed(False);app.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
    evidence={'platform':app.platformName(),'module':str(Path(book2pdf.__file__).resolve()),'version':book2pdf.__version__,'books':[]}
    converter=MainWindow();converter.show();app.processEvents()
    assert converter.layoutDirection()==Qt.LayoutDirection.RightToLeft
    converter.grab().save(str(args.evidence/'converter.png'));converter.close()
    for source in args.sources:
        row={'source':str(source),'sha256_before':sha256(source)}
        base=args.evidence/source.stem;base.mkdir()
        window=ViewerWindow(source,view_mode="SINGLE_PAGE");window.show();window.activateWindow()
        def wait(condition):
            deadline=time.monotonic()+180
            while not condition():
                app.processEvents()
                if time.monotonic()>deadline:raise TimeoutError('Viewer acceptance timeout')
                time.sleep(.01)
        try:
            wait(lambda:window.last_displayed==0 or window.worker is None)
            if window.open_result:
                row.update(status=window.open_result['status'],page_count=window.page_count,assumptions=window.open_result.get('assumptions'),
                           affected_pages=window.open_result.get('affected_pages'),preview_policy=window.open_result.get('preview_policy'))
                for label,index in [('first',0),('middle',window.page_count//2),('last',window.page_count-1)]:
                    window.go_to(index);wait(lambda:window.last_displayed==index)
                    assert not window.area.page_image.pixmap().isNull()
                    window.grab().save(str(base/(label+'.png')));row[label+'_page']='PASS'
                window.actual_button.click();window.zoom_in_button.click();assert window.zoom>1
                window.fit_page_button.click();assert window.fit_mode=='page'
                window.fit_width_button.click();assert window.fit_mode=='width'
                row.update(navigation='PASS',zoom='PASS',fit_page='PASS',fit_width='PASS',viewer='PASS')
                if row['status']=='RECONSTRUCTED_PREVIEW':assert 'תצוגה משוחזרת' in window.banner.text()
                window.save_to(base/'full.pdf');wait(lambda:window.save_result is not None)
                assert window.save_result['status']==row['status'],window.save_result
                window.export_to(base/'selected.pdf',[1,window.page_count]);wait(lambda:window.export_result is not None)
                assert window.export_result['status']==row['status'],window.export_result
                assert window.export_result['page_count']==2 and window.export_result['preservation']['page_streams_byte_identical']
                row.update(full_export='PASS',partial_export='PASS')
                printer=QPrinter(QPrinter.PrinterMode.HighResolution);printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
                printer.setOutputFileName(str(base/'printed.pdf'));window.start_print(printer,[1])
                wait(lambda:window.print_worker is None)
                assert window.print_result,window.last_error
                row['qt_pdf_print']='PASS'
            else:
                row.update(status=window.analysis_result['status'],analysis_package=window.analysis_result['analysis_package'],viewer='SAFE_REJECTION')
                assert window.analysis_button.isEnabled()
                window.grab().save(str(base/'analysis.png'))
            cache=window.worker.cache_path if window.worker else None
        finally:
            window.close();wait(lambda:window.worker is None and window.print_worker is None)
        if cache:assert not cache.exists()
        row['source_unchanged']=sha256(source)==row['sha256_before'];assert row['source_unchanged']
        evidence['books'].append(row)
        (args.evidence/'evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
    return 0


if __name__=='__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
