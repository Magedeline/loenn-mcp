"""
Lönn MCP Server — Celeste Map Editor for AI Agents

Provides tools for reading, editing, analyzing, and generating Celeste map
files (.bin) directly from VS Code via the Model Context Protocol.

Tools (categories):
  Map Reading:      list_maps, read_map_overview, read_room, get_room_tiles
  Map Reading Ext:  read_map_metadata, search_entities, search_triggers, compare_rooms
  Map Editing:      add_entity, remove_entity, add_trigger, remove_trigger,
                    set_room_tiles, add_room, remove_room, create_map
  Map Editing Ext:  update_entity, move_entity, update_room, clone_room,
                    batch_add_entities, resize_room
  Room Settings:    get_room_settings, update_room_settings,
                    rename_room, move_room, resize_room
  Map Metadata:     get_map_metadata, set_map_metadata
  Audio Events:     replace_audio_event
  Dependencies:     get_map_dependencies, set_map_dependencies
  Decals:           list_decals, add_decal, remove_decal
  Stylegrounds:     list_stylegrounds, add_styleground, remove_styleground,
                    update_styleground
  Entity Catalog:   list_entity_definitions, get_entity_definition,
                    list_trigger_definitions, list_effect_definitions
  Catalog Ext:      get_trigger_definition, get_effect_definition
  Analysis:         analyze_map, visualize_map_layout
  Advanced Analysis:analyze_entity_usage, analyze_difficulty, find_entity_references,
                    detect_map_patterns, analyze_room_connectivity
  Asset Analysis:   analyze_map_assets  (ML + Maddie480 graphics dump)
  Suggestions:      suggest_improvements, compare_maps
  Wiki/Cache:       wiki_save, wiki_search, wiki_list, wiki_get
  Mod Project:      get_mod_info, validate_map
  Import/Export:    export_room_json, import_room_json
  Diff & Fix:       summarize_map_diff, batch_validate_and_fix
  Generation:       build_pattern_library, generate_room_from_pattern,
                    validate_room, ingest_external_map
  Image/Terrain:    generate_map_from_image, generate_terrain_map,
                    preview_terrain_biomes
  Safety:           map_copy, map_commit, map_backups, map_restore, map_diff
  Editors:          editor_open, editor_wait, editor_status, editor_close
  Assets:           check_assets, check_sprites_xml
  Sprite Fusion:    sf_make_tileset, sf_import

All tools are deterministic/procedural — no AI models, no network calls to
LLM APIs. (The former ai_* tools were removed in v7.0.0.)

Usage:
  python server.py                         (uses cwd as workspace)
  LOENN_MCP_WORKSPACE=/path python server.py  (explicit workspace)
"""

import difflib
import fnmatch
import functools
import inspect
import io
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

from fastmcp import FastMCP

try:
    from . import celeste_bin as cb          # installed package
    from . import map_json                   # installed package
    from . import authoring                  # installed package
    from . import lua_defs                   # installed package
    from . import pcg                        # installed package
    from . import image_map                  # installed package
    from . import terrain_gen                # installed package
    from . import gdep_tools                 # installed package
    from . import safety, mapdiff, assets, sprite_fusion, editors
except ImportError:
    import celeste_bin as cb                 # run directly from source
    import map_json                          # run directly from source
    import authoring                         # run directly from source
    import lua_defs                          # run directly from source
    import pcg                               # run directly from source
    import image_map                         # run directly from source
    import terrain_gen                       # run directly from source
    import gdep_tools                        # run directly from source
    import safety, mapdiff, assets, sprite_fusion, editors

WORKSPACE = Path(os.environ.get("LOENN_MCP_WORKSPACE", ".")).resolve()

mcp = FastMCP(
    "loenn-mcp",
    instructions=(
        "Reads and writes Celeste .bin maps directly (lossless), no editor needed. "
        "Paths are relative to the mod folder (LOENN_MCP_WORKSPACE). "
        "Read: list_maps, read_map_overview, read_room, get_room_tiles, search_entities, list_decals. "
        "Edit: add/update/move/remove entity and trigger, set_room_tiles, add/remove/move/resize/clone room, "
        "merge_json_into_map for many additions at once (dry run first). "
        "Safety: every write keeps a timestamped backup (map_backups, map_restore). For chapter maps, "
        "map_copy, edit the copy, map_diff, then map_commit. "
        "Check: validate_map, check_assets (names and textures exist), check_sprites_xml. "
        "Editor: editor_open opens a map (and room) in Lönn, editor_wait returns when the user saved; "
        "writes to a map that is open in an editor are refused. "
        "Gray-box rooms painted in Sprite Fusion come in with sf_import (tileset from sf_make_tileset). "
        "Game: game_teleport, game_reload_map, game_play_tas need Celeste running with Everest debug mode."
    ),
)


# ─── Tool registration, write reporting and safe saves ────────────────────────

# Tools hidden unless LOENN_MCP_TOOLS=all: analysis extras and older generators
# that crowd the list for an agent doing map edits.
_EXTRA_TOOLS = {
    "wiki_save", "wiki_search", "wiki_list", "wiki_get", "generate_terrain_map", "preview_terrain_biomes",
    "generate_map_from_image", "ingest_external_map", "build_pattern_library", "generate_room_from_pattern",
    "analyze_map_assets", "detect_map_patterns", "suggest_improvements", "summarize_map_diff", "compare_maps",
    "analyze_entity_usage", "find_entity_references", "visualize_map_layout",
}
_DESC_LIMIT = 400


def _short_doc(doc: str | None) -> str | None:
    """Keep the first paragraph (capped) and the Args section, which FastMCP turns into parameter docs."""
    if not doc:
        return doc
    text = inspect.cleandoc(doc)
    head, _, rest = text.partition("\n\n")
    head = " ".join(head.split())
    if len(head) > _DESC_LIMIT:
        head = head[:_DESC_LIMIT].rsplit(" ", 1)[0] + " ..."
    m = re.search(r"^(Args|Arguments|Parameters):\s*$.*", text, re.M | re.S)
    return head + ("\n\n" + m.group(0) if m else "")


def tool(*dargs, **dkw):
    """mcp.tool() plus: short descriptions, guard refusals as clear errors, and a
    "[saved ...; backup ...]" line for every map written during the call."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            safety.begin_call()
            try:
                result = fn(*args, **kwargs)
            except safety.WriteBlocked as exc:
                safety.end_call()
                return f"Not written: {exc}"
            except editors.EditorError as exc:
                safety.end_call()
                return f"Editor error: {exc}"
            records = safety.end_call()
            if records and isinstance(result, str):
                result += "\n" + safety.describe_records(records, WORKSPACE)
            return result
        wrapper.__doc__ = _short_doc(fn.__doc__)
        mcp.tool(*dargs, **dkw)(wrapper)
        return wrapper
    return deco


def _save(path, data: dict) -> "safety.WriteRecord":
    """Write an element tree: guards, timestamped backup, validate, atomic replace."""
    return safety.save_map(Path(path), data, WORKSPACE)


def _save_bytes(path, payload: bytes, header: str = "CELESTE MAP") -> "safety.WriteRecord":
    return safety.save_bytes(Path(path), payload, WORKSPACE, header)


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

@tool()
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


@tool()
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


@tool()
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


@tool()
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

@tool()
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
    _save(path, data)

    return (
        f"Added '{entity_name}' (id={entity_id}) at ({x},{y}) "
        f"to room '{room_name}'."
    )


@tool()
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

    _save(path, data)
    return f"Removed entity id={entity_id} from '{room_name}'."


@tool()
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
    _save(path, data)

    suffix = f" with {len(node_children)} node(s)" if node_children else ""
    return (
        f"Added trigger '{trigger_name}' (id={trigger_id}) at "
        f"({x},{y}) {width}x{height} to room '{room_name}'{suffix}."
    )


@tool()
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

    _save(path, data)
    return f"Removed trigger id={trigger_id} from '{room_name}'."


@tool()
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
    _save(path, data)

    row_count = len(tiles.strip().split("\n"))
    return f"Set {layer} tiles for '{room_name}': {row_count} rows."


@tool()
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
    _save(path, data)

    return f"Added room '{name}' at ({x},{y}) {width}x{height}."


@tool()
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

    _save(path, data)
    return f"Removed room '{room_name}'."


@tool()
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
    _save(path, data)
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


@tool()
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


@tool()
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

    _save(path, data)

    layer_label = layer_el.get("__name", layer)
    return (
        f"Added styleground '{effect_name}' to {layer_label} at index {pos}."
    )


@tool()
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
    _save(path, data)
    return (
        f"Removed styleground '{removed.get('__name', '?')}' "
        f"at index {index} from {layer_el.get('__name', layer)}."
    )


@tool()
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

    _save(path, data)

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

@tool()
def list_entity_definitions(filter_text: str = "") -> str:
    """List available Lönn entity definitions from the project.

    Args:
        filter_text: Optional text to filter filenames
    """
    ent_dir = WORKSPACE / "Loenn" / "entities"
    if not ent_dir.exists():
        return "No Loenn/entities/ directory found."

    by_file: dict[Path, list[str]] = {}
    for name, f in _definition_index("entities").items():
        by_file.setdefault(f, []).append(name)
    files = sorted(ent_dir.rglob("*.lua"))
    if filter_text:
        fl = filter_text.lower()
        files = [f for f in files
                 if fl in f.stem.lower() or any(fl in n.lower() for n in by_file.get(f, []))]

    if not files:
        return "No matching entity definitions."

    lines = [f"Entity definitions ({len(files)} files):"]
    for f in files:
        names = by_file.get(f) or [f.stem]
        lines.append(f"  {f.relative_to(ent_dir).with_suffix('')}  →  {', '.join(names)}")
    return "\n".join(lines)


@tool()
def get_entity_definition(entity_file: str) -> str:
    """Read a Lönn entity Lua definition and extract its properties.

    Args:
        entity_file: Entity name as placed in maps (e.g. "CEL/KrackoBoss") or a
            filename (e.g. "kirbyBoss" or "kirbyBoss.lua")
    """
    ent_dir = (WORKSPACE / "Loenn" / "entities").resolve()
    by_name = _definition_index("entities").get(entity_file)
    if by_name is not None:
        entity_file = str(by_name.relative_to(ent_dir))
    elif "/" in entity_file and not entity_file.endswith(".lua"):
        return (f"No Lönn definition declares the entity '{entity_file}'. "
                "Use find_missing_definitions or audit_plugin_coverage to see what is undefined.")
    if not entity_file.endswith(".lua"):
        entity_file += ".lua"

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


@tool()
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


@tool()
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


# ─── Definition coverage: which placed / coded names have a Lönn plugin ──────

# A namespaced object name declared by a Lönn plugin: `.name = "CEL/Foo"` or
# `name = "CEL/Foo"` inside a table. Vanilla objects have no namespace slash,
# so only namespaced names are checked.
_DEF_NAME_RE = re.compile(r"""(?:\.name|\bname)\s*=\s*["']([^"'\s]+/[^"'\s]+)["']""")
_CUSTOM_ENTITY_RE = re.compile(r"\[\s*(?:Celeste\.Mod\.Entities\.)?CustomEntity\s*\(([^)]*)\)")
_installed_names_cache: dict[str, set[str]] = {}


def _definition_index(kind: str) -> dict[str, Path]:
    """Map every namespaced name declared under Loenn/<kind>/ to the file declaring it."""
    base = WORKSPACE / "Loenn" / kind
    index: dict[str, Path] = {}
    if base.exists():
        for f in sorted(base.rglob("*.lua")):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name in _DEF_NAME_RE.findall(text):
                index.setdefault(name, f)
    return index


def _installed_definition_names() -> set[str]:
    """Namespaced names declared by the Lönn plugins of every other installed mod.

    Only used when the workspace sits in a Celeste `Mods/` folder; scans sibling
    mod folders and zips. Cached for the life of the server.
    """
    mods = WORKSPACE.parent
    key = str(mods)
    if key in _installed_names_cache:
        return _installed_names_cache[key]
    names: set[str] = set()
    if mods.name.lower() == "mods":
        for entry in mods.iterdir():
            if entry.resolve() == WORKSPACE:
                continue
            try:
                if entry.is_dir() and (entry / "Loenn").is_dir():
                    for f in (entry / "Loenn").rglob("*.lua"):
                        names.update(_DEF_NAME_RE.findall(f.read_text(encoding="utf-8", errors="replace")))
                elif entry.suffix.lower() == ".zip":
                    with zipfile.ZipFile(entry) as z:
                        for n in z.namelist():
                            if n.lower().startswith("loenn/") and n.endswith(".lua"):
                                names.update(_DEF_NAME_RE.findall(z.read(n).decode("utf-8", "replace")))
            except (OSError, zipfile.BadZipFile):
                continue
    _installed_names_cache[key] = names
    return names


def _compiled_sources(source_dir: Path) -> tuple[list[Path], str]:
    """The .cs files a .csproj actually compiles, and a note on how that was decided.

    Projects with an explicit `<Compile Include=...>` list (default compile
    items disabled) build exactly those files; otherwise every .cs file builds
    except folders listed in `<Compile Remove=...>`.
    """
    includes: list[str] = []
    removes: list[str] = []
    explicit = False
    for proj in source_dir.glob("*.csproj"):
        text = proj.read_text(encoding="utf-8", errors="replace")
        includes += re.findall(r'<Compile\s+Include="([^"]+)"', text)
        removes += re.findall(r'<Compile\s+Remove="([^"]+)"', text)
        explicit |= bool(re.search(r"<EnableDefaultCompileItems>\s*false", text, re.I))
    norm = lambda p: p.replace("\\", "/").lower()
    every = sorted(source_dir.rglob("*.cs"))
    if explicit and includes:
        pats = [norm(i) for i in includes]
        chosen = [f for f in every
                  if any(fnmatch.fnmatch(norm(str(f.relative_to(source_dir))), p) for p in pats)]
        return chosen, f"{len(chosen)} files from the .csproj Include list"
    prefixes = [norm(r).split("*")[0].rstrip("/") + "/" for r in removes if norm(r).split("*")[0]]
    chosen = [f for f in every
              if not any(norm(str(f.relative_to(source_dir))).startswith(p) for p in prefixes)]
    return chosen, f"{len(chosen)} files, {len(prefixes)} removed folder(s)"


@tool()
def find_missing_definitions(map_path: str) -> str:
    """List namespaced entities and triggers in a map that no installed Lönn plugin defines.

    Lönn shows such objects as placeholders and cannot edit their fields; they
    are often stale IDs from renamed helpers. Checks the workspace's Loenn/
    folder and, when the workspace is inside Celeste/Mods, every other
    installed mod's plugins. Vanilla (un-namespaced) names are not checked.

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)
    defined = set(_definition_index("entities")) | set(_definition_index("triggers"))
    defined |= _installed_definition_names()

    missing: dict[str, list[str]] = {}
    total = 0
    for room in cb.get_rooms(data):
        rname = _short_room(room.get("name", "?"))
        for group in ("entities", "triggers"):
            node = cb.find_child(room, group)
            for obj in (node or {}).get("__children", []):
                name = obj.get("__name", "")
                if "/" not in name:
                    continue
                total += 1
                if name not in defined:
                    missing.setdefault(name, []).append(rname)

    lines = [f"Lönn definition check: {path.stem}",
             f"  Namespaced objects placed: {total}",
             f"  Names with no Lönn plugin: {len(missing)}"]
    for name, rooms in sorted(missing.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        shown = ", ".join(sorted(set(rooms))[:6]) + (" …" if len(set(rooms)) > 6 else "")
        lines.append(f"    {name}  ×{len(rooms)}  in {shown}")
    if not missing:
        lines.append("  Every namespaced object has a Lönn definition.")
    return "\n".join(lines)


@tool()
def audit_plugin_coverage(source_dir: str = "Source", prefix: str = "") -> str:
    """Compare the mod's C# [CustomEntity] names with its Lönn plugin definitions.

    Reports names registered in code that have no Lönn plugin (they cannot be
    placed from Lönn) and Lönn definitions with no C# registration in the
    workspace (placing them does nothing unless another mod provides them).
    Folders removed from compilation in the .csproj are skipped.

    Args:
        source_dir: C# source folder relative to the workspace (default "Source").
        prefix: Only report names starting with this (e.g. "CEL/").
    """
    src = _resolve(source_dir)
    if not src.is_dir():
        return f"Source folder not found: {source_dir}"
    files, how = _compiled_sources(src)
    coded: dict[str, str] = {}
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for args in _CUSTOM_ENTITY_RE.findall(text):
            for name in re.findall(r'"([^"]+)"', args):
                name = name.split("=")[0].strip()
                if "/" in name:
                    coded.setdefault(name, f.relative_to(src).as_posix())
    plugins = set(_definition_index("entities")) | set(_definition_index("triggers"))
    if prefix:
        coded = {k: v for k, v in coded.items() if k.startswith(prefix)}
        plugins = {p for p in plugins if p.startswith(prefix)}

    no_plugin = sorted(set(coded) - plugins)
    no_code = sorted(plugins - set(coded) - _installed_definition_names())
    lines = [f"Plugin coverage ({source_dir}: {how})",
             f"  Registered in C#: {len(coded)}   Lönn definitions: {len(plugins)}",
             f"\n  In C# but no Lönn plugin ({len(no_plugin)}):"]
    lines += [f"    {n}   ({coded[n]})" for n in no_plugin] or ["    none"]
    lines.append(f"\n  Lönn plugin but no C# registration ({len(no_code)}):")
    lines += [f"    {n}" for n in no_code] or ["    none"]
    return "\n".join(lines)


def _short_room(name: str) -> str:
    """Room name as the game sees it: Celeste strips the 'lvl_' prefix at load."""
    return name[4:] if name.startswith("lvl_") else name


def _game_root() -> Path | None:
    """Walk up from the workspace to the Celeste install (the folder holding Content/)."""
    for p in [WORKSPACE, *WORKSPACE.parents]:
        if (p / "Content" / "FMOD").is_dir():
            return p
    return None


@tool()
def validate_audio(map_path: str) -> str:
    """Check every audio event a map references against the sound banks.

    Looks at the map metadata (music, ambience, cassette song, postcard sound),
    each room's music/alt music/ambience and every entity or trigger attribute
    holding an `event:/` path. Known events come from the workspace's GUIDs.txt
    and the game's vanilla FMOD strings banks. A missing event plays nothing in
    game. Fix with replace_audio_event.

    Args:
        map_path: Path to the .bin file.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    known: set[str] = set()
    guids = WORKSPACE / "GUIDs.txt"
    if guids.exists():
        known.update(re.findall(r"(event:/\S+)", guids.read_text(encoding="utf-8", errors="replace")))
    root = _game_root()
    if root:
        for bank in (root / "Content" / "FMOD" / "Desktop").glob("*.strings.bank"):
            known.update(m.decode("utf-8", "replace")
                         for m in re.findall(rb"event:/[\x21-\x7e]+?(?=\x00)", bank.read_bytes()))
    if not known:
        return "No sound bank list found (no GUIDs.txt in the workspace and no game Content/FMOD folder)."

    def ok(event: str, key: str) -> bool:
        if key == "PostcardSoundID":  # the game appends _in / _out
            return event + "_in" in known
        return event in known

    refs: list[tuple[str, str, str]] = []  # (event, where, key)
    data = cb.read_map(path)
    meta = cb.find_child(data, "meta")

    def walk_meta(el: dict, where: str):
        for k, v in el.items():
            if isinstance(v, str) and v.startswith("event:/"):
                refs.append((v, f"meta{where}.{k}", k))
        for ch in el.get("__children", []):
            walk_meta(ch, f"{where}/{ch.get('__name', '?')}")

    if meta:
        walk_meta(meta, "")
    for room in cb.get_rooms(data):
        rname = _short_room(room.get("name", "?"))
        for k in ("music", "alt_music", "ambience"):
            v = room.get(k, "")
            if isinstance(v, str) and v.startswith("event:/"):
                refs.append((v, f"{rname}.{k}", k))
        for group in ("entities", "triggers"):
            node = cb.find_child(room, group)
            for obj in (node or {}).get("__children", []):
                for k, v in obj.items():
                    if isinstance(v, str) and v.startswith("event:/"):
                        refs.append((v, f"{rname}:{obj.get('__name', '?')}.{k}", k))

    bad: dict[str, list[str]] = {}
    for event, where, key in refs:
        if not ok(event, key):
            bad.setdefault(event, []).append(where)
    lines = [f"Audio check: {path.stem}",
             f"  Event references: {len(refs)}   Missing from the banks: {len(bad)}",
             f"  Known events: {len(known)} (GUIDs.txt{' + vanilla banks' if root else ''})"]
    tails = {e.split("/", 2)[-1].lower(): e for e in known}
    for event, wheres in sorted(bad.items()):
        lines.append(f"\n  ✗ {event}")
        lines.append(f"      used at: {', '.join(wheres[:5])}{' …' if len(wheres) > 5 else ''}")
        tail = event.split("/", 2)[-1].lower()
        close = difflib.get_close_matches(tail, list(tails), n=2, cutoff=0.6)
        if close:
            lines.append(f"      closest existing: {', '.join(tails[c] for c in close)}")
    if not bad:
        lines.append("  Every referenced event exists.")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSIS TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool()
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


@tool()
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


@tool()
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



# ─── Map preview detail: tiles and entity markers per room ────────────────────

_HAZARD_HINTS = ("spike", "spinner", "lightning", "killbox", "seeker", "fireball", "laser", "dust")
_COLLECT_HINTS = ("strawberry", "berry", "heart", "cassette", "gem", "key", "tape")


def _tile_runs_path(text: str, ox: float, oy: float) -> str:
    """SVG path covering every non-empty tile, merged into horizontal runs."""
    parts = []
    for ty, row in enumerate(text.split("\n")):
        tx = 0
        n = len(row)
        while tx < n:
            if row[tx] in "0 ":
                tx += 1
                continue
            start = tx
            while tx < n and row[tx] not in "0 ":
                tx += 1
            parts.append(f"M{ox + start * 8:g} {oy + ty * 8:g}h{(tx - start) * 8}v8h-{(tx - start) * 8}z")
    return "".join(parts)


def _entity_color(name: str) -> str:
    low = name.lower()
    if name == "player":
        return "#5dde6a"
    if "refill" in low:
        return "#a8f0a0"
    if any(h in low for h in _HAZARD_HINTS):
        return "#ff5a5a"
    if any(h in low for h in _COLLECT_HINTS):
        return "#ffd34d"
    return "#c9c9d6"


def _room_detail_svg(room: dict, rx: float, ry: float) -> str:
    """Background tiles, solid tiles and entity markers for one room, in room coordinates."""
    out = []
    bg = cb.find_child(room, "bg")
    if bg and bg.get("innerText"):
        out.append(f'<path d="{_tile_runs_path(bg["innerText"], rx, ry)}" fill="#3a3a52" pointer-events="none"/>')
    solids = cb.find_child(room, "solids")
    if solids and solids.get("innerText"):
        out.append(f'<path d="{_tile_runs_path(solids["innerText"], rx, ry)}" fill="#cfd6ea" pointer-events="none"/>')
    ents = cb.find_child(room, "entities")
    marks = []
    for e in (ents or {}).get("__children", []):
        name = e.get("__name", "")
        try:
            ex, ey = float(e.get("x", 0)), float(e.get("y", 0))
            w, h = float(e.get("width", 0) or 0), float(e.get("height", 0) or 0)
        except (TypeError, ValueError):
            continue
        color = _entity_color(name)
        title = _html_escape(name)
        if name == "cloud":  # 32x5 platform anchored at its top centre
            ex, w, h = ex - 16, 32.0, 5.0
            color = "#f2f2ff"
        if w or h:
            ww, hh = max(w, 2), max(h, 2)
            if name.startswith("spikesUp"):
                ey, hh = ey - 3, 3
            elif name.startswith("spikesDown"):
                hh = 3
            elif name.startswith("spikesLeft"):
                ex, ww = ex - 3, 3
            elif name.startswith("spikesRight"):
                ww = 3
            marks.append(f'<rect x="{rx + ex:g}" y="{ry + ey:g}" width="{ww:g}" height="{hh:g}" fill="{color}" '
                         f'fill-opacity="0.85"><title>{title}</title></rect>')
        else:
            marks.append(f'<circle cx="{rx + ex:g}" cy="{ry + ey:g}" r="3.5" fill="{color}" stroke="#111" '
                         f'stroke-width="0.8"><title>{title}</title></circle>')
    if marks:
        out.append('<g class="em">' + "".join(marks) + "</g>")
    return "".join(out)


def _html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _overlay_svg(added: list[dict], rx: float, ry: float) -> str:
    """Rings around objects a planned merge would add (red when a check failed)."""
    out = []
    for a in added:
        bad = bool(a.get("problems"))
        color = "#ff3b3b" if bad else "#ff4dff"
        kind = "trigger" if a["kind"] == "triggers" else "entity"
        tip = _html_escape(f"NEW {kind} {a['name']} id={a['id']} at ({a['x']:g},{a['y']:g})"
                           + "".join(f"\n✗ {p}" for p in a.get("problems", [])))
        if a.get("width") or a.get("height"):
            out.append(f'<rect class="ov" x="{rx + a["x"]:g}" y="{ry + a["y"]:g}" width="{a["width"] or 4:g}" '
                       f'height="{a["height"] or 4:g}" fill="{color}" fill-opacity="0.18" stroke="{color}" '
                       f'stroke-width="2" stroke-dasharray="4 2"><title>{tip}</title></rect>')
        else:
            out.append(f'<circle class="ov" cx="{rx + a["x"]:g}" cy="{ry + a["y"]:g}" r="9" fill="none" '
                       f'stroke="{color}" stroke-width="2.5"><title>{tip}</title></circle>'
                       f'<circle cx="{rx + a["x"]:g}" cy="{ry + a["y"]:g}" r="3" fill="{color}" '
                       f'pointer-events="none"/>')
    return "".join(out)


def _load_map_any(path: Path) -> dict:
    """Element tree from a .bin map or a typed JSON dump (map_to_json)."""
    if path.suffix.lower() == ".json":
        return map_json.json_to_tree(json.loads(path.read_text(encoding="utf-8")))
    return cb.read_map(path)


def _room_matches(name: str, prefix: str) -> bool:
    """Prefix match on the room name with or without its 'lvl_' prefix."""
    short = name[4:] if name.startswith("lvl_") else name
    p = prefix[4:] if prefix.startswith("lvl_") else prefix
    return short.startswith(p)


@tool()
def render_map_html(
    map_path: str,
    prefix: str = "",
    output_file: str = "",
    detail: bool = True,
    overlay: str = "",
) -> str:
    """Render an interactive HTML map preview with zoom, pan, search, and detail panel.

    Features: zoom/pan (wheel, drag, pinch), hover tooltips, click-to-select room
    detail panel (entity/trigger breakdown), live search filter, minimap with
    viewport indicator, toggleable grid and labels, keyboard shortcuts.

    Args:
        map_path: Path to a .bin file, or to a typed JSON dump made by map_to_json
        prefix: Only show rooms matching this prefix (e.g. "g-", "w-"); works
            whether or not the map's room names carry 'lvl_'
        output_file: Output HTML filename (default: map_preview.html in Temp/)
        detail: Draw each room's solid/background tiles and entity markers
            (hazards red, collectibles gold, spawns green, refills light green).
            False draws room boxes only.
        overlay: Authoring JSON (inline, or a path to a .json file) to preview
            as a merge: it is applied in memory only, and every object it would
            add is ringed in magenta (red when a placement check failed).
            Nothing is written to the map.
    """
    import json as _json

    path = _resolve(map_path)
    data = _load_map_any(path)
    highlights: dict[str, list[dict]] = {}
    overlay_note = ""
    if overlay.strip():
        try:
            doc = authoring.load_doc(overlay, WORKSPACE)
            _, res = authoring.merge_bytes(cb.serialize_map(data), doc, _catalog(), allow_unknown=True)
        except (authoring.AuthoringError, map_json.MapJsonError, ValueError, OSError) as exc:
            return f"Cannot preview overlay: {exc}"
        data = res.tree
        for a in authoring.overlay_from_result(res):
            highlights.setdefault(a["room"], []).append(a)
    all_rooms = cb.get_rooms(data)

    if not all_rooms:
        return "No rooms in this map."

    if prefix:
        rooms = [r for r in all_rooms if _room_matches(r.get("name", ""), prefix)]
        if not rooms:
            return f"No rooms matching prefix '{prefix}'."
    else:
        rooms = all_rooms
    if highlights:
        shown = [a for r in rooms for a in highlights.get(r.get("name", ""), [])]
        n_bad = sum(1 for a in shown if a["problems"])
        overlay_note = (f"\nPlanned additions highlighted: {len(shown)} "
                        f"({n_bad} with placement problems, shown red)")

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
        if detail:
            # Keep the room's layout visible: small labels in the top-left corner.
            fs = min(fs, 12)
            lx, ly, sy, anchor = rx + 5, ry + fs + 3, ry + fs + 3 + max(8, fs - 4) + 2, "start"
        else:
            lx, ly, sy, anchor = rx + rw / 2, ry + rh / 2 + fs * 0.35, ry + rh / 2 + fs + max(8, fs - 4), "middle"
        stroke = "#ffd700" if has_cp else "#5a5a7a"
        stroke_w = 3 if has_cp else 1.5
        badge_str = " ".join(badges)
        disp_label = (badge_str + " " if badge_str else "") + name
        sub_label = f"{ent_count}e · {trigger_count}t · {rw}×{rh}"

        added = highlights.get(room.get("name", ""), [])
        if added:
            badges.append(f"✚{len(added)}")
            disp_label = " ".join(badges) + " " + name
        g_parts = [
            f'<rect class="rb" x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
            f'fill="{color}" fill-opacity="{0.22 if detail else 0.72}" stroke="{stroke}" stroke-width="{stroke_w}" rx="4"/>',
            _room_detail_svg(room, rx, ry) if detail else "",
            _overlay_svg(added, rx, ry),
            f'<rect class="rh" x="{rx}" y="{ry}" width="{rw}" height="{rh}" '
            f'fill="none" stroke="transparent" rx="4" pointer-events="none"/>',
            f'<text class="ln" x="{lx:.1f}" y="{ly:.1f}" '
            f'text-anchor="{anchor}" font-size="{fs}" fill="white" '
            f'font-family="monospace" font-weight="bold" filter="url(#shadow)">{disp_label}</text>',
            f'<text class="ls" x="{lx:.1f}" y="{sy:.1f}" '
            f'text-anchor="{anchor}" font-size="{max(8, fs-4)}" fill="#aab" '
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
// first fitToWindow()/apply() runs at the end, once the minimap exists

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

fitToWindow(); apply();
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
        output_file = str(out_dir / f"map_preview_{path.stem}{'_merge' if highlights else ''}.html")
    else:
        output_file = str(_resolve(output_file))

    Path(output_file).write_text(html, encoding="utf-8")
    return (f"Preview saved to: {output_file}\nOpen this file in a browser to see the map.\n"
            f"Rooms shown: {len(rooms)}{overlay_note}")




# ═══════════════════════════════════════════════════════════════════════════════
#  PROCEDURAL GENERATION TOOLS
# ═══════════════════════════════════════════════════════════════════════════════

@tool()
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


@tool()
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
    _save(map_file, data)

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


@tool()
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


@tool()
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


@tool()
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
    _save(out_file, map_data)

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


@tool()
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
    _save(out_file, map_data)

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


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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
                    _save(path, data)
                    return f"Updated entity id={entity_id} in {room_name}: {props}"

    return f"Entity id={entity_id} not found in room '{room_name}'."


@tool()
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


@tool()
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

    _save(path, data)
    return f"Updated room '{room_name}': {', '.join(updated)}"


@tool()
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
    _save(path, data)

    return f"Cloned '{source_room}' → '{name}' at ({clone['x']}, {clone['y']})"


@tool()
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

    _save(path, data)
    return f"Added {added} entities to room '{room_name}'."


# ═══════════════════════════════════════════════════════════════════════════════
#  DECALS
# ═══════════════════════════════════════════════════════════════════════════════


@tool()
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


@tool()
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
    _save(path, data)

    return f"Added {layer} decal '{texture}' at ({x},{y}) in room '{room_name}'."


@tool()
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
    _save(path, data)
    return f"Removed {layer} decal '{removed.get('texture', '?')}' (index {index}) from '{room_name}'."


# ═══════════════════════════════════════════════════════════════════════════════
#  ADVANCED ANALYSIS (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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

    def short(names):
        return ", ".join(_short_room(n) for n in names)

    if result["hubs"]:
        lines.append(f"  Hubs (3+ connections): {short(result['hubs'])}")
    if result["dead_ends"]:
        lines.append(f"  Dead ends (1 connection): {short(result['dead_ends'])}")
    if result["isolated_rooms"]:
        lines.append(f"  Isolated (0 connections): {short(result['isolated_rooms'])}")

    lines.append("\n  Adjacency:")
    for name, nbrs in result["adjacency"].items():
        if nbrs:
            lines.append(f"    {_short_room(name)} → {short(nbrs)}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  SUGGESTIONS (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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


@tool()
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
        _save(path, data)

    lines.insert(1, f"  Issues: {total_issues}")
    if auto_fix:
        lines.insert(2, f"  Auto-fixed: {total_fixes}")
    if total_issues == 0:
        lines.append("  All rooms PASS ✓")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  CATALOG EXTENSIONS
# ═══════════════════════════════════════════════════════════════════════════════


@tool()
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


@tool()
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


@tool()
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


@tool()
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
    _save(map_file, data)
    return f"Imported room '{room.get('name')}' into {map_path} at ({room.get('x',0)},{room.get('y',0)})"


# ═══════════════════════════════════════════════════════════════════════════════
#  DIFF & FIX (gdep-inspired)
# ═══════════════════════════════════════════════════════════════════════════════


@tool()
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


@tool()
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
#  ROOM SETTINGS — metadata, music, physics, camera
# ═══════════════════════════════════════════════════════════════════════════════

_ROOM_BOOL_KEYS = frozenset({
    "dark", "space", "underwater", "whisper",
    "disableDownTransition", "delayAltMusicFade",
    "musicLayer1", "musicLayer2", "musicLayer3", "musicLayer4",
})
_ROOM_STR_KEYS = frozenset({
    "music", "alt_music", "ambience", "windPattern",
    "musicProgress", "ambienceProgress",
})
_ROOM_INT_KEYS = frozenset({
    "cameraOffsetX", "cameraOffsetY", "c",
})
_ROOM_ALL_SETTINGS = _ROOM_BOOL_KEYS | _ROOM_STR_KEYS | _ROOM_INT_KEYS

_WIND_PATTERNS = frozenset({
    "None", "Left", "Right", "LeftStrong", "RightStrong",
    "LeftOnOff", "RightOnOff", "LeftOnOffFast", "RightOnOffFast",
    "Alternating", "LeftGemsOnly", "RightCrazy", "Down", "Up",
    "Space",
})


@tool()
def get_room_settings(map_path: str, room_name: str) -> str:
    """Get all editable settings for a room (music, physics, wind, camera, flags).

    Returns all metadata properties including music tracks, physics flags
    (dark/space/underwater), wind pattern, camera offsets, and music layers.

    Args:
        map_path: Path to the .bin file
        room_name: Room name (with or without 'lvl_' prefix)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    lines = [f"Room settings: {room.get('name', '?')}"]
    lines.append("")
    lines.append("── Metadata ─────────────────────────────")
    lines.append(f"  name            = {room.get('name', '')!r}")
    lines.append(f"  position        = ({room.get('x', 0)}, {room.get('y', 0)})")
    lines.append(f"  size            = {room.get('width', 0)}x{room.get('height', 0)}")
    lines.append(f"  color (c)       = {room.get('c', 0)}")
    lines.append("")
    lines.append("── Physics ──────────────────────────────")
    lines.append(f"  dark            = {room.get('dark', False)}")
    lines.append(f"  space           = {room.get('space', False)}")
    lines.append(f"  underwater      = {room.get('underwater', False)}")
    lines.append(f"  whisper         = {room.get('whisper', False)}")
    lines.append(f"  windPattern     = {room.get('windPattern', 'None')!r}")
    lines.append(f"  disableDownTransition = {room.get('disableDownTransition', False)}")
    lines.append("")
    lines.append("── Music / Ambience ──────────────────────")
    lines.append(f"  music           = {room.get('music', '')!r}")
    lines.append(f"  alt_music       = {room.get('alt_music', '')!r}")
    lines.append(f"  ambience        = {room.get('ambience', '')!r}")
    lines.append(f"  musicProgress   = {room.get('musicProgress', '')!r}")
    lines.append(f"  ambienceProgress= {room.get('ambienceProgress', '')!r}")
    lines.append(f"  delayAltMusicFade = {room.get('delayAltMusicFade', False)}")
    lines.append(f"  musicLayer1     = {room.get('musicLayer1', True)}")
    lines.append(f"  musicLayer2     = {room.get('musicLayer2', True)}")
    lines.append(f"  musicLayer3     = {room.get('musicLayer3', True)}")
    lines.append(f"  musicLayer4     = {room.get('musicLayer4', True)}")
    lines.append("")
    lines.append("── Camera ────────────────────────────────")
    lines.append(f"  cameraOffsetX   = {room.get('cameraOffsetX', 0)}")
    lines.append(f"  cameraOffsetY   = {room.get('cameraOffsetY', 0)}")
    return "\n".join(lines)


@tool()
def update_room_settings(
    map_path: str,
    room_name: str,
    settings: str,
) -> str:
    """Update room settings (music, physics, wind, camera, flags) by merging a JSON object.

    Editable keys:
      Physics:  dark, space, underwater, whisper, disableDownTransition,
                windPattern (None/Left/Right/LeftStrong/RightStrong/…)
      Music:    music, alt_music, ambience, musicProgress, ambienceProgress,
                delayAltMusicFade, musicLayer1..4
      Camera:   cameraOffsetX, cameraOffsetY
      Meta:     c (color index 0-7)

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        settings: JSON object of key/value pairs to update,
                  e.g. '{"music": "event:/music/lvl2/beginning", "dark": true}'
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    try:
        props = json.loads(settings)
    except json.JSONDecodeError:
        return f"Invalid JSON settings: {settings}"
    if not isinstance(props, dict):
        return "settings must be a JSON object."

    applied = []
    rejected = []

    for k, v in props.items():
        if k not in _ROOM_ALL_SETTINGS:
            rejected.append(f"{k} (unknown)")
            continue
        if k in _ROOM_BOOL_KEYS:
            if not isinstance(v, bool):
                rejected.append(f"{k} (must be bool)")
                continue
        elif k in _ROOM_INT_KEYS:
            try:
                v = int(v)
            except (TypeError, ValueError):
                rejected.append(f"{k} (must be int)")
                continue
        elif k == "windPattern":
            if v not in _WIND_PATTERNS:
                rejected.append(
                    f"{k}={v!r} (unknown pattern; "
                    f"valid: {', '.join(sorted(_WIND_PATTERNS))})"
                )
                continue
        room[k] = v
        applied.append(f"{k}={v!r}")

    if applied:
        _save(path, data)

    lines = [f"Room '{room.get('name')}' settings update:"]
    if applied:
        lines.append(f"  Applied: {', '.join(applied)}")
    if rejected:
        lines.append(f"  Rejected: {', '.join(rejected)}")
    if not applied and not rejected:
        lines.append("  No changes.")
    return "\n".join(lines)


@tool()
def rename_room(map_path: str, room_name: str, new_name: str) -> str:
    """Rename a room in a map.

    Args:
        map_path: Path to the .bin file
        room_name: Current room name
        new_name: New room name (lvl_ prefix added automatically if missing)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    canonical = new_name if new_name.startswith("lvl_") else f"lvl_{new_name}"
    for r in cb.get_rooms(data):
        if r.get("name") == canonical:
            return f"A room named '{canonical}' already exists."

    old_name = room.get("name")
    room["name"] = canonical
    _save(path, data)
    return f"Renamed room '{old_name}' → '{canonical}'."


@tool()
def move_room(map_path: str, room_name: str, x: int, y: int) -> str:
    """Move a room to a new world-space position.

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        x: New X position in pixels
        y: New Y position in pixels
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    old_x, old_y = room.get("x", 0), room.get("y", 0)
    room["x"] = x
    room["y"] = y
    _save(path, data)
    return f"Moved room '{room.get('name')}' ({old_x},{old_y}) → ({x},{y})."


@tool()
def resize_room(map_path: str, room_name: str, width: int, height: int) -> str:
    """Resize a room (snaps to 8-pixel grid, preserves tile data).

    Args:
        map_path: Path to the .bin file
        room_name: Room name
        width: New width in pixels (must be multiple of 8, min 40)
        height: New height in pixels (must be multiple of 8, min 40)
    """
    path = _resolve(map_path)
    data = cb.read_map(path)
    room = cb.get_room(data, room_name)
    if room is None:
        return f"Room '{room_name}' not found. Available: {_room_names(data)}"

    width = ((width + 7) // 8) * 8
    height = ((height + 7) // 8) * 8
    if width < 40 or height < 40:
        return "Minimum room size is 40x40 pixels."

    old_w, old_h = room.get("width", 0), room.get("height", 0)
    room["width"] = width
    room["height"] = height
    _save(path, data)
    return (
        f"Resized room '{room.get('name')}': "
        f"{old_w}x{old_h} → {width}x{height} px."
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  MAP METADATA & DEPENDENCY SETTINGS
# ═══════════════════════════════════════════════════════════════════════════════

def _get_meta_node(data: dict) -> dict | None:
    """Return the meta element from map data if present."""
    return cb.find_child(data, "meta")


def _ensure_meta_node(data: dict) -> dict:
    """Return or create the meta element in map data."""
    meta = cb.find_child(data, "meta")
    if meta is None:
        meta = {"__name": "meta", "__children": []}
        data.setdefault("__children", []).append(meta)
    return meta


def _ensure_nested_node(root: dict, subpath: str) -> dict:
    """Return (creating as needed) the nested element at a '/'-separated
    subpath below *root*, e.g. "mode/audiostate". Missing intermediate
    elements are created automatically. Empty subpath returns *root* itself.
    """
    node = root
    for part in [p for p in subpath.split("/") if p]:
        child = cb.find_child(node, part)
        if child is None:
            child = {"__name": part, "__children": []}
            node.setdefault("__children", []).append(child)
        node = child
    return node


def _format_meta_node(el: dict, indent: int = 1) -> list[str]:
    """Recursively format an element's attributes and all descendant
    elements. Meta nodes commonly nest audio/side overrides several levels
    deep (e.g. meta -> mode -> audiostate -> Music), so this must not stop
    at one level or those attributes silently disappear from the report.
    """
    pad = "  " * indent
    lines = []
    for k, v in el.items():
        if k not in ("__name", "__children"):
            lines.append(f"{pad}{k} = {v!r}")
    for child in el.get("__children", []):
        cname = child.get("__name", "?")
        lines.append(f"\n{pad}[{cname}]")
        lines.extend(_format_meta_node(child, indent + 1))
    return lines


@tool()
def get_map_metadata(map_path: str) -> str:
    """Read the map-level metadata block (Everest/celeste.yaml meta settings).

    Returns the meta node that stores settings like wipe type, intro type,
    heart texture, cassette song, and other map-wide overrides — including
    nested settings such as mode/audiostate (per-side Music, AltMusic,
    AmbienceProgress, etc.), which are reported at any nesting depth.

    Args:
        map_path: Path to the .bin file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    lines = [
        f"Map: {data.get('_package', '?')}",
        f"Package: {data.get('_package', '?')}",
        "",
    ]

    meta = _get_meta_node(data)
    if meta:
        lines.append("── Meta settings ─────────────────────────")
        skip = {"__name", "__children"}
        props = {k: v for k, v in meta.items() if k not in skip}
        if props:
            for k, v in sorted(props.items()):
                lines.append(f"  {k:30s} = {v!r}")
        else:
            lines.append("  (no meta properties set)")

        for child in meta.get("__children", []):
            cname = child.get("__name", "?")
            lines.append(f"\n  [{cname}]")
            lines.extend(_format_meta_node(child, indent=2))
    else:
        lines.append("No meta node found (this is normal for most maps).")

    return "\n".join(lines)


@tool()
def set_map_metadata(map_path: str, settings: str, subpath: str = "") -> str:
    """Set map-level metadata properties (Everest meta block).

    These properties control map-wide behaviour like wipe type, mountain
    colours, intro cutscene, heart/cassette song overrides, etc.

    Common keys:
      IntroType       – "WalkInRight", "WalkInLeft", "Jump", "WakeUp", etc.
      Wipe            – "Mountain", "Curtain", "HeartWipe", "KeyDoor", etc.
      ColorGrade      – e.g. "none", "oldsite", "reflection"
      DarknessAlpha   – float 0.0-1.0
      BloomBase       – float
      BloomStrength   – float
      Dreaming        – bool
      CassetteNoteColor – hex color string

    By default this only touches attributes directly on the meta element
    itself. Several commonly-edited settings — most notably the per-side
    audio overrides shown in Lönn's "Map Metadata" dialog (Music, AltMusic,
    AmbienceProgress, MusicLayers, etc.) — actually live nested under
    meta -> mode -> audiostate, not on meta directly. Use subpath to reach
    those (and any other nested meta child) instead of silently no-op'ing.

    Args:
        map_path: Path to the .bin file
        settings: JSON object of meta key/value pairs to set,
                  e.g. '{"Wipe": "Mountain", "Dreaming": true}'
        subpath: Optional '/'-separated path to a nested element under meta
                 to target instead of meta itself, e.g. "mode/audiostate"
                 for '{"Music": "event:/music/DZ/lvl2/beginning"}'. Missing
                 intermediate elements are created automatically.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    try:
        props = json.loads(settings)
    except json.JSONDecodeError:
        return f"Invalid JSON settings: {settings}"
    if not isinstance(props, dict):
        return "settings must be a JSON object."

    meta = _ensure_meta_node(data)
    target = _ensure_nested_node(meta, subpath) if subpath else meta
    applied = []
    for k, v in props.items():
        if v is None:
            if k in target:
                del target[k]
                applied.append(f"-{k}")
        else:
            target[k] = v
            applied.append(f"{k}={v!r}")

    _save(path, data)
    label = data.get('_package', '?') + (f" [meta/{subpath}]" if subpath else " [meta]")
    return (
        f"Map metadata updated for '{label}':\n"
        + ("  " + ", ".join(applied) if applied else "  (no changes)")
    )


def _walk_replace_strings(
    el: dict, old: str, new: str, changes: list[tuple[str, str, str, str]], path: str = ""
) -> None:
    """Recursively substring-replace *old* with *new* in every string
    attribute anywhere under *el* (meta, rooms, entities, triggers, ...),
    skipping structural keys and raw tile/innerText data. Mutates in place
    and appends (element_path, key, old_value, new_value) to *changes*.
    """
    name = el.get("__name", "")
    node_path = f"{path}/{name}" if path else name
    for k, v in el.items():
        if k in ("__name", "__children") or k == "innerText":
            continue
        if isinstance(v, str) and old in v:
            new_v = v.replace(old, new)
            changes.append((node_path, k, v, new_v))
            el[k] = new_v
    for child in el.get("__children", []):
        _walk_replace_strings(child, old, new, changes, node_path)


@tool()
def replace_audio_event(map_path: str, old_event: str, new_event: str, dry_run: bool = False) -> str:
    """Find and replace an FMOD audio event path anywhere in a map.

    update_room and set_map_metadata each only reach one room, or the meta
    element's own attributes — they cannot bulk-rename an audio event that
    is scattered across map metadata (including nested mode/audiostate),
    every room's music/ambience attributes, and any entity or trigger that
    carries its own audio override (e.g. lightningBlock, PowerGenerator).
    This tool walks the entire element tree instead, so a rename like
    "pusheen" -> "DZ" reaches every occurrence in one call.

    Args:
        map_path: Path to the .bin file
        old_event: Substring to find, e.g. "pusheen" or a full event path
                    like "event:/music/pusheen/lvl2/beginning"
        new_event: Replacement substring, e.g. "DZ"
        dry_run: If true, report what would change without writing the file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    data = cb.read_map(path)

    changes: list[tuple[str, str, str, str]] = []
    _walk_replace_strings(data, old_event, new_event, changes)

    if not changes:
        return f"No occurrences of {old_event!r} found in {map_path}."

    affected = len({c[0] for c in changes})
    lines = [
        f"{'Would update' if dry_run else 'Updated'} {len(changes)} value(s) "
        f"across {affected} element(s):"
    ]
    for node_path, key, old_v, new_v in changes:
        lines.append(f"  {node_path} | {key}: {old_v} -> {new_v}")

    if not dry_run:
        _save(path, data)

    return "\n".join(lines)


@tool()
def get_map_dependencies(map_path: str) -> str:
    """Read the mod dependencies list from a map's everest.yaml or meta node.

    Scans the workspace for an everest.yaml alongside the map file, and also
    reads the Deps child element of the meta node if present.

    Args:
        map_path: Path to the .bin file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    lines = [f"Dependencies for: {path.stem}"]

    # 1. Check everest.yaml in same dir and parent dirs
    yaml_content = None
    yaml_path = None
    for search_dir in [path.parent, path.parent.parent, WORKSPACE]:
        for fname in ("everest.yaml", "everest.yml"):
            candidate = search_dir / fname
            if candidate.exists():
                try:
                    yaml_content = candidate.read_text(encoding="utf-8", errors="replace")
                    yaml_path = candidate.relative_to(WORKSPACE)
                    break
                except OSError:
                    pass
        if yaml_content:
            break

    if yaml_content:
        lines.append(f"\n── everest.yaml ({yaml_path}) ─────────────────")
        lines.append(yaml_content[:3000])
        if len(yaml_content) > 3000:
            lines.append("  ... (truncated)")
    else:
        lines.append("\n── everest.yaml ──────────────────────────────")
        lines.append("  (not found — create one to declare mod dependencies)")

    # 2. Check meta node Deps
    data = cb.read_map(path)
    meta = _get_meta_node(data)
    if meta:
        deps_el = None
        for child in meta.get("__children", []):
            if child.get("__name", "").lower() in ("deps", "dependencies"):
                deps_el = child
                break
        if deps_el:
            lines.append("\n── Meta/Deps node ────────────────────────────")
            for dep in deps_el.get("__children", []):
                dname = dep.get("Name", dep.get("name", "?"))
                dver = dep.get("Version", dep.get("version", "?"))
                lines.append(f"  {dname} >= {dver}")
        else:
            lines.append("\n── Meta/Deps node ────────────────────────────")
            lines.append("  (none)")

    return "\n".join(lines)


@tool()
def set_map_dependencies(map_path: str, dependencies: str) -> str:
    """Write or update the mod dependency list in the map's everest.yaml.

    Creates or replaces the everest.yaml file in the same directory as the map.

    Args:
        map_path: Path to the .bin file
        dependencies: JSON array of dependency objects, each with "Name" and
                      "Version" fields. Use "Celeste" and "Everest" as base deps.
                      Example: '[{"Name":"Everest","Version":"1.0.0"},
                                  {"Name":"CommunalHelper","Version":"1.8.0"}]'
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    try:
        dep_list = json.loads(dependencies)
    except json.JSONDecodeError:
        return f"Invalid JSON: {dependencies}"
    if not isinstance(dep_list, list):
        return "dependencies must be a JSON array."

    # Validate entries
    for i, d in enumerate(dep_list):
        if not isinstance(d, dict):
            return f"Entry {i} must be an object with Name and Version."
        if "Name" not in d or "Version" not in d:
            return f"Entry {i} missing Name or Version field."

    # Build YAML content (hand-rolled to avoid requiring pyyaml)
    data = cb.read_map(path)
    pkg = data.get("_package", path.stem)
    lines = ["- Name: " + str(pkg), "  Version: 1.0.0", "  Dependencies:"]
    for d in dep_list:
        lines.append(f"    - Name: {d['Name']}")
        lines.append(f"      Version: {d['Version']}")

    yaml_text = "\n".join(lines) + "\n"
    yaml_file = path.parent / "everest.yaml"
    yaml_file.write_text(yaml_text, encoding="utf-8")

    rel = yaml_file.relative_to(WORKSPACE)
    return (
        f"everest.yaml written: {rel}\n"
        f"  {len(dep_list)} dependenc{'y' if len(dep_list) == 1 else 'ies'} declared:\n"
        + "\n".join(f"  - {d['Name']} >= {d['Version']}" for d in dep_list)
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  ML ASSET ANALYSIS — Maddie480 graphics dump + Lönn source patterns
# ═══════════════════════════════════════════════════════════════════════════════

try:
    from . import graphics_analyzer          # installed package
except ImportError:
    try:
        import graphics_analyzer             # run directly from source
    except ImportError:
        graphics_analyzer = None             # optional module


@tool()
def analyze_map_assets(
    map_path: str,
    check_textures: bool = True,
    check_tilesets: bool = True,
    check_entities: bool = True,
    fetch_asset_index: bool = False,
) -> str:
    """ML-style analysis of a map's assets against the Maddie480 graphics dump.

    Extracts all texture references (parallax backgrounds, decals, entity
    textures) from the map and cross-references them against the known
    Celeste graphics dump from maddie480.ovh/celeste/graphics-dump-browser.

    Also validates entity names against the Lönn source entity catalog and
    detects unknown / missing assets that would cause in-game errors.

    Args:
        map_path: Path to the .bin file
        check_textures: Validate parallax/decal texture paths
        check_tilesets: Validate tileset character references
        check_entities: Validate entity names against Lönn catalog
        fetch_asset_index: If True, attempt to fetch the asset index from
                           maddie480.ovh (requires network access). If False,
                           uses the built-in known-asset catalog.
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"

    if graphics_analyzer is not None:
        return graphics_analyzer.analyze_map_assets(
            path=path,
            workspace=WORKSPACE,
            check_textures=check_textures,
            check_tilesets=check_tilesets,
            check_entities=check_entities,
            fetch_asset_index=fetch_asset_index,
        )

    # Fallback: built-in analysis without the optional module
    data = cb.read_map(path)
    rooms = cb.get_rooms(data)
    lines = [
        f"Asset Analysis: {path.stem}",
        f"  Rooms: {len(rooms)}",
        "",
    ]

    textures_found: dict[str, int] = {}
    entity_names: dict[str, int] = {}
    tileset_chars: set[str] = set()

    for room in rooms:
        for child in room.get("__children", []):
            cname = child.get("__name", "")
            if cname in ("fgdecals", "bgdecals"):
                for dec in child.get("__children", []):
                    t = dec.get("texture", "")
                    if t:
                        textures_found[t] = textures_found.get(t, 0) + 1
            elif cname == "entities":
                for ent in child.get("__children", []):
                    n = ent.get("__name", "")
                    if n:
                        entity_names[n] = entity_names.get(n, 0) + 1
                    # entity-level texture
                    for k in ("texture", "sprite", "atlas"):
                        t = ent.get(k, "")
                        if t:
                            textures_found[t] = textures_found.get(t, 0) + 1
            elif cname in ("solids", "bg"):
                text = child.get("innerText", "")
                for ch in text:
                    if ch not in ("0", "\n", "\r"):
                        tileset_chars.add(ch)

    # Styleground textures
    style = cb.find_child(data, "Style")
    if style:
        for layer_name in ("Foregrounds", "Backgrounds"):
            layer_el = cb.find_child(style, layer_name)
            if layer_el:
                for sg in layer_el.get("__children", []):
                    t = sg.get("texture", "")
                    if t:
                        textures_found[t] = textures_found.get(t, 0) + 1

    if check_textures and textures_found:
        lines.append(f"── Texture References ({len(textures_found)} unique) ─────")
        for t, cnt in sorted(textures_found.items(), key=lambda x: -x[1])[:30]:
            lines.append(f"  {cnt:3}×  {t}")
        if len(textures_found) > 30:
            lines.append(f"  ... +{len(textures_found) - 30} more")
        lines.append("")

    if check_entities and entity_names:
        lines.append(f"── Entity Types ({len(entity_names)} unique) ──────────────")
        # Known vanilla entities from Lönn source
        vanilla = _VANILLA_ENTITIES
        unknown = []
        for n, cnt in sorted(entity_names.items(), key=lambda x: -x[1]):
            tag = ""
            if n not in vanilla:
                tag = "  ⚠ unknown/modded"
                unknown.append(n)
            lines.append(f"  {cnt:3}×  {n}{tag}")
        lines.append("")
        if unknown:
            lines.append(
                f"  Note: {len(unknown)} entity type(s) not in vanilla catalog "
                f"— likely mod entities (check your mod's Loenn/entities/ folder)"
            )
            lines.append("")

    if check_tilesets:
        lines.append(f"── Tileset Characters Used: {sorted(tileset_chars)} ───────")
        lines.append(
            "  (Map against ForegroundTiles.xml / BackgroundTiles.xml "
            "to verify all chars are defined)"
        )
        lines.append("")

    lines.append("── Maddie480 Asset Index ─────────────────────────")
    if fetch_asset_index:
        lines.append(
            "  Set fetch_asset_index=True to query:\n"
            "  https://maddie480.ovh/celeste/graphics-dump-browser\n"
            "  This requires network access. Install the optional\n"
            "  graphics_analyzer module for full ML-powered analysis."
        )
    else:
        lines.append(
            "  To cross-reference with the full Celeste graphics dump:\n"
            "    1. Visit: https://maddie480.ovh/celeste/graphics-dump-browser\n"
            "    2. Or run with fetch_asset_index=True (requires network)\n"
            "    3. Or install graphics_analyzer module for offline ML analysis."
        )

    return "\n".join(lines)


# Vanilla Celeste entity catalog derived from CelestialCartographers/Loenn source
_VANILLA_ENTITIES = frozenset({
    "player", "checkpoint", "strawberry", "goldenBerry", "spring",
    "jumpThru", "refill", "spikes", "spikeUp", "spikeDown",
    "spikeLeft", "spikeRight", "triggerSpikesUp", "triggerSpikesDown",
    "triggerSpikesLeft", "triggerSpikesRight",
    "crumbleBlock", "fallingBlock", "moveBlock", "crushBlock",
    "bumper", "seekerBarrier", "seeker", "booster", "feather",
    "dreamBlock", "dashBlock", "fireBarrier", "iceBlock",
    "cloud", "cassette", "blackGem", "heartGem", "darkChest",
    "key", "lock", "door", "zipMover", "swapBlock", "linkedZipMover",
    "floatySpaceBlock", "glassBlock", "sinkingPlatform",
    "spikesOrigDown", "spikesOrigUp", "spikesOrigLeft", "spikesOrigRight",
    "colorSwitch", "bridgeTileController", "clutterDoor", "clutterSwitch",
    "exitBlock", "fakeWall", "fakeBlock", "bonfire", "torch",
    "lamp", "memorial", "bird", "birdTutorial", "lightbeam",
    "cobweb", "flutterbirdIntro", "blackhole", "payphone",
    "powerSourceNumber", "puffer", "rotateSpinner", "trackSpinner",
    "spinner", "templeBigEyeball", "templeGate", "theo",
    "theoPhone", "touchSwitch", "turnBlock", "wallBouncingBall",
    "water", "waterfall", "whiteblock", "wire",
    "finalBoss", "finalBossBeam", "finalBossMovingBlock",
    "lightSourceBlocker", "npc", "car", "towerViewer",
    "risingLava", "sandwichLava", "tentacles",
    "reflectionHeartStatue", "resortPlatform", "resortLantern",
    "reflectPanel", "glider", "theoCrystal", "flingBird",
    "moonCreature", "memorialTextController", "coreModeToggle",
    "frozenWaterfall", "iceBlock", "movingPlatform",
    "switchGate", "flagSwitchGate", "groupSwitchGate",
    "introCar", "introCornerWalkPast", "introRockingPlatform",
    "introCrusher", "introWalkPast", "windController",
    "killbox", "summitCheckpoint", "summitCloud",
    "coreMessage", "playbackTutorial", "playbackBillboard",
    "eventTrigger",
})


# ═══════════════════════════════════════════════════════════════════════════════
#  JSON ROUND-TRIP AND AUTHORING
# ═══════════════════════════════════════════════════════════════════════════════
#
# map_to_json / json_to_map convert any .bin to typed JSON and back,
# byte-identical when unedited. Authoring JSON is a looser format for content
# written by hand or by a script (see js/ for the JavaScript layer); it is
# merged into an existing map with merge_json_into_map, which only ever adds.

_catalog_cache: dict = {}


def _catalog(refresh: bool = False) -> "lua_defs.Catalog":
    """Lönn definitions (workspace mod, installed mods, vanilla), rebuilt when a plugin file changes."""
    sig_parts = []
    for kind in lua_defs.KINDS:
        base = WORKSPACE / "Loenn" / kind
        if base.is_dir():
            files = list(base.rglob("*.lua"))
            sig_parts.append((kind, len(files), max((f.stat().st_mtime for f in files), default=0)))
    sig = repr(sig_parts)
    if refresh or _catalog_cache.get("sig") != sig:
        _catalog_cache["cat"] = lua_defs.Catalog.load(WORKSPACE)
        _catalog_cache["sig"] = sig
    return _catalog_cache["cat"]


def _default_out(source: Path, suffix: str) -> Path:
    out_dir = WORKSPACE / "Export"
    out_dir.mkdir(exist_ok=True)
    return out_dir / f"{source.stem}{suffix}"


@tool()
def map_to_json(map_path: str, output_path: str = "", indent: int = 1) -> str:
    """Convert a .bin map to typed JSON that converts back byte-identical.

    Every attribute keeps its binary type: bool, u8, i16, i32, f32, lookup
    (string-table string), string (plain string) or rle (tile data, decoded).
    Room names are written exactly as stored (a 'lvl_' prefix stays). The dump
    is checked to convert back to the same bytes before it is saved.

    Args:
        map_path: Path to the .bin file
        output_path: Where to write the JSON (default: Export/<map>.json)
        indent: JSON indent; 0 for the most compact file
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    try:
        doc = map_json.map_to_json(path)
    except (map_json.MapJsonError, ValueError) as exc:
        return f"Not exported: {exc}"
    out = _resolve(output_path) if output_path.strip() else _default_out(path, ".json")
    out.parent.mkdir(parents=True, exist_ok=True)
    text = map_json.dumps(doc, indent=indent or None)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    rooms = len(cb.get_rooms(map_json.json_to_tree(doc)))
    return (f"Exported {path.name} → {out}\n{rooms} rooms, {len(doc['lookup'])} strings, "
            f"{len(text) // 1024} KB. Verified: converts back byte-identical "
            f"(sha256 {doc['source']['sha256'][:12]}…).")


@tool()
def json_to_map(json_path: str, map_path: str, overwrite: bool = False) -> str:
    """Convert typed JSON (from map_to_json, possibly edited) back to a .bin map.

    Refuses, writing nothing, when a value does not fit its declared type,
    when an unedited dump would not reproduce its source byte-for-byte, or when
    the output would not read back as the same map. Writes atomically (temp
    file + replace) and keeps rolling .bak copies of a file it replaces.

    Args:
        json_path: Typed JSON file
        map_path: Output .bin path
        overwrite: Must be true to replace an existing map
    """
    src = _resolve(json_path)
    out = _resolve(map_path)
    if not src.exists():
        return f"File not found: {json_path}"
    if out.exists() and not overwrite:
        return f"{map_path} exists. Pass overwrite=true to replace it (a timestamped backup is kept)."
    try:
        doc = json.loads(src.read_text(encoding="utf-8"))
        data_, notes = map_json.json_to_bin(doc)
        _save_bytes(out, data_, doc.get("header", "CELESTE MAP"))
    except json.JSONDecodeError as exc:
        return f"Not written: invalid JSON ({exc})"
    except (map_json.MapJsonError, ValueError) as exc:
        return f"Not written: {exc}"
    return f"Wrote {out}\n" + "\n".join(f"  - {n}" for n in notes)


@tool()
def check_authoring_json(authoring_json: str) -> str:
    """Resolve authoring JSON without a map: show each object with its defaults
    filled in from the Lönn definitions, and flag unknown names.

    Args:
        authoring_json: Inline JSON, or a path to a .json file. Format:
            {"rooms": [{"room": "a-05", "entities": [{"name": "strawberry", "x": 120, "y": 64}],
                        "triggers": [...]}]}
    """
    try:
        plan = authoring.resolve(authoring.load_doc(authoring_json, WORKSPACE), _catalog())
    except (authoring.AuthoringError, ValueError, OSError) as exc:
        return f"Invalid authoring JSON: {exc}"
    lines = [f"{len(plan.items())} object(s) in {len(plan.rooms)} room(s)"]
    for spec in plan.rooms:
        lines.append(f"\n[{spec.name}]" + (" (new room)" if spec.create else ""))
        for it in spec.items:
            src = f" [{it.origin}]" if it.known else " [UNKNOWN]"
            filled = f" defaults: {', '.join(it.defaults_used)}" if it.defaults_used else ""
            kind = "trigger" if it.section == "triggers" else "entity"
            lines.append(f"  {kind} {it.name}{src} at ({it.x:g},{it.y:g}){filled}")
            lines += [f"      ! {w}" for w in it.warnings]
    if plan.unknown:
        lines.append(f"\n{len(plan.unknown)} unknown name(s): "
                     + ", ".join(sorted({it.name for it in plan.unknown})))
    return "\n".join(lines)


@tool()
def merge_json_into_map(
    map_path: str,
    authoring_json: str,
    dry_run: bool = True,
    allow_unknown: bool = False,
    allow_problems: bool = False,
    preview: bool = True,
) -> str:
    """Add entities, triggers and rooms from authoring JSON to an existing map.

    Nothing already in the map is changed: the output is re-read and compared
    with the original, and the write is refused if anything but additions
    differ. New IDs are unique across the whole map, above every ID in it.
    Each added object is checked: inside room bounds, not inside solid tiles,
    not sealed in an air pocket; and the room validators must not report new
    issues.

    Args:
        map_path: Path to the .bin file
        authoring_json: Inline JSON or a path to a .json file (see check_authoring_json)
        dry_run: Only show the diff (default). Set false to write.
        allow_unknown: Place objects with no Lönn definition anyway
        allow_problems: Write even when placement checks fail
        preview: Also render an HTML preview with the additions highlighted
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    original = path.read_bytes()
    try:
        doc = authoring.load_doc(authoring_json, WORKSPACE)
        new_bytes, res = authoring.merge_bytes(original, doc, _catalog(), allow_unknown=allow_unknown)
    except (authoring.AuthoringError, map_json.MapJsonError, ValueError, OSError) as exc:
        return f"Not merged: {exc}"

    report = [f"{'DRY RUN — nothing written' if dry_run else 'MERGE'}: {path.name}",
              authoring.describe(res)]
    if preview:
        plan_path = _default_out(path, "_merge_plan.json")
        plan_path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        report.append("\n" + render_map_html(str(path), overlay=str(plan_path)))
    if dry_run:
        report.append("\nRun again with dry_run=false to write.")
        return "\n".join(report)
    if res.problems and not allow_problems:
        report.append(f"\nNot written: {len(res.problems)} placement problem(s). Fix them, or pass "
                      "allow_problems=true.")
        return "\n".join(report)
    if path.read_bytes() != original:
        return "Not written: the map changed on disk while merging. Run again."
    _save_bytes(path, new_bytes)
    report.append(f"\nWritten ({len(original)} → {len(new_bytes)} bytes).")
    return "\n".join(report)


def _run_author_script(script: Path, timeout: int = 60) -> tuple:
    """Run a JS authoring script with Node; return (authoring JSON, console output)."""
    import shutil
    import subprocess
    import tempfile
    node = os.environ.get("LOENN_MCP_NODE") or shutil.which("node")
    if not node:
        raise RuntimeError("Node.js was not found (install it, or set LOENN_MCP_NODE)")
    cli = Path(os.environ.get("LOENN_MCP_AUTHOR_CLI") or authoring.js_cli_path())
    if not cli.exists():
        raise RuntimeError(f"JS authoring CLI not found at {cli}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "authoring.json"
        proc = subprocess.run([node, str(cli), str(script), "--out", str(out)],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=timeout, cwd=str(script.parent))
        log = ((proc.stdout or "") + (proc.stderr or "")).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"script failed (exit {proc.returncode}):\n{log[-3000:]}")
        if not out.exists():
            raise RuntimeError("script produced no authoring JSON (export a Plan, or call plan.emit())")
        return json.loads(out.read_text(encoding="utf-8")), log


@tool()
def run_author_script(
    script_path: str,
    map_path: str = "",
    output_json: str = "",
    dry_run: bool = True,
    allow_unknown: bool = False,
    allow_problems: bool = False,
) -> str:
    """Run a JavaScript authoring script (js/ package, lucid-dream style) and
    use its output.

    The script builds rooms/entities/triggers in code and exports a Plan (see
    README "Authoring maps from JavaScript"). Its authoring JSON is saved to
    output_json, and merged into map_path if given (dry run by default, exactly
    like merge_json_into_map). Needs Node.js.

    Args:
        script_path: The .js file
        map_path: Map to merge into (optional)
        output_json: Where to save the generated authoring JSON
            (default: Export/<script>.authoring.json)
        dry_run: Only show the diff when merging (default)
        allow_unknown: See merge_json_into_map
        allow_problems: See merge_json_into_map
    """
    script = _resolve(script_path)
    if not script.exists():
        return f"File not found: {script_path}"
    try:
        doc, log = _run_author_script(script)
    except (RuntimeError, OSError, ValueError) as exc:
        return f"Script not run: {exc}"
    out = _resolve(output_json) if output_json.strip() else _default_out(script, ".authoring.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    report = [f"Ran {script.name} → {out}"]
    if log:
        report.append("Console:\n" + "\n".join("  " + ln for ln in log.splitlines()[-40:]))
    report.append(check_authoring_json(str(out)))
    if map_path.strip():
        report.append("\n" + merge_json_into_map(map_path, str(out), dry_run=dry_run,
                                                 allow_unknown=allow_unknown, allow_problems=allow_problems))
    return "\n".join(report)


# ═══════════════════════════════════════════════════════════════════════════════
#  LIVE GAME LINK — Everest DebugRC (+ CelesteTAS) on the running game
# ═══════════════════════════════════════════════════════════════════════════════
#
# Everest serves a debug HTTP server (default http://localhost:32270) while the
# game runs with debug mode on. These tools reload maps, teleport, read the
# session and, when CelesteTAS is installed, play inputs to prove a room is
# beatable. Override the address with LOENN_MCP_DEBUGRC.

DEBUGRC = os.environ.get("LOENN_MCP_DEBUGRC", "http://localhost:32270").rstrip("/")
_TAS_STATE = ("room={Level.Session.Level} deaths={Level.Session.Deaths} "
              "dashes={Player.Dashes} state={Player.StateMachine.State}")


def _rc(path: str, timeout: float = 30, **query) -> str:
    """GET a DebugRC endpoint; raises RuntimeError with a readable message if the game is not reachable."""
    url = DEBUGRC + path + ("?" + urllib.parse.urlencode(query) if query else "")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError(
            f"Cannot reach the game's debug server at {DEBUGRC} ({exc}). Start Celeste with "
            "Everest debug mode on, or set LOENN_MCP_DEBUGRC.") from exc


def _rc_text(page: str) -> str:
    """Visible text of a DebugRC HTML page, one item per line."""
    body = page[page.find("<body"):] if "<body" in page else page
    text = re.sub(r"<[^>]+>", "\n", body)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&").replace("&quot;", '"')
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _map_sid(map_path: str) -> str:
    """Everest SID of a workspace map: its path under Maps/ without the extension."""
    p = _resolve(map_path)
    try:
        rel = p.relative_to(WORKSPACE / "Maps")
    except ValueError:
        rel = Path(map_path)
    return rel.with_suffix("").as_posix()


def _session() -> dict[str, str]:
    fields = {}
    for line in _rc("/session").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    return fields


@tool()
def game_status() -> str:
    """Where the running game is: map SID, side, room and player position."""
    try:
        s = _session()
    except RuntimeError as exc:
        return str(exc)
    if not s.get("Area"):
        return f"Game reachable at {DEBUGRC}, but no level is loaded."
    return (f"Map: {s.get('Area')}  side {s.get('Side')}\n"
            f"Room: {s.get('Level')}\nPosition: ({s.get('X')}, {s.get('Y')})")


@tool()
def game_reload_map(map_path: str, side: str = "A") -> str:
    """Make the running game re-read a map after it was edited on disk.

    Edits to an existing map are picked up; a brand-new map file is only
    registered when the game restarts.

    Args:
        map_path: Path to the .bin file (relative to the workspace).
        side: A, B or C.
    """
    sid = _map_sid(map_path)
    try:
        _rc("/reloadmap", area=sid, side=side)
    except RuntimeError as exc:
        return str(exc)
    return f"Reload requested for {sid} ({side}-side)."


@tool()
def game_teleport(map_path: str, room: str, side: str = "A", x: int | None = None, y: int | None = None) -> str:
    """Load a room of a map in the running game (a fresh debug session).

    Args:
        map_path: Path to the .bin file (relative to the workspace).
        room: Room name, with or without 'lvl_'.
        side: A, B or C.
        x, y: Optional room-local position; the nearest spawn point is used.
    """
    sid = _map_sid(map_path)
    q = {"area": sid, "side": side, "level": _short_room(room)}
    if x is not None and y is not None:
        q.update(x=x, y=y)
    try:
        _rc("/tp", **q)
        time.sleep(1.5)
        s = _session()
    except RuntimeError as exc:
        return str(exc)
    if s.get("Area") != sid or s.get("Level") != _short_room(room):
        return (f"Teleport sent, but the game reports {s.get('Area')} / {s.get('Level')}. "
                "A map added since the game started needs a restart; check game_log for load errors.")
    return f"In {sid} room {s.get('Level')} at ({s.get('X')}, {s.get('Y')})."


@tool()
def game_play_tas(map_path: str, room: str, inputs: str, intro_frames: int = 40,
                  timeout_s: float = 60) -> str:
    """Play CelesteTAS inputs from a room's spawn and report where the player ended up.

    Use it to prove a room is beatable (or that a shortcut fails). Needs the
    CelesteTAS mod. The script loads the room, waits `intro_frames` for the
    spawn animation, then plays `inputs`, one `frames,keys` line each
    (e.g. "20,R" / "14,R,J" / "1,R,U,X"; R L U D = directions, J = jump,
    X = dash, G = grab). Hold a diagonal for ~6 frames after X so the dash
    takes its direction; a second dash needs ~12+ frames after the first.

    Args:
        map_path: Path to the .bin file (relative to the workspace).
        room: Room to start in.
        inputs: TAS input lines.
        intro_frames: Frames to wait after loading before inputs act.
        timeout_s: Give up after this many seconds.
    """
    sid = _map_sid(map_path)
    lines = [f"console load {sid} {_short_room(room)}", "   1"]
    if intro_frames > 0:
        lines.append(f"  {intro_frames}")
    lines += [ln.strip() for ln in inputs.strip().splitlines() if ln.strip()]
    import tempfile
    tas = Path(tempfile.gettempdir()) / "loenn_mcp_game_play.tas"
    tas.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    try:
        _rc("/tas/custominfo", template=_TAS_STATE)
        _rc("/tas/playtas", filePath=str(tas))
        start = time.time()
        started = False
        info = ""
        while time.time() - start < timeout_s:
            time.sleep(0.5)
            info = _rc_text(_rc("/tas/info"))
            running = "Running: True" in info
            cur = re.search(r"CurrentFrame: (\d+)", info)
            tot = re.search(r"TotalFrames: (\d+)", info)
            started |= running
            done = cur and tot and int(tot.group(1)) > 0 and int(cur.group(1)) >= int(tot.group(1))
            if started and (not running or done):
                break
            if not started and time.time() - start > 8:
                return "CelesteTAS did not start the script. Is the CelesteTAS mod installed and enabled?"
        else:
            return f"Timed out after {timeout_s:.0f}s.\n" + info
        _rc("/tas/sendhotkey", id="Start", action="press")  # stop playback
    except RuntimeError as exc:
        return str(exc)

    pos = re.search(r"Pos:\s+([-\d.]+),\s*([-\d.]+)", info)
    st = re.search(r"room=(\S*) deaths=(\d+) dashes=(.+?) state=", info)
    if not (pos and st):
        return "Played, but the game reported no player state:\n" + info[:800]
    return (f"Played {len(lines) - 2} input lines from {sid} / {_short_room(room)}.\n"
            f"  Ended in room: {st.group(1)}\n"
            f"  Position:      ({float(pos.group(1)):.1f}, {float(pos.group(2)):.1f})\n"
            f"  Deaths:        {st.group(2)}{'  (dead at the end)' if 'Dead' in info else ''}\n"
            f"  Dashes left:   {st.group(3) if st.group(3).strip().isdigit() else '-'}")


@tool()
def game_log(lines: int = 400, only_problems: bool = True) -> str:
    """Recent lines of the game's log.txt: errors, warnings and level loads.

    Args:
        lines: How many trailing log lines to scan.
        only_problems: Keep only errors, warnings about missing events or
            textures, failed map loads and level-load lines (default True).
    """
    root = _game_root()
    log = root / "log.txt" if root else None
    if not log or not log.exists():
        return "Game log.txt not found (the workspace is not inside a Celeste install)."
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    if only_problems:
        keep = re.compile(r"\[Error\]|Exception|Failed loading|Event not found|does not exist|LevelLoader")
        tail = [l for l in tail if keep.search(l) and "CONFLICT" not in l]
    return "\n".join(tail[-120:]) or "No problems in the scanned lines."


# ═══════════════════════════════════════════════════════════════════════════════
#  SAFETY: copies, commits, backups, diff
# ═══════════════════════════════════════════════════════════════════════════════

def _rel(p: Path) -> str:
    try:
        return Path(p).resolve().relative_to(WORKSPACE).as_posix()
    except ValueError:
        return str(p)


def _resolve_read(path_str: str) -> Path:
    """Like _resolve, but also accepts files in the backup store."""
    p = Path(path_str)
    if p.is_absolute():
        rp = p.resolve()
        try:
            rp.relative_to(safety.state_dir().resolve())
            return rp
        except ValueError:
            pass
    return _resolve(path_str)


def _default_copy_path(src: Path) -> Path:
    rel = Path(_rel(src))
    parts = rel.parts
    if len(parts) >= 4 and parts[0].lower() == "maps":
        folder = WORKSPACE / parts[0] / parts[1] / "wip"
    else:
        folder = src.parent
    dest = folder / f"{src.stem}.copy.bin"
    if dest.exists():
        dest = folder / f"{src.stem}.copy-{safety.timestamp()[:15]}.bin"
    return dest


@tool()
def map_copy(map_path: str, dest: str = "") -> str:
    """Copy a map to a working copy so edits never touch the original. Default target: Maps/<mod>/wip/<name>.copy.bin.

    Args:
        map_path: .bin to copy
        dest: Target .bin in the workspace; must not exist yet
    """
    src = _resolve(map_path)
    if not src.exists():
        return f"File not found: {map_path}"
    out = _resolve(dest) if dest else _default_copy_path(src)
    if out.exists():
        return f"Not copied: {_rel(out)} already exists. Pick another dest."
    data = src.read_bytes()
    _save_bytes(out, data)
    return (f"Copied {_rel(src)} -> {_rel(out)}. Edit the copy, check it with map_diff, then apply it with "
            f"map_commit(work_map='{_rel(out)}', target_map='{_rel(src)}').")


def _replace_rooms(target: dict, work: dict, names: list[str]) -> list[str]:
    """Put the named rooms from `work` into `target` (replace by name, or add). Returns a report."""
    levels = cb.find_child(target, "levels")
    report = []
    for name in names:
        room = authoring._find_room(work, name)
        if room is None:
            raise ValueError(f"room '{name}' is not in the work map")
        old = authoring._find_room(target, name)
        box = {k: room.get(k, 0) for k in ("x", "y", "width", "height")}
        for r in cb.get_rooms(target):
            if r is old:
                continue
            if authoring._overlaps(box, {k: r.get(k, 0) for k in ("x", "y", "width", "height")}):
                raise ValueError(f"room '{name}' would overlap '{r.get('name')}' in the target map")
        if old is not None:
            levels["__children"][levels["__children"].index(old)] = room
            report.append(f"replaced {mapdiff.short_name(str(room.get('name')))}")
        else:
            levels["__children"].append(room)
            report.append(f"added {mapdiff.short_name(str(room.get('name')))}")
    return report


@tool()
def map_commit(work_map: str, target_map: str, rooms: str = "", dry_run: bool = True,
               allow_issues: bool = False) -> str:
    """Apply a working copy to the real map: whole file, or only the listed rooms. Dry run shows the diff first.

    Args:
        work_map: The edited copy
        target_map: The map to update (a timestamped backup is kept)
        rooms: Comma-separated room names to take from the copy; empty = replace the whole map
        dry_run: Only show the diff and checks (default). Set false to write.
        allow_issues: Write even if the committed rooms fail validation (e.g. no player spawn)
    """
    work, target = _resolve(work_map), _resolve(target_map)
    for p, label in ((work, work_map), (target, target_map)):
        if not p.exists():
            return f"File not found: {label}"
    if work == target:
        return "work_map and target_map are the same file."
    target_bytes = target.read_bytes()
    try:
        if rooms.strip():
            names = [r.strip() for r in rooms.split(",") if r.strip()]
            tree = cb.read_map_bytes(target_bytes)
            report = _replace_rooms(tree, cb.read_map(work), names)
            payload = cb.serialize_map(tree)
            checked = [authoring._find_room(tree, n) for n in names]
        else:
            payload = work.read_bytes()
            report = [f"replace the whole map with {_rel(work)}"]
            checked = cb.get_rooms(cb.read_map_bytes(payload))
    except ValueError as exc:
        return f"Not committed: {exc}"
    issues = {}
    for room in checked:
        res = gdep_tools.validate_and_fix_room(json.loads(json.dumps(room, default=str)), auto_fix=False)
        if res["issues"]:
            issues[mapdiff.short_name(str(room.get("name")))] = res["issues"]
    d = mapdiff.diff_bytes(target_bytes, payload)
    lines = [f"{'DRY RUN, nothing written' if dry_run else 'COMMIT'}: {_rel(work)} -> {_rel(target)}",
             *[f"  {r}" for r in report], "", "Diff (target -> result):", d.text(limit=120)]
    if issues:
        lines.append("\nValidation issues in committed rooms:")
        lines += [f"  {n}: {'; '.join(i)}" for n, i in issues.items()]
    if dry_run:
        lines.append("\nRun again with dry_run=false to write.")
        return "\n".join(lines)
    if issues and not allow_issues:
        lines.append("\nNot written: fix the issues above, or pass allow_issues=true.")
        return "\n".join(lines)
    if target.read_bytes() != target_bytes:
        return "Not committed: the target changed on disk meanwhile. Run again."
    safety.save_bytes(target, payload, WORKSPACE, allow_protected=True, reason="commit")
    return "\n".join(lines)


@tool()
def map_backups(map_path: str, limit: int = 20) -> str:
    """List the timestamped backups of a map, newest first.

    Args:
        map_path: The .bin whose backups to list
        limit: Maximum entries
    """
    path = _resolve(map_path)
    found = safety.list_backups(path, WORKSPACE)
    if not found:
        return f"No backups of {_rel(path)} in {safety.backup_dir_for(path, WORKSPACE)}"
    lines = [f"{len(found)} backup(s) of {_rel(path)} (newest first):"]
    for b in found[:limit]:
        lines.append(f"  {b.name}  {b.stat().st_size} bytes")
    lines.append(f"Folder: {found[0].parent}")
    return "\n".join(lines)


@tool()
def map_restore(map_path: str, backup: str = "latest", dry_run: bool = True) -> str:
    """Restore a map from one of its backups (the current file is backed up first).

    Args:
        map_path: The .bin to restore
        backup: Backup file name from map_backups, or "latest"
        dry_run: Only show what would change (default)
    """
    path = _resolve(map_path)
    found = safety.list_backups(path, WORKSPACE)
    if not found:
        return f"No backups of {_rel(path)}."
    pick = found[0] if backup == "latest" else next((b for b in found if b.name == backup), None)
    if pick is None:
        return f"No backup named {backup}. Use map_backups to list them."
    data = pick.read_bytes()
    cur = path.read_bytes() if path.exists() else None
    diff = mapdiff.diff_bytes(cur, data).text(limit=80) if cur else "(map file is missing)"
    if dry_run:
        return f"DRY RUN: restoring {pick.name} would change {_rel(path)}:\n{diff}\nRun with dry_run=false."
    _save_bytes(path, data)
    return f"Restored {_rel(path)} from {pick.name}.\n{diff}"


@tool()
def map_diff(map_a: str, map_b: str = "", limit: int = 200) -> str:
    """Semantic diff of two maps: rooms, geometry, fg/bg tiles, entities, triggers, decals, stylegrounds, meta.

    Lönn re-encodes the whole file on save, so this compares meaning, not bytes.

    Args:
        map_a: The older map (or the map to compare with its latest backup when map_b is empty)
        map_b: The newer map; empty = compare map_a's latest backup -> map_a
        limit: Maximum output lines
    """
    a = _resolve_read(map_a)
    if not a.exists():
        return f"File not found: {map_a}"
    if map_b:
        b = _resolve_read(map_b)
        if not b.exists():
            return f"File not found: {map_b}"
        old, new, label = a, b, f"{_rel(a)} -> {_rel(b)}"
    else:
        found = safety.list_backups(a, WORKSPACE)
        if not found:
            return f"No backup of {_rel(a)} to compare with; pass map_b."
        old, new, label = found[0], a, f"backup {found[0].name} -> {_rel(a)}"
    d = mapdiff.diff_bytes(old.read_bytes(), new.read_bytes())
    return f"Diff {label}\n{d.text(limit=limit)}"


# ═══════════════════════════════════════════════════════════════════════════════
#  ASSETS (sprites first)
# ═══════════════════════════════════════════════════════════════════════════════

def _has_definition(name: str, kind: str) -> bool:
    return _catalog().get(name, "entities" if kind == "entity" else "triggers") is not None


@tool()
def check_assets(map_path: str, limit: int = 100) -> str:
    """Check that a map only uses things that exist: entity/trigger names with a Lönn definition, decal and parallax textures.

    Textures are looked up in this mod, every installed mod and the vanilla atlases (case-insensitive, like the game).

    Args:
        map_path: The .bin to check
        limit: Maximum findings to list
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    idx = assets.build_index(WORKSPACE)
    rep = assets.check_map_assets(cb.read_map(path), idx, _has_definition)
    lines = [f"Assets in {_rel(path)}: {rep.checked_names} object names, {rep.checked_textures} textures checked "
             f"against {idx.count()} known textures."]
    if rep.ok:
        lines.append("Everything exists.")
    for title, items in (("Names with no Lönn definition", rep.unknown_names),
                         ("Missing textures", rep.missing_textures)):
        if items:
            lines.append(f"{title} ({len(items)}):")
            lines += [f"  {i}" for i in items[:limit]]
    return "\n".join(lines)


@tool()
def check_sprites_xml(xml_path: str = "Graphics/Sprites.xml", limit: int = 100) -> str:
    """Check a Sprites.xml: it parses, ids are unique, and every Anim/Loop has frames in the atlas. Check this first when a sprite does not show in game.

    Args:
        xml_path: Sprites.xml in the workspace
        limit: Maximum findings to list
    """
    path = _resolve(xml_path)
    if not path.exists():
        return f"File not found: {xml_path}"
    idx = assets.build_index(WORKSPACE)
    n, issues = assets.check_sprites_xml(path, idx)
    lines = [f"{_rel(path)}: {n} sprites, {len(issues)} issue(s)."]
    by_sprite: dict[str, list] = {}
    for i in issues:
        by_sprite.setdefault(i.sprite, []).append(i)
    shown = 0
    for sprite, items in by_sprite.items():
        lines.append(f"  {sprite}:")
        for i in items:
            if shown >= limit:
                break
            lines.append(f"    {i.anim + ': ' if i.anim else ''}{i.problem}")
            shown += 1
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  EDITORS (Lönn now, Rysy later)
# ═══════════════════════════════════════════════════════════════════════════════

def _editor_session(session: str) -> "editors.EditorSession":
    if session:
        s = editors.SESSIONS.get(session)
        if s is None:
            raise editors.EditorError(f"no session '{session}'. Known: {', '.join(editors.SESSIONS) or 'none'}")
        return s
    if not editors.SESSIONS:
        raise editors.EditorError("no editor session; call editor_open first")
    running = [s for s in editors.SESSIONS.values() if s.running()]
    return (running or list(editors.SESSIONS.values()))[-1]


def _adapter_for(s: "editors.EditorSession") -> "editors.EditorAdapter":
    return editors.get_adapter(s.editor, WORKSPACE)


@tool()
def editor_open(map_path: str, room: str = "", editor: str = "loenn", storage: str = "isolated") -> str:
    """Open a map in Lönn (or Rysy), optionally on a room. MCP writes to that map are refused until it closes.

    Args:
        map_path: The .bin to open
        room: Room to select on load (Lönn only)
        editor: "loenn" or "rysy"
        storage: Lönn settings folder: "isolated" (own copy, default) or "user" (your normal Lönn)
    """
    path = _resolve(map_path)
    if not path.exists():
        return f"File not found: {map_path}"
    kw = {"storage": storage} if editor.lower().startswith(("l", "lö")) else {}
    adapter = editors.get_adapter(editor, WORKSPACE, **kw)
    s = adapter.open(path, room or None)
    lines = [f"Opened {_rel(path)} in {s.editor} (session {s.id}, pid {s.pid})" + (f" on room {room}" if room else "."),
             "Lönn takes ~20 s to load with many mods installed.",
             f"Call editor_wait(session='{s.id}') to wait for the user to save; it reports what changed.",
             "When done: editor_close. Until then MCP writes to this map are refused."]
    lines += [f"Note: {n}" for n in s.notes]
    return "\n".join(lines)


@tool()
def editor_wait(session: str = "", timeout_s: int = 120) -> str:
    """Wait until the user saves in the editor (or it closes), then report what changed. Call again to keep waiting.

    Args:
        session: Session id from editor_open; empty = the latest
        timeout_s: Seconds to wait, 1 to 600
    """
    s = _editor_session(session)
    adapter = _adapter_for(s)
    reason, ev = adapter.wait(s, max(1, min(600, timeout_s)))
    head = {"saved": "Saved.", "saved_and_exited": "Saved, and the editor closed.",
            "exited": "The editor closed without a new save.", "timeout": "No save yet (timed out)."}[reason]
    lines = [f"[{s.id}] {head}"]
    if ev:
        lines += ["Changes in this save:", ev.diff_text]
    lines.append(f"Saves this session: {len(s.saves)}. Editor running: {s.running()}.")
    return "\n".join(lines)


@tool()
def editor_status() -> str:
    """List editor sessions from this server and any other running Lönn."""
    lines = []
    for s in editors.SESSIONS.values():
        _adapter_for(s).poll(s)
        lines.append(f"{s.id}: {s.editor} pid {s.pid} {'running' if s.running() else 'closed'} on {_rel(s.map_path)}"
                     + (f" room {s.room}" if s.room else "") + f", {len(s.saves)} save(s)")
    ours = {s.pid for s in editors.SESSIONS.values() if s.running()}
    other = [p for p in editors.loenn.running_loenn(max_age=0) if p[1] not in ours]
    if other:
        lines.append("Other Lönn processes (not started here): " + ", ".join(f"pid {pid}" for _, pid in other))
    return "\n".join(lines) or "No editor sessions and no Lönn running."


@tool()
def editor_close(session: str = "", force: bool = False) -> str:
    """Close an editor session. Without force, the editor may ask about unsaved changes and stay open.

    Args:
        session: Session id; empty = the latest
        force: Kill the editor; unsaved editor changes are lost
    """
    s = _editor_session(session)
    adapter = _adapter_for(s)
    result = adapter.close(s, force=force)
    lines = [f"[{s.id}] {result}"]
    if not s.running():
        lines += ["Changes since editor_open:", adapter.diff_since_open(s)]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
#  SPRITE FUSION (gray-box rooms)
# ═══════════════════════════════════════════════════════════════════════════════

@tool()
def sf_make_tileset(out_dir: str = "") -> str:
    """Write the Celeste gray-box tileset for Sprite Fusion (PNG, legend JSON, README).

    Args:
        out_dir: Folder to write to (any absolute path); default <state dir>/sprite-fusion
    """
    out = Path(out_dir) if out_dir else safety.state_dir() / "sprite-fusion"
    files = sprite_fusion.make_tileset(out)
    return ("Wrote:\n" + "\n".join(f"  {f}" for f in files) +
            "\nImport celeste_graybox.png in Sprite Fusion with tile size 16. Tile order: "
            + ", ".join(sprite_fusion.GRAYBOX_ORDER) + ".")


def _auto_position(tree: dict, w: int, h: int) -> tuple[int, int]:
    rooms = cb.get_rooms(tree)
    if not rooms:
        return 0, 0
    right = max(r.get("x", 0) + r.get("width", 0) for r in rooms)
    top = min(r.get("y", 0) for r in rooms)
    return ((right + 64 + 7) // 8) * 8, (top // 8) * 8


@tool()
def sf_import(export_path: str, map_path: str, room: str, x: int | None = None, y: int | None = None,
              legend_path: str = "", replace: bool = False, output_path: str = "", dry_run: bool = True,
              allow_unknown: bool = False, allow_problems: bool = False) -> str:
    """Import a Sprite Fusion JSON export (map.zip) as a Celeste room: solid/bg tiles plus gray-box entities.

    One Sprite Fusion cell = one 8x8 tile. A new room is added; an existing one is only changed with replace=true
    (its fg/bg tiles and entities are replaced; triggers, decals and settings stay).

    Args:
        export_path: map.zip, its folder, or map.json (any absolute path)
        map_path: Map to import into (the base when output_path is set)
        room: Room name (lvl_ is added if the map's rooms use it)
        x: Room x in pixels for a new room; default: right of the existing rooms
        y: Room y in pixels for a new room
        legend_path: Custom legend JSON; default: the built-in gray-box legend
        replace: Replace an existing room's tiles and entities
        output_path: Write to this .bin instead of map_path (created from map_path if missing)
        dry_run: Only show the result (default)
        allow_unknown: Place names with no Lönn definition
        allow_problems: Write even when placement checks fail
    """
    src = _resolve(map_path)
    if not src.exists():
        return f"File not found: {map_path}"
    out = _resolve(output_path) if output_path else src
    try:
        exp = sprite_fusion.load_export(Path(export_path))
        legend = sprite_fusion.load_legend(Path(legend_path) if legend_path else None)
        conv = sprite_fusion.convert(exp, legend)
    except (sprite_fusion.SpriteFusionError, OSError, json.JSONDecodeError) as exc:
        return f"Cannot read the export: {exc}"
    base = out.read_bytes() if out.exists() else src.read_bytes()
    tree = cb.read_map_bytes(base)
    rooms = cb.get_rooms(tree)
    prefixed = bool(rooms) and all(str(r.get("name", "")).startswith("lvl_") for r in rooms)
    full = room if room.startswith("lvl_") or not prefixed else f"lvl_{room}"
    existing = authoring._find_room(tree, full)
    W, H = conv.width_tiles * 8, conv.height_tiles * 8
    try:
        if existing is None:
            if x is None or y is None:
                x, y = _auto_position(tree, W, H)
            new_bytes, res = authoring.merge_bytes(base, conv.room_doc(full, x, y), _catalog(),
                                                   allow_unknown=allow_unknown)
            detail = authoring.describe(res)
            problems = res.problems
            action = f"new room {full} at ({x}, {y}), {W}x{H}"
        else:
            if not replace:
                return (f"Room {existing.get('name')} already exists. Pass replace=true to replace its tiles and "
                        "entities, or pick another room name.")
            if (existing.get("width"), existing.get("height")) != (W, H):
                return (f"Room {existing.get('name')} is {existing.get('width')}x{existing.get('height')} px but the "
                        f"export is {W}x{H}. Resize one of them first (resize_room).")
            plan = authoring.resolve({"room": existing.get("name"), "entities": conv.entities}, _catalog())
            if plan.unknown and not allow_unknown:
                return ("Not imported: unknown names (no Lönn definition): "
                        + ", ".join(sorted({it.name for it in plan.unknown})))
            ents = cb.find_child(existing, "entities")
            if ents is None:
                ents = {"__name": "entities", "__children": []}
                existing["__children"].append(ents)
            removed = len(ents["__children"])
            keep_ids = authoring.map_ids(tree) - {e.get("id") for e in ents["__children"]}
            ids = authoring.IdAllocator(keep_ids)
            ents["__children"] = [authoring._element(it, ids.take(it.id, it.label())) for it in plan.items()]
            for child, rows in (("solids", conv.fg), ("bg", conv.bg)):
                el = cb.find_child(existing, child)
                if el is None:
                    el = {"__name": child, "offsetX": 0, "offsetY": 0, "__children": []}
                    existing["__children"].append(el)
                el["innerText"] = "\n".join(rows)
            reach = authoring._ReachCache(tree)
            problems = [f"{existing.get('name')}: {e.get('__name')} id={e.get('id')}: {p}"
                        for e in ents["__children"] for p in authoring.check_placement(existing, e, "entities", reach)]
            new_bytes = cb.serialize_map(tree)
            detail = f"replaced tiles and {removed} entit{'y' if removed == 1 else 'ies'} with {len(ents['__children'])}"
            action = f"replace room {existing.get('name')}"
    except (authoring.AuthoringError, ValueError) as exc:
        return f"Not imported: {exc}"
    lines = [f"{'DRY RUN, nothing written' if dry_run else 'IMPORT'}: {Path(export_path).name} -> {_rel(out)}: {action}",
             detail, "", "Preview (P player, ^ spikes, S spring, R refill, B berry, = jump-through, D dream block):",
             conv.preview()]
    lines += [f"Note: {n}" for n in conv.notes]
    if problems:
        lines.append("Placement problems:")
        lines += [f"  {p}" for p in problems]
    if dry_run:
        lines.append("\nRun again with dry_run=false to write.")
        return "\n".join(lines)
    if problems and not allow_problems:
        lines.append("\nNot written: fix the placement problems, or pass allow_problems=true.")
        return "\n".join(lines)
    _save_bytes(out, new_bytes)
    return "\n".join(lines)


# Hide the extra tools unless asked for (LOENN_MCP_TOOLS=all).
if os.environ.get("LOENN_MCP_TOOLS", "core").lower() != "all":
    mcp.disable(names=set(_EXTRA_TOOLS))


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    mcp.run()


if __name__ == "__main__":
    main()
