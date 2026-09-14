"""Isolated BKF decoding to lossless PDF images and retained original DjVu bytes."""
import hashlib
import json
import multiprocessing
from pathlib import Path
import tempfile
import zlib

from . import STRATEGY, DECODER_VERSION, ASSEMBLY_VERSION, OPAQUE_ENTRY_ASSUMPTION
from .format import parse, BKFError, MAX_INPUT


class BoundedOutput:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit
    def write(self, data):
        if self.stream.tell() + len(data) > self.limit:
            raise BKFError('PDF output exceeds disk budget')
        return self.stream.write(data)
    def __getattr__(self, name):
        return getattr(self.stream, name)


def build_pdf(source,output,scratch, *, selected=None, book=None, materialize=True, progress=None):
    import pikepdf
    from .native import Decoder
    data=Path(source).read_bytes()
    book=book or parse(data)
    from ..page_ranges import normalize_pages
    selected=normalize_pages(selected, len(book.pages)) if selected is not None else list(range(1,len(book.pages)+1))
    # File names used for dependency lookup have already passed strict validation.
    for entry in book.entries:
        if materialize and entry.kind!='UNKNOWN':(scratch/entry.name).write_bytes(entry.decoded)
    decoder=Decoder();records=[]
    try:
        with pikepdf.Pdf.new() as pdf:
            # Shared components remain extractable alongside any selected page.
            # Indirect references deduplicate these bytes within the full PDF.
            resources=[]
            for resource in book.entries:
                if resource.kind in ('PAGE','UNKNOWN'):continue
                original=pikepdf.Stream(pdf,resource.decoded);original.Type=pikepdf.Name.EmbeddedFile
                original.Subtype=pikepdf.Name('/image/vnd.djvu')
                resources.append(pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Filespec,
                    F=resource.name,UF=resource.name,AFRelationship=pikepdf.Name.Source,
                    EF=pikepdf.Dictionary(F=original))))
            for ordinal, number in enumerate(selected):
                if progress:progress({'phase':'decode', 'done':ordinal, 'total':len(selected)})
                entry=book.pages[number-1]
                native=decoder.render(scratch/entry.name)
                pixels=native.pop('pixels');w=native['width'];h=native['height'];dpi=native['dpi']
                info=next(c for c in entry.chunks if c['type']=='INFO')
                width=int.from_bytes(entry.decoded[info['offset']+8:info['offset']+10],'big')
                height=int.from_bytes(entry.decoded[info['offset']+10:info['offset']+12],'big')
                if sorted((w,h))!=sorted((width,height)):raise BKFError('Native dimensions differ from INFO')
                pw=w*72/dpi;ph=h*72/dpi
                unit=max(1.0,pw/14400,ph/14400)
                pw/=unit;ph/=unit
                page=pdf.add_blank_page(page_size=(min(14400,max(3,pw)),min(14400,max(3,ph))))
                page.obj.MediaBox=pikepdf.Array([0,0,pw,ph])
                if unit>1:page.obj.UserUnit=unit
                image=pikepdf.Stream(pdf,zlib.compress(pixels,6))
                image.Type=pikepdf.Name.XObject;image.Subtype=pikepdf.Name.Image
                image.Width=w;image.Height=h;image.BitsPerComponent=1 if native['bitonal'] else 8
                image.ColorSpace=pikepdf.Name.DeviceGray if native['bitonal'] else pikepdf.Name.DeviceRGB
                image.Filter=pikepdf.Name.FlateDecode
                if native['bitonal']:image.Decode=pikepdf.Array([1,0])
                page.Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Scan=image))
                page.Contents=pikepdf.Stream(pdf,f'q {pw:.9f} 0 0 {ph:.9f} 0 0 cm /Scan Do Q\n'.encode())
                original=pikepdf.Stream(pdf,entry.decoded);original.Type=pikepdf.Name.EmbeddedFile
                original.Subtype=pikepdf.Name('/image/vnd.djvu')
                spec=pdf.make_indirect(pikepdf.Dictionary(Type=pikepdf.Name.Filespec,
                    F=f'page-{len(records)+1:06}.djvu',UF=f'page-{len(records)+1:06}.djvu',
                    AFRelationship=pikepdf.Name.Source,EF=pikepdf.Dictionary(F=original)))
                page.obj['/AF']=pikepdf.Array([spec,*resources])
                record={**entry.evidence(),**native,'pixel_sha256':hashlib.sha256(pixels).hexdigest()}
                page.obj['/AAGSourceEntry']=entry.index
                records.append(record)
            if progress:progress({'phase':'decode', 'done':len(selected), 'total':len(selected)})
            with open(output, 'wb') as stream:
                pdf.save(BoundedOutput(stream, 1024*1024*1024),min_version='2.0',compress_streams=False,stream_decode_level=pikepdf.StreamDecodeLevel.none)
        if Path(output).stat().st_size>1024*1024*1024:raise BKFError('PDF output exceeds disk budget')
        with pikepdf.open(output,attempt_recovery=False) as verified:
            if len(verified.pages)!=len(records):raise BKFError('PDF page count changed')
            for page,record in zip(verified.pages,records):
                if hashlib.sha256(page.Resources.XObject.Scan.read_bytes()).hexdigest()!=record['pixel_sha256']:
                    raise BKFError('PDF image pixels differ from native decoding')
                if hashlib.sha256(page.obj.AF[0].EF.F.read_bytes()).hexdigest()!=record['decoded_sha256']:
                    raise BKFError('Original DjVu attachment changed')
                retained=[e for e in book.entries if e.kind not in ('PAGE','UNKNOWN')]
                for attachment,entry in zip(list(page.obj.AF)[1:],retained):
                    if attachment.EF.F.read_bytes()!=entry.decoded:raise BKFError('Shared DjVu attachment changed')
        return {'strategy':STRATEGY,'decoder_version':DECODER_VERSION,'native_decoder':decoder.version,
                'assembly_version':ASSEMBLY_VERSION,
                'assumptions':[OPAQUE_ENTRY_ASSUMPTION] if any(e.kind=='UNKNOWN' for e in book.entries) else [],
                'source_sha256':hashlib.sha256(data).hexdigest(),'page_count':len(records),
                'directory_entry_count':len(book.entries),'wrapper_offset':book.base,
                'outer_header_hex':book.header.hex(),'directory_header_hex':book.directory_header.hex(),
                'opaque_entries':[e.evidence() for e in book.entries if e.kind=='UNKNOWN'],
                'nonpage_entries':[e.evidence() for e in book.entries if e.kind!='PAGE'],
                'pages':records,'native_pixels_preserved':True,'original_djvu_bytes_preserved':True,
                'all_intact_objects_byte_identical':False,
                'synthesized_structures':['PDF page tree, page dictionaries, image placement and provenance',
                                          'lossless Flate image representation of native decoded pixels'],
                'page_count_scope':'All FORM:DJVU entries; opaque entry semantics are unknown'}
    finally:
        decoder.close()


def _child(source,output,report,scratch):
    from ..research_limits import constrain
    try:
        constrain(3*1024*1024*1024,300)
        result=build_pdf(source,output,Path(scratch))
        Path(report).write_text(json.dumps(result),encoding='utf-8')
    except Exception as exc:
        Path(report).write_text(json.dumps({'error':f'{type(exc).__name__}: {exc}', 'code':getattr(exc,'code','DEPENDENCY_MISSING' if isinstance(exc,ModuleNotFoundError) else 'RUNTIME_LOAD_FAILED' if isinstance(exc,ImportError) else 'DOCUMENT_INVALID')}),encoding='utf-8')


def reconstruct(source,output):
    """Caller owns atomic staging; child owns disposable native state and files."""
    source=Path(source)
    if source.stat().st_size>MAX_INPUT:raise BKFError('BKF source exceeds 256 MiB budget')
    with tempfile.TemporaryDirectory(prefix='aag-bkf-') as temporary:
        scratch=Path(temporary);report=scratch/'result.json';records=scratch/'records';records.mkdir()
        process=multiprocessing.get_context('spawn').Process(target=_child,args=(str(source),str(output),str(report),str(records)))
        process.start()
        try:
            process.join(360)
            if process.is_alive():
                process.terminate();process.join(2)
                if process.is_alive():process.kill();process.join()
                raise BKFError('BKF decoding exceeded its wall time budget')
            if process.exitcode!=0 or not report.is_file():raise BKFError('BKF decoder exceeded a limit or failed')
            if report.stat().st_size>16*1024*1024:raise BKFError('BKF report budget')
            result=json.loads(report.read_text(encoding='utf-8'))
            if 'error' in result:
                error=BKFError(result['error']);error.code=result.get('code','DOCUMENT_INVALID');raise error
            return result
        finally:
            if process.is_alive():process.terminate();process.join(2)
