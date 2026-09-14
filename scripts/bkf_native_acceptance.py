"""Read-only evidence gate: original names/order, whole DjVu, native pixel comparison.

Requires diagnostic djvm/ddjvu, Pillow and optionally an independent DjVu decoder
executable accepting INPUT.djvu OUTPUT.pgm. No diagnostic tools enter production.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

from book2pdf.bkf.format import parse, transform
from book2pdf.bkf.native import Decoder


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--native-bin',type=Path,required=True)
    parser.add_argument('--independent-decoder',type=Path)
    parser.add_argument('--evidence',type=Path,required=True)
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=False)
    from PIL import Image
    evidence=[]
    for source in args.sources:
        raw=source.read_bytes();before=hashlib.sha256(raw).hexdigest();book=parse(raw)
        base=args.evidence/source.stem;base.mkdir();components=base/'components';components.mkdir()
        for entry in book.entries:
            if entry.kind=='UNKNOWN':continue
            assert transform(entry.decoded[:200],encode=True)+entry.decoded[200:]==entry.raw
            (components/entry.name).write_bytes(entry.decoded)
        bundle=base/'document.djvu'
        # djvm constructs only the bundle directory/outer envelope. Component names
        # are original directory names, and argument order is source page order.
        run=subprocess.run([str(args.native_bin/'djvm'),'-c',str(bundle),*[str(components/e.name) for e in book.pages]],capture_output=True,timeout=60)
        assert run.returncode==0,run.stderr
        def verify(pair):
            page,entry=pair;path=components/entry.name;decoder=Decoder()
            try:render=decoder.render(path)
            finally:decoder.close()
            assert render['bitonal'],'This acceptance comparator currently expects bitonal controls'
            expected=Image.frombytes('1',(render['width'],render['height']),render['pixels'],'raw','1;I',render['stride']).tobytes()
            row={'page':page,'entry':entry.name,'pixel_sha256':hashlib.sha256(render['pixels']).hexdigest(),'standalone':'PASS'}
            with tempfile.TemporaryDirectory() as temp:
                target=Path(temp)/'bundle.pbm'
                bundled=subprocess.run([str(args.native_bin/'ddjvu'),'-format=pbm',f'-page={page}',str(bundle),str(target)],capture_output=True,timeout=30)
                with Image.open(target) as image:equal=image.convert('1').tobytes()==expected
                row.update(bundle_exit=bundled.returncode,bundle_pixels_equal=equal)
                assert bundled.returncode==0 and equal,row
                if args.independent_decoder:
                    target=Path(temp)/'independent.pgm'
                    independent=subprocess.run([str(args.independent_decoder),str(path),str(target)],capture_output=True,timeout=30)
                    with Image.open(target) as image:equal=image.convert('1').tobytes()==expected
                    row.update(independent_exit=independent.returncode,independent_pixels_equal=equal,independent_diagnostics=independent.stderr.decode(errors='replace'))
                    assert independent.returncode==0 and equal,row
            return row
        with ThreadPoolExecutor(max_workers=4) as pool:
            rows=list(pool.map(verify,enumerate(book.pages,1)))
        result={'source':str(source),'sha256':before,'source_unchanged':hashlib.sha256(source.read_bytes()).hexdigest()==before,
                'wrapper_offset':book.base,'directory_entries':len(book.entries),'page_count':len(book.pages),
                'form_counts':dict(Counter(e.form for e in book.entries if e.form)),
                'chunk_counts':dict(Counter(c['type'] for e in book.entries for c in e.chunks)),
                'entry_counts':dict(Counter(e.kind for e in book.entries)),
                'entries':[e.evidence() for e in book.entries],'pages':rows,
                'bundle_synthesized':'FORM:DJVM and DIRM only; original component names and page order',
                'all_encoded_records_roundtrip':True}
        assert result['source_unchanged']
        (base/'evidence.json').write_text(json.dumps(result,indent=2))
        evidence.append({k:v for k,v in result.items() if k not in ('entries','pages')})
        print(source.name,len(rows),'all standalone, bundle and requested independent comparisons PASS',flush=True)
    (args.evidence/'summary.json').write_text(json.dumps(evidence,indent=2))


if __name__=='__main__':main()
