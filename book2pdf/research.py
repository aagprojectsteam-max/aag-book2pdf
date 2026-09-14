"""Bounded automatic unknown-file analysis in a disposable spawn worker."""
from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid
from itertools import islice

from .diagnostics import layout_evidence
from .platforms import FileLock, cache_root
from .state import sha256


@dataclass(frozen=True)
class AnalysisLimits:
    input_bytes: int = 64*1024*1024
    output_bytes: int = 32*1024*1024
    candidates: int = 24
    archive_members: int = 64
    recursion_depth: int = 1
    wall_seconds: int = 20
    cpu_seconds: int = 15
    memory_bytes: int = 2*1024*1024*1024
    family_samples: int = 128
    report_bytes: int = 4*1024*1024


LIMITS = AnalysisLimits()


def analysis_root(configured=''):
    root = Path(configured) if configured else cache_root()/('aag-book2pdf-analysis-'+hashlib.sha256(str(Path.home()).encode()).hexdigest()[:12])
    if root.is_symlink():
        raise ValueError('Analysis root cannot be a symbolic link')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root.resolve()


def _regions(data):
    size = len(data)
    points = sorted({int(i*max(0,size-65536)/15) for i in range(16)})
    return [{'offset': p, 'length': len(data[p:p+65536]),
             'entropy': -sum((n/len(data[p:p+65536]))*math.log2(n/len(data[p:p+65536]))
                             for n in Counter(data[p:p+65536]).values())} for p in points if size]


def _child(source, scratch):
    """No GUI, shell execution or recursive extraction; render only in this worker."""
    try:
        from .research_limits import constrain
        constrain(LIMITS.memory_bytes,LIMITS.cpu_seconds)
        path, base = Path(source), Path(scratch)
        size = path.stat().st_size
        with path.open('rb') as stream:
            data = stream.read(LIMITS.input_bytes+1)
        limited = len(data) > LIMITS.input_bytes
        data = data[:LIMITS.input_bytes]
        report = {'schema': 1, 'source_size': size, 'header_hex': data[:128].hex(),
                  'limits': asdict(LIMITS), 'input_truncated_by_budget': limited,
                  'layout': layout_evidence(data), 'entropy_regions': _regions(data),
                  'strategies': [], 'candidate_offsets': [], 'damaged_byte_ranges': [],
                  'references': [], 'candidate_transforms': [], 'candidates': [],
                  'encryption_status': 'UNKNOWN', 'analysis_completed': True}
        family = report['layout']['observed_book_signature']
        report['candidate_family'] = family or report['layout']['classification']
        # Repetition is evidence, never a claimed record/page count.
        sampled = data[:min(len(data),4*1024*1024)]
        counts = Counter(sampled[i:i+8] for i in range(0,len(sampled)-7,8))
        repeated = [token for token,n in counts.most_common(8) if n > 1]
        report['repetition'] = [{'hex': token.hex(), 'count': data.count(token),
            'first_positions': [m.start() for m in islice(re.finditer(re.escape(token),sampled),32)]} for token in repeated]
        report['references'] = [m.group().decode() for m in islice(re.finditer(rb'\b\d+ \d+ R\b',data[:1048576]),128)]
        signatures = {'PDF': b'%PDF-', 'ZIP': b'PK\x03\x04', 'GZIP': b'\x1f\x8b\x08',
                      'XZ': b'\xfd7zXZ\x00', 'BZIP2': b'BZh', 'JPEG': b'\xff\xd8\xff',
                      'PNG': b'\x89PNG\r\n\x1a\n', '7ZIP': b"7z\xbc\xaf'\x1c", 'RAR': b'Rar!'}
        report['signatures'] = {name: [m.start() for m in islice(re.finditer(re.escape(magic), data),32)] for name,magic in signatures.items()}
        from .detector import detect
        try:
            detection = detect(path,allow_salvage=True,preview_policy='opaque') if not limited else None
            if detection:
                if size > LIMITS.output_bytes:
                    raise ValueError('Preview probe would exceed temporary disk budget')
                from .repair import reconstruct
                from .salvage import verify_reconstruction_preview,verify_salvage
                from . import validator
                # No external grandchildren in the disposable research worker.
                # Final publication still uses the normal full validator/qpdf.
                validator.qpdf_executable = lambda: None
                preview_file = base/'preview-check.pdf'
                reconstruct(path,preview_file,detection)
                if detection.preview:
                    _,_,affected = verify_reconstruction_preview(path,preview_file,detection)
                    report['affected_pages'] = affected
                elif detection.damaged:
                    verify_salvage(path,preview_file,detection)
                count,_ = validator.validate(preview_file,True)
                report['preview_page_count'] = count
                preview_file.unlink()
                report['candidate_offsets'].append(detection.shift)
                report['damaged_byte_ranges'] = detection.damaged
                report['object_relationships'] = detection.forensic
                report['strategies'].append({'name': detection.strategy, 'score': 100,
                    'outcome': 'preview_available' if detection.preview else 'known_alternative_policy',
                    'reason': 'Unique xref/object evidence; appearance assumptions require explicit policy'})
                report['preview_available'] = True
        except Exception as exc:
            (base/'preview-check.pdf').unlink(missing_ok=True)
            report['object_relationships'] = getattr(exc,'forensic',{})
            report['strategies'].append({'name': 'xref-object-relationships', 'score': 0, 'outcome': 'rejected', 'reason': str(exc)})
        from .strategies.containers import REGISTRY, decode
        probes = []
        for name,magic,kind in REGISTRY:
            for match in re.finditer(re.escape(magic), data):
                probes.append((match.start(),name,kind))
                if sum(p[2] == kind for p in probes) >= 4:
                    break
        # Include all legal zlib window sizes, not only 0x78.
        for match in re.finditer(rb'[\x08\x18\x28\x38\x48\x58\x68\x78].',data, re.S):
            if int.from_bytes(match.group(),'big') % 31 == 0:
                probes.append((match.start(),'zlib-document','zlib'))
            if len(probes) >= LIMITS.candidates:
                break
        for index,(offset,name,kind) in enumerate(sorted(set(probes))[:LIMITS.candidates]):
            row = {'name': name, 'offset': offset, 'score': 20, 'outcome': 'rejected'}
            try:
                if limited:
                    raise ValueError('Input budget exceeded; decoding skipped')
                decoded = decode(kind,data[offset:])
                row['decoded_size'] = len(decoded)
                if offset != 0:
                    raise ValueError('Decoded member has unexplained prefix; analysis only, no automatic publication')
                if not decoded.startswith(b'%PDF-'):
                    raise ValueError('Decoded payload is not a single direct PDF; recursion disabled')
                target = base/f'candidate-{index}.pdf'
                if sum(p.stat().st_size for p in base.glob('candidate-*.pdf'))+len(decoded) > LIMITS.output_bytes:
                    raise ValueError('Aggregate temporary disk budget exceeded')
                target.write_bytes(decoded)
                detection = detect(target)
                from . import validator
                validator.qpdf_executable = lambda: None
                count,_ = validator.validate(target,True)
                row.update(score=90,outcome='bounded_recovery_candidate',page_count=count,
                    reason='Complete single member, strict known PDF layout and all pages rendered inside bounded worker')
                report['candidates'].append({'file': target.name, 'strategy': name, 'decoded_sha256': sha256(target)})
            except PermissionError as exc:
                row['reason'] = str(exc)
                report['key_required'] = True
                report['encryption_status'] = 'ZIP_ENCRYPTION_FLAG_PRESENT'
            except Exception as exc:
                row['reason'] = str(exc)
            report['candidate_transforms'].append(row)
            report['strategies'].append(row)
        report['strategies'].sort(key=lambda r:-r['score'])
        report['status'] = ('PREVIEW_AVAILABLE_WITH_ASSUMPTIONS' if report.get('preview_available') else
            'KEY_REQUIRED' if report.get('key_required') else 'DECODER_REQUIRED' if family == 'BKF' and not report['candidates'] else
            'NEW_VARIANT_DETECTED' if report['candidates'] or any(report['signatures'].values()) or family else 'UNSUPPORTED_AFTER_ANALYSIS')
        if limited:
            report['status'] = 'UNSUPPORTED_AFTER_ANALYSIS'
        report['next_step'] = ('Select an explicit preview policy; validate all pages and preserve provenance' if report.get('preview_available') else
            'Supply an authorized decoder/key for the verified encrypted container' if report.get('key_required') else
            'Independently validate the complete decoded PDF candidate' if report['candidates'] else
            'Establish the container codec/framing with documented or authorized decoder evidence; do not infer keys from entropy')
        report['unproven'] = ['version fields', 'record boundaries', 'missing appearance values', 'original visual fidelity']
        encoded = json.dumps(report,ensure_ascii=False).encode('utf-8')
        if len(encoded) > LIMITS.report_bytes:
            raise ValueError('Forensic evidence exceeded report budget; recovery refused')
        (base/'analysis.json').write_bytes(encoded)
    except BaseException as exc:
        Path(scratch,'analysis.json').write_text(json.dumps({'status':'UNSUPPORTED_AFTER_ANALYSIS','analysis_completed':False,'error':repr(exc),'limits':asdict(LIMITS)}),encoding='utf-8')


def compare_profiles(profiles):
    """Hypotheses require at least two distinct hashes; no inferred decoder."""
    unique = {p['source_sha256']:p for p in profiles}
    profiles = list(unique.values())
    result = {'sample_count':len(profiles),'constant_header_bytes':[], 'variable_header_offsets':[],
              'size_field_candidates':[], 'common_repeated_metadata':[], 'block_boundaries':'NOT_PROVEN',
              'version_fields':'NOT_PROVEN', 'inference_only':True}
    if len(profiles) < 2:
        return result
    heads = [bytes.fromhex(p.get('header_hex','')) for p in profiles]
    for i in range(min(map(len,heads))):
        values = {h[i] for h in heads}
        if len(values)==1:
            result['constant_header_bytes'].append({'offset':i,'hex':f'{heads[0][i]:02x}'})
        else:
            result['variable_header_offsets'].append(i)
    if len({p['source_size'] for p in profiles}) > 1:
        for width in (2,4,8):
            for i in range(min(map(len,heads))-width+1):
                for endian in ('little','big'):
                    if all(int.from_bytes(h[i:i+width],endian)==p['source_size'] for h,p in zip(heads,profiles)):
                        result['size_field_candidates'].append({'offset':i,'width':width,'endian':endian})
    shared = set.intersection(*(set(r['hex'] for r in p.get('repetition',[])) for p in profiles))
    result['common_repeated_metadata'] = sorted(shared)
    result['block_boundary_candidates'] = {token: [r['first_positions'] for p in profiles for r in p.get('repetition',[]) if r['hex']==token] for token in shared}
    return result


def analyze_file(source, *, root='', keep_candidates=False, initial_error=''):
    source = Path(source).resolve(strict=True)
    digest = sha256(source)
    base = analysis_root(root)
    scratch = Path(tempfile.mkdtemp(prefix='.analysis-work-',dir=base))
    retained = False
    try:
        process = multiprocessing.get_context('spawn').Process(target=_child,args=(str(source),str(scratch)))
        process.start()
        process.join(LIMITS.wall_seconds)
        if process.is_alive():
            process.terminate(); process.join(2)
            if process.is_alive():
                process.kill(); process.join()
        path = scratch/'analysis.json'
        report = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {
            'status':'UNSUPPORTED_AFTER_ANALYSIS','analysis_completed':False,
            'error':'Research worker exceeded CPU/memory/wall budget or exited', 'limits':asdict(LIMITS)}
        report.update(source_sha256=digest,source_size=source.stat().st_size,source_name=source.name,
                      initial_rejection=str(initial_error),source_unchanged=sha256(source)==digest)
        if not report['source_unchanged']:
            report.update(status='UNSUPPORTED_AFTER_ANALYSIS',candidates=[],error='Source changed during analysis')
        with FileLock(base/'.family.lock'):
            history = base/'family-index.json'
            records = json.loads(history.read_text(encoding='utf-8')) if history.exists() else []
            same = [r for r in records if r.get('candidate_family') == report.get('candidate_family')]
            report['family_comparison'] = compare_profiles(same+[report])
            profile = {k:report.get(k) for k in ('source_sha256','source_size','candidate_family','header_hex','repetition')}
            profile['header_hex'] = profile['header_hex'] or ''
            profile['repetition'] = profile['repetition'] or []
            records = [r for r in records if r['source_sha256'] != digest]+[profile]
            stage = base/('.index-'+uuid.uuid4().hex+'.tmp')
            stage.write_text(json.dumps(records[-LIMITS.family_samples:]),encoding='utf-8');os.replace(stage,history)
        package = base/(digest[:16]+'-'+uuid.uuid4().hex[:8])
        package.mkdir(mode=0o700)
        report['package'] = str(package)
        (package/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        (package/'README.txt').write_text('Read-only bounded BOOK analysis. This package contains hypotheses, not proof of decoded content.\n'+report.get('next_step',report.get('error',''))+'\nOriginal source: '+str(source),encoding='utf-8')
        if keep_candidates:
            report['_scratch'] = str(scratch)
            retained = True
        return report
    finally:
        if not retained:
            shutil.rmtree(scratch,ignore_errors=True)


def compare_family(paths, root=''):
    reports = [analyze_file(p,root=root) for p in paths]
    groups = {}
    for report in reports:
        groups.setdefault(report.get('candidate_family','unknown'),[]).append(report)
    return {'files':reports,'families':{name:compare_profiles(items) for name,items in groups.items()}}
