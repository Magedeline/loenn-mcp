"""
Procedural Generation (PCG) module for loenn-mcp.

Provides:
  - Room pattern extraction from existing .bin maps
  - Strategy-based room generation with seeded randomness
  - Pattern library JSON persistence for reuse across sessions

Strategies
----------
balanced     — mix of exploration and challenge elements
exploration  — open spaces, gentle platforming, few hazards
challenge    — complex tile layouts, many hazards, tight jumps
speedrun     — linear path, minimal platforms, fast flow

Model profiles
--------------
deterministic — fixed/predictable generation (reproducible output; good for
                CI pipelines and layout planners)
creative      — high-entropy seed, maximum variety across calls
architect     — emphasises room shape and spatial connectivity over content

External source compliance
--------------------------
The ``ingest_external_map`` MCP tool (defined in server.py) downloads maps
only with explicit ``confirm_download=True`` and always records attribution
metadata alongside the extracted patterns.  GameBanana maps remain subject
to their individual mod licenses; always credit original authors.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ─── Public constants ─────────────────────────────────────────────────────────

STRATEGIES: Tuple[str, ...] = ("balanced", "exploration", "challenge", "speedrun")
MODEL_PROFILES: Tuple[str, ...] = ("deterministic", "creative", "architect")

LIBRARY_VERSION = "2.0"

# Characters that represent solid tiles in Celeste .bin tile strings.
# '0' is air; any other recognised char is treated as solid for analysis.
TILE_SOLID_CHARS: frozenset = frozenset(
    "123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
TILE_AIR = "0"
_DEFAULT_TILE_CHAR = "3"  # grey stone — common in vanilla Celeste levels

# Gameplay-role classification sets (Celeste entity names)
_HAZARD_ENTITIES: frozenset = frozenset({
    "spikes", "spikeUp", "spikeDown", "spikeLeft", "spikeRight",
    "crumbleBlock", "fallingBlock", "moveBlock", "crushBlock",
    "kevinsBarrier",
})
_COLLECTIBLE_ENTITIES: frozenset = frozenset({
    "strawberry", "goldenBerry", "cassette", "blackGem", "heartGem",
})
_NAV_ENTITIES: frozenset = frozenset({
    "checkpoint", "jumpThru", "spring", "refill",
})

# Size-class thresholds: (class_name, max_width_px, max_height_px)
_SIZE_CLASSES: List[Tuple[str, int, int]] = [
    ("tiny",   160,  88),
    ("small",  320, 184),
    ("medium", 480, 280),
    ("large",  640, 360),
]


# ─── Size helpers ─────────────────────────────────────────────────────────────

def classify_room_size(width: int, height: int) -> str:
    """Return a human-readable size class for *width* × *height* pixels."""
    for cls, max_w, max_h in _SIZE_CLASSES:
        if width <= max_w and height <= max_h:
            return cls
    return "huge"


# ─── Internal extraction helpers ─────────────────────────────────────────────

def _count_entity_types(room: Dict[str, Any]) -> Dict[str, int]:
    for child in room.get("__children", []):
        if child.get("__name") == "entities":
            counts: Dict[str, int] = {}
            for e in child.get("__children", []):
                t = e.get("__name", "unknown")
                counts[t] = counts.get(t, 0) + 1
            return counts
    return {}


def _count_trigger_types(room: Dict[str, Any]) -> Dict[str, int]:
    for child in room.get("__children", []):
        if child.get("__name") == "triggers":
            counts: Dict[str, int] = {}
            for t in child.get("__children", []):
                tp = t.get("__name", "unknown")
                counts[tp] = counts.get(tp, 0) + 1
            return counts
    return {}


def _analyze_tiles(room: Dict[str, Any]) -> Dict[str, Any]:
    """Compute tile coverage statistics for a room's foreground layer."""
    for child in room.get("__children", []):
        if child.get("__name") == "solids":
            text = child.get("innerText", "")
            rows = [r for r in text.split("\n") if r]
            if not rows:
                break
            total = sum(len(r) for r in rows)
            solid = sum(1 for r in rows for ch in r if ch in TILE_SOLID_CHARS)
            solid_pct = solid / total if total > 0 else 0.0
            has_floor = any(ch in TILE_SOLID_CHARS for ch in rows[-1])
            has_ceiling = any(ch in TILE_SOLID_CHARS for ch in rows[0])
            left_col = [r[0] if r else TILE_AIR for r in rows]
            right_col = [r[-1] if r else TILE_AIR for r in rows]
            has_walls = any(
                ch in TILE_SOLID_CHARS for ch in left_col + right_col
            )
            return {
                "solid_pct": round(solid_pct, 4),
                "has_floor": has_floor,
                "has_ceiling": has_ceiling,
                "has_walls": has_walls,
            }
    return {
        "solid_pct": 0.0,
        "has_floor": False,
        "has_ceiling": False,
        "has_walls": False,
    }


# ─── Pattern extraction ───────────────────────────────────────────────────────

def extract_pattern(
    room: Dict[str, Any],
    source_info: str = "",
    attribution: str = "",
) -> Dict[str, Any]:
    """Extract a reusable pattern record from a room element dict.

    Parameters
    ----------
    room:
        A room dict as returned by ``celeste_bin.get_rooms()``.
    source_info:
        Human-readable source description (e.g. map filename or URL).
    attribution:
        Author / licence attribution string to store alongside the pattern.
    """
    w = room.get("width", 320)
    h = room.get("height", 184)
    ent_types = _count_entity_types(room)
    trig_types = _count_trigger_types(room)
    total_ents = sum(ent_types.values())
    tile_area = (w // 8) * (h // 8)
    entity_density = round(total_ents / max(tile_area, 1), 6)
    tile_stats = _analyze_tiles(room)

    tags: List[str] = [classify_room_size(w, h)]
    if any(e in ent_types for e in _HAZARD_ENTITIES):
        tags.append("hazards")
    if any(e in ent_types for e in _COLLECTIBLE_ENTITIES):
        tags.append("collectible")
    if "checkpoint" in ent_types:
        tags.append("checkpoint")
    if "player" in ent_types:
        tags.append("spawn")
    if tile_stats["solid_pct"] > 0.4:
        tags.append("dense")
    elif tile_stats["solid_pct"] < 0.1:
        tags.append("open")

    raw_id = f"{source_info}:{room.get('name', '')}:{w}:{h}"
    pid = hashlib.sha1(raw_id.encode()).hexdigest()[:12]

    return {
        "id": pid,
        "source": source_info,
        "attribution": attribution,
        "room_name": room.get("name", ""),
        "width": w,
        "height": h,
        "size_class": classify_room_size(w, h),
        "entity_density": entity_density,
        "entity_types": ent_types,
        "trigger_types": trig_types,
        "tile_motifs": tile_stats,
        "tags": tags,
    }


# ─── Pattern library ─────────────────────────────────────────────────────────

def load_library(path: Path) -> Dict[str, Any]:
    """Load a pattern library from *path*, returning an empty library on miss."""
    if not path.exists():
        return {"version": LIBRARY_VERSION, "patterns": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("patterns"), list):
            data["patterns"] = []
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": LIBRARY_VERSION, "patterns": []}


def save_library(path: Path, library: Dict[str, Any]) -> None:
    """Persist *library* to *path* as formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(library, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def merge_patterns(
    library: Dict[str, Any],
    new_patterns: List[Dict[str, Any]],
) -> int:
    """Add *new_patterns* to *library*, deduplicating by ``id``.

    Returns the number of patterns actually added.
    """
    existing_ids = {p["id"] for p in library["patterns"]}
    added = 0
    for p in new_patterns:
        if p["id"] not in existing_ids:
            library["patterns"].append(p)
            existing_ids.add(p["id"])
            added += 1
    return added


# ─── Seed resolution ──────────────────────────────────────────────────────────

def resolve_seed(
    seed: int,
    strategy: str,
    model_profile: str,
) -> int:
    """Return a concrete integer seed for the RNG.

    * ``seed >= 0``:  used as-is (fully reproducible).
    * ``seed == -1`` + ``model_profile == "deterministic"``:  stable seed
      derived from the strategy name (same output every run).
    * ``seed == -1`` + any other profile:  random seed (creative variety).
    """
    if seed >= 0:
        return seed
    if model_profile == "deterministic":
        return abs(hash(strategy)) & 0xFFFF_FFFF
    return random.randint(0, 0xFFFF_FFFF)


# ─── Strategy-based pattern selection ────────────────────────────────────────

def pick_pattern(
    rng: random.Random,
    patterns: List[Dict[str, Any]],
    strategy: str,
    size_class: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Select one pattern from *patterns* according to *strategy*.

    Returns ``None`` if *patterns* is empty.
    """
    if not patterns:
        return None

    pool = patterns
    if size_class:
        sized = [p for p in patterns if p.get("size_class") == size_class]
        if sized:
            pool = sized

    if strategy == "exploration":
        preferred = [p for p in pool if "open" in p.get("tags", [])]
    elif strategy == "challenge":
        preferred = [p for p in pool if "hazards" in p.get("tags", [])]
    elif strategy == "speedrun":
        preferred = [
            p for p in pool
            if "small" in p.get("tags", []) or "tiny" in p.get("tags", [])
        ]
    else:  # balanced / architect / default
        preferred = pool

    return rng.choice(preferred if preferred else pool)


# ─── Tile grid generation ─────────────────────────────────────────────────────

def generate_tile_grid(
    rng: random.Random,
    width: int,
    height: int,
    strategy: str,
    reference_pattern: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a foreground tile grid for a room of *width* × *height* px.

    Returns a newline-separated string of tile rows (each char = one 8×8 tile).
    Uses *strategy* for density and *reference_pattern* tile motifs when
    available.
    """
    tw = width // 8
    th = height // 8

    grid = [[TILE_AIR] * tw for _ in range(th)]
    tc = _DEFAULT_TILE_CHAR

    motifs = (reference_pattern or {}).get("tile_motifs", {})

    # ── Floor ──
    floor_thick = 2 if strategy == "challenge" else 1
    for r in range(th - floor_thick, th):
        for c in range(tw):
            grid[r][c] = tc

    # ── Ceiling ──
    add_ceiling = motifs.get("has_ceiling", False)
    if strategy == "challenge":
        add_ceiling = add_ceiling or rng.random() < 0.55
    elif strategy == "exploration":
        add_ceiling = False  # exploration rooms feel open
    if add_ceiling:
        for c in range(tw):
            grid[0][c] = tc

    # ── Side-wall stubs (help close vertical shafts) ──
    if motifs.get("has_walls", False) or strategy == "challenge":
        stub_h = rng.randint(th // 4, th // 2)
        for r in range(th - floor_thick - stub_h, th - floor_thick):
            grid[r][0] = tc
            grid[r][tw - 1] = tc

    # ── Platform count by strategy ──
    if strategy == "speedrun":
        plat_count = rng.randint(1, 3)
    elif strategy == "exploration":
        plat_count = rng.randint(2, 5)
    elif strategy == "challenge":
        plat_count = rng.randint(4, 9)
    else:  # balanced / architect
        plat_count = rng.randint(2, 6)

    top_row = 1 if add_ceiling else 0
    for _ in range(plat_count):
        pr = rng.randint(top_row + 1, th - floor_thick - 2)
        start = rng.randint(1, max(1, tw - 5))
        length = rng.randint(3, min(9, tw - start - 1))
        for c in range(start, min(start + length, tw)):
            grid[pr][c] = tc

    return "\n".join("".join(row) for row in grid)


# ─── Entity generation ────────────────────────────────────────────────────────

def generate_entities_for_room(
    rng: random.Random,
    width: int,
    height: int,
    strategy: str,
    reference_pattern: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Return a list of entity dicts for a generated room.

    Always includes exactly one ``player`` spawn.  Additional hazards,
    collectibles, and navigation aids are added per *strategy*.
    """
    entities: List[Dict[str, Any]] = []
    next_id = 1

    # ── Spawn (always) ──
    entities.append({
        "__name": "player",
        "__children": [],
        "id": next_id,
        "x": 40,
        "y": height - 24,
    })
    next_id += 1

    ent_ref = (reference_pattern or {}).get("entity_types", {})

    # ── Hazard counts ──
    if strategy == "exploration":
        hazard_count = rng.randint(0, 2)
        collectible_chance = 0.65
    elif strategy == "challenge":
        hazard_count = rng.randint(3, 8)
        collectible_chance = 0.30
    elif strategy == "speedrun":
        hazard_count = rng.randint(1, 3)
        collectible_chance = 0.20
    else:  # balanced
        hazard_count = rng.randint(1, 4)
        collectible_chance = 0.50

    # Prefer hazard type seen in reference pattern
    preferred_hazard = "spikes"
    if ent_ref:
        known = [e for e in ent_ref if e in _HAZARD_ENTITIES]
        if known:
            preferred_hazard = max(known, key=lambda e: ent_ref[e])

    for _ in range(hazard_count):
        entities.append({
            "__name": preferred_hazard,
            "__children": [],
            "id": next_id,
            "x": rng.randint(16, width - 16),
            "y": height - 16,
        })
        next_id += 1

    # ── Collectible ──
    if rng.random() < collectible_chance:
        etype = (
            "goldenBerry"
            if (ent_ref.get("goldenBerry", 0) > 0 and rng.random() < 0.3)
            else "strawberry"
        )
        entities.append({
            "__name": etype,
            "__children": [],
            "id": next_id,
            "x": rng.randint(width // 4, width * 3 // 4),
            "y": rng.randint(height // 4, height * 3 // 4),
        })
        next_id += 1

    # ── Refill (restore dash) ──
    if strategy in ("challenge", "balanced") and rng.random() < 0.40:
        entities.append({
            "__name": "refill",
            "__children": [],
            "id": next_id,
            "x": rng.randint(width // 3, width * 2 // 3),
            "y": rng.randint(height // 3, height * 2 // 3),
        })
        next_id += 1

    # ── Spring (bounce pad) — architect profile tends to place more ──
    if strategy == "exploration" and rng.random() < 0.35:
        entities.append({
            "__name": "spring",
            "__children": [],
            "id": next_id,
            "x": rng.randint(width // 3, width * 2 // 3),
            "y": height - 16,
        })
        next_id += 1

    return entities


# ─── Room validation ──────────────────────────────────────────────────────────

def validate_room_structure(room: Dict[str, Any]) -> List[str]:
    """Return a list of warning strings for a room dict.

    An empty list means the room passes all checks.  This function never
    raises; it only returns findings so callers can decide how to proceed.
    """
    warnings: List[str] = []
    w = room.get("width", 0)
    h = room.get("height", 0)

    if w <= 0 or h <= 0:
        warnings.append(f"Invalid dimensions: {w}x{h}")
    if w % 8 != 0:
        warnings.append(f"Width {w} is not a multiple of 8")
    if h % 8 != 0:
        warnings.append(f"Height {h} is not a multiple of 8")

    ent_types = _count_entity_types(room)
    if "player" not in ent_types:
        warnings.append("No 'player' spawn entity found")

    tile_stats = _analyze_tiles(room)
    if tile_stats["solid_pct"] == 0.0:
        warnings.append("Tile grid is entirely air — room may be unplayable")
    if not tile_stats["has_floor"]:
        warnings.append("No floor tiles detected — player will fall out of bounds")

    for etype, count in ent_types.items():
        for child in room.get("__children", []):
            if child.get("__name") != "entities":
                continue
            for ent in child.get("__children", []):
                if ent.get("__name") != etype:
                    continue
                ex = ent.get("x", 0)
                ey = ent.get("y", 0)
                if not (0 <= ex <= w) or not (0 <= ey <= h):
                    warnings.append(
                        f"Entity '{etype}' id={ent.get('id', '?')} "
                        f"at ({ex},{ey}) is outside room bounds ({w}x{h})"
                    )

    return warnings
