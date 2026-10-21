"""pcg_generate_room through the celpcg CLI (skipped when dotnet is missing)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("LOENN_MCP_STATE_DIR", tempfile.mkdtemp(prefix="loenn-mcp-test-state-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loenn_mcp import celeste_bin as cb, mapdiff, pcg_core  # noqa: E402
from loenn_mcp import server as s  # noqa: E402
from test_phase1 import Workspace  # noqa: E402


class Seeds(unittest.TestCase):
    def test_fnv_matches_the_core(self):
        self.assertEqual(pcg_core.fnv1a64(""), 0xCBF29CE484222325)
        self.assertEqual(pcg_core.default_seed("Maps/x.bin", "a"), pcg_core.fnv1a64("Maps/x.bin|a"))


@unittest.skipUnless(shutil.which("dotnet"), "dotnet SDK not installed")
class GenerateTool(Workspace):
    exits = "left:16:4,right:10:4,top:20:4"

    def test_dry_run_then_write_with_seed_attributes(self):
        before = self.map.read_bytes()
        dry = s.pcg_generate_room(self.rel, "gen-01", exits=self.exits, difficulty=5, seed=42)
        self.assertIn("DRY RUN", dry)
        self.assertIn("reach check passed", dry)
        self.assertEqual(self.map.read_bytes(), before)
        out = s.pcg_generate_room(self.rel, "gen-01", exits=self.exits, difficulty=5, seed=42, dry_run=False)
        self.assertIn("[saved", out)
        room = cb.get_room(cb.read_map(self.map), "gen-01")
        self.assertEqual(room["celpcg_seed"], "42")
        self.assertEqual(room["celpcg_difficulty"], 5)
        names = {e["__name"] for e in cb.find_child(room, "entities")["__children"]}
        self.assertIn("player", names)
        self.assertLessEqual(names, {"player", "spikesUp", "jumpThru", "spring", "refill", "dreamBlock"})
        self.assertIn("Everything exists", s.check_assets(self.rel))

    def test_same_seed_same_room(self):
        a, b = self.ws / "Maps" / "a.bin", self.ws / "Maps" / "b.bin"
        for out in (a, b):
            s.pcg_generate_room(self.rel, "gen", exits=self.exits, difficulty=6, seed=7,
                                output_path=str(out.relative_to(self.ws)), dry_run=False)
        self.assertTrue(mapdiff.diff_bytes(a.read_bytes(), b.read_bytes()).identical)
        s.pcg_generate_room(self.rel, "gen", exits=self.exits, difficulty=6, seed=8,
                            output_path="Maps/c.bin", dry_run=False)
        d = mapdiff.diff_bytes(a.read_bytes(), (self.ws / "Maps" / "c.bin").read_bytes())
        self.assertFalse(d.identical)

    def test_impossible_request_is_refused(self):
        out = s.pcg_generate_room(self.rel, "bad", exits="top:10:4,right:10:4")
        self.assertIn("Not generated", out)
        self.assertIn("left or right", out)


if __name__ == "__main__":
    unittest.main()
