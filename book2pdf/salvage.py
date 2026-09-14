"""Evidence gate for explicitly reported, non-exact graphics-state salvage."""
import hashlib
from pathlib import Path

from .models import Unsupported


def hash_range(stream, start, size):
    stream.seek(start)
    digest = hashlib.sha256()
    while size:
        block = stream.read(min(size, 1024 * 1024))
        if not block:
            raise Unsupported('Unexpected EOF during object preservation audit')
        digest.update(block)
        size -= len(block)
    return digest.hexdigest()


def verify_salvage(source: Path, output: Path, detection):
    import pikepdf
    damaged = {tuple(item['object']): dict(item, resource_references=0, gs_uses=0, pages=[]) for item in detection.damaged}
    with pikepdf.open(output, attempt_recovery=False, inherit_page_attributes=False) as pdf:
        if pdf.is_encrypted:
            raise Unsupported('Encrypted documents cannot be salvaged')
        contexts = [(page, page.Resources, i + 1) for i, page in enumerate(pdf.pages)]
        contexts.extend((obj, obj.get('/Resources', pikepdf.Dictionary()), None) for obj in pdf.objects
                        if isinstance(obj, pikepdf.Stream) and obj.get('/Subtype') == pikepdf.Name.Form)
        mappings = set()
        for content, resources, page_number in contexts:
            states = resources.get('/ExtGState', pikepdf.Dictionary())
            if states.is_indirect:
                mappings.add(states.objgen)
            for name, obj in states.items():
                if obj.objgen in damaged:
                    entry = damaged[obj.objgen]
                    entry['resource_references'] += 1
                    if page_number is not None and page_number not in entry['pages']:
                        entry['pages'].append(page_number)
            for instruction in pikepdf.parse_content_stream(content):
                if str(instruction.operator) == 'gs':
                    if len(instruction.operands) != 1 or str(instruction.operands[0]) not in states:
                        raise Unsupported('Unresolved graphics-state operator')
                    obj = states[str(instruction.operands[0])]
                    if obj.objgen in damaged:
                        damaged[obj.objgen]['gs_uses'] += 1

        # Every reference to a damaged object must be a graphics-state resource.
        # Enumerate each indirect object once, descending into direct containers only.
        def inspect(container, role='', depth=0):
            if depth > 64:
                raise Unsupported('Excessively nested resources')
            if isinstance(container, (pikepdf.Dictionary, pikepdf.Stream)):
                items = container.items()
                if container.is_indirect and container.objgen in mappings:
                    role = '/ExtGState'
            elif isinstance(container, pikepdf.Array):
                items = [('', item) for item in container]
            else:
                return
            for key, value in items:
                if not isinstance(value, pikepdf.Object):
                    continue
                if value.is_indirect:
                    if value.objgen in damaged and role != '/ExtGState':
                        raise Unsupported('Damaged object has a non-graphics-state reference')
                else:
                    inspect(value, str(key), depth + 1)

        inspect(pdf.trailer)
        for obj in pdf.objects:
            inspect(obj)
        if any(not entry['resource_references'] or not entry['gs_uses'] for entry in damaged.values()):
            raise Unsupported('Cannot establish damaged-object usage as ExtGState')
        delta = len(detection.header) - detection.body_start
        ordered = sorted(detection.entries, key=lambda e: e.offset)
        audit = hashlib.sha256()
        preserved_count = 0
        with source.open('rb') as original, output.open('rb') as repaired:
            for i, entry in enumerate(ordered):
                if (entry.number, entry.generation) in damaged:
                    continue
                start = entry.offset + detection.shift
                end = (ordered[i + 1].offset + detection.shift if i + 1 < len(ordered)
                       else detection.startxref + detection.shift)
                before = hash_range(original, start, end - start)
                after = hash_range(repaired, start + delta, end - start)
                if before != after:
                    raise Unsupported('Original object bytes changed during salvage')
                audit.update(f'{entry.number}:{entry.generation}:{before}\n'.encode())
                preserved_count += 1
        images = []
        stream_count = 0
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                stream_count += 1
                if obj.get('/Subtype') == pikepdf.Name.Image:
                    images.append({'object': list(obj.objgen), 'raw_sha256': hashlib.sha256(obj.read_raw_bytes()).hexdigest(),
                                   'width': int(obj.Width), 'height': int(obj.Height)})
        return list(damaged.values()), {'all_intact_objects_byte_identical': True,
                                        'intact_object_count': preserved_count,
                                        'object_manifest_sha256': audit.hexdigest(),
                                        'original_streams_preserved': stream_count,
                                        'images': images, 'source_page_count': len(pdf.pages)}


def verify_reconstruction_preview(source: Path, output: Path, detection):
    """Verify an explicitly hypothetical image/SMask preview without claiming exact fidelity."""
    import pikepdf
    preview = detection.forensic.get('reconstruction_preview') or {}
    image_key = tuple(preview.get('image_object', ()))
    mask_key = tuple(preview.get('mask_object', ()))
    if len(image_key) != 2 or len(mask_key) != 2:
        raise Unsupported('Missing reconstruction-preview provenance')

    def object_by_key(pdf, key):
        for obj in pdf.objects:
            try:
                if obj.objgen == key:
                    return obj
            except Exception:
                pass
        raise Unsupported(f'Preview object {key} is missing from reconstructed PDF')

    with pikepdf.open(output, attempt_recovery=False, inherit_page_attributes=False) as pdf:
        if pdf.is_encrypted:
            raise Unsupported('Encrypted documents cannot be previewed')
        image = object_by_key(pdf, image_key)
        mask = object_by_key(pdf, mask_key)
        if not isinstance(image, pikepdf.Stream) or image.get('/Subtype') != pikepdf.Name.Image:
            raise Unsupported('Repaired image object is not an image stream')
        smask = image.get('/SMask')
        if not isinstance(smask, pikepdf.Object) or not smask.is_indirect or smask.objgen != mask_key:
            raise Unsupported('Image /SMask relationship was not preserved')
        if (not isinstance(mask, pikepdf.Stream) or mask.get('/Subtype') != pikepdf.Name.Image
                or int(mask.get('/Width', 0)) != 1 or int(mask.get('/Height', 0)) != 1
                or int(mask.get('/BitsPerComponent', 0)) != 8
                or mask.get('/ColorSpace') != pikepdf.Name.DeviceGray):
            raise Unsupported('Synthesized SMask does not have the verified one-pixel grayscale shape')
        mask_bytes = mask.read_bytes()
        expected = bytes.fromhex(preview['synthesized_mask_byte'])
        if mask_bytes != expected:
            raise Unsupported('Synthesized SMask value changed unexpectedly')

        xobject_maps = set()
        for obj in pdf.objects:
            if isinstance(obj,(pikepdf.Dictionary,pikepdf.Stream)):
                resources = obj.get('/Resources')
                if isinstance(resources,pikepdf.Dictionary):
                    mapping = resources.get('/XObject')
                    if isinstance(mapping,pikepdf.Dictionary) and mapping.is_indirect:
                        xobject_maps.add(mapping.objgen)

        def inspect_refs(container, depth=0, role=''):
            if depth > 64:
                raise Unsupported('Reference nesting exceeds preview evidence bound')
            if isinstance(container,(pikepdf.Dictionary,pikepdf.Stream)):
                items = container.items()
                if container.is_indirect and container.objgen in xobject_maps:
                    role = '/XObject'
            elif isinstance(container,pikepdf.Array):
                items = [('',v) for v in container]
            else:
                return
            for key,value in items:
                if not isinstance(value,pikepdf.Object):
                    continue
                if value.is_indirect:
                    if value.objgen == mask_key and not (role == '/XObject' or
                        (key == '/SMask' and isinstance(container,pikepdf.Stream) and container.get('/Subtype') == pikepdf.Name.Image)):
                        raise Unsupported('Missing mask has a non-image-SMask reference')
                else:
                    inspect_refs(value,depth+1,str(key))
        inspect_refs(pdf.trailer)
        for obj in pdf.objects:
            inspect_refs(obj)

        def reaches(obj, target, seen, depth=0):
            if depth > 64:
                raise Unsupported('Affected-page traversal exceeds evidence bound')
            if not isinstance(obj, pikepdf.Object):
                return False
            if obj.is_indirect:
                if obj.objgen == target:
                    return True
                if obj.objgen in seen:
                    return False
                seen.add(obj.objgen)
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                return any(reaches(value, target, seen, depth + 1) for key, value in obj.items() if key != '/Parent')
            if isinstance(obj, pikepdf.Array):
                return any(reaches(value, target, seen, depth + 1) for value in obj)
            return False

        affected_pages = []
        for number, page in enumerate(pdf.pages, 1):
            if reaches(page.Resources, mask_key, set()):
                affected_pages.append(number)
        if not affected_pages:
            raise Unsupported('Cannot establish any page using the repaired image object')

        delta = len(detection.header) - detection.body_start
        ordered = sorted(detection.entries, key=lambda e: e.offset)
        damaged = {tuple(item['object']) for item in detection.damaged}
        audit = hashlib.sha256()
        preserved_count = 0
        with source.open('rb') as original, output.open('rb') as repaired:
            for i, entry in enumerate(ordered):
                start = entry.offset + detection.shift
                end = (ordered[i + 1].offset + detection.shift if i + 1 < len(ordered)
                       else detection.startxref + detection.shift)
                if (entry.number, entry.generation) in damaged:
                    continue
                before = hash_range(original, start, end - start)
                after = hash_range(repaired, start + delta, end - start)
                if before != after:
                    raise Unsupported('Original intact object bytes changed during reconstruction preview')
                audit.update(f'{entry.number}:{entry.generation}:{before}\n'.encode())
                preserved_count += 1

            image_info = next(item for item in detection.damaged if tuple(item['object']) == image_key)
            prefix_len = len(image_info['replacement'].encode('ascii'))
            suffix_start = image_info['source_start'] + prefix_len
            suffix_size = image_info['source_end'] - suffix_start
            if hash_range(original, suffix_start, suffix_size) != hash_range(repaired, suffix_start + delta, suffix_size):
                raise Unsupported('Surviving image bytes changed during reconstruction preview')

        streams = 0
        images = []
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream):
                streams += 1
                if obj.get('/Subtype') == pikepdf.Name.Image:
                    images.append({'object': list(obj.objgen), 'raw_sha256': hashlib.sha256(obj.read_raw_bytes()).hexdigest(),
                                   'width': int(obj.Width), 'height': int(obj.Height)})
        preservation = {'all_intact_objects_byte_identical': True,
                        'intact_object_count': preserved_count,
                        'object_manifest_sha256': audit.hexdigest(),
                        'surviving_image_suffix_byte_identical': True,
                        'streams_after_preview': streams,
                        'original_streams_preserved': streams-1,
                        'synthesized_streams': [list(mask_key)],
                        'images': images,
                        'source_page_count': len(pdf.pages),
                        'affected_pages': affected_pages,
                        'synthesized_mask_byte': preview['synthesized_mask_byte']}
        return [dict(item) for item in detection.damaged], preservation, affected_pages
