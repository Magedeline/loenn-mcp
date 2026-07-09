"""
PCGHelper — Python port of the PCGHelper Lönn mod algorithms.

Ported from:
  D:/Celeste/celeste/Mods/PCGHelper/Loenn/library/pcg_toolkit.lua
  D:/Celeste/celeste/Mods/PCGHelper/Loenn/scripts/celeste_skeleton.lua
  D:/Celeste/celeste/Mods/PCGHelper/Loenn/scripts/markov_level_gen.lua
  D:/Celeste/celeste/Mods/PCGHelper/Loenn/scripts/celeste_pcg_pipeline.lua

Provides:
  - LCG RNG (mirrors Lua / C# System.Random subset)
  - MdmcConfigMatrix parser
  - WFC tile generator (wave-function collapse)
  - MdMC tile generator (multi-dimensional Markov chain)
  - Skeleton layout generator (non-overlapping rooms connected edge-to-edge)
  - Platformer repair (BFS reachability + corridor carving)
  - Interestingness / difficulty scoring (paper §4.2, §4.3)
  - Entity / decal / trigger placement helpers
  - Full one-shot pipeline

Internal tile grids are stored as List[List[str]], 0-based, grid[x][y],
each cell a single-character string ('0' = air).  This mirrors the Lua
convention so algorithm bodies translate almost line-for-line.
"""

from __future__ import annotations

import math
import random as _stdlib_random
import time
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TILE = 8  # pixels per tile

MDMC_DEFAULT = "000011012"

MDMC_PRESETS: Dict[str, str] = {
    "000011012": "E+S — L-shape (paper default)",
    "000011112": "E+SW+S — 3-neighbour causal",
    "001001112": "NE+E+SW+S — 4-neighbour (paper)",
    "011011012": "N+NE+W+E+S — 5-neighbour",
    "010111010": "N+W+E+S — cross (recommended for WFC)",
    "101000101": "NW+NE+SW+SE — diagonals only",
    "111101111": "All 8 neighbours — full context",
}

_DEFAULT_TILE_CHAR = "3"  # grey stone — common in vanilla Celeste


# ---------------------------------------------------------------------------
# LCG RNG  (mirrors Lua's pcg.makeRng / C# System.Random subset)
# ---------------------------------------------------------------------------

class LcgRng:
    """32-bit LCG random number generator.

    Mirrors the subset of System.Random used by the PCGHelper C#/Lua scripts:
      next_double() -> [0, 1)
      next(hi)      -> [0, hi)        (C# Next(hi))
      next_range(lo, hi) -> [lo, hi]  (C# Next(lo, hi+1))
    """

    _M = 4_294_967_296  # 2^32

    def __init__(self, seed: int = -1):
        if seed is None or seed == -1:
            state = (int(time.time() * 1000) * 1_000_003) % self._M
            state = (state + _stdlib_random.randint(0, 999_999)) % self._M
        else:
            state = int(seed) % self._M
        self._state = state if state != 0 else 257

    def _next_raw(self) -> int:
        self._state = (self._state * 1_664_525 + 1_013_904_223) % self._M
        return self._state

    def next_double(self) -> float:
        return self._next_raw() / self._M

    def next(self, hi: int) -> int:
        if hi <= 0:
            return 0
        r = math.floor(self.next_double() * hi)
        return min(r, hi - 1)

    def next_range(self, lo: int, hi: int) -> int:
        return lo + self.next(hi - lo + 1)


# ---------------------------------------------------------------------------
# Tile grid helpers
# ---------------------------------------------------------------------------

def new_grid(w: int, h: int, fill: str = "0") -> List[List[str]]:
    return [[fill] * h for _ in range(w)]


def get_tile(grid: List[List[str]], x: int, y: int, w: int, h: int) -> str:
    if x < 0 or y < 0 or x >= w or y >= h:
        return "0"
    return grid[x][y]


def set_tile(grid: List[List[str]], x: int, y: int, w: int, h: int, v: str) -> None:
    if 0 <= x < w and 0 <= y < h:
        grid[x][y] = v


def grid_size(grid: List[List[str]]) -> Tuple[int, int]:
    w = len(grid)
    h = len(grid[0]) if w > 0 else 0
    return w, h


def grid_from_tile_string(tile_str: str) -> Tuple[List[List[str]], int, int]:
    """Parse a Celeste RLE tile string into a 0-based x/y grid."""
    rows = [r for r in tile_str.split("\n") if r]
    if not rows:
        return new_grid(0, 0), 0, 0
    h = len(rows)
    w = max(len(r) for r in rows)
    grid = new_grid(w, h)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            grid[x][y] = ch
    return grid, w, h


def grid_to_tile_string(grid: List[List[str]], w: int, h: int) -> str:
    """Serialise a 0-based grid back to a newline-separated tile string."""
    rows = []
    for y in range(h):
        rows.append("".join(grid[x][y] if x < w else "0" for x in range(w)))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# MdMC configuration matrix parser
# ---------------------------------------------------------------------------

def parse_config(cfg: str) -> List[Dict[str, int]]:
    """Parse a 9-char configuration matrix string into a list of {dy, dx} offsets.

    The matrix is row-major, NW(0) N(1) NE(2) / W(3) .(4) E(5) / SW(6) S(7) SE(8).
    '1' = use this neighbour as context; '0' = ignore; '2' = target (treated as '1').
    Falls back to MDMC_DEFAULT on invalid input.
    """
    valid = (
        cfg is not None
        and len(cfg) == 9
        and all(c in "012" for c in cfg)
    )
    if not valid:
        cfg = MDMC_DEFAULT

    offsets = []
    for k in range(9):
        if k == 4:
            continue
        if cfg[k] == "1":
            dy = k // 3 - 1
            dx = k % 3 - 1
            offsets.append({"dy": dy, "dx": dx})

    return offsets if offsets else parse_config(MDMC_DEFAULT)


# ---------------------------------------------------------------------------
# WFC tile generator
# ---------------------------------------------------------------------------

def _domain_full(n: int) -> List[bool]:
    return [True] * n


def _domain_size(dom: List[bool]) -> int:
    return sum(1 for v in dom if v)


def _allowed_union(allowed_d: Dict[int, List[bool]], dom: List[bool], n: int) -> List[bool]:
    u = [False] * n
    for ti in range(n):
        if dom[ti]:
            a = allowed_d.get(ti)
            if a:
                for ni in range(n):
                    if a[ni]:
                        u[ni] = True
    return u


def train_adjacency(
    grids: List[Tuple[List[List[str]], int, int]],
    offsets: List[Dict[str, int]],
) -> Optional[Dict[str, Any]]:
    """Train adjacency rules + tile weights from a list of (grid, w, h) tuples.

    Returns a model dict or None when the data is too uniform (< 2 tile types).
    """
    freq: Dict[str, int] = {}
    for grid, w, h in grids:
        for x in range(w):
            for y in range(h):
                t = grid[x][y]
                freq[t] = freq.get(t, 0) + 1
    freq["0"] = max(freq.get("0", 1), 1)

    if len(freq) < 2:
        return None

    sorted_tiles = sorted(freq.items(), key=lambda kv: -kv[1])
    alphabet = [ch for ch, _ in sorted_tiles[:64]]
    n = len(alphabet)
    index: Dict[str, int] = {ch: i for i, ch in enumerate(alphabet)}

    # symmetric closure of constraint directions
    dir_set: Dict[str, Dict[str, int]] = {}
    for o in offsets:
        k1 = f"{o['dy']},{o['dx']}"
        k2 = f"{-o['dy']},{-o['dx']}"
        dir_set[k1] = {"dy": o["dy"], "dx": o["dx"]}
        if o["dy"] != 0 or o["dx"] != 0:
            dir_set[k2] = {"dy": -o["dy"], "dx": -o["dx"]}
    dir_set.pop("0,0", None)
    dirs = list(dir_set.values())
    if not dirs:
        dirs = [{"dy": 0, "dx": 1}, {"dy": 0, "dx": -1},
                {"dy": 1, "dx": 0}, {"dy": -1, "dx": 0}]

    # allowed[di][ti][ni] = True if tile ti may neighbour tile ni in direction di
    allowed: Dict[int, Dict[int, List[bool]]] = {
        di: {ti: [False] * n for ti in range(n)}
        for di in range(len(dirs))
    }
    for grid, w, h in grids:
        for x in range(w):
            for y in range(h):
                ti_ch = grid[x][y]
                if ti_ch not in index:
                    continue
                ti = index[ti_ch]
                for di, d in enumerate(dirs):
                    nx2 = x + d["dx"]
                    ny2 = y + d["dy"]
                    ni_ch = get_tile(grid, nx2, ny2, w, h)
                    if ni_ch in index:
                        ni = index[ni_ch]
                        allowed[di][ti][ni] = True

    weights = [freq.get(ch, 1) for ch in alphabet]
    total_w = sum(weights)
    weights = [ww / total_w for ww in weights]

    return {
        "alphabet": alphabet,
        "n": n,
        "index": index,
        "dirs": dirs,
        "allowed": allowed,
        "weights": weights,
    }


def wfc_generate(
    model: Dict[str, Any],
    w: int,
    h: int,
    rng: LcgRng,
    max_contradictions: int = 50,
) -> Optional[List[List[str]]]:
    """WFC tile generation using the trained adjacency model.

    Returns a grid or None on contradiction.
    """
    n = model["n"]
    alphabet = model["alphabet"]
    dirs = model["dirs"]
    allowed = model["allowed"]
    weights = model["weights"]

    domains: List[List[List[bool]]] = [[_domain_full(n) for _ in range(h)] for _ in range(w)]

    def collapsed_value(dom: List[bool]) -> int:
        for i, v in enumerate(dom):
            if v:
                return i
        return -1

    def propagate(start_x: int, start_y: int) -> bool:
        queue = deque([(start_x, start_y)])
        in_queue = [[False] * h for _ in range(w)]
        in_queue[start_x][start_y] = True
        while queue:
            cx, cy = queue.popleft()
            in_queue[cx][cy] = False
            dom_c = domains[cx][cy]
            for di, d in enumerate(dirs):
                nx2, ny2 = cx + d["dx"], cy + d["dy"]
                if nx2 < 0 or ny2 < 0 or nx2 >= w or ny2 >= h:
                    continue
                dom_n = domains[nx2][ny2]
                allowed_d = allowed[di]
                new_dom = _allowed_union(allowed_d, dom_c, n)
                changed = False
                for ni in range(n):
                    if dom_n[ni] and not new_dom[ni]:
                        dom_n[ni] = False
                        changed = True
                if _domain_size(dom_n) == 0:
                    return False
                if changed and not in_queue[nx2][ny2]:
                    queue.append((nx2, ny2))
                    in_queue[nx2][ny2] = True
        return True

    for _ in range(max_contradictions):
        # Find lowest-entropy uncollapsed cell
        min_ent = float("inf")
        candidates = []
        for x in range(w):
            for y in range(h):
                sz = _domain_size(domains[x][y])
                if sz <= 1:
                    continue
                ent = -sum(
                    weights[i] * math.log(weights[i] + 1e-12)
                    for i in range(n)
                    if domains[x][y][i]
                )
                if ent < min_ent:
                    min_ent = ent
                    candidates = [(x, y)]
                elif ent == min_ent:
                    candidates.append((x, y))

        if not candidates:
            break  # all collapsed

        cx, cy = candidates[rng.next(len(candidates))]
        dom = domains[cx][cy]
        # Weighted choice
        choices = [i for i in range(n) if dom[i]]
        total = sum(weights[i] for i in choices)
        r = rng.next_double() * total
        acc = 0.0
        chosen = choices[-1]
        for i in choices:
            acc += weights[i]
            if r <= acc:
                chosen = i
                break

        new_dom = [False] * n
        new_dom[chosen] = True
        domains[cx][cy] = new_dom
        if not propagate(cx, cy):
            # Contradiction — reinitialise and retry
            domains = [[_domain_full(n) for _ in range(h)] for _ in range(w)]
            continue

    # Extract result
    grid = new_grid(w, h)
    for x in range(w):
        for y in range(h):
            idx = collapsed_value(domains[x][y])
            grid[x][y] = alphabet[idx] if idx >= 0 else "0"
    return grid


# ---------------------------------------------------------------------------
# MdMC tile generator
# ---------------------------------------------------------------------------

def train_mdmc(
    grids: List[Tuple[List[List[str]], int, int]],
    offsets: List[Dict[str, int]],
) -> Dict[str, Any]:
    """Train an n-gram frequency table from a list of (grid, w, h) tuples."""
    table: Dict[str, Dict[str, int]] = {}
    for grid, w, h in grids:
        for x in range(w):
            for y in range(h):
                ctx_parts = []
                for o in offsets:
                    nx2, ny2 = x + o["dx"], y + o["dy"]
                    ctx_parts.append(get_tile(grid, nx2, ny2, w, h))
                ctx_key = ",".join(ctx_parts)
                target = grid[x][y]
                entry = table.setdefault(ctx_key, {})
                entry[target] = entry.get(target, 0) + 1
    return {"offsets": offsets, "table": table}


def mdmc_generate(
    model: Dict[str, Any],
    w: int,
    h: int,
    rng: LcgRng,
    max_backtrack: int = 8,
    max_retries: int = 20,
    keep_border: bool = True,
) -> List[List[str]]:
    """Generate a tile grid using the MdMC model with backtracking."""
    offsets = model["offsets"]
    table = model["table"]

    def _pick(ctx_key: str) -> str:
        entry = table.get(ctx_key)
        if not entry:
            return _DEFAULT_TILE_CHAR
        total = sum(entry.values())
        r = rng.next(total)
        acc = 0
        for ch, cnt in sorted(entry.items()):
            acc += cnt
            if r < acc:
                return ch
        return next(iter(entry))

    best_grid: Optional[List[List[str]]] = None
    for _ in range(max_retries):
        grid = new_grid(w, h)
        history: List[Tuple[int, int, str]] = []
        pos = 0
        total = w * h
        backtrack_budget = max_backtrack

        while pos < total:
            x, y = pos % w, pos // w
            ctx_parts = []
            for o in offsets:
                nx2, ny2 = x + o["dx"], y + o["dy"]
                ctx_parts.append(get_tile(grid, nx2, ny2, w, h))
            ctx_key = ",".join(ctx_parts)
            ch = _pick(ctx_key)
            prev = grid[x][y]
            grid[x][y] = ch
            history.append((x, y, prev))
            pos += 1

            if table.get(ctx_key) is None and backtrack_budget > 0:
                # backtrack
                steps = min(max_backtrack, len(history))
                for _ in range(steps):
                    bx, by, bv = history.pop()
                    grid[bx][by] = bv
                pos = max(0, pos - steps)
                backtrack_budget -= 1

        if keep_border:
            for x in range(w):
                grid[x][0] = _DEFAULT_TILE_CHAR
                grid[x][h - 1] = _DEFAULT_TILE_CHAR
            for y in range(h):
                grid[0][y] = _DEFAULT_TILE_CHAR
                grid[w - 1][y] = _DEFAULT_TILE_CHAR

        best_grid = grid
        break  # accept first successful generation

    return best_grid or new_grid(w, h, _DEFAULT_TILE_CHAR)


# ---------------------------------------------------------------------------
# Cleanup pass
# ---------------------------------------------------------------------------

def cleanup_tiles(grid: List[List[str]], w: int, h: int, passes: int = 2) -> None:
    """Remove isolated tiles and fill isolated air pockets (in-place)."""
    for _ in range(passes):
        for x in range(1, w - 1):
            for y in range(1, h - 1):
                ch = grid[x][y]
                is_solid = ch != "0"
                solid_neighbours = sum(
                    1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                    if get_tile(grid, x + dx, y + dy, w, h) != "0"
                )
                if is_solid and solid_neighbours == 0:
                    grid[x][y] = "0"  # isolated tile → air
                elif not is_solid and solid_neighbours == 4:
                    grid[x][y] = _DEFAULT_TILE_CHAR  # enclosed air → solid


# ---------------------------------------------------------------------------
# Auto-tiling (smooth tile edges)
# ---------------------------------------------------------------------------

def auto_tile(grid: List[List[str]], w: int, h: int) -> None:
    """Blend tile edges for smooth transitions (in-place).

    Tiles adjacent to air that were using heavy tiles get lightened.
    This is a simplified version of the Lua script's auto-tile pass.
    """
    _HEAVY = frozenset("123456789")
    _LIGHT = "1"
    for x in range(1, w - 1):
        for y in range(1, h - 1):
            ch = grid[x][y]
            if ch not in _HEAVY:
                continue
            neighbours_air = any(
                get_tile(grid, x + dx, y + dy, w, h) == "0"
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
            )
            if neighbours_air:
                grid[x][y] = _LIGHT


# ---------------------------------------------------------------------------
# Background tile generation
# ---------------------------------------------------------------------------

def generate_bg(
    fg_grid: List[List[str]],
    w: int,
    h: int,
    enhanced: bool = False,
) -> List[List[str]]:
    """Generate background tiles from the foreground layout.

    enhanced=True uses depth-appropriate fills (deeper = denser).
    """
    bg = new_grid(w, h)
    for x in range(w):
        for y in range(h):
            fg = fg_grid[x][y]
            if fg != "0":
                # Solid in FG → mirror to BG
                if enhanced:
                    depth = y / max(h - 1, 1)
                    bg[x][y] = "9" if depth > 0.7 else ("5" if depth > 0.4 else "3")
                else:
                    bg[x][y] = "g"  # cliff face — vanilla BG tile char
    return bg


# ---------------------------------------------------------------------------
# Platformer repair (BFS reachability + corridor carving)
# ---------------------------------------------------------------------------

def _bfs_reachable(grid: List[List[str]], w: int, h: int, sx: int, sy: int) -> List[List[bool]]:
    """BFS from (sx, sy) through air cells.  Returns reachability grid."""
    visited = [[False] * h for _ in range(w)]
    if sx < 0 or sy < 0 or sx >= w or sy >= h:
        return visited
    if grid[sx][sy] != "0":
        return visited
    q = deque([(sx, sy)])
    visited[sx][sy] = True
    while q:
        cx, cy = q.popleft()
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nx2, ny2 = cx + dx, cy + dy
            if 0 <= nx2 < w and 0 <= ny2 < h and not visited[nx2][ny2] and grid[nx2][ny2] == "0":
                visited[nx2][ny2] = True
                q.append((nx2, ny2))
    return visited


def platformer_repair(
    grid: List[List[str]],
    w: int,
    h: int,
    exit_points: Optional[List[Tuple[int, int]]] = None,
) -> int:
    """Ensure all exit points are reachable from the first reachable air cell.

    Carves horizontal corridors or stepping-stones where needed.
    Returns the number of corridors carved.
    """
    if not exit_points:
        return 0

    # Find a spawn: lowest air cell in the left half
    sx, sy = w // 2, 0
    for y in range(h - 2, 0, -1):
        for x in range(1, w // 2):
            if grid[x][y] == "0" and (y + 1 >= h or grid[x][y + 1] != "0"):
                sx, sy = x, y
                break
        else:
            continue
        break

    reachable = _bfs_reachable(grid, w, h, sx, sy)
    carved = 0
    for ex, ey in exit_points:
        if 0 <= ex < w and 0 <= ey < h and not reachable[ex][ey]:
            # Carve a horizontal corridor at ey toward ex
            x1, x2 = min(sx, ex), max(sx, ex)
            for x in range(x1, x2 + 1):
                if 0 < x < w - 1 and 0 < ey < h - 1:
                    grid[x][ey] = "0"
                    if ey > 0:
                        grid[x][ey - 1] = "0"
            reachable = _bfs_reachable(grid, w, h, sx, sy)
            carved += 1
    return carved


# ---------------------------------------------------------------------------
# Interestingness & difficulty scoring (paper §4.2, §4.3)
# ---------------------------------------------------------------------------

def _count_nles(grid: List[List[str]], w: int, h: int) -> int:
    """Count Non-Linear Elements (air cells with ≥2 solid neighbours)."""
    count = 0
    for x in range(1, w - 1):
        for y in range(1, h - 1):
            if grid[x][y] != "0":
                continue
            solid = sum(
                1 for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if get_tile(grid, x + dx, y + dy, w, h) != "0"
            )
            if solid >= 2:
                count += 1
    return count


def _shannon_entropy(grid: List[List[str]], w: int, h: int) -> float:
    freq: Dict[str, int] = {}
    for x in range(w):
        for y in range(h):
            ch = grid[x][y]
            freq[ch] = freq.get(ch, 0) + 1
    total = w * h
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log(c / total + 1e-12) for c in freq.values())


def score_interestingness(
    grid: List[List[str]],
    w: int,
    h: int,
    w1: float = 1.0,
    w2: float = 1.0,
    w3: float = 1.0,
    num_paths: int = 5,
) -> float:
    """Compute interestingness I (paper §4.2).

    I = w1 * global_nle_density + w2 * local_nle_density + w3 * entropy
    """
    area = w * h
    nle = _count_nles(grid, w, h)
    global_density = nle / max(area, 1)

    # Local NLE density: sample a central AOI (area of interest)
    cx, cy = w // 2, h // 2
    rx, ry = max(1, w // 4), max(1, h // 4)
    local_area = (2 * rx) * (2 * ry)
    local_nle = sum(
        1 for x in range(cx - rx, cx + rx)
        for y in range(cy - ry, cy + ry)
        if 0 <= x < w and 0 <= y < h and grid[x][y] == "0"
        and sum(1 for dx, dy in ((1,0),(-1,0),(0,1),(0,-1))
                if get_tile(grid, x+dx, y+dy, w, h) != "0") >= 2
    )
    local_density = local_nle / max(local_area, 1)

    entropy = _shannon_entropy(grid, w, h)

    return w1 * global_density + w2 * local_density + w3 * (entropy / math.log(max(len(set(
        grid[x][y] for x in range(w) for y in range(h)
    )), 2)))


def score_difficulty(
    grid: List[List[str]],
    w: int,
    h: int,
    z1: float = 1.0,
    z2: float = 1.0,
    z3: float = 1.0,
) -> float:
    """Compute difficulty D (paper §4.3).

    D = z1 * hole_frequency + z2 * local_le_density + z3 * nle_scarcity
    """
    area = w * h

    # Hole frequency: fraction of floor tiles that have air below
    holes = 0
    floor_count = 0
    for x in range(1, w - 1):
        for y in range(1, h - 1):
            if grid[x][y] != "0" and get_tile(grid, x, y - 1, w, h) == "0":
                floor_count += 1
                if get_tile(grid, x, y + 1, w, h) == "0":
                    holes += 1
    hole_freq = holes / max(floor_count, 1)

    # Local LE density: linear elements (straight solid runs) in central AOI
    cx, cy = w // 2, h // 2
    rx, ry = max(1, w // 4), max(1, h // 4)
    local_area = (2 * rx) * (2 * ry)
    local_le = sum(
        1 for x in range(cx - rx, cx + rx)
        for y in range(cy - ry, cy + ry)
        if 0 <= x < w and 0 <= y < h and grid[x][y] != "0"
        and all(get_tile(grid, x + dx, y, w, h) != "0" for dx in (-1, 1))
    )
    local_le_density = local_le / max(local_area, 1)

    # NLE scarcity: inverse of global NLE density
    nle = _count_nles(grid, w, h)
    nle_scarcity = 1.0 - min(1.0, nle / max(area * 0.1, 1))

    return z1 * hole_freq + z2 * local_le_density + z3 * nle_scarcity


# ---------------------------------------------------------------------------
# Floor / surface analysis helpers
# ---------------------------------------------------------------------------

def _find_floor_surfaces(
    grid: List[List[str]], w: int, h: int
) -> List[Dict[str, int]]:
    """Return all air cells that have a solid tile directly below them."""
    floors = []
    for x in range(1, w - 1):
        for y in range(1, h - 1):
            if grid[x][y] == "0" and get_tile(grid, x, y + 1, w, h) != "0":
                floors.append({"x": x, "y": y})
    return floors


def _find_exposed_top_surfaces(
    grid: List[List[str]], w: int, h: int
) -> List[Dict[str, int]]:
    """Return all solid cells that have air directly above them."""
    surfaces = []
    for x in range(1, w - 1):
        for y in range(1, h - 1):
            if grid[x][y] != "0" and get_tile(grid, x, y - 1, w, h) == "0":
                surfaces.append({"x": x, "y": y})
    return surfaces


# ---------------------------------------------------------------------------
# Entity / decal / trigger placement helpers
# ---------------------------------------------------------------------------

_ENTITY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "strawberry": {"winged": False, "golden": False},
    "spikes": {"type": "default"},
    "spring": {"direction": "up"},
}

_TRIGGER_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "CameraTargetTrigger": {
        "lerpStrength": 0.5, "positionMode": "NoEffect", "xOnly": False,
        "targetEntities": "", "deleteFlag": "",
    },
}


def _build_entity(name: str, x: int, y: int, attrs: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ent: Dict[str, Any] = {"__name": name, "x": x, "y": y, "id": 0}
    defaults = _ENTITY_DEFAULTS.get(name)
    if defaults:
        ent.update(defaults)
    if attrs:
        ent.update(attrs)
    return ent


def _assign_ids(room: Dict[str, Any]) -> None:
    """Assign sequential entity/trigger IDs within a room dict."""
    nxt = 1
    for section in ("entities", "triggers"):
        el = _find_child(room, section)
        if not el:
            continue
        for e in el.get("__children", []):
            e["id"] = nxt
            nxt += 1


def _find_child(room: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for child in room.get("__children", []):
        if child.get("__name") == name:
            return child
    return None


def _ensure_section(room: Dict[str, Any], name: str) -> Dict[str, Any]:
    el = _find_child(room, name)
    if el is None:
        el = {"__name": name, "__children": []}
        room.setdefault("__children", []).append(el)
    return el


def place_entities(
    room: Dict[str, Any],
    grid: List[List[str]],
    w: int,
    h: int,
    rng: LcgRng,
    is_start: bool = False,
    is_end: bool = False,
    hazard_density: float = 0.05,
    spring_density: float = 0.02,
) -> int:
    """Place player spawn, strawberries, spikes, springs (in-place in *room*).

    Returns the number of entities placed.
    """
    ent_el = _ensure_section(room, "entities")
    ents: List[Dict[str, Any]] = ent_el.setdefault("__children", [])

    floors = _find_floor_surfaces(grid, w, h)
    surfaces = _find_exposed_top_surfaces(grid, w, h)

    placed = 0

    # Spawn
    spawn = floors[len(floors) // 4] if floors else {"x": w // 2, "y": h // 2}
    if is_start:
        ents.append(_build_entity("player", spawn["x"] * TILE, spawn["y"] * TILE))
        placed += 1

    # Golden berry — vanilla Celeste has no separate "goldenBerry" entity;
    # golden berries are "strawberry" entities with golden=True.
    if is_end:
        ents.append(_build_entity(
            "strawberry", w * TILE // 2, max(0, h * TILE // 2 - TILE * 2), {"golden": True}
        ))
        placed += 1

    # Strawberries
    berry_count = max(1, len(floors) // 6)
    berry_candidates = [f for f in floors if f["y"] >= 1]
    for i in range(min(berry_count, len(berry_candidates))):
        j = rng.next(len(berry_candidates) - i)
        berry_candidates[i], berry_candidates[i + j] = berry_candidates[i + j], berry_candidates[i]
    for s in berry_candidates[:berry_count]:
        ents.append(_build_entity(
            "strawberry", s["x"] * TILE + TILE // 2, (s["y"] - 1) * TILE
        ))
        placed += 1

    # Spikes on exposed surfaces
    spike_count = int(len(surfaces) * hazard_density)
    for s in surfaces[:spike_count]:
        ents.append(_build_entity(
            "spikes", s["x"] * TILE, s["y"] * TILE,
            {"direction": "up", "type": "default"}
        ))
        placed += 1

    # Springs in vertical passages
    vertical_air = [
        {"x": x, "y": y}
        for x in range(1, w - 1)
        for y in range(1, h - 2)
        if grid[x][y] == "0" and get_tile(grid, x, y + 1, w, h) == "0" and get_tile(grid, x, y + 2, w, h) == "0"
    ]
    spring_count = int(len(vertical_air) * spring_density)
    for s in vertical_air[:spring_count]:
        ents.append(_build_entity("spring", s["x"] * TILE, s["y"] * TILE))
        placed += 1

    _assign_ids(room)
    return placed


# Valid vanilla Celeste decal paths (relative to Graphics/Atlases/Gameplay).
# The earlier "scenery/*" names don't exist in the vanilla graphics dump, so
# Lönn and the in-game renderer silently failed to draw them — use the real
# decals/generic/* and particles/* files instead.
BG_DECAL_SET = ["particles/circle", "particles/cloud", "particles/blob"]
FG_DECAL_SET = ["decals/generic/grass_a", "decals/generic/grass_b", "decals/generic/hanginggrass_a"]

_KNOWN_DECAL_TEXTURES = frozenset({
    "particles/circle", "particles/cloud", "particles/blob", "particles/snow", "particles/fire",
    "particles/stars/00", "particles/starfield/00",
    "decals/generic/grass_a", "decals/generic/grass_b", "decals/generic/grass_c", "decals/generic/grass_d",
    "decals/generic/hanginggrass_a",
    "decals/generic/snow_a", "decals/generic/snow_b", "decals/generic/snow_c", "decals/generic/snow_d",
    "decals/generic/snow_e", "decals/generic/snow_f", "decals/generic/snow_g", "decals/generic/snow_h",
    "decals/generic/snow_i", "decals/generic/snow_j", "decals/generic/snow_k", "decals/generic/snow_l",
    "decals/generic/snow_m", "decals/generic/snow_n", "decals/generic/snow_o",
    "decals/generic/algae_a", "decals/generic/algae_b", "decals/generic/algae_c",
    "decals/generic/algae_d", "decals/generic/algae_e",
})

_DECAL_DIRECTORY_VARIANTS: Dict[str, str] = {
    "decals/generic/grass": "decals/generic/grass_a",
    "decals/generic/snow": "decals/generic/snow_a",
    "decals/generic/algae": "decals/generic/algae_a",
    "particles/stars": "particles/stars/00",
    "particles/starfield": "particles/starfield/00",
}


def resolve_decal_texture(name: Optional[str]) -> str:
    """Validate a decal texture path, falling back to a safe vanilla file.

    Directory-shaped names (e.g. "decals/generic/grass") resolve to a known
    variant; anything else unrecognised falls back to a particle that is
    guaranteed to render, rather than silently drawing nothing.
    """
    if not name:
        return "particles/circle"
    if name in _KNOWN_DECAL_TEXTURES:
        return name
    variant = _DECAL_DIRECTORY_VARIANTS.get(name)
    if variant:
        return variant
    return "particles/circle"


def place_decals(
    room: Dict[str, Any],
    grid: List[List[str]],
    w: int,
    h: int,
    rng: LcgRng,
    density: float = 0.12,
) -> int:
    """Place decals spatially (background in air, foreground on walls).

    Returns number of decals placed.
    """
    if density <= 0:
        return 0

    room_w = room.get("width", w * TILE)
    room_h = room.get("height", h * TILE)

    air_cells = [
        {"x": x, "y": y}
        for x in range(1, w - 1)
        for y in range(1, h - 1)
        if grid[x][y] == "0"
    ]
    walls = [
        {"x": x, "y": y, "side": "left" if get_tile(grid, x - 1, y, w, h) != "0" else "right"}
        for x in range(1, w - 1)
        for y in range(1, h - 1)
        if grid[x][y] != "0" and (
            get_tile(grid, x - 1, y, w, h) == "0" or get_tile(grid, x + 1, y, w, h) == "0"
        )
    ]

    placed = 0
    decals_bg = _ensure_section(room, "decalsBg")
    decals_fg = _ensure_section(room, "decalsFg")

    bg_count = int(len(air_cells) * density / 8) if BG_DECAL_SET else 0
    for _ in range(bg_count):
        if not air_cells:
            break
        c = air_cells[rng.next(len(air_cells))]
        name = resolve_decal_texture(BG_DECAL_SET[rng.next(len(BG_DECAL_SET))])
        scale = 0.4 + rng.next_double() * 0.6
        rot = rng.next_double() * 0.2 - 0.1
        dx = max(0, min(room_w - 1, c["x"] * TILE + rng.next(TILE)))
        dy = max(0, min(room_h - 1, c["y"] * TILE + rng.next(TILE)))
        decals_bg.setdefault("__children", []).append({
            "__name": "decal",
            "texture": name,
            "x": dx,
            "y": dy,
            "scaleX": scale, "scaleY": scale, "rotation": rot,
        })
        placed += 1

    fg_count = int(len(walls) * density / 2) if FG_DECAL_SET else 0
    for _ in range(fg_count):
        if not walls:
            break
        c = walls[rng.next(len(walls))]
        name = resolve_decal_texture(FG_DECAL_SET[rng.next(len(FG_DECAL_SET))])
        ox = (-rng.next_double() * 4 if c.get("side") == "left" else rng.next_double() * 4)
        oy = rng.next_double() * 4
        scale = 0.8 + rng.next_double() * 0.4
        dx = max(0, min(room_w - 1, c["x"] * TILE + ox))
        dy = max(0, min(room_h - 1, c["y"] * TILE + oy))
        decals_fg.setdefault("__children", []).append({
            "__name": "decal",
            "texture": name,
            "x": dx,
            "y": dy,
            "scaleX": scale, "scaleY": scale, "rotation": 0.0,
        })
        placed += 1

    return placed


def place_triggers(
    room: Dict[str, Any],
    grid: List[List[str]],
    w: int,
    h: int,
    rng: LcgRng,
    trigger_mode: str = "camera",
) -> int:
    """Place camera target / spawn point triggers.

    Returns number of triggers placed.
    """
    if trigger_mode == "none":
        return 0

    floors = _find_floor_surfaces(grid, w, h)
    placed = 0
    trg_el = _ensure_section(room, "triggers")
    trigs: List[Dict[str, Any]] = trg_el.setdefault("__children", [])

    if trigger_mode in ("camera", "all"):
        room_w = room.get("width", w * TILE)
        room_h = room.get("height", h * TILE)
        high_points = [f for f in floors if f["y"] < h // 2]
        for _ in range(rng.next(len(high_points) + 1)):
            high_points.append({"x": rng.next(w), "y": rng.next(h // 2)})
        cam_count = min(3, len(high_points) // 6)
        for i in range(cam_count):
            c = high_points[rng.next(len(high_points))]
            tw = min(w * TILE // 2, 160)
            th = min(h * TILE, 120)
            tx = max(0, c["x"] * TILE - tw // 2)
            ty = max(0, c["y"] * TILE - th // 2)
            # Clamp so the trigger always renders inside the room.
            if tx + tw > room_w:
                tx = max(0, room_w - tw)
            if ty + th > room_h:
                ty = max(0, room_h - th)
            trigs.append({
                "__name": "CameraTargetTrigger",
                "x": tx, "y": ty,
                "width": tw, "height": th,
                "lerpStrength": 0.5, "positionMode": "NoEffect",
                "xOnly": False, "targetEntities": "", "deleteFlag": "",
                "targetX": c["x"] * TILE,
                "targetY": c["y"] * TILE,
            })
            placed += 1

    if trigger_mode in ("spawn", "all") and floors:
        c = floors[rng.next(len(floors))]
        trigs.append({
            "__name": "SpawnPointTrigger",
            "x": c["x"] * TILE, "y": (c["y"] - 1) * TILE,
            "width": TILE * 2, "height": TILE * 2,
            "spawnX": c["x"] * TILE, "spawnY": c["y"] * TILE,
        })
        placed += 1

    _assign_ids(room)
    return placed


# ---------------------------------------------------------------------------
# Skeleton layout  (celeste_skeleton.lua)
# ---------------------------------------------------------------------------

def _snap_to_tile(px: int) -> int:
    return (px // TILE) * TILE


def _interiors_overlap(a: Dict, b: Dict) -> bool:
    return a["x"] < b["right"] and b["x"] < a["right"] and a["y"] < b["bottom"] and b["y"] < a["bottom"]


def _share_exit(a: Dict, b: Dict) -> bool:
    if a["bottom"] == b["top"] or b["bottom"] == a["top"]:
        return min(a["right"], b["right"]) - max(a["left"], b["left"]) >= TILE * 2
    if a["right"] == b["left"] or b["right"] == a["left"]:
        return min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) >= TILE * 2
    return False


def _has_adequate_exit(a: Dict, b: Dict, min_width_tiles: int) -> bool:
    min_px = min_width_tiles * TILE
    if a["bottom"] == b["top"] or b["bottom"] == a["top"]:
        return min(a["right"], b["right"]) - max(a["left"], b["left"]) >= min_px
    if a["right"] == b["left"] or b["right"] == a["left"]:
        return min(a["bottom"], b["bottom"]) - max(a["top"], b["top"]) >= min_px
    return False


def _rect(left: int, top: int, w: int, h: int) -> Dict:
    return {"x": left, "y": top, "width": w, "height": h,
            "right": left + w, "bottom": top + h, "left": left, "top": top}


_DIRECTIONS = [
    {"dx": 1, "dy": 0}, {"dx": -1, "dy": 0},
    {"dx": 0, "dy": 1}, {"dx": 0, "dy": -1},
]


def generate_skeleton(
    room_count: int = 12,
    min_w_tiles: int = 30,
    max_w_tiles: int = 50,
    min_h_tiles: int = 16,
    max_h_tiles: int = 28,
    seed: int = -1,
    max_retries: int = 50,
    name_prefix: str = "lvl_",
    min_exit_width_tiles: int = 3,
    validate_connectivity: bool = True,
    ensure_loopbacks: bool = False,
) -> Dict[str, Any]:
    """Generate a non-overlapping room skeleton layout.

    Returns a dict with 'slots' (room rects + adjacency) and 'warnings'.
    Each slot: {bounds, parent_idx, neighbours, name, is_start, is_end}
    """
    rng = LcgRng(seed)
    min_w_tiles = max(5, min_w_tiles)
    min_h_tiles = max(5, min_h_tiles)

    slots: List[Dict[str, Any]] = []
    rW = rng.next_range(min_w_tiles, max_w_tiles) * TILE
    rH = rng.next_range(min_h_tiles, max_h_tiles) * TILE
    slots.append({"bounds": _rect(0, 0, rW, rH), "parent_idx": -1, "neighbours": []})

    for i in range(1, room_count):
        placed = False
        for _ in range(max_retries):
            parent_idx = rng.next(len(slots))
            parent = slots[parent_idx]["bounds"]
            d = _DIRECTIONS[rng.next(4)]

            newW = rng.next_range(min_w_tiles, max_w_tiles) * TILE
            newH = rng.next_range(min_h_tiles, max_h_tiles) * TILE

            if d["dx"] != 0:
                nx = parent["right"] if d["dx"] > 0 else parent["left"] - newW
                overlap_h = min(parent["height"], newH)
                ny = parent["y"] + rng.next(max(1, overlap_h - TILE * 2))
            else:
                ny = parent["bottom"] if d["dy"] > 0 else parent["top"] - newH
                overlap_w = min(parent["width"], newW)
                nx = parent["x"] + rng.next(max(1, overlap_w - TILE * 2))

            nx, ny = _snap_to_tile(nx), _snap_to_tile(ny)
            candidate = _rect(nx, ny, newW, newH)

            clear = not any(_interiors_overlap(candidate, s["bounds"]) for s in slots)
            if not clear:
                continue

            slot: Dict[str, Any] = {"bounds": candidate, "parent_idx": parent_idx, "neighbours": []}
            idx = len(slots)
            slots.append(slot)

            for j in range(idx):
                if _share_exit(candidate, slots[j]["bounds"]) and _has_adequate_exit(candidate, slots[j]["bounds"], min_exit_width_tiles):
                    slot["neighbours"].append(j)
                    slots[j]["neighbours"].append(idx)

            if parent_idx not in slot["neighbours"]:
                slot["neighbours"].append(parent_idx)
                slots[parent_idx]["neighbours"].append(idx)

            placed = True
            break

    # Loopbacks
    if ensure_loopbacks:
        max_loops = max(1, len(slots) // 4)
        added = 0
        for i in range(1, len(slots)):
            if added >= max_loops:
                break
            for j in range(i):
                if j not in slots[i]["neighbours"] and _share_exit(slots[i]["bounds"], slots[j]["bounds"]):
                    slots[i]["neighbours"].append(j)
                    slots[j]["neighbours"].append(i)
                    added += 1
                    break

    # Connectivity check
    warnings: List[str] = []
    if validate_connectivity and slots:
        visited = [False] * len(slots)
        q = deque([0])
        visited[0] = True
        while q:
            idx = q.popleft()
            for nb in slots[idx]["neighbours"]:
                if not visited[nb]:
                    visited[nb] = True
                    q.append(nb)
        orphans = [i for i, v in enumerate(visited) if not v]
        if orphans:
            warnings.append(f"Orphaned rooms: {orphans}")

    # Assign names + start/end
    if not slots:
        return {"slots": [], "warnings": ["No rooms generated"]}

    # furthest room from slot[0] = END
    root = slots[0]["bounds"]
    def manhattan(s: Dict) -> int:
        b = s["bounds"]
        return abs(b["x"] + b["width"] // 2 - (root["x"] + root["width"] // 2)) + \
               abs(b["y"] + b["height"] // 2 - (root["y"] + root["height"] // 2))

    end_idx = max(range(len(slots)), key=lambda i: manhattan(slots[i]))
    for i, slot in enumerate(slots):
        slot["name"] = f"{name_prefix}{i}"
        slot["is_start"] = (i == 0)
        slot["is_end"] = (i == end_idx)

    return {"slots": slots, "warnings": warnings}


# ---------------------------------------------------------------------------
# Exit carving (connect adjacent skeleton rooms)
# ---------------------------------------------------------------------------

def carve_exits(
    grids: Dict[str, Tuple[List[List[str]], int, int]],
    slots: List[Dict[str, Any]],
) -> int:
    """Carve aligned 2×3 / 3×2 doorways between adjacent skeleton rooms.

    grids: mapping of room name → (grid, w, h)
    Returns number of exits carved.
    """
    carved = 0
    for i, slot in enumerate(slots):
        for j in slot["neighbours"]:
            if j <= i:
                continue
            other = slots[j]
            a, b = slot["bounds"], other["bounds"]
            name_a, name_b = slot["name"], other["name"]
            if name_a not in grids or name_b not in grids:
                continue
            ga, wa, ha = grids[name_a]
            gb, wb, hb = grids[name_b]

            # Horizontal adjacency
            if a["right"] == b["left"] or b["right"] == a["left"]:
                overlap_y_start = max(a["top"], b["top"])
                overlap_y_end = min(a["bottom"], b["bottom"])
                mid_y = (overlap_y_start + overlap_y_end) // 2
                # Carve opening in both rooms at mid_y (3 tiles tall)
                for dy in range(-1, 2):
                    ty_a = (mid_y - a["top"]) // TILE + dy
                    ty_b = (mid_y - b["top"]) // TILE + dy
                    if a["right"] == b["left"]:
                        if 0 < ty_a < ha - 1:
                            ga[wa - 1][ty_a] = "0"
                        if 0 < ty_b < hb - 1:
                            gb[0][ty_b] = "0"
                    else:
                        if 0 < ty_a < ha - 1:
                            ga[0][ty_a] = "0"
                        if 0 < ty_b < hb - 1:
                            gb[wb - 1][ty_b] = "0"
                carved += 1

            # Vertical adjacency
            elif a["bottom"] == b["top"] or b["bottom"] == a["top"]:
                overlap_x_start = max(a["left"], b["left"])
                overlap_x_end = min(a["right"], b["right"])
                mid_x = (overlap_x_start + overlap_x_end) // 2
                for dx in range(-1, 2):
                    tx_a = (mid_x - a["left"]) // TILE + dx
                    tx_b = (mid_x - b["left"]) // TILE + dx
                    if a["bottom"] == b["top"]:
                        if 0 < tx_a < wa - 1:
                            ga[tx_a][ha - 1] = "0"
                        if 0 < tx_b < wb - 1:
                            gb[tx_b][0] = "0"
                    else:
                        if 0 < tx_a < wa - 1:
                            ga[tx_a][0] = "0"
                        if 0 < tx_b < wb - 1:
                            gb[tx_b][hb - 1] = "0"
                carved += 1

    return carved


# ---------------------------------------------------------------------------
# Pattern mode (marioGen-v2 style, from markov_level_gen.lua)
# ---------------------------------------------------------------------------

_PATTERN_TRANSITIONS: Dict[str, List[Tuple[str, float]]] = {
    "flat_ground": [("flat_ground", 3.0), ("easy_pit", 1.0), ("platforms", 0.8),
                    ("stairs_up", 0.6), ("stairs_down", 0.6), ("wall", 0.4)],
    "easy_pit":    [("flat_ground", 2.8), ("medium_pit", 0.7), ("platforms", 0.5)],
    "medium_pit":  [("flat_ground", 2.8), ("hard_pit", 0.5), ("platforms", 0.4)],
    "hard_pit":    [("flat_ground", 3.4), ("two_jumps", 0.5), ("wall", 0.4)],
    "platforms":   [("flat_ground", 2.5), ("easy_pit", 0.8), ("stairs_up", 0.5)],
    "stairs_up":   [("high_ground", 1.5), ("stairs_down", 1.0), ("platforms", 0.4)],
    "stairs_down": [("flat_ground", 2.5), ("easy_pit", 0.8), ("wall", 0.5)],
    "high_ground": [("stairs_down", 1.5), ("platforms", 0.5), ("flat_ground", 0.8)],
    "wall":        [("flat_ground", 2.5), ("easy_pit", 0.6), ("stairs_down", 0.4)],
    "two_jumps":   [("flat_ground", 3.2), ("easy_pit", 0.5), ("platforms", 0.4)],
}


def _pick_next_pattern(current: str, rng: LcgRng) -> str:
    choices = _PATTERN_TRANSITIONS.get(current, [("flat_ground", 1.0)])
    total = sum(w for _, w in choices)
    r = rng.next_double() * total
    acc = 0.0
    for name, w in choices:
        acc += w
        if r <= acc:
            return name
    return choices[-1][0]


def _apply_pattern_segment(
    grid: List[List[str]],
    w: int,
    h: int,
    pattern: str,
    start_x: int,
    seg_w: int,
    difficulty: int,
    rng: LcgRng,
) -> None:
    """Write one pattern segment into the grid at start_x."""
    end_x = min(start_x + seg_w, w)
    floor_y = h - 2

    def solid(x: int, y: int) -> None:
        if 0 <= x < w and 0 <= y < h:
            grid[x][y] = _DEFAULT_TILE_CHAR

    def air(x: int, y: int) -> None:
        if 0 <= x < w and 0 <= y < h:
            grid[x][y] = "0"

    for x in range(start_x, end_x):
        # Border
        solid(x, h - 1)

    if pattern == "flat_ground":
        for x in range(start_x, end_x):
            solid(x, floor_y)

    elif pattern in ("easy_pit", "medium_pit", "hard_pit"):
        gap = {"easy_pit": seg_w // 3, "medium_pit": seg_w // 2, "hard_pit": seg_w - 1}[pattern]
        gap = min(gap, seg_w - 2)
        pit_start = start_x + (seg_w - gap) // 2
        for x in range(start_x, end_x):
            if pit_start <= x < pit_start + gap:
                air(x, floor_y)
            else:
                solid(x, floor_y)

    elif pattern == "platforms":
        for x in range(start_x, end_x):
            solid(x, floor_y + 1)
        plat_y = floor_y - difficulty
        for x in range(start_x, end_x, 4):
            for px in range(min(3, end_x - x)):
                solid(x + px, plat_y)

    elif pattern == "stairs_up":
        for step in range(seg_w):
            x = start_x + step
            y = floor_y - step // 2
            if 0 <= x < w and 1 <= y < h - 1:
                solid(x, y)

    elif pattern == "stairs_down":
        for step in range(seg_w):
            x = start_x + step
            y = floor_y - (seg_w - step) // 2
            if 0 <= x < w and 1 <= y < h - 1:
                solid(x, y)

    elif pattern == "high_ground":
        elev = max(1, difficulty)
        for x in range(start_x, end_x):
            solid(x, floor_y - elev)

    elif pattern == "wall":
        mid = (start_x + end_x) // 2
        for y in range(max(1, floor_y - difficulty * 2), h - 1):
            solid(mid, y)

    elif pattern == "two_jumps":
        gap = max(2, difficulty)
        for x in range(start_x, end_x):
            off = x - start_x
            if off % (gap * 3) in range(gap):
                air(x, floor_y)
            else:
                solid(x, floor_y)


def generate_pattern_room(
    w: int,
    h: int,
    rng: LcgRng,
    difficulty: int = 3,
) -> List[List[str]]:
    """Generate a tile grid using pattern-mode (marioGen-v2 style)."""
    grid = new_grid(w, h, "0")

    # Solid border
    for x in range(w):
        grid[x][0] = _DEFAULT_TILE_CHAR
        grid[x][h - 1] = _DEFAULT_TILE_CHAR
    for y in range(h):
        grid[0][y] = _DEFAULT_TILE_CHAR
        grid[w - 1][y] = _DEFAULT_TILE_CHAR

    current = "flat_ground"
    x = 1
    seg_w = max(4, w // 6)
    while x < w - 1:
        _apply_pattern_segment(grid, w, h, current, x, seg_w, difficulty, rng)
        x += seg_w
        current = _pick_next_pattern(current, rng)

    return grid


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    training_rooms: List[Dict[str, Any]],
    room_count: int = 8,
    room_w_tiles: int = 40,
    room_h_tiles: int = 23,
    proba: float = 0.5,
    carve_exits_flag: bool = True,
    train_source: str = "all_rooms",
    configuration: str = MDMC_DEFAULT,
    generation_mode: str = "mdmc",
    max_backtrack_depth: int = 8,
    tries_limit: int = 20,
    seed: int = -1,
    w1: float = 1.0, w2: float = 1.0, w3: float = 1.0,
    min_interestingness: float = 0.0,
    z1: float = 1.0, z2: float = 1.0, z3: float = 1.0,
    num_paths: int = 5,
    ensure_playable: bool = True,
    place_entities_flag: bool = True,
    name_prefix: str = "gen_",
    auto_tile_flag: bool = True,
    preserve_exits: bool = True,
    generate_bg_flag: bool = False,
    use_enhanced_bg: bool = False,
    cleanup_passes: int = 2,
    hazard_density: float = 0.05,
    spring_density: float = 0.02,
    use_pattern_mode: bool = False,
    difficulty: int = 3,
    place_decals_flag: bool = True,
    place_triggers_flag: bool = True,
    decal_density: float = 0.12,
    trigger_mode: str = "camera",
) -> Dict[str, Any]:
    """End-to-end PCG pipeline.

    Parameters mirror the celeste_pcg_pipeline.lua script parameters.
    training_rooms: list of room element dicts from celeste_bin.get_rooms().
    Returns a dict with 'rooms' (list of room element dicts) and 'report'.
    """
    rng = LcgRng(seed)
    offsets = parse_config(configuration)

    # ── Build skeleton ──────────────────────────────────────────────────────
    skeleton = generate_skeleton(
        room_count=room_count,
        min_w_tiles=room_w_tiles,
        max_w_tiles=room_w_tiles,
        min_h_tiles=room_h_tiles,
        max_h_tiles=room_h_tiles,
        seed=seed,
        name_prefix=name_prefix,
    )
    slots = skeleton["slots"]
    report_lines = skeleton["warnings"][:]

    # ── Train model ─────────────────────────────────────────────────────────
    training_grids: List[Tuple[List[List[str]], int, int]] = []
    for room in training_rooms:
        w_px = room.get("width", room_w_tiles * TILE)
        h_px = room.get("height", room_h_tiles * TILE)
        wt, ht = w_px // TILE, h_px // TILE
        # Extract tile string from room's solids child
        tile_str = ""
        for child in room.get("__children", []):
            if child.get("__name") == "solids":
                tile_str = child.get("innerText", "")
                break
        if tile_str:
            g, gw, gh = grid_from_tile_string(tile_str)
            training_grids.append((g, gw, gh))

    mdmc_model: Optional[Dict[str, Any]] = None
    wfc_model: Optional[Dict[str, Any]] = None
    if training_grids:
        mdmc_model = train_mdmc(training_grids, offsets)
        if generation_mode in ("wfc", "hybrid"):
            wfc_model = train_adjacency(training_grids, offsets)

    # ── Generate rooms ──────────────────────────────────────────────────────
    result_rooms: List[Dict[str, Any]] = []
    grids_by_name: Dict[str, Tuple[List[List[str]], int, int]] = {}

    for slot in slots:
        b = slot["bounds"]
        wt, ht = b["width"] // TILE, b["height"] // TILE

        if use_pattern_mode or not training_grids:
            grid = generate_pattern_room(wt, ht, rng, difficulty)
        elif generation_mode == "mdmc" and mdmc_model:
            grid = mdmc_generate(mdmc_model, wt, ht, rng, max_backtrack_depth, tries_limit)
        elif generation_mode == "wfc" and wfc_model:
            grid = wfc_generate(wfc_model, wt, ht, rng) or generate_pattern_room(wt, ht, rng, difficulty)
        elif generation_mode == "hybrid" and mdmc_model and wfc_model:
            grid = mdmc_generate(mdmc_model, wt, ht, rng, max_backtrack_depth, tries_limit)
        else:
            grid = generate_pattern_room(wt, ht, rng, difficulty)

        cleanup_tiles(grid, wt, ht, cleanup_passes)
        if auto_tile_flag:
            auto_tile(grid, wt, ht)

        # Scoring
        interest = score_interestingness(grid, wt, ht, w1, w2, w3, num_paths)
        diff = score_difficulty(grid, wt, ht, z1, z2, z3)

        if interest < min_interestingness:
            grid = generate_pattern_room(wt, ht, rng, difficulty)

        grids_by_name[slot["name"]] = (grid, wt, ht)

        # Build room element dict
        tile_str = grid_to_tile_string(grid, wt, ht)
        bg_str = ""
        if generate_bg_flag:
            bg_grid = generate_bg(grid, wt, ht, use_enhanced_bg)
            bg_str = grid_to_tile_string(bg_grid, wt, ht)

        room: Dict[str, Any] = {
            "__name": "level",
            "name": slot["name"],
            "x": b["x"], "y": b["y"],
            "width": b["width"], "height": b["height"],
            "music": "", "musicAlternative": "", "ambience": "",
            "musicLayer1": True, "musicLayer2": True,
            "musicLayer3": True, "musicLayer4": True,
            "dark": False, "space": False, "underwater": False,
            "whisper": False, "windPattern": "None",
            "cameraOffsetX": 0, "cameraOffsetY": 0,
            "__children": [
                {"__name": "solids", "innerText": tile_str},
                {"__name": "bg", "innerText": bg_str},
                {"__name": "entities", "__children": []},
                {"__name": "triggers", "__children": []},
                {"__name": "decalsBg", "__children": []},
                {"__name": "decalsFg", "__children": []},
            ],
        }

        if ensure_playable:
            platformer_repair(grid, wt, ht)

        if place_entities_flag:
            place_entities(room, grid, wt, ht, rng, slot["is_start"], slot["is_end"],
                           hazard_density, spring_density)

        if place_decals_flag:
            place_decals(room, grid, wt, ht, rng, decal_density)

        if place_triggers_flag:
            place_triggers(room, grid, wt, ht, rng, trigger_mode)

        report_lines.append(
            f"  {slot['name']}: {wt}×{ht} tiles, "
            f"interest={interest:.3f}, difficulty={diff:.3f}"
        )
        result_rooms.append(room)

    # ── Carve exits ─────────────────────────────────────────────────────────
    if carve_exits_flag and slots:
        carved = carve_exits(grids_by_name, slots)
        # Sync carved grids back to room tile strings
        for room in result_rooms:
            name = room["name"]
            if name in grids_by_name:
                g, wt, ht = grids_by_name[name]
                for child in room["__children"]:
                    if child.get("__name") == "solids":
                        child["innerText"] = grid_to_tile_string(g, wt, ht)
                        break
        report_lines.append(f"Carved {carved} exits between adjacent rooms.")

    return {
        "rooms": result_rooms,
        "report": "\n".join(report_lines),
    }
