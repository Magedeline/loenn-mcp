# loenn-mcp

[![PyPI](https://img.shields.io/pypi/v/loenn-mcp)](https://pypi.org/project/loenn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![No AI inside](https://img.shields.io/badge/AI%20inside-none-brightgreen.svg)](#no-ai-inside-v700)

**A Celeste map editor for AI agents** — a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that brings full Celeste `.bin` map editing to Claude, GitHub Copilot, and other MCP clients, plus a standalone `pcgscene` CLI for scripting and CI. Read, edit, analyze, generate, and preview maps without opening Lönn.

*"For AI agents" describes who talks to this server (any MCP client), not what runs inside it — every tool here is deterministic and procedural. See [No AI Inside](#no-ai-inside-v700).*

Works with [Everest](https://github.com/EverestAPI/Everest) mods and maps from [Lönn](https://github.com/CelestialCartographers/Loenn) or [Ahorn](https://github.com/CelestialCartographers/Ahorn).

## Contents

- [Features](#features)
- [Installation & setup](#installation--setup)
- [`pcgscene` CLI](#pcgscene-cli)
- [Procedural generation](#procedural-generation)
- [Image-to-map conversion](#image-to-map-conversion)
- [Seeded terrain generation](#seeded-terrain-generation)
- [Analysis & insights](#analysis--insights)
- [No AI inside (v7.0.0)](#no-ai-inside-v700)
- [Configuration](#configuration)
- [Architecture](#architecture)
- [Requirements](#requirements)
- [License](#license)

## Features

**75 MCP tools** for complete map manipulation, plus the `pcgscene` command-line tool for scripting and CI.

### Core Tools

**Reading & Querying**
- `list_maps` — List all `.bin` files
- `read_map_overview` — Summary of rooms, entities, triggers, stylegrounds
- `read_room` — Full room details (tiles, entities, triggers, decals)
- `get_room_tiles` — Raw tile grid (FG or BG)
- `read_map_metadata` — Quick metadata without full read
- `search_entities` — Find entities by type, position, room
- `search_triggers` — Find triggers by type
- `compare_rooms` — Side-by-side room comparison

**Editing**
- `add_entity` / `remove_entity` — Place or delete entities
- `update_entity` / `move_entity` — Modify entity properties or position
- `add_trigger` / `remove_trigger` — Place or delete triggers
- `set_room_tiles` — Replace tile grid
- `add_room` / `remove_room` — Create or delete rooms
- `create_map` — Create new `.bin` file
- `update_room` — Modify room properties (music, dark, wind, etc.)
- `clone_room` — Duplicate a room
- `batch_add_entities` — Add multiple entities at once
- `resize_room` — Change room dimensions

**Decals & Effects**
- `list_decals` / `add_decal` / `remove_decal` — Manage foreground/background decals
- `list_stylegrounds` / `add_styleground` / `update_styleground` / `remove_styleground` — Manage map effects

**Definitions & Catalog**
- `list_entity_definitions` / `get_entity_definition` — Browse entity types
- `list_trigger_definitions` / `get_trigger_definition` — Browse trigger types
- `list_effect_definitions` / `get_effect_definition` — Browse effect types

### Analysis & Insights

**Basic Analysis**
- `analyze_map` — Entity counts, type breakdown, world bounds
- `visualize_map_layout` — ASCII mini-map
- `preview_map_section` — Detailed ASCII preview

**Advanced Analysis**
- `analyze_entity_usage` — Entity stats across entire map
- `analyze_difficulty` — Room/map difficulty estimation
- `find_entity_references` — Locate all instances of an entity type
- `detect_map_patterns` — Identify design archetypes (linear, hub, etc.)
- `analyze_room_connectivity` — Adjacency graph analysis

**Suggestions & Improvements**
- `suggest_improvements` — Actionable room suggestions
- `compare_maps` — Structural diff between maps

**Wiki & Caching**
- `wiki_save` / `wiki_search` / `wiki_list` / `wiki_get` — Persist and retrieve analysis results

**Project Management**
- `get_mod_info` — Project metadata and structure
- `validate_map` / `batch_validate_and_fix` — Playability validation with auto-fix
- `export_room_json` / `import_room_json` — JSON room exchange

**Diffing**
- `summarize_map_diff` — Track map evolution with snapshots

### Rendering

- `render_map_html` — Interactive HTML preview (zoom, pan, search, minimap)

### Procedural Generation

**Pattern-Based Generation**
- `build_pattern_library` — Extract patterns from existing maps
- `generate_room_from_pattern` — Generate rooms with strategy + seed
- `ingest_external_map` — Download and extract patterns from GameBanana

**Image & Terrain Generation**
- `generate_map_from_image` — Convert color-mapped images to playable maps
- `generate_terrain_map` — Procedural maps with Perlin noise + Voronoi biomes
- `preview_terrain_biomes` — Preview biome layout before generation

---

## Installation & Setup

### Install from PyPI
```bash
pip install loenn-mcp
```

Or from source:
```bash
git clone https://github.com/Magedeline/loenn-mcp
cd loenn-mcp
pip install -e .
```

### Connect to Claude Desktop
Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "loenn-mcp": {
      "command": "python",
      "args": ["-m", "loenn_mcp.server"],
      "env": {
        "LOENN_MCP_WORKSPACE": "/absolute/path/to/your/mod"
      }
    }
  }
}
```

### Connect to GitHub Copilot (VS Code)
Add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "loenn-mcp": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "loenn_mcp.server"],
      "env": {
        "LOENN_MCP_WORKSPACE": "${workspaceFolder}"
      }
    }
  }
}
```

### Preview Maps Locally
```bash
python -m loenn_mcp.preview_map Maps/01_City_A.bin
python -m loenn_mcp.preview_map Maps/01_City_A.bin g-   # filter by prefix
```

The interactive HTML preview supports zoom, pan, room details, search, and minimap with keyboard shortcuts.

---

## `pcgscene` CLI

A batch-first command-line tool for scanning and repairing maps outside an
MCP client — install once, use from scripts, pre-commit hooks, or CI. Every
command that touches a `.bin` file writes through the same atomic,
backed-up, round-trip-validated path as the MCP server, so a bad run can
never destroy a map.

```bash
pip install loenn-mcp
pcgscene scan MyMap.bin              # score every room + map connectivity
pcgscene validate MyMap.bin          # in-game readiness checklist (exit 0/1)
pcgscene fix MyMap.bin --dry-run     # preview safe auto-repairs
pcgscene fix MyMap.bin               # apply them (backed up first)
pcgscene diff before.bin after.bin   # what changed between two maps
```

| Command | What it does |
|---|---|
| `scan` | Scores every room (interestingness, difficulty, exit connectivity, spawn presence, fairness gate) plus map-wide reachability from the start room. |
| `score` | Deep report for a single room (`--room NAME`). |
| `validate` | In-game readiness checklist — bad names, missing spawns, sealed exits, unreachable rooms — with a CI-friendly exit code. |
| `fix` | Safe auto-repairs: adds missing spawns, sanitizes bad room names, converts `strawberry{golden}` to real `goldenBerry` entities. Supports `--dry-run` and `--only spawns,names,berries`. |
| `diff` | Room/entity/tile changes between two maps. |
| `generate` | Preset-driven generation (`quick` / `simple_fair` / `explore` / `challenge`) with a reproducible `--seed`. Requires the `pcg_helper` module from the full/Pro build ([loenn-mcp-delta](https://github.com/Magedeline/loenn-mcp-delta)) — this build's `generate` refuses gracefully and tells you so. |

Every command accepts `--json` (and `--out FILE`) for machine-readable
output, so an editor plugin or CI step can consume the report directly.

---

## Procedural Generation

### Generation Strategies

| Strategy | Description |
|---|---|
| `balanced` | Mix of exploration and challenge (default) |
| `exploration` | Open spaces, gentle platforming, few hazards |
| `challenge` | Dense tiles, many hazards, tight jumps |
| `speedrun` | Linear path, minimal platforms, fast flow |

### Model Profiles

| Profile | Behavior | Use Case |
|---|---|---|
| `creative` | Random seed each call | Maximum variety |
| `deterministic` | Stable seed from strategy | Reproducible layouts |
| `architect` | Random seed | Emphasis on shape/connectivity |

### Quick Start Example

```python
# 1. Build pattern library from existing maps
build_pattern_library()

# 2. Create a new map
create_map("Maps/PCG/Generated.bin", "PCG/Generated")

# 3. Generate rooms
generate_room_from_pattern(
  map_path="Maps/PCG/Generated.bin",
  room_name="a-01",
  strategy="exploration",
  seed=42,
  model_profile="deterministic"
)

# 4. Validate and preview
validate_room("Maps/PCG/Generated.bin", "a-01")
render_map_html("Maps/PCG/Generated.bin")
```

### Seeded Generation

Use `seed=<int>` + `model_profile="deterministic"` for reproducible output:
```python
# Both calls produce identical rooms
generate_room_from_pattern(..., strategy="challenge", seed=1234, model_profile="deterministic")
generate_room_from_pattern(..., strategy="challenge", seed=1234, model_profile="deterministic")
```

### GameBanana Integration

Download and extract patterns from community mods:
```python
# Dry-run (preview only)
ingest_external_map(
  source_url="https://gamebanana.com/mods/53774",
  attribution="Spring Collab 2020",
  confirm_download=False
)

# Download and extract
ingest_external_map(
  source_url="https://gamebanana.com/mods/53774",
  attribution="Spring Collab 2020 (various authors)",
  confirm_download=True,
  tags="community,expert"
)
```

Patterns are saved to `PCG/Datasets/` with attribution. Always verify mod licenses permit derivative use.

---

## Image-to-Map Conversion

Convert color-mapped images directly into playable Celeste maps. Each pixel becomes one 8×8 tile.

### Default Color Mapping

| Color | Hex | Maps to |
|---|---|---|
| Black | `#000000` | Solid tile |
| White | `#FFFFFF` | Air (empty) |
| Red | `#FF0000` | Spike hazard |
| Green | `#00FF00` | Player spawn |
| Blue | `#0000FF` | Jump-through platform |
| Yellow | `#FFFF00` | Strawberry |
| Magenta | `#FF00FF` | Spring |
| Cyan | `#00FFFF` | Refill crystal |
| Orange | `#FF8000` | Crumble block |
| Grey | `#808080` | Background solid |

### Usage

```python
# Basic conversion
generate_map_from_image(image_path="Assets/my_level.png")

# Custom colors and scale
generate_map_from_image(
    image_path="Assets/large_map.png",
    output_path="Maps/Custom/level.bin",
    scale=4,  # 4×4 pixel blocks → 1 tile
    color_map_json='{"#FF0000":"solid","#00FF00":"spawn"}'
)
```

Requires `Pillow`: `pip install loenn-mcp[image]`

---

## Seeded Terrain Generation

Generate complete maps with Perlin noise and Voronoi biomes. Inspired by [AliShazly/map-generator](https://github.com/AliShazly/map-generator).

### Biomes

| Biome | Terrain |
|---|---|
| `mountain` | Dense tiles, tight platforms, spikes |
| `forest` | Moderate density, many platforms, springs |
| `plains` | Open spaces, gentle platforms, collectibles |
| `lake` | Sparse tiles, jump-throughs, refills |
| `cave` | Enclosed, crumble blocks, dark rooms |
| `summit` | Sparse platforms, wind effects |

### Quick Example

```python
# Generate a 4×3 map with seed 42
generate_terrain_map(seed=42, difficulty=3, width_rooms=4, height_rooms=3)

# Preview biome layout before generating
preview_terrain_biomes(seed=42, width_rooms=4, height_rooms=3)
# Output:
# [P] [^] [^] [F]
# [~] [P] [^] [M]
# [C] [~] [P] [F]
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `seed` | -1 (random) | Integer seed for reproducible output |
| `width_rooms` | 4 | Rooms horizontally |
| `height_rooms` | 3 | Rooms vertically |
| `frequency` | 8.0 | Perlin noise frequency (lower = smoother) |
| `voronoi_points` | 12 | Number of biome region centres |
| `biome_set` | all | Comma-separated biome names |
| `difficulty` | 3 | 1-5 scale for hazard density |

---

## Analysis & Insights

Advanced analysis tools for map design, difficulty, and patterns.

### Quick Examples

```python
# Analyze difficulty
analyze_difficulty(map_path="Maps/MyMod/1-City.bin")

# Detect gameplay patterns
detect_map_patterns(map_path="Maps/MyMod/1-City.bin")
# → "standard-level (7-15 rooms)", "linear-horizontal", "checkpointed"

# Get room suggestions
suggest_improvements(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-01")

# Track map evolution
summarize_map_diff(map_path="Maps/MyMod/1-City.bin")  # save snapshot
# ... edit map ...
summarize_map_diff(map_path="Maps/MyMod/1-City.bin")  # show diff

# Cache results for instant re-use
wiki_save(key="city_difficulty", content="Avg 4.2/10, 3 hard rooms", tags="analysis")
wiki_search(query="difficulty")

# Batch validation
batch_validate_and_fix(map_path="Maps/MyMod/1-City.bin", auto_fix=True)

# Search and clone
search_entities(map_path="Maps/MyMod/1-City.bin", entity_type="strawberry")
clone_room(map_path="Maps/MyMod/1-City.bin", source_room="lvl_a-01", new_name="lvl_a-01-copy")

# Export/import rooms
export_room_json(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-01")
import_room_json(map_path="Maps/MyMod/2-Resort.bin", json_path="Export/lvl_a-01.json")
```

### Wiki Cache

Analysis results persist in `.loenn_mcp_wiki/` as JSON files for instant re-use across sessions.

---

## No AI Inside (v7.0.0)

As of v7.0.0 every tool in loenn-mcp is **deterministic and procedural** —
Markov chains, wave-function collapse, noise, BFS/graph analysis. The server
makes **no calls to any AI/LLM API**, requires no API key, and ships no AI
models. The former `ai_analyze_map` / `ai_describe_room` / `ai_suggest_entities`
tools and the `anthropic` dependency were removed.

> **Disclaimer:** this project was developed with the assistance of Claude
> (Anthropic) as a coding tool, based on the algorithms published in
> Robinet et al., *"Towards a Celeste AI Framework"* (FDG '25,
> DOI 10.1145/3723498.3723796). All output has been human-tested in Lönn and
> in-game.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `LOENN_MCP_WORKSPACE` | Current directory | Root of your Celeste mod project. All map paths are relative to this. Path traversal is blocked. |

---

## Architecture

### Core Modules

**`celeste_bin.py`** — Standalone `.bin` parser
- Pure Python (no Everest/Lönn required)
- Full read/write round-trip with no data loss
- Handles all 7 value types: bool, uint8, int16, int32, float32, lookup string, raw string, RLE-encoded string
- Recursive element tree matching Lönn/Maple format

**`pcg.py`** — Procedural generation
- Pattern extraction from rooms (size, entity density, tile motifs, gameplay tags)
- JSON pattern library with deduplication
- Strategy-based generation (balanced, exploration, challenge, speedrun)
- Seeded randomness for reproducible output
- Model profiles (deterministic, creative, architect)

**`image_map.py`** — Image-to-map conversion
- Color-to-role mapping (configurable palette)
- Automatic room splitting
- Entity placement from pixel colors
- Scale support (downscaling)
- Fuzzy color matching

**`terrain_gen.py`** — Seeded terrain generation
- Perlin noise with fractal octaves
- Voronoi biome partitioning
- Fully seeded (same seed = identical output)
- Difficulty scaling (1-5)
- Biome-aware entities

**`gdep_tools.py`** — Game analysis
- Wiki caching (`.loenn_mcp_wiki/`)
- Pattern detection (linear, hub, collectible-rich, etc.)
- Difficulty analysis (1-10 scale)
- Room connectivity graphs
- Map diffing with snapshots
- Batch validation and auto-fix
- Actionable suggestions

**`server.py`** — MCP server
- Built with [FastMCP](https://github.com/jlowin/fastmcp)
- Path-traversal protection
- Atomic map writes
- Explicit download confirmation

**`cli.py`** — `pcgscene` command-line tool
- scan / score / validate / fix / diff, all `--json`-capable
- Shares `celeste_bin`'s atomic-write path — no separate write logic to drift

---

## Requirements

- Python 3.9+
- `fastmcp >= 3.0.0`
- `Pillow >= 9.0` (optional, for image-to-map conversion)

Install with all optional features:
```bash
pip install loenn-mcp[image]
```

No Celeste installation required.

---

## License

MIT — see [LICENSE](LICENSE).
