"""
Loose authoring JSON: hand- or script-written map content that is merged into
an existing .bin without touching anything else.

    {
      "rooms": [
        {"room": "a-05",
         "entities": [{"name": "strawberry", "x": 120, "y": 64}],
         "triggers": [{"name": "CEL/PinkPlatBerryCollectTrigger", "x": 0, "y": 0,
                       "width": 16, "height": 184}]},
        {"room": "a-99", "x": 0, "y": 2000, "width": 320, "height": 184,
         "solids": "...tile rows...",
         "entities": [{"name": "player", "x": 24, "y": 160}]}
      ]
    }

A bare room object, or a list of room objects, is accepted too. Each item has
`name`, `x`, `y`, optional `width`/`height`, `nodes` ([[x, y], ...]),
`placement` (which Lönn placement's defaults to use), `id` (otherwise one is
allocated) and any other attributes, flat or under "data". Keys starting with
"#" are comments.

Missing attributes are filled from the Lönn plugin definitions (the mod's own,
other installed mods', then vanilla). Unknown names are flagged.

A room that does not exist yet is created when it gives x, y, width and
height. New entity/trigger IDs are unique across the whole map and never reuse
an ID that is already present anywhere in it.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from . import celeste_bin as cb
    from . import map_json as mj
    from .lua_defs import Catalog
except ImportError:  # run directly from source
    import celeste_bin as cb
    import map_json as mj
    from lua_defs import Catalog


class AuthoringError(ValueError):
    """The authoring JSON is malformed or cannot be applied to this map."""


SECTIONS = ("entities", "triggers")
_SINGULAR = {"entities": "entity", "triggers": "trigger"}
_ITEM_KEYS = {"name", "x", "y", "width", "height", "nodes", "placement", "id", "data", "attrs"}
_ROOM_KEYS = {"room", "x", "y", "width", "height", "entities", "triggers", "solids", "bg",
              "attrs", "create"}
# Room attributes a new room gets (the same set Lönn writes).
_NEW_ROOM_DEFAULTS: dict[str, Any] = {
    "music": "", "alt_music": "", "ambience": "", "dark": False, "space": False,
    "underwater": False, "whisper": False, "disableDownTransition": False,
    "windPattern": "None", "musicLayer1": True, "musicLayer2": True, "musicLayer3": True,
    "musicLayer4": True, "musicProgress": "", "ambienceProgress": "",
    "cameraOffsetX": 0, "cameraOffsetY": 0, "delayAltMusicFade": False, "c": 0,
}
# Collectibles whose hitbox is roughly 14x14 around their position.
_COLLECTIBLE_HINTS = ("berry", "strawberry", "heart", "cassette", "gem", "key")


# ─── Parsing and resolving ────────────────────────────────────────────────────

@dataclass
class Item:
    room: str
    section: str                      # "entities" | "triggers"
    name: str
    x: int | float
    y: int | float
    attrs: dict[str, Any]             # everything except id/x/y/nodes, defaults filled
    nodes: list[tuple[Any, Any]] = field(default_factory=list)
    id: int | None = None
    defaults_used: list[str] = field(default_factory=list)
    known: bool = True
    origin: str = ""                  # mod / installed / vanilla
    warnings: list[str] = field(default_factory=list)

    def label(self) -> str:
        return f"{self.room}: {self.name} at ({self.x:g},{self.y:g})"


@dataclass
class RoomSpec:
    name: str
    create: dict[str, Any] | None     # attributes for a new room, None = must exist
    tiles: dict[str, str]
    items: list[Item]


@dataclass
class Plan:
    rooms: list[RoomSpec]
    unknown: list[Item]
    warnings: list[str]

    def items(self) -> list[Item]:
        return [it for r in self.rooms for it in r.items]


def _as_rooms(doc: Any) -> list[dict]:
    if isinstance(doc, list):
        return doc
    if isinstance(doc, dict) and "rooms" in doc:
        if not isinstance(doc["rooms"], list):
            raise AuthoringError("'rooms' must be a list")
        return doc["rooms"]
    if isinstance(doc, dict) and "room" in doc:
        return [doc]
    raise AuthoringError("expected {\"rooms\": [...]}, a room object with \"room\", or a list of rooms")


def _number(v: Any, where: str) -> int | float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise AuthoringError(f"{where}: expected a number, got {v!r}")
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _coerce(value: Any, ftype: str | None) -> Any:
    """Apply a Lönn fieldType to a JSON value (JSON has no int/float distinction)."""
    if ftype == "integer" and isinstance(value, float) and value.is_integer():
        return int(value)
    if ftype == "number" and isinstance(value, int) and not isinstance(value, bool):
        return float(value)
    return value


def _item(raw: Any, room: str, section: str, catalog: Catalog, where: str) -> Item:
    if not isinstance(raw, dict):
        raise AuthoringError(f"{where}: each item must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not name:
        raise AuthoringError(f"{where}: missing 'name'")
    if "x" not in raw or "y" not in raw:
        raise AuthoringError(f"{where} ({name}): needs x and y")
    x, y = _number(raw["x"], f"{where}.x"), _number(raw["y"], f"{where}.y")

    given: dict[str, Any] = {}
    for key in ("data", "attrs"):
        if key in raw:
            if not isinstance(raw[key], dict):
                raise AuthoringError(f"{where}: '{key}' must be an object")
            given.update(raw[key])
    for k, v in raw.items():
        if k not in _ITEM_KEYS and not k.startswith("#"):
            given[k] = v
    for k in ("width", "height"):
        if k in raw:
            given[k] = _number(raw[k], f"{where}.{k}")
    for k, v in given.items():
        if isinstance(v, (dict, list)) or v is None:
            raise AuthoringError(f"{where}: attribute '{k}' must be a string, number or boolean")
    for k in ("__name", "__children"):
        if k in given:
            raise AuthoringError(f"{where}: '{k}' is reserved")

    nodes = []
    for n, node in enumerate(raw.get("nodes", []) or []):
        if isinstance(node, dict):
            node = [node.get("x"), node.get("y")]
        if not isinstance(node, (list, tuple)) or len(node) != 2:
            raise AuthoringError(f"{where}: node {n} must be [x, y]")
        nodes.append((_number(node[0], f"{where}.nodes[{n}]"), _number(node[1], f"{where}.nodes[{n}]")))

    eid = raw.get("id")
    if eid is not None and (isinstance(eid, bool) or not isinstance(eid, int)):
        raise AuthoringError(f"{where}: 'id' must be an integer")

    item = Item(room, section, name, x, y, {}, nodes, eid)
    definition = catalog.get(name, section)
    other = catalog.get(name, "triggers" if section == "entities" else "entities")
    attrs: dict[str, Any] = {}
    if definition is None:
        item.known = False
        if other is not None:
            item.warnings.append(f"'{name}' is defined as a {_SINGULAR[other.kind]}; it is listed under {section}")
        else:
            item.warnings.append(f"unknown {_SINGULAR[section]} '{name}': no Lönn definition found")
    else:
        item.origin = definition.origin
        try:
            placement = definition.placement(raw.get("placement", ""))
        except KeyError:
            names = ", ".join(p.name for p in definition.placements) or "none"
            raise AuthoringError(f"{where}: '{name}' has no placement '{raw.get('placement')}' (has: {names})")
        if placement is not None:
            for k, v in placement.data.items():
                if k not in given and k not in ("x", "y"):
                    attrs[k] = v
                    item.defaults_used.append(k)
            missing = [k for k in placement.computed if k not in given]
            if missing:
                item.warnings.append(f"'{name}': no literal default for {', '.join(missing)}; set it explicitly")
        elif not definition.parsed:
            item.warnings.append(f"'{name}': definition found but its defaults are computed in Lua; none filled")
        lo_hi = definition.node_limits
        if lo_hi:
            lo, hi = lo_hi
            if len(nodes) < lo or (hi >= 0 and len(nodes) > hi):
                item.warnings.append(f"'{name}' takes {lo}..{'∞' if hi < 0 else hi} nodes, got {len(nodes)}")
        ftypes = definition.field_types
        given = {k: _coerce(v, ftypes.get(k)) for k, v in given.items()}
        attrs = {k: _coerce(v, ftypes.get(k)) for k, v in attrs.items()}
    if section == "triggers":
        for k in ("width", "height"):
            if k not in given and k not in attrs:
                attrs[k] = 16
                item.defaults_used.append(k)
    attrs.update(given)
    item.attrs = attrs
    return item


def resolve(doc: Any, catalog: Catalog) -> Plan:
    """Parse authoring JSON and fill in defaults. Does not look at any map."""
    rooms: list[RoomSpec] = []
    seen: dict[str, RoomSpec] = {}
    for r_i, raw in enumerate(_as_rooms(doc)):
        where = f"rooms[{r_i}]"
        if not isinstance(raw, dict) or not isinstance(raw.get("room"), str) or not raw["room"]:
            raise AuthoringError(f"{where}: each room needs a 'room' name")
        name = raw["room"]
        unknown = [k for k in raw if k not in _ROOM_KEYS and not k.startswith("#")]
        if unknown:
            raise AuthoringError(f"{where} ({name}): unknown key(s) {unknown}; "
                                 "put room attributes under \"attrs\"")
        create = None
        if all(k in raw for k in ("x", "y", "width", "height")):
            create = {k: _number(raw[k], f"{where}.{k}") for k in ("x", "y", "width", "height")}
            create.update(raw.get("attrs", {}) or {})
        elif raw.get("attrs") or raw.get("solids") or raw.get("bg") or raw.get("create"):
            raise AuthoringError(f"{where} ({name}): room attributes and tiles are only accepted when "
                                 "creating a room (give x, y, width and height). Existing rooms are "
                                 "never modified, only added to.")
        tiles = {k: raw[k] for k in ("solids", "bg") if isinstance(raw.get(k), str)}
        items = []
        for section in SECTIONS:
            lst = raw.get(section, []) or []
            if not isinstance(lst, list):
                raise AuthoringError(f"{where}.{section} must be a list")
            items += [_item(it, name, section, catalog, f"{where}.{section}[{i}]") for i, it in enumerate(lst)]
        if name in seen:  # the same room listed twice: merge
            if create:
                raise AuthoringError(f"{where}: room '{name}' is listed twice with creation data")
            seen[name].items += items
            continue
        spec = RoomSpec(name, create, tiles, items)
        seen[name] = spec
        rooms.append(spec)
    plan = Plan(rooms, [], [])
    plan.unknown = [it for it in plan.items() if not it.known]
    return plan


# ─── Applying to a map ────────────────────────────────────────────────────────

def _find_room(tree: dict, name: str) -> dict | None:
    rooms = cb.get_rooms(tree)
    for r in rooms:
        if r.get("name") == name:
            return r
    alt = name[4:] if name.startswith("lvl_") else f"lvl_{name}"
    return next((r for r in rooms if r.get("name") == alt), None)


def map_ids(tree: dict) -> set[int]:
    """Every entity/trigger ID anywhere in the map."""
    ids: set[int] = set()
    for room in cb.get_rooms(tree):
        for section in SECTIONS:
            el = cb.find_child(room, section)
            for e in (el or {}).get("__children", []):
                v = e.get("id")
                if isinstance(v, int) and not isinstance(v, bool):
                    ids.add(v)
    return ids


class IdAllocator:
    """Hands out IDs above the highest one in the map; explicit IDs must be unused."""

    def __init__(self, used: set[int]):
        self.used = set(used)
        self.next = max(self.used, default=0) + 1

    def take(self, wanted: int | None, label: str) -> int:
        if wanted is not None:
            if wanted in self.used:
                raise AuthoringError(f"{label}: id {wanted} is already used in this map")
            self.used.add(wanted)
            self.next = max(self.next, wanted + 1)
            return wanted
        while self.next in self.used:
            self.next += 1
        if self.next > 2**31 - 1:
            raise AuthoringError("ran out of entity IDs")
        self.used.add(self.next)
        self.next += 1
        return self.next - 1


def _element(item: Item, eid: int) -> dict:
    el: dict[str, Any] = {"__name": item.name, "id": eid, "x": item.x, "y": item.y}
    for k in ("width", "height"):
        if k in item.attrs:
            el[k] = item.attrs[k]
    for k, v in item.attrs.items():
        if k not in el:
            el[k] = v
    el["__children"] = [{"__name": "node", "x": nx, "y": ny, "__children": []} for nx, ny in item.nodes]
    return el


def _fit_tiles(text: str, w: int, h: int, fill: str) -> str:
    rows = text.strip("\n").split("\n") if text.strip("\n") else []
    rows = [r[:w].ljust(w, fill) for r in rows[:h]]
    rows += [fill * w] * (h - len(rows))
    return "\n".join(rows)


def _new_room(spec: RoomSpec) -> dict:
    c = dict(spec.create or {})
    x, y, w, h = (c.pop(k) for k in ("x", "y", "width", "height"))
    if w <= 0 or h <= 0 or w % 8 or h % 8:
        raise AuthoringError(f"room '{spec.name}': width/height must be positive multiples of 8")
    tw, th = w // 8, h // 8
    obj = "\n".join([",".join(["-1"] * tw)] * th)
    room: dict[str, Any] = {"__name": "level", "name": spec.name, "x": x, "y": y, "width": w, "height": h}
    room.update(_NEW_ROOM_DEFAULTS)
    room.update(c)
    room["__children"] = [
        {"__name": "solids", "innerText": _fit_tiles(spec.tiles.get("solids", ""), tw, th, "0"),
         "offsetX": 0, "offsetY": 0, "__children": []},
        {"__name": "bg", "innerText": _fit_tiles(spec.tiles.get("bg", ""), tw, th, "0"),
         "offsetX": 0, "offsetY": 0, "__children": []},
        {"__name": "objtiles", "innerText": obj, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
        {"__name": "fgtiles", "innerText": obj, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
        {"__name": "bgtiles", "innerText": obj, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
        {"__name": "entities", "__children": []},
        {"__name": "triggers", "__children": []},
        {"__name": "fgdecals", "__children": []},
        {"__name": "bgdecals", "__children": []},
    ]
    return room


def _overlaps(a: dict, b: dict) -> bool:
    return (a["x"] < b["x"] + b["width"] and b["x"] < a["x"] + a["width"]
            and a["y"] < b["y"] + b["height"] and b["y"] < a["y"] + a["height"])


@dataclass
class Change:
    room: str
    kind: str                     # "room" | "entities" | "triggers"
    element: dict
    item: Item | None = None
    checks: list[str] = field(default_factory=list)   # problems found by placement checks


@dataclass
class MergeResult:
    changes: list[Change]
    warnings: list[str]
    validator_issues: dict[str, list[str]]  # room -> issues the merge introduced
    tree: dict
    top_level_additions: int = 0            # new rooms + new containers + items added to existing containers

    @property
    def problems(self) -> list[str]:
        return [f"{c.room}: {c.element.get('__name')} id={c.element.get('id')}: {p}"
                for c in self.changes for p in c.checks]


def apply(tree: dict, plan: Plan, check: bool = True) -> MergeResult:
    """Add everything in the plan to `tree` (in place). Raises AuthoringError
    before touching the tree if anything cannot be applied."""
    warnings: list[str] = []
    ids = IdAllocator(map_ids(tree))
    levels = cb.find_child(tree, "levels")
    if levels is None:
        raise AuthoringError("map has no 'levels' element")

    # Resolve all rooms first so nothing is half-applied on error.
    targets: list[tuple[RoomSpec, dict | None]] = []
    prefixed = sum(1 for r in cb.get_rooms(tree) if str(r.get("name", "")).startswith("lvl_"))
    total = len(cb.get_rooms(tree))
    for spec in plan.rooms:
        room = _find_room(tree, spec.name)
        if room is None and spec.create is None:
            raise AuthoringError(f"room '{spec.name}' does not exist; give x, y, width and height to create it")
        if room is not None and spec.create is not None:
            raise AuthoringError(f"room '{spec.name}' already exists; drop x/y/width/height to add to it")
        if room is None:
            new_box = {k: spec.create[k] for k in ("x", "y", "width", "height")}
            for r in cb.get_rooms(tree):
                if _overlaps(new_box, {k: r.get(k, 0) for k in ("x", "y", "width", "height")}):
                    raise AuthoringError(f"new room '{spec.name}' overlaps existing room '{r.get('name')}'")
            if total and (prefixed == total) != spec.name.startswith("lvl_"):
                warnings.append(f"new room '{spec.name}': other rooms in this map "
                                f"{'use' if prefixed == total else 'do not use'} the 'lvl_' prefix")
        targets.append((spec, room))

    before = {id(room): _validator_issues(room) for _, room in targets if room is not None} if check else {}
    planned: list[tuple[RoomSpec, dict | None, list[tuple[Item, int]]]] = []
    for spec, room in targets:
        planned.append((spec, room, [(it, ids.take(it.id, it.label())) for it in spec.items]))

    changes: list[Change] = []
    issues: dict[str, list[str]] = {}
    reach = _ReachCache(tree)
    # Count what a re-read of the output should show as appended at top level:
    # a new room (with everything in it), a new container in an existing room
    # (with its items), or each item added to a container that already existed.
    top = 0
    for spec, room, items in planned:
        is_new = room is None
        if is_new:
            room = _new_room(spec)
            levels["__children"].append(room)
            changes.append(Change(spec.name, "room", {k: v for k, v in room.items() if k != "__children"}))
            top += 1
        created: set[str] = set()
        for item, eid in items:
            container = cb.find_child(room, item.section)
            if container is None:
                container = {"__name": item.section, "__children": []}
                room["__children"].append(container)
                created.add(item.section)
                top += 0 if is_new else 1
            elif not is_new and item.section not in created:
                top += 1
            el = _element(item, eid)
            container["__children"].append(el)
            ch = Change(room.get("name", spec.name), item.section, el, item)
            if check:
                ch.checks = check_placement(room, el, item.section, reach)
            changes.append(ch)
        if check:
            new = [i for i in _validator_issues(room) if i not in before.get(id(room), [])]
            if new:
                issues[room.get("name", spec.name)] = new
    for it in plan.items():
        warnings += [f"{it.label()}: {w}" for w in it.warnings]
    return MergeResult(changes, warnings, issues, tree, top)


# ─── Placement checks ─────────────────────────────────────────────────────────

class TileGrid:
    """A room's foreground solids grid ('0' or missing = air)."""

    def __init__(self, room: dict):
        solids = cb.find_child(room, "solids")
        text = (solids or {}).get("innerText", "") or ""
        self.rows = text.split("\n")
        self.w = max(1, int(room.get("width", 0)) // 8)
        self.h = max(1, int(room.get("height", 0)) // 8)

    def solid(self, tx: int, ty: int) -> bool:
        if not (0 <= tx < self.w and 0 <= ty < self.h):
            return False
        if ty >= len(self.rows) or tx >= len(self.rows[ty]):
            return False
        return self.rows[ty][tx] not in "0 "

    def reachable_from_entry(self, room: dict, entries: list[tuple[int, int]] | None = None,
                             blocked: set[tuple[int, int]] | None = None) -> list[list[bool]]:
        """Air cells connected (4-way) to an entry: a player spawn, or an edge cell
        where the room meets a neighbour (every edge cell when `entries` is None).

        Solid tiles and `blocked` cells (hazards, killboxes, barriers) stop the
        flood. It ignores gravity and abilities, so it proves an object is not
        sealed off, not that it is easy to reach."""
        blocked = blocked or set()
        seen = [[False] * self.w for _ in range(self.h)]
        if entries is None:
            entries = [(tx, ty) for tx in range(self.w) for ty in (0, self.h - 1)]
            entries += [(tx, ty) for ty in range(self.h) for tx in (0, self.w - 1)]
        stack = list(entries)
        ents = cb.find_child(room, "entities")
        for e in (ents or {}).get("__children", []):
            if e.get("__name") == "player":
                px, py = int(e.get("x", 0)) // 8, int(e.get("y", 0)) // 8
                stack += [(px, py - 1), (px, py - 2)]
        while stack:
            tx, ty = stack.pop()
            if not (0 <= tx < self.w and 0 <= ty < self.h) or seen[ty][tx] or self.solid(tx, ty) \
                    or (tx, ty) in blocked:
                continue
            seen[ty][tx] = True
            stack += [(tx + 1, ty), (tx - 1, ty), (tx, ty + 1), (tx, ty - 1)]
        return seen


_SPINNERS = ("spinner", "rotateSpinner", "bigSpinner", "trackSpinner", "dustTrackSpinner",
             "dustRotateSpinner", "bladeTrackSpinner", "bladeRotateSpinner", "starTrackSpinner")


def _rect_cells(x: float, y: float, w: float, h: float) -> set[tuple[int, int]]:
    if w <= 0 or h <= 0:
        return set()
    return {(tx, ty) for ty in range(int(y // 8), int((y + h - 1) // 8) + 1)
            for tx in range(int(x // 8), int((x + w - 1) // 8) + 1)}


def blocked_cells(room: dict) -> set[tuple[int, int]]:
    """Cells the player cannot pass: killboxes, invisible barriers, spikes, spinners."""
    out: set[tuple[int, int]] = set()
    for e in (cb.find_child(room, "entities") or {}).get("__children", []):
        n = str(e.get("__name", ""))
        x, y = e.get("x", 0) or 0, e.get("y", 0) or 0
        w, h = e.get("width", 0) or 0, e.get("height", 0) or 0
        if n == "killbox":
            out |= _rect_cells(x, y, w, h or 32)
        elif n in ("invisibleBarrier", "seekerBarrier"):
            out |= _rect_cells(x, y, w, h)
        elif n.startswith(("spikes", "triggerSpikes")):
            if n.endswith("Up"):
                out |= _rect_cells(x, y - 8, w or 8, 8)
            elif n.endswith("Down"):
                out |= _rect_cells(x, y, w or 8, 8)
            elif n.endswith("Left"):
                out |= _rect_cells(x - 8, y, 8, h or 8)
            elif n.endswith("Right"):
                out |= _rect_cells(x, y, 8, h or 8)
        elif n in _SPINNERS:
            r = 16 if n == "bigSpinner" else 8
            for ty in range(int((y - r) // 8), int((y + r) // 8) + 1):
                for tx in range(int((x - r) // 8), int((x + r) // 8) + 1):
                    if (tx * 8 + 4 - x) ** 2 + (ty * 8 + 4 - y) ** 2 < r * r:
                        out.add((tx, ty))
    return out


def entry_cells(tree: dict | None, room: dict) -> list[tuple[int, int]] | None:
    """Edge cells where `room` meets another room (screen transitions).
    None when there is no map context or the room touches no other room."""
    if tree is None:
        return None
    rx, ry, rw, rh = (room.get(k, 0) for k in ("x", "y", "width", "height"))
    tw, th = rw // 8, rh // 8
    out: list[tuple[int, int]] = []
    for o in cb.get_rooms(tree):
        if o is room:
            continue
        ox, oy, ow, oh = (o.get(k, 0) for k in ("x", "y", "width", "height"))
        if ox + ow == rx or rx + rw == ox:          # left / right neighbour
            lo, hi = max(ry, oy), min(ry + rh, oy + oh)
            tx = 0 if ox + ow == rx else tw - 1
            out += [(tx, (py - ry) // 8) for py in range(lo, hi, 8)]
        if oy + oh == ry or ry + rh == oy:          # top / bottom neighbour
            lo, hi = max(rx, ox), min(rx + rw, ox + ow)
            ty = 0 if oy + oh == ry else th - 1
            out += [((px - rx) // 8, ty) for px in range(lo, hi, 8)]
    return out or None


class _ReachCache:
    """Per-room reachability, computed once per merge."""

    def __init__(self, tree: dict | None):
        self.tree = tree
        self._grids: dict[int, list[list[bool]]] = {}

    def __call__(self, room: dict) -> list[list[bool]]:
        key = id(room)
        if key not in self._grids:
            self._grids[key] = TileGrid(room).reachable_from_entry(
                room, entry_cells(self.tree, room), blocked_cells(room))
        return self._grids[key]


def check_placement(room: dict, el: dict, section: str, reach=None) -> list[str]:
    """Problems with where an added object sits: bounds, solid tiles, and whether
    it is cut off from every spawn and room transition. `reach` is a callable
    room -> reachability grid (see _ReachCache); without one, every room edge
    counts as an entrance."""
    out: list[str] = []
    w, h = room.get("width", 0), room.get("height", 0)
    x, y = el.get("x", 0), el.get("y", 0)
    ew, eh = el.get("width", 0) or 0, el.get("height", 0) or 0
    if section == "triggers" or ew or eh:
        if x < 0 or y < 0 or x + ew > w or y + eh > h:
            out.append(f"rectangle ({x:g},{y:g},{ew:g}x{eh:g}) leaves the room ({w}x{h})")
        if section == "triggers":
            return out
    elif not (0 <= x < w and 0 <= y < h):
        out.append(f"position ({x:g},{y:g}) is outside the room ({w}x{h})")
        return out

    grid = TileGrid(room)
    standing = (not (ew or eh) and float(y).is_integer() and int(y) % 8 == 0 and y > 0
                and grid.solid(int(x) // 8, int(y) // 8) and not grid.solid(int(x) // 8, int(y) // 8 - 1))
    name = str(el.get("__name", "")).lower()
    half = 6 if any(h_ in name for h_ in _COLLECTIBLE_HINTS) else 3
    if ew or eh:
        box = (x, y, x + ew, y + eh)
    elif standing:
        # Bottom-anchored (player, spring, ...) exactly on the floor line: it stands on the
        # floor, so check the space above its feet instead of the floor tile under them.
        box = (x - half, y - 2 * half, x + half, y)
        y = y - 1
    else:
        box = (x - half, y - half, x + half, y + half)
    cells = [(tx, ty) for ty in range(int(box[1]) // 8, (int(box[3]) - 1) // 8 + 1)
             for tx in range(int(box[0]) // 8, (int(box[2]) - 1) // 8 + 1)]
    solid = [c for c in cells if grid.solid(*c)]
    if not (ew or eh) and grid.solid(int(x) // 8, int(y) // 8):
        out.append("sits inside a solid tile")
    elif solid and not (ew or eh) and len(solid) * 2 >= len(cells):
        out.append(f"mostly overlaps solid tiles ({len(solid)}/{len(cells)} cells)")
    if not (ew or eh) and not grid.solid(int(x) // 8, int(y) // 8):
        seen = reach(room) if reach is not None else grid.reachable_from_entry(room, None, blocked_cells(room))
        if not seen[int(y) // 8][int(x) // 8]:
            out.append("is cut off from every spawn and room transition "
                       "(by solid tiles, spikes, spinners, killboxes or barriers)")
    for i, node in enumerate(n for n in el.get("__children", []) if n.get("__name") == "node"):
        nx, ny = node.get("x", 0), node.get("y", 0)
        if not (0 <= nx < w and 0 <= ny < h):
            out.append(f"node {i} ({nx:g},{ny:g}) is outside the room")
        elif grid.solid(int(nx) // 8, int(ny) // 8):
            out.append(f"node {i} ({nx:g},{ny:g}) is inside a solid tile")
    return out


def _validator_issues(room: dict) -> list[str]:
    """The server's existing room validators (pcg + gdep), as one list."""
    try:
        from . import pcg, gdep_tools
    except ImportError:
        import pcg
        import gdep_tools
    issues = list(pcg.validate_room_structure(room))
    issues += gdep_tools.validate_and_fix_room(copy.deepcopy(room), auto_fix=False)["issues"]
    return issues


# ─── Verification and diff ────────────────────────────────────────────────────

def additive_only(old_root: dict, new_root: dict, path: str = "") -> list[tuple[str, dict]]:
    """Compare two typed-JSON trees. Returns the elements `new` adds; raises
    AuthoringError if anything that was already there changed."""
    here = f"{path}/{old_root.get('name')}"
    if old_root.get("name") != new_root.get("name") or old_root.get("attrs") != new_root.get("attrs") \
            or old_root.get("name_index") != new_root.get("name_index"):
        diff = mj.first_tree_difference({k: old_root.get(k) for k in ("name", "attrs")},
                                        {k: new_root.get(k) for k in ("name", "attrs")}, here)
        raise AuthoringError(f"existing data changed at {diff}")
    old_kids, new_kids = old_root.get("children", []), new_root.get("children", [])
    if len(new_kids) < len(old_kids):
        raise AuthoringError(f"elements were removed under {here}")
    added: list[tuple[str, dict]] = []
    for i, (a, b) in enumerate(zip(old_kids, new_kids)):
        added += additive_only(a, b, f"{here}[{i}]")
    added += [(here, k) for k in new_kids[len(old_kids):]]
    return added


def merge_bytes(original: bytes, doc: Any, catalog: Catalog, allow_unknown: bool = False,
                check: bool = True) -> tuple[bytes, MergeResult]:
    """Merge authoring JSON into .bin bytes. Returns the new bytes and what changed.

    Raises AuthoringError (and produces nothing) on unknown names unless
    allow_unknown, on bad input, or if the output would change anything that
    was already in the map."""
    plan = resolve(doc, catalog)
    if plan.unknown and not allow_unknown:
        names = sorted({f"{it.name} ({it.section})" for it in plan.unknown})
        raise AuthoringError("unknown names (no Lönn definition): " + ", ".join(names)
                             + ". Fix the names, or pass allow_unknown to place them anyway.")
    tree = cb.read_map_bytes(original)
    old_doc = mj.tree_to_json(tree)
    result = apply(tree, plan, check=check)
    if not result.changes:
        raise AuthoringError("nothing to add")
    new_bytes = cb.serialize_map(tree)

    # Prove it on the real output: re-read the bytes and compare with the original.
    new_doc = mj.tree_to_json(cb.read_map_bytes(new_bytes))
    if new_doc["lookup"][:len(old_doc["lookup"])] != old_doc["lookup"]:
        raise AuthoringError("the string table was reordered; refusing to write")
    added = additive_only(old_doc["root"], new_doc["root"])
    expected = result.top_level_additions
    if len(added) != expected:
        raise AuthoringError(f"expected {expected} added elements in the output, found {len(added)}")
    return new_bytes, result


def describe(result: MergeResult, max_attrs: int = 8) -> str:
    """Readable diff of a merge."""
    lines: list[str] = []
    by_room: dict[str, list[Change]] = {}
    for c in result.changes:
        by_room.setdefault(c.room, []).append(c)
    n_items = sum(1 for c in result.changes if c.kind != "room")
    n_rooms = sum(1 for c in result.changes if c.kind == "room")
    lines.append(f"{n_items} object(s) in {len(by_room)} room(s)" + (f", {n_rooms} new room(s)" if n_rooms else ""))
    for room, chs in by_room.items():
        lines.append(f"\n[{room}]")
        for c in chs:
            el = c.element
            if c.kind == "room":
                lines.append(f"  + new room at ({el.get('x')},{el.get('y')}) {el.get('width')}x{el.get('height')}")
                continue
            extra = {k: v for k, v in el.items() if k not in ("__name", "__children", "id", "x", "y")}
            shown = ", ".join(f"{k}={json.dumps(v)}" for k, v in list(extra.items())[:max_attrs])
            if len(extra) > max_attrs:
                shown += f", … (+{len(extra) - max_attrs})"
            nodes = [n for n in el.get("__children", []) if n.get("__name") == "node"]
            node_txt = f" nodes={[(n['x'], n['y']) for n in nodes]}" if nodes else ""
            mark = "!" if c.checks else "+"
            lines.append(f"  {mark} {_SINGULAR[c.kind]} {el['__name']} id={el['id']} at ({el['x']:g},{el['y']:g})"
                         f"{node_txt}" + (f"  {{{shown}}}" if shown else ""))
            for p in c.checks:
                lines.append(f"      ✗ {p}")
    if result.validator_issues:
        lines.append("\nRoom validator issues introduced by this merge:")
        for room, issues in result.validator_issues.items():
            lines += [f"  {room}: {i}" for i in issues]
    if result.warnings:
        lines.append("\nWarnings:")
        lines += [f"  - {w}" for w in result.warnings]
    return "\n".join(lines)


def load_doc(text_or_path: str, base: Path | None = None) -> Any:
    """Authoring JSON given inline or as a path to a .json file."""
    s = text_or_path.strip()
    if s.startswith(("{", "[")):
        return json.loads(s)
    p = Path(s)
    if base is not None and not p.is_absolute():
        p = base / p
    return json.loads(p.read_text(encoding="utf-8"))


def js_cli_path() -> Path:
    """The loenn-author CLI: shipped inside the package, or js/ next to it in a checkout."""
    here = Path(__file__).resolve().parent
    for cand in (here / "js" / "bin" / "loenn-author.js", here.parent / "js" / "bin" / "loenn-author.js"):
        if cand.exists():
            return cand
    return here.parent / "js" / "bin" / "loenn-author.js"


def overlay_from_result(result: MergeResult) -> list[dict]:
    """Added objects in a shape the HTML preview can highlight."""
    out = []
    for c in result.changes:
        if c.kind == "room":
            continue
        el = c.element
        out.append({"room": c.room, "kind": c.kind, "name": el["__name"], "id": el["id"],
                    "x": el["x"], "y": el["y"], "width": el.get("width", 0), "height": el.get("height", 0),
                    "problems": c.checks})
    return out

