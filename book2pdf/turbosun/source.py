"""Read-only indexed page access. Only TIFF metadata is read during layout."""
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import time
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from . import DECODER_VERSION, STRATEGY, TurboSunError
from .core import catalogue, decrypt_record, inspect_tiff, key_for, RecoveryError
from ..supervised import checkpoint

MAX_FILES = 100000
MAX_PAGES = 250000
MAX_RECORD = 128 * 1024 * 1024
MAX_DATABASE = 256 * 1024 * 1024
JET = b'\0\1\0\0Standard Jet DB\0'


def database_files(root):
    db = Path(root)/'db'
    if not db.is_dir() or db.is_symlink():return []
    found=[]
    for i,p in enumerate(db.iterdir()):
        if i>=MAX_FILES:raise TurboSunError('TURBOSUN_LIMIT_EXCEEDED','יותר מדי קובצי קטלוג.')
        if p.is_file() and not p.is_symlink():
            with p.open('rb') as f:head=f.read(24)
            if head.startswith(JET):found.append(p)
    return found


def resolve_root(path):
    path=Path(path).absolute()
    if path.is_dir():return path
    # A selected encrypted file is resolvable only through linked catalogue evidence.
    for parent in list(path.parents)[:4]:
        if database_files(parent):return parent
    return path


def detect_dataset(path):
    root=resolve_root(path)
    if not root.is_dir():return 'NOT_TURBOSUN'
    dbs=database_files(root)
    groups=any(p.is_dir() and p.name.startswith('Files_') and p.name[6:].isdigit() for p in root.iterdir())
    if not dbs:return 'INCOMPLETE_TURBOSUN_DATASET' if groups else 'NOT_TURBOSUN'
    if len(dbs)!=1 or not groups:return 'INCOMPLETE_TURBOSUN_DATASET'
    # Jet + schema is necessary; arbitrary Access databases are not this format.
    from .core import catalogue_tables
    tables=catalogue_tables(dbs[0],mdb_bin())
    import re
    bases=[t for t in tables if re.fullmatch(r'a\d+_Text_Basis',t)]
    if not bases:return 'NOT_TURBOSUN'
    from .core import catalogue_command
    for table in bases:
        if table.replace('_Basis','_F001') not in tables:
            import csv
            if list(csv.DictReader(io.StringIO(catalogue_command('mdb-export',[dbs[0],table],mdb_bin())))):
                return 'INCOMPLETE_TURBOSUN_DATASET'
    return 'SUPPORTED_TURBOSUN_DATASET'


def mdb_bin():
    from .core import discover_mdb_bin
    return discover_mdb_bin()


def fingerprint(root, bundles):
    dig=hashlib.sha256(DECODER_VERSION.encode())
    for p in database_files(root):
        if p.stat().st_size>MAX_DATABASE:raise TurboSunError('TURBOSUN_LIMIT_EXCEEDED','קטלוג גדול מהמגבלה.')
        dig.update(p.name.encode());dig.update(p.read_bytes())
    for b in bundles:
        p=Path(b['source']);s=p.stat()
        dig.update(json.dumps([b['relative'],s.st_size,s.st_mtime_ns],ensure_ascii=False).encode())
        from ..state import sha256
        dig.update(sha256(p).encode())
    return dig.hexdigest()


class EncryptedReader:
    """Seekable TIFF header reader; AES ECB permits exact bounded block access."""
    def __init__(self, stream, offset, length, key):
        self.stream=stream;self.offset=offset;self.length=length;self.key=key;self.bytes_read=0
    def read_at(self, start, count):
        if start<0 or count<0 or count>65536 or start+count>self.length:
            raise TurboSunError('TURBOSUN_PAGE_DECODE_FAILED','שדה TIFF מחוץ לגבולות הרשומה.')
        low=start//16*16;high=(start+count+15)//16*16
        self.stream.seek(self.offset+low);cipher=self.stream.read(high-low)
        if len(cipher)!=high-low:raise RecoveryError('Truncated encrypted TIFF metadata')
        self.bytes_read+=len(cipher)
        ctx=Cipher(algorithms.AES(self.key),modes.ECB()).decryptor()
        data=ctx.update(cipher)+ctx.finalize()
        return data[start-low:start-low+count]


def geometry(reader):
    head=reader.read_at(0,8)
    if head[:4] not in (b'II*\0',b'MM\0*'):raise RecoveryError('Invalid encrypted TIFF header')
    endian='<' if head[:2]==b'II' else '>'
    off=struct.unpack(endian+'I',head[4:])[0]
    count=struct.unpack(endian+'H',reader.read_at(off,2))[0]
    if not 1<=count<=4096:raise RecoveryError('Invalid TIFF metadata field count')
    entries=reader.read_at(off+2,count*12+4);tags={}
    if struct.unpack(endian+'I',entries[-4:])[0]:raise RecoveryError('Multiple IFDs in page record')
    for pos in range(0,count*12,12):
        tag,typ,n,value=struct.unpack_from(endian+'HHII',entries,pos)
        if tag not in (256,257,282,283,296,274):continue
        if n!=1 or typ not in (3,4,5):raise RecoveryError('Unsupported TIFF geometry tag')
        if typ==5:
            a,b=struct.unpack(endian+'II',reader.read_at(value,8))
            if not b:raise RecoveryError('Invalid TIFF resolution')
            tags[tag]=a/b
        else:tags[tag]=struct.unpack_from(endian+('H' if typ==3 else 'I'),entries,pos+8)[0]
    w,h=tags[256],tags[257];unit=tags.get(296,2)
    if unit not in (2,3) or tags.get(274,1)!=1:raise RecoveryError('Unsupported TIFF orientation/resolution')
    dx,dy=tags[282],tags[283]
    if unit==3:dx*=2.54;dy*=2.54
    if not (0<w<=50000 and 0<h<=50000 and w*h<=150000000 and 1<=dx<=10000 and 1<=dy<=10000):raise RecoveryError('Unsafe TIFF geometry')
    return {'width':w*72/dx,'height':h*72/dy,'pixel_width':w,'pixel_height':h,'dpi':[dx,dy]}


class Dataset:
    def __init__(self,path):
        started=time.monotonic();self.root=resolve_root(path);self.decoded_pages=0;self.header_bytes=0
        if not self.root.is_dir():raise TurboSunError('TURBOSUN_CATALOGUE_MISSING','נדרשת תיקיית המקור עם הקטלוג והאינדקס; קובץ TIFF מוצפן לבדו אינו מספיק.')
        if not database_files(self.root):raise TurboSunError('TURBOSUN_CATALOGUE_MISSING','קטלוג TurboSun חסר בתיקייה db.')
        try:self.bundles,self.warnings=catalogue(self.root,mdb_bin())
        except FileNotFoundError as exc:raise TurboSunError('TURBOSUN_DATASET_INCOMPLETE','חסרים כלי MDB לקריאת הקטלוג. יש להתקין mdbtools או להגדיר AAG_MDB_BIN.') from exc
        except (ValueError,KeyError) as exc:
            text=str(exc)
            code='TURBOSUN_LIMIT_EXCEEDED' if 'limit' in text.lower() else 'TURBOSUN_CONTAINER_MISSING' if 'Missing/unsafe' in text else 'TURBOSUN_INDEX_MISSING' if 'sequence' in text or 'index' in text.lower() else 'TURBOSUN_UNSUPPORTED_VARIANT'
            raise TurboSunError(code,'קטלוג או אינדקס TurboSun אינו עקבי: '+text) from exc
        if not self.bundles:raise TurboSunError('TURBOSUN_UNSUPPORTED_VARIANT','לא נמצאה סכמת קטלוג נתמכת.')
        count=sum(len(b['pages']) for b in self.bundles)
        if count>MAX_PAGES:raise TurboSunError('TURBOSUN_LIMIT_EXCEEDED','מספר העמודים חורג מהמגבלה.')
        if sum(Path(b['source']).stat().st_size for b in self.bundles)>16*1024**3:
            raise TurboSunError('TURBOSUN_LIMIT_EXCEEDED','גודל האוסף חורג מהמגבלה.')
        self.identity=fingerprint(self.root,self.bundles);self.pages=[];self.unplaced=[];self.stats={}
        self.metadata_stats={str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in database_files(self.root)}
        for bi,b in enumerate(self.bundles):
            checkpoint();p=Path(b['source']);st=p.stat();self.stats[str(p)]=(st.st_size,st.st_mtime_ns)
            key=key_for(p.name,b['field'])
            with p.open('rb') as stream:
                for record in b['pages']:
                    if record['length']>MAX_RECORD:raise TurboSunError('TURBOSUN_LIMIT_EXCEEDED','רשומת עמוד גדולה מהמגבלה.')
                    reader=EncryptedReader(stream,record['offset'],record['length'],key)
                    try:layout=geometry(reader)
                    except (ValueError,KeyError,struct.error) as exc:raise TurboSunError('TURBOSUN_PAGE_DECODE_FAILED','מטא־נתוני TIFF פגומים: '+str(exc)) from exc
                    self.header_bytes+=reader.bytes_read
                    page={'number':len(self.pages)+1,'page_index':len(self.pages),'logical_page_number':record['number'],
                          'container_path':b['relative'],'record_offset':record['offset'],'record_length':record['length'],
                          'bundle_index':bi,'index_record':record['index_record'],'image_format':'TIFF','decode_status':'METADATA_VALIDATED',
                          'page_provenance':'CATALOGUE_F902_AND_PAGE_INDEX_F1','summary_count_discrepancy':len(b['pages'])!=b['catalogue_pages'],**layout}
                    self.pages.append(page)
                    # Local index proves position even when the tree label is absent.
                    # Expose these explicitly as supplemental indexed pages.
                    if record['number']>b['catalogue_pages']:
                        page['supplemental_status']='INDEXED_POSITION_PROVEN_TREE_LABEL_MISSING'
                        self.unplaced.append(page)
        self.ordered_pages=[p for p in self.pages if p not in self.unplaced]
        self.ready_seconds=time.monotonic()-started
    def unchanged(self):
        return self.identity==fingerprint(self.root,self.bundles)
    def decode_page(self,index):
        checkpoint()
        if not 0<=index<len(self.pages):raise IndexError('Page outside dataset')
        for name,expected in self.metadata_stats.items():
            st=Path(name).stat()
            if (st.st_size,st.st_mtime_ns)!=expected:raise TurboSunError('TURBOSUN_PAGE_DECODE_FAILED','הקטלוג השתנה; יש לפתוח אותו מחדש.')
        page=self.pages[index];b=self.bundles[page['bundle_index']];path=Path(b['source']);s=path.stat()
        if self.stats[str(path)]!=(s.st_size,s.st_mtime_ns):raise TurboSunError('TURBOSUN_PAGE_DECODE_FAILED','קובץ המקור השתנה; יש לפתוח אותו מחדש.')
        with path.open('rb') as f:f.seek(page['record_offset']);cipher=f.read(page['record_length'])
        try:
            data=decrypt_record(cipher,key_for(path.name,b['field']))
            if len(inspect_tiff(data))!=1:raise RecoveryError('Not a single page TIFF')
        except ValueError as exc:raise TurboSunError('TURBOSUN_PAGE_DECODE_FAILED','פענוח העמוד נכשל: '+str(exc)) from exc
        self.decoded_pages+=1
        return data
    def list_pages(self):return self.pages
    def get_page_metadata(self,index):return dict(self.pages[index])
    def get_unplaced_assets(self):return [dict(p) for p in self.unplaced]
    def validate_page(self,index):
        from .core import validate_pixels
        return validate_pixels(self.decode_page(index))


def open_dataset(path):return Dataset(path)


def protect_outputs(inputs, *outputs):
    """Reject report/state/output writes into immutable linked datasets up front."""
    for source in inputs:
        root=resolve_root(source)
        if root.is_dir() and (database_files(root) or any(p.is_dir() and p.name.startswith('Files_') and p.name[6:].isdigit() for p in root.iterdir())):
            for output in outputs:
                if output is not None and Path(output).resolve().is_relative_to(root.resolve()):
                    raise ValueError('Cannot write PDF, report or state inside the source dataset')
