"""Observed BOOK envelope profile; names are opaque bytes, never output paths.

The directory transform continues over its 14-byte header. Each component
resets the feedback state and transforms only its first 200 bytes.
"""
from dataclasses import dataclass
import hashlib
import struct

PROFILE = 'observed-book-feedback-v1'
PROFILE_VERSION = 2
MAX_INPUT = 256 * 1024 * 1024
MAX_DIRECTORY = 2 * 1024 * 1024
MAX_ENTRIES = 4096
TRANSFORM_PREFIX_BYTES = 200


class EnvelopeError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(f'{code}: {detail}')


def transform(data, *, encode=False):
    state = 0xDBE5
    result = bytearray()
    for value in data:
        plain = value if encode else value ^ (state >> 8)
        result.append(plain ^ (state >> 8) if encode else plain)
        state = (23421 * ((state + plain) & 255) + 9817) & 65535
    return bytes(result)


def decode_record(raw):
    return transform(raw[:TRANSFORM_PREFIX_BYTES]) + raw[TRANSFORM_PREFIX_BYTES:]


@dataclass(frozen=True)
class DirectoryEntry:
    name: bytes
    offset: int
    length: int


@dataclass
class Envelope:
    outer: bytes
    directory_header: bytes
    directory_raw: bytes
    base: int
    entries: list

    def unique_ranges(self):
        groups = {}
        for entry in self.entries:
            groups.setdefault((entry.offset, entry.length), []).append(entry.name.hex())
        return groups


def parse_directory(data):
    if len(data) < 30 or data[:3] not in (b'BKC', b'BKF'):
        raise EnvelopeError('PROFILE_NOT_APPLICABLE', 'No complete BOOK envelope header')
    if len(data) > MAX_INPUT:
        raise EnvelopeError('LIMIT_EXCEEDED', 'BOOK input byte budget')
    size = int.from_bytes(transform(data[7:11]), 'little')
    base = 21 + size
    if not 9 <= size <= MAX_DIRECTORY or base >= len(data):
        raise EnvelopeError('PROFILE_NOT_APPLICABLE', 'Directory length outside supported bounds')
    raw = bytes(data[7:base])
    plain = transform(raw)
    if transform(plain, encode=True) != raw:
        raise EnvelopeError('DOCUMENT_INVALID', 'Directory transform round-trip failed')
    pos = 14
    entries = []
    while pos < len(plain):
        if len(entries) >= MAX_ENTRIES:
            raise EnvelopeError('LIMIT_EXCEEDED', 'Directory entry budget')
        nul = plain.find(b'\0', pos, min(pos + 4097, len(plain)))
        if nul <= pos or nul + 9 > len(plain):
            raise EnvelopeError('PROFILE_NOT_APPLICABLE', 'Malformed directory entry')
        offset, length = struct.unpack_from('<II', plain, nul + 1)
        if length < 1:
            raise EnvelopeError('DOCUMENT_INVALID', 'Empty component')
        entries.append(DirectoryEntry(plain[pos:nul], offset, length))
        pos = nul + 9
    if not entries:
        raise EnvelopeError('DOCUMENT_INVALID', 'Empty directory')
    return Envelope(bytes(data[:7]), plain[:14], raw, base, entries)


def classify_prefix(raw):
    plain = decode_record(raw[:1024])
    if plain.startswith(b'%PDF-'):
        return 'PDF'
    if plain.startswith(b'AT&TFORM'):
        return 'DJVU' if plain[12:16] in (b'DJVU', b'DJVM') else 'DJVU_RESOURCE'
    return 'UNKNOWN'


def digest(data):
    return hashlib.sha256(data).hexdigest()
