"""
Seeded procedural terrain generator for loenn-mcp.

Inspired by AliShazly/map-generator, this module generates 2D terrain using:
  - Perlin noise for organic heightmaps and biome distribution
  - Voronoi diagrams for regional variety and biome boundaries
  - Seeded RNG for fully reproducible generation

The output is a Celeste-compatible map structure with rooms, tiles, and entities
placed according to the generated terrain.

Biomes
------
mountain   — dense solid tiles, tight platforms, spikes
forest     — moderate density, many platforms, springs
plains     — open spaces, gentle platforms, collectibles
lake       — jump-through platforms over gaps, refills
cave       — enclosed spaces, crumble blocks, refills
summit    — sparse platforms at the top, windy hazards

Generation Parameters
---------------------
seed:        Integer seed for reproducible output
width:       Map width in rooms (default 4)
height:      Map height in rooms (default 3)
frequency:   Perlin noise frequency — lower = smoother terrain (default 8)
voronoi_pts: Number of Voronoi region centres (default 12)
biome_set:   Which biomes to include (default: all)
difficulty:  1-5 scale affecting hazard density (default 3)
"""

from __future__ import annotations

import hashlib
import math
import random
from typing import Any, Dict, List, Optional, Tuple


# ─── Perlin Noise (pure Python, no scipy/numpy required) ─────────────────────

class PerlinNoise:
    """2D Perlin noise generator with seeded permutation table.

    Based on the improved Perlin noise algorithm.  Fully self-contained
    with no external dependencies beyond the standard library.
    """

    _GRAD2 = [
        (1, 1), (-1, 1), (1, -1), (-1, -1),
        (1, 0), (-1, 0), (0, 1), (0, -1),
    ]

    def __init__(self, seed: int = 0):
        self.seed = seed
        # Build permutation table
        perm = list(range(256))
        random.Random(seed).shuffle(perm)
        self._perm = perm + perm  # double for overflow

    def _fade(self, t: float) -> float:
        """Quintic fade curve: 6t^5 - 15t^4 + 10t^3"""
        return t * t * t * (t * (t * 6 - 15) + 10)

    def _lerp(self, a: float, b: float, t: float) -> float:
        return a + t * (b - a)

    def _grad(self, hash_val: int, x: float, y: float) -> float:
        g = self._GRAD2[hash_val % 8]
        return g[0] * x + g[1] * y

    def noise2d(self, x: float, y: float) -> float:
        """Compute 2D Perlin noise at (x, y). Returns value in [-1, 1]."""
        xi = int(math.floor(x)) & 255
        yi = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)

        u = self._fade(xf)
        v = self._fade(yf)

        p = self._perm
        aa = p[p[xi] + yi]
        ab = p[p[xi] + yi + 1]
        ba = p[p[xi + 1] + yi]
        bb = p[p[xi + 1] + yi + 1]

        x1 = self._lerp(self._grad(aa, xf, yf), self._grad(ba, xf - 1, yf), u)
        x2 = self._lerp(
            self._grad(ab, xf, yf - 1), self._grad(bb, xf - 1, yf - 1), u
        )
        return self._lerp(x1, x2, v)

    def fractal(
        self, x: float, y: float, octaves: int = 4, persistence: float = 0.5
    ) -> float:
        """Multi-octave fractal noise. Returns value in approximately [-1, 1]."""
        total = 0.0
        amplitude = 1.0
        frequency = 1.0
        max_val = 0.0
        for _ in range(octaves):
            total += self.noise2d(x * frequency, y * frequency) * amplitude
            max_val += amplitude
            amplitude *= persistence
            frequency *= 2.0
        return total / max_val


# ─── Voronoi Diagram (simple implementation) ─────────────────────────────────

def _generate_voronoi_points(
    rng: random.Random,
    count: int,
    width: int,
    height: int,
) -> List[Tuple[float, float]]:
    """Generate *count* random points within [0, width) × [0, height)."""
    return [(rng.uniform(0, width), rng.uniform(0, height)) for _ in range(count)]


def _assign_voronoi_region(
    x: float,
    y: float,
    points: List[Tuple[float, float]],
) -> int:
    """Return the index of the closest Voronoi centre to (x, y)."""
    best_idx = 0
    best_dist = float("inf")
    for i, (px, py) in enumerate(points):
        dist = (x - px) ** 2 + (y - py) ** 2
        if dist < best_dist:
            best_dist = dist
            best_idx = i
    return best_idx


# ─── Biome System ────────────────────────────────────────────────────────────

BIOMES = ("mountain", "forest", "plains", "lake", "cave", "summit")

# Biome properties: (solid_density, platform_count_range, hazard_prob, collectible_prob)
_BIOME_PROPS: Dict[str, Dict[str, Any]] = {
    "mountain": {
        "solid_density": 0.45,
        "platforms": (3, 7),
        "hazard_prob": 0.6,
        "collectible_prob": 0.2,
        "preferred_hazard": "spikes",
        "tile_char": "3",
    },
    "forest": {
        "solid_density": 0.30,
        "platforms": (4, 8),
        "hazard_prob": 0.3,
        "collectible_prob": 0.5,
        "preferred_hazard": "spikes",
        "tile_char": "5",
    },
    "plains": {
        "solid_density": 0.15,
        "platforms": (2, 5),
        "hazard_prob": 0.15,
        "collectible_prob": 0.65,
        "preferred_hazard": "spikes",
        "tile_char": "1",
    },
    "lake": {
        "solid_density": 0.10,
        "platforms": (3, 6),
        "hazard_prob": 0.1,
        "collectible_prob": 0.4,
        "preferred_hazard": "spikes",
        "tile_char": "9",
    },
    "cave": {
        "solid_density": 0.50,
        "platforms": (2, 5),
        "hazard_prob": 0.45,
        "collectible_prob": 0.35,
        "preferred_hazard": "crumbleBlock",
        "tile_char": "4",
    },
    "summit": {
        "solid_density": 0.20,
        "platforms": (1, 4),
        "hazard_prob": 0.35,
        "collectible_prob": 0.3,
        "preferred_hazard": "spikes",
        "tile_char": "6",
    },
}


def _assign_biome(
    noise_val: float,
    voronoi_region: int,
    biome_set: List[str],
) -> str:
    """Determine biome based on noise value and Voronoi region."""
    # Use voronoi region to pick base biome index
    base_idx = voronoi_region % len(biome_set)
    # Use noise to potentially shift the biome
    shift = int((noise_val + 1) * 1.5)  # 0-2 range shift
    idx = (base_idx + shift) % len(biome_set)
    return biome_set[idx]


# ─── Tile Grid Generation ────────────────────────────────────────────────────

TILE_AIR = "0"


def _generate_room_tiles(
    rng: random.Random,
    perlin: PerlinNoise,
    room_x: int,
    room_y: int,
    width_tiles: int,
    height_tiles: int,
    biome: str,
    difficulty: int,
    noise_frequency: float,
) -> str:
    """Generate a foreground tile grid for a room based on biome and noise.

    Returns newline-separated tile string.
    """
    props = _BIOME_PROPS.get(biome, _BIOME_PROPS["plains"])
    tc = props["tile_char"]
    density = props["solid_density"] * (0.7 + difficulty * 0.1)

    grid = [[TILE_AIR] * width_tiles for _ in range(height_tiles)]

    # Use Perlin noise to create organic terrain shapes
    for row in range(height_tiles):
        for col in range(width_tiles):
            # World-space noise coordinates
            nx = (room_x + col) / noise_frequency
            ny = (room_y + row) / noise_frequency
            n = perlin.fractal(nx, ny, octaves=3, persistence=0.5)
            # Convert noise to solid probability
            threshold = 1.0 - density * 2  # higher density = lower threshold
            if n > threshold:
                grid[row][col] = tc

    # Always add a floor (bottom row(s))
    floor_thickness = 2 if biome in ("mountain", "cave") else 1
    for r in range(height_tiles - floor_thickness, height_tiles):
        for c in range(width_tiles):
            grid[r][c] = tc

    # Add ceiling for caves
    if biome == "cave":
        for c in range(width_tiles):
            grid[0][c] = tc
            if height_tiles > 2:
                grid[1][c] = tc if rng.random() < 0.6 else TILE_AIR

    # Add some platforms using noise-guided placement
    plat_min, plat_max = props["platforms"]
    plat_count = rng.randint(plat_min, plat_max)
    # Adjust for difficulty
    plat_count = max(1, plat_count + (3 - difficulty))

    top_row = 2 if biome == "cave" else 1
    for _ in range(plat_count):
        pr = rng.randint(top_row + 1, height_tiles - floor_thickness - 2)
        start = rng.randint(1, max(1, width_tiles - 6))
        length = rng.randint(3, min(8, width_tiles - start - 1))
        for c in range(start, min(start + length, width_tiles)):
            grid[pr][c] = tc

    # Clear spawn area (bottom-left corner)
    for r in range(max(0, height_tiles - floor_thickness - 3), height_tiles - floor_thickness):
        for c in range(min(4, width_tiles)):
            grid[r][c] = TILE_AIR

    return "\n".join("".join(row) for row in grid)


# ─── Entity Generation ────────────────────────────────────────────────────────

def _generate_room_entities(
    rng: random.Random,
    width_px: int,
    height_px: int,
    biome: str,
    difficulty: int,
    is_first_room: bool,
) -> List[Dict[str, Any]]:
    """Generate entities for a room based on biome and difficulty."""
    props = _BIOME_PROPS.get(biome, _BIOME_PROPS["plains"])
    entities: List[Dict[str, Any]] = []
    next_id = 1

    # Player spawn (only in first room)
    if is_first_room:
        entities.append({
            "__name": "player",
            "__children": [],
            "id": next_id,
            "x": 24,
            "y": height_px - 32,
        })
        next_id += 1

    # Hazards
    hazard_count = int(difficulty * props["hazard_prob"] * 3)
    preferred = props["preferred_hazard"]
    for _ in range(hazard_count):
        entities.append({
            "__name": preferred,
            "__children": [],
            "id": next_id,
            "x": rng.randint(24, width_px - 24),
            "y": height_px - 16,
        })
        next_id += 1

    # Collectibles
    if rng.random() < props["collectible_prob"]:
        entities.append({
            "__name": "strawberry",
            "__children": [],
            "id": next_id,
            "x": rng.randint(width_px // 4, width_px * 3 // 4),
            "y": rng.randint(height_px // 4, height_px * 3 // 4),
        })
        next_id += 1

    # Refills (for lake and cave biomes)
    if biome in ("lake", "cave") and rng.random() < 0.5:
        entities.append({
            "__name": "refill",
            "__children": [],
            "id": next_id,
            "x": rng.randint(width_px // 3, width_px * 2 // 3),
            "y": rng.randint(height_px // 3, height_px * 2 // 3),
        })
        next_id += 1

    # Springs (for forest and plains)
    if biome in ("forest", "plains") and rng.random() < 0.4:
        entities.append({
            "__name": "spring",
            "__children": [],
            "id": next_id,
            "x": rng.randint(width_px // 3, width_px * 2 // 3),
            "y": height_px - 16,
        })
        next_id += 1

    # Jump-throughs (for lake biome)
    if biome == "lake":
        jt_count = rng.randint(2, 4)
        for _ in range(jt_count):
            entities.append({
                "__name": "jumpThru",
                "__children": [],
                "id": next_id,
                "x": rng.randint(16, width_px - 48),
                "y": rng.randint(height_px // 3, height_px * 2 // 3),
                "width": rng.choice([16, 24, 32]),
            })
            next_id += 1

    return entities


# ─── Full Map Generation ─────────────────────────────────────────────────────

def generate_terrain_map(
    seed: int = 42,
    width_rooms: int = 4,
    height_rooms: int = 3,
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
    frequency: float = 8.0,
    voronoi_points: int = 12,
    biome_set: Optional[List[str]] = None,
    difficulty: int = 3,
    package_name: str = "TerrainGen",
) -> Dict[str, Any]:
    """Generate a complete Celeste map using Perlin noise and Voronoi biomes.

    Parameters
    ----------
    seed:
        Integer seed for reproducible generation.
    width_rooms:
        Number of rooms horizontally.
    height_rooms:
        Number of rooms vertically.
    room_width_tiles:
        Width of each room in tiles (default 40 = 320 px).
    room_height_tiles:
        Height of each room in tiles (default 23 = 184 px).
    frequency:
        Perlin noise frequency (lower = smoother terrain).
    voronoi_points:
        Number of Voronoi region centres for biome placement.
    biome_set:
        List of biome names to use. Defaults to all biomes.
    difficulty:
        1-5 difficulty scale affecting hazard density.
    package_name:
        Celeste map package name.

    Returns
    -------
    Complete map element dict ready for celeste_bin.write_map().
    """
    if biome_set is None:
        biome_set = list(BIOMES)
    else:
        biome_set = [b for b in biome_set if b in BIOMES]
        if not biome_set:
            biome_set = list(BIOMES)

    difficulty = max(1, min(5, difficulty))

    rng = random.Random(seed)
    perlin = PerlinNoise(seed)

    # Total map dimensions in tiles
    total_w = width_rooms * room_width_tiles
    total_h = height_rooms * room_height_tiles

    # Generate Voronoi points for biome regions
    vor_pts = _generate_voronoi_points(rng, voronoi_points, total_w, total_h)

    # Pre-compute biome for each Voronoi region
    region_biomes: List[str] = []
    for i in range(voronoi_points):
        # Use noise at the Voronoi point to determine biome
        px, py = vor_pts[i]
        n = perlin.noise2d(px / frequency, py / frequency)
        biome = _assign_biome(n, i, biome_set)
        region_biomes.append(biome)

    # Generate rooms
    rooms: List[Dict[str, Any]] = []
    room_idx = 0

    for gy in range(height_rooms):
        for gx in range(width_rooms):
            # Room centre in tile coordinates
            cx = gx * room_width_tiles + room_width_tiles // 2
            cy = gy * room_height_tiles + room_height_tiles // 2

            # Determine biome from Voronoi region
            region_idx = _assign_voronoi_region(cx, cy, vor_pts)
            biome = region_biomes[region_idx]

            # Room position in pixels
            x_px = gx * room_width_tiles * 8
            y_px = gy * room_height_tiles * 8
            width_px = room_width_tiles * 8
            height_px = room_height_tiles * 8

            # Generate tiles
            fg_tiles = _generate_room_tiles(
                rng, perlin,
                gx * room_width_tiles, gy * room_height_tiles,
                room_width_tiles, room_height_tiles,
                biome, difficulty, frequency,
            )

            # Generate entities
            is_first = (room_idx == 0)
            entity_list = _generate_room_entities(
                rng, width_px, height_px, biome, difficulty, is_first,
            )

            # Background (air)
            air_row = TILE_AIR * room_width_tiles
            bg_tiles = "\n".join([air_row] * room_height_tiles)

            # Object tiles (empty)
            obj_row = ",".join(["-1"] * room_width_tiles)
            obj_tiles = "\n".join([obj_row] * room_height_tiles)

            # Room name
            row_letter = chr(ord("a") + (gy % 26))
            room_name = f"lvl_{row_letter}-{gx + 1:02d}"

            room_element: Dict[str, Any] = {
                "__name": "level",
                "name": room_name,
                "x": x_px,
                "y": y_px,
                "width": width_px,
                "height": height_px,
                "music": "",
                "alt_music": "",
                "ambience": "",
                "dark": biome == "cave",
                "space": False,
                "underwater": biome == "lake",
                "whisper": False,
                "disableDownTransition": False,
                "windPattern": "Left" if biome == "summit" else "None",
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
                        "innerText": bg_tiles,
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
                    {"__name": "entities", "__children": entity_list},
                    {"__name": "triggers", "__children": []},
                    {"__name": "fgdecals", "__children": []},
                    {"__name": "bgdecals", "__children": []},
                ],
            }

            rooms.append(room_element)
            room_idx += 1

    # Build complete map structure
    map_data: Dict[str, Any] = {
        "__name": "Map",
        "package": package_name,
        "__children": [
            {
                "__name": "levels",
                "__children": rooms,
            },
            {
                "__name": "Style",
                "__children": [
                    {"__name": "Foregrounds", "__children": []},
                    {"__name": "Backgrounds", "__children": []},
                ],
            },
            {
                "__name": "Filler",
                "__children": [],
            },
        ],
    }

    return map_data


def get_biome_summary(
    seed: int,
    width_rooms: int,
    height_rooms: int,
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
    frequency: float = 8.0,
    voronoi_points: int = 12,
    biome_set: Optional[List[str]] = None,
) -> str:
    """Return a text summary of the biome layout for given parameters.

    Useful for previewing what the generator will produce without
    actually generating the full map.
    """
    if biome_set is None:
        biome_set = list(BIOMES)
    else:
        biome_set = [b for b in biome_set if b in BIOMES]
        if not biome_set:
            biome_set = list(BIOMES)

    rng = random.Random(seed)
    perlin = PerlinNoise(seed)

    total_w = width_rooms * room_width_tiles
    total_h = height_rooms * room_height_tiles

    vor_pts = _generate_voronoi_points(rng, voronoi_points, total_w, total_h)

    region_biomes: List[str] = []
    for i in range(voronoi_points):
        px, py = vor_pts[i]
        n = perlin.noise2d(px / frequency, py / frequency)
        biome = _assign_biome(n, i, biome_set)
        region_biomes.append(biome)

    # Build ASCII biome map
    lines = [f"Biome Layout (seed={seed}, {width_rooms}x{height_rooms} rooms):"]
    lines.append("")

    biome_icons = {
        "mountain": "M",
        "forest": "F",
        "plains": "P",
        "lake": "~",
        "cave": "C",
        "summit": "^",
    }

    for gy in range(height_rooms):
        row_chars = []
        for gx in range(width_rooms):
            cx = gx * room_width_tiles + room_width_tiles // 2
            cy = gy * room_height_tiles + room_height_tiles // 2
            region_idx = _assign_voronoi_region(cx, cy, vor_pts)
            biome = region_biomes[region_idx]
            row_chars.append(f"[{biome_icons.get(biome, '?')}]")
        lines.append(" ".join(row_chars))

    lines.append("")
    lines.append("Legend: M=mountain, F=forest, P=plains, ~=lake, C=cave, ^=summit")

    return "\n".join(lines)
