#!/usr/bin/env python3
"""Research-only native check of candidate transforms. No recovered book output.

Use explicit paths to independent DjVuLibre binaries. Each temporary record is
removed. Native success is not treated as proof of pixel/content preservation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from book2pdf.bkf_framing import inspect, transform, MAX_INPUT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',type=Path,nargs='+')
    parser.add_argument('--ddjvu',type=Path,required=True)
    parser.add_argument('--djvudump',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():raise ValueError('Refusing to overwrite native evidence')
    results=[]
    with tempfile.TemporaryDirectory(prefix='aag-bkf-native-') as temporary:
        candidate=Path(temporary)/'candidate.djvu'
        for source in args.files:
            if source.stat().st_size>MAX_INPUT:raise ValueError('64 MiB source limit')
            raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
            report=inspect(raw)
            for row in report.get('records',[]):
                if 'djvu' not in row:continue
                p=row['marker_offset'];plain=transform(raw[p:p+row['total_length']])
                candidate.write_bytes(plain)
                result={'source':str(source),'source_sha256':digest,'record_index':row['record_index'],
                        'label':row['label'],'offset':p,'candidate_sha256':hashlib.sha256(plain).hexdigest(),
                        'pixel_preservation_proven':False}
                for label,command in [('structure',[str(args.djvudump),str(candidate)]),
                                       ('render',[str(args.ddjvu),'-format=pbm',str(candidate),os.devnull])]:
                    try:
                        process=subprocess.run(command,capture_output=True,timeout=30)
                        result[label]={'exit':process.returncode,'stdout':process.stdout[:8192].decode(errors='replace'),
                                       'stderr':process.stderr[:8192].decode(errors='replace')}
                    except subprocess.TimeoutExpired:
                        result[label]={'exit':None,'error':'30 second limit'}
                results.append(result)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=digest:raise ValueError('Source changed')
    with args.output.open('x',encoding='utf-8') as stream:json.dump(results,stream,indent=2)
    print(json.dumps({'records':len(results),'structure_exit_zero':sum(r['structure'].get('exit')==0 for r in results),
                      'render_exit_zero':sum(r['render'].get('exit')==0 for r in results),
                      'complete_decoder_proven':False}))


if __name__=='__main__':main()
