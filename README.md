# loenn-mcp

[![PyPI](https://img.shields.io/pypi/v/loenn-mcp)](https://pypi.org/project/loenn-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)

**Celeste map editor for AI agents** — a [Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that lets GitHub Copilot, Claude, and other MCP clients read, edit, analyze, and preview Celeste `.bin` map files without ever opening Lönn.

Built for use with [Everest](https://github.com/EverestAPI/Everest) mods. Works with maps created by [Lönn](https://github.com/CelestialCartographers/Loenn) or [Ahorn](https://github.com/CelestialCartographers/Ahorn).

---

## Features

### 18 MCP tools across 5 categories

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
- *"Which entity type appears most often across all maps?"*

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

You can import it independently:

```python
import celeste_bin as cb

data = cb.read_map("Maps/7-Summit.bin")
rooms = cb.get_rooms(data)
for room in rooms:
    print(room["name"], room["width"], room["height"])

cb.write_map("Maps/7-Summit.bin", data)
```

### `server.py` — MCP server

Built with [FastMCP](https://github.com/jlowin/fastmcp). All file paths are resolved relative to `LOENN_MCP_WORKSPACE` with path-traversal protection. Map writes are atomic (parse → mutate → write).

---

## Procedural Generation (PCG)

The `Maps/PCG/` folder contains maps generated entirely by AI agents using this server. The typical workflow:

1. Agent calls `create_map` to make a blank `.bin`
2. Agent calls `add_room` to lay out rooms
3. Agent calls `set_room_tiles` to fill in tile grids
4. Agent calls `add_entity` to place spawnpoints, hazards, collectibles
5. Agent calls `render_map_html` to get a visual review

This is usable for rapid prototyping, layout sketching, or fully AI-authored levels.

---

## Requirements

- Python 3.9+
- `fastmcp >= 3.0.0`

No Celeste installation required to parse or generate maps.

---

## License

MIT — see [LICENSE](LICENSE).
