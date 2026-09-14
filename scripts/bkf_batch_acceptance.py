"""Real mixed-format recursive/resume gate; inputs are read-only symlink targets."""
import argparse
import json
from pathlib import Path
from book2pdf.batch import run_batch
from book2pdf.models import Options
from book2pdf.state import sha256


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('sources',type=Path,nargs='+')
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--expected',type=Path,required=True,help='JSON mapping input basename to [status, page_count]')
    parser.add_argument('--verify-existing',action='store_true')
    args=parser.parse_args()
    expected=json.loads(args.expected.read_text())
    if args.verify_existing:
        result=json.loads((args.evidence/'evidence.json').read_text());verify(result,expected);return
    args.evidence.mkdir(parents=True,exist_ok=False)
    books=args.evidence/'input';books.mkdir()
    before={str(p):sha256(p) for p in args.sources}
    for i,source in enumerate(args.sources):
        folder=books/f'group-{i%2}'/'nested';folder.mkdir(parents=True,exist_ok=True)
        (folder/source.name).symlink_to(source.resolve())
    (books/'unknown.book').write_bytes(b'UNKNOWN TEST CONTAINER\0'*20)
    options=Options(jobs=2,recursive=True,validate_all_pages=True,recovery='reconstruction-preview-opaque',analysis_dir=str(args.evidence/'analysis'))
    first=run_batch([books],args.evidence/'output',options)
    print(json.dumps(first),flush=True)
    resumed=run_batch([books],args.evidence/'output',options)
    unchanged={p:sha256(Path(p))==value for p,value in before.items()}
    result={'first':first,'resumed':resumed,'sources_unchanged':unchanged}
    (args.evidence/'evidence.json').write_text(json.dumps(result,indent=2))
    verify(result,expected)
    print(json.dumps(result),flush=True)


def verify(result,expected):
    first=result['first'];resumed=result['resumed']
    rows=[json.loads(line) for line in Path(first['REPORT']).read_text().splitlines()]
    actual={Path(r['source_path']).name:[r['status'],r['page_count']] for r in rows if 'source_path' in r}
    assert actual==dict(expected,**{'unknown.book':['UNSUPPORTED_AFTER_ANALYSIS',0]}),actual
    successful=sum(status in ('PASS_EXACT','PASS_REPAIRED','PASS_DECODED','RECONSTRUCTED_PREVIEW') for status,count in expected.values())
    assert first['PASS']+first['PREVIEW']==successful and first['FAILED']==0,first
    assert resumed['SKIPPED']==successful and resumed['FAILED']==0,resumed
    assert resumed['UNSUPPORTED']==len(expected)+1-successful,resumed
    assert all(result['sources_unchanged'].values())
    print('MIXED_RECURSIVE_BATCH_AND_RESUME=PASS',flush=True)


if __name__=='__main__':main()
