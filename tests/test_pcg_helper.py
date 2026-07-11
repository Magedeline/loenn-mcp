"""Regression tests for the MdMC tile generator in loenn_mcp.pcg_helper.

Run with:  python -m unittest tests.test_pcg_helper -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from loenn_mcp import pcg_helper as ph


def _build_training_room(w: int = 20, h: int = 14) -> list:
    """Hand-built room with real platformer geometry: floor, a floating
    platform, and a vertical wall — not just a hollow border."""
    grid = ph.new_grid(w, h, "0")

    # Solid border (as a real Celeste room would have).
    for x in range(w):
        grid[x][0] = "3"
        grid[x][h - 1] = "3"
    for y in range(h):
        grid[0][y] = "3"
        grid[w - 1][y] = "3"

    # Ground floor spanning most of the width.
    for x in range(1, w - 1):
        grid[x][h - 2] = "3"
        grid[x][h - 3] = "3"

    # A floating platform in the middle of the room.
    for x in range(6, 13):
        grid[x][h - 7] = "3"

    # A vertical wall segment.
    for y in range(2, h - 3):
        grid[15][y] = "3"

    return grid


class MdmcCausalScanDirsTest(unittest.TestCase):
    def test_default_preset_is_fully_causal_when_reversed(self):
        # Default "000011012" preset = East + South neighbours only.
        offsets = ph.parse_config(ph.MDMC_DEFAULT)
        x_dir, y_dir = ph._causal_scan_dirs(offsets)
        self.assertEqual((x_dir, y_dir), (-1, -1))

    def test_cross_preset_keeps_forward_scan(self):
        # N+W+E+S is symmetric on both axes -> ties break to forward scan.
        offsets = ph.parse_config("010111010")
        x_dir, y_dir = ph._causal_scan_dirs(offsets)
        self.assertEqual((x_dir, y_dir), (1, 1))


class MdmcGenerateInteriorTest(unittest.TestCase):
    def test_generated_room_has_non_trivial_interior_solids(self):
        w, h = 20, 14
        training_grid = _build_training_room(w, h)
        offsets = ph.parse_config(ph.MDMC_DEFAULT)
        model = ph.train_mdmc([(training_grid, w, h)], offsets)

        rng = ph.LcgRng(seed=12345)
        grid = ph.mdmc_generate(model, w, h, rng, keep_border=True)

        interior_total = (w - 2) * (h - 2)
        interior_solid = sum(
            1
            for x in range(1, w - 1)
            for y in range(1, h - 1)
            if grid[x][y] != "0"
        )
        solid_fraction = interior_solid / interior_total

        # Before the causal-scan-order fix, the E+S default preset's
        # context was frozen at "0,0" (air, air) for almost every cell
        # (both neighbours are still unvisited air at generation time),
        # collapsing every room to a hollow border with zero interior
        # solids. A correct generator should reproduce a meaningful
        # fraction of the training room's solid density.
        self.assertGreater(
            solid_fraction, 0.05,
            f"expected non-trivial interior solid density, got {solid_fraction:.3f} "
            f"({interior_solid}/{interior_total} solid) -- looks like a hollow box",
        )

    def test_generated_room_is_not_just_the_border(self):
        w, h = 16, 12
        training_grid = _build_training_room(w, h)
        offsets = ph.parse_config(ph.MDMC_DEFAULT)
        model = ph.train_mdmc([(training_grid, w, h)], offsets)

        rng = ph.LcgRng(seed=7)
        grid = ph.mdmc_generate(model, w, h, rng, keep_border=True)

        interior_solid_cells = [
            (x, y)
            for x in range(1, w - 1)
            for y in range(1, h - 1)
            if grid[x][y] != "0"
        ]
        self.assertTrue(
            interior_solid_cells,
            "generated room has zero solid interior tiles (hollow box regression)",
        )


if __name__ == "__main__":
    unittest.main()
