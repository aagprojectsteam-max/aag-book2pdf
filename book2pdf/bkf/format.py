"""Bounded BKF directory, prefix transform and recursive DjVu IFF parsing."""
from dataclasses import dataclass
import hashlib
import struct

from ..envelope import (transform, decode_record, parse_directory, EnvelopeError,
                        MAX_INPUT, MAX_ENTRIES, MAX_DIRECTORY, TRANSFORM_PREFIX_BYTES)


class BKFError(ValueError):
    pass


@dataclass
class Entry:
    index: int
    name: str
    offset: int
    stored_offset: int
    raw: bytes
    decoded: bytes
    kind: str
    form: str = ''
    chunks: tuple = ()
    dependencies: tuple = ()

    def evidence(self):
        return {'index':self.index, 'name':self.name, 'source_offset':self.offset,
                'stored_relative_offset':self.stored_offset, 'length':len(self.raw),
                'kind':self.kind, 'form':self.form, 'chunks':self.chunks,
                'dependencies':self.dependencies,
                'encoded_sha256':hashlib.sha256(self.raw).hexdigest(),
                'decoded_sha256':hashlib.sha256(self.decoded).hexdigest(),
                'unchanged_tail_bytes':max(0,len(self.raw)-TRANSFORM_PREFIX_BYTES),
                'opaque_hex':self.raw.hex() if self.kind=='UNKNOWN' else ''}


@dataclass
class Book:
    header: bytes
    directory_header: bytes
    base: int
    entries: list

    @property
    def pages(self):
        return [e for e in self.entries if e.kind=='PAGE']


def iff(data):
    """Inventory bounded nested chunks; lengths exclude inter-chunk alignment."""
    if len(data)<16 or data[:8]!=b'AT&TFORM':
        raise BKFError('Missing DjVu AT&T FORM header')
    if int.from_bytes(data[8:12],'big')+12!=len(data):
        raise BKFError('FORM extent differs from directory length')
    inventory=[];dependencies=[]
    def walk(start,end,depth):
        if depth>16:raise BKFError('IFF nesting limit')
        pos=start
        while pos<end:
            if pos&1:
                if data[pos]!=0:raise BKFError('Invalid IFF alignment')
                pos+=1
            if pos+8>end:raise BKFError('Truncated IFF chunk')
            code=data[pos:pos+4]
            if not all(32<=c<127 for c in code):raise BKFError('Invalid IFF identifier')
            size=int.from_bytes(data[pos+4:pos+8],'big');stop=pos+8+size
            if stop>end:raise BKFError('IFF chunk exceeds parent')
            if len(inventory)>=16384:raise BKFError('IFF chunk budget')
            row={'type':code.decode('ascii'),'offset':pos,'length':size,'depth':depth}
            inventory.append(row)
            if code in (b'FORM',b'LIST',b'PROP',b'CAT '):
                if size<4:raise BKFError('Missing nested FORM type')
                row['form']=data[pos+8:pos+12].decode('ascii',errors='strict')
                walk(pos+12,stop,depth+1)
            if code==b'INCL':
                try:ref=data[pos+8:stop].rstrip(b'\0').decode('ascii')
                except UnicodeError as exc:raise BKFError('Invalid dependency name') from exc
                if not safe_name(ref):raise BKFError('Unsafe dependency path')
                dependencies.append(ref)
            pos=stop
    walk(4,len(data),0)
    return data[12:16].decode('ascii'),tuple(inventory),tuple(dependencies)


def safe_name(name):
    # The same materialized resource names must be safe on Linux and Windows.
    reserved = {'CON', 'PRN', 'AUX', 'NUL'} | {f'{prefix}{n}' for prefix in ('COM', 'LPT') for n in range(1, 10)}
    return (bool(name) and len(name)<=240 and not name.endswith(('.', ' '))
            and name.split('.')[0].upper() not in reserved
            and all(32<=ord(c)<127 and c not in '/\\:<>"|?*' for c in name))


def parse(data):
    if len(data)<30 or len(data)>MAX_INPUT or data[:3]!=b'BKF':raise BKFError('Not a bounded BKF source')
    try: envelope = parse_directory(data)
    except EnvelopeError as exc: raise BKFError(str(exc)) from exc
    base = envelope.base
    entries=[];cursor=base;names=set()
    for row in envelope.entries:
        try:name=row.name.decode('ascii')
        except UnicodeError as exc:raise BKFError('Unsupported directory encoding') from exc
        if not safe_name(name) or name.casefold() in names:raise BKFError('Unsafe or duplicate directory name')
        names.add(name.casefold());stored,length=row.offset,row.length
        if length<1 or cursor+length>len(data):raise BKFError('Record extends beyond source')
        raw=data[cursor:cursor+length]
        # A zero-offset one-byte NUL entry is an opaque sentinel, not a page.
        # Retain its bytes and position. Never invent a blank scan for it.
        sentinel=stored==0 and raw==b'\0'
        if not sentinel and stored!=cursor-base:raise BKFError('Directory offset conflicts with sequential extent')
        if sentinel:
            entry=Entry(len(entries)+1,name,cursor,stored,raw,raw,'UNKNOWN')
        else:
            decoded=decode_record(raw)
            try:form,chunks,deps=iff(decoded)
            except (UnicodeError,ValueError) as exc:raise BKFError(f'Invalid DjVu entry {len(entries)+1}: {exc}') from exc
            kind={'DJVU':'PAGE','DJVI':'SHARED_RESOURCE','THUM':'THUMBNAIL','DJVM':'METADATA'}.get(form,'UNKNOWN')
            if form=='DJVI' and any(c['type']=='Djbz' for c in chunks):kind='SHARED_DICTIONARY'
            if form not in ('DJVU','DJVI','THUM'):raise BKFError('Nested/unknown document FORM requires another strategy')
            entry=Entry(len(entries)+1,name,cursor,stored,raw,decoded,kind,form,chunks,deps)
            if kind=='PAGE':
                info=[c for c in chunks if c['type']=='INFO' and c['depth']==1]
                if len(info)!=1 or info[0]['length']!=10:raise BKFError('Missing or duplicate INFO')
                off=info[0]['offset']+8;w,h=struct.unpack_from('>HH',decoded,off)
                if not w or not h or w*h>64_000_000:raise BKFError('Page dimension budget')
        entries.append(entry);cursor+=length
    if cursor!=len(data) or not entries:raise BKFError('Directory does not cover the complete source')
    by_name={e.name:e for e in entries}
    depths = {}
    def visit(entry, active):
        if entry.name in active or len(active)>16:raise BKFError('Cyclic/deep DjVu dependency')
        if entry.name in depths:return depths[entry.name]
        height = 0
        for ref in entry.dependencies:
            if ref not in by_name or by_name[ref].kind not in ('SHARED_RESOURCE','SHARED_DICTIONARY'):
                raise BKFError('Missing/invalid shared DjVu resource')
            height = max(height, 1 + visit(by_name[ref], active|{entry.name}))
        if height>16:raise BKFError('Cyclic/deep DjVu dependency')
        depths[entry.name] = height
        return height
    for entry in entries:visit(entry,set())
    book=Book(data[:7],envelope.directory_header,base,entries)
    if not book.pages:raise BKFError('No DjVu page entries')
    return book
