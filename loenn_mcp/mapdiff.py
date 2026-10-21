"""
Semantic diff of two Celeste maps.

Lönn rewrites the whole file on save (new string table, rooms sorted), so byte
comparison says nothing useful. This compares meaning: rooms by name (with or
without the lvl_ prefix), room attributes and geometry, foreground/background
tile cells, entities and triggers by id, decals by texture and position,
stylegrounds and map metadata.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

try:
    from . import celeste_bin as cb
except ImportError:  # run directly from source
    import celeste_bin as cb

_ROOM_CHILD_TILES = {"solids": "fg", "bg": "bg"}
_OBJECT_SECTIONS = ("entities", "triggers")
_SINGULAR = {"entities": "entity", "triggers": "trigger"}
_DECAL_SECTIONS = ("fgdecals", "bgdecals")


def short_name(name: str) -> str:
    return name[4:] if name.startswith("lvl_") else name


def _attrs(el: dict) -> dict[str, Any]:
    return {k: v for k, v in el.items() if k not in ("__name", "__children", "_package")}


def _norm(v: Any) -> Any:
    # 1 and 1.0 mean the same in a map; Lönn may switch between them.
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _attr_changes(a: dict, b: dict, skip: tuple[str, ...] = ()) -> list[str]:
    out = []
    for k in sorted(set(a) | set(b)):
        if k in skip:
            continue
        va, vb = a.get(k, "<missing>"), b.get(k, "<missing>")
        if _norm(va) != _norm(vb):
            out.append(f"{k}: {va!r} -> {vb!r}")
    return out


def _rows(room: dict, child: str) -> list[str]:
    el = cb.find_child(room, child)
    text = el.get("innerText", "") if el else ""
    return text.split("\n") if text else []


def _tile_delta(ra: list[str], rb: list[str]) -> tuple[int, tuple[int, int, int, int] | None]:
    """Changed cell count and bounding box (tile units, inclusive). Missing cells count as air '0'."""
    n = 0
    box = None
    for y in range(max(len(ra), len(rb))):
        a = ra[y] if y < len(ra) else ""
        b = rb[y] if y < len(rb) else ""
        for x in range(max(len(a), len(b))):
            ca = a[x] if x < len(a) else "0"
            cb_ = b[x] if x < len(b) else "0"
            if ca != cb_:
                n += 1
                box = (x, y, x, y) if box is None else (min(box[0], x), min(box[1], y), max(box[2], x), max(box[3], y))
    return n, box


def _objects(room: dict, section: str) -> dict[Any, dict]:
    el = cb.find_child(room, section)
    out: dict[Any, dict] = {}
    for i, o in enumerate(el.get("__children", []) if el else []):
        key = o.get("id")
        if key is None or key in out:
            key = ("noid", o.get("__name"), i)
        out[key] = o
    return out


def _nodes(o: dict) -> list[tuple[Any, Any]]:
    return [(_norm(n.get("x")), _norm(n.get("y"))) for n in o.get("__children", []) if n.get("__name") == "node"]


def _decals(room: dict, section: str) -> Counter:
    el = cb.find_child(room, section)
    return Counter((d.get("texture"), _norm(d.get("x")), _norm(d.get("y")), _norm(d.get("scaleX", 1)),
                    _norm(d.get("scaleY", 1))) for d in (el.get("__children", []) if el else []))


@dataclass
class RoomDiff:
    name: str
    changes: list[str] = field(default_factory=list)

    def empty(self) -> bool:
        return not self.changes


@dataclass
class MapDiff:
    rooms_added: list[str] = field(default_factory=list)
    rooms_removed: list[str] = field(default_factory=list)
    rooms_changed: list[RoomDiff] = field(default_factory=list)
    map_changes: list[str] = field(default_factory=list)
    byte_identical: bool = False

    @property
    def identical(self) -> bool:
        return not (self.rooms_added or self.rooms_removed or self.rooms_changed or self.map_changes)

    def summary(self) -> dict:
        return {"identical": self.identical, "byte_identical": self.byte_identical,
                "rooms_added": self.rooms_added, "rooms_removed": self.rooms_removed,
                "rooms_changed": {r.name: r.changes for r in self.rooms_changed},
                "map_changes": self.map_changes}

    def text(self, limit: int = 200) -> str:
        if self.identical:
            return "No semantic differences" + (" (byte-identical)." if self.byte_identical else
                                                " (bytes differ only in encoding).")
        lines = []
        if self.rooms_added:
            lines.append(f"Rooms added ({len(self.rooms_added)}): {', '.join(self.rooms_added)}")
        if self.rooms_removed:
            lines.append(f"Rooms removed ({len(self.rooms_removed)}): {', '.join(self.rooms_removed)}")
        for c in self.map_changes:
            lines.append(f"Map: {c}")
        for r in self.rooms_changed:
            lines.append(f"Room {r.name}:")
            lines += [f"  {c}" for c in r.changes]
        if len(lines) > limit:
            lines = lines[:limit] + [f"... {len(lines) - limit} more lines"]
        return "\n".join(lines)


def diff_rooms(a: dict, b: dict) -> list[str]:
    out = []
    geo = ("x", "y", "width", "height")
    ga, gb = tuple(a.get(k) for k in geo), tuple(b.get(k) for k in geo)
    if ga != gb:
        out.append(f"geometry {ga} -> {gb}")
    out += [f"attr {c}" for c in _attr_changes(_attrs(a), _attrs(b), skip=geo + ("name",))]
    for child, label in _ROOM_CHILD_TILES.items():
        n, box = _tile_delta(_rows(a, child), _rows(b, child))
        if n:
            out.append(f"{label} tiles: {n} cell(s) changed in x {box[0]}..{box[2]}, y {box[1]}..{box[3]}")
    for section in _OBJECT_SECTIONS:
        oa, ob = _objects(a, section), _objects(b, section)
        for k in oa.keys() - ob.keys():
            o = oa[k]
            out.append(f"{_SINGULAR[section]} removed: {o.get('__name')} id={o.get('id')} at ({o.get('x')}, {o.get('y')})")
        for k in ob.keys() - oa.keys():
            o = ob[k]
            out.append(f"{_SINGULAR[section]} added: {o.get('__name')} id={o.get('id')} at ({o.get('x')}, {o.get('y')})")
        for k in oa.keys() & ob.keys():
            x, y = oa[k], ob[k]
            ch = []
            if x.get("__name") != y.get("__name"):
                ch.append(f"type {x.get('__name')} -> {y.get('__name')}")
            ch += _attr_changes(_attrs(x), _attrs(y))
            if _nodes(x) != _nodes(y):
                ch.append(f"nodes {_nodes(x)} -> {_nodes(y)}")
            if ch:
                out.append(f"{_SINGULAR[section]} changed: {y.get('__name')} id={y.get('id')}: " + "; ".join(ch))
    for section in _DECAL_SECTIONS:
        da, db = _decals(a, section), _decals(b, section)
        removed, added = da - db, db - da
        if removed or added:
            out.append(f"{section}: +{sum(added.values())} -{sum(removed.values())}"
                       + (f" (e.g. +{next(iter(added))[0]})" if added else ""))
    return out


def _named_children(el: dict | None) -> list[tuple[str, dict]]:
    return [(c.get("__name", ""), c) for c in (el.get("__children", []) if el else [])]


def diff_maps(a: dict, b: dict, bytes_a: bytes | None = None, bytes_b: bytes | None = None) -> MapDiff:
    d = MapDiff(byte_identical=bytes_a is not None and bytes_a == bytes_b)
    ra = {short_name(str(r.get("name", ""))): r for r in cb.get_rooms(a)}
    rb = {short_name(str(r.get("name", ""))): r for r in cb.get_rooms(b)}
    d.rooms_added = sorted(rb.keys() - ra.keys())
    d.rooms_removed = sorted(ra.keys() - rb.keys())
    for name in sorted(ra.keys() & rb.keys()):
        ch = diff_rooms(ra[name], rb[name])
        if ch:
            d.rooms_changed.append(RoomDiff(name, ch))

    if a.get("_package") != b.get("_package"):
        d.map_changes.append(f"package {a.get('_package')!r} -> {b.get('_package')!r}")
    for section in ("Style", "meta", "Filler"):
        ea, eb = cb.find_child(a, section), cb.find_child(b, section)
        if (ea is None) != (eb is None):
            d.map_changes.append(f"{section} {'added' if ea is None else 'removed'}")
            continue
        if ea is None:
            continue
        if section == "Style":
            for layer in ("Backgrounds", "Foregrounds"):
                la, lb = _named_children(cb.find_child(ea, layer)), _named_children(cb.find_child(eb, layer))
                sa = Counter((n, tuple(sorted((k, _norm(v)) for k, v in _attrs(c).items()))) for n, c in la)
                sb = Counter((n, tuple(sorted((k, _norm(v)) for k, v in _attrs(c).items()))) for n, c in lb)
                if sa != sb:
                    d.map_changes.append(f"stylegrounds {layer}: {len(la)} -> {len(lb)} entries, "
                                         f"{sum((sb - sa).values())} new/changed")
        elif section == "Filler":
            if len(ea.get("__children", [])) != len(eb.get("__children", [])):
                d.map_changes.append(f"filler rects {len(ea.get('__children', []))} -> {len(eb.get('__children', []))}")
        else:
            d.map_changes += [f"meta {c}" for c in _attr_changes(_attrs(ea), _attrs(eb))]
            for name, child in _named_children(ea):
                other = cb.find_child(eb, name)
                if other is not None:
                    d.map_changes += [f"meta/{name} {c}" for c in _attr_changes(_attrs(child), _attrs(other))]
    return d


def diff_bytes(a: bytes, b: bytes) -> MapDiff:
    return diff_maps(cb.read_map_bytes(a), cb.read_map_bytes(b), a, b)
