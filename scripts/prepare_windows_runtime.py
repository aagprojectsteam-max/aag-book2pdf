"""Prepare a hash-pinned native DjVu runtime without executing Windows binaries.

Builder-only dependencies: pefile, zstandard (Python <3.14). Corresponding source
archives and licenses accompany the DLLs. This is not native runtime acceptance.
"""
from contextlib import contextmanager
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
LOCK=ROOT/'packaging/windows/djvu-runtime-lock.json'
DLLS={'libdjvulibre-21.dll','libgcc_s_seh-1.dll','libstdc++-6.dll','libjpeg-8.dll','libwinpthread-1.dll'}
SYSTEM_DLLS={'advapi32.dll','kernel32.dll','msvcrt.dll','user32.dll','bcrypt.dll','shell32.dll','ole32.dll','ws2_32.dll','ntdll.dll'}
LIMIT=512*1024*1024


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def download(row,cache):
    target=cache/row['file']
    if target.exists():
        if digest(target)!=row['sha256']:raise ValueError('Cached package SHA256 mismatch: '+target.name)
        return target
    if not row['url'].startswith('https://repo.msys2.org/mingw/'):
        raise ValueError('Unexpected runtime package origin')
    with tempfile.NamedTemporaryFile(dir=cache,delete=False) as stream:
        staging=Path(stream.name)
        try:
            with urllib.request.urlopen(row['url'],timeout=60) as response:
                length=0
                while data:=response.read(1024*1024):
                    length+=len(data)
                    if length>LIMIT:raise ValueError('Runtime archive download budget')
                    stream.write(data)
            stream.flush()
            if digest(staging)!=row['sha256']:raise ValueError('Downloaded package SHA256 mismatch: '+target.name)
        except BaseException:
            stream.close();staging.unlink(missing_ok=True);raise
    staging.replace(target)
    return target


@contextmanager
def open_archive(path):
    # Streaming traversal: never extract arbitrary archive paths or symlinks.
    import zstandard
    with path.open('rb') as raw:
        with zstandard.ZstdDecompressor().stream_reader(raw) as stream:
            with tarfile.open(fileobj=stream,mode='r|') as archive:
                yield archive


def audit(directory):
    import pefile
    available={p.name.lower():p for p in directory.glob('*.dll')}
    if set(available)!=DLLS:raise ValueError('Missing/unexpected native runtime DLLs')
    result=[]
    for name,path in available.items():
        pe=pefile.PE(str(path))
        try:
            if pe.FILE_HEADER.Machine!=0x8664:raise ValueError('Non-x64 PE runtime: '+name)
            imports={row.dll.decode('ascii').lower() for row in getattr(pe,'DIRECTORY_ENTRY_IMPORT',[])}
            imports|={row.dll.decode('ascii').lower() for row in getattr(pe,'DIRECTORY_ENTRY_DELAY_IMPORT',[])}
            missing=imports-set(available)-SYSTEM_DLLS
            if missing:raise ValueError('Unresolved DLL imports: '+repr(missing))
            if name=='libdjvulibre-21.dll':
                symbols={row.name for row in pe.DIRECTORY_ENTRY_EXPORT.symbols}
                required={b'ddjvu_get_version_string',b'ddjvu_context_create',b'ddjvu_document_create_by_filename_utf8',b'ddjvu_page_render'}
                if not required<=symbols:raise ValueError('Public native decoder API missing')
            result.append({'file':name,'sha256':digest(path),'imports':sorted(imports),'machine':'AMD64'})
        finally:pe.close()
    return result


def prepare(destination,cache):
    rows=json.loads(LOCK.read_text());destination=destination.resolve();cache.mkdir(parents=True,exist_ok=True)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        manifest=destination/'SOURCE-MANIFEST.json'
        if not manifest.is_file():raise ValueError('Refuse to modify an unmanaged runtime directory')
        previous=json.loads(manifest.read_text())
        if previous['lock_sha256']!=digest(LOCK):raise ValueError('Existing runtime belongs to another lock; use a new directory')
        for file,expected in previous['retained_files'].items():
            if digest(destination/file)!=expected:raise ValueError('Existing runtime was modified: '+file)
        audit(destination);return
    with tempfile.TemporaryDirectory(prefix='djvu-runtime-',dir=destination.parent) as temporary:
        stage=Path(temporary)/'runtime';stage.mkdir()
        for row in rows:
            binary=download(row['binaries'],cache);source=download(row['source'],cache)
            shutil.copyfile(source,stage/('SOURCE-'+source.name))
            with open_archive(binary) as archive:
                for member in archive:
                    if not member.isfile():continue
                    path=Path(member.name)
                    is_dll=path.parent.as_posix()=='mingw64/bin' and path.name.lower() in DLLS
                    is_license=member.name.startswith('mingw64/share/licenses/')
                    if not (is_dll or is_license):continue
                    if member.size>32*1024*1024:raise ValueError('Extracted runtime file budget')
                    target=stage/(path.name if is_dll else 'LICENSE-'+row['name']+'-'+path.name)
                    data=archive.extractfile(member).read()
                    if target.exists() and target.read_bytes()!=data:raise ValueError('Conflicting runtime component')
                    target.write_bytes(data)
            print(row['name'],row['version'],'verified',flush=True)
        dlls=audit(stage)
        (stage/'SOURCE-LICENSES.md').write_text('''# Native runtime source and licenses

Unmodified native packages are from MSYS2. Matching source archives are supplied
as SOURCE-*.src.tar.zst, including their packaging recipes/patches. Binary package
hashes were checked against MSYS2 package pages; source hashes pin the HTTPS
retrieval. Original license texts inside source archives remain authoritative.
DjVuLibre: GPL. GCC libraries: GPL with GCC runtime exception and LGPL components.
libjpeg-turbo: BSD/IJG/zlib terms. winpthreads: MIT and BSD terms.
This directory is a native runtime dependency, not the standalone application.
Native Windows loading/rendering remains mandatory in the subsequent build gate.
''')
        manifest={'lock_sha256':digest(LOCK),'packages':rows,'dlls':dlls,'native_execution':'NOT_PROVEN',
                  'retained_files':{p.name:digest(p) for p in stage.iterdir() if p.is_file()}}
        (stage/'SOURCE-MANIFEST.json').write_text(json.dumps(manifest,indent=2))
        stage.rename(destination)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path,default=ROOT/'.build/djvu-runtime')
    parser.add_argument('--cache',type=Path,default=ROOT/'.build/windows-runtime-downloads')
    args=parser.parse_args();prepare(args.destination,args.cache)
