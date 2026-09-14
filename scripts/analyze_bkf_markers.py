"""Reproduce cross-sample marker evidence; private reports only, no recovered PDFs."""
import argparse
from collections import Counter,defaultdict
import csv
import hashlib
import json
from pathlib import Path
import statistics

from book2pdf.bkf_records import LEAD_MARKER,inspect_bounded,column_profiles,entropy


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('files',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False,mode=0o700)
    reports=[];data={};summaries=[]
    for path in args.files:
        report=inspect_bounded(path);key=path.stem+'-'+report['source_sha256'][:8]
        (args.output/(key+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        with (args.output/(key+'-occurrences.csv')).open('w',newline='') as out:
            writer=csv.DictWriter(out,fieldnames=['index','offset','before64_hex','marker_hex','after64_hex','before_truncated','after_truncated'])
            writer.writeheader();writer.writerows(report['contexts'])
        reports.append(report);data[report['source_sha256']]=path.read_bytes()
        variants={}
        for start in range(3):
            token=LEAD_MARKER[start:start+8];positions=[];p=0
            while (p:=data[report['source_sha256']].find(token,p))>=0:positions.append(p);p+=1
            variants[token.hex()]={'count':len(positions),'same_occurrences_after_offset_normalization':positions==[p+start for p in report['offsets']]}
        summaries.append({'source':str(path),'signature':report['observed_signature'],'sha256':report['source_sha256'],
          'size':report['source_size'],'marker_count':report['marker_count'],'entropy':entropy(data[report['source_sha256']]),
          'distance_distribution':report['distance_distribution'],'variant_checks':variants,
          'random_assessment':report['random_assessment'],'high_byte_models':report['high_byte_distance_hypotheses'],
          'body_entropy':{'min':min((r['body_entropy'] for r in report['records']),default=None),
                          'max':max((r['body_entropy'] for r in report['records']),default=None),
                          'mean':statistics.mean([r['body_entropy'] for r in report['records']]) if report['records'] else None},
          'complete_compression_probes':sum(p['complete_interval'] for r in report['records'] for p in r['compression_probes']),
          'compression_probe_count':sum(len(r['compression_probes']) for r in report['records']),
          'image_probes':[p for r in report['records'] for p in r['image_probes']],
          'checksum_matches':sum(len(r['checksum_matches']) for r in report['records']),
          'strong_plain_integer_fields':[{k:f[k] for k in ('relative_offset','width','endian','relations')} for f in report['integer_fields'] if any(v['strong_candidate'] for v in f['relations'])],
          'markers_per_MiB':report['marker_count']/(report['source_size']/1048576),'source_unchanged':report['source_unchanged']})
    # Deduplicate content; duplicate source paths are not independent evidence.
    unique={r['source_sha256']:r for r in reports};positive=[r for r in unique.values() if r['marker_count']]
    common_models=None
    for report in positive:
        models={(m['relative_offset'],m['distance_adjustment'],m['xor_constant_hex']) for m in report['high_byte_distance_hypotheses']['best_models'] if m['matches']==m['tested']}
        common_models=models if common_models is None else common_models & models
    cross={'unique_samples':len(unique),'shared_perfect_high_byte_models':sorted(common_models or []),
           'record_framing_proven':False,'low_byte_model_caveat':'Empirical residual tables, not a decoder or cryptographic key; unseen high-byte values cannot be predicted'}
    tests=[];model_tables=[]
    if common_models:
        relative,adjustment,constant=sorted(common_models)[0]
        for train in positive:
            raw=data[train['source_sha256']];table=defaultdict(Counter)
            for a,b in zip(train['offsets'],train['offsets'][1:]):
                length=b-a-adjustment
                table[length>>8][raw[a+relative+1]^(length&255)]+=1
            modal={k:counts.most_common(1)[0][0] for k,counts in table.items()}
            model_tables.append({'training_source':train['source_path'],'observed_high_byte_groups':len(table),
                'groups':{k:dict(v) for k,v in table.items()}})
            for test in positive:
                if test is train:continue
                raw=data[test['source_sha256']];matches=0;covered=0;exceptions=[]
                for a,b in zip(test['offsets'],test['offsets'][1:]):
                    high=raw[a+relative]^int(constant,16)
                    if high not in modal:continue
                    covered+=1;predicted=(high<<8)+(raw[a+relative+1]^modal[high])+adjustment
                    if predicted==b-a:matches+=1
                    else:exceptions.append({'start':a,'next_marker':b,'observed_distance':b-a,
                        'predicted_distance':predicted,'unexplained_bytes_hex':raw[a+predicted:b].hex() if predicted<b-a else None})
                tests.append({'training_source':train['source_path'],'test_source':test['source_path'],
                    'covered_intervals':covered,'exact_predictions':matches,'exceptions':exceptions})
    cross.update(low_byte_tables=model_tables,cross_file_predictions=tests)
    contexts=defaultdict(list);pooled=[]
    for report in positive:
        for c in report['contexts']:
            raw=bytes.fromhex(c['before64_hex']+c['marker_hex']+c['after64_hex'])
            contexts[raw.hex()].append([report['source_path'],c['offset']]);pooled.append(raw)
    full=[c for c in pooled if len(c)==128+len(LEAD_MARKER)]
    joined=b''.join(full);stride=128+len(LEAD_MARKER)
    cross['pooled_context_columns']=column_profiles(joined,[i*stride+64 for i in range(len(full))],LEAD_MARKER)
    cross['repeated_full_contexts']={k:v for k,v in contexts.items() if len(v)>1}
    cross['page_correlation']='NOT_PROVEN: no independently observed page count for either BKF sample'
    cross['size_correlation']='Only two unique BKF samples: Pearson correlation is trivially +1; not inferential evidence'
    result={'marker':LEAD_MARKER.hex(),'samples':summaries,'cross_sample':cross}
    (args.output/'summary.json').write_text(json.dumps(result,indent=2))
    # Standard scientific plotting; no source scan images are created or altered.
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,axes=plt.subplots(2,2,figsize=(12,7))
        for report in positive:
            offsets=report['offsets'];gaps=[b-a for a,b in zip(offsets,offsets[1:])];label=Path(report['source_path']).name
            axes[0,0].hist(gaps,bins=range(0,65001,5000),histtype='step',label=label)
            axes[0,1].plot(range(1,len(gaps)+1),gaps,'.',markersize=2,label=label)
            axes[1,0].scatter(gaps,[r['body_entropy'] for r in report['records'][:-1]],s=4,label=label)
            raw=data[report['source_sha256']]
            axes[1,1].scatter([gap-12 for gap in gaps],[raw[p+10] for p in offsets[:-1]],s=4,label=label)
        labels=[('Inter-marker distance (bytes)','Count'),('Interval index','Distance (bytes)'),('Distance (bytes)','Body entropy (bits/byte)'),('Observed distance minus 12','Byte immediately after marker')]
        for axis,(x,y) in zip(axes.flat,labels):axis.set_xlabel(x);axis.set_ylabel(y);axis.legend();axis.grid(alpha=.2)
        fig.suptitle('BKF marker research: candidate framing, payload codec unknown');fig.tight_layout();fig.savefig(args.output/'marker-statistics.png',dpi=160);plt.close(fig)
    except ImportError:
        (args.output/'plot-unavailable.txt').write_text('Optional matplotlib not installed; full numerical evidence is in JSON.')
    for report in reports:
        assert hashlib.sha256(Path(report['source_path']).read_bytes()).hexdigest()==report['source_sha256']
    print(json.dumps({'samples':[(Path(s['source']).name,s['signature'],s['marker_count']) for s in summaries],
                      'shared_models':cross['shared_perfect_high_byte_models'],'cross_file_predictions':tests},indent=2))


if __name__=='__main__':
    import multiprocessing
    multiprocessing.freeze_support()
    main()
