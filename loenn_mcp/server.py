"""
Lönn MCP Server — Celeste Map Editor for AI Agents

Provides 60 tools for reading, editing, analyzing, and generating Celeste map
files (.bin) directly from VS Code via the Model Context Protocol.

Tools (60 across 18 categories):
  Map Reading:      list_maps, read_map_overview, read_room, get_room_tiles
  Map Reading Ext:  read_map_metadata, search_entities, search_triggers, compare_rooms
  Map Editing:      add_entity, remove_entity, add_trigger, remove_trigger,
                    set_room_tiles, add_room, remove_room, create_map
  Map Editing Ext:  update_entity, move_entity, update_room, clone_room,
                    batch_add_entities, resize_room
  Decals:           list_decals, add_decal, remove_decal
  Stylegrounds:     list_stylegrounds, add_styleground, remove_styleground,
                    update_styleground
  Entity Catalog:   list_entity_definitions, get_entity_definition,
                    list_trigger_definitions
  Catalog Ext:      get_trigger_definition, get_effect_definition
  Analysis:         analyze_map, visualize_map_layout
  Advanced Analysis:analyze_entity_usage, analyze_difficulty, find_entity_references,
                    detect_map_patterns, analyze_room_connectivity
  Suggestions:      suggest_improvements, compare_maps
  Wiki/Cache:       wiki_save, wiki_search, wiki_list, wiki_get
  Mod Project:      get_mod_info, validate_map
  Import/Export:    export_room_json, import_room_json
  Diff & Fix:       summarize_map_diff, batch_validate_and_fix
  Generation:       build_pattern_library, generate_room_from_pattern,
                    validate_room, ingest_external_map
  Image/Terrain:    generate_map_from_image, generate_terrain_map,
                    preview_terrain_biomes

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
    from . import image_map                  # installed package
    from . import terrain_gen                # installed package
    from . import gdep_tools                 # installed package
except ImportError:
    import celeste_bin as cb                 # run directly from source
    import pcg                               # run directly from source
    import image_map                         # run directly from source
    import terrain_gen                       # run directly from source
    import gdep_tools                        # run directly from source

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
def add_trigger(
    map_path: str,
    room_name: str,
    trigger_name: str,
    x: int,
    y: int,
    width: int = 16,
    height: int = 16,
    trigger_id: int = -1,
    properties: str = "{}",
    nodes: str = "[]",
) -> str:
    """Add a trigger to a room and save the map.

    Triggers are rectangular regions that fire effects when the player enters
    them (dialog, camera moves, music changes, flag toggles, etc.). Trigger
    IDs are auto-assigned to be unique across both entities and triggers in
    the room.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        trigger_name: Trigger type (e.g. "everest/dialogTrigger",
            "2.5DHelper/StarterFlagTrigger")
        x: X position in pixels
        y: Y position in pixels
        width: Trigger width in pixels (default 16)
        height: Trigger height in pixels (default 16)
        trigger_id: Trigger ID (-1 to auto-assign)
        properties: JSON object string of extra properties
            (e.g. '{"dialog_id": "MAP_INTRO"}')
        nodes: JSON array of {"x": int, "y": int} objects for triggers that
            need path/target nodes (e.g. cameraTargetTrigger). Default "[]".
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    trig_el = cb.find_child(room, "triggers")
    if trig_el is None:
        trig_el = {"__name": "triggers", "__children": []}
        room["__children"].append(trig_el)

    if trigger_id < 0:
        trigger_id = _next_entity_id(room)

    try:
        props = json.loads(properties)
    except json.JSONDecodeError:
        return f"Invalid JSON properties: {properties}"
    if not isinstance(props, dict):
        return "properties must be a JSON object."

    try:
        node_list = json.loads(nodes)
    except json.JSONDecodeError:
        return f"Invalid JSON nodes: {nodes}"
    if not isinstance(node_list, list):
        return "nodes must be a JSON array of {x, y} objects."

    node_children: list[dict] = []
    for i, n in enumerate(node_list):
        if not isinstance(n, dict) or "x" not in n or "y" not in n:
            return f"Invalid node at index {i}: expected {{'x': int, 'y': int}}."
        node_children.append({
            "__name": "node",
            "__children": [],
            "x": int(n["x"]),
            "y": int(n["y"]),
        })

    trigger: dict = {
        "__name": trigger_name,
        "__children": node_children,
        "id": trigger_id,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }
    _protected = frozenset(("__name", "__children", "id", "x", "y", "width", "height"))
    trigger.update({k: v for k, v in props.items() if k not in _protected})

    trig_el["__children"].append(trigger)
    cb.write_map(path, data)

    suffix = f" with {len(node_children)} node(s)" if node_children else ""
    return (
        f"Added trigger '{trigger_name}' (id={trigger_id}) at "
        f"({x},{y}) {width}x{height} to room '{room_name}'{suffix}."
    )


@mcp.tool()
def remove_trigger(map_path: str, room_name: str, trigger_id: int) -> str:
    """Remove a trigger from a room by its ID.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        trigger_id: Trigger ID to remove
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)

    if room is None:
        return f"Room '{room_name}' not found."

    trig_el = cb.find_child(room, "triggers")
    if trig_el is None:
        return "No triggers in this room."

    children = trig_el.get("__children", [])
    before = len(children)
    trig_el["__children"] = [t for t in children if t.get("id") != trigger_id]

    if len(trig_el["__children"]) == before:
        return f"Trigger id={trigger_id} not found."

    cb.write_map(path, data)
    return f"Removed trigger id={trigger_id} from '{room_name}'."


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
#  STYLEGROUND TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

# Attributes shown inline next to a styleground in list_stylegrounds output.
_STYLE_PREVIEW_KEYS = (
    "texture", "only", "exclude", "flag", "notflag", "tag",
    "scrollx", "scrolly", "speedx", "speedy", "color", "alpha",
)


def _style_layer_element(map_data: dict, layer: str, create: bool = False) -> dict | None:
    """Return Style/Foregrounds or Style/Backgrounds, optionally creating it."""
    layer_norm = layer.lower()
    if layer_norm in ("fg", "foreground", "foregrounds"):
        layer_name = "Foregrounds"
    elif layer_norm in ("bg", "background", "backgrounds"):
        layer_name = "Backgrounds"
    else:
        return None

    style = cb.find_child(map_data, "Style")
    if style is None:
        if not create:
            return None
        style = {"__name": "Style", "__children": [
            {"__name": "Foregrounds", "__children": []},
            {"__name": "Backgrounds", "__children": []},
        ]}
        map_data["__children"].append(style)

    el = cb.find_child(style, layer_name)
    if el is None and create:
        el = {"__name": layer_name, "__children": []}
        style["__children"].append(el)
    return el


def _styleground_summary(el: dict) -> str:
    """One-line preview of a styleground element (effect name + key props)."""
    name = el.get("__name", "?")
    parts = [name]
    for k in _STYLE_PREVIEW_KEYS:
        if k in el:
            parts.append(f"{k}={el[k]}")
    return " ".join(parts)


@mcp.tool()
def list_stylegrounds(map_path: str) -> str:
    """List all stylegrounds (foreground + background effects) in a map.

    Stylegrounds live under Style/Foregrounds and Style/Backgrounds. Each
    entry is shown with its top-level index (used by remove_styleground /
    update_styleground), the effect type, and a few key attributes. Children
    of `apply` group elements are listed indented underneath their group.

    Args:
        map_path: Path to the .bin file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    lines = [f"Map: {data.get('_package', '?')}"]
    for layer_name in ("Foregrounds", "Backgrounds"):
        layer_el = _style_layer_element(data, layer_name)
        children = layer_el.get("__children", []) if layer_el else []
        lines.append("")
        lines.append(f"{layer_name} ({len(children)}):")
        if not children:
            lines.append("  (none)")
            continue
        for i, sg in enumerate(children):
            lines.append(f"  [{i}] {_styleground_summary(sg)}")
            # apply groups can wrap nested stylegrounds
            for nested in sg.get("__children", []):
                lines.append(f"        \u21b3 {_styleground_summary(nested)}")
    return "\n".join(lines)


@mcp.tool()
def add_styleground(
    map_path: str,
    effect_name: str,
    layer: str = "bg",
    properties: str = "{}",
    index: int = -1,
) -> str:
    """Add a styleground effect to a map.

    Stylegrounds are layered visual effects (parallax backgrounds, custom
    Lua effects like 2.5DHelper/VoidBg, snow/dust overlays, etc.) rendered
    behind or in front of every room. They live under Style/Foregrounds or
    Style/Backgrounds — auto-created if missing.

    Args:
        map_path: Path to the .bin file
        effect_name: Effect type (e.g. "parallax", "2.5DHelper/VoidBg",
            "WhiteholeBg", "apply" for a group element)
        layer: "bg" / "background" / "backgrounds" (default), or "fg" /
            "foreground" / "foregrounds"
        properties: JSON object of effect properties
            (e.g. '{"texture": "bgs/01/bg", "only": "lvl_a-01"}')
        index: Position to insert at (-1 appends to the end)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)

    layer_el = _style_layer_element(data, layer, create=True)
    if layer_el is None:
        return (
            f"Invalid layer: '{layer}'. Use 'fg'/'foregrounds' or "
            f"'bg'/'backgrounds'."
        )

    try:
        props = json.loads(properties)
    except json.JSONDecodeError:
        return f"Invalid JSON properties: {properties}"
    if not isinstance(props, dict):
        return "properties must be a JSON object."

    sg: dict = {"__name": effect_name, "__children": []}
    _protected = frozenset(("__name", "__children"))
    sg.update({k: v for k, v in props.items() if k not in _protected})

    children = layer_el["__children"]
    if index < 0 or index >= len(children):
        children.append(sg)
        pos = len(children) - 1
    else:
        children.insert(index, sg)
        pos = index

    cb.write_map(path, data)

    layer_label = layer_el.get("__name", layer)
    return (
        f"Added styleground '{effect_name}' to {layer_label} at index {pos}."
    )


@mcp.tool()
def remove_styleground(map_path: str, layer: str, index: int) -> str:
    """Remove a styleground from a map by its index in the layer.

    Use list_stylegrounds first to see the current indices.

    Args:
        map_path: Path to the .bin file
        layer: "fg"/"foregrounds" or "bg"/"backgrounds"
        index: 0-based index of the styleground in that layer
    """
    path = _resolve(map_path)
    data = cb.read_map(path)

    layer_el = _style_layer_element(data, layer)
    if layer_el is None:
        return f"No '{layer}' stylegrounds in this map."

    children = layer_el.get("__children", [])
    if not (0 <= index < len(children)):
        return (
            f"Index {index} out of range "
            f"(layer has {len(children)} stylegrounds)."
        )

    removed = children.pop(index)
    cb.write_map(path, data)
    return (
        f"Removed styleground '{removed.get('__name', '?')}' "
        f"at index {index} from {layer_el.get('__name', layer)}."
    )


@mcp.tool()
def update_styleground(
    map_path: str,
    layer: str,
    index: int,
    properties: str,
) -> str:
    """Merge properties into an existing styleground without replacing it.

    Existing keys in `properties` overwrite the styleground's values; keys
    not present in `properties` are left unchanged. To clear a key, pass
    its value as null.

    Args:
        map_path: Path to the .bin file
        layer: "fg"/"foregrounds" or "bg"/"backgrounds"
        index: 0-based index of the styleground in that layer
        properties: JSON object of properties to merge in
    """
    path = _resolve(map_path)
    data = cb.read_map(path)

    layer_el = _style_layer_element(data, layer)
    if layer_el is None:
        return f"No '{layer}' stylegrounds in this map."

    children = layer_el.get("__children", [])
    if not (0 <= index < len(children)):
        return (
            f"Index {index} out of range "
            f"(layer has {len(children)} stylegrounds)."
        )

    try:
        props = json.loads(properties)
    except json.JSONDecodeError:
        return f"Invalid JSON properties: {properties}"
    if not isinstance(props, dict):
        return "properties must be a JSON object."

    sg = children[index]
    _protected = frozenset(("__name", "__children"))
    cleared: list[str] = []
    updated: list[str] = []
    for k, v in props.items():
        if k in _protected:
            continue
        if v is None:
            if k in sg:
                del sg[k]
                cleared.append(k)
        else:
            sg[k] = v
            updated.append(k)

    cb.write_map(path, data)

    parts = []
    if updated:
        parts.append(f"set {', '.join(updated)}")
    if cleared:
        parts.append(f"cleared {', '.join(cleared)}")
    summary = "; ".join(parts) if parts else "no changes"
    return (
        f"Updated styleground '{sg.get('__name', '?')}' "
        f"at {layer_el.get('__name', layer)}[{index}]: {summary}."
    )


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
#  IMAGE-TO-MAP AND TERRAIN GENERATION TOOLS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def generate_map_from_image(
    image_path: str,
    output_path: str = "",
    package_name: str = "ImageMap",
    scale: int = 1,
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
    color_map_json: str = "",
    tolerance: int = 64,
) -> str:
    """Convert a color-mapped image into a full playable Celeste map.

    Each pixel (or pixel block when scale > 1) in the source image is
    interpreted as one 8×8 tile in the map.  Colors are mapped to tile
    types and entities using either the default color map or a custom one.

    Default color mapping:
      Black (#000000)   → Solid tile
      White (#FFFFFF)   → Air (empty space)
      Red (#FF0000)     → Spike hazard
      Green (#00FF00)   → Player spawn point
      Blue (#0000FF)    → Jump-through platform
      Yellow (#FFFF00)  → Strawberry collectible
      Magenta (#FF00FF) → Spring (bounce pad)
      Cyan (#00FFFF)    → Refill crystal
      Orange (#FF8000)  → Crumble block
      Grey (#808080)    → Background solid (decorative)

    The image is automatically split into rooms of the specified tile size.
    Each room gets a player spawn if none is found in the color data.

    Args:
        image_path: Path to the source image (PNG, JPG, BMP, etc.)
                    relative to the workspace.
        output_path: Output .bin map file path (relative to workspace).
                     Default: auto-generated from image filename.
        package_name: Celeste map package name (default: "ImageMap").
        scale: Pixels per tile — 1 means each pixel = one tile.
               Use higher values for large images (e.g. scale=4 means
               every 4×4 pixel block = one tile).
        room_width_tiles: Max room width in tiles (default 40 = 320px).
        room_height_tiles: Max room height in tiles (default 23 = 184px).
        color_map_json: Optional custom color map as JSON object.
                        Keys are hex colors (e.g. "#FF0000"), values are
                        role strings (solid/air/spike/spawn/jumpthru/
                        strawberry/spring/refill/crumble/bg_solid).
                        Example: '{"#FF0000":"solid","#00FF00":"spawn"}'
        tolerance: Color matching tolerance (0-255). Higher = more lenient
                   matching. Default 64.
    """
    # Resolve image path
    img_file = _resolve(image_path)
    if not img_file.exists():
        return f"Image file not found: {image_path}"

    # Parse custom color map if provided
    custom_cmap = None
    if color_map_json.strip():
        try:
            raw = json.loads(color_map_json)
            if not isinstance(raw, dict):
                return "color_map_json must be a JSON object (dict)."
            custom_cmap = {}
            for hex_color, role in raw.items():
                hex_color = hex_color.strip().lstrip("#")
                if len(hex_color) != 6:
                    return f"Invalid hex color: #{hex_color}. Use 6-digit hex."
                r = int(hex_color[0:2], 16)
                g = int(hex_color[2:4], 16)
                b = int(hex_color[4:6], 16)
                custom_cmap[(r, g, b)] = role
        except (json.JSONDecodeError, ValueError) as e:
            return f"Invalid color_map_json: {e}"

    # Resolve output path
    if not output_path.strip():
        stem = img_file.stem
        output_path = f"Maps/ImageMap/{stem}.bin"
    out_file = _resolve(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Generate map data
    try:
        map_data = image_map.image_to_map_data(
            image_path=str(img_file),
            package_name=package_name,
            color_map=custom_cmap,
            scale=scale,
            room_width_tiles=room_width_tiles,
            room_height_tiles=room_height_tiles,
            tolerance=tolerance,
        )
    except ImportError as e:
        return (
            f"Missing dependency: {e}\n"
            "Install Pillow with: pip install Pillow"
        )
    except Exception as e:
        return f"Error converting image: {e}"

    # Write .bin file
    cb.write_map(out_file, map_data)

    # Count rooms and entities
    levels = cb.find_child(map_data, "levels")
    room_count = len(levels["__children"]) if levels else 0
    total_entities = 0
    for room in (levels or {}).get("__children", []):
        ent_el = cb.find_child(room, "entities")
        if ent_el:
            total_entities += len(ent_el.get("__children", []))

    rel_out = out_file.relative_to(WORKSPACE)
    lines = [
        f"Map generated from image: {image_path}",
        f"  Output:     {rel_out}",
        f"  Package:    {package_name}",
        f"  Scale:      {scale} px/tile",
        f"  Rooms:      {room_count}",
        f"  Entities:   {total_entities}",
        f"  Room size:  {room_width_tiles}x{room_height_tiles} tiles "
        f"({room_width_tiles * 8}x{room_height_tiles * 8} px)",
        f"  Tolerance:  {tolerance}",
    ]
    if custom_cmap:
        lines.append(f"  Custom colors: {len(custom_cmap)} entries")
    else:
        lines.append("  Color map: default (10 colours)")
    return "\n".join(lines)


@mcp.tool()
def generate_terrain_map(
    output_path: str = "",
    package_name: str = "TerrainGen",
    seed: int = -1,
    width_rooms: int = 4,
    height_rooms: int = 3,
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
    frequency: float = 8.0,
    voronoi_points: int = 12,
    biome_set: str = "",
    difficulty: int = 3,
) -> str:
    """Generate a procedural Celeste map using Perlin noise and Voronoi biomes.

    Creates a complete playable map with terrain shaped by Perlin noise and
    biome regions defined by Voronoi diagrams.  Inspired by procedural map
    generators that combine noise-based heightmaps with regional variety.

    Each room is assigned a biome (mountain, forest, plains, lake, cave,
    summit) based on its Voronoi region and local noise value.  Tile density,
    platform placement, hazards, and collectibles are all biome-aware.

    Biomes:
      mountain — dense tiles, tight platforms, spikes
      forest   — moderate density, many platforms, springs
      plains   — open spaces, gentle platforms, collectibles
      lake     — jump-throughs over gaps, refills
      cave     — enclosed spaces, crumble blocks, dark rooms
      summit   — sparse platforms, wind effects

    The generator is fully seeded: the same seed + parameters always produce
    the same map.

    Args:
        output_path: Output .bin map file path (relative to workspace).
                     Default: auto-generated as "Maps/TerrainGen/seed_<N>.bin".
        package_name: Celeste map package name (default: "TerrainGen").
        seed: Integer seed for reproducible generation.
              -1 = generate random seed.
        width_rooms: Number of rooms horizontally (default 4).
        height_rooms: Number of rooms vertically (default 3).
        room_width_tiles: Tiles per room width (default 40 = 320px).
        room_height_tiles: Tiles per room height (default 23 = 184px).
        frequency: Perlin noise frequency — lower = smoother terrain.
                   Range 2-32 recommended (default 8).
        voronoi_points: Number of biome region centres (default 12).
                        More points = smaller, more varied regions.
        biome_set: Comma-separated or JSON array of biomes to use.
                   Default (empty): use all biomes.
                   Example: "mountain,cave,summit" or '["forest","plains"]'
        difficulty: 1-5 difficulty scale (default 3).
                    Affects hazard count and tile density.
    """
    # Resolve seed
    if seed < 0:
        seed = random.randint(0, 0xFFFF_FFFF)

    # Parse biome set
    biomes: list = []
    if biome_set.strip():
        try:
            parsed = json.loads(biome_set)
            if isinstance(parsed, list):
                biomes = [str(b).strip() for b in parsed]
            else:
                biomes = [str(parsed).strip()]
        except json.JSONDecodeError:
            biomes = [b.strip() for b in biome_set.split(",") if b.strip()]

    # Validate biomes
    valid_biomes = set(terrain_gen.BIOMES)
    if biomes:
        invalid = [b for b in biomes if b not in valid_biomes]
        if invalid:
            return (
                f"Unknown biome(s): {', '.join(invalid)}. "
                f"Valid biomes: {', '.join(valid_biomes)}"
            )
    else:
        biomes = None  # Use all biomes

    # Validate parameters
    if width_rooms < 1 or height_rooms < 1:
        return "width_rooms and height_rooms must be >= 1."
    if width_rooms > 20 or height_rooms > 20:
        return "Maximum 20x20 room grid (400 rooms)."
    if room_width_tiles < 10 or room_height_tiles < 10:
        return "Room dimensions must be at least 10 tiles."
    if not (1 <= difficulty <= 5):
        return "difficulty must be between 1 and 5."

    # Resolve output path
    if not output_path.strip():
        output_path = f"Maps/TerrainGen/seed_{seed}.bin"
    out_file = _resolve(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # Generate map
    map_data = terrain_gen.generate_terrain_map(
        seed=seed,
        width_rooms=width_rooms,
        height_rooms=height_rooms,
        room_width_tiles=room_width_tiles,
        room_height_tiles=room_height_tiles,
        frequency=frequency,
        voronoi_points=voronoi_points,
        biome_set=biomes,
        difficulty=difficulty,
        package_name=package_name,
    )

    # Write .bin file
    cb.write_map(out_file, map_data)

    # Get biome summary
    summary = terrain_gen.get_biome_summary(
        seed=seed,
        width_rooms=width_rooms,
        height_rooms=height_rooms,
        room_width_tiles=room_width_tiles,
        room_height_tiles=room_height_tiles,
        frequency=frequency,
        voronoi_points=voronoi_points,
        biome_set=biomes,
    )

    # Count entities
    levels = cb.find_child(map_data, "levels")
    room_count = len(levels["__children"]) if levels else 0
    total_entities = 0
    for room in (levels or {}).get("__children", []):
        ent_el = cb.find_child(room, "entities")
        if ent_el:
            total_entities += len(ent_el.get("__children", []))

    rel_out = out_file.relative_to(WORKSPACE)
    lines = [
        f"Terrain map generated with seed {seed}",
        f"  Output:        {rel_out}",
        f"  Package:       {package_name}",
        f"  Grid:          {width_rooms}x{height_rooms} rooms ({room_count} total)",
        f"  Room size:     {room_width_tiles}x{room_height_tiles} tiles "
        f"({room_width_tiles * 8}x{room_height_tiles * 8} px)",
        f"  Frequency:     {frequency}",
        f"  Voronoi pts:   {voronoi_points}",
        f"  Difficulty:    {difficulty}/5",
        f"  Entities:      {total_entities}",
        f"  Seed:          {seed}",
        "",
        summary,
    ]
    return "\n".join(lines)


@mcp.tool()
def preview_terrain_biomes(
    seed: int = 42,
    width_rooms: int = 4,
    height_rooms: int = 3,
    frequency: float = 8.0,
    voronoi_points: int = 12,
    biome_set: str = "",
) -> str:
    """Preview the biome layout for a terrain generation without creating the map.

    Shows an ASCII grid of which biome each room would get, useful for
    trying different seeds and parameters before committing to generation.

    Args:
        seed: Integer seed for the preview.
        width_rooms: Number of rooms horizontally.
        height_rooms: Number of rooms vertically.
        frequency: Perlin noise frequency.
        voronoi_points: Number of Voronoi biome centres.
        biome_set: Comma-separated or JSON array of biomes.
    """
    # Parse biome set
    biomes: list = []
    if biome_set.strip():
        try:
            parsed = json.loads(biome_set)
            if isinstance(parsed, list):
                biomes = [str(b).strip() for b in parsed]
            else:
                biomes = [str(parsed).strip()]
        except json.JSONDecodeError:
            biomes = [b.strip() for b in biome_set.split(",") if b.strip()]

    valid_biomes = set(terrain_gen.BIOMES)
    if biomes:
        invalid = [b for b in biomes if b not in valid_biomes]
        if invalid:
            return (
                f"Unknown biome(s): {', '.join(invalid)}. "
                f"Valid: {', '.join(valid_biomes)}"
            )
    else:
        biomes = None

    return terrain_gen.get_biome_summary(
        seed=seed,
        width_rooms=width_rooms,
        height_rooms=height_rooms,
        frequency=frequency,
        voronoi_points=voronoi_points,
        biome_set=biomes,
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP READING EXTENSIONS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def read_map_metadata(map_path: str) -> str:
    """Read high-level metadata from a map without listing all room contents.

    Returns package name, room count, total entity/trigger counts,
    world bounds, and style information.

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    total_entities = 0
    total_triggers = 0
    for r in rooms:
        for child in r.get("__children", []):
            if child.get("__name") == "entities":
                total_entities += len(child.get("__children", []))
            elif child.get("__name") == "triggers":
                total_triggers += len(child.get("__children", []))

    xs = [r.get("x", 0) for r in rooms]
    ys = [r.get("y", 0) for r in rooms]
    max_x = max(r.get("x", 0) + r.get("width", 0) for r in rooms) if rooms else 0
    max_y = max(r.get("y", 0) + r.get("height", 0) for r in rooms) if rooms else 0

    style = cb.find_child(data, "Style")
    fg_count = 0
    bg_count = 0
    if style:
        fg = cb.find_child(style, "Foregrounds")
        bg = cb.find_child(style, "Backgrounds")
        fg_count = len(fg.get("__children", [])) if fg else 0
        bg_count = len(bg.get("__children", [])) if bg else 0

    lines = [
        f"Map: {path.stem}",
        f"  Package:       {data.get('package', '?')}",
        f"  Rooms:         {len(rooms)}",
        f"  Total entities:{total_entities}",
        f"  Total triggers:{total_triggers}",
        f"  World bounds:  ({min(xs) if xs else 0},{min(ys) if ys else 0}) to ({max_x},{max_y})",
        f"  Stylegrounds:  {fg_count} FG, {bg_count} BG",
    ]
    return "\n".join(lines)


@mcp.tool()
def search_entities(
    map_path: str,
    entity_type: str = "",
    room_name: str = "",
    min_x: int = -999999,
    max_x: int = 999999,
    min_y: int = -999999,
    max_y: int = 999999,
) -> str:
    """Search for entities across all rooms (or a specific room) by type and/or position.

    Args:
        map_path: Path to the .bin file.
        entity_type: Filter by entity type name (substring match). Empty = all.
        room_name: Search only in this room. Empty = all rooms.
        min_x: Minimum X position filter.
        max_x: Maximum X position filter.
        min_y: Minimum Y position filter.
        max_y: Maximum Y position filter.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    results = []
    for r in rooms:
        rname = r.get("name", "?")
        if room_name and room_name not in rname:
            continue
        for child in r.get("__children", []):
            if child.get("__name") == "entities":
                for ent in child.get("__children", []):
                    etype = ent.get("__name", "")
                    if entity_type and entity_type.lower() not in etype.lower():
                        continue
                    ex, ey = ent.get("x", 0), ent.get("y", 0)
                    if ex < min_x or ex > max_x or ey < min_y or ey > max_y:
                        continue
                    results.append(
                        f"  [{rname}] {etype} id={ent.get('id', '?')} at ({ex},{ey})"
                    )

    if not results:
        return "No matching entities found."
    header = f"Found {len(results)} matching entities:"
    return "\n".join([header] + results[:200])


@mcp.tool()
def search_triggers(
    map_path: str,
    trigger_type: str = "",
    room_name: str = "",
) -> str:
    """Search for triggers across all rooms by type.

    Args:
        map_path: Path to the .bin file.
        trigger_type: Filter by trigger type (substring match). Empty = all.
        room_name: Search only in this room. Empty = all rooms.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    results = []
    for r in rooms:
        rname = r.get("name", "?")
        if room_name and room_name not in rname:
            continue
        for child in r.get("__children", []):
            if child.get("__name") == "triggers":
                for trig in child.get("__children", []):
                    ttype = trig.get("__name", "")
                    if trigger_type and trigger_type.lower() not in ttype.lower():
                        continue
                    tx, ty = trig.get("x", 0), trig.get("y", 0)
                    tw, th = trig.get("width", 0), trig.get("height", 0)
                    results.append(
                        f"  [{rname}] {ttype} id={trig.get('id', '?')} "
                        f"at ({tx},{ty}) size {tw}x{th}"
                    )

    if not results:
        return "No matching triggers found."
    header = f"Found {len(results)} matching triggers:"
    return "\n".join([header] + results[:200])


@mcp.tool()
def compare_rooms(map_path: str, room_a: str, room_b: str) -> str:
    """Compare two rooms in the same map side-by-side.

    Shows differences in size, entity counts, tile coverage, and difficulty.

    Args:
        map_path: Path to the .bin file.
        room_a: First room name.
        room_b: Second room name.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    ra = cb.get_room(data, room_a)
    rb = cb.get_room(data, room_b)
    if ra is None:
        return f"Room '{room_a}' not found."
    if rb is None:
        return f"Room '{room_b}' not found."

    da = gdep_tools.analyze_difficulty_data(ra)
    db = gdep_tools.analyze_difficulty_data(rb)

    lines = [
        f"Comparison: {room_a} vs {room_b}",
        f"{'':15} {'Room A':>12} {'Room B':>12}",
        f"{'Size':15} {ra.get('width',0)}x{ra.get('height',0):>6} {rb.get('width',0)}x{rb.get('height',0):>6}",
        f"{'Entities':15} {da['total_entities']:>12} {db['total_entities']:>12}",
        f"{'Hazards':15} {da['hazards']:>12} {db['hazards']:>12}",
        f"{'Nav aids':15} {da['nav_aids']:>12} {db['nav_aids']:>12}",
        f"{'Solid %':15} {da['solid_pct']:>12.1%} {db['solid_pct']:>12.1%}",
        f"{'Difficulty':15} {da['difficulty_score']:>12}/10 {db['difficulty_score']:>12}/10",
        f"{'Label':15} {da['difficulty_label']:>12} {db['difficulty_label']:>12}",
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP EDITING EXTENSIONS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def update_entity(
    map_path: str,
    room_name: str,
    entity_id: int,
    properties_json: str,
) -> str:
    """Update properties of an existing entity by ID.

    Args:
        map_path: Path to the .bin file.
        room_name: Room containing the entity.
        entity_id: ID of the entity to update.
        properties_json: JSON object of properties to set/update.
                         Example: '{"x": 100, "y": 50, "width": 16}'
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    try:
        props = json.loads(properties_json)
        if not isinstance(props, dict):
            return "properties_json must be a JSON object."
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            for ent in child.get("__children", []):
                if ent.get("id") == entity_id:
                    for k, v in props.items():
                        if k not in ("__name", "__children"):
                            ent[k] = v
                    cb.write_map(path, data)
                    return f"Updated entity id={entity_id} in {room_name}: {props}"

    return f"Entity id={entity_id} not found in room '{room_name}'."


@mcp.tool()
def move_entity(
    map_path: str,
    room_name: str,
    entity_id: int,
    x: int,
    y: int,
) -> str:
    """Move an entity to a new position.

    Args:
        map_path: Path to the .bin file.
        room_name: Room containing the entity.
        entity_id: ID of the entity to move.
        x: New X position.
        y: New Y position.
    """
    return update_entity(map_path, room_name, entity_id, json.dumps({"x": x, "y": y}))


@mcp.tool()
def update_room(
    map_path: str,
    room_name: str,
    properties_json: str,
) -> str:
    """Update room-level properties (music, dark, wind, etc.).

    Args:
        map_path: Path to the .bin file.
        room_name: Room name (with or without lvl_ prefix).
        properties_json: JSON object of properties to update.
                         Example: '{"dark": true, "windPattern": "Left"}'
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    try:
        props = json.loads(properties_json)
        if not isinstance(props, dict):
            return "properties_json must be a JSON object."
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    protected = {"__name", "__children", "name"}
    updated = []
    for k, v in props.items():
        if k in protected:
            continue
        room[k] = v
        updated.append(k)

    if not updated:
        return "No valid properties to update."

    cb.write_map(path, data)
    return f"Updated room '{room_name}': {', '.join(updated)}"


@mcp.tool()
def clone_room(
    map_path: str,
    source_room: str,
    new_name: str,
    x: int = -1,
    y: int = -1,
) -> str:
    """Clone an existing room to a new name and position.

    Args:
        map_path: Path to the .bin file.
        source_room: Name of the room to clone.
        new_name: Name for the cloned room.
        x: X position for the clone (-1 = place to the right of source).
        y: Y position for the clone (-1 = same Y as source).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    data = cb.read_map(path)
    room = cb.get_room(data, source_room)
    if room is None:
        return f"Room '{source_room}' not found. Available: {_room_names(data)}"

    import copy
    clone = copy.deepcopy(room)
    name = new_name if new_name.startswith("lvl_") else f"lvl_{new_name}"
    clone["name"] = name

    if x >= 0:
        clone["x"] = x
    else:
        clone["x"] = room.get("x", 0) + room.get("width", 320) + 8

    if y >= 0:
        clone["y"] = y

    # Check for duplicate
    for r in cb.get_rooms(data):
        if r.get("name") == name:
            return f"Room '{name}' already exists."

    levels = cb.find_child(data, "levels")
    levels["__children"].append(clone)
    cb.write_map(path, data)

    return f"Cloned '{source_room}' → '{name}' at ({clone['x']}, {clone['y']})"


@mcp.tool()
def batch_add_entities(
    map_path: str,
    room_name: str,
    entities_json: str,
) -> str:
    """Add multiple entities to a room in one call.

    Args:
        map_path: Path to the .bin file.
        room_name: Target room name.
        entities_json: JSON array of entity objects.
                       Each must have "__name". Optional: x, y, width, height, etc.
                       IDs are auto-assigned.
                       Example: '[{"__name":"strawberry","x":100,"y":50},
                                  {"__name":"spring","x":200,"y":160}]'
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    try:
        entities = json.loads(entities_json)
        if not isinstance(entities, list):
            return "entities_json must be a JSON array."
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    ent_el = cb.find_child(room, "entities")
    if ent_el is None:
        ent_el = {"__name": "entities", "__children": []}
        room["__children"].append(ent_el)

    next_id = _next_entity_id(room)
    added = 0

    for e in entities:
        if not isinstance(e, dict) or "__name" not in e:
            continue
        entity = {
            "__name": e["__name"],
            "__children": e.get("__children", []),
            "id": next_id,
        }
        for k, v in e.items():
            if k not in ("__name", "__children", "id"):
                entity[k] = v
        if "x" not in entity:
            entity["x"] = 0
        if "y" not in entity:
            entity["y"] = 0
        ent_el["__children"].append(entity)
        next_id += 1
        added += 1

    cb.write_map(path, data)
    return f"Added {added} entities to room '{room_name}'."


@mcp.tool()
def resize_room(
    map_path: str,
    room_name: str,
    width: int = -1,
    height: int = -1,
) -> str:
    """Resize a room (updates width/height; does NOT resize tile grids).

    Note: After resizing you may need to call set_room_tiles to adjust
    the tile grid to match the new dimensions.

    Args:
        map_path: Path to the .bin file.
        room_name: Room to resize.
        width: New width in pixels (must be multiple of 8, -1 = keep current).
        height: New height in pixels (must be multiple of 8, -1 = keep current).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    changes = []
    if width > 0:
        if width % 8 != 0:
            return f"Width must be multiple of 8 (got {width})."
        room["width"] = width
        changes.append(f"width={width}")
    if height > 0:
        if height % 8 != 0:
            return f"Height must be multiple of 8 (got {height})."
        room["height"] = height
        changes.append(f"height={height}")

    if not changes:
        return "No changes — specify width and/or height."

    cb.write_map(path, data)
    return f"Resized '{room_name}': {', '.join(changes)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  DECALS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def list_decals(map_path: str, room_name: str, layer: str = "fg") -> str:
    """List all decals in a room (foreground or background).

    Args:
        map_path: Path to the .bin file.
        room_name: Room name.
        layer: "fg" for foreground decals, "bg" for background decals.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    section_name = "fgdecals" if layer == "fg" else "bgdecals"
    decals_el = cb.find_child(room, section_name)
    if not decals_el or not decals_el.get("__children"):
        return f"No {layer} decals in room '{room_name}'."

    lines = [f"{layer.upper()} decals in {room_name} ({len(decals_el['__children'])}):\n"]
    for i, d in enumerate(decals_el["__children"]):
        texture = d.get("texture", "?")
        x, y = d.get("x", 0), d.get("y", 0)
        sx = d.get("scaleX", 1)
        sy = d.get("scaleY", 1)
        lines.append(f"  [{i}] {texture} at ({x},{y}) scale ({sx},{sy})")
    return "\n".join(lines)


@mcp.tool()
def add_decal(
    map_path: str,
    room_name: str,
    texture: str,
    x: int,
    y: int,
    layer: str = "fg",
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> str:
    """Add a decal to a room.

    Args:
        map_path: Path to the .bin file.
        room_name: Target room.
        texture: Decal texture path (e.g. "decals/1-forsakencity/flag_a00").
        x: X position.
        y: Y position.
        layer: "fg" or "bg".
        scale_x: Horizontal scale (default 1.0).
        scale_y: Vertical scale (default 1.0).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    section_name = "fgdecals" if layer == "fg" else "bgdecals"
    decals_el = cb.find_child(room, section_name)
    if decals_el is None:
        decals_el = {"__name": section_name, "__children": []}
        room["__children"].append(decals_el)

    decal = {
        "__name": "decal",
        "__children": [],
        "texture": texture,
        "x": x,
        "y": y,
        "scaleX": scale_x,
        "scaleY": scale_y,
    }
    decals_el["__children"].append(decal)
    cb.write_map(path, data)

    return f"Added {layer} decal '{texture}' at ({x},{y}) in room '{room_name}'."


@mcp.tool()
def remove_decal(
    map_path: str,
    room_name: str,
    index: int,
    layer: str = "fg",
) -> str:
    """Remove a decal by index.

    Args:
        map_path: Path to the .bin file.
        room_name: Room name.
        index: Decal index (from list_decals output).
        layer: "fg" or "bg".
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    section_name = "fgdecals" if layer == "fg" else "bgdecals"
    decals_el = cb.find_child(room, section_name)
    if not decals_el or not decals_el.get("__children"):
        return f"No {layer} decals in room '{room_name}'."

    children = decals_el["__children"]
    if index < 0 or index >= len(children):
        return f"Invalid index {index}. Range: 0-{len(children)-1}"

    removed = children.pop(index)
    cb.write_map(path, data)
    return f"Removed {layer} decal '{removed.get('texture', '?')}' (index {index}) from '{room_name}'."


# ═══════════════════════════════════════════════════════════════════════════════
#  ADVANCED ANALYSIS (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def analyze_entity_usage(map_path: str) -> str:
    """Analyze entity usage across the entire map.

    Shows total counts, unique types, and per-room breakdown of entity usage.

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    result = gdep_tools.analyze_entity_usage_data(rooms)

    lines = [
        f"Entity Usage Analysis: {path.stem}",
        f"  Total entities: {result['total_entities']}",
        f"  Unique types:   {result['unique_types']}",
        "",
        "  Type counts:",
    ]
    for etype, count in list(result["counts"].items())[:30]:
        lines.append(f"    {etype:30} {count:>4}")

    return "\n".join(lines)


@mcp.tool()
def analyze_difficulty(map_path: str, room_name: str = "") -> str:
    """Estimate room or map difficulty from hazard density, nav aids, and tile coverage.

    Args:
        map_path: Path to the .bin file.
        room_name: Specific room to analyze. Empty = analyze all rooms.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    if room_name:
        room = cb.get_room(data, room_name)
        if room is None:
            return f"Room '{room_name}' not found. Available: {_room_names(data)}"
        d = gdep_tools.analyze_difficulty_data(room)
        lines = [
            f"Difficulty Analysis: {room_name}",
            f"  Score:       {d['difficulty_score']}/10 ({d['difficulty_label']})",
            f"  Hazards:     {d['hazards']}",
            f"  Nav aids:    {d['nav_aids']}",
            f"  Entities:    {d['total_entities']}",
            f"  Solid tiles: {d['solid_pct']:.1%}",
        ]
        return "\n".join(lines)

    # All rooms
    rooms = cb.get_rooms(data)
    lines = [f"Difficulty Analysis: {path.stem} ({len(rooms)} rooms)\n"]
    lines.append(f"{'Room':25} {'Score':>6} {'Label':>12} {'Hazards':>8} {'NavAids':>8}")
    lines.append("-" * 65)
    total_score = 0
    for r in rooms:
        d = gdep_tools.analyze_difficulty_data(r)
        total_score += d["difficulty_score"]
        lines.append(
            f"{r.get('name','?'):25} {d['difficulty_score']:>6}/10 "
            f"{d['difficulty_label']:>12} {d['hazards']:>8} {d['nav_aids']:>8}"
        )
    avg = total_score / len(rooms) if rooms else 0
    lines.append(f"\n  Average difficulty: {avg:.1f}/10")
    return "\n".join(lines)


@mcp.tool()
def find_entity_references(map_path: str, entity_type: str) -> str:
    """Find all occurrences of an entity type across the map.

    Args:
        map_path: Path to the .bin file.
        entity_type: Entity type to search for (exact match).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    refs = []
    for r in rooms:
        rname = r.get("name", "?")
        for child in r.get("__children", []):
            if child.get("__name") == "entities":
                for ent in child.get("__children", []):
                    if ent.get("__name") == entity_type:
                        refs.append(
                            f"  {rname}: id={ent.get('id','?')} at ({ent.get('x',0)},{ent.get('y',0)})"
                        )

    if not refs:
        return f"No '{entity_type}' entities found in {map_path}."
    return f"Found {len(refs)} '{entity_type}' entities:\n" + "\n".join(refs)


@mcp.tool()
def detect_map_patterns(map_path: str) -> str:
    """Detect gameplay design archetypes (linear, hub, collectible-rich, etc.).

    Analyzes room layout, entity distribution, and map structure to identify
    design patterns.

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    patterns = gdep_tools.detect_map_patterns_data(rooms)

    lines = [f"Design Patterns Detected: {path.stem}", ""]
    for p in patterns:
        lines.append(f"  • {p}")
    return "\n".join(lines)


@mcp.tool()
def analyze_room_connectivity(map_path: str) -> str:
    """Analyze room adjacency graph showing isolated rooms, dead ends, and hubs.

    Rooms are considered connected if they share an edge (within 8px tolerance).

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    result = gdep_tools.analyze_room_connectivity_data(rooms)

    lines = [
        f"Room Connectivity: {path.stem}",
        f"  Rooms:       {result['total_rooms']}",
        f"  Connections: {result['total_connections']}",
        "",
    ]

    if result["hubs"]:
        lines.append(f"  Hubs (3+ connections): {', '.join(result['hubs'])}")
    if result["dead_ends"]:
        lines.append(f"  Dead ends (1 connection): {', '.join(result['dead_ends'])}")
    if result["isolated_rooms"]:
        lines.append(f"  Isolated (0 connections): {', '.join(result['isolated_rooms'])}")

    lines.append("\n  Adjacency:")
    for name, nbrs in result["adjacency"].items():
        if nbrs:
            lines.append(f"    {name} → {', '.join(nbrs)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  SUGGESTIONS (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def suggest_improvements(map_path: str, room_name: str) -> str:
    """Suggest improvements for a room based on analysis.

    Checks for missing spawns, missing floors, difficulty balance issues,
    and empty rooms.

    Args:
        map_path: Path to the .bin file.
        room_name: Room to analyze.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    suggestions = gdep_tools.suggest_improvements_data(room)

    if not suggestions:
        return f"Room '{room_name}' looks good — no suggestions."
    lines = [f"Suggestions for '{room_name}':\n"]
    for i, s in enumerate(suggestions, 1):
        lines.append(f"  {i}. {s}")
    return "\n".join(lines)


@mcp.tool()
def compare_maps(map_path_a: str, map_path_b: str) -> str:
    """Compare two maps and show structural differences.

    Args:
        map_path_a: Path to first .bin file.
        map_path_b: Path to second .bin file.
    """
    path_a = _resolve(map_path_a)
    path_b = _resolve(map_path_b)
    if not path_a.exists():
        return f"File not found: {map_path_a}"
    if not path_b.exists():
        return f"File not found: {map_path_b}"

    data_a = cb.read_map(path_a)
    data_b = cb.read_map(path_b)
    rooms_a = cb.get_rooms(data_a)
    rooms_b = cb.get_rooms(data_b)

    snap_a = gdep_tools.compute_map_snapshot(rooms_a)
    snap_b = gdep_tools.compute_map_snapshot(rooms_b)
    changes = gdep_tools.diff_snapshots(snap_a, snap_b)

    lines = [
        f"Map Comparison: {path_a.stem} vs {path_b.stem}",
        f"  Map A: {len(rooms_a)} rooms",
        f"  Map B: {len(rooms_b)} rooms",
        f"  Changes: {len(changes)}",
        "",
    ]
    if changes:
        for c in changes:
            lines.append(f"  {c}")
    else:
        lines.append("  Maps are structurally identical.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  WIKI / CACHE (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def wiki_save(key: str, content: str, tags: str = "") -> str:
    """Save analysis results or notes to the local wiki cache.

    The wiki provides persistent local storage for analysis results so
    repeated queries are instant.

    Args:
        key: Unique key for this entry (e.g. "difficulty/01_City_A").
        content: Text content to store.
        tags: Comma-separated tags for searching (e.g. "analysis,difficulty").
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    rel_path = gdep_tools.wiki_save_entry(WORKSPACE, key, content, tag_list)
    return f"Saved wiki entry '{key}' ({len(content)} chars) → {rel_path}"


@mcp.tool()
def wiki_search(query: str) -> str:
    """Search the wiki cache by key, content, or tags.

    Args:
        query: Search query (substring match in key, content, and tags).
    """
    results = gdep_tools.wiki_search_entries(WORKSPACE, query)
    if not results:
        return f"No wiki entries matching '{query}'."
    lines = [f"Found {len(results)} wiki entries matching '{query}':\n"]
    for r in results[:20]:
        preview = r.get("content", "")[:80].replace("\n", " ")
        lines.append(f"  [{r.get('key', '?')}] {preview}...")
    return "\n".join(lines)


@mcp.tool()
def wiki_list() -> str:
    """List all entries in the wiki cache."""
    entries = gdep_tools.wiki_list_entries(WORKSPACE)
    if not entries:
        return "Wiki is empty. Use wiki_save to add entries."
    lines = [f"Wiki entries ({len(entries)}):\n"]
    for e in entries:
        tags_str = f" [{', '.join(e['tags'])}]" if e.get("tags") else ""
        lines.append(f"  {e['key']}{tags_str}")
    return "\n".join(lines)


@mcp.tool()
def wiki_get(key: str) -> str:
    """Retrieve a specific wiki entry by key.

    Args:
        key: The entry key to look up.
    """
    entry = gdep_tools.wiki_get_entry(WORKSPACE, key)
    if entry is None:
        return f"Wiki entry '{key}' not found. Use wiki_list to see available entries."
    lines = [
        f"Wiki: {entry.get('key', key)}",
        f"Tags: {', '.join(entry.get('tags', []))}",
        "",
        entry.get("content", ""),
    ]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  MOD PROJECT
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_mod_info() -> str:
    """Get information about the current mod project.

    Reads everest.yaml/everest.yml if present, lists map count,
    and shows workspace structure.
    """
    lines = [f"Mod Project: {WORKSPACE.name}", f"  Path: {WORKSPACE}", ""]

    # Check for everest.yaml
    for fname in ("everest.yaml", "everest.yml"):
        meta_path = WORKSPACE / fname
        if meta_path.exists():
            lines.append(f"  Everest metadata: {fname}")
            content = meta_path.read_text(encoding="utf-8")
            for line in content.strip().split("\n")[:10]:
                lines.append(f"    {line}")
            lines.append("")
            break
    else:
        lines.append("  No everest.yaml found")
        lines.append("")

    # Count maps
    maps_dir = WORKSPACE / "Maps"
    if maps_dir.exists():
        bins = list(maps_dir.rglob("*.bin"))
        lines.append(f"  Maps: {len(bins)} .bin files")
        for b in bins[:15]:
            lines.append(f"    {b.relative_to(WORKSPACE)}")
        if len(bins) > 15:
            lines.append(f"    ... and {len(bins) - 15} more")
    else:
        lines.append("  Maps: no Maps/ directory")

    # PCG library
    pcg_lib = WORKSPACE / "PCG" / "patterns.json"
    if pcg_lib.exists():
        try:
            lib = json.loads(pcg_lib.read_text(encoding="utf-8"))
            lines.append(f"\n  PCG pattern library: {len(lib.get('patterns', []))} patterns")
        except (json.JSONDecodeError, OSError):
            pass

    # Wiki
    wiki_dir = WORKSPACE / gdep_tools.WIKI_DIR_NAME
    if wiki_dir.exists():
        entries = list(wiki_dir.glob("*.json"))
        lines.append(f"  Wiki cache: {len(entries)} entries")

    return "\n".join(lines)


@mcp.tool()
def validate_map(map_path: str, auto_fix: bool = False) -> str:
    """Validate an entire map for playability issues.

    Checks all rooms for common problems: missing spawns, no floor tiles,
    entities out of bounds, invalid dimensions.

    Args:
        map_path: Path to the .bin file.
        auto_fix: If True, automatically fix issues where possible
                  (adds spawns, floor tiles, clamps entities).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    total_issues = 0
    total_fixes = 0
    lines = [f"Map Validation: {path.stem} ({len(rooms)} rooms)\n"]

    for r in rooms:
        result = gdep_tools.validate_and_fix_room(r, auto_fix=auto_fix)
        if result["issues"]:
            total_issues += len(result["issues"])
            total_fixes += len(result["fixes_applied"])
            lines.append(f"  {r.get('name', '?')}:")
            for issue in result["issues"]:
                lines.append(f"    ✗ {issue}")
            for fix in result["fixes_applied"]:
                lines.append(f"    ✓ Fixed: {fix}")

    if auto_fix and total_fixes > 0:
        cb.write_map(path, data)

    lines.insert(1, f"  Issues: {total_issues}")
    if auto_fix:
        lines.insert(2, f"  Auto-fixed: {total_fixes}")
    if total_issues == 0:
        lines.append("  All rooms PASS ✓")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  CATALOG EXTENSIONS
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def get_trigger_definition(trigger_name: str) -> str:
    """Read the source of a Lönn trigger definition .lua file.

    Args:
        trigger_name: Name of the trigger (searches in Loenn/triggers/).
    """
    # Search in common Lönn directories
    search_dirs = [
        WORKSPACE / "Loenn" / "triggers",
        WORKSPACE / "loenn" / "triggers",
        WORKSPACE / "Triggers",
    ]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for lua in search_dir.rglob("*.lua"):
            if trigger_name.lower() in lua.stem.lower():
                content = lua.read_text(encoding="utf-8", errors="replace")
                rel = lua.relative_to(WORKSPACE)
                return f"-- {rel}\n\n{content}"

    return (
        f"Trigger definition '{trigger_name}' not found.\n"
        f"Searched: {', '.join(str(d.relative_to(WORKSPACE)) for d in search_dirs if d.exists())}"
    )


@mcp.tool()
def get_effect_definition(effect_name: str) -> str:
    """Read the source of a Lönn effect definition .lua file.

    Args:
        effect_name: Name of the effect (searches in Loenn/effects/).
    """
    search_dirs = [
        WORKSPACE / "Loenn" / "effects",
        WORKSPACE / "loenn" / "effects",
        WORKSPACE / "Effects",
    ]
    for search_dir in search_dirs:
        if not search_dir.exists():
            continue
        for lua in search_dir.rglob("*.lua"):
            if effect_name.lower() in lua.stem.lower():
                content = lua.read_text(encoding="utf-8", errors="replace")
                rel = lua.relative_to(WORKSPACE)
                return f"-- {rel}\n\n{content}"

    return (
        f"Effect definition '{effect_name}' not found.\n"
        f"Searched: {', '.join(str(d.relative_to(WORKSPACE)) for d in search_dirs if d.exists())}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  IMPORT / EXPORT
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def export_room_json(map_path: str, room_name: str, output_path: str = "") -> str:
    """Export a room as a JSON file for external editing or sharing.

    Args:
        map_path: Path to the .bin file.
        room_name: Room to export.
        output_path: Output JSON path (default: auto-generated).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    if not output_path.strip():
        safe_name = room.get("name", "room").replace("/", "_")
        output_path = f"Export/{safe_name}.json"
    out_file = _resolve(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    out_file.write_text(
        json.dumps(room, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    rel = out_file.relative_to(WORKSPACE)
    return f"Exported room '{room_name}' → {rel}"


@mcp.tool()
def import_room_json(
    map_path: str,
    json_path: str,
    new_name: str = "",
    x: int = 0,
    y: int = 0,
) -> str:
    """Import a room from a JSON file into a map.

    Args:
        map_path: Path to the .bin file to import into.
        json_path: Path to the JSON file (exported by export_room_json).
        new_name: Optional new name for the room (overrides name in JSON).
        x: X position override (-1 = use position from JSON).
        y: Y position override (-1 = use position from JSON).
    """
    map_file = _resolve(map_path)
    if not map_file.exists():
        return f"Map file not found: {map_path}"

    json_file = _resolve(json_path)
    if not json_file.exists():
        return f"JSON file not found: {json_path}"

    try:
        room = json.loads(json_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"

    if not isinstance(room, dict) or "__name" not in room:
        return "Invalid room JSON: missing __name field."

    data = cb.read_map(map_file)
    levels = cb.find_child(data, "levels")
    if levels is None:
        return "Invalid map: no 'levels' element."

    if new_name:
        name = new_name if new_name.startswith("lvl_") else f"lvl_{new_name}"
        room["name"] = name

    if x != 0:
        room["x"] = x
    if y != 0:
        room["y"] = y

    # Check duplicate
    for r in cb.get_rooms(data):
        if r.get("name") == room.get("name"):
            return f"Room '{room.get('name')}' already exists in map."

    levels["__children"].append(room)
    cb.write_map(map_file, data)
    return f"Imported room '{room.get('name')}' into {map_path} at ({room.get('x',0)},{room.get('y',0)})"


# ═══════════════════════════════════════════════════════════════════════════════
#  DIFF & FIX (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@mcp.tool()
def summarize_map_diff(map_path: str, snapshot_key: str = "") -> str:
    """Create or compare a structural snapshot of the map.

    First call (or with new snapshot_key): saves current state to wiki.
    Subsequent call with same key: shows what changed since the snapshot.

    Args:
        map_path: Path to the .bin file.
        snapshot_key: Key to identify this snapshot (default: map filename).
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)

    current_snap = gdep_tools.compute_map_snapshot(rooms)
    if not snapshot_key:
        snapshot_key = f"snapshot/{path.stem}"

    # Check if previous snapshot exists
    prev_entry = gdep_tools.wiki_get_entry(WORKSPACE, snapshot_key)

    # Save current snapshot
    gdep_tools.wiki_save_entry(
        WORKSPACE, snapshot_key,
        json.dumps(current_snap, ensure_ascii=False),
        tags=["snapshot", "diff"],
    )

    if prev_entry is None:
        return (
            f"Snapshot saved: '{snapshot_key}' ({len(rooms)} rooms)\n"
            "Call again later to see what changed."
        )

    # Compare
    try:
        old_snap = json.loads(prev_entry.get("content", "{}"))
    except json.JSONDecodeError:
        return "Previous snapshot was corrupted. New snapshot saved."

    changes = gdep_tools.diff_snapshots(old_snap, current_snap)

    if not changes:
        return f"No structural changes since last snapshot ('{snapshot_key}')."

    lines = [f"Changes since last snapshot '{snapshot_key}':", ""]
    for c in changes:
        lines.append(f"  {c}")
    lines.append(f"\n(New snapshot saved. {len(changes)} change(s) detected.)")
    return "\n".join(lines)


@mcp.tool()
def batch_validate_and_fix(map_path: str, auto_fix: bool = False) -> str:
    """Validate all rooms in a map and optionally auto-fix issues.

    Checks for: missing spawns, no floors, entities out of bounds,
    invalid dimensions. Auto-fix adds spawns, floor tiles, and clamps
    entities to room bounds.

    Args:
        map_path: Path to the .bin file.
        auto_fix: If True, apply automatic fixes.
    """
    return validate_map(map_path, auto_fix=auto_fix)


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    mcp.run()


if __name__ == "__main__":
    main()
