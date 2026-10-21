"""
Celeste Map Binary (.bin) Parser and Writer

Implements the Celeste binary map format used by Lönn, Maple, and Everest.

Format structure:
  1. Header string ("CELESTE MAP")
  2. Package name string
  3. Lookup table (deduplicated sorted strings)
  4. Element tree (recursive: name + attributes + children)

Value type tags:
  0: bool, 1: uint8, 2: int16, 3: int32, 4: float32,
  5: lookup string, 6: raw string, 7: RLE-encoded string
"""

import os
import shutil
import struct
from pathlib import Path
from typing import Any


# ─── Binary Reader ────────────────────────────────────────────────────────────

class BinaryReader:
    """Sequential binary reader for Celeste .bin format."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read_byte(self) -> int:
        val = self._data[self._pos]
        self._pos += 1
        return val

    def read_ushort(self) -> int:
        val = struct.unpack_from('<H', self._data, self._pos)[0]
        self._pos += 2
        return val

    def read_short(self) -> int:
        val = struct.unpack_from('<h', self._data, self._pos)[0]
        self._pos += 2
        return val

    def read_int32(self) -> int:
        val = struct.unpack_from('<i', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_float(self) -> float:
        val = struct.unpack_from('<f', self._data, self._pos)[0]
        self._pos += 4
        return val

    def read_varint(self) -> int:
        result = 0
        shift = 0
        while True:
            b = self.read_byte()
            result |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                break
        return result

    def read_string(self) -> str:
        length = self.read_varint()
        s = self._data[self._pos:self._pos + length].decode('utf-8')
        self._pos += length
        return s

    def read_bytes(self, n: int) -> bytes:
        data = self._data[self._pos:self._pos + n]
        self._pos += n
        return data


# ─── Binary Writer ────────────────────────────────────────────────────────────

class BinaryWriter:
    """Sequential binary writer for Celeste .bin format."""

    def __init__(self):
        self._parts: list[bytes] = []

    def write_byte(self, v: int):
        self._parts.append(bytes([v & 0xFF]))

    def write_ushort(self, v: int):
        self._parts.append(struct.pack('<H', v & 0xFFFF))

    def write_short(self, v: int):
        self._parts.append(struct.pack('<h', v))

    def write_int32(self, v: int):
        self._parts.append(struct.pack('<i', v))

    def write_float(self, v: float):
        self._parts.append(struct.pack('<f', v))

    def write_varint(self, v: int):
        while v > 127:
            self.write_byte((v & 0x7F) | 0x80)
            v >>= 7
        self.write_byte(v)

    def write_string(self, s: str):
        encoded = s.encode('utf-8')
        self.write_varint(len(encoded))
        self._parts.append(encoded)

    def write_raw(self, data: bytes):
        self._parts.append(data)

    def get_bytes(self) -> bytes:
        return b''.join(self._parts)


# ─── Run-Length Encoding ──────────────────────────────────────────────────────

def decode_rle(data: bytes) -> str:
    """Decode RLE tile data. Format: pairs of (count_byte, char_byte)."""
    chars = []
    i = 0
    while i + 1 < len(data):
        count = data[i]
        char = chr(data[i + 1])
        chars.append(char * count)
        i += 2
    return ''.join(chars)


def encode_rle(text: str) -> bytes:
    """RLE-encode a string. Format: pairs of (count_byte, char_byte)."""
    if not text:
        return b''
    parts: list[bytes] = []
    count = 1
    current = text[0]
    for ch in text[1:]:
        if ch != current or count == 255:
            parts.append(bytes([count, ord(current)]))
            count = 1
            current = ch
        else:
            count += 1
    parts.append(bytes([count, ord(current)]))
    return b''.join(parts)


# ─── Internal: Element I/O ────────────────────────────────────────────────────

# Keys that are structural metadata, not serialized as element attributes.
# The root's '_package' key is handled separately in _write_element: it is
# written as an attribute only when the file really had one (Lönn writes it),
# never as a copy of the header package string.
_SKIP_KEYS = frozenset(('__name', '__children'))


class Element(dict):
    """A parsed element. Behaves exactly like a dict; `_bin` remembers how the
    element was encoded (lookup indexes, value type tags, raw RLE bytes) so an
    unedited map is written back byte-for-byte. It is an attribute, not a key,
    so it never appears in items(), JSON exports or diffs."""

    _bin: dict


def _read_value(r: BinaryReader, lookup: list[str]) -> tuple[Any, int, Any]:
    """Return (value, type tag, encoding detail needed to write it back unchanged)."""
    tag = r.read_byte()
    if tag == 0:
        return r.read_byte() != 0, tag, None
    if tag == 1:
        return r.read_byte(), tag, None
    if tag == 2:
        return r.read_short(), tag, None
    if tag == 3:
        return r.read_int32(), tag, None
    if tag == 4:
        return r.read_float(), tag, None
    if tag == 5:
        idx = r.read_short()
        return lookup[idx], tag, idx
    if tag == 6:
        return r.read_string(), tag, None
    if tag == 7:
        rle_len = r.read_short()
        raw = r.read_bytes(rle_len)
        return decode_rle(raw), tag, raw
    raise ValueError(f"Unknown value type tag: {tag}")


def _read_element(r: BinaryReader, lookup: list[str]) -> Element:
    name_idx = r.read_ushort()
    element = Element(__name=lookup[name_idx])
    enc = {'n': name_idx, 'k': {}, 't': {}, 'v': {}, 'r': {}}

    attr_count = r.read_byte()
    for _ in range(attr_count):
        key_idx = r.read_ushort()
        key = lookup[key_idx]
        value, tag, detail = _read_value(r, lookup)
        element[key] = value
        enc['k'][key] = key_idx
        enc['t'][key] = tag
        if tag == 5:
            enc['v'][key] = detail
        elif tag == 7:
            enc['r'][key] = detail

    child_count = r.read_ushort()
    element['__children'] = [_read_element(r, lookup) for _ in range(child_count)]
    element._bin = enc
    return element


def _collect_strings(element: dict, seen: set[str]):
    seen.add(element.get('__name', ''))
    for k, v in element.items():
        if k in _SKIP_KEYS:
            continue
        seen.add(k)
        if isinstance(v, str) and k != 'innerText':
            seen.add(v)
    for child in element.get('__children', []):
        _collect_strings(child, seen)


def _fits(tag: int, value: Any) -> bool:
    """Can `value` still be written with the type tag it was read with?"""
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if tag == 0:
        return isinstance(value, bool)
    if tag == 1:
        return is_int and 0 <= value <= 255
    if tag == 2:
        return is_int and -32768 <= value <= 32767
    if tag == 3:
        return is_int and -2147483648 <= value <= 2147483647
    if tag == 4:
        return (is_int or isinstance(value, float))
    return isinstance(value, str)


class _Lookup:
    """Append-only string table: existing indexes never move, new strings go at the end."""

    def __init__(self, strings: list[str]):
        self.strings = list(strings)
        self._first: dict[str, int] = {}
        for i, s in enumerate(self.strings):
            self._first.setdefault(s, i)

    def index(self, s: str) -> int:
        if s not in self._first:
            self._first[s] = len(self.strings)
            self.strings.append(s)
        return self._first[s]

    def reuse(self, idx: int | None, s: str) -> int:
        """Keep a recorded index when it still names the same string (handles duplicate entries)."""
        if idx is not None and 0 <= idx < len(self.strings) and self.strings[idx] == s:
            return idx
        return self.index(s)

    def get(self, s: str) -> int | None:
        return self._first.get(s)


def _write_original(w: BinaryWriter, tag: int, value: Any, lookup: _Lookup,
                    vidx: int | None, raw: bytes | None) -> bool:
    """Write `value` with its original encoding. Returns False if that is not possible."""
    if tag == 7:
        rle = raw if raw is not None and decode_rle(raw) == value else encode_rle(value)
        if len(rle) > 32767:
            return False
        w.write_byte(7)
        w.write_short(len(rle))
        w.write_raw(rle)
        return True
    w.write_byte(tag)
    if tag == 0:
        w.write_byte(1 if value else 0)
    elif tag == 1:
        w.write_byte(value)
    elif tag == 2:
        w.write_short(value)
    elif tag == 3:
        w.write_int32(value)
    elif tag == 4:
        w.write_float(float(value))
    elif tag == 5:
        w.write_short(lookup.reuse(vidx, value))
    elif tag == 6:
        w.write_string(value)
    return True


def _write_value(w: BinaryWriter, key: str, value: Any, lookup: _Lookup):
    if isinstance(value, bool):
        w.write_byte(0)
        w.write_byte(1 if value else 0)
    elif isinstance(value, float):
        w.write_byte(4)
        w.write_float(value)
    elif isinstance(value, int):
        if 0 <= value <= 255:
            w.write_byte(1)
            w.write_byte(value)
        elif -32768 <= value <= 32767:
            w.write_byte(2)
            w.write_short(value)
        elif -2147483648 <= value <= 2147483647:
            w.write_byte(3)
            w.write_int32(value)
        else:
            w.write_byte(4)
            w.write_float(float(value))
    elif isinstance(value, str):
        if key == 'innerText':
            rle = encode_rle(value)
            if len(rle) < len(value.encode('utf-8')) and len(rle) < 32767:
                w.write_byte(7)
                w.write_short(len(rle))
                w.write_raw(rle)
            else:
                w.write_byte(6)
                w.write_string(value)
        else:
            # Celeste's own packer stores attribute strings in the lookup table.
            w.write_byte(5)
            w.write_short(lookup.index(value))
    else:
        w.write_byte(6)
        w.write_string(str(value))


def _write_element(w: BinaryWriter, element: dict, lookup: _Lookup, is_root: bool = False):
    enc = getattr(element, '_bin', None)
    w.write_ushort(lookup.reuse(enc['n'] if enc else None, element.get('__name', '')))

    attrs = [(k, v) for k, v in element.items() if k not in _SKIP_KEYS]
    if is_root and enc is not None and '_package' not in enc['t']:
        # '_package' here only mirrors the header package; the file had no such attribute.
        attrs = [(k, v) for k, v in attrs if k != '_package']
    w.write_byte(len(attrs))
    for key, value in attrs:
        w.write_ushort(lookup.reuse(enc['k'].get(key) if enc else None, key))
        tag = enc['t'].get(key) if enc else None
        if tag is not None and _fits(tag, value) and _write_original(
                w, tag, value, lookup, enc['v'].get(key), enc['r'].get(key)):
            continue
        _write_value(w, key, value, lookup)

    children = element.get('__children', [])
    w.write_ushort(len(children))
    for child in children:
        _write_element(w, child, lookup)


# ─── Public API ───────────────────────────────────────────────────────────────

def read_map_bytes(data: bytes, expected_header: str = "CELESTE MAP") -> dict:
    """Parse Celeste .bin map bytes and return the element tree."""
    r = BinaryReader(data)

    header = r.read_string()
    if header != expected_header:
        raise ValueError(f"Invalid header: '{header}' (expected '{expected_header}')")

    package = r.read_string()

    lookup_count = r.read_ushort()
    lookup = [r.read_string() for _ in range(lookup_count)]

    root = _read_element(r, lookup)
    root._bin['lookup'] = lookup
    root._bin['header_package'] = package
    if '_package' not in root:
        # Mirror the header package for display; it is not written back as an attribute.
        root['_package'] = package
    return root


def read_map(path: str | Path) -> dict:
    """Read a Celeste .bin map file and return the element tree.

    Returns a dict with keys:
      __name: element name (e.g. "Map")
      __children: list of child elements
      _package: map package name
      + any attributes on the root element
    """
    return read_map_bytes(Path(path).read_bytes())


# Number of rolling .bak copies kept next to each map file.
BACKUP_KEEP = 3


def _rotate_backups(path: Path, keep: int = BACKUP_KEEP):
    """Shift path.bak1 -> .bak2 -> ... and copy the current file to .bak1.

    The current file itself is only copied, never moved, so the map stays
    intact until the atomic replace swaps in the validated new version.
    """
    def bak(i: int) -> Path:
        return path.with_name(path.name + f".bak{i}")

    for i in range(keep - 1, 0, -1):
        if bak(i).exists():
            os.replace(bak(i), bak(i + 1))
    shutil.copy2(path, bak(1))


def serialize_map(data: dict, header: str = "CELESTE MAP") -> bytes:
    """Serialize an element tree to Celeste .bin bytes (no file I/O).

    A tree that came from read_map keeps its string table order and value
    encodings, so unedited parts are written back byte-for-byte and edits
    only append to the table.
    """
    enc = getattr(data, '_bin', None)
    if enc is not None and 'lookup' in enc:
        lookup = _Lookup(enc['lookup'])
        if '_package' in enc['t']:
            package = enc.get('header_package', '')
        else:
            package = data.get('_package', enc.get('header_package', ''))
    else:
        seen: set[str] = set()
        _collect_strings(data, seen)
        lookup = _Lookup(sorted(seen))
        package = data.get('_package', '')

    # Elements first: writing them may append new strings to the table.
    body = BinaryWriter()
    _write_element(body, data, lookup, is_root=True)
    if len(lookup.strings) > 32767:
        raise ValueError(f"String table too large ({len(lookup.strings)} entries)")

    w = BinaryWriter()
    w.write_string(header)
    w.write_string(package)
    w.write_ushort(len(lookup.strings))
    for s in lookup.strings:
        w.write_string(s)
    w.write_raw(body.get_bytes())
    return w.get_bytes()


def write_map(path: str | Path, data: dict, header: str = "CELESTE MAP",
              backup: bool = True, validate: bool = True):
    """Write a Celeste .bin map file from an element tree — safely.

    A destroyed map.bin is unrecoverable for the user, so this never touches
    the target file until the new bytes are known-good:
      1. serialize to memory
      2. round-trip self-check: re-parse the bytes (validate=True)
      3. write to a temp file next to the target
      4. rotate rolling backups of the existing file (backup=True)
      5. atomically replace the target with the temp file
    """
    write_map_bytes(path, serialize_map(data, header), header, backup, validate)


def write_map_bytes(path: str | Path, payload: bytes, header: str = "CELESTE MAP",
                    backup: bool = True, validate: bool = True):
    """Write already-serialized map bytes with the same safety steps as write_map:
    parse check, temp file, rolling backups, atomic replace."""
    path = Path(path)
    if validate:
        try:
            read_map_bytes(payload, expected_header=header)
        except Exception as exc:
            raise ValueError(
                f"Refusing to write {path.name}: serialized map failed "
                f"round-trip validation ({exc})"
            ) from exc

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(payload)
    try:
        if backup and path.exists():
            _rotate_backups(path)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ─── Tree Navigation Helpers ──────────────────────────────────────────────────

def find_child(element: dict, name: str) -> dict | None:
    """Find the first direct child with the given __name."""
    for child in element.get('__children', []):
        if child.get('__name') == name:
            return child
    return None


def find_children(element: dict, name: str) -> list[dict]:
    """Find all direct children with the given __name."""
    return [c for c in element.get('__children', []) if c.get('__name') == name]


def get_rooms(map_data: dict) -> list[dict]:
    """Get all room (level) elements from a map."""
    levels = find_child(map_data, 'levels')
    if levels is None:
        return []
    return find_children(levels, 'level')


def get_room(map_data: dict, room_name: str) -> dict | None:
    """Find a room by name (with or without 'lvl_' prefix)."""
    for room in get_rooms(map_data):
        name = room.get('name', '')
        if name == room_name or name == f'lvl_{room_name}':
            return room
    return None
