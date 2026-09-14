"""Read-only real corpus acceptance through production worker and direct exports."""
import argparse
import json
import multiprocessing
from pathlib import Path
import subprocess
import time


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--evidence',required=True,type=Path);parser.add_argument('sources',nargs='+',type=Path)
    args=parser.parse_args();args.evidence.mkdir(parents=True,exist_ok=True)
    from book2pdf.worker import convert
    from book2pdf.models import Options
    from book2pdf.state import sha256
    from book2pdf.container_decode import extract_pdf
    from book2pdf.export import export_pages,page_fingerprint
    import pikepdf
    rows=[]
    for source in args.sources:
        base=args.evidence/source.stem;base.mkdir(exist_ok=True)
        before=sha256(source);out=base/'full.pdf'
        r=convert(source,out,Options(validate_all_pages=True,overwrite=True),base/'.book2pdf-state.sqlite3')
        row=r.to_dict();rows.append(row)
        (args.evidence/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
        print(source.name,r.status,r.page_count,r.error,flush=True)
        assert r.status=='PASS_DECODED',r.error
        if r.recovery_class=='DECODED_CONTAINER_CONTENT':
            component,e=extract_pdf(source,base)
            assert component.read_bytes()==out.read_bytes()
            component.unlink()
            assert not r.assumptions and not r.reconstructed_objects
            row['full_component_byte_identity']='PASS'
            with pikepdf.open(out,attempt_recovery=False) as pdf:
                row['original_stream_hashes']={str(obj.objgen):__import__('hashlib').sha256(obj.read_raw_bytes()).hexdigest() for obj in pdf.objects if isinstance(obj,pikepdf.Stream)}
        check=subprocess.run(['qpdf','--check',str(out)],capture_output=True,text=True)
        row['qpdf']={'returncode':check.returncode,'output':check.stdout+check.stderr};assert check.returncode==0
        for spec in ('1','1-10','1,3,5','1-5,8,10-20',str(r.page_count//2)):
            target=base/(spec+'.pdf');partial=export_pages(out,target,spec,overwrite=True,recovery_record=r.to_dict())
            assert partial.status=='PASS_DECODED',partial.error
            row.setdefault('exports',[]).append({'range':spec,'count':partial.page_count,'page_streams_preserved':partial.preservation['page_streams_byte_identical'],'model':partial.recovered_document['provenance'].get('document_relation')})
        row['source_unchanged']=sha256(source)==before;assert row['source_unchanged']
        (args.evidence/'results.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2))
    return 0


if __name__=='__main__':
    multiprocessing.freeze_support();raise SystemExit(main())
