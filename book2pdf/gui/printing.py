"""Native Qt printing, with page rendering outside the GUI thread."""
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, QRectF
from PySide6.QtGui import QImage, QPainter
from PySide6.QtPrintSupport import QPrinter

from ..atomic import staged_output
from ..validator import validate
from ..viewer import render_page
from ..supervised import Session, checkpoint


class PrintThread(QThread):
    progress = Signal(int,int)
    completed = Signal(dict)
    failed = Signal(str)

    def __init__(self, pdf, printer, pages, *, original_source, overwrite=False, parent=None, viewing_source=None, recovery="safe-salvage", expected_hash=None):
        super().__init__(parent)
        self.pdf = Path(pdf)
        self.printer = printer
        self.pages = pages
        self.original_source = Path(original_source)
        self.overwrite = overwrite
        self.cancelled = threading.Event()
        self.viewing_source = viewing_source
        self.recovery = recovery
        self.expected_hash = expected_hash
        self.source_pages = list(pages)

    def paint(self):
        painter = QPainter()
        if not painter.begin(self.printer):
            raise ValueError('לא ניתן להתחיל הדפסה במדפסת שנבחרה.')
        try:
            with Session() as pool:
                pages = self.pages
                if self.printer.pageOrder() == QPrinter.PageOrder.LastPageFirst:
                    pages = list(reversed(pages))
                for index,page in enumerate(pages):
                    if self.cancelled.is_set():
                        self.printer.abort()
                        raise ValueError('ההדפסה בוטלה.')
                    if index and not self.printer.newPage():
                        raise ValueError('המדפסת לא קיבלה עמוד חדש.')
                    # 300 dpi requested; shared renderer caps longest side at 4096px.
                    data = pool.call(render_page,self.pdf,page-1,300/72,1,cancelled=self.cancelled.is_set)
                    image = QImage(data['pixels'],data['width'],data['height'],data['stride'],QImage.Format.Format_RGB888).copy()
                    rect = self.printer.pageRect(QPrinter.Unit.DevicePixel)
                    scale = min(rect.width()/image.width(),rect.height()/image.height())
                    width,height = image.width()*scale,image.height()*scale
                    painter.drawImage(QRectF((rect.width()-width)/2,(rect.height()-height)/2,width,height),image)
                    self.progress.emit(index+1,len(pages))
        finally:
            painter.end()
        return pages

    def run(self):
        try:
            if self.viewing_source:
                from ..interactive import export_selection, ACCEPTED
                with tempfile.TemporaryDirectory(dir=self.pdf.parent,prefix='print-') as directory:
                    root=Path(directory)
                    with Session() as session:
                        result=session.call(export_selection,self.viewing_source,root/'backend',self.recovery,
                            root/'selected.pdf',self.source_pages,False,self.expected_hash,cancelled=self.cancelled.is_set)
                    if result['status'] not in ACCEPTED:raise ValueError(result['error'])
                    self.pdf=root/'selected.pdf'
                    self.pages=list(range(1,len(self.source_pages)+1))
                    self.run_print()
            else:self.run_print()
        except Exception as exc:
            self.failed.emit(str(exc))

    def run_print(self):
        try:
            if self.printer.outputFormat() == QPrinter.OutputFormat.PdfFormat:
                target = Path(self.printer.outputFileName()).absolute()
                with staged_output(target,self.original_source,self.overwrite) as tmp:
                    self.printer.setOutputFileName(str(tmp))
                    printed_pages = self.paint()
                    from ..provenance import embedded_provenance,stamp_preview,stamp_decoded
                    record = embedded_provenance(self.pdf)
                    if record is None:
                        from ..state import pdf_provenance, sha256
                        prior = pdf_provenance(self.pdf, sha256(self.pdf))
                        record = prior if prior and prior.get('recovery_class') == 'DECODED_CONTAINER_CONTENT' else None
                    if record:
                        from types import SimpleNamespace
                        from ..recovery.document import GENERIC_CLASSES
                        if record['recovery_class']=='RECONSTRUCTED_PREVIEW':
                            record['affected_pages'] = [i+1 for i,p in enumerate(printed_pages) if p in record.get('affected_pages',[])]
                            stamp_preview(tmp,SimpleNamespace(**record))
                        elif record['recovery_class'] in GENERIC_CLASSES:
                            from ..recovery.document import selected_model
                            from ..provenance import stamp_recovered
                            record['recovered_document']=selected_model(record['recovered_document'],printed_pages,printed=True)
                            record['fidelity']='PRINT_RENDERING'
                            stamp_recovered(tmp,SimpleNamespace(**record))
                        else:
                            record['preservation']['pages']=[record['preservation']['pages'][p-1] for p in printed_pages]
                            record['preservation'].update(page_count=len(printed_pages),native_pixels_preserved=False,
                                original_djvu_bytes_preserved=False,rendering_mode='Qt-print-to-PDF')
                            record['fidelity']='PRINT_RENDERING'
                            stamp_decoded(tmp,SimpleNamespace(**record))
                    with Session() as pool:
                        count,_ = pool.call(validate,tmp,True,progress=checkpoint,cancelled=self.cancelled.is_set)
                    if count != len(self.pages):
                        raise ValueError('מספר העמודים שהודפסו אינו תואם לבחירה.')
                self.completed.emit({'status':'PASS','pages':[self.source_pages[p-1] for p in printed_pages] if self.viewing_source else printed_pages,'output':str(target),'mode':'Qt-print-to-PDF'})
            else:
                self.paint()
                self.completed.emit({'status':'SUBMITTED','pages':self.source_pages,'mode':'physical-printer'})
        except Exception as exc:
            self.failed.emit(str(exc))
