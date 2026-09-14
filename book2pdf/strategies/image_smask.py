"""Explicit, bounded image-header repair and assumed SMask preview."""
import re
from ..models import Unsupported

def _entry_end(entries, entry, shift, xref_position):
    later = [e.offset + shift for e in entries if e.offset > entry.offset]
    return min(later) if later else xref_position


def _mask_template(number, generation, value):
    if value not in (0x00, 0xFF):
        raise Unsupported("Preview mask byte must be 00 or FF")
    return (f"{number} {generation} obj\n<<\n/Width 1\n/Type /XObject\n/Subtype /Image\n/Name /X\n"
            "/Intent /RelativeColorimetric\n/Height 1\n/ColorSpace /DeviceGray\n/BitsPerComponent 8\n"
            "/Length 1\n>>\nstream\n").encode() + bytes([value]) + b"\nendstream\nendobj\n"


def preview_image_smask(data, entries, shift, xref_position, damaged, policy):
    """Return deterministic structural patches plus an explicitly hypothetical SMask.

    This is *not* exact recovery.  It is only enabled by an explicit preview policy.
    The surviving image object must prove the missing object is its /SMask, the image
    stream must still be a JPEG, and at least two intact one-pixel sibling masks must
    establish the container shape.  The missing one-byte mask value remains an
    explicit user-selected assumption.
    """
    if policy not in ('opaque', 'transparent') or len(damaged) != 2:
        return None
    by_obj = {(e.number, e.generation): e for e in damaged}
    image_entry = mask_entry = None
    image_begin = image_end = None
    for entry in damaged:
        begin = entry.offset + shift
        end = _entry_end(entries, entry, shift, xref_position)
        raw = data[begin:end]
        expected = f"{entry.number} {entry.generation} obj".encode()
        # Bounded in-place header repair: only support cases where the surviving
        # suffix proves the header and the lost prefix has the same byte length.
        suffix = f"{entry.generation} obj".encode()
        prefix = f"{entry.number} ".encode()
        if len(prefix) <= 8 and raw[len(prefix):].startswith(suffix):
            dictionary = raw[len(prefix):].split(b"stream", 1)[0]
            ref = re.search(rb"/SMask\s+(\d+)\s+(\d+)\s+R\b", dictionary)
            if ref and re.search(rb"/Subtype\s*/Image\b", dictionary):
                key = (int(ref[1]), int(ref[2]))
                if key in by_obj and key != (entry.number, entry.generation):
                    stream_pos = raw.find(b"stream")
                    if stream_pos >= 0:
                        payload = raw[stream_pos + 6:]
                        payload = payload.lstrip(b"\r\n")
                        if payload.startswith(b"\xff\xd8\xff"):
                            image_entry = entry
                            mask_entry = by_obj[key]
                            image_begin, image_end = begin, end
                            break
    if not image_entry:
        return None

    # Establish that sibling one-pixel grayscale masks of the same shape really
    # occur repeatedly in intact objects.  This proves a preview *shape*, not the
    # missing original value.
    sibling_bodies = []
    for entry in entries:
        if entry in damaged:
            continue
        begin = entry.offset + shift
        end = _entry_end(entries, entry, shift, xref_position)
        raw = data[begin:end]
        m = re.match(rb"\d+\s+\d+\s+obj\n(<<.*?>>\nstream\n)([\x00\xff])\nendstream\nendobj\n?$", raw, re.S)
        if not m:
            continue
        dictionary = m[1]
        if (re.search(rb"/Width\s+1\b", dictionary)
                and re.search(rb"/Height\s+1\b", dictionary)
                and re.search(rb"/Subtype\s*/Image\b", dictionary)
                and re.search(rb"/ColorSpace\s*/DeviceGray\b", dictionary)
                and re.search(rb"/BitsPerComponent\s+8\b", dictionary)
                and re.search(rb"/Length\s+1\b", dictionary)):
            sibling_bodies.append((dictionary, m[2][0]))
    if len(sibling_bodies) < 2:
        return None

    mask_begin = mask_entry.offset + shift
    mask_end = _entry_end(entries, mask_entry, shift, xref_position)
    value = 0xFF if policy == 'opaque' else 0x00
    replacement = _mask_template(mask_entry.number, mask_entry.generation, value)
    if len(replacement) != mask_end - mask_begin:
        return None
    expected_prefix = f"{image_entry.number} ".encode()
    if len(expected_prefix) > image_end - image_begin:
        return None
    patches = [(image_begin, image_begin + len(expected_prefix), expected_prefix),
               (mask_begin, mask_end, replacement)]
    info = [
        {'object': [image_entry.number, image_entry.generation], 'role': 'surviving-image-header',
         'source_start': image_begin, 'source_end': image_end,
         'damaged_byte_range': [image_begin,image_begin+len(expected_prefix)],
         'surviving_byte_range': [image_begin+len(expected_prefix),image_end],
         'replacement': expected_prefix.decode('ascii'),
         'uncertainty': 'Only the damaged object-number prefix is restored; the remaining original object bytes are preserved'},
        {'object': [mask_entry.number, mask_entry.generation], 'role': 'synthesized-smask-preview',
         'source_start': mask_begin, 'source_end': mask_end,
         'damaged_byte_range': [mask_begin,mask_end],
         'replacement': replacement.decode('latin1'),
         'uncertainty': f'Original SMask byte is missing; {policy} value {value:02X} is a user-selected preview assumption'},
    ]
    forensic = {'preview_policy': policy, 'image_object': [image_entry.number, image_entry.generation],
                'mask_object': [mask_entry.number, mask_entry.generation],
                'sibling_mask_count': len(sibling_bodies), 'synthesized_mask_byte': f'{value:02X}'}
    return patches, info, forensic

