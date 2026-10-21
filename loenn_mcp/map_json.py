"""
Typed, lossless JSON for Celeste .bin maps.

A dump keeps every value's binary type, so a map can go .bin → JSON → .bin and
come back byte-identical. The idea of a JSON form that records each attribute's
value type comes from maddievision/celestial (map2json.rb); this format and code
are a fresh implementation, since that project has no license.

Shape:

    {
      "format": "celeste-map-json", "version": 1,
      "header": "CELESTE MAP",
      "package": "<header package string>",
      "source": {"file": "...", "sha256": "<of the .bin>", "tree_sha256": "<of root below>"},
      "lookup": ["Map", "levels", ...],        # string table, file order
      "root": {
        "name": "Map",
        "attrs": {"key": ["<type>", value], ...},
        "children": [ ...same shape... ]
      }
    }

Types: bool, u8, i16, i32, f32, lookup (string-table string), string (plain
string), rle (run-length tile data, decoded). Values are exactly what the file
holds: a room name stays "lvl_a-00" if the file says so.

Some files carry quirks a canonical writer would not reproduce (a duplicate
string-table entry used by one element, RLE runs split unusually, non-finite
floats). Those are kept in optional extras so the dump stays lossless:
    element "name_index": n
    attribute [type, value, {"key_index": k, "value_index": v, "rle_raw": "<base64>"}]

"lookup" and "source" are optional. Without "lookup" the writer builds a fresh
sorted string table; attributes may also be given untyped (a bare value) and
get the type Celeste's own packer would pick.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import struct
from pathlib import Path
from typing import Any

try:
    from . import celeste_bin as cb
except ImportError:  # run directly from source
    import celeste_bin as cb

FORMAT = "celeste-map-json"
VERSION = 1

TAG_TO_TYPE = {0: "bool", 1: "u8", 2: "i16", 3: "i32", 4: "f32", 5: "lookup", 6: "string", 7: "rle"}
TYPE_TO_TAG = {v: k for k, v in TAG_TO_TYPE.items()}


class MapJsonError(ValueError):
    """A typed JSON document is malformed, or it would not convert faithfully."""


# ─── .bin → JSON ──────────────────────────────────────────────────────────────

def _f32_out(value: float) -> Any:
    if math.isfinite(value):
        return value
    return "f32bits:" + struct.pack("<f", value)[::-1].hex()


def _f32_in(value: Any, where: str) -> float:
    if isinstance(value, str) and value.startswith("f32bits:"):
        return struct.unpack("<f", bytes.fromhex(value[8:])[::-1])[0]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MapJsonError(f"{where}: f32 needs a number, got {value!r}")
    # Store what the file can hold: the nearest float32.
    return struct.unpack("<f", struct.pack("<f", float(value)))[0]


def _untyped_pair(key: str, value: Any) -> list:
    """Type a value the way the writer would encode it (for trees built in memory)."""
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, float):
        return ["f32", _f32_out(value)]
    if isinstance(value, int):
        if 0 <= value <= 255:
            return ["u8", value]
        if -32768 <= value <= 32767:
            return ["i16", value]
        if -2**31 <= value < 2**31:
            return ["i32", value]
        return ["f32", float(value)]
    if isinstance(value, str):
        if key == "innerText":
            rle = cb.encode_rle(value)
            if len(rle) < len(value.encode("utf-8")) and len(rle) < 32767:
                return ["rle", value]
            return ["string", value]
        return ["lookup", value]
    return ["string", str(value)]


def _element_to_json(el: dict, first: dict[str, int], is_root: bool) -> dict:
    enc = getattr(el, "_bin", None)
    name = el.get("__name", "")
    out: dict[str, Any] = {"name": name}
    if enc is not None and enc["n"] is not None and enc["n"] != first.get(name):
        out["name_index"] = enc["n"]

    attrs: dict[str, list] = {}
    for key, value in el.items():
        if key in ("__name", "__children"):
            continue
        if is_root and key == "_package" and (enc is None or "_package" not in enc["t"]):
            continue  # display mirror of the header package, not a real attribute
        tag = enc["t"].get(key) if enc is not None else None
        if tag is None or not cb._fits(tag, value):
            attrs[key] = _untyped_pair(key, value)
            continue
        typ = TAG_TO_TYPE[tag]
        pair: list = [typ, _f32_out(float(value)) if tag == 4 else value]
        extra: dict[str, Any] = {}
        kidx = enc["k"].get(key)
        if kidx is not None and kidx != first.get(key):
            extra["key_index"] = kidx
        vidx = enc["v"].get(key)
        if tag == 5 and vidx is not None and vidx != first.get(value):
            extra["value_index"] = vidx
        if tag == 7:
            raw = enc["r"].get(key)
            if raw is not None and cb.decode_rle(raw) == value and cb.encode_rle(value) != raw:
                extra["rle_raw"] = base64.b64encode(raw).decode("ascii")
        if extra:
            pair.append(extra)
        attrs[key] = pair
    if attrs:
        out["attrs"] = attrs

    kids = el.get("__children", [])
    if kids:
        out["children"] = [_element_to_json(c, first, False) for c in kids]
    return out


def tree_sha256(root_json: dict) -> str:
    """Stable hash of a JSON element tree (attribute order matters, as in the file)."""
    blob = json.dumps(root_json, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def tree_to_json(tree: dict, source_bytes: bytes | None = None, source_name: str = "",
                 header: str = "CELESTE MAP") -> dict:
    """Typed JSON for an element tree (normally one returned by celeste_bin.read_map*)."""
    enc = getattr(tree, "_bin", None)
    lookup = list(enc["lookup"]) if enc is not None and "lookup" in enc else None
    first: dict[str, int] = {}
    for i, s in enumerate(lookup or []):
        first.setdefault(s, i)
    if lookup is not None:
        package = enc.get("header_package", "")
    else:
        package = tree.get("_package", "")

    root = _element_to_json(tree, first, True)
    doc: dict[str, Any] = {"format": FORMAT, "version": VERSION, "header": header, "package": package}
    if source_bytes is not None:
        doc["source"] = {"file": source_name,
                         "sha256": hashlib.sha256(source_bytes).hexdigest(),
                         "tree_sha256": tree_sha256(root)}
    if lookup is not None:
        doc["lookup"] = lookup
    doc["root"] = root
    return doc


def bin_to_json(data: bytes, source_name: str = "", header: str = "CELESTE MAP") -> dict:
    """Typed JSON for .bin bytes. Raises MapJsonError if the dump would not
    convert back to the same bytes (it never should; this guards the format)."""
    tree = cb.read_map_bytes(data, expected_header=header)
    doc = tree_to_json(tree, data, source_name, header)
    again = cb.serialize_map(json_to_tree(doc), header)
    if again != data:
        raise MapJsonError(
            f"{source_name or 'map'}: the JSON dump does not convert back byte-identical "
            f"({_first_difference(data, again)}). Not exporting a lossy dump.")
    return doc


def map_to_json(path: str | Path) -> dict:
    path = Path(path)
    return bin_to_json(path.read_bytes(), path.name)


# ─── JSON → tree ──────────────────────────────────────────────────────────────

def _check_value(typ: str, value: Any, where: str) -> Any:
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if typ == "bool":
        if not isinstance(value, bool):
            raise MapJsonError(f"{where}: bool needs true/false, got {value!r}")
    elif typ in ("u8", "i16", "i32"):
        lo, hi = {"u8": (0, 255), "i16": (-32768, 32767), "i32": (-2**31, 2**31 - 1)}[typ]
        if not is_int or not lo <= value <= hi:
            raise MapJsonError(f"{where}: {typ} needs an integer in [{lo}, {hi}], got {value!r}")
    elif typ == "f32":
        return _f32_in(value, where)
    elif typ in ("lookup", "string", "rle"):
        if not isinstance(value, str):
            raise MapJsonError(f"{where}: {typ} needs a string, got {value!r}")
        if typ == "rle" and any(ord(c) > 255 for c in value):
            raise MapJsonError(f"{where}: rle data can only hold single-byte characters")
    else:
        raise MapJsonError(f"{where}: unknown type '{typ}' (use one of {', '.join(TYPE_TO_TAG)})")
    return value


def _json_to_element(j: Any, lookup: list[str] | None, path: str) -> cb.Element:
    if not isinstance(j, dict) or not isinstance(j.get("name"), str):
        raise MapJsonError(f"{path or '/'}: an element needs a string 'name'")
    unknown = set(j) - {"name", "name_index", "attrs", "children"}
    if unknown:
        raise MapJsonError(f"{path or '/'}: unknown element field(s) {sorted(unknown)}")
    name = j["name"]
    el = cb.Element(__name=name)
    enc: dict[str, Any] = {"n": None, "k": {}, "t": {}, "v": {}, "r": {}}

    def hint(idx: Any, s: str) -> int | None:
        # An index hint only counts while it still names the same string.
        if lookup is not None and isinstance(idx, int) and 0 <= idx < len(lookup) and lookup[idx] == s:
            return idx
        return None

    enc["n"] = hint(j.get("name_index"), name)
    here = f"{path}/{name}"
    attrs = j.get("attrs", {})
    if not isinstance(attrs, dict):
        raise MapJsonError(f"{here}: 'attrs' must be an object")
    for key, spec in attrs.items():
        where = f"{here}@{key}"
        if not isinstance(spec, list):
            if isinstance(spec, (dict, type(None))):
                raise MapJsonError(f"{where}: values must be a [type, value] pair or a plain value")
            el[key] = spec  # untyped: the writer picks the type
            continue
        if len(spec) not in (2, 3) or not isinstance(spec[0], str):
            raise MapJsonError(f"{where}: expected [type, value] or [type, value, extras]")
        typ = spec[0]
        value = _check_value(typ, spec[1], where)
        el[key] = value
        enc["t"][key] = TYPE_TO_TAG[typ]
        extra = spec[2] if len(spec) == 3 else {}
        if not isinstance(extra, dict):
            raise MapJsonError(f"{where}: extras must be an object")
        k = hint(extra.get("key_index"), key)
        if k is not None:
            enc["k"][key] = k
        if typ == "lookup":
            v = hint(extra.get("value_index"), value)
            if v is not None:
                enc["v"][key] = v
        if typ == "rle" and "rle_raw" in extra:
            raw = base64.b64decode(extra["rle_raw"])
            if cb.decode_rle(raw) == value:
                enc["r"][key] = raw
    kids = j.get("children", [])
    if not isinstance(kids, list):
        raise MapJsonError(f"{here}: 'children' must be a list")
    el["__children"] = [_json_to_element(c, lookup, f"{here}[{i}]") for i, c in enumerate(kids)]
    el._bin = enc
    return el


def json_to_tree(doc: dict) -> cb.Element:
    """Element tree (with encoding hints) for a typed JSON document."""
    if not isinstance(doc, dict) or "root" not in doc:
        raise MapJsonError("not a typed map JSON document (no 'root')")
    if doc.get("format", FORMAT) != FORMAT:
        raise MapJsonError(f"unsupported format '{doc.get('format')}'")
    lookup = doc.get("lookup")
    if lookup is not None and (not isinstance(lookup, list) or not all(isinstance(s, str) for s in lookup)):
        raise MapJsonError("'lookup' must be a list of strings")
    package = doc.get("package", "")
    if not isinstance(package, str):
        raise MapJsonError("'package' must be a string")
    root = _json_to_element(doc["root"], lookup, "")
    if lookup is not None:
        root._bin["lookup"] = list(lookup)
        root._bin["header_package"] = package
    if "_package" not in root:
        root["_package"] = package  # mirrors the header; not written as an attribute
    return root


def _first_difference(a: bytes, b: bytes) -> str:
    n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"first difference at byte {n}; sizes {len(a)} vs {len(b)}"


def first_tree_difference(a: Any, b: Any, path: str = "") -> str:
    """Human-readable location of the first difference between two JSON trees ('' if equal)."""
    if type(a) is not type(b):
        return f"{path or '/'}: {a!r:.80} vs {b!r:.80}"
    if isinstance(a, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in a or k not in b:
                return f"{path}/{k}: present on one side only"
            d = first_tree_difference(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        if list(a) != list(b):
            return f"{path}: key order differs"
        return ""
    if isinstance(a, list):
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_tree_difference(x, y, f"{path}[{i}]")
            if d:
                return d
        return f"{path}: length {len(a)} vs {len(b)}" if len(a) != len(b) else ""
    return "" if a == b else f"{path}: {a!r:.80} vs {b!r:.80}"


def json_to_bin(doc: dict) -> tuple[bytes, list[str]]:
    """Serialize a typed JSON document and prove the result is faithful.

    Returns (bytes, notes). Raises MapJsonError, writing nothing, when:
      - the document is malformed or a value does not fit its declared type;
      - it is an unedited dump (tree hash matches "source") but the bytes are
        not identical to the source .bin;
      - re-reading the bytes does not give back the same typed tree.
    """
    header = doc.get("header", "CELESTE MAP")
    tree = json_to_tree(doc)
    want = tree_to_json(tree, header=header)["root"]  # input as understood: stale hints dropped, f32 rounded
    data = cb.serialize_map(tree, header)
    notes: list[str] = []

    src = doc.get("source") or {}
    if src.get("tree_sha256") and src.get("sha256"):
        if tree_sha256(doc["root"]) == src["tree_sha256"]:
            got = hashlib.sha256(data).hexdigest()
            if got != src["sha256"]:
                raise MapJsonError(
                    f"this is an unedited dump of {src.get('file') or 'a map'}, but it serializes "
                    f"to different bytes (sha256 {got[:12]}… vs {src['sha256'][:12]}…). Refusing to "
                    "write a map that does not round-trip byte-identical.")
            notes.append("unedited dump: output is byte-identical to the source map")
        else:
            notes.append("edited dump: unchanged parts keep their original encoding")

    back = tree_to_json(cb.read_map_bytes(data, expected_header=header), header=header)["root"]
    if back != want:
        raise MapJsonError("the written bytes would not read back as the same map ("
                           + first_tree_difference(want, back) + "). Nothing written.")
    return data, notes


def write_json_as_map(doc: dict, out_path: str | Path, backup: bool = True) -> list[str]:
    """json_to_bin + atomic write (temp file, rolling .bak copies, replace)."""
    data, notes = json_to_bin(doc)
    cb.write_map_bytes(out_path, data, doc.get("header", "CELESTE MAP"), backup=backup)
    return notes + [f"wrote {len(data)} bytes to {Path(out_path).name}"]


def dumps(doc: dict, indent: int | None = 1) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=indent, allow_nan=False)
