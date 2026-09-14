"""Research inspection tests; these fixtures do not establish a production format."""
import json
from pathlib import Path
import random
import subprocess
import sys
import zlib

import pytest
from book2pdf import bkf_records as research


def test_variable_intervals_contexts_and_plain_length_field(tmp_path):
    marker=b'RESEARCH!'
    parts=[]
    for size in (21,317,1091):
        length=len(marker)+2+size
        parts.append(marker+length.to_bytes(2,'little')+random.Random(size).randbytes(size))
    data=b'BKF'+b'x'*70+b''.join(parts)
    source=tmp_path/'בדיקה עם רווח.book';source.write_bytes(data)
    result=research.inspect_file(source,marker)
    expected=[73,73+len(parts[0]),73+len(parts[0])+len(parts[1])]
    assert result['offsets']==expected and result['marker_count']==3
    assert result['distance_distribution']['exact_frequency']=={len(parts[0]):1,len(parts[1]):1}
    assert result['contexts'][1]['before64_hex']==data[expected[1]-64:expected[1]].hex()
    assert result['contexts'][1]['after64_hex']==data[expected[1]+len(marker):expected[1]+len(marker)+64].hex()
    assert result['records'][-1]['boundary']=='EOF_CENSORED'
    field=next(f for f in result['integer_fields'] if f['relative_offset']==len(marker) and f['width']==2 and f['endian']=='little')
    assert field['values_in_occurrence_order']==list(map(len,parts))
    assert result['research_only'] and not result['content_decoded'] and not result['record_framing_proven']
    assert source.read_bytes()==data and result['source_unchanged']


def test_high_byte_model_is_inferred_not_fixed():
    marker=b'0123456789';parts=[]
    for size in (17,253,257,1023,1999,4097,8100):
        header=bytes([(size>>8)^0xa5,(size&255)^(((size>>8)*3+11)&255)])
        parts.append(marker+header+random.Random(size).randbytes(size))
    r=research.analyze_bytes(b'BKF'+b''.join(parts),marker)
    assert any(m['relative_offset']==10 and m['distance_adjustment']==12 and m['xor_constant_hex']=='a5' and m['matches']==6
               for m in r['high_byte_distance_hypotheses']['best_models'])
    assert not r['record_framing_proven']


def test_overlap_and_limits(monkeypatch):
    assert research.occurrences(b'aaaaaa',b'aaaa')==[0,1,2]
    r=research.analyze_bytes(b'aaaaaa',b'aaaa')
    assert r['contexts'][0]['before_truncated'] and r['contexts'][-1]['after_truncated']
    assert r['random_assessment']['log10_fixed_marker_tail_union_bound'] is None
    monkeypatch.setattr(research,'MAX_OCCURRENCES',2)
    with pytest.raises(ValueError,match='occurrence budget'):research.occurrences(b'aaaaaa',b'aaaa')
    with pytest.raises(ValueError,match='4..64'):research.analyze_bytes(b'BKF',b'x')


def test_codec_and_checksum_observations_do_not_authorize_decoding():
    marker=b'RESEARCH';content=b'example '*200
    r=research.analyze_bytes(marker+zlib.compress(content),marker)
    assert any(p['codec']=='zlib' and p['complete_interval'] for p in r['records'][0]['compression_probes'])
    assert not r['content_decoded'] and not r['record_framing_proven']
    body=zlib.crc32(content).to_bytes(4,'little')+content
    r=research.analyze_bytes(marker+body,marker)
    assert {'placement':'prefix','algorithm':'crc32','endian':'little'} in r['records'][0]['checksum_matches']


@pytest.mark.parametrize('codec',['zlib','gzip','raw-deflate','bzip2','xz'])
def test_invalid_codec_probe_is_a_recorded_rejection(codec):
    assert not research.compression_probe(b'not a valid compressed stream',0,codec)['complete_interval']


def test_cli_read_only_control_and_error(tmp_path):
    source=tmp_path/'BKC control.book';source.write_bytes(b'BKC'+b'x'*100)
    command=[sys.executable,'-m','book2pdf.cli','inspect-bkf-records']
    result=subprocess.run(command+[str(source)],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['observed_signature']=='BKC' and report['marker_count']==0 and report['source_unchanged']
    assert source.read_bytes()==b'BKC'+b'x'*100
    result=subprocess.run(command+[str(tmp_path/'missing.book')],capture_output=True,text=True,timeout=15)
    assert result.returncode==2 and 'Traceback' not in result.stderr
