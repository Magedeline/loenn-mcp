"""
Sprite Fusion (spritefusion.com) gray-box rooms -> Celeste rooms.

Sprite Fusion exports `map.zip` = `map.json` + `spritesheet.png`:

    {"tileSize": 16, "mapWidth": 40, "mapHeight": 23,
     "layers": [{"name": "Blocks", "collider": true,
                 "tiles": [{"id": "0", "x": 3, "y": 22}, ...]}, ...]}

x/y are tile units from the top-left; `id` indexes the exported spritesheet,
left to right, top to bottom. One Sprite Fusion cell becomes one 8x8 Celeste
tile whatever the tileSize.

Because ids index the *exported* sheet, tiles are matched to the gray-box
tileset by their pixels when the spritesheet is present (needs Pillow), and by
id otherwise. The default tileset is generated here (`make_tileset`): import
`celeste_graybox.png` into Sprite Fusion and paint with it.

What each tile becomes is set by the legend (built in, or a JSON file):
  solid      -> foreground tile (char from the legend, default "1")
  bg         -> background tile
  player, spikes*, spring, refill, strawberry, jumpThru, dreamBlock -> vanilla entities
Spikes and jump-throughs merge along runs, dream blocks merge into rectangles.

Unmatched tiles on a collider layer count as solid foreground. Unmatched tiles
on other layers are decoration and are ignored (gray-box first, decorate
last); the report says how many. A tile can also carry Sprite Fusion
attributes: `entity` (an entity name), `anchor`, and any entity data.
"""

from __future__ import annotations

import io
import json
import struct
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class SpriteFusionError(ValueError):
    """The export or legend cannot be converted. The message says why."""


# ─── Built-in gray-box legend ─────────────────────────────────────────────────

# kind -> what it becomes. anchor: where the entity's x/y goes for tile (x, y):
#   feet (bottom centre), center, topleft, bottomleft, topright.
# merge: "h" (horizontal runs -> width), "v" (vertical runs -> height), "rect".
KINDS: dict[str, dict[str, Any]] = {
    "solid":       {"layer": "fg"},
    "bg":          {"layer": "bg"},
    "player":      {"entity": "player", "anchor": "feet"},
    "spikesUp":    {"entity": "spikesUp", "anchor": "bottomleft", "merge": "h"},
    "spikesDown":  {"entity": "spikesDown", "anchor": "topleft", "merge": "h"},
    "spikesLeft":  {"entity": "spikesLeft", "anchor": "topright", "merge": "v"},
    "spikesRight": {"entity": "spikesRight", "anchor": "topleft", "merge": "v"},
    "spring":      {"entity": "spring", "anchor": "feet"},
    "refill":      {"entity": "refill", "anchor": "center"},
    "strawberry":  {"entity": "strawberry", "anchor": "center"},
    "jumpThru":    {"entity": "jumpThru", "anchor": "topleft", "merge": "h"},
    "dreamBlock":  {"entity": "dreamBlock", "anchor": "topleft", "merge": "rect"},
}
GRAYBOX_ORDER = list(KINDS)          # tileset index -> kind
GRAYBOX_TILE = 16

DEFAULT_LEGEND: dict[str, Any] = {
    "format": "celeste-sf-legend", "version": 1,
    "tileset": "celeste_graybox.png", "tileSize": GRAYBOX_TILE,
    "fg_char": "1", "bg_char": "1",
    "tiles": {str(i): k for i, k in enumerate(GRAYBOX_ORDER)},
    "kinds": {},
}


# ─── PNG (writer always available, reader via Pillow) ─────────────────────────

def write_png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _read_png(data: bytes) -> tuple[int, int, bytes]:
    try:
        from PIL import Image
    except ImportError as exc:
        raise SpriteFusionError("Pillow is needed to read spritesheet.png (pip install Pillow)") from exc
    im = Image.open(io.BytesIO(data)).convert("RGBA")
    return im.width, im.height, im.tobytes()


def _pillow_available() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


# ─── Gray-box tileset art ─────────────────────────────────────────────────────

def _graybox_tile(kind: str, n: int = GRAYBOX_TILE) -> list[list[tuple[int, int, int, int]]]:
    """Simple, distinct 16x16 pictures so tiles are readable in the editor and unique for matching."""
    T = (0, 0, 0, 0)
    px = [[T for _ in range(n)] for _ in range(n)]

    def rect(x0, y0, x1, y1, c):
        for y in range(max(0, y0), min(n, y1)):
            for x in range(max(0, x0), min(n, x1)):
                px[y][x] = c

    def tri(direction, c):
        # Two spikes per tile. `along` runs parallel to the wall, `out` is the distance from it.
        half = n // 2
        for along in range(n):
            local = along % half
            height = int((half - abs(2 * local - (half - 1))) * (n - 3) / half) + 1
            for out in range(2, 2 + height):
                if out >= n:
                    break
                y, x = {"up": (n - 1 - out, along), "down": (out, along),
                        "left": (along, n - 1 - out), "right": (along, out)}[direction]
                px[y][x] = c

    if kind == "solid":
        rect(0, 0, n, n, (110, 110, 118, 255)); rect(1, 1, n - 1, n - 1, (150, 150, 160, 255))
        rect(4, 4, 6, 6, (110, 110, 118, 255)); rect(10, 9, 12, 11, (110, 110, 118, 255))
    elif kind == "bg":
        rect(0, 0, n, n, (52, 58, 82, 255))
        for k in range(0, n, 4):
            rect(k, k, k + 2, k + 2, (70, 78, 108, 255))
    elif kind == "player":
        rect(5, 2, 11, 7, (240, 140, 120, 255)); rect(4, 7, 12, 13, (80, 160, 230, 255))
        rect(5, 13, 7, 16, (60, 60, 70, 255)); rect(9, 13, 11, 16, (60, 60, 70, 255))
        rect(4, 1, 12, 3, (200, 40, 40, 255))
    elif kind.startswith("spikes"):
        tri(kind[6:].lower(), (230, 230, 240, 255))
        edge = {"up": (0, n - 2, n, n), "down": (0, 0, n, 2), "left": (n - 2, 0, n, n), "right": (0, 0, 2, n)}[kind[6:].lower()]
        rect(*edge, (200, 40, 60, 255))
    elif kind == "spring":
        rect(2, 10, 14, 16, (120, 120, 120, 255)); rect(3, 8, 13, 10, (250, 200, 40, 255))
        rect(6, 10, 10, 14, (200, 200, 200, 255))
    elif kind == "refill":
        for y in range(n):
            w = n // 2 - abs(y - n // 2)
            rect(n // 2 - w + 1, y, n // 2 + w - 1, y + 1, (60, 220, 100, 255))
        rect(7, 7, 9, 9, (230, 255, 230, 255))
    elif kind == "strawberry":
        for y in range(n):
            for x in range(n):
                if (x - 7.5) ** 2 + (y - 9) ** 2 <= 30:
                    px[y][x] = (220, 40, 60, 255)
        rect(6, 2, 10, 5, (60, 180, 60, 255)); rect(5, 8, 6, 9, (255, 230, 120, 255)); rect(9, 11, 10, 12, (255, 230, 120, 255))
    elif kind == "jumpThru":
        rect(0, 0, n, 4, (150, 100, 50, 255)); rect(0, 4, n, 5, (90, 60, 30, 255))
        for k in range(2, n, 6):
            rect(k, 5, k + 1, 9, (90, 60, 30, 255))
    elif kind == "dreamBlock":
        rect(0, 0, n, n, (12, 12, 20, 255))
        for (x, y, c) in ((3, 3, (255, 255, 255, 255)), (11, 5, (255, 120, 220, 255)), (6, 11, (120, 220, 255, 255)),
                          (13, 13, (255, 240, 120, 255))):
            rect(x, y, x + 1, y + 1, c)
        rect(0, 0, n, 1, (255, 255, 255, 255)); rect(0, n - 1, n, n, (255, 255, 255, 255))
        rect(0, 0, 1, n, (255, 255, 255, 255)); rect(n - 1, 0, n, n, (255, 255, 255, 255))
    else:
        rect(0, 0, n, n, (255, 0, 255, 255))
    return px


def graybox_png(columns: int = 4) -> tuple[bytes, int, int]:
    n = GRAYBOX_TILE
    count = len(GRAYBOX_ORDER)
    rows = (count + columns - 1) // columns
    w, h = columns * n, rows * n
    buf = bytearray(w * h * 4)
    for i, kind in enumerate(GRAYBOX_ORDER):
        tile = _graybox_tile(kind)
        ox, oy = (i % columns) * n, (i // columns) * n
        for y in range(n):
            for x in range(n):
                o = ((oy + y) * w + ox + x) * 4
                buf[o:o + 4] = bytes(tile[y][x])
    return write_png(w, h, bytes(buf)), w, h


def make_tileset(out_dir: Path) -> list[Path]:
    """Write celeste_graybox.png + celeste_graybox.legend.json + a short README into out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)
    png, w, h = graybox_png()
    (out_dir / "celeste_graybox.png").write_bytes(png)
    legend = dict(DEFAULT_LEGEND)
    legend["columns"] = 4
    legend["kinds_doc"] = {k: v for k, v in KINDS.items()}
    (out_dir / "celeste_graybox.legend.json").write_text(json.dumps(legend, indent=1), encoding="utf-8")
    readme = (
        "Celeste gray-box tileset for Sprite Fusion\n\n"
        "1. In Sprite Fusion, import celeste_graybox.png as a tileset with tile size 16.\n"
        "2. Make a map 40 wide x 23 high (one screen; any multiple works). One cell = one 8x8 Celeste tile.\n"
        "3. Paint solid ground on a layer with Collider on, background walls on a layer named bg,\n"
        "   and gameplay tiles (player, spikes, spring, refill, strawberry, jump-through, dream block) on any layer.\n"
        "4. Export as JSON (map.zip), then call sf_import with the zip path.\n\n"
        "Tile order (left to right, top to bottom, 4 per row): " + ", ".join(GRAYBOX_ORDER) + "\n")
    (out_dir / "README.txt").write_text(readme, encoding="utf-8")
    return [out_dir / "celeste_graybox.png", out_dir / "celeste_graybox.legend.json", out_dir / "README.txt"]


# ─── Reading an export ────────────────────────────────────────────────────────

@dataclass
class Export:
    doc: dict
    spritesheet: bytes | None
    source: str


def load_export(path: Path) -> Export:
    """A Sprite Fusion export: map.zip, a folder holding map.json, or map.json itself."""
    path = Path(path)
    if not path.exists():
        raise SpriteFusionError(f"not found: {path}")
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            names = {Path(n).name.lower(): n for n in z.namelist()}
            if "map.json" not in names:
                raise SpriteFusionError(f"{path.name} has no map.json (is it a Sprite Fusion JSON export?)")
            doc = json.loads(z.read(names["map.json"]).decode("utf-8-sig"))
            sheet = z.read(names["spritesheet.png"]) if "spritesheet.png" in names else None
    else:
        folder = path if path.is_dir() else path.parent
        mp = path if path.is_file() else folder / "map.json"
        if not mp.exists():
            raise SpriteFusionError(f"no map.json in {folder}")
        doc = json.loads(mp.read_text(encoding="utf-8-sig"))
        sp = folder / "spritesheet.png"
        sheet = sp.read_bytes() if sp.exists() else None
    for k in ("tileSize", "mapWidth", "mapHeight", "layers"):
        if k not in doc:
            raise SpriteFusionError(f"map.json has no '{k}'; this does not look like a Sprite Fusion JSON export")
    return Export(doc, sheet, str(path))


def load_legend(path: Path | None) -> dict:
    if path is None:
        return dict(DEFAULT_LEGEND)
    leg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if "tiles" not in leg:
        raise SpriteFusionError("legend needs a 'tiles' object: {\"<tileset index>\": \"<kind>\"}")
    out = dict(DEFAULT_LEGEND)
    out.update(leg)
    out["_dir"] = str(Path(path).parent)
    return out


# ─── Matching export ids to legend kinds ──────────────────────────────────────

def _tiles_of(width: int, height: int, rgba: bytes, size: int) -> list[bytes]:
    cols, rows = width // size, height // size
    out = []
    for ty in range(rows):
        for tx in range(cols):
            rows_bytes = []
            for y in range(size):
                o = ((ty * size + y) * width + tx * size) * 4
                rows_bytes.append(rgba[o:o + size * 4])
            out.append(b"".join(rows_bytes))
    return out


def _resample(tile: bytes, src: int, dst: int) -> bytes:
    if src == dst:
        return tile
    out = bytearray(dst * dst * 4)
    for y in range(dst):
        for x in range(dst):
            sx, sy = x * src // dst, y * src // dst
            o = (sy * src + sx) * 4
            out[(y * dst + x) * 4:(y * dst + x) * 4 + 4] = tile[o:o + 4]
    return bytes(out)


def _distance(a: bytes, b: bytes) -> float:
    """Mean per-channel difference; fully transparent pixels compare equal whatever their colour."""
    total = 0
    n = len(a) // 4
    for i in range(0, len(a), 4):
        if a[i + 3] == 0 and b[i + 3] == 0:
            continue
        total += abs(a[i] - b[i]) + abs(a[i + 1] - b[i + 1]) + abs(a[i + 2] - b[i + 2]) + abs(a[i + 3] - b[i + 3])
    return total / (n * 4)


def reference_tiles(legend: dict) -> tuple[list[bytes], int] | None:
    """Reference tile pixels for the legend's tileset (the built-in gray-box is generated in memory)."""
    name = legend.get("tileset")
    size = int(legend.get("tileSize", GRAYBOX_TILE))
    if name == "celeste_graybox.png" and "_dir" not in legend:
        png, w, h = graybox_png()
        return _tiles_of(w, h, _read_png(png)[2] if _pillow_available() else _decode_own(png, w, h), size), size
    if name and legend.get("_dir") and (Path(legend["_dir"]) / name).exists():
        if name == "celeste_graybox.png":
            png, w, h = graybox_png()
            return _tiles_of(w, h, _decode_own(png, w, h), size), size
        w, h, rgba = _read_png((Path(legend["_dir"]) / name).read_bytes())
        return _tiles_of(w, h, rgba, size), size
    return None


def _decode_own(png: bytes, w: int, h: int) -> bytes:
    """Decode a PNG written by write_png (filter 0, RGBA)."""
    pos, idat = 8, b""
    while pos < len(png):
        ln = struct.unpack(">I", png[pos:pos + 4])[0]
        tag = png[pos + 4:pos + 8]
        if tag == b"IDAT":
            idat += png[pos + 8:pos + 8 + ln]
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4 + 1
    return b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(h))


def map_ids(export: Export, legend: dict, notes: list[str]) -> dict[str, str]:
    """Export tile id -> legend kind ('' when unmatched)."""
    tiles = {str(k): v for k, v in legend.get("tiles", {}).items()}
    used = sorted({str(t.get("id")) for layer in export.doc["layers"] for t in layer.get("tiles", [])},
                  key=lambda s: int(s) if s.isdigit() else 1 << 30)
    ref = None
    if export.spritesheet is not None:
        if _pillow_available():
            ref = reference_tiles(legend)
            if ref is None:
                notes.append("legend has no reference tileset image, so tiles are matched by id")
        else:
            notes.append("Pillow is not installed, so tiles are matched by id (pip install Pillow for pixel matching)")
    else:
        notes.append("export has no spritesheet.png, so tiles are matched by id")
    if ref is None:
        return {i: tiles.get(i, "") for i in used}
    ref_tiles, ref_size = ref
    size = int(export.doc["tileSize"])
    w, h, rgba = _read_png(export.spritesheet)
    sheet = _tiles_of(w, h, rgba, size)
    out: dict[str, str] = {}
    for i in used:
        if not i.isdigit() or int(i) >= len(sheet):
            out[i] = ""
            continue
        t = sheet[int(i)]
        best, best_d = None, 1e9
        for j, r in enumerate(ref_tiles):
            d = _distance(t, _resample(r, ref_size, size))
            if d < best_d:
                best, best_d = j, d
        out[i] = tiles.get(str(best), "") if best is not None and best_d <= 6.0 else ""
    return out


# ─── Conversion ───────────────────────────────────────────────────────────────

def _anchor(anchor: str, x: int, y: int) -> tuple[int, int]:
    return {
        "feet": (x * 8 + 4, (y + 1) * 8),
        "center": (x * 8 + 4, y * 8 + 4),
        "topleft": (x * 8, y * 8),
        "bottomleft": (x * 8, (y + 1) * 8),
        "topright": ((x + 1) * 8, y * 8),
    }.get(anchor, (x * 8, y * 8))


def _attrs(tile: dict) -> dict[str, Any]:
    a = tile.get("attributes")
    if isinstance(a, dict):
        return dict(a)
    if isinstance(a, list):
        out = {}
        for item in a:
            if isinstance(item, dict):
                k = item.get("key", item.get("name"))
                if k is not None:
                    out[str(k)] = item.get("value")
        return out
    return {}


@dataclass
class Conversion:
    width_tiles: int
    height_tiles: int
    fg: list[str]
    bg: list[str]
    entities: list[dict]
    notes: list[str] = field(default_factory=list)
    unmatched: dict[str, int] = field(default_factory=dict)
    decoration: int = 0
    attrs: dict[str, Any] = field(default_factory=dict)   # extra level attributes (e.g. generator seed)

    def room_doc(self, name: str, x: int | None = None, y: int | None = None) -> dict:
        d: dict[str, Any] = {"room": name, "solids": "\n".join(self.fg), "bg": "\n".join(self.bg),
                             "entities": self.entities}
        if x is not None and y is not None:
            d.update({"x": x, "y": y, "width": self.width_tiles * 8, "height": self.height_tiles * 8})
            if self.attrs:
                d["attrs"] = dict(self.attrs)
        return d

    def preview(self) -> str:
        grid = [list(r.replace("0", ".")) for r in self.fg]
        for e in self.entities:
            ex, ey = int(e["x"]) // 8, max(0, (int(e["y"]) - 1) // 8)
            if 0 <= ey < len(grid) and 0 <= ex < len(grid[0]):
                grid[ey][ex] = {"player": "P", "spring": "S", "refill": "R", "strawberry": "B",
                                "jumpThru": "=", "dreamBlock": "D"}.get(e["name"], "^" if "spikes" in e["name"] else "E")
        return "\n".join("".join(r) for r in grid)


def convert(export: Export, legend: dict, by_id_ok: bool = False) -> Conversion:
    """by_id_ok: the export's ids already are legend indexes (Summer scenes), so no matching note."""
    doc = export.doc
    W, H = int(doc["mapWidth"]), int(doc["mapHeight"])
    if W <= 0 or H <= 0:
        raise SpriteFusionError("map has no size")
    notes: list[str] = []
    kinds_by_id = map_ids(export, legend, [] if by_id_ok else notes)
    kind_specs = dict(KINDS)
    kind_specs.update(legend.get("kinds", {}))
    fg = [["0"] * W for _ in range(H)]
    bg = [["0"] * W for _ in range(H)]
    marks: dict[str, set[tuple[int, int]]] = {}
    singles: list[dict] = []
    conv = Conversion(W, H, [], [], [], notes)

    for layer in doc["layers"]:
        lname = str(layer.get("name", ""))
        collider = bool(layer.get("collider"))
        is_bg_layer = lname.strip().lower().startswith("bg") or "background" in lname.lower()
        for t in layer.get("tiles", []):
            x, y = int(t.get("x", -1)), int(t.get("y", -1))
            if not (0 <= x < W and 0 <= y < H):
                notes.append(f"tile at ({x},{y}) in layer '{lname}' is outside the {W}x{H} map; skipped")
                continue
            attrs = _attrs(t)
            if attrs.get("entity"):
                name = str(attrs.pop("entity"))
                anchor = str(attrs.pop("anchor", "topleft"))
                ex, ey = _anchor(anchor, x, y)
                if "px" in attrs and "py" in attrs:          # exact Celeste pixels (Summer markers)
                    ex, ey = int(attrs.pop("px")), int(attrs.pop("py"))
                singles.append({"name": name, "x": ex, "y": ey, **attrs})
                continue
            kind = kinds_by_id.get(str(t.get("id")), "")
            spec = kind_specs.get(kind) if kind else None
            if spec is None:
                if collider:
                    fg[y][x] = legend.get("fg_char", "1")
                elif is_bg_layer:
                    bg[y][x] = legend.get("bg_char", "1")
                else:
                    conv.decoration += 1
                    conv.unmatched[str(t.get("id"))] = conv.unmatched.get(str(t.get("id")), 0) + 1
                continue
            if spec.get("layer") == "fg":
                fg[y][x] = spec.get("char", legend.get("fg_char", "1"))
            elif spec.get("layer") == "bg":
                bg[y][x] = spec.get("char", legend.get("bg_char", "1"))
            elif spec.get("entity"):
                marks.setdefault(kind, set()).add((x, y))

    for kind, cells in marks.items():
        spec = kind_specs[kind]
        name, anchor, merge = spec["entity"], spec.get("anchor", "topleft"), spec.get("merge")
        extra = {k: v for k, v in spec.items() if k not in ("entity", "anchor", "merge", "layer", "char")}
        if merge == "h":
            for (x0, y0, n) in _runs(cells, horizontal=True):
                ex, ey = _anchor(anchor, x0, y0)
                conv.entities.append({"name": name, "x": ex, "y": ey, "width": n * 8, **extra})
        elif merge == "v":
            for (x0, y0, n) in _runs(cells, horizontal=False):
                ex, ey = _anchor(anchor, x0, y0)
                conv.entities.append({"name": name, "x": ex, "y": ey, "height": n * 8, **extra})
        elif merge == "rect":
            for (x0, y0, w, h) in _rects(cells):
                conv.entities.append({"name": name, "x": x0 * 8, "y": y0 * 8, "width": w * 8, "height": h * 8, **extra})
        else:
            for (x, y) in sorted(cells, key=lambda c: (c[1], c[0])):
                ex, ey = _anchor(anchor, x, y)
                conv.entities.append({"name": name, "x": ex, "y": ey, **extra})
    conv.entities += singles
    conv.entities.sort(key=lambda e: (e["name"] != "player", e["y"], e["x"]))
    conv.fg = ["".join(r) for r in fg]
    conv.bg = ["".join(r) for r in bg]
    if conv.decoration:
        notes.append(f"{conv.decoration} decoration tile(s) on non-collider layers were ignored "
                     f"(ids {', '.join(sorted(conv.unmatched))}); decorate in Lönn after the gray-box works")
    if not any(e["name"] == "player" for e in conv.entities):
        notes.append("no player spawn painted; Celeste treats a spawn-less room as a dummy room")
    if W < 40 or H < 23:
        notes.append(f"map is {W}x{H} tiles; a Celeste screen is 40x23, smaller rooms show void around them")
    return conv


def _runs(cells: set[tuple[int, int]], horizontal: bool) -> list[tuple[int, int, int]]:
    out = []
    seen: set[tuple[int, int]] = set()
    for (x, y) in sorted(cells, key=(lambda c: (c[1], c[0])) if horizontal else (lambda c: (c[0], c[1]))):
        if (x, y) in seen:
            continue
        n = 0
        while ((x + n, y) if horizontal else (x, y + n)) in cells:
            seen.add((x + n, y) if horizontal else (x, y + n))
            n += 1
        out.append((x, y, n))
    return out


def _rects(cells: set[tuple[int, int]]) -> list[tuple[int, int, int, int]]:
    left = set(cells)
    out = []
    while left:
        x, y = min(left, key=lambda c: (c[1], c[0]))
        w = 0
        while (x + w, y) in left:
            w += 1
        h = 1
        while all((x + i, y + h) in left for i in range(w)):
            h += 1
        for j in range(h):
            for i in range(w):
                left.discard((x + i, y + j))
        out.append((x, y, w, h))
    return out


def write_export(path: Path, width: int, height: int, layers: list[dict], tile_size: int = GRAYBOX_TILE) -> Path:
    """Write a Sprite Fusion-shaped map.zip using the gray-box tileset as the spritesheet (tests, demos)."""
    png, _, _ = graybox_png()
    doc = {"tileSize": tile_size, "mapWidth": width, "mapHeight": height, "layers": layers}
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("map.json", json.dumps(doc))
        z.writestr("spritesheet.png", png)
    return path
