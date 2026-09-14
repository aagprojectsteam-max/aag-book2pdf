"""Append preview provenance without rewriting any surviving PDF object bytes."""
import json
from pathlib import Path
import re

FIELDS = ('recovery_class','preview_policy','fidelity','missing_data_synthesized','assumptions','affected_pages')
KEY = '/AAGBook2PDFProvenance'


def embedded_provenance(path):
    import pikepdf
    with pikepdf.open(path,attempt_recovery=False) as pdf:
        value = pdf.docinfo.get(KEY)
        if not value:
            return None
        record = json.loads(str(value))
        from .recovery.document import GENERIC_CLASSES, SCHEMA
        if record.get('recovery_class') in GENERIC_CLASSES:
            model=record.get('recovered_document',{})
            if model.get('schema')!=SCHEMA or not isinstance(model.get('pages'),list):
                raise ValueError('Invalid recovered-document provenance')
            if len(model['pages'])!=len(pdf.pages):
                raise ValueError('Recovered-document provenance page count mismatch')
            return record
        if record.get('recovery_class') == 'DECODED_ORIGINAL_CONTENT':
            if record.get('preservation',{}).get('strategy') != 'bkf-djvu-prefix200-v1':
                raise ValueError('Unrecognized decoded-content provenance')
            return record
        if record.get('recovery_class') != 'RECONSTRUCTED_PREVIEW' or record.get('preview_policy') not in ('opaque','transparent'):
            raise ValueError('Unrecognized embedded preview provenance')
        return record


def stamp_recovered(path,result):
    _stamp(path,{field:getattr(result,field) for field in
        ('recovery_class','fidelity','assumptions','warning','recovered_document','missing_data_synthesized','reconstructed_objects')})


def stamp_preview(path, result):
    _stamp(path,{field:getattr(result,field) for field in FIELDS})


def stamp_decoded(path, result):
    _stamp(path,{field:getattr(result,field) for field in
                ('recovery_class','fidelity','assumptions','warning','source_hash','preservation')})


def _stamp(path, record):
    """Standards-compliant incremental Info update on a private staging PDF.

    Existing bytes and object IDs remain unchanged. All validation runs again
    after this append; publication never precedes that validation.
    """
    import pikepdf
    path = Path(path)
    with pikepdf.open(path,attempt_recovery=False) as pdf:
        info = pikepdf.Dictionary(pdf.docinfo)
        info[KEY] = json.dumps(record,ensure_ascii=False)
        info_bytes = info.unparse()
        number = max(int(pdf.trailer.Size), max(getattr(obj,'objgen',(0,0))[0] for obj in pdf.objects)+1)
        root = f'{pdf.Root.objgen[0]} {pdf.Root.objgen[1]} R'.encode()
        identifier = pdf.trailer.get('/ID')
        identifier_bytes = b' /ID '+identifier.unparse() if identifier is not None else b''
    with path.open('rb') as stream:
        stream.seek(max(0,path.stat().st_size-4096))
        tail = stream.read()
    starts = list(re.finditer(rb'startxref\s+(\d+)\s+%%EOF',tail))
    if not starts:
        raise ValueError('No verified previous xref for preview provenance')
    previous = int(starts[-1][1])
    with path.open('ab') as stream:
        stream.write(b'\n')
        offset = stream.tell()
        stream.write(f'{number} 0 obj\n'.encode()+info_bytes+b'\nendobj\n')
        xref = stream.tell()
        stream.write(f'xref\n{number} 1\n{offset:010d} 00000 n \ntrailer\n<< /Size {number+1} /Root '.encode()+root+
            f' /Info {number} 0 R /Prev {previous}'.encode()+identifier_bytes+
            f' >>\nstartxref\n{xref}\n%%EOF\n'.encode())
