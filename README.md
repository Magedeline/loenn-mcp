# loenn-mcp

[![PyPI](https://img.shields.io/pypi/v/loenn-mcp)](https://pypi.org/project/loenn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

**Celeste map editor for AI agents** — a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets GitHub Copilot, Claude, and other MCP clients read, edit, analyze, **procedurally generate**, and preview Celeste `.bin` map files without ever opening Lönn.

Built for use with [Everest](https://github.com/EverestAPI/Everest) mods. Works with maps created by [Lönn](https://github.com/CelestialCartographers/Loenn) or [Ahorn](https://github.com/CelestialCartographers/Ahorn).

---

## Features

### 60 MCP tools across 18 categories

**Map Reading**
| Tool | Description |
|---|---|
| `list_maps` | List all `.bin` files in the project |
| `read_map_overview` | Summary of rooms, entities, triggers, and stylegrounds |
| `read_room` | Full detail for a single room: tiles, entities, triggers, decals |
| `get_room_tiles` | Raw tile grid (foreground or background) for a room |

**Map Reading Extensions (NEW in v5)**
| Tool | Description |
|---|---|
| `read_map_metadata` | Quick metadata (package, room count, world bounds) without full read |
| `search_entities` | Search entities across rooms by type, position, and room |
| `search_triggers` | Search triggers across rooms by type |
| `compare_rooms` | Side-by-side comparison of two rooms (size, difficulty, entities) |

**Map Editing**
| Tool | Description |
|---|---|
| `add_entity` | Place an entity in a room (auto-assigns ID) |
| `remove_entity` | Delete an entity by ID |
| `add_trigger` | Place a trigger (rectangular region) in a room, with optional path nodes |
| `remove_trigger` | Delete a trigger by ID |
| `set_room_tiles` | Replace the tile grid for a room |
| `add_room` | Create a new room with custom position/size |
| `remove_room` | Delete a room from the map |
| `create_map` | Create a new empty `.bin` map file |

**Map Editing Extensions (NEW in v5)**
| Tool | Description |
|---|---|
| `update_entity` | Update properties of an existing entity by ID |
| `move_entity` | Move an entity to a new position |
| `update_room` | Update room-level properties (music, dark, wind, etc.) |
| `clone_room` | Clone a room to a new name and position |
| `batch_add_entities` | Add multiple entities in one call (JSON array) |
| `resize_room` | Change room dimensions |

**Decals (NEW in v5)**
| Tool | Description |
|---|---|
| `list_decals` | List all decals in a room (FG or BG) |
| `add_decal` | Add a decal with texture, position, and scale |
| `remove_decal` | Remove a decal by index |

**Stylegrounds**
| Tool | Description |
|---|---|
| `list_stylegrounds` | List foreground + background effects (with indices) |
| `add_styleground` | Add an effect (parallax, custom Lua effect, `apply` group, etc.) to FG or BG |
| `remove_styleground` | Remove an effect by index |
| `update_styleground` | Merge property changes into an existing effect |

**Entity / Trigger Catalog**
| Tool | Description |
|---|---|
| `list_entity_definitions` | Browse Lönn entity `.lua` files in the project |
| `get_entity_definition` | Read the full source of a single entity definition |
| `list_trigger_definitions` | Browse Lönn trigger `.lua` files |
| `list_effect_definitions` | Browse Lönn effect `.lua` files |

**Catalog Extensions (NEW in v5)**
| Tool | Description |
|---|---|
| `get_trigger_definition` | Read the source of a trigger `.lua` file by name |
| `get_effect_definition` | Read the source of an effect `.lua` file by name |

**Analysis**
| Tool | Description |
|---|---|
| `analyze_map` | Statistics: entity counts, type breakdown, world bounds |
| `visualize_map_layout` | ASCII mini-map of room positions |
| `preview_map_section` | Detailed ASCII preview of a map region |

**Advanced Analysis — gdep-inspired (NEW in v5)**
| Tool | Description |
|---|---|
| `analyze_entity_usage` | Entity usage stats across the entire map |
| `analyze_difficulty` | Estimate room/map difficulty from hazards, nav aids, tile coverage |
| `find_entity_references` | Find all occurrences of an entity type across rooms |
| `detect_map_patterns` | Detect design archetypes (linear, hub, collectible-rich, etc.) |
| `analyze_room_connectivity` | Adjacency graph: isolated rooms, dead ends, hubs |

**Suggestions — gdep-inspired (NEW in v5)**
| Tool | Description |
|---|---|
| `suggest_improvements` | Actionable suggestions for a room (spawns, floors, balance) |
| `compare_maps` | Structural diff between two map files |

**Wiki / Cache — gdep-inspired (NEW in v5)**
| Tool | Description |
|---|---|
| `wiki_save` | Persist analysis results locally for instant repeated queries |
| `wiki_search` | Search cached wiki entries by key, content, or tags |
| `wiki_list` | List all wiki entries |
| `wiki_get` | Retrieve a specific wiki entry |

**Mod Project (NEW in v5)**
| Tool | Description |
|---|---|
| `get_mod_info` | Project info: everest.yaml, map count, PCG library, wiki |
| `validate_map` | Whole-map playability validation with optional auto-fix |

**Import / Export (NEW in v5)**
| Tool | Description |
|---|---|
| `export_room_json` | Export a room as JSON for external editing or sharing |
| `import_room_json` | Import a room from JSON into a map |

**Diff & Fix — gdep-inspired (NEW in v5)**
| Tool | Description |
|---|---|
| `summarize_map_diff` | Snapshot-based structural diffing for tracking map evolution |
| `batch_validate_and_fix` | Batch playability checks with optional auto-fix |

**Rendering**
| Tool | Description |
|---|---|
| `render_map_html` | Interactive HTML preview (zoom, pan, room details, minimap, search) |

**Procedural Generation**
| Tool | Description |
|---|---|
| `build_pattern_library` | Scan local `.bin` maps and extract room patterns into a reusable JSON library |
| `generate_room_from_pattern` | Generate a new room using patterns + a strategy + seed |
| `validate_room` | Check a room for playability issues (spawn, floor, bounds) |
| `ingest_external_map` | Download maps from external URLs (GameBanana etc.) and extract patterns |

**Image-to-Map & Terrain Generation (v4)**
| Tool | Description |
|---|---|
| `generate_map_from_image` | Convert a color-mapped image (PNG/JPG/BMP) into a full playable Celeste map |
| `generate_terrain_map` | Procedural map using seeded Perlin noise + Voronoi biomes |
| `preview_terrain_biomes` | ASCII preview of biome layout before generating |

---

## Quick Start

### 1 — Install from PyPI

```bash
pip install loenn-mcp
```

Or clone and install from source:

```bash
git clone https://github.com/Maggy-Studio/loenn-mcp
cd loenn-mcp
pip install -e .
```

### 2 — Connect to GitHub Copilot (VS Code)

Add to your project's `.vscode/mcp.json`:

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

Then ask Copilot things like:
- *"What rooms are in 01_City_A.bin?"*
- *"Add a strawberry to room a-03 at position (120, 80)"*
- *"Render an HTML preview of 07_Hell_A.bin and open it"*
- *"Build a pattern library from all my maps, then generate 5 challenge rooms"*

### 3 — Connect to Claude Desktop

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

### 4 — One-click map preview (standalone)

```bash
python -m loenn_mcp.preview_map Maps/Maggy/Main/01_City_A.bin
python -m loenn_mcp.preview_map Maps/Maggy/Main/01_City_A.bin g-   # filter rooms by prefix
```

The HTML preview opens in your browser and supports:
- **Scroll** to zoom, **drag** to pan, **pinch** on touch
- Click a room → detail panel (entities, triggers, size)
- Live **search** filter (`F` to focus)
- **Minimap** with viewport indicator
- Keyboard shortcuts: `+` / `-` zoom, `0` fit, `Esc` deselect

---

## Procedural Generation (PCG)

### Generation strategies

| Strategy | Description |
|---|---|
| `balanced` | Mix of exploration and challenge — good default |
| `exploration` | Open spaces, gentle platforming, few hazards |
| `challenge` | Dense tiles, many hazards, tight jumps |
| `speedrun` | Linear path, minimal platforms, fast flow |

### Model profiles

| Profile | Seed behaviour | Best for |
|---|---|---|
| `creative` | Random seed each call | Maximum room variety |
| `deterministic` | Stable seed from strategy name | CI pipelines, reproducible layouts |
| `architect` | Random seed | Emphasis on room shape and connectivity |

### End-to-end pipeline

```
1. Build pattern library from existing maps
2. (Optional) ingest community maps from GameBanana
3. Create a blank map
4. Generate rooms with a chosen strategy and seed
5. Validate each room
6. Render HTML preview
```

**Example agent prompts:**

```
# Step 1 — build pattern library
build_pattern_library()

# Step 2 — ingest a GameBanana mod for richer pattern data
ingest_external_map(
  source_url="https://gamebanana.com/mods/53774",
  attribution="Spring Collab 2020 (various authors)",
  confirm_download=True,
  tags="community,collab"
)

# Step 3 — create map and generate rooms
create_map("Maps/PCG/MyAIMap.bin", "PCG/MyAIMap")
generate_room_from_pattern(
  map_path="Maps/PCG/MyAIMap.bin",
  room_name="a-01",
  strategy="exploration",
  seed=42,
  model_profile="deterministic"
)
generate_room_from_pattern(
  map_path="Maps/PCG/MyAIMap.bin",
  room_name="a-02",
  strategy="challenge",
  x=320
)

# Step 4 — validate
validate_room("Maps/PCG/MyAIMap.bin", "a-01")

# Step 5 — preview
render_map_html("Maps/PCG/MyAIMap.bin")
```

### Reproducible generation (seeded)

Pass `seed=<integer>` and `model_profile="deterministic"` to get the exact same room every time:

```python
# These two calls produce identical output:
generate_room_from_pattern(map_path="...", room_name="r1", strategy="challenge", seed=1234, model_profile="deterministic")
generate_room_from_pattern(map_path="...", room_name="r2", strategy="challenge", seed=1234, model_profile="deterministic")
```

### GameBanana integration

`ingest_external_map` can fetch maps directly from [GameBanana](https://gamebanana.com/games/6460):

```
# Dry-run (no download) — shows what would happen
ingest_external_map(
  source_url="https://gamebanana.com/mods/53774",
  attribution="Spring Collab 2020",
  confirm_download=False
)

# Actual download + pattern extraction
ingest_external_map(
  source_url="https://gamebanana.com/mods/53774",
  attribution="Spring Collab 2020 (various authors, see mod page)",
  confirm_download=True,
  tags="expert,collab"
)
```

Downloaded files are saved to `PCG/Datasets/` with an `attribution.json` file.
**Always verify the mod's licence permits derivative use before building on its patterns.**

---

## Image-to-Map Conversion (NEW in v4)

Convert any color-mapped image directly into a playable Celeste map. Each pixel becomes one 8×8 tile, with colors mapped to tile types and entities.

### Default color mapping

| Color | Hex | Maps to |
|---|---|---|
| Black | `#000000` | Solid tile (foreground) |
| White | `#FFFFFF` | Air (empty space) |
| Red | `#FF0000` | Spike hazard |
| Green | `#00FF00` | Player spawn |
| Blue | `#0000FF` | Jump-through platform |
| Yellow | `#FFFF00` | Strawberry collectible |
| Magenta | `#FF00FF` | Spring (bounce pad) |
| Cyan | `#00FFFF` | Refill crystal |
| Orange | `#FF8000` | Crumble block |
| Grey | `#808080` | Background solid (decorative) |

### Usage

```python
# Basic — converts image using default color mapping
generate_map_from_image(image_path="Assets/my_level.png")

# Custom colors and scale
generate_map_from_image(
    image_path="Assets/large_map.png",
    output_path="Maps/Custom/level.bin",
    scale=4,  # 4×4 pixel blocks → 1 tile
    color_map_json='{"#FF0000":"solid","#00FF00":"spawn","#0000FF":"air"}'
)
```

### How it works

1. The image is loaded and optionally downscaled by `scale` factor
2. Each pixel is matched to the closest color in the color map (within tolerance)
3. The grid is split into room-sized chunks (default 40×23 tiles = 320×184 px)
4. Each chunk becomes a room with proper tiles, entities, and a player spawn
5. The complete map is written as a `.bin` file

Requires `Pillow` — install with: `pip install loenn-mcp[image]`

---

## Seeded Terrain Generation (NEW in v4)

Generate complete maps procedurally using Perlin noise and Voronoi diagrams, inspired by [AliShazly/map-generator](https://github.com/AliShazly/map-generator).

### Biomes

| Biome | Character | Terrain |
|---|---|---|
| `mountain` | Dense tiles | Tight platforms, spikes |
| `forest` | Moderate density | Many platforms, springs |
| `plains` | Open spaces | Gentle platforms, collectibles |
| `lake` | Sparse tiles | Jump-throughs, refills |
| `cave` | Enclosed | Crumble blocks, dark rooms |
| `summit` | Sparse platforms | Wind effects |

### Usage

```python
# Generate with specific seed (reproducible)
generate_terrain_map(seed=42, difficulty=3)

# Customize grid size and biomes
generate_terrain_map(
    seed=1234,
    width_rooms=5,
    height_rooms=4,
    biome_set="mountain,cave,summit",
    difficulty=4,
    frequency=12.0,
    voronoi_points=16
)

# Preview biome layout first (no file created)
preview_terrain_biomes(seed=42, width_rooms=4, height_rooms=3)
# Output:
# [P] [^] [^] [F]
# [~] [P] [^] [M]
# [C] [~] [P] [F]
```

### Generation algorithm

1. **Perlin noise** creates organic heightmap terrain — controls where solid tiles, platforms, and gaps appear
2. **Voronoi diagrams** partition the map into biome regions — each room inherits the biome of its Voronoi region
3. **Seeded RNG** ensures the same `seed` + parameters always produce the exact same output
4. **Difficulty scaling** (1-5) adjusts hazard density, tile coverage, and platform frequency
5. Biome properties control tile characters, entity types, room flags (dark, underwater, wind)

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

## Game Analysis & Wiki (NEW in v5 — gdep-inspired)

Advanced analysis tools adapted from game design analysis patterns.

### Usage examples

```python
# Analyze difficulty across all rooms
analyze_difficulty(map_path="Maps/MyMod/1-City.bin")

# Detect gameplay patterns
detect_map_patterns(map_path="Maps/MyMod/1-City.bin")
# → "standard-level (7-15 rooms)", "linear-horizontal", "checkpointed (3 checkpoints)"

# Get suggestions for a room
suggest_improvements(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-01")

# Track map evolution with snapshots
summarize_map_diff(map_path="Maps/MyMod/1-City.bin")  # saves snapshot
# ... make edits ...
summarize_map_diff(map_path="Maps/MyMod/1-City.bin")  # shows diff

# Cache analysis results for instant re-use
wiki_save(key="city_difficulty", content="Avg difficulty 4.2/10, 3 hard rooms", tags="analysis")
wiki_search(query="difficulty")

# Batch validate and auto-fix
batch_validate_and_fix(map_path="Maps/MyMod/1-City.bin", auto_fix=True)

# Search for specific entities
search_entities(map_path="Maps/MyMod/1-City.bin", entity_type="strawberry")

# Clone and modify rooms
clone_room(map_path="Maps/MyMod/1-City.bin", source_room="lvl_a-01", new_name="lvl_a-01-copy")

# Export/import rooms as JSON
export_room_json(map_path="Maps/MyMod/1-City.bin", room_name="lvl_a-01")
import_room_json(map_path="Maps/MyMod/2-Resort.bin", json_path="Export/lvl_a-01.json")
```

### Wiki cache

The wiki stores analysis results in `.loenn_mcp_wiki/` as JSON files.
Results persist across sessions so repeated queries return instantly.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `LOENN_MCP_WORKSPACE` | Current working directory | Root of your Celeste mod project. The server resolves all map paths relative to this. Path traversal outside the workspace is blocked. |

---

## How It Works

### `celeste_bin.py` — standalone binary parser

A pure-Python implementation of the Celeste `.bin` map format (no Everest or Lönn required):

- Full read/write round-trip with no data loss
- Handles all 7 value types: `bool`, `uint8`, `int16`, `int32`, `float32`, lookup string, raw string, RLE-encoded string
- Recursive element tree matching the internal Lönn/Maple format

### `pcg.py` — procedural generation module

Provides:

- **Pattern extraction** — converts `.bin` rooms into reusable pattern records (size class, entity density, tile motifs, trigger usage, gameplay tags)
- **Pattern library** — JSON-based store with deduplication by content hash
- **Strategy-based generation** — `balanced`, `exploration`, `challenge`, `speedrun` modes
- **Seeded randomness** — `random.Random(seed)` for reproducible outputs; seed exposed via MCP tool parameters
- **Model profiles** — `deterministic` / `creative` / `architect` profiles control how seeds are resolved

### `image_map.py` — image-to-map conversion (NEW in v4)

Converts color-mapped images into playable Celeste maps:

- **Color-to-role mapping** — configurable palette mapping colors to tiles and entities
- **Automatic room splitting** — large images are divided into room-sized chunks
- **Entity placement** — spawns, hazards, collectibles extracted directly from pixel colors
- **Scale support** — large images can be downscaled (N×N pixel blocks → 1 tile)
- **Tolerance matching** — fuzzy color matching for hand-drawn or anti-aliased images

### `terrain_gen.py` — seeded terrain generator (NEW in v4)

Procedural map generation inspired by [AliShazly/map-generator](https://github.com/AliShazly/map-generator):

- **Perlin noise** — pure-Python implementation with fractal octaves for organic terrain
- **Voronoi biomes** — map partitioned into distinct biome regions (mountain, forest, plains, lake, cave, summit)
- **Fully seeded** — same seed + parameters = identical output every time
- **Difficulty scaling** — 1-5 scale controls hazard density, tile coverage, and platform frequency
- **Biome-aware entities** — each biome has appropriate hazards, collectibles, and room flags

### `gdep_tools.py` — game analysis tools (NEW in v5)

Integrates game analysis concepts from [pirua-game/ai_game_base_analysis_cli_mcp_tool](https://github.com/pirua-game/ai_game_base_analysis_cli_mcp_tool) (gdep):

- **Wiki caching** — persist analysis results locally so repeated queries are instant (`.loenn_mcp_wiki/`)
- **Pattern detection** — detect gameplay design archetypes (linear progression, hub layouts, collectible-rich, wind corridors)
- **Difficulty analysis** — estimate room/map difficulty from hazard density, navigation aids, tile coverage (1-10 scale)
- **Room connectivity** — adjacency graph analysis showing isolated rooms, dead ends, and hubs
- **Map diffing** — snapshot-based structural diffing for tracking map evolution over time
- **Batch validation** — whole-map playability checks (spawns, floors, bounds) with optional auto-fix
- **Suggestions** — actionable improvement suggestions based on room analysis

### `server.py` — MCP server

Built with [FastMCP](https://github.com/jlowin/fastmcp). All file paths are resolved relative to `LOENN_MCP_WORKSPACE` with path-traversal protection. Map writes are atomic (parse → mutate → write). External downloads require explicit `confirm_download=True`.

---

## Requirements

- Python 3.9+
- `fastmcp >= 3.0.0`
- `Pillow >= 9.0` (optional — only needed for `generate_map_from_image`)

Install with image support: `pip install loenn-mcp[image]`

No Celeste installation required to parse, generate, or preview maps.

---

## License

MIT — see [LICENSE](LICENSE).
