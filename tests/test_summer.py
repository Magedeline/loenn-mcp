"""Summer Engine bridge: mapping between Celeste rooms and scene dumps (always),
and a live round trip through Summer.exe (LOENN_MCP_TEST_SUMMER=1)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("LOENN_MCP_STATE_DIR", tempfile.mkdtemp(prefix="loenn-mcp-test-state-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loenn_mcp import celeste_bin as cb, mapdiff, summer  # noqa: E402
from loenn_mcp import server as s  # noqa: E402
from test_phase1 import Workspace, _map, _room  # noqa: E402


def _graybox_room():
    r = _room("lvl_g", w=320, h=184)
    rows = cb.find_child(r, "solids")["innerText"].split("\n")
    rows[21] = "3" * 40                                   # a different tileset on one row
    cb.find_child(r, "solids")["innerText"] = "\n".join(rows)
    cb.find_child(r, "entities")["__children"] = [
        {"__name": "player", "id": 1, "x": 36, "y": 160, "__children": []},
        {"__name": "spikesUp", "id": 2, "x": 96, "y": 160, "width": 24, "type": "default", "__children": []},
        {"__name": "dreamBlock", "id": 3, "x": 128, "y": 48, "width": 16, "height": 24, "__children": []},
        {"__name": "bonfire", "id": 4, "x": 200, "y": 160, "mode": "Lit", "__children": []},
    ]
    return r


def _dump_from_instructions(ins: dict, room: str, tile: int = 16) -> dict:
    """What export_room.gd would report for a scene built from these instructions."""
    layers = [{"name": n, "collision": n == "fg", "visible": True, "tile_size": [tile, tile],
               "cells": [{"x": x, "y": y, "source": 0, "ax": i % 4, "ay": i // 4,
                          "texture": "res://celeste/celeste_graybox.png", "kind": summer.sf.GRAYBOX_ORDER[i],
                          "solid": summer.sf.GRAYBOX_ORDER[i] == "solid"} for x, y, i in cells]}
              for n, cells in ins["layers"].items()]
    markers = [{"name": m["entity"], "x": m["x"], "y": m["y"], "meta": {"entity": m["entity"], **m["meta"]}}
               for m in ins["markers"]]
    return {"scene": "res://rooms/g.tscn", "meta": {"celeste_room": room, "room_width_tiles": ins["width"],
                                                     "room_height_tiles": ins["height"]},
            "layers": layers, "markers": markers}


class Mapping(unittest.TestCase):
    def test_room_to_instructions_and_back(self):
        room = _graybox_room()
        ins, notes = summer.room_instructions(room)
        self.assertEqual(len(ins["markers"]), 1)
        self.assertEqual(ins["markers"][0]["entity"], "bonfire")
        self.assertEqual((ins["markers"][0]["x"], ins["markers"][0]["y"]), (400, 320))   # 16 px tiles
        conv, name = summer.scene_conversion(_dump_from_instructions(ins, "g"))
        self.assertEqual(name, "g")
        back = sorted((e["name"], e["x"], e["y"], e.get("width"), e.get("height"), e.get("mode")) for e in conv.entities)
        self.assertEqual(back, sorted([("player", 36, 160, None, None, None), ("spikesUp", 96, 160, 24, None, None),
                                       ("dreamBlock", 128, 48, 16, 24, None), ("bonfire", 200, 160, None, None, "Lit")]))
        self.assertEqual(conv.notes, [])
        self.assertEqual(conv.fg[21], "1" * 40)          # gray-box only knows "solid"

    def test_foreign_tiles_and_out_of_room_cells(self):
        dump = {"scene": "res://rooms/x.tscn", "meta": {"room_width_tiles": 40, "room_height_tiles": 23}, "markers": [],
                "layers": [
                    {"name": "Terrain", "collision": True, "tile_size": [8, 8], "cells": [
                        {"x": 0, "y": 22, "source": 3, "ax": 5, "ay": 1, "texture": "res://art/cave.png"},
                        {"x": -1, "y": 0, "source": 3, "ax": 5, "ay": 1, "texture": "res://art/cave.png"}]},
                    {"name": "Deco", "collision": False, "tile_size": [8, 8], "cells": [
                        {"x": 3, "y": 3, "source": 3, "ax": 1, "ay": 1, "texture": "res://art/cave.png"},
                        {"x": 4, "y": 4, "source": 3, "ax": 2, "ay": 1, "texture": "res://art/cave.png", "solid": True}]}]}
        conv, _ = summer.scene_conversion(dump)
        self.assertEqual(conv.fg[22][0], "1")             # "Terrain" counts as the solid layer
        self.assertEqual(conv.fg[4][4], "1")              # a tile with collision is solid on any layer
        self.assertEqual(conv.decoration, 1)
        self.assertTrue(any("outside" in n for n in conv.notes))


class ReplaceKeepsTilesets(Workspace):
    def test_solid_cells_keep_their_character(self):
        tree = cb.read_map(self.map)
        rows = cb.find_child(cb.get_room(tree, "a-00"), "solids")["innerText"].split("\n")
        self.assertEqual(s._keep_tile_chars(["0033", "3300"], ["0110", "1100"]), ["0130", "3300"])
        self.assertTrue(rows)


@unittest.skipUnless(os.environ.get("LOENN_MCP_TEST_SUMMER") == "1", "set LOENN_MCP_TEST_SUMMER=1 to run Summer.exe")
class LiveSummer(Workspace):
    def test_room_round_trips_through_a_summer_scene(self):
        tree = cb.read_map(self.map)
        tree["__children"][0]["__children"].append(_graybox_room() | {"x": 640})
        cb.write_map(self.map, tree, backup=False)
        project = self.state / "summer-proj"
        out = s.summer_init(str(project))
        self.assertIn("Summer project ready", out)
        out = s.summer_import_room(self.rel, "g", project_dir=str(project))
        self.assertIn("Wrote res://rooms/g.tscn", out)
        self.assertIn("1 marker entity", out)
        self.assertIn("g", s.summer_rooms(str(project)))
        before = self.map.read_bytes()
        out = s.summer_export_room("g", self.rel, replace=True, dry_run=False, project_dir=str(project))
        self.assertIn("replace room lvl_g", out)
        self.assertIn("[saved", out)
        d = mapdiff.diff_bytes(before, self.map.read_bytes())
        # Same entities and tiles (tileset chars kept); only entity ids are reassigned.
        changes = [c for r in d.rooms_changed for c in r.changes]
        self.assertFalse([c for c in changes if "tiles" in c], changes)
        self.assertEqual(d.rooms_added + d.rooms_removed, [])
        tree = cb.read_map(self.map)
        names = sorted(e["__name"] for e in cb.find_child(cb.get_room(tree, "g"), "entities")["__children"])
        self.assertEqual(names, ["bonfire", "dreamBlock", "player", "spikesUp"])


if __name__ == "__main__":
    unittest.main()
