"""One source per process, validated staging, exclusive output lock, atomic publish."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
import tempfile
import time

from .detector import detect
from .models import Options, Result, Unsupported
from .repair import reconstruct
from .state import State, sha256, pdf_provenance
from .validator import ValidationError, validate
from .platforms import FileLock, output_key, publish


def initialize_process():
    import signal
    # Terminal Ctrl+C reaches the whole process group. The coordinator handles it
    # and lets active safe units finish; children must not raise KeyboardInterrupt.
    signal.signal(signal.SIGINT, signal.SIG_IGN)


def _cache_recovery_compatible(requested, previous):
    from . import __version__
    if previous.get('engine_version') != __version__:
        return False
    klass = previous.get('recovery_class', 'STRICT_EXACT_RECOVERY')
    from .recovery.document import GENERIC_CLASSES
    if klass == 'DECODED_CONTAINER_CONTENT':
        from .container_decode import cache_compatible
        return cache_compatible(previous)
    if klass == 'DECODED_IMAGE_CONTENT':
        from .turbosun import DECODER_VERSION,STRATEGY
        provenance=previous.get('recovered_document',{}).get('provenance',{})
        return provenance.get('strategy')==STRATEGY and provenance.get('decoder_version')==DECODER_VERSION
    if klass in GENERIC_CLASSES:
        model=previous.get('recovered_document',{})
        from .recovery.engine import STRATEGY_VERSION
        return (model.get('engine_version')==__version__
                and model.get('provenance',{}).get('strategy')==STRATEGY_VERSION
                and model.get('assumptions',[])==previous.get('assumptions',[])
                and (requested!='exact' or klass=='DECODED_PDF_CONTENT'))
    if klass == 'DECODED_ORIGINAL_CONTENT':
        from .bkf import STRATEGY, DECODER_VERSION, ASSEMBLY_VERSION, OPAQUE_ENTRY_ASSUMPTION
        from .bkf.native import runtime_version
        try:native_version = runtime_version()
        except (ValueError, OSError, AttributeError):return False
        preservation = previous.get('preservation', {})
        return (preservation.get('strategy') == STRATEGY
                and preservation.get('decoder_version') == DECODER_VERSION
                and preservation.get('assembly_version') == ASSEMBLY_VERSION
                and preservation.get('native_decoder') == native_version
                and preservation.get('assumptions') == previous.get('assumptions', [])
                and previous.get('assumptions', []) == ([OPAQUE_ENTRY_ASSUMPTION] if preservation.get('opaque_entries') else []))
    if requested == 'exact':
        return klass == 'STRICT_EXACT_RECOVERY'
    if requested == 'safe-salvage':
        return klass in ('STRICT_EXACT_RECOVERY', 'SAFE_SALVAGE_RECOVERY')
    if requested in ('reconstruction-preview-opaque', 'reconstruction-preview-transparent'):
        if klass in ('STRICT_EXACT_RECOVERY', 'SAFE_SALVAGE_RECOVERY'):
            return True
        return (klass == 'RECONSTRUCTED_PREVIEW'
                and previous.get('preview_policy') == requested.removeprefix('reconstruction-preview-'))
    return False


def convert(source: Path, output: Path, options: Options, state_path: Path, expected_source_hash=None, *, progress=None, isolated_bkf=True, cancel_path=None) -> Result:
    if source.is_dir() or source.suffix.lower() in ('.tif','.tiff'):
        from .turbosun.backend import convert as convert_turbosun
        def turbo_progress(event=None):
            if cancel_path and Path(cancel_path).exists():raise InterruptedError('Conversion cancelled')
            if progress:progress(event)
        return convert_turbosun(source,output,options,state_path,expected_source_hash,turbo_progress if cancel_path else progress)
    start = time.monotonic()
    result = Result(str(source), str(output), timestamp=datetime.now(timezone.utc).isoformat(),
                    validation_mode="all_pages" if options.validate_all_pages else "representative")
    tmp = None
    state = None
    analysis = None
    generic = None
    container = None
    reconstruction_source = source
    try:
        import pikepdf
        import pymupdf
        if source.suffix.lower() not in ('.book', '.pdf') or not source.is_file():
            result.status = "FAIL_INVALID_SOURCE"
            raise ValueError("Source must be a regular .book or .pdf file")
        if output.suffix.lower() != ".pdf":
            raise ValueError("Output must use the .pdf extension")
        if source.resolve() == output.resolve() or (output.exists() and os.path.samefile(source, output)):
            raise ValueError("Source and output must be different files")
        source_stat = source.stat()
        result.source_size = source_stat.st_size
        result.source_mtime = source_stat.st_mtime_ns
        result.source_hash = sha256(source)
        if expected_source_hash and result.source_hash != expected_source_hash:
            raise ValueError('Source changed since opening; reopen the book before saving')
        if source.suffix.lower() == '.pdf':
            prior = pdf_provenance(source, result.source_hash)
            from .provenance import embedded_provenance
            import pikepdf
            try:embedded = embedded_provenance(source)
            except pikepdf.PdfError:embedded = None  # Damaged PDF proceeds to bounded reconstruction.
            if embedded:
                prior = {**(prior or {}), **embedded}
            if prior and prior.get('recovery_class') in ('SAFE_SALVAGE_RECOVERY','RECONSTRUCTED_PREVIEW','DECODED_ORIGINAL_CONTENT','DECODED_CONTAINER_CONTENT','DECODED_PDF_CONTENT','STRUCTURAL_PDF_RECOVERY','PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY','DECODED_IMAGE_CONTENT'):
                for name in ('recovery_class','reconstructed_objects','preservation','warning','fidelity','missing_data_synthesized','assumptions','affected_pages','preview_policy','recovered_document'):
                    if name in prior:
                        setattr(result, name, prior[name])
                result.validation_mode = 'all_pages'
                if result.recovery_class == 'RECONSTRUCTED_PREVIEW' and not _cache_recovery_compatible(options.recovery,prior):
                    result.status = 'PREVIEW_AVAILABLE_WITH_ASSUMPTIONS'
                    result.error = 'PDF contains an assumed preview; select its explicit preview policy'
                    return result
        output.parent.mkdir(parents=True, exist_ok=True)
        # Directory-relative lock remains present so processes cannot lock different
        # inodes after an unlink race. File contains no source content.
        key = output_key(output)
        lock_path = output.parent / f".book2pdf-{key}.lock"
        with FileLock(lock_path):
            if output.is_symlink():
                raise ValueError("Refusing a symbolic-link output")
            # Recover only this output's private staging files while holding its lock.
            prefix = f".book2pdf-{key}-"
            for stale in output.parent.glob(prefix + "*.tmp.pdf"):
                if stale.is_file() and not stale.is_symlink():
                    stale.unlink()
            state = State(state_path)
            previous = state.lookup(source, output)
            if options.skip_existing and not options.overwrite and output.is_file() and previous:
                matches = (previous["source_hash"] == result.source_hash
                           and previous["source_size"] == result.source_size
                           and previous["source_mtime"] == result.source_mtime
                           and previous["output_size"] == output.stat().st_size
                           and previous["output_hash"] == sha256(output)
                           and _cache_recovery_compatible(options.recovery, previous)
                           and (not options.validate_all_pages or previous["validation_mode"] == "all_pages"))
                if matches:
                    for name in ("page_count", "output_size", "output_hash", "detected_pdf_version",
                                 "detected_wrapper_offset", "detected_startxref", "conversion_strategy",
                                 "recovery_class", "reconstructed_objects", "preservation", "validation_mode", "warning",
                                 "fidelity", "missing_data_synthesized", "assumptions", "affected_pages", "preview_policy",
                                 "dimensions", "analysis_package", "recovered_document", "engine_version"):
                        if name in previous:
                            setattr(result, name, previous[name])
                    result.validation_status = "PASS_CACHED_SHA256"
                    result.status = "SKIPPED_EXISTING_VALID"
                    return result
            if output.exists() and not options.overwrite:
                raise FileExistsError("Output exists without matching validated provenance; choose overwrite or another destination")
            preview_policy = {'reconstruction-preview-opaque': 'opaque', 'reconstruction-preview-transparent': 'transparent'}.get(options.recovery)
            from .container_decode import extract_pdf, RECOVERY_CLASS
            from .envelope import EnvelopeError
            try:
                container = extract_pdf(source, output.parent, prefix=prefix) if source.suffix.lower()=='.book' else None
            except EnvelopeError as exc:
                if exc.code == 'PROFILE_NOT_APPLICABLE':
                    container = None  # Bounded generic analysis still applies to other profiles.
                else:
                    result.status = 'LIMIT_EXCEEDED' if exc.code == 'LIMIT_EXCEEDED' else 'NEW_VARIANT_DETECTED'
                    result.error = str(exc)
                    from .research import analyze_file
                    analysis = analyze_file(source,root=options.analysis_dir,keep_candidates=True,initial_error=str(exc))
                    result.analysis_package = analysis['package']
                    result.forensic = {'container_rejection': exc.code, 'detail': str(exc),
                        'fallback_analysis':{k:v for k,v in analysis.items() if not k.startswith('_')}}
                    return result
            if container:
                reconstruction_source, result.preservation = container
                result.recovery_class = RECOVERY_CLASS
                result.fidelity = 'DECODED_COMPONENT_BYTES_PRESERVED_NO_SYNTHESIS'
                result.validation_mode = 'all_pages'
            try:
                if source.suffix.lower()=='.pdf' or container:
                    import pikepdf
                    from .validator import check_tree
                    from .detector import Detection
                    try:
                        with pikepdf.open(reconstruction_source,attempt_recovery=False) as pdf:
                            check_tree(pdf)
                            if pdf.check_pdf_syntax():raise ValueError('PDF syntax repair required')
                        with reconstruction_source.open('rb') as src:header=src.read(8)
                        if not header.startswith(b'%PDF-'):raise ValueError('Missing PDF header')
                        detection=Detection('shared-book-envelope-pdf-v2' if container else 'validated-pdf-copy',header[5:].decode('ascii'),
                            result.preservation['selected_range'][0] if container else 0,
                            result.preservation.get('original_startxref',0) if container else 0,0,reconstruction_source.stat().st_size)
                    except (pikepdf.PdfError,ValueError) as exc:raise Unsupported(str(exc)) from exc
                else:
                    detection = detect(source, allow_salvage=options.recovery != 'exact', preview_policy=preview_policy)
            except Unsupported as initial:
                from .recovery.engine import attempt
                generic=attempt(reconstruction_source,output.parent)
                if generic.get('success'):
                    model=generic['model']
                    if container:
                        model['provenance']['container'] = container[1]
                        model['provenance']['source_sha256'] = result.source_hash
                    if options.recovery=='exact' and model['recovery_level'] not in ('EXACT','DECODED'):
                        result.status='NEW_VARIANT_DETECTED'
                        result.error='Validated structural recovery is available; exact mode forbids synthesis'
                        return result
                    from .detector import Detection
                    reconstruction_source=Path(generic['_path'])
                    with reconstruction_source.open('rb') as recovered_file:
                        version=recovered_file.read(8)[5:].decode('ascii')
                    detection=Detection('universal-'+generic['selected_hypothesis']['kind'],version,
                        model['provenance'].get('origin',0),model['provenance'].get('original_startxref'),0,reconstruction_source.stat().st_size)
                    result.recovered_document=model
                    result.recovery_class={'DECODED':'DECODED_PDF_CONTENT','EXACT':'STRICT_EXACT_RECOVERY',
                        'STRUCTURAL_REPAIR':'STRUCTURAL_PDF_RECOVERY','PAGE_LEVEL_RECOVERY':'PAGE_LEVEL_RECOVERY',
                        'IMAGE_LEVEL_RECOVERY':'IMAGE_LEVEL_RECOVERY'}[model['recovery_level']]
                    result.fidelity=model['confidence']
                    result.assumptions=model['assumptions']
                    result.reconstructed_objects=[{
                        'object':'document structure', 'replacement':description,
                        'uncertainty':'; '.join(model.get('assumptions',[])) or 'Constrained by surviving source objects and validated page graph',
                    } for description in model['provenance'].get('synthesized_structures',[])]
                    result.missing_data_synthesized=bool(result.reconstructed_objects)
                    result.preservation={**model['resources'],'strategy':generic['strategy'],
                        'transformations':model['provenance'].get('transformations',[]),
                        'streams':model['provenance'].get('preserved_streams',[])}
                    result.validation_mode='all_pages'
                    result.forensic={k:v for k,v in generic.items() if not k.startswith('_') and k!='model'}
                    if result.assumptions:
                        result.warning=' '.join(result.assumptions)
                else:
                    if container:
                        result.status = 'DOCUMENT_INVALID'
                        result.error = 'Decoded component remains invalid: ' + str(initial)
                        result.forensic = {k:v for k,v in generic.items() if not k.startswith('_')}
                        return result
                    from .research import analyze_file
                    analysis = analyze_file(source,root=options.analysis_dir,keep_candidates=True,initial_error=str(initial))
                    result.analysis_package = analysis['package']
                    result.forensic = {k:v for k,v in analysis.items() if not k.startswith('_')}
                    candidates = analysis.get('candidates',[])
                    if len(candidates) == 1 and analysis['source_unchanged']:
                        reconstruction_source = Path(analysis['_scratch'])/candidates[0]['file']
                        detection = detect(reconstruction_source)
                        result.conversion_strategy = candidates[0]['strategy']
                        result.validation_mode = 'all_pages'
                    else:
                        result.status = analysis['status']
                        result.error = str(initial)+'; '+analysis.get('next_step',analysis.get('error','Analysis budget exhausted'))
                        result.dimensions = {'FORMAT_IDENTIFIED':analysis.get('candidate_family','UNKNOWN'),
                            'CONTAINER_DECODED':False,'CONTENT_RECOVERED':False,'ENCRYPTION_STATUS':analysis.get('encryption_status','UNKNOWN'),
                            'SOURCE_UNCHANGED':analysis['source_unchanged']}
                        return result
            result.detected_pdf_version = detection.version
            result.detected_wrapper_offset = detection.wrapper_offset
            result.detected_startxref = detection.startxref
            result.conversion_strategy = result.conversion_strategy or detection.strategy
            if options.debug and analysis is None and generic is None:
                result.forensic = detection.forensic
            fd, name = tempfile.mkstemp(prefix=prefix, suffix=".tmp.pdf", dir=output.parent)
            os.close(fd)
            tmp = Path(name)
            from .bkf import STRATEGY as BKF_STRATEGY
            if detection.strategy == BKF_STRATEGY:
                from .bkf.pipeline import reconstruct as reconstruct_bkf
                from .bkf.format import BKFError
                try:
                    if isolated_bkf:
                        result.preservation = reconstruct_bkf(source, tmp)
                    else:
                        # The supervised viewer worker is already a disposable
                        # process. Avoid a nested decoder that could outlive it.
                        from .bkf.pipeline import build_pdf
                        with tempfile.TemporaryDirectory(dir=output.parent, prefix='bkf-validation-') as scratch:
                            result.preservation = build_pdf(source, tmp, Path(scratch), progress=progress)
                except BKFError as exc:
                    result.status = getattr(exc,'code','FAIL_VALIDATION')
                    raise
                result.recovery_class = 'DECODED_ORIGINAL_CONTENT'
                result.fidelity = 'NATIVE_SCAN_PIXELS_AND_RECOVERED_DJVU_BYTES_PRESERVED'
                result.validation_mode = 'all_pages'
                result.reconstructed_objects = [{'object':'BKF-to-PDF container',
                    'replacement':description,'uncertainty':'Native pixels and original DjVu bytes retained'}
                    for description in result.preservation['synthesized_structures']]
                if result.preservation['opaque_entries']:
                    result.warning = ('BKF contains opaque one-byte directory entries; their bytes and positions are retained in provenance. '
                                      'Displayed pages are all FORM:DJVU entries; opaque-entry meaning is unknown.')
                    result.assumptions = result.preservation['assumptions']
            else:
                if detection.strategy in ('shared-book-envelope-pdf-v2', 'validated-pdf-copy'):
                    import shutil
                    shutil.copyfile(reconstruction_source, tmp)
                else:
                    reconstruct(reconstruction_source, tmp, detection)
            if detection.damaged:
                if detection.preview:
                    from .salvage import verify_reconstruction_preview
                    result.reconstructed_objects, result.preservation, result.affected_pages = verify_reconstruction_preview(source, tmp, detection)
                    result.recovery_class = 'RECONSTRUCTED_PREVIEW'
                    result.preview_policy = detection.preview_policy
                    result.fidelity = 'UNKNOWN_OR_ASSUMED_APPEARANCE'
                    result.missing_data_synthesized = True
                    result.assumptions = [f"Missing SMask value synthesized using explicit {detection.preview_policy} preview policy"]
                else:
                    from .salvage import verify_salvage
                    result.reconstructed_objects, result.preservation = verify_salvage(source, tmp, detection)
                    result.recovery_class = 'SAFE_SALVAGE_RECOVERY'
                    result.fidelity = 'SURVIVING_BYTES_PRESERVED_LOST_GRAPHICS_STATE_ASSUMED'
                    result.missing_data_synthesized = True
                    result.affected_pages = sorted({p for obj in result.reconstructed_objects for p in obj.get('pages',[])})
                    result.assumptions = ['Lost ExtGState opacity/blend values replaced with documented defaults']
                result.validation_mode = 'all_pages'
            result.conversion_seconds = time.monotonic() - start
            validation_start = time.monotonic()
            validation_options = {'progress':progress} if progress else {}
            if result.recovery_class == RECOVERY_CLASS:validation_options['font_policy']='report-fonts'
            result.page_count, validation_warning = validate(tmp, result.validation_mode == 'all_pages', **validation_options)
            result.warning = (result.warning + ' ' + validation_warning).strip()
            if detection.damaged:
                if detection.preview:
                    result.warning = ('RECONSTRUCTED_PREVIEW: missing appearance-affecting SMask data was synthesized by explicit user policy; '
                                      'the original appearance is NOT proven. Surviving original bytes are audited separately. ' + result.warning)
                else:
                    result.warning = ('Original graphics-state values are not exactly recoverable. '
                                      'Explicit opaque/Normal defaults used; surviving original content/image bytes are preserved, '
                                      'but exact original appearance is not proven. ' + result.warning)
            result.validation_seconds = time.monotonic() - validation_start
            if result.recovery_class == 'RECONSTRUCTED_PREVIEW':
                from .provenance import stamp_preview
                stamp_preview(tmp,result)
                result.page_count,_ = validate(tmp,True, **({'progress':progress} if progress else {}))
            elif result.recovery_class == 'DECODED_ORIGINAL_CONTENT':
                from .provenance import stamp_decoded
                stamp_decoded(tmp,result)
                result.page_count,_ = validate(tmp,True, **({'progress':progress} if progress else {}))
            from .recovery.document import GENERIC_CLASSES
            if result.recovery_class in GENERIC_CLASSES and result.recovery_class != RECOVERY_CLASS:
                from .provenance import stamp_recovered
                stamp_recovered(tmp,result)
                result.page_count,_ = validate(tmp,True, **({'progress':progress} if progress else {}))
            current = source.stat()
            if ((current.st_size, current.st_mtime_ns) != (source_stat.st_size, source_stat.st_mtime_ns)
                    or sha256(source) != result.source_hash):
                raise ValueError("Source changed during conversion; refusing output")
            result.output_size = tmp.stat().st_size
            result.output_hash = sha256(tmp)
            publish(tmp, output, options.overwrite)
            tmp = None
            result.validation_status = "PASS"
            result.status = ('RECONSTRUCTED_PREVIEW' if result.recovery_class == 'RECONSTRUCTED_PREVIEW'
                             else 'PASS_REPAIRED' if result.recovery_class in ('SAFE_SALVAGE_RECOVERY','STRUCTURAL_PDF_RECOVERY','PAGE_LEVEL_RECOVERY','IMAGE_LEVEL_RECOVERY') else 'PASS_DECODED' if reconstruction_source != source or result.recovery_class in ('DECODED_ORIGINAL_CONTENT','DECODED_CONTAINER_CONTENT','DECODED_PDF_CONTENT','DECODED_IMAGE_CONTENT') else 'PASS_EXACT')
            result.elapsed_seconds = time.monotonic() - start
            with source.open('rb') as prefix_file:
                source_prefix = prefix_file.read(3).decode('ascii',errors='replace')
            result.dimensions = {'FORMAT_IDENTIFIED':result.conversion_strategy if reconstruction_source != source else source_prefix if source.suffix.lower()=='.book' else 'PDF',
                'CONTAINER_DECODED':True,'DOCUMENT_PARSEABLE':True,'PAGES_ENUMERABLE':result.page_count,
                'PAGES_RENDERED':result.page_count if result.validation_mode=='all_pages' else 'representative',
                'SURVIVING_BYTES_PRESERVED':True,'MISSING_DATA_SYNTHESIZED':result.missing_data_synthesized,
                'ASSUMPTIONS':result.assumptions,'AFFECTED_PAGES':result.affected_pages,
                'FIDELITY_TO_AUTHORIZED_ORIGINAL':'NOT_PROVEN','SOURCE_UNCHANGED':True}
            from .recovery.document import from_result
            result.recovered_document=from_result(result)
            state.save(result)
    except Unsupported as exc:
        result.error = str(exc)
        if source.suffix.lower()=='.book' and analysis is None:
            from .research import analyze_file
            analysis = analyze_file(source,root=options.analysis_dir,keep_candidates=True,initial_error=str(exc))
            result.status = analysis['status']
            result.analysis_package = analysis['package']
            result.forensic = {k:v for k,v in analysis.items() if not k.startswith('_')}
        else:
            result.status = 'UNSUPPORTED_AFTER_ANALYSIS' if analysis else 'FAIL_VALIDATION'
    except ModuleNotFoundError as exc:
        result.status = 'DEPENDENCY_MISSING'
        result.error = str(exc)
    except ImportError as exc:
        result.status = 'RUNTIME_LOAD_FAILED'
        result.error = str(exc)
    except ValidationError as exc:
        result.status = "FAIL_VALIDATION"
        result.validation_status = "FAIL"
        result.error = str(exc)
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        if result.status in ("PASS_EXACT", "PASS_REPAIRED", "RECONSTRUCTED_PREVIEW", "PASS_DECODED"):
            result.status = "FAIL_REPAIR"
            result.warning = "Validated PDF published, but durable state commit failed; file is protected on restart"
    finally:
        if container:
            container[0].unlink(missing_ok=True)
        if generic and generic.get('_scratch'):
            import shutil
            if result.analysis_package:
                try:
                    destination=Path(result.analysis_package)/'generic-recovery'
                    shutil.copytree(generic['_scratch'],destination,ignore=shutil.ignore_patterns('*.pdf'))
                except OSError:
                    result.warning += ' Generic evidence copy failed.'
            shutil.rmtree(generic['_scratch'],ignore_errors=True)
        if analysis and result.analysis_package:
            import json
            package = Path(result.analysis_package)
            # Evidence is separate from document output; no source or PDF writes.
            final = {k:v for k,v in analysis.items() if not k.startswith('_')}
            final['recovery_attempt'] = {'status':result.status,'error':result.error,'page_count':result.page_count,
                                         'validation_status':result.validation_status}
            try:
                (package/'report.json').write_text(json.dumps(final,ensure_ascii=False,indent=2),encoding='utf-8')
            except OSError:
                result.warning += ' Final forensic report update failed; initial package remains available.'
        if analysis and analysis.get('_scratch'):
            import shutil
            shutil.rmtree(analysis['_scratch'],ignore_errors=True)
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        if state is not None:
            state.close()
        result.elapsed_seconds = round(time.monotonic() - start, 4)
    return result
