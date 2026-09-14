"""Evidence-driven strategies for classic xrefs and intact embedded xref streams."""
import mmap
import re
from dataclasses import dataclass, field
from pathlib import Path

from .models import Unsupported
from .parser import OBJECT, classic_xref, matches_object
from .diagnostics import layout_evidence, rejection
from .strategies.image_smask import _entry_end, preview_image_smask

HEADER = re.compile(rb"%PDF-(1\.[0-7]|2\.0)")
START = re.compile(rb"startxref\s+(\d+)\s+%%EOF")
XREF = re.compile(rb"(?m)^xref[\r\n ]")


@dataclass
class Detection:
    strategy: str
    version: str
    wrapper_offset: int
    startxref: int
    body_start: int
    end: int
    patches: list = field(default_factory=list)
    header: bytes = b""
    forensic: dict = field(default_factory=dict)
    damaged: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    shift: int = 0
    preview: bool = False
    preview_policy: str = ""


def bounded_matches(pattern, data, limit=4096):
    result = []
    for match in pattern.finditer(data):
        if len(result) >= limit:
            raise Unsupported("Too many structural candidates; safety limit reached")
        result.append(match)
    return result


def detect(path: Path, allow_salvage=False, preview_policy=None) -> Detection:
    if path.stat().st_size < 32:
        raise Unsupported("File is too short to contain a PDF")
    with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as data:
        if data[:3] == b'BKF':
            from .bkf.format import parse, BKFError
            from .bkf import STRATEGY
            try:
                book = parse(data)
            except BKFError:
                pass  # A signature alone never overrides another positive strategy.
            else:
                return Detection(STRATEGY, '2.0', book.base, 0, book.base, len(data),
                    forensic={'family':'BKF','directory_entries':len(book.entries),
                              'page_entries':len(book.pages),
                              'opaque_entries':[e.evidence() for e in book.entries if e.kind=='UNKNOWN']})
        headers = bounded_matches(HEADER, data)
        starts = bounded_matches(START, data)
        xrefs = bounded_matches(XREF, data)
        forensic = {
            "file_size": len(data), "header_hex": data[:24].hex(),
            "pdf_positions": [m.start() for m in headers],
            "xref_positions": [m.start() for m in xrefs],
            "startxref_candidates": [int(m[1]) for m in starts],
            "eof_positions": [m.end() - 5 for m in starts], "hypotheses": [],
        }
        if not starts:
            forensic['layout'] = layout_evidence(data)
            layout = forensic['layout']
            if layout['classification'] == 'OPAQUE_HIGH_ENTROPY_NO_PDF_STRUCTURE':
                signature = layout['observed_book_signature'] or layout['signature_hex']
                raise rejection(f'Opaque container ({signature}): no visible PDF objects, xref or EOF. '
                                'Encoding/compression/encryption is undetermined; no verified decoder is available.', forensic)
            raise rejection('No complete startxref / EOF pair; no verified PDF layout (truncation is only one possible cause)', forensic)
        if len(starts) != 1:
            raise rejection("Multiple documents or revisions are ambiguous", forensic)
        last = starts[-1]
        if data[last.end():].strip(b"\x00\t\r\n \x0c"):
            raise rejection("Non-whitespace data after final EOF", forensic)
        stated = int(last[1])
        candidates = []
        for xm in xrefs:
            x = xm.start()
            if x > last.start():
                continue
            try:
                entries, trailer = classic_xref(data, x)
                tail = data[trailer:last.start()]
                if len(tail) > 1_000_000 or re.search(rb"/(Prev|XRefStm)\b", tail):
                    raise Unsupported("Incremental or hybrid xrefs require a future strategy")
                if not re.search(rb"/Root\s+\d+\s+\d+\s+R\b", tail):
                    raise Unsupported("Missing unambiguous catalog reference")
                shifts = {0, x - stated, *(h.start() for h in headers)}
                # Allows a damaged startxref if live object positions prove one shift.
                anchor = entries[0]
                for obj in bounded_matches(OBJECT, data, 100_000):
                    if int(obj[1]) == anchor.number and int(obj[2]) == anchor.generation:
                        shifts.add(obj.start() - anchor.offset)
                valid = []
                salvage = []
                for shift in sorted(shifts):
                    damaged = [e for e in entries if not matches_object(data, e.offset + shift, e)]
                    ok = not damaged
                    forensic["hypotheses"].append({"xref": x, "shift": shift, "valid": ok})
                    if ok and all(e.offset + shift < x for e in entries):
                        valid.append(shift)
                    elif ((allow_salvage or preview_policy) and shift == x - stated and len(entries) >= 8
                          and 1 <= len(damaged) <= min(4, len(entries) * .05)
                          and all(0 <= e.offset + shift < x for e in entries)):
                        intact_start = min(e.offset + shift for e in entries if e not in damaged)
                        # Legacy safe salvage remains deliberately narrow.  Preview mode
                        # may inspect a large partially surviving object without treating
                        # the entire object size as the damaged-byte span.
                        candidate = (preview_image_smask(data, entries, shift, x, damaged, preview_policy)
                                     if preview_policy else None)
                        if candidate:
                            salvage.append((shift, damaged, intact_start, candidate))
                        elif allow_salvage and all(0 < intact_start - (e.offset + shift) <= 4096 for e in damaged):
                            salvage.append((shift, damaged, intact_start, None))
                damaged = []
                preview_candidate = None
                if not valid and len(salvage) == 1:
                    shift, damaged, intact_start, preview_candidate = salvage[0]
                    valid = [shift]
                if len(valid) != 1:
                    raise Unsupported("Xref offsets do not prove exactly one consistent shift")
                shift = valid[0]
                first = min(e.offset + shift for e in entries)
                # A second complete PDF or a stale revision is never silently selected.
                if any(m.start() < first for m in starts):
                    raise Unsupported("Multiple documents or revisions are ambiguous")
                preceding = [h for h in headers if h.start() < first]
                version = preceding[-1][1].decode() if preceding else "1.4"
                wrapper = preceding[-1].start() if preceding else max(0, shift)
                header = f"%PDF-{version}\n".encode() + b"%\xe2\xe3\xcf\xd3\n"
                delta = len(header) - first
                patches = [(e.field_position, e.field_position + 10,
                            f"{e.offset + shift + delta:010d}".encode()) for e in entries]
                patches.append((last.start(1), last.end(1), str(x + delta).encode()))
                damaged_info = []
                preview = False
                if damaged and preview_candidate:
                    extra_patches, damaged_info, preview_forensic = preview_candidate
                    patches.extend(extra_patches)
                    forensic['reconstruction_preview'] = preview_forensic
                    strategy = 'bkc-image-smask-reconstruction-preview'
                    preview = True
                else:
                    for entry in sorted(damaged, key=lambda e: e.offset):
                        begin = entry.offset + shift
                        end = _entry_end(entries, entry, shift, x)
                        raw = data[begin:end]
                        # Retain only complete, recognized scalar entries in the surviving
                        # dictionary suffix. Unknown or partial values are never guessed.
                        suffix = re.search(rb'((?:/(?:op|OP|SA|AIS|TK)\s+(?:true|false)\s*)+)(?:/Type\s*/ExtGState\s*)?>>\s*endobj\s*$', raw)
                        surviving = suffix[1].decode('ascii').strip() if suffix else ''
                        body = f'{entry.number} {entry.generation} obj\n<< /Type /ExtGState /CA 1 /ca 1 /BM /Normal {surviving} >>\nendobj\n'.encode()
                        if len(body) > end - begin:
                            raise Unsupported('Damaged object has insufficient space for bounded salvage')
                        patches.append((begin, end, body + b' ' * (end - begin - len(body))))
                        damaged_info.append({'object': [entry.number, entry.generation], 'source_start': begin,
                                             'source_end': end, 'retained_suffix': surviving,
                                             'replacement': body.decode(),
                                             'uncertainty': 'Original opacity, blend mode and other lost graphics-state keys cannot be proven'})
                    strategy = 'damaged-early-extgstate-salvage' if damaged else 'classic-xref-offset-repair'
                candidates.append(Detection(strategy, version, wrapper, stated, first, last.end(), patches,
                                            header, forensic, damaged_info, entries, shift, preview,
                                            preview_policy if preview else ''))
            except Unsupported as exc:
                forensic["hypotheses"].append({"xref": x, "reason": str(exc)})
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise rejection("Multiple valid classic-xref reconstructions", forensic)
        # Strategy B: exact embedded PDF with a cross-reference stream. The parser
        # must later open without recovery; compressed entries are handled by qpdf.
        for h in headers:
            pos = h.start() + stated
            if 0 <= pos < last.start() and OBJECT.match(data, pos, pos + 100):
                dictionary = data[pos:min(pos + 4096, len(data))].split(b"stream", 1)[0]
                if re.search(rb"/Type\s*/XRef\b", dictionary) and not re.search(rb"/(Prev|Encrypt)\b", dictionary):
                    candidates.append(Detection("embedded-xref-stream", h[1].decode(), h.start(),
                                                stated, h.start(), last.end(), forensic=forensic))
        if len(candidates) == 1:
            return candidates[0]
        error = Unsupported("UNSUPPORTED_OR_AMBIGUOUS: no unique, verified PDF layout")
        error.forensic = forensic
        raise error
