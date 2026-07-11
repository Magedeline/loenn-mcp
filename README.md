# loenn-mcp

[![PyPI](https://img.shields.io/pypi/v/loenn-mcp)](https://pypi.org/project/loenn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

**AI-powered Celeste map editor** — A [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that brings full Celeste `.bin` map editing to Claude, GitHub Copilot, and other MCP clients. Read, edit, analyze, generate, and preview maps without opening Lönn.

Works with [Everest](https://github.com/EverestAPI/Everest) mods and maps from [Lönn](https://github.com/CelestialCartographers/Loenn) or [Ahorn](https://github.com/CelestialCartographers/Ahorn).

## Features

**87 MCP tools** across 19 categories for complete map manipulation and AI-assisted design.

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

### Procedural Generation (Advanced)

**PCGHelper Tools** — MdMC/WFC tile generation
- `pcg_mdmc_presets` — List configuration presets
- `pcg_skeleton_generate` — Layout non-overlapping room skeleton
- `pcg_markov_fill` — Fill rooms with MdMC/WFC/hybrid
- `pcg_score_room` — Evaluate interestingness & difficulty
- `pcg_pipeline` — One-shot end-to-end generation

---

## Installation & Setup

### Install from PyPI
```bash
pip install loenn-mcp
```

Or from source:
```bash
git clone https://github.com/Maggy-Studio/loenn-mcp
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

## AI-Powered Analysis

Leverage Claude AI for intelligent map feedback, room descriptions, and entity suggestions.

### Setup
```bash
pip install loenn-mcp
export ANTHROPIC_API_KEY="sk-ant-..."  # Get from https://console.anthropic.com/
```

### Available Tools

| Tool | Description |
|---|---|
| `ai_analyze_map` | Design feedback (general/difficulty/visual/flow) |
| `ai_describe_room` | Generate room descriptions in various styles |
| `ai_suggest_entities` | Entity placement recommendations with coordinates |

### Examples

```python
# Get design feedback
ai_analyze_map(map_path="Maps/MyMod/1-City.bin", analysis_type="general")
# → "Strengths: Good checkpoints. Add more strawberries in rooms 3-5..."

# Generate descriptions
ai_describe_room(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", style="atmospheric")
# → "A windswept precipice where ancient stone meets howling gales..."

# Get entity suggestions
ai_suggest_entities(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03", goal="add_challenge")
# → "1. Add spikes at (120, 80) for timing challenge..."
```

### Analysis Types
- `general` — Overall design with suggestions
- `difficulty` — Difficulty curve analysis
- `visual` — Visual variety and theme feedback
- `flow` — Player movement and navigation

### Description Styles
- `atmospheric` — Evocative, mood-focused
- `technical` — Gameplay-focused
- `story` — Narrative snippets
- `brief` — 1-2 sentence summaries

### Suggestion Goals
- `improve_flow` — Better player guidance
- `add_challenge` — Skill-testing elements
- `reduce_difficulty` — Accessibility
- `add_secrets` — Exploration rewards

Gracefully degrades if `ANTHROPIC_API_KEY` is not set.

---

## Advanced PCG (MdMC/WFC)

Full Python port of [PCGHelper Lönn mod](https://github.com/Maggy-Studio/PCGHelper) — MdMC/WFC tile generation, skeleton layout, and end-to-end pipeline.

### Tools

| Tool | Description |
|---|---|
| `pcg_mdmc_presets` | List 7 MdMC configuration presets |
| `pcg_skeleton_generate` | Layout non-overlapping room skeleton |
| `pcg_markov_fill` | Fill rooms with MdMC/WFC/hybrid |
| `pcg_score_room` | Evaluate interestingness & difficulty |
| `pcg_pipeline` | One-shot end-to-end generation |

### Generation Modes
- `mdmc` — Multi-dimensional Markov Chain
- `wfc` — Wave Function Collapse
- `hybrid` — Combination of both

### Scoring Metrics

**Interestingness I** = w1·global_NLE + w2·local_NLE + w3·Shannon_entropy

**Difficulty D** = z1·hole_frequency + z2·local_LE + z3·NLE_scarcity

### MdMC Presets

| Key | Description |
|---|---|
| `000011012` | L-shape (default) |
| `000011112` | 3-neighbour causal |
| `001001112` | 4-neighbour |
| `011011012` | 5-neighbour |
| `010111010` | Cross (WFC recommended) |
| `101000101` | Diagonals only |
| `111101111` | All 8 neighbours |

### Example

```python
# One-shot end-to-end generation
pcg_pipeline(
    map_path="Maps/MyMod/1-City.bin",
    room_count=8,
    generation_mode="mdmc",
    seed=42,
    ensure_playable=True,
    place_entities=True,
)
# → "PCG pipeline complete: 8 rooms added"

# Score a room
pcg_score_room(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-03")
# → "Interestingness I = 0.4231 | Difficulty D = 0.5812"

# Fill existing empty rooms
pcg_markov_fill(
    map_path="Maps/MyMod/1-City.bin",
    room_names="skel_0,skel_1,skel_2",
    generation_mode="wfc",
    configuration="010111010",
    start_room="skel_0",  # gets the player spawn
    end_room="skel_2",    # gets the golden berry
    seed=99,
)
```

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

**`ai_analyzer.py`** — AI-powered analysis
- Claude API integration
- Design feedback (general, difficulty, visual, flow)
- Room descriptions (atmospheric, technical, story, brief)
- Entity placement suggestions
- Graceful degradation

**`server.py`** — MCP server
- Built with [FastMCP](https://github.com/jlowin/fastmcp)
- Path-traversal protection
- Atomic map writes
- Explicit download confirmation

---

## Requirements

- Python 3.9+
- `fastmcp >= 3.0.0`
- `anthropic >= 0.40.0` (optional, for AI-powered tools)
- `Pillow >= 9.0` (optional, for image-to-map conversion)

Install with all optional features:
```bash
pip install loenn-mcp[image]
```

No Celeste installation required.

---

## License

MIT — see [LICENSE](LICENSE).
