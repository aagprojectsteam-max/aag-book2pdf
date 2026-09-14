"""TurboSun adapter to the shared viewer, export and supervised worker contract."""
import copy
import hashlib
from pathlib import Path
import tempfile
import time
from . import DECODER_VERSION,STRATEGY
from .source import Dataset
from .pdf import Writer,tiff_info,validate_export
from ..models import Result
from ..recovery.document import RecoveredDocument,selected_model
from ..supervised import checkpoint
from ..atomic import staged_output
from ..state import sha256,State


class Backend:
    def __init__(self,source,cache,recovery=None,*,first_page=0):
        self.dataset=Dataset(source);self.source=self.dataset.root;self.cache=Path(cache)
        if self.cache.resolve().is_relative_to(self.source.resolve()):raise ValueError('Cache must be outside source dataset')
        self.cache.mkdir(parents=True,exist_ok=True)
        self.last_page=None;self.pdf=self.cache/'active-page.pdf'
        self.result=Result(str(self.source),str(self.pdf),source_hash=self.dataset.identity,
            page_count=len(self.dataset.pages),status='INTERACTIVE_READY',validation_status='PENDING_FULL_VALIDATION',
            recovery_class='DECODED_IMAGE_CONTENT',conversion_strategy=STRATEGY,fidelity='ORIGINAL_TIFF_AND_CCITT_BYTES',
            warning='אוסף כרכים לפי הקטלוג; עמודים ללא תווית עץ מוצגים בנפרד גם ברשימת העמודים הנוספים.',
            dimensions={'FORMAT_IDENTIFIED':'TURBOSUN'})
        self.result.recovered_document=RecoveredDocument('TURBOSUN','DECODED',copy.deepcopy(self.dataset.pages),
            metadata={'supplemental_pages':self.dataset.get_unplaced_assets(),'catalogue_warnings':self.dataset.warnings,
                      'collection_not_single_book':True,'dataset_ready_seconds':self.dataset.ready_seconds},
            provenance={'source_sha256':self.dataset.identity,'strategy':STRATEGY,'decoder_version':DECODER_VERSION,
                        'ordering':'catalogue group, F902, local TF_F001_F1; supplemental local positions proven'},
            validation={'status':'PENDING_FULL_VALIDATION','mode':'on_demand'}).to_dict()
        self.render(first_page,1,1,1200)
    def render(self,page,zoom,ratio,max_dimension=4096):
        from ..viewer import render_page
        if self.last_page!=page:
            raw=self.dataset.decode_page(page);_,info=tiff_info(raw)
            with self.pdf.open('wb') as f:
                writer=Writer(f);writer.page(raw,info);writer.finish({})
            self.last_page=page
        result=render_page(self.pdf,0,zoom,ratio,max_dimension);result['page']=page
        import pymupdf
        pymupdf.TOOLS.store_shrink(100)
        return result
    def export(self,target,pages,overwrite=False,progress=checkpoint):
        from .progress import throttled
        progress=throttled(progress)
        from ..page_ranges import parse_pages,normalize_pages
        selected=parse_pages(pages,len(self.dataset.pages)) if isinstance(pages,str) else normalize_pages(pages,len(self.dataset.pages))
        target=Path(target).absolute()
        if target.resolve().is_relative_to(self.source.resolve()):raise ValueError('Cannot write inside source dataset')
        if not self.dataset.unchanged():raise ValueError('Dataset changed since opening')
        result=copy.deepcopy(self.result);result.output_path=str(target);result.page_count=len(selected)
        result.fidelity='ORIGINAL_CCITT_STREAMS_PRESERVED'
        result.recovered_document=selected_model(result.recovered_document,selected)
        expected=[]
        with staged_output(target,self.source,overwrite) as temp:
            with temp.open('wb') as f:
                writer=Writer(f)
                for n,p in enumerate(selected):
                    progress({'phase':'decode','done':n,'total':len(selected)})
                    raw=self.dataset.decode_page(p-1);_,info=tiff_info(raw)
                    writer.page(raw,info)
                    if n in (0,len(selected)//2,len(selected)-1):
                        from PIL import Image
                        import io
                        with Image.open(io.BytesIO(raw)) as image:
                            info['pixel_sha256']=hashlib.sha256(image.convert('L').tobytes()).hexdigest()
                    expected.append(info)
                    result.recovered_document['pages'][n].update(tiff_sha256=hashlib.sha256(raw).hexdigest(),decode_status='DECODED_VALIDATED')
                result.validation_status='PASS';result.status='PASS_DECODED'
                result.recovered_document['validation']={'status':'PASS','mode':'all_selected_pages'}
                writer.finish({name:getattr(result,name) for name in ('recovery_class','fidelity','warning','assumptions','recovered_document','missing_data_synthesized','reconstructed_objects')})
            result.recovered_document['validation']=validate_export(temp,expected,progress)
            if not self.dataset.unchanged():raise ValueError('Dataset changed during export')
            result.output_hash=sha256(temp);result.output_size=temp.stat().st_size
            progress({'phase':'publication','done':len(selected),'total':len(selected)})
        result.preservation={'selected_source_pages':selected,'page_streams_byte_identical':True,'original_ccitt_streams_preserved':sum(len(e['strips']) for e in expected)}
        return result


def convert(source,target,options,state_path,expected_hash=None,progress=None):
    result=Result(str(source),str(target));started=time.monotonic();progress=progress or checkpoint
    try:
        target=Path(target)
        from .source import resolve_root
        if target.resolve().is_relative_to(resolve_root(source).resolve()):raise ValueError('Cannot write inside source dataset')
        target.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='turbosun-export-') as cache:
            backend=Backend(source,cache)
            if expected_hash and backend.result.source_hash!=expected_hash:raise ValueError('Dataset changed since opening')
            with_state=State(Path(state_path))
            try:
                old=with_state.lookup(str(backend.source),str(target))
                if options.skip_existing and target.is_file() and old and old['source_hash']==backend.result.source_hash and old['engine_version']==result.engine_version and sha256(target)==old['output_hash']:
                    result=Result(**old);result.status='SKIPPED_EXISTING_VALID';return result
                result=backend.export(target,list(range(1,backend.result.page_count+1)),options.overwrite,progress)
                with_state.save(result)
            finally:with_state.close()
    except Exception as exc:
        result.status=getattr(exc,'code','CANCELLED' if isinstance(exc,InterruptedError) else 'FAIL_VALIDATION');result.error=str(exc)
    result.elapsed_seconds=time.monotonic()-started
    return result


def validate_source(source,expected_hash,cache_parent=None):
    with tempfile.TemporaryDirectory(prefix='turbosun-validation-',dir=cache_parent) as cache:
        backend=Backend(source,cache)
        if backend.dataset.identity!=expected_hash:raise ValueError('Dataset changed since opening')
        from .progress import throttled
        progress=throttled(checkpoint)
        for i in range(backend.result.page_count):
            progress({'phase':'render_validation','done':i,'total':backend.result.page_count})
            # Validate exact AES round-trip, all TIFF extents and an actual PDF
            # rendering of every CCITT page, without retaining decoded rasters.
            backend.render(i,.3,1,300)
            backend.result.recovered_document['pages'][i]['decode_status']='DECODED_VALIDATED'
        if not backend.dataset.unchanged():raise ValueError('Dataset changed during validation')
        result=backend.result
        result.status='PASS_DECODED';result.validation_status='PASS';result.validation_mode='all_pages'
        result.recovered_document['validation']={'status':'PASS','mode':'all_pages','full_document_accepted':True}
        return result.to_dict()
