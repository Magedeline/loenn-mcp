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
- [JSON round-trip & authoring](#json-round-trip--authoring)
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

**88 MCP tools** for complete map manipulation, plus the `pcgscene` command-line tool for scripting and CI.

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

**JSON & Authoring** (see [JSON round-trip & authoring](#json-round-trip--authoring))
- `map_to_json` / `json_to_map` — Any `.bin` to typed JSON and back, byte-identical; refuses lossy writes
- `check_authoring_json` — Resolve hand-written content: defaults filled from Lönn plugins, unknown names flagged
- `merge_json_into_map` — Add entities/triggers/rooms to a map; dry-run diff + highlighted preview, map-wide unique IDs, placement checks, additions only
- `run_author_script` — Run a JavaScript authoring script (`js/`, lucid-dream style) and merge its output

**Decals & Effects**
- `list_decals` / `add_decal` / `remove_decal` — Manage foreground/background decals
- `list_stylegrounds` / `add_styleground` / `update_styleground` / `remove_styleground` — Manage map effects

**Definitions & Catalog**
- `list_entity_definitions` / `get_entity_definition` — Browse entity types; look a definition up by the name placed in maps (e.g. `MyMod/CloudBoss`)
- `find_missing_definitions` — Namespaced entities/triggers in a map that no installed Lönn plugin defines
- `audit_plugin_coverage` — C# `[CustomEntity]` names with no Lönn plugin, and plugins with no C# code (reads the .csproj compile list)
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
- `game_status` / `game_reload_map` / `game_teleport` / `game_log` — Drive the running game through Everest's debug server: see where you are, reload an edited map, jump to a room, read load errors
- `game_play_tas` — Play CelesteTAS inputs from a room's spawn and report the room, position, deaths and dashes at the end: proof a room is beatable, or that a shortcut fails
- `validate_audio` — Every audio event a map references, checked against GUIDs.txt and the game's FMOD banks, with closest-match suggestions
- `export_room_json` / `import_room_json` — JSON room exchange

**Diffing**
- `summarize_map_diff` — Track map evolution with snapshots

### Rendering

- `render_map_html` — Interactive HTML map preview with tiles, colour-coded entity markers, zoom/pan, search and a minimap; reads a `.bin` or a typed JSON dump, and can highlight a planned merge (`overlay`)

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

## JSON round-trip & authoring

Maps can leave the binary format and come back without losing a byte, and new
content can be written by hand or in JavaScript and merged into an existing map.
Nothing here needs Ruby or Node at runtime; Node is only used if you run JS
authoring scripts.

### Typed JSON: `map_to_json` / `json_to_map`

```text
map_to_json("Maps/MyMod/1-City.bin")                 → Export/1-City.json
json_to_map("Export/1-City.json", "Maps/MyMod/1-City-copy.bin")
```

Every attribute keeps its binary type, so the round trip is byte-identical:

```json
{
  "format": "celeste-map-json", "version": 1,
  "header": "CELESTE MAP", "package": "1-City",
  "source": {"file": "1-City.bin", "sha256": "…", "tree_sha256": "…"},
  "lookup": ["Map", "levels", "level", "…"],
  "root": {"name": "Map", "children": [
    {"name": "levels", "children": [
      {"name": "level", "attrs": {
        "name": ["lookup", "lvl_a-00"],
        "x": ["i16", -40], "width": ["i32", 320], "dark": ["bool", false],
        "music": ["string", "event:/music/lvl1/main"], "alpha": ["f32", 0.3]},
       "children": [{"name": "solids", "attrs": {"innerText": ["rle", "0000…"]}}]}]}]}
}
```

- **Types:** `bool`, `u8`, `i16`, `i32`, `f32`, `lookup` (string-table string),
  `string` (plain string), `rle` (run-length tile data, shown decoded).
- **Values are what the file holds.** A room named `lvl_a-00` stays `lvl_a-00`.
- **`lookup`** is the file's string table in file order; edits only append to it.
  File quirks a canonical writer would not reproduce (a duplicate table entry, an
  unusual RLE split) are kept as optional extras (`[type, value, {"key_index": 12}]`).
- **Refusals.** `json_to_map` writes nothing if a value does not fit its type
  (`["u8", 300]`), if an *unedited* dump would not reproduce its source byte-for-byte
  (checked with `source.sha256`), or if the output would not read back as the same
  map. Writes are atomic (temp file + replace) with rolling `.bak` copies.
- Hand-written documents may skip `lookup`/`source` and give bare values
  (`"x": 8`); those get the type Celeste's own packer would pick.

### Authoring JSON

A looser format for content you write yourself. Only names and positions are needed:

```json
{"rooms": [
  {"room": "a-05",
   "entities": [{"name": "strawberry", "x": 120, "y": 64},
                {"name": "CEL/GreenBerry", "x": 200, "y": 40}],
   "triggers": [{"name": "CEL/PinkPlatBerryCollectTrigger", "x": 0, "y": 0,
                 "width": 16, "height": 184}]},
  {"room": "a-99", "x": 0, "y": 2400, "width": 320, "height": 184,
   "solids": "1111…\n1000…", "attrs": {"dark": true},
   "entities": [{"name": "player", "x": 24, "y": 160}]}
]}
```

- **Defaults** come from the Lönn plugin definitions: your mod's `Loenn/`, other
  installed mods (when the workspace is in `Celeste/Mods`), then vanilla Lönn.
  `strawberry` above gets `winged`, `moon`, `checkpointID` and `order`.
  `"placement": "normal_winged"` picks another placement's defaults; any other key
  (flat or under `"data"`) sets an attribute; `"nodes": [[x, y], …]` adds nodes.
- **Unknown names are flagged** (typos, a trigger listed as an entity, a helper that
  isn't installed). `check_authoring_json` shows the resolved result without a map.
- **Existing rooms are only added to.** Room attributes and tiles are accepted only
  when creating a room (give `x`, `y`, `width`, `height`).
- Room names match with or without `lvl_`.

### Merging: `merge_json_into_map`

```text
merge_json_into_map("Maps/MyMod/1-City.bin", "plan.json")                  # dry run + preview
merge_json_into_map("Maps/MyMod/1-City.bin", "plan.json", dry_run=false)   # write
```

- **Dry run by default.** It prints a per-room diff and renders an HTML preview
  with every added object ringed (magenta, or red when a check failed).
- **IDs** are unique across the whole map: new ones start above the highest ID in
  any room, and an explicit `"id"` that is already used is refused.
- **Placement checks** for each added object: inside the room, not inside solid
  tiles, not in an air pocket sealed off from every room edge and spawn. The
  existing room validators (`validate_room`, `validate_map`) run before and after,
  and any issue the merge introduces is reported. A write with problems is refused
  unless `allow_problems=true`; unknown names need `allow_unknown=true`.
- **Only additions.** The output bytes are re-read and compared with the original;
  if anything that was already there differs, nothing is written.

### Authoring maps from JavaScript

`js/` is a small dependency-free Node package whose API follows
[lucid-dream](https://www.npmjs.com/package/lucid-dream) (see
`THIRD_PARTY_NOTICES.md`). It produces authoring JSON; the Python server does all
`.bin` writing.

```js
// add-berries.js
const { Plan, Room, Entity, Trigger, Tiles, line } = require('loenn-mcp-author'); // or require('./js')

const plan = new Plan();
plan.room('a-05').add(Entity.Strawberry({ x: 120, y: 64 }));          // existing room
for (const [x, y] of line(64, 96, 192, 96, 3)) plan.room('a-06').add(Entity.Strawberry({ x, y }));
plan.room('l-07').add(Trigger('CEL/PinkPlatBerryCollectTrigger', { x: 1700, y: 300, width: 56, height: 104 }));

// a new room, lucid-dream style: pixel position, size in tiles, tiles as text
plan.addRoom(new Room([0, 2400], { name: 'a-99', size: [40, 23], fgTiles: new Tiles(`...`),
  entities: [Entity.Player({ x: 24, y: 160 })] }));

module.exports = plan;          // or: module.exports = async (api) => plan;  or: plan.emit();
```

- `Entity.Strawberry(...)` camel-cases the property to the placed name
  (`strawberry`); namespaced names use the call form, `Entity('CEL/GreenBerry', {...})`.
- Run it through the server (dry run, with preview):
  `run_author_script("add-berries.js", map_path="Maps/MyMod/1-City.bin")`.
- Or from a shell: `node js/bin/loenn-author.js add-berries.js --out plan.json`,
  or `loenn-json author add-berries.js --map Maps/MyMod/1-City.bin [--write]`.
- Tests: `node js/test/test.js`. Examples: `js/examples/`.

### JSON-driven preview

`render_map_html` takes a typed JSON dump as well as a `.bin`, and an `overlay`
(authoring JSON) that is merged in memory only and highlighted. This is the
viewer idea from [maddievision/celestial](https://github.com/maddievision/celestial)
built on the existing renderer.

### `loenn-json` CLI

```bash
loenn-json map2json Maps/MyMod/1-City.bin -o city.json
loenn-json json2map city.json Maps/MyMod/1-City.bin --overwrite
loenn-json --mod . merge Maps/MyMod/1-City.bin plan.json            # dry run
loenn-json --mod . merge Maps/MyMod/1-City.bin plan.json --write
loenn-json --mod . author add-berries.js --map Maps/MyMod/1-City.bin
```

More examples: `examples/authoring/`.

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
| `LOENN_MCP_LOENN` | (shipped table) | A Lönn install (folder with `Lönn.exe`, or its `src/`) to read vanilla entity/trigger defaults from, instead of `loenn_mcp/data/vanilla_defs.json` |
| `LOENN_MCP_NODE` | `node` on PATH | Node.js executable for `run_author_script` |
| `LOENN_MCP_AUTHOR_CLI` | bundled `js/bin/loenn-author.js` | Alternative authoring CLI |

---

## Architecture

### Core Modules

**`celeste_bin.py`** — Standalone `.bin` parser
- Pure Python (no Everest/Lönn required)
- Lossless read/write: an unedited map is written back byte-for-byte (string table order, value types and RLE data are preserved), and edits only append to the string table
- Handles all 7 value types: bool, uint8, int16, int32, float32, lookup string, raw string, RLE-encoded string
- Recursive element tree matching Lönn/Maple format

**`map_json.py`** — Typed JSON for `.bin` maps
- Every value keeps its type; unedited dumps convert back byte-identical (verified on export)
- Refuses writes that would not round-trip; atomic writes through `celeste_bin`

**`lua_defs.py`** — Lönn plugin definitions without running Lua
- Tokenizes plugin files and evaluates literal tables (placements, field types, node limits)
- Catalog of the mod's own, other installed mods' and vanilla definitions (`data/vanilla_defs.json`, generated from Lönn by `tools/gen_vanilla_defs.py`)

**`authoring.py`** — Authoring JSON and merges
- Default filling, unknown-name flags, map-wide ID allocation, placement checks
- Additions-only verification on the re-read output bytes

**`js/`** — `loenn-mcp-author`, the JavaScript authoring package (Node, no dependencies)

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
- Node.js 14+ (optional, only for JavaScript authoring scripts)

Install with all optional features:
```bash
pip install loenn-mcp[image]
```

No Celeste installation required.

---

## License

MIT — see [LICENSE](LICENSE). Credits for lucid-dream, Lönn and maddievision/celestial: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
