# loenn-mcp

[![PyPI](https://img.shields.io/pypi/v/loenn-mcp)](https://pypi.org/project/loenn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

**Celeste map editor for AI agents** — a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets GitHub Copilot, Claude, and other MCP clients read, edit, analyze, **procedurally generate**, and preview Celeste `.bin` map files without ever opening Lönn.

Built for use with [Everest](https://github.com/EverestAPI/Everest) mods. Works with maps created by [Lönn](https://github.com/CelestialCartographers/Loenn) or [Ahorn](https://github.com/CelestialCartographers/Ahorn).

---

## Features

### 22 MCP tools across 6 categories

**Map Reading**
| Tool | Description |
|---|---|
| `list_maps` | List all `.bin` files in the project |
| `read_map_overview` | Summary of rooms, entities, triggers, and stylegrounds |
| `read_room` | Full detail for a single room: tiles, entities, triggers, decals |
| `get_room_tiles` | Raw tile grid (foreground or background) for a room |

**Map Editing**
| Tool | Description |
|---|---|
| `add_entity` | Place an entity in a room (auto-assigns ID) |
| `remove_entity` | Delete an entity by ID |
| `set_room_tiles` | Replace the tile grid for a room |
| `add_room` | Create a new room with custom position/size |
| `remove_room` | Delete a room from the map |
| `create_map` | Create a new empty `.bin` map file |

**Entity / Trigger Catalog**
| Tool | Description |
|---|---|
| `list_entity_definitions` | Browse Lönn entity `.lua` files in the project |
| `get_entity_definition` | Read the full source of a single entity definition |
| `list_trigger_definitions` | Browse Lönn trigger `.lua` files |
| `list_effect_definitions` | Browse Lönn effect `.lua` files |

**Analysis**
| Tool | Description |
|---|---|
| `analyze_map` | Statistics: entity counts, type breakdown, world bounds |
| `visualize_map_layout` | ASCII mini-map of room positions |
| `preview_map_section` | Detailed ASCII preview of a map region |

**Rendering**
| Tool | Description |
|---|---|
| `render_map_html` | Interactive HTML preview (zoom, pan, room details, minimap, search) |

**Procedural Generation (NEW in v2)**
| Tool | Description |
|---|---|
| `build_pattern_library` | Scan local `.bin` maps and extract room patterns into a reusable JSON library |
| `generate_room_from_pattern` | Generate a new room using patterns + a strategy + seed |
| `validate_room` | Check a room for playability issues (spawn, floor, bounds) |
| `ingest_external_map` | Download maps from external URLs (GameBanana etc.) and extract patterns |

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

New in v2. Provides:

- **Pattern extraction** — converts `.bin` rooms into reusable pattern records (size class, entity density, tile motifs, trigger usage, gameplay tags)
- **Pattern library** — JSON-based store with deduplication by content hash
- **Strategy-based generation** — `balanced`, `exploration`, `challenge`, `speedrun` modes
- **Seeded randomness** — `random.Random(seed)` for reproducible outputs; seed exposed via MCP tool parameters
- **Model profiles** — `deterministic` / `creative` / `architect` profiles control how seeds are resolved

### `server.py` — MCP server

Built with [FastMCP](https://github.com/jlowin/fastmcp). All file paths are resolved relative to `LOENN_MCP_WORKSPACE` with path-traversal protection. Map writes are atomic (parse → mutate → write). External downloads require explicit `confirm_download=True`.

---

## Requirements

- Python 3.9+
- `fastmcp >= 3.0.0`
- `pygame >= 2.6.1`
- `pytmx >= 3.32`

No Celeste installation required to parse, generate, or preview maps.

---

## License

MIT — see [LICENSE](LICENSE).
