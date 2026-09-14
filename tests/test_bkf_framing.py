"""Synthetic structural fixtures, not claims about native JB2 decoding."""
import struct

import pytest
from book2pdf.bkf_framing import transform, inspect, djvu_structure
from book2pdf.bkf_records import LEAD_MARKER


def image(payload):
    info=struct.pack('>HH',640,480)+bytes.fromhex('18002c011601')
    body=b'DJVUINFO'+struct.pack('>I',len(info))+info+b'Sjbz'+struct.pack('>I',len(payload))+payload
    return b'AT&TFORM'+struct.pack('>I',len(body))+body


def book(records, anomalous=None):
    directory=b'';offset=0;encoded=[]
    for i, plain in enumerate(records):
        raw=transform(plain,encode=True) if plain!=b'\0' else plain
        directory+=f'entry-{i}-variable-name'.encode()+b'\0'+struct.pack('<II',0 if i==anomalous else offset,len(raw))
        offset+=len(raw);encoded.append(raw)
    prefix=struct.pack('<I',len(directory))+b'unknown!!!'+directory
    return b'BKF'+b'????'+transform(prefix,encode=True)+b''.join(encoded)


def test_transform_known_header_and_inverse():
    assert transform(b'AT&TFORM\0\0',encode=True)==LEAD_MARKER
    data=bytes(range(256))*4
    assert transform(transform(data,encode=True))==data


def test_variable_directory_lengths_and_terminal():
    records=[image(b'a'*21),image(b'b'*1031),image(b'c'*4107)]
    r=inspect(book(records))
    assert r['record_framing_proven'] and not r['content_decoded']
    assert r['directory_entries']==3 and r['djvu_structural_records']==3
    assert r['terminal']['eof_match'] and r['terminal']['form_length_matches']
    assert not r['terminal']['uses_next_marker']
    for row,plain in zip(r['records'],records):
        assert row['total_length']==len(plain)
        h=(len(plain)-12)>>8
        raw=transform(plain,encode=True)
        assert raw[10]^0xf1==h
        mask=((23421*((h+118)&255)+9817)&65535)>>8
        assert raw[11]^mask==(len(plain)-12)&255


def test_extra_zero_is_retained_as_directory_entry_not_stuffing():
    r=inspect(book([image(b'x'*91),b'\0',image(b'y'*260)],anomalous=1))
    assert r['sequential_lengths_cover_source']
    assert r['directory_entries']==3 and r['djvu_structural_records']==2
    assert r['records'][1]['raw_non_djvu_hex']=='00'
    assert len(r['offset_anomalies'])==1
    assert not r['record_framing_proven'] and not r['content_decoded']
    assert r['terminal']['form_length_matches']


def test_false_marker_in_payload_does_not_partition_records():
    plain=image(b'payload'*100)
    encoded=bytearray(transform(plain,encode=True))
    encoded[80:90]=LEAD_MARKER
    altered=transform(encoded)
    r=inspect(book([altered,image(b'terminal')]))
    assert r['record_framing_proven'] and r['directory_entries']==2


def test_full_uint32_form_length_not_ten_byte_marker_definition():
    r=inspect(book([image(b'large'*14000)]))
    assert r['record_framing_proven']
    assert r['records'][0]['djvu']['form_size']>65535


@pytest.mark.parametrize('mutate',[lambda b:b[:-1],lambda b:b[:35],lambda b:b+b'\0',
                                  lambda b:b[:3]+b'????'+b'\xff'*4+b[11:]])
def test_malformed_truncated_rejected(mutate):
    r=inspect(mutate(book([image(b'x'*90)])))
    assert not r['record_framing_proven'] and 'rejection' in r


def test_wrong_directory_offset_and_invalid_jb2_remain_truthful():
    r=inspect(book([image(b'NOT JB2'),image(b'ALSO NOT JB2')],anomalous=1))
    assert not r['record_framing_proven'] and not r['content_decoded']
    assert r['djvu_structural_records']==2  # The parser never asserts image validity.


def test_bkc_control_and_marker_do_not_override_signature():
    assert not inspect(b'BKC'+LEAD_MARKER*100)['record_framing_proven']


def test_chunk_bounds_and_odd_terminal_size():
    assert djvu_structure(image(b'x'*3))['chunks'][-1]['length']==3
    bad=bytearray(image(b'x'*3));bad[38:42]=struct.pack('>I',900)
    with pytest.raises(ValueError,match='extent'):djvu_structure(bad)
