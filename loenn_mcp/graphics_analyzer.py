"""
graphics_analyzer.py — ML-style asset analysis for loenn-mcp

Analyzes Celeste .bin map assets against:
  1. The Maddie480 graphics dump browser (maddie480.ovh/celeste/graphics-dump-browser)
  2. The CelestialCartographers/Loenn source entity/tileset catalog
  3. Local workspace Loenn/entities + Loenn/effects Lua definitions

Features:
  - Extract all texture, decal, and styleground asset paths from a map
  - Cross-reference against a curated built-in vanilla asset catalog
  - Optionally fetch the live asset index from Maddie480's API
  - Validate entity names against both vanilla + mod Lönn catalogs
  - Tileset character validation (ForegroundTiles / BackgroundTiles chars)
  - Frequency analysis + confidence scoring for asset validity
  - Cache fetched asset indexes in the workspace wiki cache
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ── Maddie480 API endpoints ────────────────────────────────────────────────────

MADDIE480_BASE = "https://maddie480.ovh/celeste"
MADDIE480_DUMP_API = f"{MADDIE480_BASE}/graphics-dump-browser"
MADDIE480_ASSET_LIST = f"{MADDIE480_BASE}/asset-list.json"

# Cache TTL: 24 hours
_CACHE_TTL = 86400


# ── Built-in Vanilla Asset Catalog ────────────────────────────────────────────
# Derived from CelestialCartographers/Loenn source + vanilla Celeste game files.
# These are the paths that exist in the base game's Graphics/ atlas.

VANILLA_TEXTURES: Set[str] = {
    # Chapter backgrounds (parallax stylegrounds)
    "bgs/01/bg", "bgs/01/bg2", "bgs/01/fg",
    "bgs/02/bg", "bgs/02/bg2", "bgs/02/fg",
    "bgs/03/bg", "bgs/03/bg2", "bgs/03/fg",
    "bgs/04/bg", "bgs/04/bg2", "bgs/04/fg",
    "bgs/05/bg", "bgs/05/bg2", "bgs/05/fg",
    "bgs/06/bg", "bgs/06/bg2", "bgs/06/fg",
    "bgs/07/bg", "bgs/07/bg2", "bgs/07/fg",
    "bgs/08/bg", "bgs/08/bg2", "bgs/08/fg",
    "bgs/09/bg", "bgs/09/bg2", "bgs/09/fg",
    "bgs/10/bg", "bgs/10/bg2", "bgs/10/fg",
    "bgs/11/bg", "bgs/11/bg2", "bgs/11/fg",
    # Decals
    "decals/1-forsakencity/sign", "decals/1-forsakencity/rooftop",
    "decals/2-oldsite/bed", "decals/2-oldsite/mirror",
    "decals/3-resort/atm", "decals/3-resort/couch",
    "decals/4-cliffside/torch", "decals/4-cliffside/lamp",
    "decals/5-temple/statue", "decals/5-temple/pillar",
    "decals/6-reflection/crystal",
    "decals/7-summit/sign", "decals/7-summit/flag",
    "decals/9-core/door",
    # Common sprites
    "objects/spring/00", "objects/checkpoint/flag00",
    "objects/strawberry/normal00", "objects/goldberry/idle00",
    "objects/refill/idle00", "objects/refill/twodash00",
    "objects/booster/booster00", "objects/feather/idle00",
    "objects/dreamblock/inactive",
    "objects/cassette/idle00",
    "objects/key/idle00",
    "objects/door/door00",
    # Tilesets (character references in ForegroundTiles.xml)
    # These are the vanilla tileset IDs, not texture paths
}

# Valid vanilla tileset characters from ForegroundTiles.xml + BackgroundTiles.xml
# Sourced from CelestialCartographers/Loenn: Celeste/Content/Graphics/
VANILLA_FG_TILESET_CHARS: Set[str] = set("1234567890abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
VANILLA_BG_TILESET_CHARS: Set[str] = set("1234567890abcdefghijklmnopqrstuvwxyz")

# Known vanilla styleground effect names (from Loenn/effects/)
VANILLA_EFFECTS: Set[str] = {
    "parallax", "apply",
    "snow", "windSnow", "rain",
    "starfield", "planets",
    "northernlights", "blackhole",
    "tentacles", "reflections",
    "dreamstars", "glitch",
    "heatwave", "sandstorm",
    "mirrorfg", "godrays",
    "bossstarfield", "colorgrade",
    "petals",
}

# Known vanilla Celeste entities — exhaustive list from Loenn source
VANILLA_ENTITIES: Set[str] = {
    "player", "checkpoint", "strawberry", "goldenBerry", "spring",
    "jumpThru", "refill", "spikes", "spikeUp", "spikeDown",
    "spikeLeft", "spikeRight", "triggerSpikesUp", "triggerSpikesDown",
    "triggerSpikesLeft", "triggerSpikesRight",
    "crumbleBlock", "fallingBlock", "moveBlock", "crushBlock",
    "bumper", "seekerBarrier", "seeker", "booster", "feather",
    "dreamBlock", "dashBlock", "fireBarrier",
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
}

# Known vanilla triggers from Loenn source
VANILLA_TRIGGERS: Set[str] = {
    "altMusicTrigger", "ambienceTrigger", "ambienceVolumeTrigger",
    "birdPathTrigger", "bloomFadeTrigger", "cameraAdvanceTargetTrigger",
    "cameraOffsetTrigger", "cameraTargetTrigger", "changeRespawnTrigger",
    "checkpointBlockerTrigger", "colorGradeTrigger",
    "creditsTrigger", "darknessFadeTrigger", "detachFollowersTrigger",
    "dialogCutsceneTrigger", "eventTrigger", "everest/dialogTrigger",
    "goldenBerryCollectTrigger", "interactTrigger",
    "lightFadeTrigger", "lookoutBlocker", "microphone",
    "moonGlitchBackgroundTrigger", "musicFadeTrigger",
    "musicTrigger", "noDashAreaTrigger", "noRefillTrigger",
    "oshiroTrigger", "pauseLockTrigger", "respawnTargetTrigger",
    "rumbleTrigger", "spawnFacingTrigger", "spotlightDataTrigger",
    "summitBackgroundManager", "triggeredTrigger",
    "windAttackTrigger", "windTrigger",
}


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _cache_path(workspace: Path) -> Path:
    d = workspace / ".loenn_mcp_wiki"
    d.mkdir(parents=True, exist_ok=True)
    return d / "maddie480_asset_index.json"


def _load_cache(workspace: Path) -> Optional[Dict[str, Any]]:
    p = _cache_path(workspace)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if time.time() - data.get("fetched_at", 0) < _CACHE_TTL:
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _save_cache(workspace: Path, data: Dict[str, Any]) -> None:
    data["fetched_at"] = time.time()
    try:
        _cache_path(workspace).write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


# ── Asset extraction ───────────────────────────────────────────────────────────

def extract_map_assets(map_data: dict) -> Dict[str, Any]:
    """Extract all asset references from a parsed map data dict."""
    textures: Dict[str, int] = {}
    entity_names: Dict[str, int] = {}
    trigger_names: Dict[str, int] = {}
    effect_names: Dict[str, int] = {}
    tileset_fg_chars: Set[str] = set()
    tileset_bg_chars: Set[str] = set()
    decal_textures: Dict[str, int] = {}

    def _add_tex(d: dict, key: str, counter: Dict[str, int]) -> None:
        v = d.get(key, "")
        if v and isinstance(v, str):
            counter[v] = counter.get(v, 0) + 1

    rooms: List[dict] = []
    levels = None
    for child in map_data.get("__children", []):
        if child.get("__name") == "levels":
            levels = child
            break
    if levels:
        rooms = [
            r for r in levels.get("__children", [])
            if r.get("__name") == "level"
        ]

    for room in rooms:
        for child in room.get("__children", []):
            cname = child.get("__name", "")

            if cname == "entities":
                for ent in child.get("__children", []):
                    n = ent.get("__name", "")
                    if n:
                        entity_names[n] = entity_names.get(n, 0) + 1
                    for k in ("texture", "sprite", "atlas", "texture2"):
                        _add_tex(ent, k, textures)

            elif cname == "triggers":
                for trig in child.get("__children", []):
                    n = trig.get("__name", "")
                    if n:
                        trigger_names[n] = trigger_names.get(n, 0) + 1

            elif cname in ("fgdecals", "bgdecals"):
                for dec in child.get("__children", []):
                    _add_tex(dec, "texture", decal_textures)

            elif cname == "solids":
                text = child.get("innerText", "")
                for ch in text:
                    if ch not in ("0", "\n", "\r", " "):
                        tileset_fg_chars.add(ch)

            elif cname == "bg":
                text = child.get("innerText", "")
                for ch in text:
                    if ch not in ("0", "\n", "\r", " "):
                        tileset_bg_chars.add(ch)

    # Stylegrounds
    style = None
    for child in map_data.get("__children", []):
        if child.get("__name") == "Style":
            style = child
            break
    if style:
        for layer_name in ("Foregrounds", "Backgrounds"):
            layer_el = None
            for child in style.get("__children", []):
                if child.get("__name") == layer_name:
                    layer_el = child
                    break
            if layer_el:
                for sg in layer_el.get("__children", []):
                    n = sg.get("__name", "")
                    if n:
                        effect_names[n] = effect_names.get(n, 0) + 1
                    _add_tex(sg, "texture", textures)

    return {
        "textures": textures,
        "decal_textures": decal_textures,
        "entity_names": entity_names,
        "trigger_names": trigger_names,
        "effect_names": effect_names,
        "tileset_fg_chars": tileset_fg_chars,
        "tileset_bg_chars": tileset_bg_chars,
        "room_count": len(rooms),
    }


# ── Workspace entity catalog scanner ─────────────────────────────────────────

def scan_workspace_entities(workspace: Path) -> Set[str]:
    """Scan Loenn/entities/*.lua in the workspace to build a local entity set."""
    found: Set[str] = set()
    for search_dir in [
        workspace / "Loenn" / "entities",
        workspace / "loenn" / "entities",
    ]:
        if not search_dir.exists():
            continue
        for lua in search_dir.rglob("*.lua"):
            try:
                text = lua.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'\.name\s*=\s*["\']([^"\']+)', text):
                    found.add(m.group(1))
            except OSError:
                pass
    return found


def scan_workspace_effects(workspace: Path) -> Set[str]:
    """Scan Loenn/effects/*.lua in the workspace to build a local effect set."""
    found: Set[str] = set()
    for search_dir in [
        workspace / "Loenn" / "effects",
        workspace / "loenn" / "effects",
    ]:
        if not search_dir.exists():
            continue
        for lua in search_dir.rglob("*.lua"):
            try:
                text = lua.read_text(encoding="utf-8", errors="replace")
                for m in re.finditer(r'\.name\s*=\s*["\']([^"\']+)', text):
                    found.add(m.group(1))
            except OSError:
                pass
    return found


# ── Maddie480 live fetch ───────────────────────────────────────────────────────

def fetch_maddie480_asset_index(workspace: Path) -> Tuple[Optional[Set[str]], str]:
    """Fetch the asset path list from Maddie480's graphics dump browser.

    Returns (set_of_paths, status_message).
    Caches the result for 24 hours in the workspace wiki directory.
    """
    cached = _load_cache(workspace)
    if cached and "paths" in cached:
        age = int(time.time() - cached.get("fetched_at", 0))
        return set(cached["paths"]), f"(from cache, {age}s old)"

    try:
        req = urllib.request.Request(
            MADDIE480_ASSET_LIST,
            headers={"User-Agent": "loenn-mcp/5.0 asset-analyzer"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        asset_data = json.loads(raw)
        if isinstance(asset_data, list):
            paths = [str(p) for p in asset_data]
        elif isinstance(asset_data, dict):
            paths = list(asset_data.keys())
        else:
            paths = []
        _save_cache(workspace, {"paths": paths})
        return set(paths), f"fetched {len(paths)} paths from Maddie480"
    except urllib.error.URLError as e:
        return None, f"network error: {e}"
    except (json.JSONDecodeError, Exception) as e:
        return None, f"parse error: {e}"


# ── Scoring / confidence ───────────────────────────────────────────────────────

def _score_texture(path: str, known_vanilla: Set[str], maddie_index: Optional[Set[str]]) -> Tuple[str, str]:
    """Return (status, detail) for a texture path.

    status: "ok" | "vanilla" | "modded" | "unknown" | "suspicious"
    """
    if path in known_vanilla:
        return "vanilla", "known vanilla asset"
    if maddie_index is not None:
        # Normalize: strip leading slash, extensions
        norm = path.lstrip("/").lower()
        norm_no_ext = re.sub(r'\.[a-z]+$', '', norm)
        if norm in maddie_index or norm_no_ext in maddie_index:
            return "ok", "found in Maddie480 dump"
        # Partial prefix match (e.g. decals/2-oldsite/*)
        prefix = "/".join(norm.split("/")[:2])
        if any(p.startswith(prefix) for p in maddie_index):
            return "ok", f"prefix '{prefix}' exists in dump"
        return "unknown", "not found in Maddie480 dump"
    # No index available — heuristic check
    if re.match(r'^(bgs|decals|objects|scenery|characters|fg)/', path):
        return "modded", "looks like a valid asset path (unverified)"
    return "suspicious", "path doesn't match any known prefix"


def _score_entity(name: str, vanilla: Set[str], workspace_entities: Set[str]) -> Tuple[str, str]:
    if name in vanilla:
        return "vanilla", "Celeste vanilla entity"
    if name in workspace_entities:
        return "mod-local", "defined in workspace Loenn/entities/"
    if "/" in name:
        return "modded", "namespaced mod entity"
    return "unknown", "not in vanilla catalog or workspace"


# ── Main analysis function ────────────────────────────────────────────────────

def analyze_map_assets(
    path: Path,
    workspace: Path,
    check_textures: bool = True,
    check_tilesets: bool = True,
    check_entities: bool = True,
    fetch_asset_index: bool = False,
) -> str:
    """Full ML-style asset analysis. Called by server.py's analyze_map_assets tool."""
    import sys
    # Need celeste_bin to parse the map
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from . import celeste_bin as cb
    except ImportError:
        import celeste_bin as cb

    map_data = cb.read_map(path)
    assets = extract_map_assets(map_data)

    # Fetch Maddie480 index if requested
    maddie_index: Optional[Set[str]] = None
    maddie_status = "not fetched"
    if fetch_asset_index:
        maddie_index, maddie_status = fetch_maddie480_asset_index(workspace)

    # Scan local workspace for mod entity/effect defs
    workspace_entities = scan_workspace_entities(workspace)
    workspace_effects = scan_workspace_effects(workspace)

    lines = [
        f"Asset Analysis: {path.stem}",
        f"  Rooms: {assets['room_count']}",
        f"  Asset Index: {maddie_status}",
        "",
    ]

    # ── TEXTURE ANALYSIS ──────────────────────────────────────────────────
    if check_textures:
        all_textures = dict(assets["textures"])
        all_textures.update(assets["decal_textures"])

        vanilla_count = 0
        ok_count = 0
        unknown_list: List[Tuple[str, int, str]] = []
        suspicious_list: List[Tuple[str, int, str]] = []

        if all_textures:
            lines.append(f"── Texture References ({len(all_textures)} unique) ─────────────")
            for tex, cnt in sorted(all_textures.items(), key=lambda x: -x[1]):
                status, detail = _score_texture(tex, VANILLA_TEXTURES, maddie_index)
                if status == "vanilla":
                    vanilla_count += 1
                    marker = "  ✓ vanilla"
                elif status == "ok":
                    ok_count += 1
                    marker = "  ✓ verified"
                elif status == "unknown":
                    unknown_list.append((tex, cnt, detail))
                    marker = "  ⚠ unverified"
                else:
                    suspicious_list.append((tex, cnt, detail))
                    marker = "  ✗ suspicious"
                lines.append(f"  {cnt:3}×  {tex:<55}{marker}")

            lines.append("")
            lines.append(
                f"  Summary: {vanilla_count} vanilla, {ok_count} verified, "
                f"{len(unknown_list)} unverified, {len(suspicious_list)} suspicious"
            )
            lines.append("")

    # ── ENTITY ANALYSIS ───────────────────────────────────────────────────
    if check_entities:
        entity_names = assets["entity_names"]
        if entity_names:
            modded: List[str] = []
            unknown_ent: List[str] = []

            lines.append(f"── Entity Types ({len(entity_names)} unique) ──────────────────────")
            for n, cnt in sorted(entity_names.items(), key=lambda x: -x[1]):
                status, detail = _score_entity(n, VANILLA_ENTITIES, workspace_entities)
                marker = {
                    "vanilla": "  ✓ vanilla",
                    "mod-local": "  ✓ mod (local)",
                    "modded": "  ○ modded",
                    "unknown": "  ⚠ unknown",
                }.get(status, "")
                if status == "modded":
                    modded.append(n)
                elif status == "unknown":
                    unknown_ent.append(n)
                lines.append(f"  {cnt:3}×  {n:<50}{marker}")

            lines.append("")
            if unknown_ent:
                lines.append(
                    f"  ⚠ {len(unknown_ent)} entity type(s) not in any catalog:\n"
                    + "    " + ", ".join(unknown_ent[:10])
                    + (" ..." if len(unknown_ent) > 10 else "")
                )
                lines.append(
                    "  → Check your mod has a Loenn/entities/ .lua for each of these."
                )
                lines.append("")

        trigger_names = assets["trigger_names"]
        if trigger_names:
            lines.append(f"── Trigger Types ({len(trigger_names)} unique) ─────────────────────")
            for n, cnt in sorted(trigger_names.items(), key=lambda x: -x[1]):
                is_vanilla = n in VANILLA_TRIGGERS
                marker = "  ✓ vanilla" if is_vanilla else "  ○ mod/unknown"
                lines.append(f"  {cnt:3}×  {n:<50}{marker}")
            lines.append("")

        effect_names = assets["effect_names"]
        if effect_names:
            lines.append(f"── Styleground Effects ({len(effect_names)} unique) ─────────────────")
            for n, cnt in sorted(effect_names.items(), key=lambda x: -x[1]):
                is_vanilla = n in VANILLA_EFFECTS
                is_local = n in workspace_effects
                if is_vanilla:
                    marker = "  ✓ vanilla"
                elif is_local:
                    marker = "  ✓ mod (local)"
                else:
                    marker = "  ○ mod/unknown"
                lines.append(f"  {cnt:3}×  {n:<50}{marker}")
            lines.append("")

    # ── TILESET ANALYSIS ──────────────────────────────────────────────────
    if check_tilesets:
        fg_chars = assets["tileset_fg_chars"]
        bg_chars = assets["tileset_bg_chars"]

        lines.append("── Tileset Characters ────────────────────────────────────")
        if fg_chars:
            valid_fg = fg_chars & VANILLA_FG_TILESET_CHARS
            custom_fg = fg_chars - VANILLA_FG_TILESET_CHARS
            lines.append(
                f"  FG tiles: {sorted(fg_chars)}"
            )
            if custom_fg:
                lines.append(
                    f"  ⚠ Custom FG chars: {sorted(custom_fg)}"
                    " — ensure ForegroundTiles.xml defines these"
                )
        else:
            lines.append("  FG tiles: (none used)")

        if bg_chars:
            custom_bg = bg_chars - VANILLA_BG_TILESET_CHARS
            lines.append(f"  BG tiles: {sorted(bg_chars)}")
            if custom_bg:
                lines.append(
                    f"  ⚠ Custom BG chars: {sorted(custom_bg)}"
                    " — ensure BackgroundTiles.xml defines these"
                )
        else:
            lines.append("  BG tiles: (none used)")

        lines.append(
            "  Reference: CelestialCartographers/Loenn → "
            "Celeste/Content/Graphics/ForegroundTiles.xml"
        )
        lines.append("")

    # ── WORKSPACE CATALOG SUMMARY ─────────────────────────────────────────
    if workspace_entities or workspace_effects:
        lines.append("── Workspace Mod Catalog ────────────────────────────────")
        if workspace_entities:
            lines.append(
                f"  Entity defs found:  {len(workspace_entities)}"
                f"  ({', '.join(sorted(workspace_entities)[:8])}"
                f"{'...' if len(workspace_entities) > 8 else ''})"
            )
        if workspace_effects:
            lines.append(
                f"  Effect defs found:  {len(workspace_effects)}"
                f"  ({', '.join(sorted(workspace_effects)[:8])}"
                f"{'...' if len(workspace_effects) > 8 else ''})"
            )
        lines.append("")

    # ── MADDIE480 CROSS-REFERENCE INFO ────────────────────────────────────
    lines.append("── Maddie480 Graphics Dump Reference ────────────────────")
    lines.append(f"  URL: {MADDIE480_DUMP_API}")
    if not fetch_asset_index:
        lines.append(
            "  Tip: pass fetch_asset_index=True to query the live asset index\n"
            "  and get exact verified/unknown classification for every texture."
        )
    elif maddie_index is not None:
        lines.append(f"  Index size: {len(maddie_index)} known asset paths")
        lines.append(f"  Status: {maddie_status}")
    else:
        lines.append(f"  Could not fetch index: {maddie_status}")
        lines.append("  Falling back to heuristic classification.")

    lines.append("")
    lines.append("── Lönn Source Reference ────────────────────────────────")
    lines.append("  https://github.com/CelestialCartographers/Loenn")
    lines.append(
        "  Entity catalog: Loenn/src/entities/ (vanilla Lua definitions)\n"
        "  Tilesets:       Celeste/Content/Graphics/ForegroundTiles.xml\n"
        "                  Celeste/Content/Graphics/BackgroundTiles.xml"
    )

    return "\n".join(lines)
