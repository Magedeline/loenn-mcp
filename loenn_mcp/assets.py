"""
Sprites-first checks: does every texture a map or Sprites.xml needs exist?

Texture sources, in order: the workspace mod's Graphics/Atlases/<atlas>/,
every other installed mod (folders and .zip files next to it), and the
vanilla atlas index (Content/Graphics/Atlases/<atlas>.meta, parsed directly).

Atlas lookups follow Celeste: a key matches a texture with exactly that name,
or that name followed only by digits (animation frames, e.g. idle00).
"""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import celeste_bin as cb
except ImportError:  # run directly from source
    import celeste_bin as cb


# ─── Vanilla atlas index ──────────────────────────────────────────────────────

def read_atlas_meta(path: Path) -> list[str]:
    """Texture names in a Monocle packed-atlas .meta file (forward slashes, no extension)."""
    d = path.read_bytes()
    pos = 0

    def rint():
        nonlocal pos
        pos += 4

    def rshort() -> int:
        nonlocal pos
        v = struct.unpack_from("<h", d, pos)[0]
        pos += 2
        return v

    def rstr() -> str:
        nonlocal pos
        n = s = 0
        while True:
            b = d[pos]
            pos += 1
            n |= (b & 0x7F) << s
            s += 7
            if not b & 0x80:
                break
        v = d[pos:pos + n].decode("utf-8")
        pos += n
        return v

    rint()
    rstr()
    rint()
    names: list[str] = []
    for _ in range(rshort()):
        rstr()
        for _ in range(rshort()):
            names.append(rstr().replace("\\", "/"))
            pos += 16
    return names


# ─── Texture index ────────────────────────────────────────────────────────────

_PNG_IN_ATLAS = re.compile(r"(?:^|/)Graphics/Atlases/([^/]+)/(.+)\.png$", re.I)


_TRAILING_DIGITS = re.compile(r"\d+$")


@dataclass
class TextureIndex:
    by_atlas: dict[str, dict[str, str]] = field(default_factory=dict)   # atlas -> name -> source label
    frames: dict[str, dict[str, list[str]]] = field(default_factory=dict)  # atlas -> name minus trailing digits -> names

    # Monocle atlases look textures up case-insensitively, so the index does too.
    def add(self, atlas: str, name: str, source: str) -> None:
        a, name = atlas.lower(), name.replace("\\", "/").lower()
        self.by_atlas.setdefault(a, {}).setdefault(name, source)
        base = _TRAILING_DIGITS.sub("", name)
        if base != name:
            self.frames.setdefault(a, {}).setdefault(base, []).append(name)

    def find(self, key: str, atlas: str = "Gameplay") -> str | None:
        """Source of a texture key (exact, or followed only by frame digits), else None."""
        a, key = atlas.lower(), key.replace("\\", "/").lower()
        names = self.by_atlas.get(a, {})
        if key in names:
            return names[key]
        for n in self.frames.get(a, {}).get(_TRAILING_DIGITS.sub("", key), []):
            if n.startswith(key) and n[len(key):].isdigit():
                return names[n]
        return None

    def count(self) -> int:
        return sum(len(v) for v in self.by_atlas.values())


def _scan_folder(index: TextureIndex, root: Path, label: str) -> None:
    atlases = root / "Graphics" / "Atlases"
    if not atlases.is_dir():
        return
    for png in atlases.rglob("*.png"):
        rel = png.relative_to(atlases).as_posix()
        atlas, _, name = rel.partition("/")
        if name:
            index.add(atlas, name[:-4], label)


def _scan_zip(index: TextureIndex, zpath: Path) -> None:
    try:
        with zipfile.ZipFile(zpath) as z:
            for n in z.namelist():
                m = _PNG_IN_ATLAS.search(n)
                if m:
                    index.add(m.group(1), m.group(2), zpath.name)
    except (zipfile.BadZipFile, OSError):
        pass


def game_root_for(workspace: Path) -> Path | None:
    for p in [workspace, *workspace.parents]:
        if (p / "Content" / "Graphics" / "Atlases").is_dir():
            return p
    return None


_INDEX_CACHE: dict[str, TextureIndex] = {}


def build_index(workspace: Path, installed: bool = True, vanilla: bool = True, refresh: bool = False) -> TextureIndex:
    key = f"{workspace.resolve()}|{installed}|{vanilla}"
    if key in _INDEX_CACHE and not refresh:
        return _INDEX_CACHE[key]
    idx = TextureIndex()
    _scan_folder(idx, workspace, "mod")
    if installed and workspace.parent.name.lower() == "mods":
        for other in sorted(workspace.parent.iterdir()):
            if other.resolve() == workspace.resolve():
                continue
            if other.is_dir():
                _scan_folder(idx, other, other.name)
            elif other.suffix.lower() == ".zip":
                _scan_zip(idx, other)
    root = game_root_for(workspace)
    if vanilla and root:
        for meta in (root / "Content" / "Graphics" / "Atlases").glob("*.meta"):
            try:
                for n in read_atlas_meta(meta):
                    idx.add(meta.stem, n, "vanilla")
            except Exception:
                pass
    _INDEX_CACHE[key] = idx
    return idx


# ─── Sprites.xml ──────────────────────────────────────────────────────────────

@dataclass
class SpriteIssue:
    sprite: str
    anim: str
    key: str
    problem: str


def check_sprites_xml(xml_path: Path, index: TextureIndex) -> tuple[int, list[SpriteIssue]]:
    """(sprites checked, issues). Every Anim/Loop needs at least one frame in the atlas."""
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        return 0, [SpriteIssue("<file>", "", "", f"XML does not parse: {exc}")]
    issues: list[SpriteIssue] = []
    ids = [el.tag for el in root]
    for dup in sorted({t for t in ids if ids.count(t) > 1}):
        issues.append(SpriteIssue(dup, "", "", f"sprite id defined {ids.count(dup)} times (the last one wins)"))
    by_id = {el.tag: el for el in root}
    checked = 0
    for el in root:
        checked += 1
        path = el.get("path")
        copy = el.get("copy")
        if copy is not None and copy not in by_id:
            issues.append(SpriteIssue(el.tag, "", copy, f"copy='{copy}' names no sprite in this file "
                                      "(fine only if it is a vanilla sprite id)"))
        if path is None:
            if copy is None:
                issues.append(SpriteIssue(el.tag, "", "", "no path attribute"))
            continue
        atlas = el.get("atlas", "Gameplay")
        for anim in el:
            if anim.tag not in ("Anim", "Loop"):
                continue
            key = path + (anim.get("path") or "")
            if index.find(key, atlas) is None:
                issues.append(SpriteIssue(el.tag, anim.get("id", "?"), key,
                                          f"no texture '{key}' (or '{key}00'...) in atlas {atlas}"))
    return checked, issues


# ─── Map textures and names ───────────────────────────────────────────────────

@dataclass
class MapAssetReport:
    missing_textures: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)
    checked_textures: int = 0
    checked_names: int = 0

    @property
    def ok(self) -> bool:
        return not (self.missing_textures or self.unknown_names)


def check_map_assets(tree: dict, index: TextureIndex, has_definition) -> MapAssetReport:
    """Decal and parallax textures exist; every entity/trigger name has a Lönn definition.

    has_definition(name, kind) -> bool, kind in {"entity", "trigger"}.
    """
    rep = MapAssetReport()
    seen_tex: set[tuple[str, str]] = set()
    seen_names: set[tuple[str, str]] = set()
    for room in cb.get_rooms(tree):
        rname = str(room.get("name", "?"))
        for section, atlas_key in (("fgdecals", "decals/"), ("bgdecals", "decals/")):
            el = cb.find_child(room, section)
            for d in el.get("__children", []) if el else []:
                tex = str(d.get("texture", "")).replace("\\", "/")
                tex = tex[:-4] if tex.lower().endswith(".png") else tex
                key = tex if tex.startswith("decals/") else atlas_key + tex
                if (key, rname) in seen_tex:
                    continue
                seen_tex.add((key, rname))
                rep.checked_textures += 1
                if index.find(key) is None:
                    rep.missing_textures.append(f"{rname}: {section[:-1]} {key}")
        for section, kind in (("entities", "entity"), ("triggers", "trigger")):
            el = cb.find_child(room, section)
            for o in el.get("__children", []) if el else []:
                name = str(o.get("__name", ""))
                if (name, kind) in seen_names:
                    continue
                seen_names.add((name, kind))
                rep.checked_names += 1
                if not has_definition(name, kind):
                    rep.unknown_names.append(f"{kind} {name} (first in {rname})")
    style = cb.find_child(tree, "Style")
    for layer in ("Backgrounds", "Foregrounds"):
        el = cb.find_child(style, layer) if style else None
        stack = list(el.get("__children", [])) if el else []
        while stack:
            s = stack.pop()
            stack += s.get("__children", [])
            if s.get("__name") == "parallax" and s.get("texture"):
                key = str(s["texture"]).replace("\\", "/")
                key = key[:-4] if key.lower().endswith(".png") else key
                rep.checked_textures += 1
                if index.find(key) is None and index.find(key, "Misc") is None:
                    rep.missing_textures.append(f"styleground parallax {key}")
    return rep
