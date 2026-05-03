"""
gdep-inspired analysis and extended editing tools for loenn-mcp.

Integrates game analysis concepts from pirua-game/ai_game_base_analysis_cli_mcp_tool
into loenn-mcp, adding advanced analysis, wiki caching, batch operations, and more.

Categories:
  Map Reading Extensions — read_map_metadata, search_entities, search_triggers, compare_rooms
  Map Editing Extensions — update_entity, move_entity, update_room, clone_room,
                           batch_add_entities, resize_room
  Decals — list_decals, add_decal, remove_decal
  Advanced Analysis — analyze_entity_usage, analyze_difficulty, find_entity_references,
                      detect_map_patterns, analyze_room_connectivity
  Suggestions — suggest_improvements, compare_maps
  Wiki/Cache — wiki_save, wiki_search, wiki_list, wiki_get
  Mod Project — get_mod_info, validate_map
  Catalog Extensions — get_trigger_definition, get_effect_definition
  Import/Export — export_room_json, import_room_json
  Diff & Fix — summarize_map_diff, batch_validate_and_fix
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ─── Wiki / Cache System ──────────────────────────────────────────────────────

WIKI_DIR_NAME = ".loenn_mcp_wiki"


def _wiki_dir(workspace: Path) -> Path:
    d = workspace / WIKI_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def wiki_save_entry(workspace: Path, key: str, content: str, tags: List[str] = None) -> str:
    """Save an entry to the wiki cache."""
    d = _wiki_dir(workspace)
    entry = {
        "key": key,
        "content": content,
        "tags": tags or [],
        "timestamp": time.time(),
    }
    safe_key = key.replace("/", "_").replace(" ", "_")[:80]
    path = d / f"{safe_key}.json"
    path.write_text(json.dumps(entry, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path.relative_to(workspace))


def wiki_search_entries(workspace: Path, query: str) -> List[Dict[str, Any]]:
    """Search wiki entries by key or content substring."""
    d = _wiki_dir(workspace)
    results = []
    query_lower = query.lower()
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if (query_lower in data.get("key", "").lower()
                    or query_lower in data.get("content", "").lower()
                    or any(query_lower in t.lower() for t in data.get("tags", []))):
                results.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return results


def wiki_list_entries(workspace: Path) -> List[Dict[str, str]]:
    """List all wiki entries (key + timestamp)."""
    d = _wiki_dir(workspace)
    entries = []
    for f in sorted(d.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            entries.append({
                "key": data.get("key", f.stem),
                "tags": data.get("tags", []),
                "timestamp": data.get("timestamp", 0),
            })
        except (json.JSONDecodeError, OSError):
            continue
    return entries


def wiki_get_entry(workspace: Path, key: str) -> Optional[Dict[str, Any]]:
    """Get a specific wiki entry by key."""
    d = _wiki_dir(workspace)
    safe_key = key.replace("/", "_").replace(" ", "_")[:80]
    path = d / f"{safe_key}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
    # Fallback: search by key field
    for f in d.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("key") == key:
                return data
        except (json.JSONDecodeError, OSError):
            continue
    return None


# ─── Analysis Helpers ─────────────────────────────────────────────────────────

_HAZARD_ENTITIES = frozenset({
    "spikes", "spikeUp", "spikeDown", "spikeLeft", "spikeRight",
    "crumbleBlock", "fallingBlock", "moveBlock", "crushBlock",
    "kevinsBarrier", "bumper", "seekerBarrier",
})

_NAV_AIDS = frozenset({
    "spring", "refill", "jumpThru", "dreamBlock", "dashBlock",
    "booster", "feather", "cloud",
})

_COLLECTIBLES = frozenset({
    "strawberry", "goldenBerry", "cassette", "blackGem", "heartGem",
})


def analyze_entity_usage_data(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze entity usage across all rooms."""
    entity_counts: Dict[str, int] = {}
    room_entity_map: Dict[str, List[str]] = {}

    for room in rooms:
        room_name = room.get("name", "?")
        room_entities = []
        for child in room.get("__children", []):
            if child.get("__name") == "entities":
                for ent in child.get("__children", []):
                    ename = ent.get("__name", "unknown")
                    entity_counts[ename] = entity_counts.get(ename, 0) + 1
                    room_entities.append(ename)
        room_entity_map[room_name] = room_entities

    sorted_counts = sorted(entity_counts.items(), key=lambda x: -x[1])
    return {
        "total_entities": sum(entity_counts.values()),
        "unique_types": len(entity_counts),
        "counts": dict(sorted_counts),
        "room_entity_map": room_entity_map,
    }


def analyze_difficulty_data(room: Dict[str, Any]) -> Dict[str, Any]:
    """Estimate difficulty of a room based on hazard density, nav aids, tile coverage."""
    w = room.get("width", 320)
    h = room.get("height", 184)
    tile_area = (w // 8) * (h // 8)

    hazards = 0
    nav_aids = 0
    total_entities = 0

    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            for ent in child.get("__children", []):
                ename = ent.get("__name", "")
                total_entities += 1
                if ename in _HAZARD_ENTITIES:
                    hazards += 1
                if ename in _NAV_AIDS:
                    nav_aids += 1

    # Tile coverage
    solid_pct = 0.0
    for child in room.get("__children", []):
        if child.get("__name") == "solids":
            text = child.get("innerText", "")
            rows = [r for r in text.split("\n") if r]
            total_chars = sum(len(r) for r in rows)
            solid_chars = sum(1 for r in rows for ch in r if ch != "0")
            if total_chars > 0:
                solid_pct = solid_chars / total_chars

    hazard_density = hazards / max(tile_area, 1)
    nav_density = nav_aids / max(tile_area, 1)

    # Difficulty score: 1-10
    score = min(10, max(1, int(
        2 + hazard_density * 500 + solid_pct * 3 - nav_density * 200
    )))

    return {
        "hazards": hazards,
        "nav_aids": nav_aids,
        "total_entities": total_entities,
        "solid_pct": round(solid_pct, 3),
        "hazard_density": round(hazard_density, 6),
        "difficulty_score": score,
        "difficulty_label": _difficulty_label(score),
    }


def _difficulty_label(score: int) -> str:
    if score <= 2:
        return "very easy"
    elif score <= 4:
        return "easy"
    elif score <= 6:
        return "moderate"
    elif score <= 8:
        return "hard"
    else:
        return "very hard"


def detect_map_patterns_data(rooms: List[Dict[str, Any]]) -> List[str]:
    """Detect gameplay design archetypes from room layout and entities."""
    patterns = []

    if len(rooms) <= 2:
        patterns.append("micro-map (1-2 rooms)")
    elif len(rooms) <= 6:
        patterns.append("short-level (3-6 rooms)")
    elif len(rooms) <= 15:
        patterns.append("standard-level (7-15 rooms)")
    else:
        patterns.append("extended-level (16+ rooms)")

    # Linear vs hub detection
    room_positions = [(r.get("x", 0), r.get("y", 0)) for r in rooms]
    xs = [p[0] for p in room_positions]
    ys = [p[1] for p in room_positions]
    x_spread = max(xs) - min(xs) if xs else 0
    y_spread = max(ys) - min(ys) if ys else 0

    if len(rooms) > 2:
        if x_spread > y_spread * 3:
            patterns.append("linear-horizontal")
        elif y_spread > x_spread * 3:
            patterns.append("linear-vertical")
        elif x_spread > 0 and y_spread > 0:
            patterns.append("grid-layout")

    # Entity-based patterns
    all_entity_names = []
    has_wind = False
    for room in rooms:
        if room.get("windPattern", "None") != "None":
            has_wind = True
        for child in room.get("__children", []):
            if child.get("__name") == "entities":
                for ent in child.get("__children", []):
                    all_entity_names.append(ent.get("__name", ""))

    hazard_count = sum(1 for e in all_entity_names if e in _HAZARD_ENTITIES)
    collectible_count = sum(1 for e in all_entity_names if e in _COLLECTIBLES)

    if collectible_count > len(rooms) * 2:
        patterns.append("collectible-rich")
    if hazard_count > len(rooms) * 5:
        patterns.append("hazard-dense")
    if has_wind:
        patterns.append("wind-corridor")

    # Checkpoint detection
    checkpoint_count = sum(1 for e in all_entity_names if e == "checkpoint")
    if checkpoint_count > 0:
        patterns.append(f"checkpointed ({checkpoint_count} checkpoints)")

    return patterns


def analyze_room_connectivity_data(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Analyze room adjacency graph by position overlap."""
    adjacency: Dict[str, List[str]] = {}
    room_info = []

    for r in rooms:
        name = r.get("name", "?")
        x = r.get("x", 0)
        y = r.get("y", 0)
        w = r.get("width", 320)
        h = r.get("height", 184)
        room_info.append({"name": name, "x": x, "y": y, "w": w, "h": h})
        adjacency[name] = []

    # Check adjacency (rooms that touch or overlap edges)
    for i, ri in enumerate(room_info):
        for j, rj in enumerate(room_info):
            if i >= j:
                continue
            # Check if rooms share an edge (within 8px tolerance)
            tol = 8
            touches_h = (
                abs(ri["x"] + ri["w"] - rj["x"]) <= tol
                or abs(rj["x"] + rj["w"] - ri["x"]) <= tol
            )
            touches_v = (
                abs(ri["y"] + ri["h"] - rj["y"]) <= tol
                or abs(rj["y"] + rj["h"] - ri["y"]) <= tol
            )
            overlaps_h = (
                ri["x"] < rj["x"] + rj["w"] + tol
                and rj["x"] < ri["x"] + ri["w"] + tol
            )
            overlaps_v = (
                ri["y"] < rj["y"] + rj["h"] + tol
                and rj["y"] < ri["y"] + ri["h"] + tol
            )

            if (touches_h and overlaps_v) or (touches_v and overlaps_h):
                adjacency[ri["name"]].append(rj["name"])
                adjacency[rj["name"]].append(ri["name"])

    # Classify nodes
    isolated = [name for name, nbrs in adjacency.items() if len(nbrs) == 0]
    dead_ends = [name for name, nbrs in adjacency.items() if len(nbrs) == 1]
    hubs = [name for name, nbrs in adjacency.items() if len(nbrs) >= 3]

    return {
        "adjacency": adjacency,
        "total_rooms": len(rooms),
        "total_connections": sum(len(v) for v in adjacency.values()) // 2,
        "isolated_rooms": isolated,
        "dead_ends": dead_ends,
        "hubs": hubs,
    }


def suggest_improvements_data(room: Dict[str, Any]) -> List[str]:
    """Suggest improvements for a room based on analysis."""
    suggestions = []
    diff = analyze_difficulty_data(room)
    w = room.get("width", 320)
    h = room.get("height", 184)

    # No spawn
    has_spawn = False
    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            for ent in child.get("__children", []):
                if ent.get("__name") == "player":
                    has_spawn = True

    if not has_spawn:
        suggestions.append("Add a player spawn entity (required for the room to be playable)")

    # Floor check
    for child in room.get("__children", []):
        if child.get("__name") == "solids":
            text = child.get("innerText", "")
            rows = [r for r in text.split("\n") if r]
            if rows:
                last_row = rows[-1]
                if all(ch == "0" for ch in last_row):
                    suggestions.append("Add floor tiles — players will fall out of bounds")

    # Difficulty suggestions
    if diff["difficulty_score"] >= 8 and diff["nav_aids"] == 0:
        suggestions.append("Very difficult room with no navigation aids — consider adding a refill or spring")

    if diff["hazards"] > 10 and diff["total_entities"] == diff["hazards"]:
        suggestions.append("Room is all hazards with no collectibles or nav aids — add variety")

    if diff["solid_pct"] > 0.7:
        suggestions.append("Very dense tile coverage (>70%) — consider opening up some space")

    if diff["solid_pct"] < 0.05 and diff["hazards"] == 0:
        suggestions.append("Nearly empty room — add platforms, entities, or tile features")

    # Size suggestions
    if w > 640 and diff["total_entities"] < 3:
        suggestions.append("Large room with very few entities — populate with gameplay elements")

    return suggestions


def compute_map_snapshot(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute a structural snapshot for diffing."""
    snapshot = {}
    for room in rooms:
        name = room.get("name", "?")
        entities = []
        triggers = []
        solids_hash = ""

        for child in room.get("__children", []):
            cname = child.get("__name", "")
            if cname == "entities":
                for ent in child.get("__children", []):
                    entities.append({
                        "type": ent.get("__name", "?"),
                        "x": ent.get("x", 0),
                        "y": ent.get("y", 0),
                    })
            elif cname == "triggers":
                for trig in child.get("__children", []):
                    triggers.append({
                        "type": trig.get("__name", "?"),
                        "x": trig.get("x", 0),
                        "y": trig.get("y", 0),
                    })
            elif cname == "solids":
                text = child.get("innerText", "")
                solids_hash = hashlib.md5(text.encode()).hexdigest()[:8]

        snapshot[name] = {
            "width": room.get("width", 0),
            "height": room.get("height", 0),
            "x": room.get("x", 0),
            "y": room.get("y", 0),
            "entity_count": len(entities),
            "trigger_count": len(triggers),
            "entities": entities,
            "triggers": triggers,
            "solids_hash": solids_hash,
        }
    return snapshot


def diff_snapshots(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    """Compare two map snapshots and return list of changes."""
    changes = []

    old_rooms = set(old.keys())
    new_rooms = set(new.keys())

    for name in new_rooms - old_rooms:
        changes.append(f"+ Room added: {name}")
    for name in old_rooms - new_rooms:
        changes.append(f"- Room removed: {name}")

    for name in old_rooms & new_rooms:
        o = old[name]
        n = new[name]
        if o["width"] != n["width"] or o["height"] != n["height"]:
            changes.append(f"~ Room {name}: resized {o['width']}x{o['height']} → {n['width']}x{n['height']}")
        if o["x"] != n["x"] or o["y"] != n["y"]:
            changes.append(f"~ Room {name}: moved ({o['x']},{o['y']}) → ({n['x']},{n['y']})")
        if o["solids_hash"] != n["solids_hash"]:
            changes.append(f"~ Room {name}: tiles changed")
        ent_diff = n["entity_count"] - o["entity_count"]
        if ent_diff != 0:
            changes.append(f"~ Room {name}: entities {o['entity_count']} → {n['entity_count']} ({'+' if ent_diff > 0 else ''}{ent_diff})")
        trig_diff = n["trigger_count"] - o["trigger_count"]
        if trig_diff != 0:
            changes.append(f"~ Room {name}: triggers {o['trigger_count']} → {n['trigger_count']} ({'+' if trig_diff > 0 else ''}{trig_diff})")

    return changes


def validate_and_fix_room(room: Dict[str, Any], auto_fix: bool = False) -> Dict[str, Any]:
    """Validate a room and optionally auto-fix common issues."""
    issues = []
    fixes_applied = []
    w = room.get("width", 0)
    h = room.get("height", 0)

    if w % 8 != 0:
        issues.append(f"Width {w} not multiple of 8")
        if auto_fix:
            room["width"] = ((w + 7) // 8) * 8
            fixes_applied.append(f"Width adjusted to {room['width']}")

    if h % 8 != 0:
        issues.append(f"Height {h} not multiple of 8")
        if auto_fix:
            room["height"] = ((h + 7) // 8) * 8
            fixes_applied.append(f"Height adjusted to {room['height']}")

    # Check spawn
    has_spawn = False
    entities_el = None
    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            entities_el = child
            for ent in child.get("__children", []):
                if ent.get("__name") == "player":
                    has_spawn = True

    if not has_spawn:
        issues.append("No player spawn")
        if auto_fix and entities_el is not None:
            max_id = max(
                (e.get("id", 0) for e in entities_el.get("__children", [])),
                default=0,
            )
            entities_el["__children"].append({
                "__name": "player",
                "__children": [],
                "id": max_id + 1,
                "x": 24,
                "y": h - 24,
            })
            fixes_applied.append("Added player spawn at (24, h-24)")

    # Check floor
    for child in room.get("__children", []):
        if child.get("__name") == "solids":
            text = child.get("innerText", "")
            rows = [r for r in text.split("\n") if r]
            if rows:
                last_row = rows[-1]
                if all(ch == "0" for ch in last_row):
                    issues.append("No floor tiles")
                    if auto_fix:
                        rows[-1] = "1" * len(last_row)
                        child["innerText"] = "\n".join(rows)
                        fixes_applied.append("Added floor tiles (bottom row)")
            elif text.strip() == "":
                issues.append("Empty tile grid")

    # Entity bounds check
    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            for ent in child.get("__children", []):
                ex, ey = ent.get("x", 0), ent.get("y", 0)
                if ex < 0 or ey < 0 or ex > w or ey > h:
                    issues.append(
                        f"Entity '{ent.get('__name', '?')}' at ({ex},{ey}) outside bounds"
                    )
                    if auto_fix:
                        ent["x"] = max(0, min(ex, w - 8))
                        ent["y"] = max(0, min(ey, h - 8))
                        fixes_applied.append(
                            f"Clamped '{ent.get('__name', '?')}' to room bounds"
                        )

    return {
        "issues": issues,
        "fixes_applied": fixes_applied,
        "is_valid": len(issues) == 0,
    }
