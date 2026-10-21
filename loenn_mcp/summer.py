"""
Summer Engine (a Godot 4.7 fork) as a gray-box room editor for Celeste.

Summer.exe runs a GDScript inside a headless editor and reports back:

    Summer.exe --path <project> --summer-editor-script <script.gd>
               --summer-editor-script-out <dir> --summer-editor-script-max <sec>

It waits for the first filesystem scan (imports), runs the EditorScript's
_run(), writes <dir>/result.json ({ok, output, errors, boot_errors, ...}) and
exits non-zero if the script fails. Requests go to the scripts through the
CELESTE_SUMMER_REQUEST environment variable (a JSON file path).

The project (default ~/Documents/CEL-Graybox, LOENN_MCP_SUMMER_PROJECT):
    celeste/celeste_graybox.png   the gray-box tileset (same as Sprite Fusion's)
    celeste/graybox_tileset.tres  TileSet: custom data "kind", collision on solids
    celeste/*.gd                  bridge scripts (rewritten on every run)
    rooms/_template.tscn          Room (Node2D) > bg, fg, gameplay TileMapLayers
    rooms/<room>.tscn             one scene per room

A room scene: root Node2D with metadata celeste_room (name), room_width_tiles,
room_height_tiles; TileMapLayers named "fg" (solid), "bg", and any other name
(gameplay tiles: player, spikes, ...); Marker2D nodes with metadata "entity"
(plus any entity data as metadata) for entities that are not gray-box tiles.
One tile = one 8x8 Celeste tile whatever the Godot tile size.

All mapping between Celeste and the scene happens here in Python; the
GDScripts only read and write cells and markers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from . import celeste_bin as cb
    from . import sprite_fusion as sf
except ImportError:  # run directly from source
    import celeste_bin as cb
    import sprite_fusion as sf


class SummerError(RuntimeError):
    """Summer is missing, a bridge script failed, or a request is invalid."""


# ─── Locations ────────────────────────────────────────────────────────────────

def find_exe() -> Path:
    env = os.environ.get("SUMMER_EXE")
    cands = [Path(env)] if env else []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(Path(local) / "SummerEngine" / "current" / "Summer.exe")
    for c in cands:
        if c.exists():
            return c
    raise SummerError("Summer.exe not found. Install Summer Engine, or set SUMMER_EXE. Looked in: "
                      + "; ".join(str(c) for c in cands))


def default_project() -> Path:
    env = os.environ.get("LOENN_MCP_SUMMER_PROJECT")
    return Path(env) if env else Path.home() / "Documents" / "CEL-Graybox"


# ─── Bridge scripts ───────────────────────────────────────────────────────────

_COMMON = r'''
static func read_request() -> Dictionary:
	var path := OS.get_environment("CELESTE_SUMMER_REQUEST")
	if path == "":
		push_error("CELESTE_SUMMER_REQUEST is not set")
		return {}
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		push_error("cannot read request " + path)
		return {}
	var data = JSON.parse_string(f.get_as_text())
	return data if data is Dictionary else {}

static func write_json(path: String, data) -> void:
	var f := FileAccess.open(path, FileAccess.WRITE)
	if f == null:
		push_error("cannot write " + path)
		return
	f.store_string(JSON.stringify(data, " "))
	f.close()

static func set_owner_recursive(node: Node, owner: Node) -> void:
	for c in node.get_children():
		c.owner = owner
		set_owner_recursive(c, owner)
'''

BUILD_GD = r'''@tool
extends EditorScript
# Builds celeste/graybox_tileset.tres and rooms/_template.tscn from celeste/kinds.json.

const Common = preload("res://celeste/common.gd")

func _run() -> void:
	var kinds: Array = JSON.parse_string(FileAccess.get_file_as_string("res://celeste/kinds.json"))
	var tex: Texture2D = load("res://celeste/celeste_graybox.png")
	if tex == null:
		push_error("celeste_graybox.png did not import")
		return
	var size := 16
	var ts := TileSet.new()
	ts.tile_size = Vector2i(size, size)
	ts.add_custom_data_layer()
	ts.set_custom_data_layer_name(0, "kind")
	ts.set_custom_data_layer_type(0, TYPE_STRING)
	ts.add_physics_layer()
	var src := TileSetAtlasSource.new()
	src.texture = tex
	src.texture_region_size = Vector2i(size, size)
	ts.add_source(src, 0)
	for i in kinds.size():
		var coords := Vector2i(i % 4, i / 4)
		src.create_tile(coords)
		var td := src.get_tile_data(coords, 0)
		td.set_custom_data("kind", kinds[i])
		if kinds[i] == "solid":
			var h := size / 2.0
			td.add_collision_polygon(0)
			td.set_collision_polygon_points(0, 0, PackedVector2Array([Vector2(-h, -h), Vector2(h, -h), Vector2(h, h), Vector2(-h, h)]))
	var err := ResourceSaver.save(ts, "res://celeste/graybox_tileset.tres")
	if err != OK:
		push_error("saving tileset failed: %d" % err)
		return
	print("BUILD tileset tiles=", kinds.size())

	var ts_loaded: TileSet = load("res://celeste/graybox_tileset.tres")
	var root := Node2D.new()
	root.name = "Room"
	root.set_meta("celeste_room", "new-room")
	root.set_meta("room_width_tiles", 40)
	root.set_meta("room_height_tiles", 23)
	for layer_name in ["bg", "fg", "gameplay"]:
		var layer := TileMapLayer.new()
		layer.name = layer_name
		layer.tile_set = ts_loaded
		if layer_name == "bg":
			layer.modulate = Color(1, 1, 1, 0.6)
			layer.z_index = -1
		if layer_name != "fg":
			layer.collision_enabled = false
		root.add_child(layer)
	var frame := ReferenceRect.new()
	frame.name = "ScreenFrame"
	frame.size = Vector2(40 * size, 23 * size)
	frame.border_color = Color(1, 0.8, 0.2)
	frame.editor_only = true
	frame.mouse_filter = Control.MOUSE_FILTER_IGNORE
	root.add_child(frame)
	Common.set_owner_recursive(root, root)
	var ps := PackedScene.new()
	ps.pack(root)
	err = ResourceSaver.save(ps, "res://rooms/_template.tscn")
	root.free()
	if err != OK:
		push_error("saving template failed: %d" % err)
		return
	print("BUILD template ok")
'''

EXPORT_GD = r'''@tool
extends EditorScript
# Dumps a room scene: every tile-map layer's cells and every entity marker.
# Request: {"scene": "res://rooms/x.tscn", "out": "<absolute json path>"}

const Common = preload("res://celeste/common.gd")

func _layer_cells(layer, cells_getter, layer_index) -> Array:
	var out := []
	var ts: TileSet = layer.tile_set
	var has_kind := ts != null and ts.get_custom_data_layer_by_name("kind") != -1
	var has_physics := ts != null and ts.get_physics_layers_count() > 0
	for c in cells_getter.call():
		var sid: int
		var ac: Vector2i
		var td: TileData
		if layer_index < 0:
			sid = layer.get_cell_source_id(c)
			ac = layer.get_cell_atlas_coords(c)
			td = layer.get_cell_tile_data(c)
		else:
			sid = layer.get_cell_source_id(layer_index, c)
			ac = layer.get_cell_atlas_coords(layer_index, c)
			td = layer.get_cell_tile_data(layer_index, c)
		var tex := ""
		if ts != null and ts.has_source(sid):
			var src = ts.get_source(sid)
			if src is TileSetAtlasSource and src.texture != null:
				tex = src.texture.resource_path
		var entry := {"x": c.x, "y": c.y, "source": sid, "ax": ac.x, "ay": ac.y, "texture": tex}
		if td != null:
			if has_kind:
				entry["kind"] = str(td.get_custom_data("kind"))
			if has_physics:
				entry["solid"] = td.get_collision_polygons_count(0) > 0
		out.append(entry)
	return out

func _walk(node: Node, origin: Node2D, layers: Array, markers: Array) -> void:
	for c in node.get_children():
		if c is TileMapLayer:
			var ts: TileSet = c.tile_set
			layers.append({"name": str(c.name), "collision": c.collision_enabled, "visible": c.visible,
				"tile_size": [ts.tile_size.x, ts.tile_size.y] if ts else [16, 16],
				"cells": _layer_cells(c, c.get_used_cells, -1)})
		elif c.get_class() == "TileMap":
			var ts2: TileSet = c.tile_set
			for li in c.get_layers_count():
				layers.append({"name": str(c.name) + "/" + c.get_layer_name(li), "collision": true, "visible": true,
					"tile_size": [ts2.tile_size.x, ts2.tile_size.y] if ts2 else [16, 16],
					"cells": _layer_cells(c, c.get_used_cells.bind(li), li)})
		if c is Node2D and c.has_meta("entity"):
			var meta := {}
			for k in c.get_meta_list():
				meta[str(k)] = c.get_meta(k)
			var p: Vector2 = origin.to_local(c.global_position)
			markers.append({"name": str(c.name), "x": p.x, "y": p.y, "meta": meta})
		_walk(c, origin, layers, markers)

func _run() -> void:
	var req := Common.read_request()
	var ps: PackedScene = load(req.get("scene", ""))
	if ps == null:
		push_error("cannot load scene " + str(req.get("scene", "")))
		return
	var root := ps.instantiate()
	var meta := {}
	for k in root.get_meta_list():
		meta[str(k)] = root.get_meta(k)
	var layers := []
	var markers := []
	_walk(root, root, layers, markers)
	Common.write_json(req["out"], {"scene": req["scene"], "root_name": str(root.name), "meta": meta,
		"layers": layers, "markers": markers})
	root.free()
	print("EXPORT layers=", layers.size(), " markers=", markers.size())
'''

IMPORT_GD = r'''@tool
extends EditorScript
# Builds a room scene from cells and markers prepared by loenn-mcp.
# Request: {"scene": "res://rooms/x.tscn", "room": name, "width": w, "height": h,
#           "layers": {"bg": [[x, y, index]...], "fg": [...], "gameplay": [...]},
#           "markers": [{"entity": name, "x": px, "y": py, "meta": {...}}], "overwrite": bool}

const Common = preload("res://celeste/common.gd")

func _run() -> void:
	var req := Common.read_request()
	var path: String = req["scene"]
	if ResourceLoader.exists(path) and not req.get("overwrite", false):
		push_error("scene exists: " + path)
		return
	var tmpl: PackedScene = load("res://rooms/_template.tscn")
	var root: Node2D = tmpl.instantiate()
	root.name = "Room"
	root.set_meta("celeste_room", req["room"])
	root.set_meta("room_width_tiles", int(req["width"]))
	root.set_meta("room_height_tiles", int(req["height"]))
	var size := 16
	for layer_name in req["layers"]:
		var layer: TileMapLayer = root.get_node_or_null(NodePath(layer_name))
		if layer == null:
			push_error("template has no layer " + layer_name)
			continue
		size = layer.tile_set.tile_size.x
		for cell in req["layers"][layer_name]:
			var i := int(cell[2])
			layer.set_cell(Vector2i(int(cell[0]), int(cell[1])), 0, Vector2i(i % 4, i / 4))
	var frame = root.get_node_or_null("ScreenFrame")
	if frame:
		frame.size = Vector2(int(req["width"]) * size, int(req["height"]) * size)
	var markers := Node2D.new()
	markers.name = "Entities"
	root.add_child(markers)
	for m in req["markers"]:
		var n := Marker2D.new()
		n.name = str(m["entity"]).replace("/", "_")
		n.position = Vector2(float(m["x"]), float(m["y"]))
		n.set_meta("entity", m["entity"])
		for k in m.get("meta", {}):
			n.set_meta(k, m["meta"][k])
		markers.add_child(n)
	Common.set_owner_recursive(root, root)
	var ps := PackedScene.new()
	ps.pack(root)
	var err := ResourceSaver.save(ps, path)
	root.free()
	if err != OK:
		push_error("saving %s failed: %d" % [path, err])
		return
	print("IMPORT saved ", path)
'''

PROJECT_GODOT = """; Celeste gray-box rooms for Summer Engine, managed by loenn-mcp.
config_version=5

[application]

config/name="CEL Graybox"
config/description="Gray-box Celeste rooms. Paint fg/bg/gameplay layers with the gray-box tileset; loenn-mcp turns rooms/*.tscn into Celeste rooms."

[rendering]

textures/canvas_textures/default_texture_filter=0
"""

README = """CEL Graybox (Summer Engine project managed by loenn-mcp)

Rooms live in rooms/. Duplicate rooms/_template.tscn (or let summer_import_room create one).
- Paint solid ground on the "fg" layer, background walls on "bg", and gameplay tiles
  (player, spikes, spring, refill, strawberry, jump-through, dream block) on "gameplay".
- One tile = one 8x8 Celeste tile. The orange frame is one Celeste screen (40x23).
- Room name and size are metadata on the root node: celeste_room, room_width_tiles, room_height_tiles.
- For an entity that has no gray-box tile, add a Marker2D with metadata "entity" (for example
  "CEL/RendaFeather") plus any entity fields as metadata. Unknown names are refused on import.
- Gray-box first: anything else you draw (decoration layers) is ignored by the importer.

Then ask the agent: summer_export_room(scene="rooms/<name>.tscn", map_path=..., room=...).
Files in celeste/ are rewritten by loenn-mcp; do not edit them.
"""


def _write_scripts(project: Path) -> None:
    d = project / "celeste"
    d.mkdir(parents=True, exist_ok=True)
    files = {"common.gd": "extends RefCounted\n" + _COMMON, "build_project.gd": BUILD_GD,
             "export_room.gd": EXPORT_GD, "import_room.gd": IMPORT_GD}
    for name, text in files.items():
        p = d / name
        if not p.exists() or p.read_text(encoding="utf-8") != text:
            p.write_text(text, encoding="utf-8")
    (d / "kinds.json").write_text(json.dumps(sf.GRAYBOX_ORDER), encoding="utf-8")
    png = d / "celeste_graybox.png"
    data, _, _ = sf.graybox_png()
    if not png.exists() or png.read_bytes() != data:
        png.write_bytes(data)


# ─── Running scripts ──────────────────────────────────────────────────────────

@dataclass
class ScriptResult:
    ok: bool
    output: list[str]
    errors: list[str]
    raw: dict


def run_script(project: Path, script: str, request: dict | None = None, timeout: int = 240) -> ScriptResult:
    """Run res://celeste/<script> in a headless Summer editor on `project`."""
    exe = find_exe()
    project = project.resolve()
    _write_scripts(project)
    with tempfile.TemporaryDirectory(prefix="summer-") as tmp:
        out_dir = Path(tmp) / "out"
        out_dir.mkdir()
        env = dict(os.environ)
        if request is not None:
            req = Path(tmp) / "request.json"
            req.write_text(json.dumps(request), encoding="utf-8")
            env["CELESTE_SUMMER_REQUEST"] = str(req)
        args = [str(exe), "--path", str(project), "--summer-editor-script", str(project / "celeste" / script),
                "--summer-editor-script-out", str(out_dir), "--summer-editor-script-max", str(timeout)]
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            proc = subprocess.run(args, env=env, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout + 60, creationflags=flags)
        except subprocess.TimeoutExpired as exc:
            raise SummerError(f"Summer did not finish {script} within {timeout + 60} s") from exc
        result_path = out_dir / "result.json"
        if not result_path.exists():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
            raise SummerError(f"Summer wrote no result for {script} (exit {proc.returncode}):\n{tail}")
        raw = json.loads(result_path.read_text(encoding="utf-8"))
    errors = list(raw.get("errors", [])) + list(raw.get("boot_errors", []))
    res = ScriptResult(bool(raw.get("ok")) and proc.returncode == 0 and not errors, list(raw.get("output", [])),
                       errors, raw)
    if not res.ok:
        raise SummerError(f"{script} failed: " + "; ".join(str(e) for e in errors or ["exit code %d" % proc.returncode]))
    return res


def init_project(project: Path | None = None) -> tuple[Path, list[str]]:
    project = (project or default_project()).resolve()
    project.mkdir(parents=True, exist_ok=True)
    notes = []
    if not (project / "project.godot").exists():
        (project / "project.godot").write_text(PROJECT_GODOT, encoding="utf-8")
        notes.append("created project.godot")
    (project / "rooms").mkdir(exist_ok=True)
    (project / "README.txt").write_text(README, encoding="utf-8")
    res = run_script(project, "build_project.gd")
    return project, notes + res.output


def ensure_project(project: Path | None = None) -> Path:
    project = (project or default_project()).resolve()
    if not (project / "celeste" / "graybox_tileset.tres").exists() or not (project / "rooms" / "_template.tscn").exists():
        init_project(project)
    return project


def scene_res_path(project: Path, scene: str) -> str:
    """res:// path for a scene given as res://..., a project-relative path, or a bare room name."""
    if scene.startswith("res://"):
        return scene
    p = Path(scene)
    if p.is_absolute():
        try:
            return "res://" + p.resolve().relative_to(project.resolve()).as_posix()
        except ValueError as exc:
            raise SummerError(f"{scene} is not inside the Summer project {project}") from exc
    if p.suffix != ".tscn":
        p = Path("rooms") / f"{scene}.tscn"
    return "res://" + p.as_posix()


def list_rooms(project: Path) -> list[str]:
    return sorted(p.stem for p in (project / "rooms").glob("*.tscn") if not p.stem.startswith("_"))


# ─── Scene -> Celeste ─────────────────────────────────────────────────────────

_KIND_INDEX = {k: i for i, k in enumerate(sf.GRAYBOX_ORDER)}
_FG_NAMES = ("fg", "solid", "solids", "collision", "blocks", "ground", "terrain", "walls")


def export_scene(project: Path, scene: str, timeout: int = 240) -> dict:
    project = ensure_project(project)
    res_path = scene_res_path(project, scene)
    if not (project / res_path[len("res://"):]).exists():
        raise SummerError(f"no scene {res_path}. Rooms: {', '.join(list_rooms(project)) or 'none'}")
    with tempfile.TemporaryDirectory(prefix="summer-export-") as tmp:
        out = Path(tmp) / "room.json"
        run_script(project, "export_room.gd", {"scene": res_path, "out": str(out)}, timeout)
        return json.loads(out.read_text(encoding="utf-8"))


def dump_to_export(dump: dict) -> tuple[sf.Export, list[str]]:
    """Scene dump -> a Sprite Fusion-shaped export (ids = gray-box indexes), so sprite_fusion.convert does the rest."""
    notes: list[str] = []
    meta = dump.get("meta", {})
    cells_all = [c for layer in dump["layers"] for c in layer["cells"]]
    w = int(meta.get("room_width_tiles") or 0) or (max((c["x"] for c in cells_all), default=39) + 1)
    h = int(meta.get("room_height_tiles") or 0) or (max((c["y"] for c in cells_all), default=22) + 1)
    layers = []
    outside = 0
    for layer in dump["layers"]:
        lname = layer["name"].split("/")[-1]
        is_fg = lname.strip().lower() in _FG_NAMES
        tiles = []
        for c in layer["cells"]:
            if not (0 <= c["x"] < w and 0 <= c["y"] < h):
                outside += 1
                continue
            kind = c.get("kind") or ""
            if not kind and "celeste_graybox" in c.get("texture", ""):
                idx = c["ay"] * 4 + c["ax"]
                kind = sf.GRAYBOX_ORDER[idx] if 0 <= idx < len(sf.GRAYBOX_ORDER) else ""
            if kind in _KIND_INDEX:
                tid = str(_KIND_INDEX[kind])
            elif c.get("solid"):
                tid = str(_KIND_INDEX["solid"])
            else:
                tid = f"ext:{c.get('texture', '')}:{c['ax']},{c['ay']}"
            tiles.append({"id": tid, "x": c["x"], "y": c["y"]})
        layers.append({"name": lname, "collider": is_fg, "tiles": tiles})
    if outside:
        notes.append(f"{outside} tile(s) outside the {w}x{h} room were ignored (negative or past the size in the "
                     "root metadata)")
    tile = 16
    for layer in dump["layers"]:
        tile = int(layer.get("tile_size", [16])[0]) or 16
        break
    marker_tiles = []
    for m in dump.get("markers", []):
        data = {k: v for k, v in m["meta"].items() if k not in ("entity", "_edit_lock_", "_edit_group_")}
        name = str(m["meta"]["entity"])
        px, py = round(m["x"] * 8 / tile), round(m["y"] * 8 / tile)
        marker_tiles.append({"id": "marker", "x": max(0, px // 8), "y": max(0, py // 8),
                             "attributes": {"entity": name, "px": px, "py": py, **data}})
    if marker_tiles:
        layers.append({"name": "markers", "collider": False, "tiles": marker_tiles})
    doc = {"tileSize": tile, "mapWidth": w, "mapHeight": h, "layers": layers}
    return sf.Export(doc, None, dump.get("scene", "")), notes


def scene_conversion(dump: dict) -> tuple["sf.Conversion", str]:
    export, notes = dump_to_export(dump)
    conv = sf.convert(export, sf.load_legend(None), by_id_ok=True)
    conv.notes = notes + conv.notes
    room = str(dump.get("meta", {}).get("celeste_room") or "")
    return conv, room


# ─── Celeste -> scene ─────────────────────────────────────────────────────────

_ANCHOR_CELL = {
    "feet": lambda x, y: (x // 8, (y - 1) // 8),
    "bottomleft": lambda x, y: (x // 8, (y - 1) // 8),
    "center": lambda x, y: (x // 8, y // 8),
    "topleft": lambda x, y: (x // 8, y // 8),
    "topright": lambda x, y: ((x - 1) // 8, y // 8),
}


def room_instructions(room: dict, tile: int = 16) -> tuple[dict, list[str]]:
    """Cells and markers for a Celeste room element (fg/bg tiles, gray-box entities as tiles, others as markers)."""
    notes: list[str] = []
    w, h = int(room.get("width", 320)) // 8, int(room.get("height", 184)) // 8
    layers: dict[str, list[list[int]]] = {"fg": [], "bg": [], "gameplay": []}
    for child, layer, kind in (("solids", "fg", "solid"), ("bg", "bg", "bg")):
        el = cb.find_child(room, child)
        rows = (el.get("innerText", "") if el else "").split("\n")
        for y, row in enumerate(rows[:h]):
            for x, ch in enumerate(row[:w]):
                if ch not in "0 ":
                    layers[layer].append([x, y, _KIND_INDEX[kind]])
    by_entity = {spec["entity"]: k for k, spec in sf.KINDS.items() if "entity" in spec}
    markers = []
    ents = cb.find_child(room, "entities")
    for e in ents.get("__children", []) if ents else []:
        name = str(e.get("__name"))
        kind = by_entity.get(name)
        x, y = int(e.get("x", 0)), int(e.get("y", 0))
        if kind:
            spec = sf.KINDS[kind]
            cx, cy = _ANCHOR_CELL[spec.get("anchor", "topleft")](x, y)
            if spec.get("merge") == "rect":
                cw, chh = max(1, int(e.get("width", 8)) // 8), max(1, int(e.get("height", 8)) // 8)
                cells = [(x // 8 + i, y // 8 + j) for j in range(chh) for i in range(cw)]
            elif spec.get("merge") == "h":
                cells = [(cx + i, cy) for i in range(max(1, int(e.get("width", 8)) // 8))]
            elif spec.get("merge") == "v":
                cells = [(cx, cy + i) for i in range(max(1, int(e.get("height", 8)) // 8))]
            else:
                cells = [(cx, cy)]
            layers["gameplay"] += [[a, b, _KIND_INDEX[kind]] for a, b in cells if 0 <= a < w and 0 <= b < h]
            continue
        meta = {k: v for k, v in e.items() if k not in ("__name", "__children", "id", "x", "y")
                and isinstance(v, (str, int, float, bool))}
        markers.append({"entity": name, "x": x * tile / 8, "y": y * tile / 8, "meta": meta})
    if any(e for e in (cb.find_child(room, "triggers") or {}).get("__children", [])):
        notes.append("triggers are not shown in Summer; they stay in the .bin and are kept on replace")
    return {"width": w, "height": h, "layers": layers, "markers": markers}, notes


def import_room(project: Path, scene: str, room_name: str, instructions: dict, overwrite: bool = False,
                timeout: int = 240) -> str:
    project = ensure_project(project)
    res_path = scene_res_path(project, scene)
    if (project / res_path[len("res://"):]).exists() and not overwrite:
        raise SummerError(f"{res_path} already exists; pass overwrite=true to replace it")
    req = {"scene": res_path, "room": room_name, "overwrite": overwrite, **instructions}
    run_script(project, "import_room.gd", req, timeout)
    return res_path


# ─── Editor ───────────────────────────────────────────────────────────────────

def open_editor(project: Path, scene: str | None = None) -> subprocess.Popen:
    exe = find_exe()
    project = ensure_project(project)
    args = [str(exe), "--path", str(project), "--editor"]
    if scene:
        args.append(scene_res_path(project, scene))
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
    return subprocess.Popen(args, cwd=str(exe.parent), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, creationflags=flags)


def remove_project_scripts(project: Path) -> None:  # used by tests
    shutil.rmtree(project / "celeste", ignore_errors=True)
