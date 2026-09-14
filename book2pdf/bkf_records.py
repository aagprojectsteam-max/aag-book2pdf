"""Research-only marker/interval inspection. Never authorizes document recovery.

The default token is a user-supplied lead, not a BKF signature or decoder rule.
All offsets are zero-based; intervals are end-exclusive. Spacing is variable.
"""
from collections import Counter
import hashlib
import lzma
import math
from pathlib import Path
import statistics
import zlib

LEAD_MARKER = bytes.fromhex('9aee1a5290efe8c81b51')
MAX_INPUT = 64 * 1024 * 1024
MAX_OCCURRENCES = 4096
MAX_DECOMPRESSED = 256 * 1024
MAGICS = {'gzip': b'\x1f\x8b\x08', 'bzip2': b'BZh', 'xz': b'\xfd7zXZ\x00',
          'zip': b'PK\x03\x04', 'jpeg': b'\xff\xd8\xff', 'png': b'\x89PNG\r\n\x1a\n',
          'jbig2': b'\x97JB2\r\n\x1a\n', 'pdf': b'%PDF-', 'tiff_le': b'II\x2a\x00',
          'tiff_be': b'MM\x00\x2a', 'zstd': b'\x28\xb5\x2f\xfd'}


def occurrences(data, marker):
    positions = []
    start = 0
    while (offset := data.find(marker, start)) >= 0:
        if len(positions) >= MAX_OCCURRENCES:
            raise ValueError('Marker occurrence budget exceeded; no partial report presented as complete')
        positions.append(offset)
        start = offset + 1  # Include overlaps; never assume fixed spacing.
    return positions


def entropy(data):
    n = len(data)
    return -sum((v/n)*math.log2(v/n) for v in Counter(data).values()) if n else 0.0


def distribution(values, bin_width=5000):
    if not values:
        return {'count': 0}
    frequencies = Counter(values)
    return {'count': len(values), 'min': min(values), 'max': max(values),
            'mean': statistics.mean(values), 'median': statistics.median(values),
            'stdev': statistics.pstdev(values), 'bin_width': bin_width,
            'histogram': dict(sorted(Counter((n//bin_width)*bin_width for n in values).items())),
            'exact_frequency': dict(sorted(frequencies.items())),
            'repeated_distances': {k:v for k,v in sorted(frequencies.items()) if v > 1}}


def column_profiles(data, positions, marker):
    columns = []
    for relative in range(-64, len(marker)+64):
        values = [data[p+relative] for p in positions if 0 <= p+relative < len(data)]
        if not values:
            continue
        counts = Counter(values)
        columns.append({'relative_offset': relative, 'count': len(values), 'distinct_values': len(counts),
                        'entropy': entropy(values), 'top_values': counts.most_common(5),
                        'bit_one_counts_lsb_first': [sum((v >> bit)&1 for v in values) for bit in range(8)],
                        'conditioned_on_marker': 0 <= relative < len(marker)})
    return columns


def integer_fields(data, positions, marker):
    """All unsigned fields in the immediate 32-byte flanks; no guessed transforms."""
    fields = []
    targets = {
        'next_distance': [b-a for a,b in zip(positions,positions[1:])] + [None],
        'previous_distance': [None]+[b-a for a,b in zip(positions,positions[1:])],
        'next_absolute_offset': positions[1:]+[None],
        'zero_based_sequence': list(range(len(positions))),
        'one_based_sequence': list(range(1,len(positions)+1)),
    }
    for width in (2,4,8):
        for relative in list(range(-32,1-width))+list(range(len(marker),len(marker)+33-width)):
            for endian in ('little','big'):
                values = [int.from_bytes(data[p+relative:p+relative+width],endian)
                          if 0 <= p+relative and p+relative+width <= len(data) else None for p in positions]
                valid = [v for v in values if v is not None]
                evidence = []
                for name, target in targets.items():
                    pairs = [(v,t) for v,t in zip(values,target) if v is not None and t is not None]
                    if not pairs:
                        continue
                    deltas = Counter(v-t for v,t in pairs if abs(v-t) <= 64)
                    if deltas:
                        delta,count = deltas.most_common(1)[0]
                        evidence.append({'target': name, 'delta': delta, 'matches': count, 'tested': len(pairs),
                                         'strong_candidate': count >= 3 and count/len(pairs) >= .9})
                correlation = None
                pairs = [(v,t) for v,t in zip(values,targets['next_distance']) if v is not None and t is not None]
                if len(pairs) > 2 and len({v for v,t in pairs}) > 1 and len({t for v,t in pairs}) > 1:
                    correlation = statistics.correlation([v for v,t in pairs],[t for v,t in pairs])
                fields.append({'relative_offset': relative, 'width': width, 'endian': endian,
                    'values_in_occurrence_order': values, 'distinct': len(set(valid)), 'relations': evidence,
                    'next_distance_pearson_r': correlation,
                    'values_in_dimension_range_16_16384': sum(16 <= v <= 16384 for v in valid),
                    'dimension_interpretation': 'NOT_PROVEN'})
    return fields


def high_byte_hypotheses(data, positions, marker):
    """Explore a small, disclosed model grid; matches are not a decoded length.

    Offset, adjustment and XOR value are inferred from data, never used to move
    between records. Marker scanning remains the only partition hypothesis.
    """
    rows = [(a,b-a) for a,b in zip(positions,positions[1:])]
    best = []; score = 0
    for relative in range(len(marker),len(marker)+4):
        for adjustment in range(-32,65):
            pairs = [(data[a+relative],(gap-adjustment)>>8) for a,gap in rows
                     if a+relative < len(data) and 0 <= gap-adjustment < 65536]
            if not pairs:
                continue
            value,count = Counter(v^high for v,high in pairs).most_common(1)[0]
            if count > score:
                score = count; best = []
            if count == score:
                best.append({'relative_offset':relative,'distance_adjustment':adjustment,
                    'xor_constant_hex':f'{value:02x}','matches':count,'tested':len(pairs)})
    return {'model':'byte_at_relative_offset XOR constant == ((next_distance - adjustment) >> 8)',
            'grid':'first four post-marker bytes, adjustment -32..64, all 256 XOR constants by counting',
            'best_models':best,'post_selection_in_sample':True,'not_a_key_or_decoder':True}


def signatures(data):
    result = {}
    for name, magic in MAGICS.items():
        found = []; start = 0; count = 0
        while (offset := data.find(magic,start)) >= 0:
            count += 1
            if len(found) < 8:
                found.append(offset)
            start = offset+1
        if count:
            result[name] = {'count': count, 'first_relative_offsets': found}
    return result


def compression_probe(data, offset, mode):
    """Bounded codec probe; no decoded content is published or used for recovery."""
    try:
        if mode == 'bzip2':
            import bz2
            decoder = bz2.BZ2Decompressor()
        elif mode == 'xz':
            decoder = lzma.LZMADecompressor(memlimit=128*1024*1024)
        else:
            decoder = zlib.decompressobj({'zlib':15,'gzip':31,'raw-deflate':-15}[mode])
        out = decoder.decompress(data[offset:], MAX_DECOMPRESSED+1)
        return {'offset':offset,'codec':mode,'decoded_bytes':len(out),
                'complete_interval': decoder.eof and not decoder.unused_data and len(out) <= MAX_DECOMPRESSED,
                'eof':decoder.eof,'trailing_bytes':len(decoder.unused_data),
                'output_limit_reached':len(out)>MAX_DECOMPRESSED,
                'decoded_prefix_hex':out[:16].hex() if decoder.eof else ''}
    except (zlib.error, lzma.LZMAError, ValueError, OSError, EOFError):
        return {'offset':offset,'codec':mode,'complete_interval':False,'error':'invalid stream'}


def record_info(data, start, end, number, marker_size, terminal):
    raw = data[start:end]; body = raw[marker_size:]
    hashes = {'sha256':hashlib.sha256(raw).hexdigest(), 'crc32_fingerprint':f'{zlib.crc32(raw):08x}'}
    checksums = []
    # Test whole body with stored 32-bit prefix or suffix, in both byte orders.
    if len(body) >= 4:
        for placement, stored, payload in [('prefix',body[:4],body[4:]),('suffix',body[-4:],body[:-4])]:
            for name,func in [('crc32',zlib.crc32),('adler32',zlib.adler32)]:
                for endian in ('little','big'):
                    if int.from_bytes(stored,endian) == func(payload):
                        checksums.append({'placement':placement,'algorithm':name,'endian':endian})
    probes = [compression_probe(raw, offset, mode) for offset in (marker_size,marker_size+2,marker_size+4)
              if offset < len(raw) for mode in ('zlib','gzip','raw-deflate')]
    found = signatures(body)
    for kind in ('gzip','bzip2','xz'):
        for offset in found.get(kind,{}).get('first_relative_offsets',[]):
            probes.append(compression_probe(raw,marker_size+offset,kind))
    image_probes = []
    for kind in ('jpeg','png','tiff_le','tiff_be'):
        for offset in found.get(kind,{}).get('first_relative_offsets',[]):
            try:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(body[offset:])) as image:
                    if image.width*image.height > 16*1024*1024:
                        raise ValueError('Image pixel budget exceeded')
                    image.verify()
                    image_probes.append({'signature':kind,'body_offset':offset,'verified':True,'size':list(image.size)})
            except Exception as exc:
                image_probes.append({'signature':kind,'body_offset':offset,'verified':False,'reason':type(exc).__name__})
    return {'index':number,'start':start,'end':end,'length':end-start,
            'boundary':'EOF_CENSORED' if terminal else 'NEXT_MARKER_CANDIDATE',
            'body_entropy':entropy(body), 'first32_hex':raw[:32].hex(),'last32_hex':raw[-32:].hex(),
            'prefix_after_marker_hex':body[:16].hex(),'suffix16_hex':raw[-16:].hex(),
            'signatures':found,'compression_probes':probes,'image_probes':image_probes,'checksum_matches':checksums,
            'checksum_scope':'32-bit prefix/suffix of entire post-marker body; other models NOT_PROVEN',**hashes}


def random_assessment(data, marker, positions):
    n = len(data); k = len(positions); length = len(marker)
    probability = 256.0**(-length)
    counts = Counter(data)
    empirical = math.prod(counts[b]/n for b in marker) if n else 0
    # Union bound over all sets of k non-overlapping occurrences, relaxed to
    # choose(N,k). Independence applies to disjoint windows under the stated IID null.
    # If this marker overlaps itself in the observed sample, do not apply this bound.
    bound = None
    disjoint = all(b-a >= length for a,b in zip(positions,positions[1:]))
    if k and disjoint:
        log_choose = sum(math.log10((max(0,n-length+1)-i)/(i+1)) for i in range(k))
        bound = min(0,log_choose-k*length*math.log10(256))
    return {'null_model':'independent uniformly distributed bytes; not a universal model of compressed/ciphertext data',
            'expected_fixed_marker_occurrences':max(0,n-length+1)*probability,
            'empirical_byte_frequency_iid_expected':max(0,n-length+1)*empirical,
            'observed_occurrences_nonoverlapping':disjoint,
            'log10_fixed_marker_tail_union_bound':bound,
            'log10_any_marker_selection_adjusted_bound':min(0,bound+length*math.log10(256)) if bound is not None else None,
            'selection_caveat':'Lead was discovered from these data; any-marker bound corrects token selection only, not all analysis choices',
            'interpretation':'Strong repetition evidence, not proof of delimiter semantics, encryption or keys' if k > 2 else 'No high repetition detected'}


def analyze_bytes(data, marker=LEAD_MARKER):
    if not 4 <= len(marker) <= 64:
        raise ValueError('Research marker must contain 4..64 bytes')
    if len(data) > MAX_INPUT:
        raise ValueError('Research input limit is 64 MiB')
    positions = occurrences(data,marker)
    contexts = [{'index':i+1,'offset':p,'before64_hex':data[max(0,p-64):p].hex(),
                 'marker_hex':marker.hex(),'after64_hex':data[p+len(marker):p+len(marker)+64].hex(),
                 'before_truncated':p<64,'after_truncated':p+len(marker)+64>len(data)} for i,p in enumerate(positions)]
    records = [record_info(data,p,positions[i+1] if i+1<len(positions) else len(data),i+1,len(marker),i+1==len(positions))
               for i,p in enumerate(positions)]
    clusters = {}
    for field in ('length','prefix_after_marker_hex','suffix16_hex','sha256'):
        groups = {}
        for row in records:
            groups.setdefault(str(row[field]),[]).append(row['index'])
        clusters[field] = {'unique_clusters':len(groups),'repeated_groups':{k:v for k,v in groups.items() if len(v)>1}}
    clusters['length_5k_bins'] = {}
    for row in records:
        clusters['length_5k_bins'].setdefault(str((row['length']//5000)*5000),[]).append(row['index'])
    return {'schema':1,'research_only':True,'record_framing_proven':False,'content_decoded':False,
        'observed_signature_hex':data[:3].hex(),'observed_signature':data[:3].decode('ascii',errors='replace'),
        'source_size':len(data),'source_sha256':hashlib.sha256(data).hexdigest(),'marker_hex':marker.hex(),
        'marker_is_research_lead_not_format_definition':True,'marker_count':len(positions),'offsets':positions,
        'contexts':contexts,'context_columns':column_profiles(data,positions,marker),
        'distance_distribution':distribution([b-a for a,b in zip(positions,positions[1:])]),
        'offset_residues':{str(mod):dict(Counter(p%mod for p in positions)) for mod in (2,4,8,16,32)},
        'records':records,'complete_intermarker_intervals':max(0,len(positions)-1),
        'unframed_prefix':{'start':0,'end':positions[0] if positions else len(data)},
        'terminal_interval_is_censored':bool(positions),'clusters':clusters,
        'integer_fields':integer_fields(data,positions,marker) if positions else [],
        'high_byte_distance_hypotheses':high_byte_hypotheses(data,positions,marker),
        'random_assessment':random_assessment(data,marker,positions),
        'limits':{'input_bytes':MAX_INPUT,'occurrences':MAX_OCCURRENCES,'decoded_bytes_per_probe':MAX_DECOMPRESSED,
                  'probe_offsets_after_marker':[0,2,4],'probe_codecs':['zlib','gzip','raw-deflate']}}


def inspect_file(source, marker=LEAD_MARKER):
    source = Path(source).resolve(strict=True)
    if not source.is_file() or source.stat().st_size > MAX_INPUT:
        raise ValueError('Expected a regular source file no larger than 64 MiB')
    with source.open('rb') as stream:
        data = stream.read(MAX_INPUT+1)
    result = analyze_bytes(data,marker)
    from .bkf_framing import inspect
    result['framing_analysis'] = inspect(data)
    result['record_framing_proven'] = result['framing_analysis']['record_framing_proven']
    result['content_decoded'] = False  # IFF headers do not validate JB2 pixels.
    # Read only a second time to establish source identity after analysis.
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):
            digest.update(block)
    result.update(source_path=str(source),source_unchanged=digest.hexdigest()==result['source_sha256'])
    if not result['source_unchanged']:
        raise ValueError('Source changed during read-only investigation')
    return result


def _inspection_child(source, marker, result_path):
    import json
    from .research_limits import constrain
    try:
        constrain(2*1024*1024*1024,60)
        result = inspect_file(source,marker)
        encoded = json.dumps(result,ensure_ascii=False).encode('utf-8')
        if len(encoded) > 64*1024*1024:
            raise ValueError('Research report exceeds 64 MiB budget')
        Path(result_path).write_bytes(encoded)
    except Exception as exc:
        Path(result_path).write_text(json.dumps({'error':str(exc)}),encoding='utf-8')


def inspect_bounded(source, marker=LEAD_MARKER):
    import json
    import multiprocessing
    import tempfile
    with tempfile.TemporaryDirectory(prefix='book2pdf-record-research-') as scratch:
        target = Path(scratch)/'report.json'
        process = multiprocessing.get_context('spawn').Process(target=_inspection_child,args=(str(source),marker,str(target)))
        process.start();process.join(90)
        if process.is_alive():
            process.terminate();process.join(2)
            if process.is_alive():
                process.kill();process.join()
        if process.exitcode != 0 or not target.is_file():
            raise ValueError('Record research exceeded its budget or could not inspect this input; no decoding attempted')
        result = json.loads(target.read_text(encoding='utf-8'))
        if 'error' in result:
            raise ValueError(result['error'])
        return result


def command(argv):
    import argparse
    import json
    parser = argparse.ArgumentParser(prog='book2pdf inspect-bkf-records',description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('--marker',default=LEAD_MARKER.hex(),help='Hex research token; never a production format definition')
    args = parser.parse_args(argv)
    try:
        marker = bytes.fromhex(args.marker)
        if not 4 <= len(marker) <= 64:
            raise ValueError('Research marker must contain 4..64 bytes')
        result = inspect_bounded(args.source,marker)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (ValueError,OSError) as exc:
        import sys
        print('Record research failed: '+str(exc),file=sys.stderr)
        return 2
