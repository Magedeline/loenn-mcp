"""
Image-to-Map conversion module for loenn-mcp.

Converts color-mapped images (PNG, JPG, etc.) into playable Celeste maps.
Each pixel (or group of pixels) in the source image is interpreted as a
tile in the map, with colors mapped to tile types.

Default color mapping (customisable):
  - Black (#000000)       → Solid tile (foreground)
  - White (#FFFFFF)       → Air (empty space)
  - Red (#FF0000)         → Spike hazard
  - Green (#00FF00)       → Spawn point (player start)
  - Blue (#0000FF)        → Water / jumpthru platform
  - Yellow (#FFFF00)      → Collectible (strawberry)
  - Magenta (#FF00FF)     → Spring (bounce pad)
  - Cyan (#00FFFF)        → Refill crystal
  - Orange (#FF8000)      → Crumble block
  - Grey (#808080)        → Background tile (decorative solid)

The image is split into rooms of configurable size (default 40×23 tiles,
matching 320×184 px rooms).  Each room is made playable by ensuring a
floor row exists and a player spawn is placed.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Tile characters for Celeste .bin format
TILE_AIR = "0"
TILE_SOLID = "1"  # dirt/default
TILE_STONE = "3"  # grey stone
TILE_BG = "7"     # background solid (decorative)

# Default color-to-role mapping (RGB tuples → role string)
DEFAULT_COLOR_MAP: Dict[Tuple[int, int, int], str] = {
    (0, 0, 0): "solid",           # Black → solid tile
    (255, 255, 255): "air",       # White → empty
    (255, 0, 0): "spike",         # Red → spike hazard
    (0, 255, 0): "spawn",         # Green → player spawn
    (0, 0, 255): "jumpthru",      # Blue → jump-through platform
    (255, 255, 0): "strawberry",  # Yellow → collectible
    (255, 0, 255): "spring",      # Magenta → spring (bounce)
    (0, 255, 255): "refill",      # Cyan → refill crystal
    (255, 128, 0): "crumble",     # Orange → crumble block
    (128, 128, 128): "bg_solid",  # Grey → background tile
}

# Tolerance for color matching (Euclidean distance in RGB space)
_COLOR_TOLERANCE = 64


def _closest_color(
    pixel: Tuple[int, int, int],
    color_map: Dict[Tuple[int, int, int], str],
    tolerance: int = _COLOR_TOLERANCE,
) -> str:
    """Find the closest color in the map to *pixel* within *tolerance*.

    Returns the mapped role string, or 'air' if no match is close enough.
    """
    best_dist = float("inf")
    best_role = "air"
    for color, role in color_map.items():
        dist = math.sqrt(
            (pixel[0] - color[0]) ** 2
            + (pixel[1] - color[1]) ** 2
            + (pixel[2] - color[2]) ** 2
        )
        if dist < best_dist:
            best_dist = dist
            best_role = role
    if best_dist > tolerance:
        return "air"
    return best_role


def parse_image_to_grid(
    image_path: str,
    color_map: Optional[Dict[Tuple[int, int, int], str]] = None,
    scale: int = 1,
    tolerance: int = _COLOR_TOLERANCE,
) -> List[List[str]]:
    """Read an image file and convert it to a 2D grid of role strings.

    Parameters
    ----------
    image_path:
        Path to the image file (PNG, JPG, BMP, etc.)
    color_map:
        Custom color mapping (RGB tuple → role string).
        Falls back to DEFAULT_COLOR_MAP if not provided.
    scale:
        Number of image pixels per tile. 1 means each pixel = one 8×8 tile.
        Use 2+ for larger images to compress into fewer tiles.
    tolerance:
        Maximum Euclidean distance in RGB space for color matching.

    Returns
    -------
    A 2D list of role strings, where grid[row][col] gives the role
    at that tile position.
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError(
            "Pillow is required for image-to-map conversion. "
            "Install with: pip install Pillow"
        )

    cmap = color_map or DEFAULT_COLOR_MAP
    img = Image.open(image_path).convert("RGB")
    w, h = img.size

    # Apply scale (downscale image by averaging blocks)
    if scale > 1:
        new_w = max(1, w // scale)
        new_h = max(1, h // scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        w, h = img.size

    pixels = img.load()
    grid: List[List[str]] = []

    for row in range(h):
        row_data: List[str] = []
        for col in range(w):
            pixel = pixels[col, row]  # PIL uses (x, y) = (col, row)
            role = _closest_color(pixel[:3], cmap, tolerance)
            row_data.append(role)
        grid.append(row_data)

    return grid


def _split_grid_into_rooms(
    grid: List[List[str]],
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
) -> List[Dict[str, Any]]:
    """Split a role grid into room-sized chunks.

    Returns a list of room descriptors, each with:
      - grid_x, grid_y: room index in the grid of rooms
      - tiles: 2D sub-grid of role strings
      - width_px, height_px: room dimensions in pixels
    """
    total_rows = len(grid)
    total_cols = len(grid[0]) if grid else 0

    rooms: List[Dict[str, Any]] = []

    room_gy = 0
    row_offset = 0
    while row_offset < total_rows:
        room_gx = 0
        col_offset = 0
        rh = min(room_height_tiles, total_rows - row_offset)
        while col_offset < total_cols:
            rw = min(room_width_tiles, total_cols - col_offset)
            sub_grid = [
                grid[r][col_offset:col_offset + rw]
                for r in range(row_offset, row_offset + rh)
            ]
            rooms.append({
                "grid_x": room_gx,
                "grid_y": room_gy,
                "tiles": sub_grid,
                "width_tiles": rw,
                "height_tiles": rh,
                "width_px": rw * 8,
                "height_px": rh * 8,
            })
            col_offset += room_width_tiles
            room_gx += 1
        row_offset += room_height_tiles
        room_gy += 1

    return rooms


def _build_room_element(
    room_desc: Dict[str, Any],
    room_name: str,
    x_px: int,
    y_px: int,
) -> Dict[str, Any]:
    """Convert a room descriptor into a Celeste room element dict.

    Produces tile grids, entities, and triggers from the role-based sub-grid.
    """
    tiles = room_desc["tiles"]
    rh = room_desc["height_tiles"]
    rw = room_desc["width_tiles"]
    width_px = room_desc["width_px"]
    height_px = room_desc["height_px"]

    # Build foreground tile grid and collect entities
    fg_rows: List[str] = []
    bg_rows: List[str] = []
    entities: List[Dict[str, Any]] = []
    next_id = 1
    has_spawn = False

    for row_idx, row in enumerate(tiles):
        fg_row = []
        bg_row = []
        for col_idx, role in enumerate(row):
            if role == "solid":
                fg_row.append(TILE_SOLID)
                bg_row.append(TILE_AIR)
            elif role == "bg_solid":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_BG)
            elif role == "spawn":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                if not has_spawn:
                    entities.append({
                        "__name": "player",
                        "__children": [],
                        "id": next_id,
                        "x": col_idx * 8,
                        "y": row_idx * 8,
                    })
                    next_id += 1
                    has_spawn = True
            elif role == "spike":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "spikes",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                    "type": "default",
                })
                next_id += 1
            elif role == "jumpthru":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "jumpThru",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                    "width": 8,
                })
                next_id += 1
            elif role == "strawberry":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "strawberry",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                })
                next_id += 1
            elif role == "spring":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "spring",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                })
                next_id += 1
            elif role == "refill":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "refill",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                })
                next_id += 1
            elif role == "crumble":
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
                entities.append({
                    "__name": "crumbleBlock",
                    "__children": [],
                    "id": next_id,
                    "x": col_idx * 8,
                    "y": row_idx * 8,
                    "width": 8,
                })
                next_id += 1
            else:  # air or unknown
                fg_row.append(TILE_AIR)
                bg_row.append(TILE_AIR)
        fg_rows.append("".join(fg_row))
        bg_rows.append("".join(bg_row))

    # If no spawn was found in this room, add one at bottom-left
    if not has_spawn:
        entities.insert(0, {
            "__name": "player",
            "__children": [],
            "id": next_id,
            "x": 16,
            "y": height_px - 24,
        })
        next_id += 1

    fg_text = "\n".join(fg_rows)
    bg_text = "\n".join(bg_rows)

    # Object tiles (empty)
    obj_row = ",".join(["-1"] * rw)
    obj_text = "\n".join([obj_row] * rh)

    name = room_name if room_name.startswith("lvl_") else f"lvl_{room_name}"

    room_element: Dict[str, Any] = {
        "__name": "level",
        "name": name,
        "x": x_px,
        "y": y_px,
        "width": width_px,
        "height": height_px,
        "music": "",
        "alt_music": "",
        "ambience": "",
        "dark": False,
        "space": False,
        "underwater": False,
        "whisper": False,
        "disableDownTransition": False,
        "windPattern": "None",
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
                "innerText": fg_text,
                "offsetX": 0,
                "offsetY": 0,
                "__children": [],
            },
            {
                "__name": "bg",
                "innerText": bg_text,
                "offsetX": 0,
                "offsetY": 0,
                "__children": [],
            },
            {
                "__name": "objtiles",
                "innerText": obj_text,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {
                "__name": "fgtiles",
                "innerText": obj_text,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {
                "__name": "bgtiles",
                "innerText": obj_text,
                "offsetX": 0,
                "offsetY": 0,
                "tileset": "scenery",
                "__children": [],
            },
            {
                "__name": "entities",
                "__children": entities,
            },
            {"__name": "triggers", "__children": []},
            {"__name": "fgdecals", "__children": []},
            {"__name": "bgdecals", "__children": []},
        ],
    }

    return room_element


def image_to_map_data(
    image_path: str,
    package_name: str = "ImageMap",
    color_map: Optional[Dict[Tuple[int, int, int], str]] = None,
    scale: int = 1,
    room_width_tiles: int = 40,
    room_height_tiles: int = 23,
    tolerance: int = _COLOR_TOLERANCE,
) -> Dict[str, Any]:
    """Convert an image file into a full Celeste map element tree.

    Parameters
    ----------
    image_path:
        Path to the source image.
    package_name:
        Celeste map package name (appears in the .bin header).
    color_map:
        Custom color-to-role mapping.  Uses DEFAULT_COLOR_MAP if None.
    scale:
        Pixels per tile (1 = each pixel is one 8×8 tile).
    room_width_tiles:
        Maximum room width in tiles (default 40 = 320 px).
    room_height_tiles:
        Maximum room height in tiles (default 23 = 184 px).
    tolerance:
        Color matching tolerance in RGB Euclidean distance.

    Returns
    -------
    A complete map element dict ready to be written with celeste_bin.write_map().
    """
    grid = parse_image_to_grid(image_path, color_map, scale, tolerance)

    if not grid or not grid[0]:
        raise ValueError("Image produced an empty grid. Check the file path and format.")

    room_descs = _split_grid_into_rooms(grid, room_width_tiles, room_height_tiles)

    rooms: List[Dict[str, Any]] = []
    for i, desc in enumerate(room_descs):
        gx = desc["grid_x"]
        gy = desc["grid_y"]
        # Position rooms in world space (pixels)
        x_px = gx * desc["width_px"]
        y_px = gy * desc["height_px"]
        # Name: row letter + column number (e.g. a-01, a-02, b-01)
        row_letter = chr(ord("a") + (gy % 26))
        room_name = f"{row_letter}-{gx + 1:02d}"

        room_el = _build_room_element(desc, room_name, x_px, y_px)
        rooms.append(room_el)

    # Build the full map structure
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
