"""Interactive readiness is explicitly provisional, never conversion acceptance.

PDF structure/geometry and the first requested render permit reading. Canonical
convert() still supplies full acceptance independently. BKF uses the same lossless
assembler, selecting one native component instead of assembling every scan.
"""
import copy
import math
from pathlib import Path
import shutil
import tempfile

from .models import Result, Options
from .state import sha256
from .supervised import checkpoint
from .viewer import document_layout, render_page
from .recovery.document import from_result, selected_model

ACCEPTED=('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW')
_backend=None


def _checked_render(path,page,zoom,ratio,max_dimension,font_policy):
    import pymupdf
    from .render_diagnostics import classify
    from .validator import ValidationError
    pymupdf.TOOLS.mupdf_warnings()
    image=render_page(path,page,zoom,ratio,max_dimension)
    warning=pymupdf.TOOLS.mupdf_warnings()
    if not classify(warning,font_policy):raise ValidationError(warning[:2000])
    image['warning']=warning
    return image


def _pdf_structure(path):
    import pikepdf
    import pymupdf
    from .validator import check_tree, ValidationError
    with path.open('rb') as stream:
        if not stream.read(8).startswith(b'%PDF-'):raise ValidationError('Missing PDF header')
    with pikepdf.open(path,attempt_recovery=False,inherit_page_attributes=False) as pdf:
        if pdf.is_encrypted:raise ValidationError('Encrypted document')
        count=check_tree(pdf)
        if count!=len(pdf.pages):raise ValidationError('Page enumeration mismatch')
        if pdf.get_warnings():raise ValidationError('PDF structural parsing reported warnings')
    with pymupdf.open(path) as pdf:
        if pdf.is_encrypted or pdf.is_repaired or pdf.page_count!=count:
            raise ValidationError('Independent parser rejected structure')
        for page in pdf:
            if not all(math.isfinite(v) and v>0 for v in (page.rect.width,page.rect.height)):
                raise ValidationError('Invalid page geometry')
    return count


def _info_geometry(entry):
    info=next(c for c in entry.chunks if c['type']=='INFO' and c['depth']==1)
    raw=entry.decoded[info['offset']+8:info['offset']+18]
    width=int.from_bytes(raw[:2],'big');height=int.from_bytes(raw[2:4],'big')
    dpi=int.from_bytes(raw[6:8],'little') if raw[7]!=255 else 300
    if not 25<=dpi<=6000:dpi=300
    # DjVuLibre DjVuInfo::decode: orientations 6/5 are quarter turns.
    # https://github.com/DjVuLibre/djvulibre/blob/master/libdjvu/DjVuInfo.cpp
    if raw[9]&7 in (5,6):width,height=height,width
    return {'width':width*72/dpi,'height':height*72/dpi}


class Backend:
    def __init__(self,source,cache,recovery, *, first_page=0):
        from .worker import convert
        from .container_decode import extract_pdf, RECOVERY_CLASS, STRATEGY
        from .envelope import EnvelopeError
        self.source=Path(source);self.cache=Path(cache);self.cache.mkdir(parents=True,exist_ok=True)
        self.pdf=self.cache/'view.pdf';self.book=None;self.last_native_page=None
        before=self.source.stat()
        result=Result(str(self.source),str(self.pdf),source_hash=sha256(self.source),
                      source_size=before.st_size,source_mtime=before.st_mtime_ns,
                      status='INTERACTIVE_READY',validation_status='PENDING_FULL_VALIDATION',validation_mode='first_visible_page')
        with self.source.open('rb') as stream:signature=stream.read(3)
        try:
            if signature==b'BKF':
                from .bkf.format import parse, MAX_INPUT
                from .bkf import STRATEGY as BKF_STRATEGY, OPAQUE_ENTRY_ASSUMPTION
                if before.st_size>MAX_INPUT:raise ValueError('BKF input budget')
                self.book=parse(self.source.read_bytes())
                self.records=self.cache/'components';self.records.mkdir()
                for entry in self.book.entries:
                    if entry.kind!='UNKNOWN':(self.records/entry.name).write_bytes(entry.decoded)
                result.page_count=len(self.book.pages)
                result.recovery_class='DECODED_ORIGINAL_CONTENT';result.conversion_strategy=BKF_STRATEGY
                result.fidelity='PENDING_NATIVE_PAGE_VALIDATION'
                opaque=[e.evidence() for e in self.book.entries if e.kind=='UNKNOWN']
                result.assumptions=[OPAQUE_ENTRY_ASSUMPTION] if opaque else []
                result.warning=' '.join(result.assumptions)
                result.preservation={'opaque_entries':opaque,'native_pixels_preserved':'PENDING',
                                     'original_djvu_bytes_preserved':'PENDING'}
                result.dimensions={'FORMAT_IDENTIFIED':'BKF'}
                result.recovered_document=from_result(result)
                for page,entry in zip(result.recovered_document['pages'],self.book.pages):page.update(_info_geometry(entry))
            else:
                container=extract_pdf(self.source,self.cache) if self.source.suffix.lower()=='.book' else None
                if container:
                    path,result.preservation=container
                    path.replace(self.pdf)
                    result.recovery_class=RECOVERY_CLASS;result.conversion_strategy=STRATEGY
                    result.fidelity='DECODED_COMPONENT_BYTES_PRESERVED_NO_SYNTHESIS'
                    result.dimensions={'FORMAT_IDENTIFIED':'BKC'}
                elif self.source.suffix.lower()=='.pdf':
                    shutil.copyfile(self.source,self.pdf)
                    from .provenance import embedded_provenance
                    from .state import pdf_provenance
                    prior=embedded_provenance(self.pdf) or pdf_provenance(self.source,result.source_hash)
                    if prior:
                        for field in ('recovery_class','reconstructed_objects','preservation','warning','fidelity',
                                      'missing_data_synthesized','assumptions','affected_pages','preview_policy','recovered_document'):
                            if field in prior:setattr(result,field,copy.deepcopy(prior[field]))
                    result.conversion_strategy='validated-pdf-copy'
                else:raise ValueError('Use established recovery for unrecognized envelope')
                result.page_count=_pdf_structure(self.pdf)
                result.recovered_document=document_layout(self.pdf,from_result(result))
            self.result=result
            self.font_policy='report-fonts' if result.recovery_class==RECOVERY_CLASS else 'strict'
            if not 0<=first_page<result.page_count:raise ValueError('Requested page outside document')
            self.render(first_page,1,1,1200)  # Validate a visible page before readiness.
            result.recovered_document['validation']={'status':'PENDING_FULL_VALIDATION',
                'mode':'interactive','verified_pages':[first_page+1],'full_document_accepted':False}
            result.recovered_document['confidence']='INTERACTIVE_STRUCTURE_AND_REQUESTED_RENDER'
        except (ImportError,ModuleNotFoundError):
            raise
        except Exception as exc:
            if getattr(exc,'code',None) in ('DEPENDENCY_MISSING','RUNTIME_LOAD_FAILED'):raise
            # Existing strict recovery and bounded unknown-family analysis remain
            # authoritative for documents that cannot enter the fast path.
            self.book=None
            if self.pdf.exists():self.pdf.unlink()
            result=convert(self.source,self.pdf,Options(jobs=1,recovery=recovery,validate_all_pages=True),
                           self.cache/'.book2pdf-state.sqlite3',isolated_bkf=False,progress=checkpoint)
            if result.status not in ACCEPTED:
                self.result=result;return
            result.recovered_document=document_layout(self.pdf,result.recovered_document)
            self.result=result
            self.font_policy='report-fonts' if result.recovery_class==RECOVERY_CLASS else 'strict'
        if sha256(self.source)!=result.source_hash:raise ValueError('Source changed while opening')

    def render(self,page,zoom,ratio,max_dimension=4096):
        checkpoint()
        if not 0<=page<self.result.page_count:raise ValueError('Page outside document')
        if self.book:
            from .bkf.pipeline import build_pdf
            path=self.cache/'active-page.pdf'
            if self.last_native_page!=page:
                build_pdf(self.source,path,self.records,selected=[page+1],book=self.book,materialize=False)
                self.last_native_page=page
            image=_checked_render(path,0,zoom,ratio,max_dimension,'strict')
            geometry=self.result.recovered_document['pages'][page]
            if abs(geometry['width']-image['page_width'])>.01 or abs(geometry['height']-image['page_height'])>.01:
                raise ValueError('Native page geometry differs from directory INFO')
            image['page']=page
            return image
        return _checked_render(self.pdf,page,zoom,ratio,max_dimension,self.font_policy)

    def export(self,target,pages,overwrite=False):
        from .export import export_pages
        from .page_ranges import normalize_pages,parse_pages
        target=Path(target)
        if target.resolve()==self.source.resolve() or (target.exists() and target.samefile(self.source)):
            return Result(str(self.source),str(target),error='Cannot overwrite original source')
        selected=parse_pages(pages,self.result.page_count) if isinstance(pages,str) else normalize_pages(pages,self.result.page_count)
        if sha256(self.source)!=self.result.source_hash:raise ValueError('Source changed since opening')
        record=copy.deepcopy(self.result.to_dict())
        with tempfile.TemporaryDirectory(dir=self.cache,prefix='selection-') as temporary:
            path=self.pdf
            local_pages=selected
            if self.book:
                from .bkf.pipeline import build_pdf
                path=Path(temporary)/'selected.pdf'
                record['preservation']=build_pdf(self.source,path,self.records,selected=selected,book=self.book,
                                                 materialize=False,progress=checkpoint)
                record['fidelity']='NATIVE_SCAN_PIXELS_AND_RECOVERED_DJVU_BYTES_PRESERVED'
                record['recovered_document']=selected_model(record['recovered_document'],selected)
                local_pages=list(range(1,len(selected)+1))
            result=export_pages(path,target,local_pages,overwrite,record)
        result.source_path=str(self.source);result.source_hash=self.result.source_hash
        result.source_size=self.result.source_size;result.source_mtime=self.result.source_mtime
        result.preservation['selected_source_pages']=selected
        return result


def prepare(source,cache,recovery,first_page=0):
    global _backend
    _backend=make_backend(source,cache,recovery,first_page=first_page)
    return _backend.result.to_dict()


def render(page,zoom,ratio,max_dimension=4096):return _backend.render(page,zoom,ratio,max_dimension)


def export_prepared(target,pages,overwrite=False):return _backend.export(target,pages,overwrite).to_dict()


def export_selection(source,cache,recovery,target,pages,overwrite=False,expected_hash=None):
    from .page_ranges import parse_pages
    if expected_hash and not is_dataset_source(source) and sha256(Path(source))!=expected_hash:raise ValueError('Source changed since opening')
    requested=parse_pages(pages) if isinstance(pages,str) else pages
    with tempfile.TemporaryDirectory(dir=Path(cache).parent,prefix='selected-backend-') as directory:
        backend=make_backend(source,directory,recovery,first_page=requested[0]-1)
        if expected_hash and backend.result.source_hash!=expected_hash:raise ValueError('Source changed since opening')
        if backend.result.status not in (*ACCEPTED,'INTERACTIVE_READY'):return backend.result.to_dict()
        return backend.export(target,pages,overwrite).to_dict()


def full_conversion(source,target,recovery,expected_hash=None,overwrite=False):
    from .worker import convert
    return convert(Path(source),Path(target),Options(jobs=1,recovery=recovery,overwrite=overwrite,validate_all_pages=True),
        Path(target).parent/'.book2pdf-state.sqlite3',expected_hash,progress=checkpoint,isolated_bkf=False).to_dict()


def is_dataset_source(source):
    path=Path(source)
    return path.is_dir() or path.suffix.lower() in ('.tif','.tiff')


def make_backend(source,cache,recovery,*,first_page=0):
    if is_dataset_source(source):
        from .turbosun.backend import Backend as TurboSunBackend
        return TurboSunBackend(source,cache,recovery,first_page=first_page)
    return Backend(source,cache,recovery,first_page=first_page)


def background_validation(source,target,recovery,expected_hash):
    if is_dataset_source(source):
        from .turbosun.backend import validate_source
        return validate_source(source,expected_hash,Path(target).parent)
    return full_conversion(source,target,recovery,expected_hash)
