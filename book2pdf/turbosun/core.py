#!/usr/bin/env python3
"""Read-only TurboSun catalogue/AES page recovery. No TurboSun executable is run."""
from __future__ import annotations
import argparse, collections, csv, hashlib, io, json, os, shutil, struct, subprocess, sys, tempfile, time
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

class RecoveryError(ValueError):
    pass

def sha(data):
    return hashlib.sha256(data).hexdigest()

def key_for(filename, field):
    # Recovered VB6 flow: lowercase stem, ANSI(cp1255) Asc+2, insert F910
    # after 4 chars, even VB positions followed by odd, resize byte array to 32.
    stem = Path(filename).stem.lower().encode('cp1255', errors='strict')
    shifted = bytes((c + 2) % 256 for c in stem)
    if not field or not field.isascii():
        raise RecoveryError('Missing/unsupported F910 metadata')
    mixed = shifted[:4] + field.encode('ascii') + shifted[4:]
    return (mixed[1::2] + mixed[::2])[:32].ljust(32, b'\0')

def decrypt_record(ciphertext, key):
    if not ciphertext or len(ciphertext) % 16:
        raise RecoveryError('Cipher record must contain complete AES blocks')
    ctx = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = ctx.update(ciphertext) + ctx.finalize()
    remainder = padded[-1]
    if remainder > 15:
        raise RecoveryError('Invalid Rijndael remainder padding')
    length = len(padded) - 16 + remainder
    if padded[length:-1] != bytes(len(padded) - length - 1):
        raise RecoveryError('Nonzero Rijndael padding')
    clear = padded[:length]
    # Round-trip includes every original ciphertext byte, including padding.
    ctx = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    encoded = ctx.update(clear + bytes(15 - remainder) + bytes([remainder])) + ctx.finalize()
    if encoded != ciphertext:
        raise RecoveryError('Re-encoding did not reproduce original stream')
    return clear

TYPE_SIZES = {1:1, 2:1, 3:2, 4:4, 5:8, 6:1, 7:1, 8:2, 9:4, 10:8, 11:4, 12:8, 13:4}

def inspect_tiff(data):
    """Bounded original TIFF/IFD/strip validation, without repairing any bytes."""
    if len(data) < 8 or data[:4] not in (b'II*\0', b'MM\0*'):
        raise RecoveryError('Decrypted record is not classic TIFF')
    endian = '<' if data[:2] == b'II' else '>'
    u16 = lambda p: struct.unpack_from(endian+'H', data, p)[0]
    u32 = lambda p: struct.unpack_from(endian+'I', data, p)[0]
    offset = u32(4); seen=set(); pages=[]; extent=8
    while offset:
        if offset in seen or len(seen) >= 10000 or not 8 <= offset <= len(data)-6:
            raise RecoveryError('Invalid/cyclic TIFF IFD')
        seen.add(offset); count=u16(offset); end=offset+2+12*count+4
        if count > 4096 or end > len(data):
            raise RecoveryError('TIFF IFD outside record')
        extent=max(extent,end); tags={}
        for p in range(offset+2,end-4,12):
            tag,typ,n=struct.unpack_from(endian+'HHI',data,p)
            if typ not in TYPE_SIZES or n > 10000000:
                raise RecoveryError('Unknown or excessive TIFF field')
            size=TYPE_SIZES[typ]*n; start=p+8 if size<=4 else u32(p+8)
            if start+size>len(data):
                raise RecoveryError('TIFF field points outside record')
            extent=max(extent,start+size)
            if typ in (1,3,4):
                fmt={1:'B',3:'H',4:'I'}[typ]; tags[tag]=struct.unpack_from(endian+str(n)+fmt,data,start)
        if 256 not in tags or 257 not in tags:
            raise RecoveryError('Missing TIFF dimensions')
        w,h=tags[256][0],tags[257][0]
        if not (0<w<=50000 and 0<h<=50000 and w*h<=150000000):
            raise RecoveryError('Unsafe TIFF dimensions')
        offsets=tags.get(273,tags.get(324)); lengths=tags.get(279,tags.get(325))
        if not offsets or not lengths or len(offsets)!=len(lengths):
            raise RecoveryError('Missing/mismatched TIFF strip/tile arrays')
        for start,size in zip(offsets,lengths):
            if start<8 or size<=0 or start+size>len(data):
                raise RecoveryError('TIFF strip/tile outside record')
            extent=max(extent,start+size)
        pages.append({'width':w,'height':h,'compression':tags.get(259,(1,))[0],
                      'bits_per_sample':list(tags.get(258,(1,))), 'ifd_offset':offset,
                      'strip_offsets':list(offsets),'strip_lengths':list(lengths)})
        offset=u32(end-4)
    if not pages or extent!=len(data):
        raise RecoveryError(f'TIFF extent {extent} differs from original plaintext length {len(data)}')
    return pages

def discover_mdb_bin():
    configured=os.environ.get('AAG_MDB_BIN')
    if configured:return Path(configured)
    native=Path(sys.prefix)/'native'/'mdbtools'/'bin'
    return native if native.is_dir() else None


def catalogue_command(name, args, mdb_bin=None):
    """Trusted installed tool, bounded output/time; never execute dataset files."""
    binpath=Path(mdb_bin) if mdb_bin else None
    command=str(binpath/name) if binpath else name
    env=os.environ.copy()
    if binpath:
        lib=binpath.parent/'lib/x86_64-linux-gnu'
        if lib.exists():env['LD_LIBRARY_PATH']=str(lib)
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        child=subprocess.Popen([command,*map(str,args)],stdout=output,stderr=errors,stdin=subprocess.DEVNULL,env=env)
        started=time.monotonic()
        try:
            while child.poll() is None:
                if time.monotonic()-started>60 or os.fstat(output.fileno()).st_size>128*1024*1024 or os.fstat(errors.fileno()).st_size>1024*1024:
                    raise RecoveryError('Catalogue tool time/output limit exceeded')
                time.sleep(.01)
            if child.returncode:
                errors.seek(0)
                raise RecoveryError('Catalogue reader failed: '+errors.read(2000).decode('utf-8',errors='replace'))
            if os.fstat(output.fileno()).st_size>128*1024*1024:raise RecoveryError('Catalogue output limit exceeded')
            output.seek(0)
            return output.read().decode('utf-8')
        finally:
            if child.poll() is None:child.kill()
            child.wait()



def catalogue_tables(database,mdb_bin=None):
    return catalogue_command('mdb-tables',['-1',database],mdb_bin).splitlines()


def catalogue(source, mdb_bin=None, metadata_dir=None):
    """Obtain catalogue from the ORIGINAL Jet database using read-only mdbtools."""
    source=Path(source).resolve()
    dbdir=source/'db'
    if not dbdir.is_dir() or dbdir.is_symlink():raise RecoveryError('Missing catalogue db directory')
    dbs=[p for p in dbdir.iterdir() if p.is_file() and not p.is_symlink()]
    if len(dbs)>10000:raise RecoveryError('Catalogue file limit exceeded')
    jets=[]
    for p in dbs:
        if p.stat().st_size>256*1024*1024:raise RecoveryError('Catalogue byte limit exceeded')
        with p.open('rb') as f: head=f.read(24)
        if head.startswith(b'\0\1\0\0Standard Jet DB\0'): jets.append(p)
    if len(jets)!=1: raise RecoveryError('Expected one Jet catalogue in db/')
    if metadata_dir:
        tables=[p.stem.split('__',1)[1] for p in metadata_dir.glob(jets[0].stem+'__*.csv')]
        def get(table):
            with (metadata_dir/(jets[0].stem+'__'+table+'.csv')).open() as f: return list(csv.DictReader(f))
    else:
        tables=catalogue_tables(jets[0],mdb_bin)
        def get(table):
            if table not in tables:raise RecoveryError('Missing page index table: '+table)
            text=catalogue_command('mdb-export',[jets[0],table],mdb_bin)
            rows=list(csv.DictReader(io.StringIO(text)))
            if len(rows)>250000:raise RecoveryError('Catalogue record limit exceeded')
            return rows
    import re
    bundles=[]; warnings=[]
    for table in sorted(tables):
        m=re.fullmatch(r'a(\d+)_Text_Basis',table)
        if not m:continue
        g=int(m[1]); rows=get(table)
        if not rows:continue
        sequences=[int(r['F902']) for r in rows]
        if min(sequences)<0 or len(set(sequences))!=len(sequences):raise RecoveryError('Ambiguous catalogue book order')
        by=collections.defaultdict(list)
        for r in get(f'a{g:02d}_Text_F001'):by[int(r['TF_ptrBasis'])].append(r)
        for row in sorted(rows,key=lambda r:int(r['F902'])):
            for component in (row['F904'],row['F905']):
                if not component or component in ('.','..') or any(c in component for c in ('/','\\',':','\0')):
                    raise RecoveryError('Missing/unsafe catalogue path component')
            relative=Path(f'Files_{g:02d}')/row['F904']/row['F905']
            path=(source/relative).resolve()
            if not path.is_relative_to(source.resolve()) or not path.is_file() or (source/relative).is_symlink():
                raise RecoveryError(f'Missing/unsafe catalogue path: {relative}')
            ix=sorted(by.pop(int(row['TB_IndexCounter']),[]),key=lambda r:int(r['TF_F001_F1']))
            if not ix or [int(r['TF_F001_F1']) for r in ix]!=list(range(1,len(ix)+1)):
                raise RecoveryError(f'Incomplete/duplicate page sequence: {relative}')
            # Infer the virtual wrapper bias from independent file size and ALL lengths.
            excess=sum(int(r['TF_F001_F3']) for r in ix)-path.stat().st_size
            bias,remainder=divmod(excess,len(ix))
            if remainder or bias<0 or bias>65536:
                raise RecoveryError(f'Cannot infer index bias: {relative}')
            off=0; pages=[]
            for r in ix:
                start=int(r['TF_F001_F2']); length=int(r['TF_F001_F3'])-bias
                if start!=(1 if not off else off+bias+1) or length<=0 or length>128*1024*1024 or length%16:
                    raise RecoveryError(f'Index offsets/lengths do not cover source: {relative}')
                pages.append({'number':int(r['TF_F001_F1']),'offset':off,'length':length,
                              'index_record':int(r['TF001_IndexCounter']),'stored_offset':start,
                              'stored_length':int(r['TF_F001_F3'])});off+=length
            if off!=path.stat().st_size:raise RecoveryError('Index does not cover entire source')
            if len(ix)!=int(row['F903']):
                warnings.append({'file':str(relative),'issue':'catalogue_count_differs_from_page_index',
                                 'catalogue_pages':int(row['F903']),'indexed_pages':len(ix)})
            bundles.append({'source':str(path),'relative':str(relative),'group':g,
                            'book_sequence':int(row['F902']),'basis_id':int(row['TB_IndexCounter']),
                            'catalogue_pages':int(row['F903']),'first_tree_record':int(row['TB_R_Number']),
                            'field':row['F910'],'pages':pages,'index_bias':bias})
        if by:raise RecoveryError('Unmapped page-index records')
    actual={str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() and p.suffix.lower()=='.tif'}
    mapped={b['relative'] for b in bundles}
    if len(mapped)!=len(bundles) or mapped!=actual:raise RecoveryError('Catalogue does not map all TIF sources one-to-one')
    return bundles,warnings

def validate_pixels(data):
    from PIL import Image
    import pymupdf
    with Image.open(io.BytesIO(data)) as image:
        if image.n_frames!=1:raise RecoveryError('Unexpected multi-IFD page record')
        image.load(); grayscale=image.convert('L'); size=image.size
        pillow_hash=sha(grayscale.tobytes())
    pix=pymupdf.Pixmap(data)
    if pix.alpha:pix=pymupdf.Pixmap(pix,0)
    if pix.colorspace.n!=1:pix=pymupdf.Pixmap(pymupdf.csGRAY,pix)
    if (pix.width,pix.height)!=size:raise RecoveryError('Independent decoders disagree on dimensions')
    mupdf_hash=sha(pix.samples)
    if pillow_hash!=mupdf_hash:raise RecoveryError('Independent decoders disagree on pixels')
    return {'pillow':'PASS','mupdf':'PASS','pixels_equal':True,'grayscale_sha256':pillow_hash}

def recover_bundle(bundle, staging, validate=True):
    start=time.monotonic();source=Path(bundle['source']);key=key_for(source.name,bundle['field'])
    output=Path(staging)/Path(bundle['relative']).with_suffix('');output.mkdir(parents=True)
    records=[];source_digest=hashlib.sha256()
    with source.open('rb') as f:
        for page in bundle['pages']:
            if f.tell()!=page['offset']:raise RecoveryError('Unexpected source read offset')
            ciphertext=f.read(page['length']);source_digest.update(ciphertext)
            data=decrypt_record(ciphertext,key);tiff=inspect_tiff(data)
            if len(tiff)!=1:raise RecoveryError('Unexpected multi-page TIFF record')
            validation=validate_pixels(data) if validate else {'status':'NOT_RUN'}
            destination=output/f"page-{page['number']:04d}.tif"
            with destination.open('xb') as out:out.write(data)
            records.append({**page,**tiff[0],'output':str(destination.relative_to(staging)),
                            'output_bytes':len(data),'sha256':sha(data),'ciphertext_sha256':sha(ciphertext),
                            'reencode_exact':True,'validation':validation})
        if f.read(1):raise RecoveryError('Unmapped trailing source bytes')
    return {k:v for k,v in bundle.items() if k not in ('field','pages')} | {
        'source_sha256':source_digest.hexdigest(),'pages':records,'elapsed_seconds':time.monotonic()-start}

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path);ap.add_argument('-o','--output',type=Path)
    ap.add_argument('--mdb-bin',type=Path,help='Directory containing read-only mdbtools utilities')
    ap.add_argument('--metadata-dir',type=Path,help='Research-only reuse of previously exported catalogue CSVs')
    ap.add_argument('--workers',type=int,default=2)
    ap.add_argument('--inspect-only',action='store_true')
    args=ap.parse_args(argv);source=args.source.resolve()
    try:
        bundles,warnings=catalogue(source,args.mdb_bin or discover_mdb_bin(),args.metadata_dir)
        count=sum(len(b['pages']) for b in bundles)
        if args.inspect_only:
            print(json.dumps({'files':len(bundles),'pages':count,'warnings':warnings,
                              'index_biases':sorted({b['index_bias'] for b in bundles})},ensure_ascii=False,indent=2));return 0
        target=(args.output or source.with_name(source.name+'_RECOVERED')).absolute()
        if target.exists() or target.is_symlink():raise RecoveryError('Output already exists; overwriting is forbidden')
        if target.resolve().is_relative_to(source):raise RecoveryError('Output must be outside original dataset')
        if not 1<=args.workers<=8:raise RecoveryError('Worker count must be 1..8')
        staging=Path(tempfile.mkdtemp(prefix='.'+target.name+'-',dir=target.parent))
        print(f'Validating {len(bundles)} containers / {count} pages; staging {staging}',flush=True)
        from concurrent.futures import ProcessPoolExecutor,as_completed
        results=[];started=time.monotonic()
        try:
            with ProcessPoolExecutor(max_workers=args.workers) as pool:
                futures={pool.submit(recover_bundle,b,str(staging)):b for b in bundles}
                for f in as_completed(futures):
                    result=f.result();results.append(result)
                    print(f"{len(results)}/{len(bundles)} {result['relative']}: {len(result['pages'])} pages validated",flush=True)
            results.sort(key=lambda b:(b['group'],b['book_sequence']))
            manifest={'format':'TurboSun catalogue-indexed AES-256-ECB TIFF records','source':str(source),
                      'containers':len(results),'page_count':count,'warnings':warnings,
                      'validation':'Every page decoded independently with Pillow/libtiff and MuPDF; grayscale pixels identical',
                      'elapsed_seconds':time.monotonic()-started,'results':results}
            with (staging/'recovery-manifest.json').open('x') as f:json.dump(manifest,f,ensure_ascii=False,indent=2)
            # No-clobber publication: create destination exclusively, then move our own staging contents.
            target.mkdir()
            for item in staging.iterdir():item.rename(target/item.name)
            staging.rmdir()
            print(json.dumps({'RECOVERED_FILES':count,'ALL_RECOVERED_OPEN':'PASS','OUTPUT':str(target)},ensure_ascii=False));return 0
        except BaseException:
            shutil.rmtree(staging)
            raise
    except (RecoveryError,OSError,subprocess.SubprocessError) as exc:
        print(f'Recovery refused: {exc}',file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
