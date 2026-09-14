"""Direct PDF page copying, with byte-level content/image preservation checks."""
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time

from .atomic import staged_output
from .models import Result, Options
from .page_ranges import parse_pages, normalize_pages
from .state import sha256
from .validator import validate, ValidationError


def page_fingerprint(page):
    """Content streams + all reachable resource streams, independent of object IDs."""
    import pikepdf
    seen = set()
    streams = []
    def visit(obj):
        if not isinstance(obj, pikepdf.Object):
            return
        if obj.is_indirect:
            if obj.objgen in seen:
                return
            seen.add(obj.objgen)
        if isinstance(obj, pikepdf.Stream):
            streams.append(hashlib.sha256(obj.read_raw_bytes()).hexdigest())
            for key, value in obj.items():
                if key not in ('/Length', '/Parent'):
                    visit(value)
        elif isinstance(obj, pikepdf.Dictionary):
            for key, value in obj.items():
                if key != '/Parent':
                    visit(value)
        elif isinstance(obj, pikepdf.Array):
            for value in obj:
                visit(value)
    visit(page.obj.get('/Contents'))
    visit(page.Resources)
    visit(page.obj.get('/AF'))  # Retained original DjVu bytes travel with selected pages.
    return {'stream_hashes': sorted(streams), 'mediabox': list(map(float, page.mediabox)),
            'rotate': int(page.obj.get('/Rotate', 0))}


def export_pages(source: Path, target: Path, pages, overwrite=False, recovery_record=None):
    import pikepdf
    start = time.monotonic()
    result = Result(str(source), str(target), status='FAIL_REPAIR', timestamp=datetime.now(timezone.utc).isoformat(),
                    conversion_strategy='direct-page-object-copy', validation_mode='all_pages')
    try:
        before = source.stat()
        result.source_size = before.st_size
        result.source_mtime = before.st_mtime_ns
        result.source_hash = sha256(source)
        from .provenance import embedded_provenance, stamp_preview
        embedded = embedded_provenance(source)
        if embedded:
            recovery_record = {**(recovery_record or {}), **embedded}
        if recovery_record:
            result.recovery_class = recovery_record['recovery_class']
            result.recovered_document = recovery_record.get('recovered_document',{})
            result.reconstructed_objects = recovery_record.get('reconstructed_objects',[])
            result.warning = recovery_record.get('warning', '')
            result.fidelity = recovery_record.get('fidelity', 'UNASSESSED')
            result.missing_data_synthesized = recovery_record.get('missing_data_synthesized', False)
            result.assumptions = recovery_record.get('assumptions', [])
            result.affected_pages = recovery_record.get('affected_pages', [])
            result.preview_policy = recovery_record.get('preview_policy', '')
            if result.recovery_class == 'DECODED_ORIGINAL_CONTENT':
                import copy
                result.preservation = copy.deepcopy(recovery_record.get('preservation', {}))
        with pikepdf.open(source, attempt_recovery=False) as pdf:
            if pdf.is_encrypted:
                raise ValueError('Encrypted source is not supported')
            selected = parse_pages(pages, len(pdf.pages)) if isinstance(pages, str) else normalize_pages(pages, len(pdf.pages))
            fingerprints = [page_fingerprint(pdf.pages[p - 1]) for p in selected]
            with staged_output(target, source, overwrite) as tmp:
                with pikepdf.Pdf.new() as extracted:
                    extracted.pages.extend(pdf.pages[p - 1] for p in selected)
                    extracted.save(tmp, compress_streams=False, stream_decode_level=pikepdf.StreamDecodeLevel.none,
                                   object_stream_mode=pikepdf.ObjectStreamMode.preserve, recompress_flate=False)
                result.conversion_seconds = time.monotonic() - start
                validation_start = time.monotonic()
                if result.recovery_class == 'RECONSTRUCTED_PREVIEW':
                    result.affected_pages = [i+1 for i,p in enumerate(selected) if p in result.affected_pages]
                    stamp_preview(tmp,result)
                elif result.recovery_class == 'DECODED_ORIGINAL_CONTENT':
                    from .provenance import stamp_decoded
                    records = result.preservation.get('pages', [])
                    if len(records) != len(pdf.pages):
                        raise ValidationError('Decoded provenance page count differs from source')
                    result.preservation['pages'] = [records[p-1] for p in selected]
                    result.preservation['page_count'] = len(selected)
                    stamp_decoded(tmp,result)
                from .recovery.document import GENERIC_CLASSES,selected_model
                if result.recovery_class in GENERIC_CLASSES:
                    from .provenance import stamp_recovered
                    result.recovered_document=selected_model(result.recovered_document,selected)
                    # Private staging only: publication below requires this exact
                    # selection to pass every check after the provenance stamp.
                    result.recovered_document['validation'].update(status='PASS',mode='all_selected_pages')
                    result.fidelity='PAGE_STREAMS_PRESERVED'
                    stamp_recovered(tmp,result)
                elif result.recovered_document:
                    result.recovered_document=selected_model(result.recovered_document,selected)
                result.page_count, warning = validate(tmp, True, **({'font_policy':'report-fonts'} if result.recovery_class == 'DECODED_CONTAINER_CONTENT' else {}))
                with pikepdf.open(tmp, attempt_recovery=False) as check:
                    if [page_fingerprint(p) for p in check.pages] != fingerprints:
                        raise ValidationError('Export changed page contents, resources or dimensions')
                if result.page_count != len(selected):
                    raise ValidationError('Export page count mismatch')
                if sha256(source) != result.source_hash:
                    raise ValueError('Source changed during page export')
                result.validation_seconds = time.monotonic() - validation_start
                result.output_hash = sha256(tmp)
                result.output_size = tmp.stat().st_size
                result.preservation.update({'selected_source_pages': selected, 'page_streams_byte_identical': True,
                                       'fingerprints': fingerprints})
                result.warning += (' ' + warning if warning else '')
        result.status = ('RECONSTRUCTED_PREVIEW' if result.recovery_class == 'RECONSTRUCTED_PREVIEW'
                         else 'PASS_REPAIRED' if result.recovery_class in ('SAFE_SALVAGE_RECOVERY','STRUCTURAL_PDF_RECOVERY','PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY') else 'PASS_DECODED' if result.recovery_class in ('DECODED_ORIGINAL_CONTENT','DECODED_CONTAINER_CONTENT','DECODED_PDF_CONTENT','DECODED_IMAGE_CONTENT') else 'PASS_EXACT')
        result.validation_status = 'PASS'
        from .recovery.document import from_result
        result.recovered_document=from_result(result)
    except ValidationError as exc:
        result.status = 'FAIL_VALIDATION'
        result.validation_status = 'FAIL'
        result.error = str(exc)
    except Exception as exc:
        result.error = str(exc)
    result.elapsed_seconds = time.monotonic() - start
    return result


def convert_selected(source: Path, target: Path, specification: str, options=Options()):
    from .viewer import ViewingCache
    from .supervised import Session
    from .interactive import prepare, export_prepared, ACCEPTED
    parse_pages(specification)  # Reject syntax errors before recovery work.
    cache = ViewingCache()
    try:
        with Session() as session:
            prepared=session.call(prepare,source.resolve(),cache.path,options.recovery,parse_pages(specification)[0]-1)
            if prepared['status'] not in (*ACCEPTED,'INTERACTIVE_READY'):
                return Result(**prepared)
            selected=parse_pages(specification,prepared['page_count'])
            return Result(**session.call(export_prepared,target,selected,options.overwrite))
    finally:
        cache.close()
