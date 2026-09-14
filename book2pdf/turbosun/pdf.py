"""Incremental PDF assembly preserving every original CCITT strip byte."""
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from PIL import Image
from ..supervised import checkpoint

def tiff_info(raw):
    with Image.open(io.BytesIO(raw)) as image:
        if image.format!='TIFF' or image.n_frames!=1:raise ValueError(f'Expected exactly one TIFF frame: record')
        w,h=image.size;tags=dict(image.tag_v2)
        if not (0<w<=50000 and 0<h<=50000 and w*h<=150000000):raise ValueError('TIFF dimensions outside bounds')
        if tags.get(259)!=4 or tuple(tags.get(258,(1,)))!=(1,) or tags.get(277,1)!=1:raise ValueError('Direct G4 embedding unavailable; no automatic transcode performed')
        if tags.get(266,1)!=1 or tags.get(274,1)!=1 or tags.get(262) not in (0,1) or tags.get(293,0)!=0:raise ValueError('Unsupported TIFF fill/orientation/photometric/G4 options')
        unit=tags.get(296,2)
        if unit not in (2,3) or 282 not in tags or 283 not in tags:raise ValueError('Missing/ambiguous physical TIFF resolution')
        dpi=[float(tags[282]),float(tags[283])]
        if unit==3:dpi=[d*2.54 for d in dpi]
        if not all(math.isfinite(d) and 1<=d<=10000 for d in dpi):raise ValueError('Invalid TIFF DPI')
        offsets=tags.get(273);lengths=tags.get(279);rows_per_strip=tags.get(278,h)
        if not offsets or len(offsets)!=len(lengths) or rows_per_strip<=0 or len(offsets)!=(h+rows_per_strip-1)//rows_per_strip:raise ValueError('Invalid TIFF strip map')
        strips=[];top=0
        for off,n in zip(offsets,lengths):
            rows=min(rows_per_strip,h-top)
            if n<=0 or off<8 or off+n>len(raw):raise ValueError('Strip outside TIFF')
            strips.append({'offset':off,'length':n,'rows':rows,'top':top,'sha256':hashlib.sha256(raw[off:off+n]).hexdigest()});top+=rows
    return raw,{'width':w,'height':h,'dpi':dpi,'photo':tags[262],'strips':strips,'sha256':hashlib.sha256(raw).hexdigest()}


class Writer:
    def __init__(self,stream):
        self.stream=stream;self.offsets=[0,0,0];self.pages=[]
        stream.write(b'%PDF-1.7\n%\xe2\xe3\xcf\xd3\n')
    def object(self,data,number=None):
        if number is None:number=len(self.offsets);self.offsets.append(0)
        self.offsets[number]=self.stream.tell()
        self.stream.write(f'{number} 0 obj\n'.encode()+data+b'\nendobj\n')
        return number
    def blob(self,data,dictionary=b''):
        return self.object(b'<< '+dictionary+f' /Length {len(data)} >>\nstream\n'.encode()+data+b'\nendstream')
    def page(self,raw,info):
        w,h=info['width'],info['height'];dx,dy=info['dpi'];pw,ph=w*72/dx,h*72/dy
        refs=[];ops=[]
        for i,s in enumerate(info['strips']):
            params=f'/Type /XObject /Subtype /Image /Width {w} /Height {s["rows"]} /BitsPerComponent 1 /ColorSpace /DeviceGray /Filter /CCITTFaxDecode /Interpolate false /DecodeParms << /K -1 /BlackIs1 {str(info["photo"]!=0).lower()} /Columns {w} /Rows {s["rows"]} >>'
            number=self.blob(raw[s['offset']:s['offset']+s['length']],params.encode())
            refs.append(f'/S{i} {number} 0 R')
            ops.append(f'q {pw:.10f} 0 0 {s["rows"]*72/dy:.10f} 0 {(h-s["top"]-s["rows"])*72/dy:.10f} cm /S{i} Do Q\n')
        content=self.blob(''.join(ops).encode())
        page=self.object(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {pw:.10f} {ph:.10f}] /Resources << /XObject << {" ".join(refs)} >> >> /Contents {content} 0 R >>'.encode())
        self.pages.append(page)
    def finish(self,record):
        self.object(b'<< /Type /Catalog /Pages 2 0 R >>',1)
        self.object(f'<< /Type /Pages /Count {len(self.pages)} /Kids [{" ".join(f"{n} 0 R" for n in self.pages)}] >>'.encode(),2)
        # PDF hex string is UTF16BE: provenance survives ordinary PDF opening/export.
        text=b'\xfe\xff'+json.dumps(record,ensure_ascii=False,separators=(',',':')).encode('utf-16-be')
        info=self.object(b'<< /Producer (AAG Book2PDF TurboSun original CCITT) /AAGBook2PDFProvenance <'+text.hex().encode()+b'> >>')
        xref=self.stream.tell()
        if xref>=10**10:raise ValueError('PDF exceeds classic xref offset limit')
        self.stream.write(f'xref\n0 {len(self.offsets)}\n0000000000 65535 f \n'.encode())
        for offset in self.offsets[1:]:self.stream.write(f'{offset:010d} 00000 n \n'.encode())
        self.stream.write(f'trailer\n<< /Size {len(self.offsets)} /Root 1 0 R /Info {info} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
        self.stream.flush();os.fsync(self.stream.fileno())


def validate_export(path,expected,progress=checkpoint):
    import pikepdf
    import pymupdf
    count=len(expected);streams=0
    with pikepdf.open(path,attempt_recovery=False) as pdf:
        if len(pdf.pages)!=count or pdf.get_warnings():raise ValueError('PDF structure/page count mismatch')
        for i,(page,entry) in enumerate(zip(pdf.pages,expected)):
            progress({'phase':'structure','done':i+1,'total':count})
            if set(page.Resources.XObject.keys())!={f'/S{n}' for n in range(len(entry['strips']))}:raise ValueError('PDF strip count mismatch')
            w,h=entry['width'],entry['height'];dx,dy=entry['dpi'];pw,ph=w*72/dx,h*72/dy
            if any(abs(float(a)-b)>.0001 for a,b in zip(page.MediaBox,[0,0,pw,ph])):raise ValueError('PDF page geometry changed')
            operations=''.join(f'q {pw:.10f} 0 0 {s["rows"]*72/dy:.10f} 0 {(h-s["top"]-s["rows"])*72/dy:.10f} cm /S{n} Do Q\n' for n,s in enumerate(entry['strips']))
            if page.Contents.read_bytes()!=operations.encode():raise ValueError('PDF strip placement changed')
            for n,original in enumerate(entry['strips']):
                obj=page.Resources.XObject[f'/S{n}']
                if str(obj.Filter)!='/CCITTFaxDecode' or hashlib.sha256(obj.read_raw_bytes()).hexdigest()!=original['sha256']:
                    raise ValueError('Original CCITT strip changed')
                dp=obj.DecodeParms
                if (obj.Width!=w or obj.Height!=original['rows'] or obj.BitsPerComponent!=1 or str(obj.ColorSpace)!='/DeviceGray' or dp.K!=-1 or dp.Columns!=w or dp.Rows!=original['rows'] or bool(dp.BlackIs1)!=(entry['photo']!=0)):
                    raise ValueError('PDF image parameters changed')
                streams+=1
    with pymupdf.open(path) as pdf:
        if pdf.is_repaired or len(pdf)!=count:raise ValueError('MuPDF rejected structure')
        for i,page in enumerate(pdf):
            progress({'phase':'render_validation','done':i+1,'total':count})
            pix=page.get_pixmap(matrix=pymupdf.Matrix(min(1,300/max(page.rect.width,page.rect.height)),min(1,300/max(page.rect.width,page.rect.height))),alpha=False)
            if not pix.width or not pix.height:raise ValueError('PDF page failed rendering')
            if expected[i].get('pixel_sha256'):
                images={item[7]:item[0] for item in page.get_images(full=True)};digest=hashlib.sha256()
                for n in range(len(expected[i]['strips'])):
                    decoded=pymupdf.Pixmap(pdf,images[f'S{n}'])
                    if decoded.colorspace.n!=1 or decoded.alpha:raise ValueError('Unexpected PDF image colorspace')
                    digest.update(decoded.samples)
                if digest.hexdigest()!=expected[i]['pixel_sha256']:raise ValueError('Independent TIFF/PDF decoded pixels differ')
            pymupdf.TOOLS.store_shrink(100)
    if shutil.which('qpdf'):
        check=subprocess.run(['qpdf','--check',str(path)],capture_output=True,text=True,timeout=300)
        if check.returncode:raise ValueError('qpdf validation failed: '+check.stderr[-1000:])
    return {'status':'PASS','mode':'all_pages','original_ccitt_streams_preserved':streams,'qpdf':'PASS' if shutil.which('qpdf') else 'NOT_AVAILABLE'}
