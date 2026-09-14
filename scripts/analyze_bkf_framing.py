#!/usr/bin/env python3
"""Reproduce read-only framing evidence; run from repository with .venv Python.

Outputs JSON only. No native image decoder is invoked by this bounded inspector.
Private outputs contain source-derived metadata. Never use this as conversion.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from book2pdf.bkf_framing import inspect, transform
from book2pdf.bkf_records import LEAD_MARKER, occurrences, entropy, distribution


def analyze(source):
    data=source.read_bytes()
    digest=hashlib.sha256(data).hexdigest()
    framed=inspect(data)
    positions=occurrences(data,LEAD_MARKER)
    rows=[]
    for i,p in enumerate(positions):
        end=positions[i+1] if i+1<len(positions) else len(data)
        header=transform(data[p:p+12])
        size=int.from_bytes(header[8:12],'big')
        row={'index':i+1,'marker_offset':p,'before16':data[max(0,p-16):p].hex(),
             'marker':data[p:p+10].hex(),'after22':data[p+10:p+32].hex(),
             'distance':end-p,'candidate_form_length':size,'unaccounted_bytes':end-p-size-12,
             'terminal':i+1==len(positions),
             'high_byte_match':data[p+10]^0xf1==(end-p-12)>>8,
             'low_byte_match':size==(end-p-12)}
        rows.append(row)
    payloads=[];dims=Counter();repeat_blocks=Counter();suffixes=Counter();prefixes=Counter()
    for row in framed.get('records',[]):
        if 'djvu' not in row:continue
        p=row['marker_offset'];plain=transform(data[p:p+row['total_length']]);body=plain[12:]
        payloads.append(body);dims[(row['djvu']['width'],row['djvu']['height'])]+=1
        repeat_blocks.update(set(body[j:j+16] for j in range(16,len(body)-15,16)))
        prefixes[body[:16].hex()]+=1;suffixes[body[-16:].hex()]+=1
    pairs=Counter(zip(data,data[1:]));zeros=data.count(0)
    zero_neighbors=[]
    for byte in sorted(set(LEAD_MARKER)|{0,0xf1}):
        count=data.count(byte);expected=count*zeros/len(data)
        zero_neighbors.append({'byte':byte,'count':count,'followed_by_zero':pairs[byte,0],
                               'preceded_by_zero':pairs[0,byte],
                               'iid_marginal_expected':expected,
                               'descriptive_only_not_independence_test':True})
    source_unchanged=hashlib.sha256(source.read_bytes()).hexdigest()==digest
    return {'source':str(source),'size':len(data),'sha256':digest,'source_unchanged':source_unchanged,
            'framing':framed,'marker_headers':rows,
            'complete_intervals':max(0,len(positions)-1),
            'high_byte_matches':sum(r['high_byte_match'] for r in rows if not r['terminal']),
            'low_byte_matches_without_directory_entry':sum(r['low_byte_match'] for r in rows if not r['terminal']),
            'zero_neighbors':zero_neighbors,'zero_count':zeros,
            'image_dimensions':[{'width':w,'height':h,'count':n} for (w,h),n in dims.most_common()],
            'payload_entropy':distribution([entropy(p) for p in payloads],bin_width=1),
            'payload_prefix16':prefixes,'payload_suffix16':suffixes,
            'shared_aligned_16byte_blocks':[{'hex':b.hex(),'records':n} for b,n in repeat_blocks.most_common(20) if n>1],
            'codec_limitations':{'zstd':'Signature scan only; no zstd signature at payload zero',
                                 'brotli':'Not probed: no unambiguous signature; no bounded decoder installed',
                                 'lzma':'XZ container tested; LZMA-alone not separately decoded',
                                 'native_djvu':'See native-validation.json; header agreement alone is insufficient'}}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',type=Path,nargs='+')
    parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    before=[]
    for source in args.sources:
        if source.stat().st_size>64*1024*1024:raise ValueError('64 MiB input budget exceeded')
        result=analyze(source)
        # Content-hash names; source directory names never control extraction paths.
        target=args.output_dir/(result['sha256'][:16]+'.json')
        if target.resolve()==source.resolve() or target.exists():raise ValueError('Refusing existing output')
        with target.open('x',encoding='utf-8') as stream:json.dump(result,stream,indent=2)
        before.append({k:result[k] for k in ('source','size','sha256','source_unchanged')})
        print(source.name,'markers',len(result['marker_headers']),'entries',result['framing'].get('directory_entries'),
              'framing_proven',result['framing']['record_framing_proven'],flush=True)
    target=args.output_dir/'sources.json'
    with target.open('x',encoding='utf-8') as stream:json.dump(before,stream,indent=2)


if __name__=='__main__':main()
