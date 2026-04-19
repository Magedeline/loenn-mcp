"""
Lönn MCP Server — Celeste Map Editor for AI Agents

Provides tools for reading, editing, analyzing, and generating Celeste map
files (.bin) directly from VS Code via the Model Context Protocol.

Tools:
  Map Reading:    list_maps, read_map_overview, read_room, get_room_tiles
  Map Editing:    add_entity, remove_entity, set_room_tiles, add_room,
                  remove_room, create_map
  Entity Catalog: list_entity_definitions, get_entity_definition,
                  list_trigger_definitions
  Analysis:       analyze_map, visualize_map_layout
  Generation:     build_pattern_library, generate_room_from_pattern,
                  validate_room, ingest_external_map

Usage:
  python server.py                         (uses cwd as workspace)
  LOENN_MCP_WORKSPACE=/path python server.py  (explicit workspace)
"""

import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from fastmcp import FastMCP

try:
    from . import celeste_bin as cb          # installed package
    from . import pcg                        # installed package
except ImportError:
    import celeste_bin as cb                 # run directly from source
    import pcg                               # run directly from source

WORKSPACE = Path(os.environ.get("LOENN_MCP_WORKSPACE", ".")).resolve()

mcp = FastMCP(
    "loenn-mcp",
    instructions=(
        "Celeste / Lönn Map Editor MCP — read, edit, analyze, and "
        "procedurally generate Celeste .bin map files. "
        "Use build_pattern_library to extract room patterns from existing maps, "
        "generate_room_from_pattern to create new rooms with a chosen strategy "
        "(balanced/exploration/challenge/speedrun) and optional seed, "
        "validate_room to check basic playability, and "
        "ingest_external_map to import and attribute maps from external URLs "
        "such as GameBanana mod downloads."
    ),
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resolve(map_path: str) -> Path:
    """Resolve a map path relative to the workspace, with path-traversal guard."""
    p = Path(map_path)
    if not p.is_absolute():
        p = WORKSPACE / p
    p = p.resolve()
    # Use is_relative_to (Python 3.9+) for a watertight boundary check.
    # startswith("/foo/bar") would incorrectly pass "/foo/bar-evil/".
    try:
        p.relative_to(WORKSPACE)
    except ValueError:
        raise ValueError("Path must be within the workspace")
    return p


def _room_names(map_data: dict) -> str:
    return ", ".join(r.get("name", "?") for r in cb.get_rooms(map_data))


def _next_entity_id(room: dict) -> int:
    """Return the next safe entity ID, unique within this room (entities + triggers)."""
    ids: list[int] = []
    for section in ("entities", "triggers"):
        el = cb.find_child(room, section)
        if el:
            for e in el.get("__children", []):
                eid = e.get("id")
                if isinstance(eid, int):
                    ids.append(eid)
    return max(ids, default=0) + 1


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP READING TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_maps(subdir: str = "Maps") -> str:
    """List all .bin map files in the project.

    Args:
        subdir: Subdirectory to search (default: "Maps")
    """
    maps_dir = (WORKSPACE / subdir).resolve()
    try:
        maps_dir.relative_to(WORKSPACE)
    except ValueError:
        return "Invalid subdir: path must be within the workspace"

    if not maps_dir.exists():
        return f"Directory not found: {subdir}"

    bins = sorted(maps_dir.rglob("*.bin"))
    if not bins:
        return "No .bin map files found."

    lines = [f"Found {len(bins)} map files:"]
    for b in bins:
        rel = b.relative_to(WORKSPACE)
        size_kb = b.stat().st_size / 1024
        lines.append(f"  {rel}  ({size_kb:.1f} KB)")
    return "\n".join(lines)


@mcp.tool()
def read_map_overview(map_path: str) -> str:
    """Parse a .bin map file and return a summary of rooms and structure.

    Args:
        map_path: Path to the .bin file (relative to workspace or absolute)
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    lines = [
        f"Map: {data.get('_package', '?')}",
        f"Rooms: {len(rooms)}",
        "",
        "Room list:",
    ]

    for room in rooms:
        name = room.get("name", "?")
        x, y = room.get("x", 0), room.get("y", 0)
        w, h = room.get("width", 0), room.get("height", 0)

        entities_el = cb.find_child(room, "entities")
        ent_n = len(entities_el.get("__children", [])) if entities_el else 0

        triggers_el = cb.find_child(room, "triggers")
        trig_n = len(triggers_el.get("__children", [])) if triggers_el else 0

        lines.append(
            f"  {name}: pos=({x},{y}) size={w}x{h} "
            f"entities={ent_n} triggers={trig_n}"
        )

    style = cb.find_child(data, "Style")
    if style:
        fg = cb.find_child(style, "Foregrounds")
        bg = cb.find_child(style, "Backgrounds")
        fg_n = len(fg.get("__children", [])) if fg else 0
        bg_n = len(bg.get("__children", [])) if bg else 0
        lines.append(f"\nStylegrounds: {fg_n} foreground, {bg_n} background")

    return "\n".join(lines)


@mcp.tool()
def read_room(map_path: str, room_name: str) -> str:
    """Get detailed data for a specific room: tiles, entities, triggers.

    Args:
        map_path: Path to the .bin file
        room_name: Room name (with or without 'lvl_' prefix)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    lines = [
        f"Room: {room.get('name')}",
        f"Position: ({room.get('x', 0)}, {room.get('y', 0)})",
        f"Size: {room.get('width', 0)}x{room.get('height', 0)} px",
        f"Dark: {room.get('dark', False)}, Space: {room.get('space', False)}",
        f"Music: {room.get('music', '')!r}",
        f"Wind: {room.get('windPattern', 'None')}",
    ]

    # Entities
    ent_el = cb.find_child(room, "entities")
    if ent_el:
        ents = ent_el.get("__children", [])
        lines.append(f"\nEntities ({len(ents)}):")
        for e in ents:
            extra = {
                k: v
                for k, v in e.items()
                if k not in ("__name", "__children", "id", "x", "y")
            }
            extra_s = " ".join(f"{k}={v}" for k, v in extra.items())
            lines.append(
                f"  [{e.get('id', 0)}] {e.get('__name', '?')} "
                f"({e.get('x', 0)},{e.get('y', 0)}) {extra_s}".rstrip()
            )

    # Triggers
    trig_el = cb.find_child(room, "triggers")
    if trig_el:
        trigs = trig_el.get("__children", [])
        lines.append(f"\nTriggers ({len(trigs)}):")
        for t in trigs:
            lines.append(
                f"  [{t.get('id', 0)}] {t.get('__name', '?')} "
                f"({t.get('x', 0)},{t.get('y', 0)}) "
                f"{t.get('width', 0)}x{t.get('height', 0)}"
            )

    # Decals
    for layer in ("fgdecals", "bgdecals"):
        dec_el = cb.find_child(room, layer)
        if dec_el:
            decs = dec_el.get("__children", [])
            if decs:
                lines.append(f"\n{layer} ({len(decs)}):")
                for d in decs[:10]:
                    lines.append(
                        f"  {d.get('texture', '?')} "
                        f"({d.get('x', 0)},{d.get('y', 0)}) "
                        f"scale=({d.get('scaleX', 1)},{d.get('scaleY', 1)})"
                    )
                if len(decs) > 10:
                    lines.append(f"  ... and {len(decs) - 10} more")

    # Tile preview
    solids = cb.find_child(room, "solids")
    if solids:
        tiles = solids.get("innerText", "")
        tile_lines = tiles.split("\n")[:20]
        lines.append(f"\nFG tiles (first {len(tile_lines)} rows):")
        for tl in tile_lines:
            lines.append(f"  {tl}")

    return "\n".join(lines)


@mcp.tool()
def get_room_tiles(map_path: str, room_name: str, layer: str = "fg") -> str:
    """Get the complete tile grid for a room as text.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        layer: "fg" for foreground (solids), "bg" for background
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found."

    child_name = "solids" if layer == "fg" else "bg"
    tiles_el = cb.find_child(room, child_name)

    if tiles_el is None:
        return f"No {layer} tiles in this room."

    tiles = tiles_el.get("innerText", "")
    tw = room.get("width", 0) // 8
    th = room.get("height", 0) // 8
    return f"Layer: {layer} ({tw}x{th} tiles)\n{tiles}"


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP EDITING TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def add_entity(
    map_path: str,
    room_name: str,
    entity_name: str,
    x: int,
    y: int,
    entity_id: int = -1,
    width: int = 0,
    height: int = 0,
    properties: str = "{}",
) -> str:
    """Add an entity to a room and save the map.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        entity_name: Entity type (e.g. "player", "MaggyHelper/KirbyBoss")
        x: X position in pixels
        y: Y position in pixels
        entity_id: Entity ID (-1 to auto-assign)
        width: Entity width (0 if inapplicable)
        height: Entity height (0 if inapplicable)
        properties: JSON object string of extra properties
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    ent_el = cb.find_child(room, "entities")
    if ent_el is None:
        ent_el = {"__name": "entities", "__children": []}
        room["__children"].append(ent_el)

    if entity_id < 0:
        entity_id = _next_entity_id(room)

    try:
        props = json.loads(properties)
    except json.JSONDecodeError:
        return f"Invalid JSON properties: {properties}"

    entity: dict = {
        "__name": entity_name,
        "__children": [],
        "id": entity_id,
        "x": x,
        "y": y,
    }
    if width > 0:
        entity["width"] = width
    if height > 0:
        entity["height"] = height
    # Guard: never let user-supplied props overwrite structural keys
    _protected = frozenset(("__name", "__children", "id", "x", "y", "width", "height"))
    entity.update({k: v for k, v in props.items() if k not in _protected})

    ent_el["__children"].append(entity)
    cb.write_map(path, data)

    return (
        f"Added '{entity_name}' (id={entity_id}) at ({x},{y}) "
        f"to room '{room_name}'."
    )


@mcp.tool()
def remove_entity(map_path: str, room_name: str, entity_id: int) -> str:
    """Remove an entity from a room by its ID.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        entity_id: Entity ID to remove
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found."

    ent_el = cb.find_child(room, "entities")
    if ent_el is None:
        return "No entities in this room."

    children = ent_el.get("__children", [])
    before = len(children)
    ent_el["__children"] = [e for e in children if e.get("id") != entity_id]

    if len(ent_el["__children"]) == before:
        return f"Entity id={entity_id} not found."

    cb.write_map(path, data)
    return f"Removed entity id={entity_id} from '{room_name}'."


@mcp.tool()
def set_room_tiles(
    map_path: str,
    room_name: str,
    tiles: str,
    layer: str = "fg",
) -> str:
    """Set tile data for a room. Each row is a line of characters ('0'=air).

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        tiles: Tile data (newline-separated rows, 1 char per tile)
        layer: "fg" for foreground (solids), "bg" for background
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found."

    child_name = "solids" if layer == "fg" else "bg"
    tiles_el = cb.find_child(room, child_name)

    if tiles_el is None:
        tiles_el = {
            "__name": child_name,
            "__children": [],
            "offsetX": 0,
            "offsetY": 0,
        }
        room["__children"].append(tiles_el)

    tiles_el["innerText"] = tiles.strip()
    cb.write_map(path, data)

    row_count = len(tiles.strip().split("\n"))
    return f"Set {layer} tiles for '{room_name}': {row_count} rows."


@mcp.tool()
def add_room(
    map_path: str,
    room_name: str,
    x: int = 0,
    y: int = 0,
    width: int = 320,
    height: int = 184,
    dark: bool = False,
    space: bool = False,
) -> str:
    """Add a new empty room to a map.

    Args:
        map_path: Path to the .bin file
        room_name: Room name (prefixed with 'lvl_' if needed)
        x: Room X position in pixels
        y: Room Y position in pixels
        width: Room width in pixels (multiple of 8)
        height: Room height in pixels (multiple of 8)
        dark: Whether the room is dark
        space: Whether the room has space physics
    """
    path = _resolve(map_path)
    data = cb.read_map(path)

    levels = cb.find_child(data, "levels")
    if levels is None:
        return "Invalid map: no 'levels' element."

    name = room_name if room_name.startswith("lvl_") else f"lvl_{room_name}"

    for r in cb.get_rooms(data):
        if r.get("name") == name:
            return f"Room '{name}' already exists."

    tw, th = width // 8, height // 8
    air_row = "0" * tw
    fg_tiles = "\n".join([air_row] * th)
    obj_row = ",".join(["-1"] * tw)
    obj_tiles = "\n".join([obj_row] * th)

    room: dict = {
        "__name": "level",
        "name": name,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "music": "",
        "alt_music": "",
        "ambience": "",
        "dark": dark,
        "space": space,
        "underwater": False,
        "whisper": False,
        "disableDownTransition": False,
        "windPattern": "None",
        "musicLayer1": True,
        "musicLayer2": True,
        "musicLayer3": True,
        "musicLayer4": True,
        "musicProgress": "",
        "ambienceProgress": "",
        "cameraOffsetX": 0,
        "cameraOffsetY": 0,
        "delayAltMusicFade": False,
        "c": 0,
        "__children": [
            {"__name": "solids", "innerText": fg_tiles, "offsetX": 0, "offsetY": 0, "__children": []},
            {"__name": "bg", "innerText": fg_tiles, "offsetX": 0, "offsetY": 0, "__children": []},
            {"__name": "objtiles", "innerText": obj_tiles, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
            {"__name": "fgtiles", "innerText": obj_tiles, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
            {"__name": "bgtiles", "innerText": obj_tiles, "offsetX": 0, "offsetY": 0, "tileset": "scenery", "__children": []},
            {"__name": "entities", "__children": []},
            {"__name": "triggers", "__children": []},
            {"__name": "fgdecals", "__children": []},
            {"__name": "bgdecals", "__children": []},
        ],
    }

    levels["__children"].append(room)
    cb.write_map(path, data)

    return f"Added room '{name}' at ({x},{y}) {width}x{height}."


@mcp.tool()
def remove_room(map_path: str, room_name: str) -> str:
    """Remove a room from a map.

    Args:
        map_path: Path to the .bin file
        room_name: Room name to remove
    """
    path = _resolve(map_path)
    data = cb.read_map(path)

    levels = cb.find_child(data, "levels")
    if levels is None:
        return "Invalid map."

    name = room_name if room_name.startswith("lvl_") else f"lvl_{room_name}"
    children = levels.get("__children", [])
    before = len(children)
    levels["__children"] = [
        r for r in children
        if r.get("name") != name and r.get("name") != room_name
    ]

    if len(levels["__children"]) == before:
        return f"Room '{room_name}' not found."

    cb.write_map(path, data)
    return f"Removed room '{room_name}'."


@mcp.tool()
def create_map(map_path: str, package_name: str = "") -> str:
    """Create a new empty Celeste .bin map file.

    Args:
        map_path: Path for the new file (relative to workspace)
        package_name: Map package name (e.g. "Maggy/PCG/MyMap")
    """
    path = _resolve(map_path)
    if path.exists():
        return f"File already exists: {map_path}"

    if not package_name:
        package_name = path.stem

    data: dict = {
        "__name": "Map",
        "_package": package_name,
        "__children": [
            {"__name": "Filler", "__children": []},
            {"__name": "levels", "__children": []},
            {
                "__name": "Style",
                "__children": [
                    {"__name": "Foregrounds", "__children": []},
                    {"__name": "Backgrounds", "__children": []},
                ],
            },
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    cb.write_map(path, data)
    return f"Created empty map: {map_path} (package: {package_name})"


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTITY / TRIGGER CATALOG TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def list_entity_definitions(filter_text: str = "") -> str:
    """List available Lönn entity definitions from the project.

    Args:
        filter_text: Optional text to filter filenames
    """
    ent_dir = WORKSPACE / "Loenn" / "entities"
    if not ent_dir.exists():
        return "No Loenn/entities/ directory found."

    files = sorted(ent_dir.glob("*.lua"))
    if filter_text:
        fl = filter_text.lower()
        files = [f for f in files if fl in f.stem.lower()]

    if not files:
        return "No matching entity definitions."

    lines = [f"Entity definitions ({len(files)} files):"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\.name\s*=\s*[\"']([^\"']+)", text)
            label = m.group(1) if m else f.stem
        except Exception:
            label = f.stem
        lines.append(f"  {f.stem}  →  {label}")
    return "\n".join(lines)


@mcp.tool()
def get_entity_definition(entity_file: str) -> str:
    """Read a Lönn entity Lua definition and extract its properties.

    Args:
        entity_file: Filename (e.g. "kirbyBoss" or "kirbyBoss.lua")
    """
    if not entity_file.endswith(".lua"):
        entity_file += ".lua"

    ent_dir = (WORKSPACE / "Loenn" / "entities").resolve()
    path = (ent_dir / entity_file).resolve()
    # Guard against path traversal (e.g. entity_file = "../../secrets")
    try:
        path.relative_to(ent_dir)
    except ValueError:
        return "Invalid entity file path."
    if not path.exists():
        return f"Entity file not found: {entity_file}"

    text = path.read_text(encoding="utf-8", errors="replace")

    names = re.findall(r"\.name\s*=\s*[\"']([^\"']+)", text)
    placements = re.findall(
        r"placements.*?name\s*=\s*[\"']([^\"']+)", text, re.DOTALL
    )
    data_blocks = re.findall(r"data\s*=\s*\{([^}]+)\}", text)

    lines = [f"=== {entity_file} ==="]
    if names:
        lines.append(f"Names: {', '.join(names)}")
    if placements:
        lines.append(f"Placements: {', '.join(placements)}")
    if data_blocks:
        lines.append("\nDefault properties:")
        for block in data_blocks[:3]:
            for prop in re.findall(r"(\w+)\s*=\s*([^,\n]+)", block):
                lines.append(f"  {prop[0]} = {prop[1].strip()}")

    lines.append(f"\n--- Source ({len(text)} chars) ---")
    if len(text) > 4000:
        lines.append(text[:4000] + "\n... (truncated)")
    else:
        lines.append(text)

    return "\n".join(lines)


@mcp.tool()
def list_trigger_definitions(filter_text: str = "") -> str:
    """List available Lönn trigger definitions.

    Args:
        filter_text: Optional text to filter filenames
    """
    trig_dir = WORKSPACE / "Loenn" / "triggers"
    if not trig_dir.exists():
        return "No Loenn/triggers/ directory found."

    files = sorted(trig_dir.glob("*.lua"))
    if filter_text:
        fl = filter_text.lower()
        files = [f for f in files if fl in f.stem.lower()]

    lines = [f"Trigger definitions ({len(files)} files):"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\.name\s*=\s*[\"']([^\"']+)", text)
            label = m.group(1) if m else f.stem
        except Exception:
            label = f.stem
        lines.append(f"  {f.stem}  →  {label}")
    return "\n".join(lines)


@mcp.tool()
def list_effect_definitions(filter_text: str = "") -> str:
    """List available Lönn effect definitions.

    Args:
        filter_text: Optional text to filter filenames
    """
    fx_dir = WORKSPACE / "Loenn" / "effects"
    if not fx_dir.exists():
        return "No Loenn/effects/ directory found."

    files = sorted(fx_dir.glob("*.lua"))
    if filter_text:
        fl = filter_text.lower()
        files = [f for f in files if fl in f.stem.lower()]

    lines = [f"Effect definitions ({len(files)} files):"]
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"\.name\s*=\s*[\"']([^\"']+)", text)
            label = m.group(1) if m else f.stem
        except Exception:
            label = f.stem
        lines.append(f"  {f.stem}  →  {label}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSIS TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def analyze_map(map_path: str) -> str:
    """Get detailed statistics about a map file.

    Args:
        map_path: Path to the .bin file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    total_ent = 0
    total_trig = 0
    total_fg_dec = 0
    total_bg_dec = 0
    entity_types: dict[str, int] = {}

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")

    for room in rooms:
        x, y = room.get("x", 0), room.get("y", 0)
        w, h = room.get("width", 0), room.get("height", 0)
        min_x, min_y = min(min_x, x), min(min_y, y)
        max_x, max_y = max(max_x, x + w), max(max_y, y + h)

        for section, label in [
            ("entities", "ent"),
            ("triggers", "trig"),
            ("fgdecals", "fgdec"),
            ("bgdecals", "bgdec"),
        ]:
            el = cb.find_child(room, section)
            if not el:
                continue
            children = el.get("__children", [])
            n = len(children)
            if label == "ent":
                total_ent += n
                for c in children:
                    t = c.get("__name", "unknown")
                    entity_types[t] = entity_types.get(t, 0) + 1
            elif label == "trig":
                total_trig += n
            elif label == "fgdec":
                total_fg_dec += n
            elif label == "bgdec":
                total_bg_dec += n

    lines = [
        f"Map: {data.get('_package', '?')}",
        f"File: {path.name} ({path.stat().st_size / 1024:.1f} KB)",
        "",
        f"Rooms: {len(rooms)}",
        f"Entities: {total_ent}",
        f"Triggers: {total_trig}",
        f"Decals: {total_fg_dec} fg + {total_bg_dec} bg",
    ]

    if rooms:
        lines += [
            "",
            f"Bounds: ({min_x},{min_y}) → ({max_x},{max_y})",
            f"Span: {max_x - min_x}x{max_y - min_y} px",
        ]

    if entity_types:
        top = sorted(entity_types.items(), key=lambda x: -x[1])
        lines.append(f"\nEntity types ({len(entity_types)} unique):")
        for name, count in top[:25]:
            lines.append(f"  {name}: {count}")
        if len(top) > 25:
            lines.append(f"  ... +{len(top) - 25} more")

    return "\n".join(lines)


@mcp.tool()
def visualize_map_layout(map_path: str) -> str:
    """Generate an ASCII visualization of all room positions in a map.

    Args:
        map_path: Path to the .bin file
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    if not rooms:
        return "No rooms in this map."

    # Determine scale
    min_x = min(r.get("x", 0) for r in rooms)
    min_y = min(r.get("y", 0) for r in rooms)
    max_x = max(r.get("x", 0) + r.get("width", 0) for r in rooms)
    max_y = max(r.get("y", 0) + r.get("height", 0) for r in rooms)

    span_x = max_x - min_x or 1
    span_y = max_y - min_y or 1
    scale = max(span_x // 100, span_y // 50, 32)

    grid_w = span_x // scale + 2
    grid_h = span_y // scale + 2
    grid = [[" "] * grid_w for _ in range(grid_h)]

    for i, room in enumerate(rooms):
        rx = (room.get("x", 0) - min_x) // scale
        ry = (room.get("y", 0) - min_y) // scale
        rw = max(1, room.get("width", 0) // scale)
        rh = max(1, room.get("height", 0) // scale)
        ch = chr(ord("A") + i % 26) if i < 26 else str(i % 10)

        for dy in range(rh):
            for dx in range(rw):
                gx, gy = rx + dx, ry + dy
                if 0 <= gx < grid_w and 0 <= gy < grid_h:
                    if dy == 0 or dy == rh - 1 or dx == 0 or dx == rw - 1:
                        grid[gy][gx] = ch
                    else:
                        grid[gy][gx] = "·"

    lines = ["".join(row).rstrip() for row in grid]
    while lines and not lines[-1].strip():
        lines.pop()

    legend = ["\nLegend:"]
    for i, room in enumerate(rooms):
        ch = chr(ord("A") + i % 26) if i < 26 else str(i % 10)
        legend.append(f"  {ch} = {room.get('name', '?')}")

    return "\n".join(lines + legend)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP PREVIEW TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def preview_map_section(
    map_path: str,
    prefix: str = "",
    center_room: str = "",
    cols: int = 140,
    rows: int = 60,
) -> str:
    """Generate an ASCII preview of a map section, like a Lönn mini-map.

    Shows room boxes with names, borders drawn with box-drawing characters,
    checkpoint markers, and entity counts. Zoom into a specific area by
    providing a room prefix (e.g. "g-") or a center room name.

    Args:
        map_path: Path to the .bin file
        prefix: Only show rooms whose name starts with this (e.g. "g-", "h-")
        center_room: Center the view on this room name
        cols: Terminal width for the preview (default 140)
        rows: Terminal height for the preview (default 60)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    all_rooms = cb.get_rooms(data)

    if not all_rooms:
        return "No rooms in this map."

    # Filter rooms
    if prefix:
        pfx = prefix if prefix.startswith("lvl_") else f"lvl_{prefix}"
        rooms = [r for r in all_rooms if r.get("name", "").startswith(pfx)]
        if not rooms:
            return f"No rooms matching prefix '{prefix}'."
    else:
        rooms = all_rooms

    # Determine viewport bounds
    if center_room:
        cn = center_room if center_room.startswith("lvl_") else f"lvl_{center_room}"
        cr = next((r for r in all_rooms if r.get("name") == cn), None)
        if cr:
            cx = cr["x"] + cr["width"] // 2
            cy = cr["y"] + cr["height"] // 2
        else:
            cx = sum(r["x"] + r["width"] // 2 for r in rooms) // len(rooms)
            cy = sum(r["y"] + r["height"] // 2 for r in rooms) // len(rooms)
    else:
        min_x = min(r.get("x", 0) for r in rooms)
        min_y = min(r.get("y", 0) for r in rooms)
        max_x = max(r.get("x", 0) + r.get("width", 0) for r in rooms)
        max_y = max(r.get("y", 0) + r.get("height", 0) for r in rooms)
        cx = (min_x + max_x) // 2
        cy = (min_y + max_y) // 2

    # Calculate scale so filtered rooms fit in the grid
    min_x = min(r.get("x", 0) for r in rooms)
    min_y = min(r.get("y", 0) for r in rooms)
    max_x = max(r.get("x", 0) + r.get("width", 0) for r in rooms)
    max_y = max(r.get("y", 0) + r.get("height", 0) for r in rooms)
    span_x = max_x - min_x or 1
    span_y = max_y - min_y or 1

    # Pick scale to fit in grid, leave margin
    usable_cols = cols - 2
    usable_rows = rows - 2
    scale_x = max(span_x // usable_cols, 1)
    scale_y = max(span_y // usable_rows, 1)
    scale = max(scale_x, scale_y * 2)  # chars are ~2x taller than wide

    grid_w = span_x // scale + 4
    grid_h = span_y // (scale * 2) + 4  # double because chars are tall
    grid_w = min(grid_w, cols)
    grid_h = min(grid_h, rows)

    # Initialize grid
    grid = [[" "] * grid_w for _ in range(grid_h)]

    # Draw rooms
    room_info = []
    for room in rooms:
        rx = room.get("x", 0)
        ry = room.get("y", 0)
        rw = room.get("width", 0)
        rh = room.get("height", 0)
        name = room.get("name", "?").replace("lvl_", "")

        # Grid coordinates
        gx = (rx - min_x) // scale
        gy = (ry - min_y) // (scale * 2)
        gw = max(2, rw // scale)
        gh = max(2, rh // (scale * 2))

        # Count entities
        ent_count = 0
        has_checkpoint = False
        has_player = False
        for child in room.get("__children", []):
            if child.get("__name") == "entities":
                for ent in child.get("__children", []):
                    ent_count += 1
                    if ent.get("__name") == "checkpoint":
                        has_checkpoint = True
                    if ent.get("__name") == "player":
                        has_player = True

        # Draw box
        for dy in range(gh):
            for dx in range(gw):
                x, y = gx + dx, gy + dy
                if 0 <= x < grid_w and 0 <= y < grid_h:
                    if dy == 0 and dx == 0:
                        grid[y][x] = "┌"
                    elif dy == 0 and dx == gw - 1:
                        grid[y][x] = "┐"
                    elif dy == gh - 1 and dx == 0:
                        grid[y][x] = "└"
                    elif dy == gh - 1 and dx == gw - 1:
                        grid[y][x] = "┘"
                    elif dy == 0 or dy == gh - 1:
                        grid[y][x] = "─"
                    elif dx == 0 or dx == gw - 1:
                        grid[y][x] = "│"
                    else:
                        grid[y][x] = "·"

        # Write room name inside the box
        label = name
        if has_checkpoint:
            label = "★" + name
        lx = gx + 1
        ly = gy + 1 if gh > 2 else gy
        for ci, ch in enumerate(label[:gw - 2]):
            x = lx + ci
            if 0 <= x < grid_w and 0 <= ly < grid_h:
                grid[ly][x] = ch

        # Write entity count on line below name if space
        if gh > 3:
            count_str = f"{ent_count}e"
            ly2 = gy + 2
            for ci, ch in enumerate(count_str[:gw - 2]):
                x = lx + ci
                if 0 <= x < grid_w and 0 <= ly2 < grid_h:
                    grid[ly2][x] = ch

        room_info.append((name, rx, ry, rw, rh, ent_count, has_checkpoint))

    # Render grid
    output_lines = ["".join(row).rstrip() for row in grid]
    while output_lines and not output_lines[-1].strip():
        output_lines.pop()

    # Add summary below
    output_lines.append("")
    output_lines.append(f"Rooms shown: {len(rooms)}  |  Scale: 1 char ≈ {scale}px wide, {scale*2}px tall")
    output_lines.append("")

    # Room table
    output_lines.append(f"{'Room':<14} {'Pos':>16} {'Size':>10} {'Ents':>5} {'CP':>3}")
    output_lines.append("─" * 52)
    for name, rx, ry, rw, rh, ec, cp in room_info:
        cp_mark = "★" if cp else ""
        output_lines.append(f"{name:<14} ({rx:>6},{ry:>6}) {rw:>4}x{rh:<4} {ec:>5} {cp_mark:>3}")

    return "\n".join(output_lines)


@mcp.tool()
def render_map_html(
    map_path: str,
    prefix: str = "",
    output_file: str = "",
) -> str:
    """Render an interactive HTML map preview with zoom, pan, search, and detail panel.

    Features: zoom/pan (wheel, drag, pinch), hover tooltips, click-to-select room
    detail panel (entity/trigger breakdown), live search filter, minimap with
    viewport indicator, toggleable grid and labels, keyboard shortcuts.

    Args:
        map_path: Path to the .bin file
        prefix: Only show rooms matching this prefix (e.g. "g-", "w-")
        output_file: Output HTML filename (default: map_preview.html in Temp/)
    """
    import json as _json

    path = _resolve(map_path)
    data = cb.read_map(path)
    all_rooms = cb.get_rooms(data)

    if not all_rooms:
        return "No rooms in this map."

    if prefix:
        pfx = prefix if prefix.startswith("lvl_") else f"lvl_{prefix}"
        rooms = [r for r in all_rooms if r.get("name", "").startswith(pfx)]
        if not rooms:
            return f"No rooms matching prefix '{prefix}'."
    else:
        rooms = all_rooms

    # World bounds
    min_x = min(r.get("x", 0) for r in rooms)
    min_y = min(r.get("y", 0) for r in rooms)
    max_x = max(r.get("x", 0) + r.get("width", 0) for r in rooms)
    max_y = max(r.get("y", 0) + r.get("height", 0) for r in rooms)
    margin = 320
    vw = max_x - min_x + margin * 2
    vh = max_y - min_y + margin * 2

    PALETTE = [
        "#4a80c8", "#5aa050", "#c86040", "#8040c0",
        "#40a8b0", "#c09030", "#b83880", "#3890c0",
        "#70b030", "#c04040",
    ]

    rooms_data = []
    svg_groups = []

    for i, room in enumerate(rooms):
        rx = room.get("x", 0) - min_x + margin
        ry = room.get("y", 0) - min_y + margin
        rw = room.get("width", 0)
        rh = room.get("height", 0)
        name = room.get("name", "?").replace("lvl_", "")

        area = name.split("-")[0] if "-" in name else name
        color = PALETTE[abs(hash(area)) % len(PALETTE)]

        has_cp = False
        has_strawberry = False
        has_heart = False
        has_cassette = False
        entity_types: dict = {}
        trigger_types: dict = {}

        for child in room.get("__children", []):
            cname = child.get("__name")
            if cname == "entities":
                for ent in child.get("__children", []):
                    etype = ent.get("__name", "?")
                    entity_types[etype] = entity_types.get(etype, 0) + 1
                    if etype == "checkpoint": has_cp = True
                    if etype in ("strawberry", "goldenBerry", "memorialTextController"):
                        has_strawberry = True
                    if etype in ("blackGem", "heartGem", "darkChest"):
                        has_heart = True
                    if etype == "cassette":
                        has_cassette = True
            elif cname == "triggers":
                for t in child.get("__children", []):
                    ttype = t.get("__name", "?")
                    trigger_types[ttype] = trigger_types.get(ttype, 0) + 1

        ent_count = sum(entity_types.values())
        trigger_count = sum(trigger_types.values())
        top_ents = sorted(entity_types.items(), key=lambda x: -x[1])[:14]
        top_trigs = sorted(trigger_types.items(), key=lambda x: -x[1])[:8]

        badges = []
        if has_cp: badges.append("★")
        if has_strawberry: badges.append("🍓")
        if has_heart: badges.append("💎")
        if has_cassette: badges.append("📼")

        rooms_data.append({
            "idx": i, "name": name,
            "wx": room.get("x", 0), "wy": room.get("y", 0),
            "w": rw, "h": rh,
            "sx": rx, "sy": ry,
            "color": color,
            "has_cp": has_cp,
            "badges": badges,
            "ent": ent_count, "trig": trigger_count,
            "entities": top_ents,
            "triggers": top_trigs,
        })

        fs = max(10, min(20, rw // max(len(name), 1)))
        cx = rx + rw / 2
        cy = ry + rh / 2
        stroke = "#ffd700" if has_cp else "#5a5a7a"
        stroke_w = 3 if has_cp else 1.5
        badge_str = " ".join(badges)
        disp_label = (badge_str + " " if badge_str else "") + name
        sub_label = f"{ent_count}e · {trigger_count}t · {rw}×{rh}"

        g_parts = [
            f'<rect class="rb" x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
            f'fill="{color}" fill-opacity="0.72" stroke="{stroke}" stroke-width="{stroke_w}" rx="4"/>',
            f'<rect class="rh" x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
            f'fill="none" stroke="transparent" rx="4" pointer-events="none"/>',
            f'<text class="ln" x="{cx:.1f}" y="{cy + fs*0.35:.1f}" '
            f'text-anchor="middle" font-size="{fs}" fill="white" '
            f'font-family="monospace" font-weight="bold" filter="url(#shadow)">{disp_label}</text>',
            f'<text class="ls" x="{cx:.1f}" y="{cy + fs + max(8, fs-4):.1f}" '
            f'text-anchor="middle" font-size="{max(8, fs-4)}" fill="#aab" '
            f'font-family="monospace" filter="url(#shadow)">{sub_label}</text>',
            f'<rect class="ri" x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
            f'fill="transparent" data-idx="{i}"/>',
        ]
        svg_groups.append(f'<g class="rg" id="rg{i}">' + "".join(g_parts) + "</g>")

    svg_body = "\n".join(svg_groups)
    rooms_json = _json.dumps(rooms_data)

    # Grid lines every 320px (one standard Celeste room width)
    grid_parts = []
    for y in range(0, vh + 320, 320):
        grid_parts.append(f'<line x1="0" y1="{y}" x2="{vw}" y2="{y}"/>')
    for x in range(0, vw + 320, 320):
        grid_parts.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{vh}"/>')
    grid_svg = "\n    ".join(grid_parts)

    # ── CSS (plain string — no f-string brace conflicts) ──────────────────────
    css = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #12121e; color: #d8d8e8;
  font-family: Consolas, 'Courier New', monospace;
  display: flex; flex-direction: column; height: 100vh; overflow: hidden; }
#topbar { display: flex; align-items: center; gap: 10px; padding: 7px 14px;
  background: #1c1c30; border-bottom: 1px solid #2e2e48; flex-shrink: 0; }
#title { font-size: 14px; font-weight: bold; color: #9ab0ff; flex: 1;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#stats { font-size: 11px; color: #556; white-space: nowrap; }
#toolbar { display: flex; align-items: center; gap: 5px; padding: 5px 12px;
  background: #161626; border-bottom: 1px solid #252538;
  flex-shrink: 0; flex-wrap: wrap; }
.btn { background: #222238; color: #bbc; border: 1px solid #3a3a56; padding: 3px 9px;
  border-radius: 4px; cursor: pointer; font-size: 12px; font-family: inherit;
  white-space: nowrap; line-height: 20px; }
.btn:hover { background: #2e2e50; }
.btn.on { background: #2c2c56; border-color: #5060a0; color: #cce; }
#zdsp { font-size: 12px; color: #667; min-width: 46px; text-align: center; }
#search { background: #222238; color: #ccd; border: 1px solid #3a3a56;
  padding: 3px 8px; border-radius: 4px; font-size: 12px;
  font-family: inherit; width: 165px; }
#search:focus { outline: none; border-color: #6878c8; }
#search::placeholder { color: #445; }
.sep { width: 1px; height: 18px; background: #252538; margin: 0 2px; }
#main { display: flex; flex: 1; overflow: hidden; }
#wrap { flex: 1; overflow: hidden; cursor: grab; position: relative; background: #12121e; }
#wrap.dragging { cursor: grabbing; }
svg { display: block; }
/* label zoom tiers */
svg.z0 .ln, svg.z0 .ls { display: none; }
svg.z1 .ls { display: none; }
/* room appearance */
.rh { transition: stroke 0.07s; }
.rg:hover .rh { stroke: rgba(255,255,255,0.9) !important; stroke-width: 3 !important; }
.rg.sel .rh { stroke: white !important; stroke-width: 4 !important; }
.rg.dim { opacity: 0.1; }
.ri { cursor: pointer; }
/* sidebar */
#sb { width: 252px; flex-shrink: 0; background: #181828;
  border-left: 1px solid #252538; display: flex; flex-direction: column; }
#sb.closed { display: none; }
#sb-hdr { padding: 10px 12px 8px; background: #1e1e34; border-bottom: 1px solid #252538; }
#sb-idx { font-size: 10px; color: #446; text-transform: uppercase; letter-spacing: 1px; }
#sb-name { font-size: 18px; color: #e0e8ff; font-weight: bold; margin: 2px 0; }
#sb-meta { font-size: 11px; color: #667; }
#sb-badges { font-size: 16px; margin-top: 5px; letter-spacing: 3px; min-height: 22px; }
#sb-body { flex: 1; overflow-y: auto; padding: 8px 12px; font-size: 12px; }
#sb-body::-webkit-scrollbar { width: 5px; }
#sb-body::-webkit-scrollbar-track { background: #181828; }
#sb-body::-webkit-scrollbar-thumb { background: #333350; border-radius: 3px; }
.sec { font-size: 10px; color: #4858a0; text-transform: uppercase; letter-spacing: 1.5px;
  border-bottom: 1px solid #222238; padding-bottom: 3px; margin: 10px 0 5px; }
.none { color: #334; font-size: 11px; padding: 2px 0; }
.erow { display: flex; justify-content: space-between; padding: 2px 0; }
.en { color: #9aaabb; flex: 1; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; font-size: 11px; }
.ec { color: #5868b0; font-size: 11px; min-width: 28px; text-align: right; }
#sb-copy { padding: 6px; margin: 6px 10px 10px; background: #1e1e34;
  border: 1px solid #303050; border-radius: 5px; cursor: pointer;
  font-size: 11px; color: #8898b8; text-align: center; font-family: inherit; width: calc(100% - 20px); }
#sb-copy:hover { background: #252545; color: #aab8d0; }
#sb-empty { flex: 1; display: flex; align-items: center; justify-content: center;
  color: #334; font-size: 12px; text-align: center; line-height: 2; }
/* tooltip */
#tip { position: fixed; background: #1c1c30; border: 1px solid #3a3a58;
  border-radius: 6px; padding: 8px 10px; font-size: 11px;
  pointer-events: none; z-index: 200; opacity: 0; transition: opacity 0.07s;
  box-shadow: 0 4px 18px rgba(0,0,0,0.65); max-width: 260px; white-space: pre-line; }
#tip.vis { opacity: 1; }
#tip-name { font-size: 13px; font-weight: bold; color: #b0c0ff; margin-bottom: 3px; }
#tip-size { color: #667; margin-bottom: 3px; }
#tip-ents { color: #8899aa; }
/* minimap */
#mm-wrap { position: absolute; bottom: 12px; right: 12px; border: 1px solid #2a2a44;
  border-radius: 5px; overflow: hidden; cursor: pointer;
  box-shadow: 0 2px 12px rgba(0,0,0,0.6); }
#mmv { position: absolute; top: 0; left: 0; pointer-events: none; }
"""

    # ── JS (plain string so literal {} needs no escaping) ─────────────────────
    js = r"""
(function() {
const wrap = document.getElementById('wrap');
const svg  = document.getElementById('svg');
const zdsp = document.getElementById('zdsp');
const tip  = document.getElementById('tip');
const sb   = document.getElementById('sb');
const ZOOM_STEP = 1.25, MIN_Z = 0.04, MAX_Z = 12;
let zoom = 1, panX = 0, panY = 0;
let dragging = false, dragMoved = false, lx = 0, ly = 0;
let selIdx = -1;

// ── zoom / pan ────────────────────────────────────────────────────────────
function fitToWindow() {
  const ww = wrap.clientWidth, wh = wrap.clientHeight;
  zoom = Math.min(ww / SVG_W, wh / SVG_H) * 0.93;
  panX = (ww - SVG_W * zoom) / 2;
  panY = (wh - SVG_H * zoom) / 2;
}
function clamp() {
  const ww = wrap.clientWidth, wh = wrap.clientHeight, pad = 120;
  panX = Math.min(panX, pad);
  panY = Math.min(panY, pad);
  panX = Math.max(panX, ww - SVG_W * zoom - pad);
  panY = Math.max(panY, wh - SVG_H * zoom - pad);
}
function apply() {
  svg.style.transform = `translate(${panX}px,${panY}px) scale(${zoom})`;
  svg.style.transformOrigin = '0 0';
  zdsp.textContent = Math.round(zoom * 100) + '%';
  svg.className = zoom < 0.15 ? 'z0' : zoom < 0.4 ? 'z1' : 'z2';
  drawMMViewport();
}
function zoomAt(cx, cy, f) {
  const nz = Math.min(MAX_Z, Math.max(MIN_Z, zoom * f));
  const s = nz / zoom;
  panX = cx - s * (cx - panX);
  panY = cy - s * (cy - panY);
  zoom = nz; clamp(); apply();
}
fitToWindow(); apply();

// ── mouse wheel ───────────────────────────────────────────────────────────
wrap.addEventListener('wheel', e => {
  e.preventDefault();
  const r = wrap.getBoundingClientRect();
  zoomAt(e.clientX - r.left, e.clientY - r.top,
         e.deltaY < 0 ? ZOOM_STEP : 1/ZOOM_STEP);
}, {passive: false});

// ── drag to pan ───────────────────────────────────────────────────────────
// dragMoved is only true once the pointer has moved > 8px, preventing
// accidental click suppression from normal hand tremor.
wrap.addEventListener('mousedown', e => {
  if (e.button !== 0) return;
  dragging = true; dragMoved = false; lx = e.clientX; ly = e.clientY;
  wrap.classList.add('dragging');
});
window.addEventListener('mousemove', e => {
  if (!dragging) return;
  const dx = e.clientX - lx, dy = e.clientY - ly;
  if (!dragMoved && Math.abs(dx) < 8 && Math.abs(dy) < 8) return; // ignore tremor
  dragMoved = true;
  panX += dx; panY += dy; lx = e.clientX; ly = e.clientY;
  clamp(); apply();
});
window.addEventListener('mouseup', () => {
  dragging = false; wrap.classList.remove('dragging');
});

// ── touch pinch/pan ───────────────────────────────────────────────────────
let touches = {}, lastDist = null;
wrap.addEventListener('touchstart', e => {
  for (const t of e.changedTouches) touches[t.identifier] = {x: t.clientX, y: t.clientY};
}, {passive: true});
wrap.addEventListener('touchmove', e => {
  e.preventDefault();
  if (e.touches.length === 1) {
    const t = e.touches[0], p = touches[t.identifier] || {x: t.clientX, y: t.clientY};
    panX += t.clientX - p.x; panY += t.clientY - p.y;
    touches[t.identifier] = {x: t.clientX, y: t.clientY};
    clamp(); apply();
  } else if (e.touches.length === 2) {
    const [a, b] = e.touches;
    const d = Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY);
    if (lastDist !== null) {
      const r = wrap.getBoundingClientRect();
      zoomAt((a.clientX + b.clientX)/2 - r.left,
             (a.clientY + b.clientY)/2 - r.top, d/lastDist);
    }
    lastDist = d;
    for (const t of e.touches) touches[t.identifier] = {x: t.clientX, y: t.clientY};
  }
}, {passive: false});
wrap.addEventListener('touchend', e => {
  for (const t of e.changedTouches) delete touches[t.identifier];
  if (e.touches.length < 2) lastDist = null;
}, {passive: true});

// ── keyboard shortcuts ────────────────────────────────────────────────────
window.addEventListener('keydown', e => {
  if (e.target.tagName === 'INPUT') return;
  if (e.key === '=' || e.key === '+') zoomAt(wrap.clientWidth/2, wrap.clientHeight/2, ZOOM_STEP);
  else if (e.key === '-')             zoomAt(wrap.clientWidth/2, wrap.clientHeight/2, 1/ZOOM_STEP);
  else if (e.key === '0') { fitToWindow(); apply(); }
  else if (e.key === 'Escape') { selectRoom(-1); }
  else if (e.key === 'f' || e.key === 'F') { document.getElementById('search').focus(); }
});

// ── tooltip ───────────────────────────────────────────────────────────────
const tipName = document.getElementById('tip-name');
const tipSize = document.getElementById('tip-size');
const tipEnts = document.getElementById('tip-ents');

wrap.addEventListener('mousemove', e => {
  let tx = e.clientX + 16, ty = e.clientY - 8;
  if (tx + 270 > window.innerWidth) tx = e.clientX - 260;
  tip.style.left = tx + 'px';
  tip.style.top  = ty + 'px';
});

const roomsEl = document.getElementById('rooms');
roomsEl.addEventListener('mouseover', e => {
  const idx = e.target.dataset?.idx;
  if (idx === undefined) { tip.classList.remove('vis'); return; }
  const r = ROOMS[+idx];
  tipName.textContent = ([...r.badges, r.name]).join(' ');
  tipSize.textContent = r.w + ' × ' + r.h + ' px  (world ' + r.wx + ', ' + r.wy + ')';
  const top3 = r.entities.slice(0, 3).map(([n, c]) => n + ': ' + c).join('  ·  ');
  tipEnts.textContent = r.ent + ' entities · ' + r.trig + ' triggers' +
                        (top3 ? '\n' + top3 : '');
  tip.classList.add('vis');
});
roomsEl.addEventListener('mouseout', e => {
  if (e.target.dataset?.idx !== undefined) tip.classList.remove('vis');
});

// ── room click / select ───────────────────────────────────────────────────
roomsEl.addEventListener('click', e => {
  if (dragMoved) return;
  const idx = e.target.dataset?.idx;
  if (idx === undefined) return;
  selectRoom(+idx === selIdx ? -1 : +idx);
});

function getRG(i) { return document.getElementById('rg' + i); }

function selectRoom(idx) {
  if (selIdx >= 0) getRG(selIdx)?.classList.remove('sel');
  selIdx = idx;
  if (idx >= 0) {
    getRG(idx)?.classList.add('sel');
    showSidebar(ROOMS[idx]);
  } else {
    hideSidebar();
  }
}

// ── sidebar ───────────────────────────────────────────────────────────────
function showSidebar(r) {
  document.getElementById('sb-empty').style.display = 'none';
  document.getElementById('sb-hdr').style.display   = '';
  document.getElementById('sb-body').style.display  = '';
  document.getElementById('sb-copy').style.display  = '';
  document.getElementById('sb-idx').textContent  = 'Room ' + (r.idx + 1);
  document.getElementById('sb-name').textContent = r.name;
  document.getElementById('sb-meta').textContent =
      r.w + ' × ' + r.h + ' px  ·  (' + r.wx + ', ' + r.wy + ')';
  document.getElementById('sb-badges').textContent = r.badges.join('  ');

  let html = '<div class="sec">Entities (' + r.ent + ')</div>';
  if (!r.entities.length) html += '<div class="none">—</div>';
  for (const [n, c] of r.entities)
    html += '<div class="erow"><span class="en" title="' + n + '">' + n +
            '</span><span class="ec">' + c + '</span></div>';

  html += '<div class="sec">Triggers (' + r.trig + ')</div>';
  if (!r.triggers.length) html += '<div class="none">—</div>';
  for (const [n, c] of r.triggers)
    html += '<div class="erow"><span class="en" title="' + n + '">' + n +
            '</span><span class="ec">' + c + '</span></div>';

  document.getElementById('sb-body').innerHTML = html;
}

function hideSidebar() {
  document.getElementById('sb-empty').style.display  = '';
  document.getElementById('sb-hdr').style.display    = 'none';
  document.getElementById('sb-body').style.display   = 'none';
  document.getElementById('sb-copy').style.display   = 'none';
}

document.getElementById('sb-copy').addEventListener('click', () => {
  if (selIdx < 0) return;
  navigator.clipboard?.writeText(ROOMS[selIdx].name);
  const btn = document.getElementById('sb-copy');
  btn.textContent = '✓ Copied!';
  setTimeout(() => { btn.textContent = '📋 Copy name'; }, 1500);
});

// ── search / filter ───────────────────────────────────────────────────────
document.getElementById('search').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  ROOMS.forEach(r => {
    const g = getRG(r.idx);
    if (g) g.classList.toggle('dim', !!q && !r.name.toLowerCase().includes(q));
  });
});

// ── toolbar buttons ───────────────────────────────────────────────────────
document.getElementById('btn-zi').addEventListener('click', () =>
  zoomAt(wrap.clientWidth/2, wrap.clientHeight/2, ZOOM_STEP));
document.getElementById('btn-zo').addEventListener('click', () =>
  zoomAt(wrap.clientWidth/2, wrap.clientHeight/2, 1/ZOOM_STEP));
document.getElementById('btn-fit').addEventListener('click', () => { fitToWindow(); apply(); });

document.getElementById('btn-grid').addEventListener('click', function() {
  this.classList.toggle('on');
  document.getElementById('grid').style.display = this.classList.contains('on') ? '' : 'none';
});
document.getElementById('btn-lbl').addEventListener('click', function() {
  this.classList.toggle('on');
  const v = this.classList.contains('on') ? '' : 'none';
  document.querySelectorAll('.ln,.ls').forEach(el => el.style.display = v);
});

let sbOpen = true;
document.getElementById('btn-sb').addEventListener('click', function() {
  sbOpen = !sbOpen;
  this.classList.toggle('on', sbOpen);
  sb.classList.toggle('closed', !sbOpen);
});

// ── minimap ───────────────────────────────────────────────────────────────
const mmC   = document.getElementById('mm');
const mmCtx = mmC.getContext('2d');
const mmV   = document.getElementById('mmv');
const mmVCtx = mmV.getContext('2d');
const MM_W = mmC.width, MM_H = mmC.height;
const mmSX = MM_W / SVG_W, mmSY = MM_H / SVG_H;

// Draw static room tiles once
mmCtx.fillStyle = '#0e0e1a';
mmCtx.fillRect(0, 0, MM_W, MM_H);
for (const r of ROOMS) {
  mmCtx.fillStyle = r.color + 'bb';
  mmCtx.fillRect(r.sx * mmSX, r.sy * mmSY, Math.max(1, r.w * mmSX), Math.max(1, r.h * mmSY));
  if (r.has_cp) {
    mmCtx.strokeStyle = '#ffd700';
    mmCtx.lineWidth = 0.5;
    mmCtx.strokeRect(r.sx * mmSX, r.sy * mmSY, Math.max(1, r.w * mmSX), Math.max(1, r.h * mmSY));
  }
}

function drawMMViewport() {
  mmVCtx.clearRect(0, 0, MM_W, MM_H);
  const ww = wrap.clientWidth, wh = wrap.clientHeight;
  const vx = (-panX / zoom) * mmSX;
  const vy = (-panY / zoom) * mmSY;
  const vw2 = (ww / zoom) * mmSX;
  const vh2 = (wh / zoom) * mmSY;
  const cx = Math.max(0, vx), cy = Math.max(0, vy);
  const cw = Math.min(vw2, MM_W - Math.max(0, vx));
  const ch = Math.min(vh2, MM_H - Math.max(0, vy));
  if (cw > 0 && ch > 0) {
    mmVCtx.fillStyle = 'rgba(255,255,255,0.07)';
    mmVCtx.fillRect(cx, cy, cw, ch);
    mmVCtx.strokeStyle = 'rgba(255,255,255,0.75)';
    mmVCtx.lineWidth = 1.5;
    mmVCtx.strokeRect(cx, cy, cw, ch);
  }
}

// Click minimap to pan
mmC.addEventListener('click', e => {
  const r2 = mmC.getBoundingClientRect();
  const mx = (e.clientX - r2.left) / mmSX;
  const my = (e.clientY - r2.top) / mmSY;
  panX = wrap.clientWidth  / 2 - mx * zoom;
  panY = wrap.clientHeight / 2 - my * zoom;
  clamp(); apply();
});

drawMMViewport();
})();
"""

    # Prepend data constants (f-string only for the variable values)
    js_with_data = (
        f"const ROOMS = {rooms_json};\nconst SVG_W = {vw}, SVG_H = {vh};\n" + js
    )

    # ── Assemble HTML ─────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><title>Map Preview — {path.stem}</title>
<style>{css}</style>
</head><body>
<div id="topbar">
  <div id="title">🗺️ {path.stem}</div>
  <div id="stats">{len(rooms)} rooms · {prefix or 'all'} · scroll=zoom · drag=pan · click=details · F=search</div>
</div>
<div id="toolbar">
  <button class="btn" id="btn-zi" title="Zoom in (+)">＋</button>
  <span id="zdsp">100%</span>
  <button class="btn" id="btn-zo" title="Zoom out (-)">－</button>
  <button class="btn" id="btn-fit" title="Fit to window (0)">⟳ Fit</button>
  <div class="sep"></div>
  <button class="btn on" id="btn-grid">Grid</button>
  <button class="btn on" id="btn-lbl">Labels</button>
  <div class="sep"></div>
  <input id="search" type="text" placeholder="Search rooms… (F)">
  <div class="sep"></div>
  <button class="btn on" id="btn-sb" title="Toggle detail panel">Panel</button>
</div>
<div id="main">
  <div id="wrap">
    <svg id="svg" width="{vw}" height="{vh}" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <filter id="shadow">
          <feDropShadow dx="1" dy="1" stdDeviation="1.2"
            flood-color="#000" flood-opacity="0.9"/>
        </filter>
      </defs>
      <rect width="100%" height="100%" fill="#12121e"/>
      <g id="grid" stroke="#1e1e2c" stroke-width="1.5">
        {grid_svg}
      </g>
      <g id="rooms">{svg_body}</g>
    </svg>
    <div id="mm-wrap">
      <canvas id="mm"  width="180" height="110"></canvas>
      <canvas id="mmv" width="180" height="110"></canvas>
    </div>
  </div>
  <div id="sb">
    <div id="sb-empty">← Click a room<br>to view details</div>
    <div id="sb-hdr" style="display:none">
      <div id="sb-idx"></div>
      <div id="sb-name"></div>
      <div id="sb-meta"></div>
      <div id="sb-badges"></div>
    </div>
    <div id="sb-body" style="display:none"></div>
    <button id="sb-copy" style="display:none">📋 Copy name</button>
  </div>
</div>
<div id="tip">
  <div id="tip-name"></div>
  <div id="tip-size"></div>
  <div id="tip-ents"></div>
</div>
<script>{js_with_data}</script>
</body></html>"""

    # Write file
    if not output_file:
        out_dir = WORKSPACE / "Temp"
        out_dir.mkdir(exist_ok=True)
        output_file = str(out_dir / f"map_preview_{path.stem}.html")
    else:
        output_file = str(_resolve(output_file))

    Path(output_file).write_text(html, encoding="utf-8")
    return f"Preview saved to: {output_file}\nOpen this file in a browser to see the map.\nRooms shown: {len(rooms)}"




# ═══════════════════════════════════════════════════════════════════════════════
#  PROCEDURAL GENERATION TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@mcp.tool()
def build_pattern_library(
    map_paths: str = "",
    output_path: str = "PCG/patterns.json",
    attribution: str = "",
) -> str:
    """Scan local .bin maps and extract room patterns into a reusable library.

    The library is saved as JSON and is used by generate_room_from_pattern.
    Run this once (or whenever you add new reference maps) to populate the
    pattern pool.

    Args:
        map_paths: JSON array of map paths relative to the workspace, e.g.
                   '["Maps/01_City.bin","Maps/02_Cliffs.bin"]'.
                   Pass "" to scan every .bin file under Maps/.
        output_path: Output JSON path (relative to workspace).
                     Default: "PCG/patterns.json"
        attribution: Free-text attribution / author credit stored in each
                     extracted pattern (useful when building from mod maps).
    """
    out = _resolve(output_path)

    # Resolve map list
    if map_paths.strip():
        try:
            paths_raw = json.loads(map_paths)
            if not isinstance(paths_raw, list):
                return "map_paths must be a JSON array of strings."
        except json.JSONDecodeError:
            return f"Invalid JSON in map_paths: {map_paths}"
        bin_files = []
        for p in paths_raw:
            try:
                bin_files.append(_resolve(p))
            except ValueError as e:
                return str(e)
    else:
        maps_dir = WORKSPACE / "Maps"
        if not maps_dir.exists():
            return (
                "No Maps/ directory found. Pass map_paths explicitly or "
                "create a Maps/ folder in the workspace."
            )
        bin_files = sorted(maps_dir.rglob("*.bin"))
        if not bin_files:
            return "No .bin files found under Maps/."

    library = pcg.load_library(out)
    total_added = 0
    skipped_files = 0

    for bin_path in bin_files:
        try:
            data = cb.read_map(bin_path)
            rooms = cb.get_rooms(data)
        except Exception as exc:
            skipped_files += 1
            continue
        src = str(bin_path.relative_to(WORKSPACE))
        new_patterns = [
            pcg.extract_pattern(r, source_info=src, attribution=attribution)
            for r in rooms
        ]
        total_added += pcg.merge_patterns(library, new_patterns)

    pcg.save_library(out, library)

    total = len(library["patterns"])
    rel_out = out.relative_to(WORKSPACE)
    lines = [
        f"Pattern library updated: {rel_out}",
        f"Maps scanned:    {len(bin_files) - skipped_files}/{len(bin_files)}",
        f"Patterns added:  {total_added}",
        f"Library total:   {total} patterns",
    ]
    if skipped_files:
        lines.append(f"Files skipped (parse error): {skipped_files}")
    return "\n".join(lines)


@mcp.tool()
def generate_room_from_pattern(
    map_path: str,
    room_name: str,
    library_path: str = "PCG/patterns.json",
    strategy: str = "balanced",
    seed: int = -1,
    model_profile: str = "creative",
    x: int = 0,
    y: int = 0,
    width: int = 320,
    height: int = 184,
) -> str:
    """Generate a new room using patterns from the library and a randomness strategy.

    Picks the best matching pattern for the chosen strategy, generates a tile
    grid and entity set with seeded randomness, then writes the room to the map.

    Strategies:
      balanced     — mix of exploration and challenge (default)
      exploration  — open spaces, gentle platforming, few hazards
      challenge    — complex tiles, many hazards, tight jumps
      speedrun     — fast, linear, minimal platforms

    Model profiles (control seed behaviour):
      creative      — random seed each call; maximum variety (default)
      deterministic — stable seed derived from strategy; same output every run
      architect     — random seed; emphasises room shape and connectivity

    Args:
        map_path: Path to the .bin file to write the room into.
                  The file must exist (create it with create_map first).
        room_name: Name for the new room (lvl_ prefix added automatically).
        library_path: Path to the pattern library JSON (default: PCG/patterns.json).
        strategy: Generation strategy — balanced/exploration/challenge/speedrun.
        seed: Integer seed >= 0 for reproducible output; -1 = auto.
        model_profile: Seed-selection profile — creative/deterministic/architect.
        x: Room X position in pixels.
        y: Room Y position in pixels.
        width: Room width in pixels (multiple of 8, default 320).
        height: Room height in pixels (multiple of 8, default 184).
    """
    if strategy not in pcg.STRATEGIES:
        return f"Unknown strategy '{strategy}'. Choose from: {', '.join(pcg.STRATEGIES)}"
    if model_profile not in pcg.MODEL_PROFILES:
        return (
            f"Unknown model_profile '{model_profile}'. "
            f"Choose from: {', '.join(pcg.MODEL_PROFILES)}"
        )
    if width % 8 != 0 or height % 8 != 0:
        return f"width and height must be multiples of 8 (got {width}x{height})."
    if width <= 0 or height <= 0:
        return f"width and height must be positive (got {width}x{height})."

    map_file = _resolve(map_path)
    if not map_file.exists():
        return (
            f"Map file not found: {map_path}. "
            "Create it first with create_map."
        )

    lib_file = _resolve(library_path)
    library = pcg.load_library(lib_file)
    patterns = library.get("patterns", [])

    # Resolve seed and build RNG
    size_class = pcg.classify_room_size(width, height)
    actual_seed = pcg.resolve_seed(seed, strategy, model_profile)
    rng = random.Random(actual_seed)

    reference = pcg.pick_pattern(rng, patterns, strategy, size_class)

    # Generate tiles and entities
    fg_tiles = pcg.generate_tile_grid(rng, width, height, strategy, reference)
    entity_list = pcg.generate_entities_for_room(
        rng, width, height, strategy, reference
    )

    # Build air tile strings for bg/obj layers
    tw, th = width // 8, height // 8
    air_row = pcg.TILE_AIR * tw
    air_tiles = "\n".join([air_row] * th)
    obj_row = ",".join(["-1"] * tw)
    obj_tiles = "\n".join([obj_row] * th)

    name = room_name if room_name.startswith("lvl_") else f"lvl_{room_name}"

    # Load map and check for duplicate
    data = cb.read_map(map_file)
    levels = cb.find_child(data, "levels")
    if levels is None:
        return "Invalid map: no 'levels' element."
    for r in cb.get_rooms(data):
        if r.get("name") == name:
            return f"Room '{name}' already exists in {map_path}."

    # Build entity elements
    entity_children = [
        {
            "__name": e["__name"],
            "__children": e.get("__children", []),
            **{k: v for k, v in e.items() if k not in ("__name", "__children")},
        }
        for e in entity_list
    ]

    room: dict = {
        "__name": "level",
        "name": name,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "music": "",
        "alt_music": "",
        "ambience": "",
        "dark": False,
        "space": False,
        "underwater": False,
        "whisper": False,
        "disableDownTransition": False,
        "windPattern": "None",
        "musicLayer1": True,
        "musicLayer2": True,
        "musicLayer3": True,
        "musicLayer4": True,
        "musicProgress": "",
        "ambienceProgress": "",
        "cameraOffsetX": 0,
        "cameraOffsetY": 0,
        "delayAltMusicFade": False,
        "c": 0,
        "__children": [
            {
                "__name": "solids",
                "innerText": fg_tiles,
                "offsetX": 0,
                "offsetY": 0,
                "__children": [],
            },
            {
                "__name": "bg",
                "innerText": air_tiles,
                "offsetX": 0,
                "offsetY": 0,
                "__children": [],
            },
            {
                "__name": "objtiles",
                "innerText": obj_tiles,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {
                "__name": "fgtiles",
                "innerText": obj_tiles,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {
                "__name": "bgtiles",
                "innerText": obj_tiles,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {"__name": "entities", "__children": entity_children},
            {"__name": "triggers", "__children": []},
            {"__name": "fgdecals", "__children": []},
            {"__name": "bgdecals", "__children": []},
        ],
    }

    levels["__children"].append(room)
    cb.write_map(map_file, data)

    entity_summary = ", ".join(
        f"{e['__name']}"
        for e in entity_list
    )
    ref_note = (
        f"reference pattern: {reference['id']} from {reference['source']!r}"
        if reference
        else "no reference pattern (library empty — generic layout used)"
    )
    lines = [
        f"Generated room '{name}' in {map_path}",
        f"  Position:  ({x}, {y})",
        f"  Size:      {width}x{height} px ({size_class})",
        f"  Strategy:  {strategy}",
        f"  Profile:   {model_profile}",
        f"  Seed:      {actual_seed}",
        f"  Entities:  {entity_summary}",
        f"  Ref:       {ref_note}",
    ]
    return "\n".join(lines)


@mcp.tool()
def validate_room(map_path: str, room_name: str) -> str:
    """Check a room for common playability problems.

    Reports issues such as: missing player spawn, no floor tiles, entity
    positions outside room bounds, or invalid dimensions.  An empty warning
    list means the room passed all checks.

    Args:
        map_path: Path to the .bin file.
        room_name: Room name (with or without 'lvl_' prefix).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    warnings = pcg.validate_room_structure(room)

    name = room.get("name", room_name)
    w = room.get("width", 0)
    h = room.get("height", 0)

    lines = [
        f"Validation: {name} ({w}x{h} px)",
        f"Status: {'PASS ✓' if not warnings else f'FAIL — {len(warnings)} issue(s)'}",
    ]
    if warnings:
        lines.append("")
        lines.append("Issues found:")
        for warn in warnings:
            lines.append(f"  ✗ {warn}")
    else:
        lines.append("No issues detected.")
    return "\n".join(lines)


@mcp.tool()
def ingest_external_map(
    source_url: str,
    attribution: str = "",
    confirm_download: bool = False,
    tags: str = "",
    library_path: str = "PCG/patterns.json",
) -> str:
    """Download a Celeste map from an external URL and extract room patterns.

    Supports:
      • Direct .bin URL — downloads the file directly.
      • .zip URL — downloads archive and extracts all .bin files inside.
      • GameBanana mod page URL (https://gamebanana.com/mods/XXXXX) —
        queries the GameBanana API to find downloadable files, then
        fetches the first .zip or .bin found.

    Downloaded files are saved under PCG/Datasets/ in the workspace.
    An attribution metadata JSON is written alongside the files.
    Patterns are extracted and merged into the pattern library.

    ⚠  Legal / compliance notice:
      Always verify that the mod's licence permits derivative use before
      building on its patterns.  This tool records the attribution string
      for traceability.  GameBanana mods are covered by their authors'
      individual licences — credit original creators in your project.

    Args:
        source_url: URL to a .bin file, .zip archive, or GameBanana mod page.
        attribution: Author / licence credit (required for good practice).
        confirm_download: Must be True to actually download.  Pass False to
                          do a dry-run that shows what would happen.
        tags: Comma-separated or JSON-array tags to apply to extracted patterns.
        library_path: Path to the pattern library JSON (default: PCG/patterns.json).
    """
    if not source_url.strip():
        return "source_url is required."

    # Parse extra tags
    extra_tags: list = []
    if tags.strip():
        try:
            parsed = json.loads(tags)
            if isinstance(parsed, list):
                extra_tags = [str(t) for t in parsed]
            else:
                extra_tags = [str(parsed)]
        except json.JSONDecodeError:
            extra_tags = [t.strip() for t in tags.split(",") if t.strip()]

    if not confirm_download:
        return (
            "Dry-run — no files downloaded.\n"
            f"  Source URL:  {source_url}\n"
            f"  Attribution: {attribution or '(none)'}\n"
            f"  Extra tags:  {extra_tags or '(none)'}\n"
            f"  Library:     {library_path}\n\n"
            "To proceed, call this tool again with confirm_download=True.\n"
            "Ensure the mod licence permits derivative use before downloading."
        )

    # ── Resolve GameBanana page URL to a direct download URL ──
    download_url = source_url
    gb_match = re.match(
        r"https?://(?:www\.)?gamebanana\.com/mods/(\d+)", source_url
    )
    if gb_match:
        mod_id = gb_match.group(1)
        api_url = (
            f"https://api.gamebanana.com/Core/Item/Data"
            f"?itemtype=Mod&itemid={mod_id}"
            f"&fields=name,Owner%28%29.name,Files%28%29.aFiles%28%29"
        )
        try:
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": "loenn-mcp/2.0 (pattern-ingestion)"},
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                api_data = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            return f"Failed to query GameBanana API: {exc}"

        # api_data is a list: [mod_name, owner_name, files_dict]
        if not isinstance(api_data, list) or len(api_data) < 3:
            return (
                f"Unexpected GameBanana API response shape for mod {mod_id}.\n"
                "Try passing the direct download URL instead."
            )
        mod_name = api_data[0] or f"mod-{mod_id}"
        owner_name = api_data[1] or "unknown"
        files_dict = api_data[2]  # {file_id: {sFile, sDownloadUrl, ...}, ...}

        if not attribution:
            attribution = f"{mod_name} by {owner_name} (GameBanana mod {mod_id})"

        # Find first .zip or .bin in the files dict
        download_url = ""
        if isinstance(files_dict, dict):
            for _fid, finfo in files_dict.items():
                if not isinstance(finfo, dict):
                    continue
                url_candidate = finfo.get("sDownloadUrl", "")
                fname = finfo.get("sFile", "").lower()
                if fname.endswith(".zip") or fname.endswith(".bin"):
                    download_url = url_candidate
                    break

        if not download_url:
            return (
                f"No downloadable .bin or .zip found for GameBanana mod {mod_id}.\n"
                f"Files returned: {list(files_dict.keys()) if isinstance(files_dict, dict) else files_dict}"
            )

        time.sleep(1)  # be courteous to the GameBanana API

    # ── Download the file ──
    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "loenn-mcp/2.0 (pattern-ingestion)"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw_bytes = resp.read()
    except urllib.error.URLError as exc:
        return f"Download failed: {exc}\nURL: {download_url}"

    url_lower = download_url.lower().split("?")[0]

    # ── Collect .bin file bytes ──
    bin_files: list = []  # list of (filename, bytes)

    if url_lower.endswith(".bin"):
        fname = Path(url_lower).name or "map.bin"
        bin_files.append((fname, raw_bytes))
    elif url_lower.endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                for member in zf.namelist():
                    if member.lower().endswith(".bin"):
                        bin_files.append((Path(member).name, zf.read(member)))
        except zipfile.BadZipFile as exc:
            return f"Failed to open zip archive: {exc}"
    else:
        # Attempt .bin parse heuristic then zip
        if raw_bytes[:11] == b"CELESTE MAP":
            fname = Path(url_lower).name or "map.bin"
            bin_files.append((fname, raw_bytes))
        else:
            try:
                with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
                    for member in zf.namelist():
                        if member.lower().endswith(".bin"):
                            bin_files.append((Path(member).name, zf.read(member)))
            except zipfile.BadZipFile:
                return (
                    "Could not determine file type from URL. "
                    "Provide a direct .bin or .zip URL."
                )

    if not bin_files:
        return "No .bin map files found in the downloaded content."

    # ── Save to workspace and extract patterns ──
    url_hash = re.sub(r"[^\w]", "_", re.sub(r"https?://", "", download_url))[:48]
    dataset_dir = WORKSPACE / "PCG" / "Datasets" / url_hash
    dataset_dir.mkdir(parents=True, exist_ok=True)

    # Attribution metadata
    meta = {
        "source_url": source_url,
        "download_url": download_url,
        "attribution": attribution,
        "tags": extra_tags,
        "files": [fname for fname, _ in bin_files],
    }
    (dataset_dir / "attribution.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    lib_file = _resolve(library_path)
    library = pcg.load_library(lib_file)
    total_added = 0
    total_rooms = 0
    skipped = 0

    for fname, bin_bytes in bin_files:
        save_path = dataset_dir / fname
        save_path.write_bytes(bin_bytes)
        try:
            map_data = cb.read_map(save_path)
            rooms = cb.get_rooms(map_data)
        except Exception:
            skipped += 1
            continue
        total_rooms += len(rooms)
        new_patterns = [
            pcg.extract_pattern(
                r,
                source_info=source_url,
                attribution=attribution,
            )
            for r in rooms
        ]
        # Attach extra tags
        if extra_tags:
            for p in new_patterns:
                for t in extra_tags:
                    if t not in p["tags"]:
                        p["tags"].append(t)
        total_added += pcg.merge_patterns(library, new_patterns)

    pcg.save_library(lib_file, library)

    rel_dir = dataset_dir.relative_to(WORKSPACE)
    lines = [
        f"Ingested external map from: {source_url}",
        f"  Attribution:     {attribution or '(none provided)'}",
        f"  Saved to:        {rel_dir}/",
        f"  .bin files:      {len(bin_files)} ({skipped} skipped)",
        f"  Rooms processed: {total_rooms}",
        f"  Patterns added:  {total_added}",
        f"  Library total:   {len(library['patterns'])} patterns",
        f"  Library path:    {library_path}",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    mcp.run()


if __name__ == "__main__":
    main()
