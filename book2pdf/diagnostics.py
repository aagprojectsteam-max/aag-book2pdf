"""Read-only layout classification. Findings never authorize PDF recovery."""
from collections import Counter
import math
import re


def layout_evidence(data):
    """Bound report size and entropy sampling; scan structural markers in full.

    Signatures are observations, not proof of a codec, version or encryption.
    An opaque BKF-prefixed file and a plaintext PDF wrapped in BKF must not be
    treated alike merely because their first three bytes match.
    """
    patterns = {
        'pdf_header': rb'%PDF-(?:1\.[0-7]|2\.0)',
        'object': rb'\d+[\x00\t\n\x0c\r ]+\d+[\x00\t\n\x0c\r ]+obj\b',
        'classic_xref': rb'(?m)^xref[\r\n ]',
        'startxref': rb'startxref\b', 'eof': rb'%%EOF',
        'trailer': rb'\btrailer\b', 'catalog': rb'/Catalog\b',
        'pages': rb'/Pages\b', 'page': rb'/Page\b',
        'stream': rb'\bstream[\r\n]',
        'image_dictionary': rb'/Subtype\s*/Image\b',
        'image_mask_reference': rb'/SMask\s+\d+\s+\d+\s+R\b',
        'image_filter': rb'/(?:DCTDecode|JPXDecode|JBIG2Decode|CCITTFaxDecode)\b',
    }
    markers = {}
    for name, pattern in patterns.items():
        count = 0
        positions = []
        for match in re.finditer(pattern, data):
            count += 1
            if len(positions) < 16:
                positions.append(match.start())
        markers[name] = {'count': count, 'first_positions': positions}
    size = len(data)
    samples = []
    for start in sorted({0, max(0, size // 2 - 32768), max(0, size - 65536)}):
        block = data[start:start + 65536]
        entropy = -sum((n / len(block)) * math.log2(n / len(block)) for n in Counter(block).values()) if block else 0
        samples.append({'offset': start, 'length': len(block), 'entropy_bits_per_byte': round(entropy, 6)})
    has_structure = any(item['count'] for item in markers.values())
    high_entropy = bool(samples) and all(item['length'] >= 4096 and item['entropy_bits_per_byte'] > 7.8 for item in samples)
    classification = ('PDF_STRUCTURE_VISIBLE' if has_structure else
                      'OPAQUE_HIGH_ENTROPY_NO_PDF_STRUCTURE' if high_entropy else 'NO_VISIBLE_PDF_STRUCTURE')
    prefix = bytes(data[:3])
    return {'classification': classification, 'signature_hex': prefix.hex(),
            'observed_book_signature': prefix.decode('ascii') if prefix in (b'BKC', b'BKF') else None,
            'markers': markers, 'entropy_samples': samples,
            'codec': 'UNKNOWN', 'encryption': 'NOT_DETERMINED',
            'note': 'Byte signatures and entropy do not establish compression, encryption, page count or a recoverable PDF boundary.'}


def rejection(message, forensic):
    from .models import Unsupported
    error = Unsupported(message)
    error.forensic = forensic
    return error
