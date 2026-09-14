"""Read-only source comparison with bounded codec probes and validated outputs.

All outputs go in a NEW evidence directory. Source books are opened only for
reading. Byte probes never become recovery strategies or authorize acceptance.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import zlib

from book2pdf.diagnostics import layout_evidence
from book2pdf.detector import detect
from book2pdf.models import Options, Unsupported
from book2pdf.state import sha256
from book2pdf.worker import convert


def probe_opaque(data):
    signatures = {'zip': b'PK\x03\x04', 'gzip': b'\x1f\x8b\x08', 'bzip2': b'BZh',
                  'xz': b'\xfd7zXZ\x00', '7zip': b"7z\xbc\xaf'\x1c", 'rar': b'Rar!',
                  'jpeg': b'\xff\xd8\xff', 'png': b'\x89PNG\r\n\x1a\n'}
    magic = {name: [m.start() for m in re.finditer(re.escape(sig), data)] for name, sig in signatures.items()}
    candidates = [i for i in range(len(data)-1) if data[i] & 15 == 8 and data[i] >> 4 <= 7
                  and ((data[i] << 8) + data[i+1]) % 31 == 0]
    complete, long_prefixes = [], []
    for i in candidates:
        try:
            decoder = zlib.decompressobj()
            block = data[i:i+1048576]
            decoded = decoder.decompress(block, 1048576)
            if decoder.eof:
                complete.append({'offset': i, 'decoded_length': len(decoded),
                                 'consumed': len(block)-len(decoder.unused_data)})
            elif len(decoded) > 1024:
                long_prefixes.append({'offset': i, 'decoded_length': len(decoded)})
        except zlib.error:
            pass
    transforms = {}
    for marker in (b'%PDF-', b'startxref', b'%%EOF', b'/Catalog'):
        transforms[marker.decode()] = {
            'single_byte_xor_keys': [k for k in range(256) if bytes(x ^ k for x in marker) in data],
            'fixed_byte_add_keys': [k for k in range(256) if bytes((x+k) % 256 for x in marker) in data]}
    # Discover repeated octets from the source rather than installing a magic
    # delimiter/offset in the converter. Shifted copies may span record boundaries.
    counts = Counter(data[i:i+8] for i in range(0, len(data)-7, 8))
    repeated = []
    for token, count in counts.most_common(3):
        if count < 2:
            continue
        positions = [m.start() for m in re.finditer(re.escape(token), data)]
        repeated.append({'hex': token.hex(), 'aligned_count': count, 'all_positions': positions})
    return {'magic_positions': magic, 'zlib_header_candidates': len(candidates),
            'complete_zlib_members': complete, 'zlib_prefixes_over_1KiB': long_prefixes,
            'probe_limits': '1 MiB input/output per zlib candidate; negative probes do not rule out other codecs or custom framing',
            'simple_transforms': transforms, 'repeated_octets': repeated,
            'interpretation': 'Repeated opaque byte patterns do not prove records, page boundaries, stream count, cipher or a decoding key.'}


def inspect(source, destination):
    data = source.read_bytes()
    before = sha256(source)
    counts = Counter(data)
    report = {'source': str(source), 'size': len(data), 'sha256': before,
              'header_hex': data[:64].hex(), 'last_64_hex': data[-64:].hex(),
              'entropy_bits_per_byte': -sum((n/len(data))*math.log2(n/len(data)) for n in counts.values()),
              'layout': layout_evidence(data)}
    try:
        detection = detect(source, allow_salvage=True)
        report['detection'] = {'strategy': detection.strategy, 'wrapper_offset': detection.wrapper_offset,
            'body_start': detection.body_start, 'end': detection.end, 'stated_startxref': detection.startxref,
            'offset_shift': detection.shift, 'live_entries': len(detection.entries),
            'damaged_objects': detection.damaged, 'evidence': detection.forensic}
    except Unsupported as exc:
        report['detection'] = {'error': str(exc), 'evidence': getattr(exc, 'forensic', {})}
        report['opaque_probes'] = probe_opaque(data)
        import pikepdf
        import pymupdf
        parser_results = {}
        try:
            with pikepdf.open(source, attempt_recovery=False) as pdf:
                parser_results['libqpdf_strict'] = {'opened': True, 'pages': len(pdf.pages)}
        except Exception as error:
            parser_results['libqpdf_strict'] = {'opened': False, 'error': str(error)}
        try:
            with pymupdf.open(stream=data, filetype='pdf') as pdf:
                parser_results['mupdf'] = {'opened': True, 'pages': len(pdf)}
        except Exception as error:
            parser_results['mupdf'] = {'opened': False, 'error': str(error)}
        report['direct_parser_probes'] = parser_results
    output = destination / (source.stem + '.pdf')
    result = convert(source, output, Options(validate_all_pages=True, debug=True), destination / 'state.sqlite3')
    report['conversion'] = result.to_dict()
    if result.status in ('PASS_EXACT', 'PASS_REPAIRED'):
        import pikepdf
        import pymupdf
        with pikepdf.open(output, attempt_recovery=False) as pdf:
            report['catalog'] = str(pdf.Root)
            report['trailer'] = str(pdf.trailer)
            report['streams'] = [{'object': list(obj.objgen), 'length': len(obj.read_raw_bytes()),
                'sha256': hashlib.sha256(obj.read_raw_bytes()).hexdigest(), 'subtype': str(obj.get('/Subtype')),
                'filter': str(obj.get('/Filter'))} for obj in pdf.objects if isinstance(obj, pikepdf.Stream)]
        with pymupdf.open(output) as pdf:
            report['representative_renders'] = []
            for i in sorted({0, len(pdf)//2, len(pdf)-1}):
                pix = pdf[i].get_pixmap(matrix=pymupdf.Matrix(1,1), alpha=False)
                pix.save(str(destination / f'{source.stem}-page-{i+1}.png'))
                report['representative_renders'].append({'page': i+1, 'pixel_sha256': hashlib.sha256(pix.samples).hexdigest()})
    report['source_unchanged'] = sha256(source) == before
    assert report['source_unchanged']
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources', nargs='+', type=Path)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    reports = [inspect(path.resolve(strict=True), args.evidence) for path in args.sources]
    target = args.evidence / 'comparison.json'
    target.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps([{'source': r['source'], 'status': r['conversion']['status'],
        'page_count': r['conversion']['page_count'], 'unchanged': r['source_unchanged']} for r in reports], indent=2))
    print(target)


if __name__ == '__main__':
    raise SystemExit(main())
