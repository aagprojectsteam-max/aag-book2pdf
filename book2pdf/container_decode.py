"""Extract exactly one structurally selected PDF before any salvage is tried."""
from pathlib import Path
import mmap
import re
import tempfile
from .envelope import (parse_directory, decode_record, classify_prefix, digest,
                       transform, EnvelopeError, PROFILE, PROFILE_VERSION)

STRATEGY = 'shared-book-envelope-pdf-v2'
RECOVERY_CLASS = 'DECODED_CONTAINER_CONTENT'


def extract_pdf(source, destination, *, prefix='.book2pdf-decoded-'):
    with Path(source).open('rb') as stream:
        if stream.read(3) not in (b'BKC', b'BKF'):
            return None
        stream.seek(0)
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
            envelope = parse_directory(data)
            # BKF pages/resources are assembled by the existing DjVu path, which
            # uses this same directory parser and feedback transform.
            if envelope.outer[:3] == b'BKF':
                return None
            ranges = sorted(envelope.unique_ranges().items())
            cursor = envelope.base
            components = []
            gaps = []
            for (relative, length), aliases in ranges:
                start = envelope.base + relative
                end = start + length
                if start < cursor:
                    raise EnvelopeError('OVERLAPPING_COMPONENTS_AMBIGUOUS', 'Distinct source ranges overlap')
                if end > len(data):
                    raise EnvelopeError('DOCUMENT_INVALID', 'Component extends beyond source')
                if start > cursor:
                    gaps.append({'start': cursor, 'end': start, 'sha256': digest(data[cursor:start])})
                raw = data[start:end]
                components.append({'start': start, 'length': length, 'aliases_hex': aliases,
                                   'kind': classify_prefix(raw), 'encoded_sha256': digest(raw)})
                cursor = end
            if cursor < len(data):
                gaps.append({'start': cursor, 'end': len(data), 'sha256': digest(data[cursor:])})
            documents = [row for row in components if row['kind'] in ('PDF', 'DJVU')]
            if len(documents) != 1 or documents[0]['kind'] != 'PDF':
                code = 'MULTIPLE_PDF_COMPONENTS_AMBIGUOUS' if len(documents) > 1 else 'NO_UNIQUE_PDF_COMPONENT'
                raise EnvelopeError(code, 'No unique PDF document; components=' + repr(components))
            selected = documents[0]
            start, length = selected['start'], selected['length']
            raw = data[start:start+length]
            decoded = decode_record(raw)
            n = min(200, length)
            if transform(decoded[:n], encode=True) != raw[:n]:
                raise EnvelopeError('DOCUMENT_INVALID', 'Component round-trip failed')
            starts = list(re.finditer(rb'startxref\s+(\d+)\s+%%EOF', decoded[-4096:]))
            evidence = {'original_startxref':int(starts[-1][1]) if starts else None, 'strategy': STRATEGY, 'profile': PROFILE, 'profile_version': PROFILE_VERSION,
                        'outer_hex': envelope.outer.hex(), 'directory_header_hex': envelope.directory_header.hex(),
                        'encoded_directory_hex': envelope.directory_raw.hex(), 'origin': envelope.base,
                        'components': components, 'gaps': gaps, 'selected_range': [start, start+length],
                        'decoded_component_sha256': digest(decoded), 'decoded_component_length': length,
                        'passthrough_sha256': digest(raw[n:]), 'passthrough_bytes': length-n,
                        'transform_prefix_bytes': n, 'round_trip': True,
                        'selection_basis': 'Unique PDF by decoded content; exact range aliases grouped',
                        'document_relation': 'FULL_DECODED_COMPONENT', 'full_component_bytes_preserved': True}
    fd, name = tempfile.mkstemp(prefix=prefix, suffix='.tmp.pdf', dir=destination)
    import os
    with os.fdopen(fd, 'wb') as target:
        target.write(decoded)
    return Path(name), evidence


def cache_compatible(previous):
    evidence = previous.get('preservation', {})
    return (evidence.get('strategy') == STRATEGY and evidence.get('profile') == PROFILE
            and evidence.get('profile_version') == PROFILE_VERSION
            and evidence.get('round_trip') is True
            and evidence.get('document_relation') == 'FULL_DECODED_COMPONENT'
            and evidence.get('full_component_bytes_preserved') is True
            and previous.get('output_hash') == evidence.get('decoded_component_sha256')
            and not previous.get('reconstructed_objects') and not previous.get('assumptions'))
