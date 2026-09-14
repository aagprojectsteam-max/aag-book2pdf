"""Research-only BKF directory and arithmetic transform hypothesis.

No production detector imports this module. Structural agreement does not prove
JB2 decoding or the meaning of exceptional directory entries. Source bytes are
never rewritten. See BKF_FRAMING_RESEARCH.md for derivation and limitations.
"""
from collections import Counter
import hashlib
import struct
import zlib

MAX_INPUT = 64 * 1024 * 1024
MAX_DIRECTORY = 1024 * 1024
MAX_RECORDS = 4096
INITIAL_STATE = 0xDBE5
MULTIPLIER = 23421
INCREMENT = 9817


def transform(data, *, encode=False):
    """Length-preserving candidate feedback transform; reset for each object."""
    state = INITIAL_STATE
    result = bytearray()
    for value in data:
        plain = value if encode else value ^ (state >> 8)
        result.append(plain ^ (state >> 8) if encode else plain)
        state = (MULTIPLIER * ((state + plain) & 255) + INCREMENT) & 65535
    return bytes(result)


def djvu_structure(plain):
    """Bounded IFF structure check, explicitly NOT a JB2 image decoder."""
    if len(plain) < 16 or plain[:8] != b'AT&TFORM' or plain[12:16] != b'DJVU':
        raise ValueError('Candidate is not an AT&T FORM:DJVU record')
    size = int.from_bytes(plain[8:12], 'big')
    if size + 12 != len(plain):
        raise ValueError('FORM size does not match the directory record length')
    chunks = []
    cursor = 16
    while cursor < len(plain):
        if cursor & 1:
            if plain[cursor] != 0:
                raise ValueError('Nonzero inter-chunk alignment byte')
            cursor += 1
        if cursor + 8 > len(plain):
            raise ValueError('Truncated IFF chunk header')
        kind = plain[cursor:cursor+4]
        length = int.from_bytes(plain[cursor+4:cursor+8], 'big')
        end = cursor + 8 + length
        if end > len(plain) or not all(32 <= value < 127 for value in kind):
            raise ValueError('Invalid IFF chunk extent or identifier')
        chunks.append({'type':kind.decode('ascii'), 'offset':cursor, 'length':length})
        cursor = end  # Alignment belongs before a following chunk, not beyond EOF.
    if not chunks or chunks[0]['type'] != 'INFO' or chunks[0]['length'] != 10:
        raise ValueError('Expected a ten-byte INFO chunk')
    width, height = struct.unpack_from('>HH', plain, 24)
    if not width or not height:
        raise ValueError('Zero INFO dimensions')
    return {'form_size':size, 'chunks':chunks, 'width':width, 'height':height,
            'codec_candidate':'DjVu JB2' if any(c['type']=='Sjbz' for c in chunks) else 'UNKNOWN',
            'image_render_validated':False}


def _fingerprint(data):
    return {'sha256':hashlib.sha256(data).hexdigest(), 'first_32_bytes':data[:32].hex(),
            'last_32_bytes':data[-32:].hex()}


def inspect(data):
    """Return evidence even on rejection; never silently drop directory entries."""
    result = {'research_only':True, 'model':'BKF_ARITHMETIC_DIRECTORY_HYPOTHESIS_V1',
              'record_framing_proven':False, 'content_decoded':False,
              'production_recovery_authorized':False, 'payload_decoder':'NOT_VALIDATED',
              'unknown_fields':['file bytes 3..6', 'directory header bytes 4..13'],
              'transform':{'initial_state':INITIAL_STATE, 'multiplier':MULTIPLIER,
                           'increment':INCREMENT,
                           'rule':'p=c XOR (s>>8); s=(23421*((s+p)&255)+9817)&65535'}}
    try:
        _inspect(data, result)
    except (ValueError, struct.error) as exc:
        result['rejection'] = str(exc)
    return result


def _inspect(data, result):
    from .bkf_records import entropy, distribution, signatures, compression_probe
    if len(data) > MAX_INPUT or len(data) < 21 or data[:3] != b'BKF':
        raise ValueError('Not a bounded BKF directory candidate')
    # 3-byte signature + 4 unknown bytes; this is a candidate field layout, not
    # a sample-specific embedded payload offset. Directory size determines base.
    directory_size = int.from_bytes(transform(data[7:11]), 'little')
    base = 7 + 14 + directory_size
    if not 9 <= directory_size <= MAX_DIRECTORY or base >= len(data):
        raise ValueError('Directory length exceeds source or research budget')
    prefix = transform(data[7:base])
    result['prefix'] = {'encoded_offset':7, 'header_size':14, 'entry_bytes':directory_size,
                        'payload_base':base, 'header_hex':prefix[:14].hex(),
                        'opaque_file_field_hex':data[3:7].hex(), 'sha256':hashlib.sha256(prefix).hexdigest()}
    entries = []
    pos = 14
    while pos < len(prefix):
        if len(entries) >= MAX_RECORDS:
            raise ValueError('Directory entry budget exceeded')
        nul = prefix.find(b'\0', pos, min(pos+256, len(prefix)))
        if nul <= pos or nul+9 > len(prefix):
            raise ValueError('Truncated or overlong directory name/fields')
        name = prefix[pos:nul]
        if not all(32 <= c < 127 for c in name):
            raise ValueError('Non-ASCII directory name in this candidate layout')
        offset, length = struct.unpack_from('<II', prefix, nul+1)
        if not length:
            raise ValueError('Zero-length directory entry')
        entries.append({'record_index':len(entries)+1, 'directory_entry_offset':pos,
                        'label':name.decode('ascii'), 'stored_relative_offset':offset,
                        'total_length':length})
        pos = nul+9
    result['directory_entries'] = len(entries)
    if sum(e['total_length'] for e in entries) != len(data)-base:
        raise ValueError('Sequential directory lengths do not exactly cover data through EOF')
    result['sequential_lengths_cover_source'] = True
    cursor = base
    anomalies = []
    for row in entries:
        end = cursor+row['total_length']
        raw = data[cursor:end]
        candidate = transform(raw)
        row.update(marker_offset=cursor, header_hex=raw[:12].hex(),
                   payload_offset=cursor+12 if len(raw)>=12 else None,
                   payload_length=max(0,len(raw)-12), entropy=entropy(raw), **_fingerprint(raw))
        row['stored_offset_matches_sequential'] = base+row['stored_relative_offset']==cursor
        if not row['stored_offset_matches_sequential']:
            anomalies.append({'record_index':row['record_index'], 'label':row['label'],
                              'stored_offset':row['stored_relative_offset'],
                              'sequential_relative_offset':cursor-base,
                              'total_length':len(raw), 'raw_hex':raw[:64].hex(),
                              'interpretation':'NOT_PROVEN; retained, never skipped or rendered as a blank page'})
        try:
            row['djvu'] = djvu_structure(candidate)
            row['candidate_decoded_sha256'] = hashlib.sha256(candidate).hexdigest()
            payload = candidate[12:]
            row['payload'] = {**_fingerprint(payload), 'entropy':entropy(payload),
                              'encoded_entropy':entropy(raw[12:]),
                              'signatures':signatures(payload),
                              'offset_zero_probes':[compression_probe(payload,0,mode) for mode in
                                                   ('zlib','gzip','raw-deflate','bzip2','xz')]}
            row['payload']['checksum_matches'] = []
            for placement, stored, region in [('prefix',payload[:4],payload[4:]),
                                               ('suffix',payload[-4:],payload[:-4])]:
                for algorithm, func in [('crc32',zlib.crc32),('adler32',zlib.adler32)]:
                    for endian in ('little','big'):
                        if int.from_bytes(stored,endian)==func(region):
                            row['payload']['checksum_matches'].append([placement,algorithm,endian])
        except ValueError as exc:
            row['structure_error'] = str(exc)
            row['raw_non_djvu_hex'] = raw[:64].hex()
        cursor = end
    result['records'] = entries
    result['offset_anomalies'] = anomalies
    valid = [row for row in entries if 'djvu' in row]
    result['djvu_structural_records'] = len(valid)
    result['non_djvu_entries'] = len(entries)-len(valid)
    result['terminal'] = {'offset':entries[-1]['marker_offset'], 'end':cursor,
                          'eof_match':cursor==len(data),
                          'form_length_matches':bool('djvu' in entries[-1]),
                          'uses_next_marker':False}
    result['record_framing_proven'] = len(valid)==len(entries) and not anomalies
    result['framing_scope'] = 'Directory offsets, lengths, EOF and IFF extents only; no image decoding claim'
    result['payload_entropy'] = distribution([row['payload']['entropy'] for row in valid],bin_width=1)
    result['payload_lengths'] = distribution([row['payload_length'] for row in valid])
    result['shared_candidate_prefixes'] = Counter(row['payload']['first_32_bytes'][:24] for row in valid)
    result['shared_candidate_suffixes'] = Counter(row['payload']['last_32_bytes'][-16:] for row in valid)
    result['whole_file_checksum_matches'] = []
    for label, region in [('encoded_directory',data[7:base]),('candidate_directory',prefix),
                          ('encoded_records',data[base:]),('bytes_after_header',data[7:])]:
        for algorithm, func in [('crc32',zlib.crc32),('adler32',zlib.adler32)]:
            for endian in ('little','big'):
                if int.from_bytes(data[3:7],endian)==func(region):
                    result['whole_file_checksum_matches'].append([label,algorithm,endian])
