"""Real Qt viewer acceptance; usable as a sitecustomize hook for desktop dispatch."""
import json
import os
from pathlib import Path
import time


def install_hook():
    from PySide6 import QtWidgets
    from PySide6.QtCore import Qt,QTimer
    from PySide6.QtWidgets import QFileDialog
    original_app = QtWidgets.QApplication

    class AcceptanceApplication(original_app):
        def __init__(self,*args,**kwargs):
            super().__init__(*args,**kwargs)
            self.setQuitOnLastWindowClosed(False)
            self.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeDialogs,True)
            self.acceptance = Driver(self)

    QtWidgets.QApplication = AcceptanceApplication


class Driver:
    def __init__(self,app):
        from PySide6.QtCore import QTimer
        self.app=app
        self.base=Path(os.environ['BOOK2PDF_ACCEPTANCE_DIR']).resolve()
        self.base.mkdir(parents=True,exist_ok=True)
        import book2pdf
        self.evidence={'errors':[],'stage':0,'platform':app.platformName(),
                       'module':str(Path(book2pdf.__file__).resolve()),'version':book2pdf.__version__}
        self.stage=0
        self.window=None
        self.start=time.monotonic()
        self.timer=QTimer(app)
        self.timer.timeout.connect(self.tick)
        self.timer.start(80)
        self.ticks=0

    def fail(self,message):
        self.evidence['errors'].append(message)
        self.finish(2)

    def finish(self,code=0):
        self.evidence['ui_ticks']=self.ticks
        (self.base/'viewer-evidence.json').write_text(json.dumps(self.evidence,ensure_ascii=False,indent=2))
        print(json.dumps(self.evidence,ensure_ascii=False),flush=True)
        self.timer.stop()
        self.app.exit(code)

    def tick(self):
        try:
            self.advance()
        except Exception as exc:
            self.fail(repr(exc))

    def advance(self):
        from PySide6.QtCore import Qt,QTimer
        from PySide6.QtWidgets import QFileDialog
        from PySide6.QtPrintSupport import QPrinter,QPrintDialog
        from book2pdf.gui.viewer_window import ViewerWindow
        from book2pdf.gui.page_dialog import PageSelectionDialog
        from book2pdf.state import sha256
        self.ticks+=1
        if time.monotonic()-self.start>300:
            self.fail('Timed out')
            return
        if self.window is None:
            self.window=next((w for w in self.app.topLevelWidgets() if isinstance(w,ViewerWindow)),None)
            if not self.window:
                return
            self.before=sha256(self.window.source)
        w=self.window
        self.evidence['stage']=self.stage
        if self.ticks%25==0:
            (self.base/'progress.json').write_text(json.dumps({'stage':self.stage,'page_count':w.page_count,
                'displayed':w.last_displayed,'page_index':w.page_index,'zoom':w.zoom,
                'status':w.status.text(),'error':w.last_error},ensure_ascii=False))
        if w.last_error:
            raise RuntimeError(w.last_error)
        if self.stage==0 and w.last_displayed==0:
            expected_count=os.environ.get('BOOK2PDF_EXPECTED_PAGES')
            expected_status=os.environ.get('BOOK2PDF_EXPECTED_STATUS')
            if expected_count: assert w.page_count==int(expected_count)
            if expected_status: assert w.open_result['status'] in (expected_status,'INTERACTIVE_READY')
            self.evidence.update(DIRECT_BOOK_OPEN=True,REAL_BOOK_PAGE_COUNT=w.page_count,
                                 INTERACTIVE_STATUS=w.open_result['status'],FIRST_PAGE_VISIBLE=(0 in w.continuous_area.cache if w.view_mode=="CONTINUOUS_SCROLL" else not w.area.page_image.pixmap().isNull()))
            w.grab().save(str(self.base/'viewer-first.png'))
            self.cache=w.worker.cache_path
            self.stage=1;w.go_to(w.page_count//2)
        elif self.stage==1 and w.last_displayed==w.page_count//2:
            self.evidence['MIDDLE_PAGE_VISIBLE']=True
            w.grab().save(str(self.base/'viewer-middle.png'))
            self.stage=2;w.last_button.click()
        elif self.stage==2 and w.last_displayed==w.page_count-1:
            self.evidence['LAST_PAGE_VISIBLE']=True
            w.grab().save(str(self.base/'viewer-last.png'))
            self.stage=3;w.page_spin.setValue(25)
        elif self.stage==3 and w.last_displayed==24:
            self.evidence['PAGE_NAVIGATION']=w.page_label.text()==f'עמוד 25 מתוך {w.page_count}'
            self.stage=4;w.actual_button.click();w.zoom_in_button.click()
        elif self.stage==4 and abs(w.zoom-1.2)<.001 and w.last_displayed==w.page_index:
            self.evidence['ZOOM']=True
            self.stage=5;w.fit_page_button.click()
        elif self.stage==5 and w.fit_mode=='page' and w.last_displayed==w.page_index:
            assert (w.continuous_area.rects[w.page_index][1]<=w.continuous_area.viewport().height()+2 if w.view_mode=="CONTINUOUS_SCROLL" else w.area.page_image.height()<=w.area.viewport().height()+2)
            assert (w.continuous_area.rects[w.page_index][0]<=w.continuous_area.viewport().width()+2 if w.view_mode=="CONTINUOUS_SCROLL" else w.area.page_image.width()<=w.area.viewport().width()+2)
            self.evidence['FIT_PAGE']=True
            self.stage=6;w.fit_width_button.click()
        elif self.stage==6 and w.fit_mode=='width' and w.last_displayed==w.page_index:
            assert (w.continuous_area.rects[w.page_index][0]<=w.continuous_area.viewport().width()+2 if w.view_mode=="CONTINUOUS_SCROLL" else w.area.page_image.width()<=w.area.viewport().width()+2)
            self.evidence['FIT_WIDTH']=True
            self.stage=7;w.save_to(self.base/'saved-from-viewer.pdf')
        elif self.stage==7 and w.save_result and w.validation_result:
            expected_status=os.environ.get('BOOK2PDF_EXPECTED_STATUS')
            if expected_status:assert w.validation_result['status']==expected_status
            self.evidence['RECOVERY_STATUS']=w.validation_result['status']
            assert w.save_result['status']==w.open_result['status'],w.save_result
            self.evidence['SAVE_AS_PDF_FROM_VIEWER']=True
            self.stage=8;w.export_to(self.base/'page-1.pdf','1')
        elif self.stage==8 and w.export_result:
            assert w.export_result['page_count']==1,w.export_result
            self.evidence['EXPORT_PAGE_1']=True
            w.export_result=None;self.stage=9;w.export_to(self.base/'pages-1-10.pdf','1-10')
        elif self.stage==9 and w.export_result:
            assert w.export_result['page_count']==10,w.export_result
            self.evidence['EXPORT_PAGES_1_10']=True
            w.export_result=None;self.stage=10;w.export_to(self.base/'pages-5-7-20-25.pdf','5-7,20-25')
        elif self.stage==10 and w.export_result:
            assert w.export_result['page_count']==9,w.export_result
            self.evidence['EXPORT_PAGES_5_7_20_25']=True
            self.evidence['EXPORT_QUALITY_PRESERVED']=w.export_result['preservation']['page_streams_byte_identical']
            self.stage=11
            # Exercise the actual compact range dialog through the Print button,
            # inspect the subsequent native Qt print dialog, then cancel submission.
            def select_current():
                dialog=self.app.activeModalWidget()
                assert isinstance(dialog,PageSelectionDialog)
                dialog.current_button.setChecked(True)
                dialog.accept()
                QTimer.singleShot(150,reject_print)
            def reject_print():
                dialog=self.app.activeModalWidget()
                assert isinstance(dialog,QPrintDialog)
                self.evidence['PRINT_DIALOG']=True
                dialog.reject()
            QTimer.singleShot(150,select_current)
            w.print_button.click()
        elif self.stage==11:
            printer=QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(self.base/'print-current.pdf'))
            self.stage=12;w.start_print(printer,[w.page_index+1])
        elif self.stage==12 and w.print_worker is None and w.print_result:
            assert w.print_result['pages']==[25],w.print_result
            self.evidence['PRINT_CURRENT_PAGE']=True
            w.print_result=None
            printer=QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(self.base/'print-custom.pdf'))
            from book2pdf.page_ranges import parse_pages
            self.stage=13;w.start_print(printer,parse_pages('1,3,5',w.page_count))
        elif self.stage==13 and w.print_worker is None and w.print_result:
            assert w.print_result['pages']==[1,3,5]
            self.evidence.update(PRINT_CUSTOM_RANGE=True,PRINT_TO_PDF=True,SOURCE_BOOK_UNCHANGED=sha256(w.source)==self.before)
            self.stage=14;w.close()
        elif self.stage==14 and w.worker is None:
            self.evidence['CLOSED_CLEANLY']=not w.isVisible()
            self.evidence['TEMP_CLEANED']=not self.cache.exists()
            self.finish()


if __name__=='__main__':
    import sys
    os.environ.setdefault('BOOK2PDF_ACCEPTANCE_DIR',str(Path(sys.argv[2]).resolve()))
    install_hook()
    from book2pdf.gui.app import main
    raise SystemExit(main([sys.argv[1]]))
