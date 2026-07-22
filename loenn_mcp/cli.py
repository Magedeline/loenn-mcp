"""
pcgscene — CLI pipeline for Celeste PCG maps.

Replaces ad-hoc script runs with a batch-first, JSON-friendly workflow that
scales to big maps with many rooms:

    pcgscene scan     <map.bin>            score every room (I, D, exits,
                                           connectivity, spawn, fairness gate)
    pcgscene score    <map.bin> -r NAME    one-room deep report
    pcgscene validate <map.bin>            in-game readiness checklist,
                                           exit code 0/1 (CI-friendly)
    pcgscene fix      <map.bin>            safe auto-repairs (spawns, names,
                                           golden berries) with backup+dry-run
    pcgscene diff     <a.bin> <b.bin>      what changed between two maps
    pcgscene generate <map.bin>            preset-driven map generation
                                           (requires the full build with
                                           pcg_helper)

Every command accepts --json for machine-readable output (e.g. for a Lönn
report-viewer script). All writes go through the atomic, backed-up,
round-trip-validated celeste_bin.write_map — a bad run can never destroy a
map.bin.

All logic here is deterministic/procedural; no AI models, no network calls.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from . import celeste_bin as cb
except ImportError:  # run directly from source
    import celeste_bin as cb

TILE = 8

# Scarcity cap and AOI radius mirror the Lua toolkit (pcg_toolkit.lua) so the
# editor scripts and this CLI report comparable numbers.
SCARCITY_CAP = 20.0
AOI_RADIUS = 2
AIR = "0"

VALID_NAME = re.compile(r"^[A-Za-z0-9_\-]+$")


# ─── Grid extraction ─────────────────────────────────────────────────────────

def room_grid(room: Dict[str, Any]) -> Tuple[List[List[str]], int, int]:
    """Extract the fg tile grid from a room element. grid[x][y], 0-based.

    Rows in the .bin may be shorter than the room width (trailing air is
    trimmed); missing cells read as air.
    """
    w = int(room.get("width", 0)) // TILE
    h = int(room.get("height", 0)) // TILE
    grid = [[AIR] * h for _ in range(w)]
    solids = cb.find_child(room, "solids")
    text = solids.get("innerText", "") if solids else ""
    for y, line in enumerate(text.split("\n")):
        if y >= h:
            break
        for x, ch in enumerate(line):
            if x >= w:
                break
            grid[x][y] = ch
    return grid, w, h


def room_entities(room: Dict[str, Any]) -> List[Dict[str, Any]]:
    ent = cb.find_child(room, "entities")
    return ent.get("__children", []) if ent else []


def dominant_solid(grid: List[List[str]], w: int, h: int) -> str:
    freq: Dict[str, int] = {}
    for x in range(w):
        for y in range(h):
            t = grid[x][y]
            if t != AIR:
                freq[t] = freq.get(t, 0) + 1
    return max(freq, key=freq.get) if freq else "3"


# ─── Exit detection + pathing (mirrors pcg_toolkit.lua scoreCore) ────────────

def find_exits(grid: List[List[str]], w: int, h: int, min_run: int = 2) -> List[Dict[str, Any]]:
    exits: List[Dict[str, Any]] = []

    def scan(length: int, get, make, side: str):
        run_start = None
        for k in range(length + 1):
            is_air = k < length and get(k) == AIR
            if is_air:
                if run_start is None:
                    run_start = k
            elif run_start is not None:
                if k - run_start >= min_run:
                    exits.append({"side": side,
                                  "cells": [make(i) for i in range(run_start, k)],
                                  "lo": run_start, "hi": k - 1})
                run_start = None

    if w == 0 or h == 0:
        return exits
    scan(h, lambda y: grid[0][y], lambda y: (0, y), "left")
    scan(h, lambda y: grid[w - 1][y], lambda y: (w - 1, y), "right")
    scan(w, lambda x: grid[x][0], lambda x: (x, 0), "top")
    scan(w, lambda x: grid[x][h - 1], lambda x: (x, h - 1), "bottom")
    return exits


def bfs_exit_to_exit(grid, w, h, from_cells, to_cells) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
    target = {c[1] * w + c[0] for c in to_cells}
    visited: Dict[int, Optional[int]] = {}
    queue: List[Tuple[int, int, int]] = []
    for (x, y) in from_cells:
        k = y * w + x
        if grid[x][y] == AIR and k not in visited:
            visited[k] = None
            queue.append((x, y, 0))
    head = 0
    while head < len(queue):
        x, y, dist = queue[head]
        head += 1
        k = y * w + x
        if k in target:
            path = []
            cur: Optional[int] = k
            while cur is not None:
                path.append((cur % w, cur // w))
                cur = visited[cur]
            return dist, path
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nx, ny = x + dx, y + dy
            if 0 <= nx < w and 0 <= ny < h and grid[nx][ny] == AIR:
                nk = ny * w + nx
                if nk not in visited:
                    visited[nk] = k
                    queue.append((nx, ny, dist + 1))
    return None


def score_room(room: Dict[str, Any], num_paths: int = 5, seed: int = 42,
               weights=(1.0, 1.0, 1.0), zweights=(1.0, 1.0, 1.0)) -> Dict[str, Any]:
    """Interestingness/difficulty per paper §4.1-4.3 with the port fixes:
    exit-pair paths, path-based AOI, normalised entropy, capped scarcity."""
    grid, w, h = room_grid(room)
    if w < 3 or h < 3:
        return {"error": "room too small to score", "width": w, "height": h}

    rng = random.Random(seed)
    dom = dominant_solid(grid, w, h)
    exits = find_exits(grid, w, h)

    path_lens: List[int] = []
    path_cells: set = set()
    sampled_pairs = connected_pairs = 0

    if len(exits) >= 2:
        pairs = [(i, j) for i in range(len(exits)) for j in range(i + 1, len(exits))]
        rng.shuffle(pairs)
        sampled_pairs = min(len(pairs), max(1, num_paths))
        for i, j in pairs[:sampled_pairs]:
            res = bfs_exit_to_exit(grid, w, h, exits[i]["cells"], exits[j]["cells"])
            if res:
                connected_pairs += 1
                path_lens.append(res[0])
                path_cells.update(c[1] * w + c[0] for c in res[1])

    # AOI: dilated paths, else centre-third box
    aoi: set = set()
    if path_cells:
        for k in path_cells:
            cx, cy = k % w, k // w
            for dx in range(-AOI_RADIUS, AOI_RADIUS + 1):
                for dy in range(-AOI_RADIUS, AOI_RADIUS + 1):
                    nx, ny = cx + dx, cy + dy
                    if 1 <= nx < w - 1 and 1 <= ny < h - 1:
                        aoi.add(ny * w + nx)
    else:
        for x in range(w // 3, 2 * w // 3):
            for y in range(h // 3, 2 * h // 3):
                aoi.add(y * w + x)
    aoi_area = max(1, len(aoi))

    area = max(1, (w - 2) * (h - 2))
    nle_total = nle_aoi = le_aoi = hole_cols = 0
    freq: Dict[str, int] = {}
    for x in range(1, w - 1):
        if grid[x][h - 2] == AIR:
            hole_cols += 1
        for y in range(1, h - 1):
            t = grid[x][y]
            if t != AIR:
                freq[t] = freq.get(t, 0) + 1
                in_aoi = (y * w + x) in aoi
                if t != dom:
                    nle_total += 1
                    if in_aoi:
                        nle_aoi += 1
                elif grid[x][y - 1] == AIR and in_aoi:
                    le_aoi += 1

    path_mean = sum(path_lens) / len(path_lens) if path_lens else 0.0
    path_var = (sum((l - path_mean) ** 2 for l in path_lens) / len(path_lens)
                if len(path_lens) > 1 else 0.0)

    total = sum(freq.values())
    entropy = 0.0
    if total > 0 and len(freq) > 1:
        for c in freq.values():
            p = c / total
            entropy -= p * math.log(p)
        entropy /= math.log(len(freq))

    d_global = nle_total / area
    d_local = nle_aoi / aoi_area
    scarcity = min(1.0 / d_local, SCARCITY_CAP) if d_local > 0 else SCARCITY_CAP
    # Hf = holes / path length (paper §4.3); when no path was sampled,
    # normalise by interior width so the term stays a bounded ratio instead
    # of the raw hole count.
    hole_freq = hole_cols / (path_mean if path_mean > 0 else max(w - 2, 1))
    le_local = le_aoi / aoi_area

    w1, w2, w3 = weights
    z1, z2, z3 = zweights
    spawns = [e for e in room_entities(room) if e.get("__name") == "player"]

    # median-based variance check (paper §4.1.2)
    variance_ok = True
    if len(path_lens) >= 2:
        s = sorted(path_lens)
        n = len(s)
        median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
        variance_ok = path_var <= 2 * median

    # A single-exit room (an end/leaf room) has nothing to disconnect — treat
    # it as connected; only sealed rooms and split multi-exit rooms fail.
    connected = (len(exits) >= 2 and connected_pairs == sampled_pairs and sampled_pairs > 0) \
        or len(exits) == 1
    air_cells = sum(1 for x in range(1, w - 1) for y in range(1, h - 1) if grid[x][y] == AIR)
    air_ok = air_cells >= (w - 2) * (h - 2) * 15 // 100

    return {
        "name": room.get("name", "?"),
        "width": w, "height": h,
        "exits": len(exits),
        "exit_sides": [e["side"] for e in exits],
        "sampled_pairs": sampled_pairs,
        "connected_pairs": connected_pairs,
        "connected": connected,
        "spawns": len(spawns),
        "interestingness": round(w1 * d_global + w2 * d_local + w3 * entropy, 4),
        "difficulty": round(z1 * hole_freq + z2 * le_local + z3 * scarcity, 4),
        "path_mean": round(path_mean, 2),
        "path_variance": round(path_var, 2),
        "entropy_norm": round(entropy, 4),
        "scarcity": round(scarcity, 4),
        "variance_ok": variance_ok,
        "air_ratio_ok": air_ok,
        "fair": bool(connected and len(spawns) >= 1 and variance_ok and air_ok),
    }


# ─── Cross-room connectivity ─────────────────────────────────────────────────

def _border_holes_world(room: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Exit runs in world pixel coordinates, for adjacency matching."""
    grid, w, h = room_grid(room)
    rx, ry = int(room.get("x", 0)), int(room.get("y", 0))
    holes = []
    for e in find_exits(grid, w, h):
        if e["side"] in ("top", "bottom"):
            lo = rx + e["lo"] * TILE
            hi = rx + e["hi"] * TILE + TILE - 1
        else:
            lo = ry + e["lo"] * TILE
            hi = ry + e["hi"] * TILE + TILE - 1
        holes.append({"side": e["side"], "lo": lo, "hi": hi})
    return holes


def map_connectivity(rooms: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Which rooms actually connect through matching border holes, and which
    are unreachable from the room that carries the first spawn."""
    holes = [_border_holes_world(r) for r in rooms]
    n = len(rooms)
    adj: List[set] = [set() for _ in range(n)]

    def rect(r):
        return (int(r.get("x", 0)), int(r.get("y", 0)),
                int(r.get("width", 0)), int(r.get("height", 0)))

    opposite = {"left": "right", "right": "left", "top": "bottom", "bottom": "top"}
    for i in range(n):
        xi, yi, wi, hi = rect(rooms[i])
        for j in range(i + 1, n):
            xj, yj, wj, hj = rect(rooms[j])
            for hole in holes[i]:
                side = hole["side"]
                adjacent = (
                    (side == "right" and xi + wi == xj) or
                    (side == "left" and xj + wj == xi) or
                    (side == "bottom" and yi + hi == yj) or
                    (side == "top" and yj + hj == yi)
                )
                if not adjacent:
                    continue
                for other in holes[j]:
                    if other["side"] == opposite[side] and \
                       other["lo"] <= hole["hi"] and hole["lo"] <= other["hi"]:
                        adj[i].add(j)
                        adj[j].add(i)
                        break

    start = 0
    for i, r in enumerate(rooms):
        if any(e.get("__name") == "player" for e in room_entities(r)):
            start = i
            break

    seen = {start}
    stack = [start]
    while stack:
        cur = stack.pop()
        for nxt in adj[cur]:
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)

    return {
        "start_room": rooms[start].get("name", "?") if rooms else None,
        "edges": sorted(
            [rooms[i].get("name", "?"), rooms[j].get("name", "?")]
            for i in range(n) for j in adj[i] if j > i
        ),
        "unreachable": [rooms[i].get("name", "?") for i in range(n) if i not in seen],
    }


# ─── Validation checklist ────────────────────────────────────────────────────

def validate_map(data: Dict[str, Any]) -> Dict[str, Any]:
    rooms = cb.get_rooms(data)
    problems: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    def problem(room: str, kind: str, msg: str):
        problems.append({"room": room, "kind": kind, "message": msg})

    def warn(room: str, kind: str, msg: str):
        warnings.append({"room": room, "kind": kind, "message": msg})

    if not rooms:
        problem("-", "no-rooms", "Map has no rooms")

    names: Dict[str, int] = {}
    for r in rooms:
        name = r.get("name", "")
        names[name] = names.get(name, 0) + 1
        if not VALID_NAME.match(name or ""):
            problem(name, "bad-name",
                    "Room name has spaces or special characters — breaks debug "
                    "teleports and rando references")

        ents = room_entities(r)
        spawns = [e for e in ents if e.get("__name") == "player"]
        if not spawns:
            problem(name, "no-spawn",
                    "No player spawn — dying in this room errors out in-game")
        else:
            grid, w, h = room_grid(r)
            for s in spawns:
                tx = int(s.get("x", 0)) // TILE
                ty = int(s.get("y", 0)) // TILE
                if 0 <= tx < w and 0 <= ty < h and grid[tx][ty] != AIR:
                    warn(name, "spawn-in-solid",
                         f"Spawn at tile ({tx},{ty}) is inside a solid tile")

        for e in ents:
            if e.get("__name") == "strawberry" and e.get("golden"):
                warn(name, "fake-golden",
                     "strawberry{golden=true} is not a real golden berry — "
                     "use the goldenBerry entity")

    for name, count in names.items():
        if count > 1:
            problem(name, "duplicate-name", f"Room name used {count} times")

    # room overlap
    for i in range(len(rooms)):
        xi, yi = int(rooms[i].get("x", 0)), int(rooms[i].get("y", 0))
        wi, hi = int(rooms[i].get("width", 0)), int(rooms[i].get("height", 0))
        for j in range(i + 1, len(rooms)):
            xj, yj = int(rooms[j].get("x", 0)), int(rooms[j].get("y", 0))
            wj, hj = int(rooms[j].get("width", 0)), int(rooms[j].get("height", 0))
            if xi < xj + wj and xj < xi + wi and yi < yj + hj and yj < yi + hi:
                problem(rooms[i].get("name", "?"), "overlap",
                        f"Overlaps room {rooms[j].get('name', '?')}")

    conn = map_connectivity(rooms) if rooms else {"unreachable": []}
    for name in conn.get("unreachable", []):
        warn(name, "unreachable",
             "No carved hole path from the start room — dead end in-game")

    return {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "connectivity": conn,
        "rooms": len(rooms),
    }


# ─── Fixes ───────────────────────────────────────────────────────────────────

def _sanitize_name(name: str, taken: set) -> str:
    base = name.split(" ")[0].split("=")[0] or "room"
    base = re.sub(r"[^A-Za-z0-9_\-]", "_", base)
    cand, i = base, 2
    while cand in taken:
        cand = f"{base}_{i}"
        i += 1
    return cand


def _add_spawn(room: Dict[str, Any]) -> Optional[Tuple[int, int]]:
    """Place a player entity on the best floor cell (mirrors pcg.ensureSpawn)."""
    grid, w, h = room_grid(room)
    best, best_score = None, None
    for x in range(1, w - 1):
        for y in range(2, h - 1):
            if grid[x][y] == AIR and y + 1 < h and grid[x][y + 1] != AIR \
               and grid[x][y - 1] == AIR:
                score = y - abs(x - w // 2)
                if best_score is None or score > best_score:
                    best_score, best = score, (x, y)
    if best is None:
        best = (w // 2, h // 2)

    ent = cb.find_child(room, "entities")
    if ent is None:
        ent = {"__name": "entities", "__children": []}
        room.setdefault("__children", []).append(ent)
    ids = [e.get("id", 0) for e in ent["__children"] if isinstance(e.get("id"), int)]
    ent["__children"].append({
        "__name": "player",
        "id": (max(ids) + 1) if ids else 1,
        "x": best[0] * TILE,
        "y": best[1] * TILE,
        "__children": [],
    })
    return best


def fix_map(data: Dict[str, Any], only: Optional[set] = None) -> List[Dict[str, str]]:
    """Apply safe auto-repairs in place. Returns the list of changes made."""
    rooms = cb.get_rooms(data)
    changes: List[Dict[str, str]] = []
    all_fixes = {"spawns", "names", "berries"}
    active = all_fixes if not only else (only & all_fixes)

    if "names" in active:
        taken = {r.get("name", "") for r in rooms}
        for r in rooms:
            name = r.get("name", "")
            if not VALID_NAME.match(name or ""):
                taken.discard(name)
                new = _sanitize_name(name, taken)
                taken.add(new)
                r["name"] = new
                changes.append({"room": name, "fix": "rename", "detail": f"-> {new}"})

    for r in rooms:
        name = r.get("name", "?")
        ents = room_entities(r)

        if "spawns" in active and not any(e.get("__name") == "player" for e in ents):
            pos = _add_spawn(r)
            changes.append({"room": name, "fix": "add-spawn",
                            "detail": f"player at tile {pos}"})

        if "berries" in active:
            for e in ents:
                if e.get("__name") == "strawberry" and e.get("golden"):
                    e["__name"] = "goldenBerry"
                    e.pop("golden", None)
                    e.pop("winged", None)
                    e.pop("moon", None)
                    changes.append({"room": name, "fix": "golden-berry",
                                    "detail": "strawberry{golden} -> goldenBerry"})

    return changes


# ─── Generate (full build only) ──────────────────────────────────────────────

# Same preset philosophy as the Lönn pipeline dialog: paper-calibrated bundles
# (config 000011012, backtracking depth 2 per §4.1.2).
GENERATE_PRESETS: Dict[str, Dict[str, Any]] = {
    "quick": dict(room_count=6, proba=0.3, generation_mode="mdmc",
                  max_backtrack_depth=2, tries_limit=8,
                  hazard_density=0.02, spring_density=0.02,
                  place_decals_flag=False, place_triggers_flag=False),
    "simple_fair": dict(room_count=8, proba=0.4, generation_mode="mdmc",
                        max_backtrack_depth=2, tries_limit=20,
                        hazard_density=0.03, spring_density=0.02),
    "explore": dict(room_count=14, proba=0.85, generation_mode="mdmc",
                    max_backtrack_depth=4, tries_limit=20,
                    hazard_density=0.05, spring_density=0.03),
    "challenge": dict(room_count=10, proba=0.5, generation_mode="mdmc",
                      max_backtrack_depth=4, tries_limit=25,
                      hazard_density=0.12, spring_density=0.04),
}


def _empty_map(package: str) -> Dict[str, Any]:
    return {
        "__name": "Map",
        "_package": package,
        "__children": [
            {"__name": "levels", "__children": []},
            {"__name": "Filler", "__children": []},
            {"__name": "Style", "__children": [
                {"__name": "Foregrounds", "__children": []},
                {"__name": "Backgrounds", "__children": []},
            ]},
        ],
    }


def cmd_generate(args) -> int:
    try:
        try:
            from . import pcg_helper
        except ImportError:
            import pcg_helper  # type: ignore
    except ImportError:
        print("generate requires the full loenn-mcp build (pcg_helper module "
              "with the MdMC/WFC pipeline). This build ships scan/score/"
              "validate/fix/diff only.", file=sys.stderr)
        return 2

    path = Path(args.map)
    if path.exists():
        data = cb.read_map(path)
    else:
        data = _empty_map(path.stem)

    train_path = Path(args.train) if args.train else path
    training_rooms: List[Dict[str, Any]] = []
    if train_path.exists():
        training_rooms = cb.get_rooms(cb.read_map(train_path))

    kwargs: Dict[str, Any] = dict(GENERATE_PRESETS.get(args.preset, GENERATE_PRESETS["simple_fair"]))
    if args.rooms is not None:
        kwargs["room_count"] = args.rooms
    seed = args.seed if args.seed is not None else random.randrange(2 ** 31)
    kwargs["seed"] = seed

    result = pcg_helper.run_pipeline(training_rooms, **kwargs)
    new_rooms = result.get("rooms", [])

    levels = cb.find_child(data, "levels")
    if levels is None:
        levels = {"__name": "levels", "__children": []}
        data.setdefault("__children", []).append(levels)

    existing = {r.get("name") for r in cb.get_rooms(data)}
    added = []
    for room in new_rooms:
        name = room.get("name", "gen")
        if name in existing:
            room["name"] = _sanitize_name(name + "_x", existing)
        existing.add(room["name"])
        levels["__children"].append(room)
        added.append(room["name"])

    # Post-generation fairness gate: same repairs the fix command applies.
    fix_map(data)

    report = {
        "map": str(path),
        "preset": args.preset,
        "seed": seed,
        "rooms_added": added,
        "pipeline_report": result.get("report", ""),
        "dry_run": bool(args.dry_run),
    }

    if args.dry_run:
        _emit(args, report, text=f"[dry-run] would add {len(added)} room(s) "
                                 f"with preset={args.preset} seed={seed}")
        return 0

    cb.write_map(path, data)
    report["validation"] = validate_map(cb.read_map(path))
    _emit(args, report,
          text=f"Added {len(added)} room(s) to {path.name} "
               f"(preset={args.preset}, seed={seed}). "
               f"Validation: {'OK' if report['validation']['ok'] else 'PROBLEMS — run pcgscene validate'}")
    return 0


# ─── Output helpers ──────────────────────────────────────────────────────────

def _emit(args, payload: Dict[str, Any], text: str = ""):
    if getattr(args, "json", False):
        out = json.dumps(payload, indent=2)
        if getattr(args, "out", None):
            Path(args.out).write_text(out, encoding="utf-8")
            print(f"wrote {args.out}")
        else:
            print(out)
    elif text:
        print(text)


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_scan(args) -> int:
    data = cb.read_map(args.map)
    rooms = cb.get_rooms(data)
    reports = [score_room(r, num_paths=args.paths, seed=args.seed or 42) for r in rooms]
    conn = map_connectivity(rooms)
    fair = sum(1 for r in reports if r.get("fair"))

    payload = {
        "map": args.map,
        "package": data.get("_package", "?"),
        "rooms": reports,
        "connectivity": conn,
        "summary": {
            "rooms": len(reports),
            "fair": fair,
            "unfair": len(reports) - fair,
            "unreachable": len(conn["unreachable"]),
        },
    }
    if args.json:
        _emit(args, payload)
        return 0

    print(f"{data.get('_package', '?')} — {len(reports)} room(s)")
    print(f"{'room':<24} {'size':>9} {'exits':>5} {'conn':>4} {'spawn':>5} "
          f"{'I':>7} {'D':>7} {'fair':>4}")
    for r in reports:
        if "error" in r:
            print(f"{r.get('name', '?'):<24} {r['error']}")
            continue
        print(f"{r['name']:<24} {r['width']}x{r['height']:>3} {r['exits']:>6} "
              f"{'yes' if r['connected'] else 'NO':>4} {r['spawns']:>5} "
              f"{r['interestingness']:>7.3f} {r['difficulty']:>7.2f} "
              f"{'yes' if r['fair'] else 'NO':>4}")
    if conn["unreachable"]:
        print(f"\nUNREACHABLE from {conn['start_room']}: {', '.join(conn['unreachable'])}")
    print(f"\n{fair}/{len(reports)} rooms pass the fairness gate")
    return 0


def cmd_score(args) -> int:
    data = cb.read_map(args.map)
    room = cb.get_room(data, args.room)
    if room is None:
        available = ", ".join(r.get("name", "?") for r in cb.get_rooms(data))
        print(f"Room '{args.room}' not found. Available: {available}", file=sys.stderr)
        return 1
    report = score_room(room, num_paths=args.paths, seed=args.seed or 42)
    if args.json:
        _emit(args, report)
    else:
        for k, v in report.items():
            print(f"{k:>18}: {v}")
    return 0


def cmd_validate(args) -> int:
    data = cb.read_map(args.map)
    report = validate_map(data)
    report["map"] = args.map
    if args.json:
        _emit(args, report)
    else:
        for p in report["problems"]:
            print(f"PROBLEM  [{p['kind']}] {p['room']}: {p['message']}")
        for wrn in report["warnings"]:
            print(f"warning  [{wrn['kind']}] {wrn['room']}: {wrn['message']}")
        print(f"{'OK' if report['ok'] else 'FAIL'} — {report['rooms']} room(s), "
              f"{len(report['problems'])} problem(s), {len(report['warnings'])} warning(s)")
    return 0 if report["ok"] else 1


def cmd_fix(args) -> int:
    data = cb.read_map(args.map)
    only = set(args.only.split(",")) if args.only else None
    changes = fix_map(data, only)

    payload = {"map": args.map, "dry_run": bool(args.dry_run), "changes": changes}
    if args.dry_run:
        _emit(args, payload,
              text="\n".join(f"[dry-run] {c['room']}: {c['fix']} ({c['detail']})"
                             for c in changes) or "[dry-run] nothing to fix")
        return 0

    if changes:
        cb.write_map(args.map, data)
    _emit(args, payload,
          text="\n".join(f"{c['room']}: {c['fix']} ({c['detail']})" for c in changes)
               or "nothing to fix")
    return 0


def cmd_diff(args) -> int:
    a = cb.read_map(args.map)
    b = cb.read_map(args.other)
    ra = {r.get("name"): r for r in cb.get_rooms(a)}
    rb = {r.get("name"): r for r in cb.get_rooms(b)}

    added = sorted(set(rb) - set(ra))
    removed = sorted(set(ra) - set(rb))
    changed = []
    for name in sorted(set(ra) & set(rb)):
        x, y = ra[name], rb[name]
        deltas = []
        for attr in ("x", "y", "width", "height"):
            if x.get(attr) != y.get(attr):
                deltas.append(f"{attr}: {x.get(attr)} -> {y.get(attr)}")
        ea, eb = len(room_entities(x)), len(room_entities(y))
        if ea != eb:
            deltas.append(f"entities: {ea} -> {eb}")
        sa = cb.find_child(x, "solids")
        sb = cb.find_child(y, "solids")
        if (sa.get("innerText", "") if sa else "") != (sb.get("innerText", "") if sb else ""):
            deltas.append("tiles changed")
        if deltas:
            changed.append({"room": name, "changes": deltas})

    payload = {"a": args.map, "b": args.other,
               "added": added, "removed": removed, "changed": changed}
    if args.json:
        _emit(args, payload)
    else:
        for n in added:
            print(f"+ {n}")
        for n in removed:
            print(f"- {n}")
        for c in changed:
            print(f"~ {c['room']}: {'; '.join(c['changes'])}")
        if not (added or removed or changed):
            print("maps are identical (rooms/entities/tiles)")
    return 0


# ─── Entry point ─────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="pcgscene",
        description="Celeste PCG pipeline CLI — scan, validate, repair, "
                    "generate .bin maps. Deterministic; no AI.")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p, needs_map=True):
        if needs_map:
            p.add_argument("map", help="path to the .bin map file")
        p.add_argument("--json", action="store_true", help="machine-readable output")
        p.add_argument("--out", help="write JSON to this file instead of stdout")
        p.add_argument("--seed", type=int, default=None, help="RNG seed (reproducible)")

    p = sub.add_parser("scan", help="score every room + map connectivity")
    common(p)
    p.add_argument("--paths", type=int, default=5, help="exit pairs sampled per room")
    p.set_defaults(func=cmd_scan)

    p = sub.add_parser("score", help="deep report for one room")
    common(p)
    p.add_argument("--room", "-r", required=True, help="room name")
    p.add_argument("--paths", type=int, default=5)
    p.set_defaults(func=cmd_score)

    p = sub.add_parser("validate", help="in-game readiness checklist (exit 0/1)")
    common(p)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("fix", help="safe auto-repairs (backed-up atomic write)")
    common(p)
    p.add_argument("--only", help="comma list: spawns,names,berries (default all)")
    p.add_argument("--dry-run", action="store_true", help="report without writing")
    p.set_defaults(func=cmd_fix)

    p = sub.add_parser("diff", help="compare two maps")
    common(p)
    p.add_argument("other", help="second .bin map file")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("generate", help="preset-driven generation (full build)")
    common(p)
    p.add_argument("--preset", default="simple_fair",
                   choices=sorted(GENERATE_PRESETS))
    p.add_argument("--rooms", type=int, default=None, help="override room count")
    p.add_argument("--train", help="train on this .bin instead of the target map")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_generate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
