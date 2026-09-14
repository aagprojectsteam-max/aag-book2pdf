"""Small ctypes binding to DjVuLibre's public C API, used only in a limited child."""
import ctypes as c
import ctypes.util
import os
from pathlib import Path
import sys
import time
from functools import lru_cache


class NativeError(ValueError):
    def __init__(self, message, code='DOCUMENT_INVALID'):
        self.code=code
        super().__init__(message)


class Rect(c.Structure):
    _fields_=[('x',c.c_int),('y',c.c_int),('w',c.c_uint),('h',c.c_uint)]


class Message(c.Structure):
    _fields_=[('tag',c.c_int),('context',c.c_void_p),('document',c.c_void_p),
              ('page',c.c_void_p),('job',c.c_void_p),('message',c.c_char_p)]


def library_path():
    configured=os.environ.get('BOOK2PDF_DJVU_LIBRARY')
    if configured:return configured
    for root in (Path(getattr(sys,'_MEIPASS',Path(__file__).parent)),Path(__file__).parent/'runtime'):
        for name in ('libdjvulibre-21.dll','libdjvulibre.dll','djvulibre.dll','libdjvulibre.so.21','libdjvulibre.dylib'):
            if (root/name).is_file():return str(root/name)
    return ctypes.util.find_library('djvulibre')


class Decoder:
    def __init__(self):
        path=library_path()
        if not path:raise NativeError('DjVuLibre runtime missing; install libdjvulibre21 (Linux) or configure BOOK2PDF_DJVU_LIBRARY (Windows)', 'DEPENDENCY_MISSING')
        self.dll_directory = (os.add_dll_directory(str(Path(path).resolve().parent))
                              if os.name=='nt' and Path(path).is_file() else None)
        try:self.lib=c.CDLL(path)
        except OSError as exc:
            if self.dll_directory:self.dll_directory.close()
            raise NativeError('Cannot load DjVuLibre runtime: '+str(exc), 'RUNTIME_LOAD_FAILED') from exc
        V=c.c_void_p;I=c.c_int;U=c.c_uint;S=c.c_char_p
        declarations={
            'ddjvu_get_version_string':(S,[]), 'ddjvu_context_create':(V,[S]),
            'ddjvu_context_release':(None,[V]), 'ddjvu_message_peek':(V,[V]), 'ddjvu_message_pop':(None,[V]),
            'ddjvu_document_create_by_filename_utf8':(V,[V,S,I]), 'ddjvu_document_job':(V,[V]),
            'ddjvu_document_get_pagenum':(I,[V]), 'ddjvu_page_create_by_pageno':(V,[V,I]),
            'ddjvu_page_job':(V,[V]), 'ddjvu_job_status':(I,[V]), 'ddjvu_job_release':(None,[V]),
            'ddjvu_page_get_width':(I,[V]),'ddjvu_page_get_height':(I,[V]),
            'ddjvu_page_get_resolution':(I,[V]),'ddjvu_page_get_type':(I,[V]),
            'ddjvu_format_create':(V,[I,I,c.POINTER(U)]), 'ddjvu_format_release':(None,[V]),
            'ddjvu_format_set_row_order':(None,[V,I]), 'ddjvu_format_set_y_direction':(None,[V,I]),
            'ddjvu_page_render':(I,[V,I,c.POINTER(Rect),c.POINTER(Rect),V,c.c_ulong,V])}
        for name,(restype,args) in declarations.items():
            f=getattr(self.lib,name);f.restype=restype;f.argtypes=args
        self.version=self.lib.ddjvu_get_version_string().decode()
        self.context=self.lib.ddjvu_context_create(b'AAG Book2PDF')
        if not self.context:raise NativeError('Cannot create DjVu context')

    def close(self):
        if self.context:self.lib.ddjvu_context_release(self.context);self.context=None
        if self.dll_directory:self.dll_directory.close();self.dll_directory=None

    def messages(self):
        errors=[]
        for _ in range(100000):
            ptr=self.lib.ddjvu_message_peek(self.context)
            if not ptr:break
            message=c.cast(ptr,c.POINTER(Message)).contents
            if message.tag==0:errors.append((message.message or b'DjVu error').decode(errors='replace'))
            self.lib.ddjvu_message_pop(self.context)
        else:raise NativeError('DjVu message budget')
        if errors:raise NativeError('; '.join(errors))

    def wait(self,job):
        deadline=time.monotonic()+30
        while self.lib.ddjvu_job_status(job)<2:
            self.messages()
            if time.monotonic()>deadline:raise NativeError('DjVu decode timeout')
            time.sleep(.002)
        self.messages()
        if self.lib.ddjvu_job_status(job)!=2:raise NativeError('DjVu native decoding failed')

    def render(self,path):
        document=page=format_=None
        try:
            document=self.lib.ddjvu_document_create_by_filename_utf8(self.context,str(Path(path).resolve()).encode('utf-8'),0)
            if not document:raise NativeError('Cannot open recovered DjVu')
            self.wait(self.lib.ddjvu_document_job(document))
            if self.lib.ddjvu_document_get_pagenum(document)!=1:raise NativeError('Expected exactly one page per PAGE entry')
            page=self.lib.ddjvu_page_create_by_pageno(document,0)
            if not page:raise NativeError('Cannot create DjVu page')
            self.wait(self.lib.ddjvu_page_job(page))
            w=self.lib.ddjvu_page_get_width(page);h=self.lib.ddjvu_page_get_height(page)
            if not w or not h or w*h>64_000_000:raise NativeError('Native page dimension budget')
            # Preserve native scan pixels: packed one-bit for bitonal, RGB otherwise.
            bitonal=self.lib.ddjvu_page_get_type(page)==1
            style=6 if bitonal else 1;stride=(w+7)//8 if bitonal else w*3
            format_=self.lib.ddjvu_format_create(style,0,None)
            if not format_:raise NativeError('Cannot create DjVu pixel format')
            self.lib.ddjvu_format_set_row_order(format_,1);self.lib.ddjvu_format_set_y_direction(format_,1)
            rect=Rect(0,0,w,h);buffer=c.create_string_buffer(stride*h)
            if not self.lib.ddjvu_page_render(page,0,c.byref(rect),c.byref(rect),format_,stride,buffer):
                raise NativeError('DjVu did not render a complete page')
            self.messages()
            dpi=self.lib.ddjvu_page_get_resolution(page)
            if not 1<=dpi<=9600:raise NativeError('Invalid native resolution')
            return {'width':w,'height':h,'dpi':dpi,'bitonal':bitonal,'stride':stride,'pixels':buffer.raw}
        finally:
            if format_:self.lib.ddjvu_format_release(format_)
            if page:self.lib.ddjvu_job_release(self.lib.ddjvu_page_job(page))
            if document:self.lib.ddjvu_job_release(self.lib.ddjvu_document_job(document))


@lru_cache(maxsize=1)
def runtime_version():
    decoder = Decoder()
    try:
        return decoder.version
    finally:
        decoder.close()
