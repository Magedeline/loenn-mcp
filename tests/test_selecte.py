"""seleCte bridge: locating the clone, the log summary and the selecte_generate
tool against a fake seleCte (always), and one real generate + encode + validate
run with LOENN_MCP_TEST_SELECTE=1 (needs the clone, uv and Julia)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("LOENN_MCP_STATE_DIR", tempfile.mkdtemp(prefix="loenn-mcp-test-state-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loenn_mcp import celeste_bin as cb, selecte  # noqa: E402
from loenn_mcp import server as s  # noqa: E402
from test_phase1 import Workspace, _map, _room  # noqa: E402


def _fake_clone(root: Path) -> Path:
    (root / selecte.ENCODER).parent.mkdir(parents=True, exist_ok=True)
    (root / selecte.ENCODER).write_text("# stand-in\n", encoding="utf-8")
    (root / "pyproject.toml").write_text("[project]\nname = 'seleCte'\n", encoding="utf-8")
    (root / selecte.MODELS).mkdir(parents=True, exist_ok=True)
    for m in ("pe_d5_full", "2R_000011012"):
        (root / selecte.MODELS / f"{m}.json").write_text("{}", encoding="utf-8")
    return root


class FakeSelecte:
    """Stands in for `uv run ... level_generation` and `julia ... data_to_level.jl`."""

    def __init__(self, unrecognized_flag=False, spawnless=False):
        self.unrecognized_flag = unrecognized_flag
        self.spawnless = spawnless
        self.calls: list[list[str]] = []

    def __call__(self, cmd, cwd, timeout):
        self.calls.append(list(cmd))
        if "seleCte.pcg.level_generation" in cmd:
            if self.unrecognized_flag:
                return subprocess.CompletedProcess(cmd, 2, "", "usage: level_generation\nlevel_generation: error: "
                                                                "unrecognized arguments: --seed 7\n")
            level_dir = Path(cmd[cmd.index("--level-name") + 1])
            n = int(cmd[cmd.index("--nb-rooms") + 1])
            level_dir.mkdir(parents=True, exist_ok=True)
            data = {f"room_{i}": {"origin": [0, 0], "size": [22, 14], "exits": {}} for i in range(1, n + 1)}
            (level_dir / "data.json").write_text(json.dumps(data), encoding="utf-8")
            for i in range(1, n + 1):
                (level_dir / f"room_{i}.csv").write_text("0,0\n", encoding="utf-8")
            log = "".join(f"INFO:__main__:Generated room_{i} in 1 iterations.\n" for i in range(1, n))
            log += (f"WARNING:__main__:room_{n} generation failed in 4 tries.\n"
                    "WARNING:__main__:Pursuing generation with an unplayable room.\n")
            return subprocess.CompletedProcess(cmd, 0, "", log)
        if "data_to_level.jl" in str(cmd[2]):
            level_dir, name, out_dir = Path(cmd[3]), cmd[4], Path(cmd[5])
            n = len(json.loads((level_dir / "data.json").read_text(encoding="utf-8")))
            rooms = [_room(f"room_{i}", x=(i - 1) * 320, spawn=not (self.spawnless and i == 1))
                     for i in range(1, n + 1)]
            out_dir.mkdir(parents=True, exist_ok=True)
            cb.write_map(out_dir / f"{name}.bin", _map(*rooms), backup=False)
            return subprocess.CompletedProcess(cmd, 0, f"Done - encoded {n} rooms into {out_dir / (name + '.bin')}\n",
                                               "")
        raise AssertionError(f"unexpected command {cmd}")


class Locating(unittest.TestCase):
    def test_env_override_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _fake_clone(Path(tmp) / "clone")
            with mock.patch.dict(os.environ, {"SELECTE_DIR": str(root)}):
                self.assertEqual(selecte.find_dir(), root)
                self.assertEqual(selecte.models(root), ["2R_000011012", "pe_d5_full"])
            with mock.patch.dict(os.environ, {"SELECTE_DIR": str(Path(tmp) / "nowhere")}), \
                    mock.patch.object(Path, "home", return_value=Path(tmp)), \
                    mock.patch.object(selecte, "__file__", str(Path(tmp) / "a" / "b" / "selecte.py")):
                with self.assertRaises(selecte.SelecteError) as ctx:
                    selecte.find_dir()
                self.assertIn("SELECTE_DIR", str(ctx.exception))
                self.assertIn("nowhere", str(ctx.exception))


class LogSummary(unittest.TestCase):
    def test_counts_and_skeleton_reset(self):
        log = ("INFO:__main__:Generated room_1 in 1 iterations.\n"
               "WARNING:__main__:room_2 generation failed in 4 tries.\n"
               "WARNING:__main__:Re-generating a skeleton.\n"
               "INFO:__main__:Generated room_1 in 2 iterations.\n"
               "INFO:__main__:Generated room_2 in 1 iterations.\n"
               "WARNING:__main__:room_3 generation failed in 4 tries.\n"
               "WARNING:__main__:Pursuing generation with an unplayable room.\n"
               "INFO:__main__:Filling room_4, off the traversal path.\n")
        sm = selecte.summarize_log(log)
        self.assertEqual(sm.generated, ["room_1", "room_2"])
        self.assertEqual(sm.unplayable, ["room_3"])
        self.assertEqual(sm.skeleton_redraws, 1)
        self.assertEqual(sm.off_path, ["room_4"])


class GenerateTool(Workspace):
    def setUp(self):
        super().setUp()
        self.clone = _fake_clone(self.ws.parent / "seleCte")
        self.fake = FakeSelecte()
        self._patches = [mock.patch.dict(os.environ, {"SELECTE_DIR": str(self.clone)}),
                         mock.patch.object(selecte, "_run", self.fake),
                         mock.patch.object(selecte, "_exe", lambda name, env: name)]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()
        super().tearDown()

    def test_generate_encode_validate_without_writing(self):
        out = s.selecte_generate("lvl-a", nb_rooms=3, seed=7, model="2R_000011012")
        self.assertIn("3 rooms", out)
        self.assertIn("Validation: OK", out)
        self.assertIn("could not make playable: room_3", out)
        self.assertNotIn("[saved", out)
        work = selecte.default_work_dir("lvl-a")
        self.assertTrue((work / "lvl-a.bin").exists())
        self.assertTrue((work / "data.json").exists())
        self.assertTrue((work / "generate.log").exists())
        gen = self.fake.calls[0]
        self.assertEqual(gen[:6], ["uv", "run", "--frozen", "python", "-m", "seleCte.pcg.level_generation"])
        self.assertEqual(gen[gen.index("--seed") + 1], "7")
        self.assertEqual(gen[gen.index("--dict-proba-estimation") + 1], "2R_000011012")
        self.assertEqual(gen[gen.index("--room-size") + 1:gen.index("--room-size") + 3], ["22", "14"])
        enc = self.fake.calls[1]
        self.assertEqual(enc[0], "julia")
        self.assertEqual(enc[1], f"--project={self.clone}")
        self.assertEqual(enc[4], "lvl-a")

    def test_default_model_is_not_passed(self):
        s.selecte_generate("lvl-b", nb_rooms=2)
        gen = self.fake.calls[0]
        self.assertNotIn("--seed", gen)
        self.assertNotIn("--dict-proba-estimation", gen)
        self.assertNotIn("--reset-skeleton", gen)
        s.selecte_generate("lvl-b", nb_rooms=2, reset_skeleton=True)
        self.assertIn("--reset-skeleton", self.fake.calls[2])

    def test_copy_into_workspace(self):
        out = s.selecte_generate("lvl-c", nb_rooms=2, output_path="Maps/TestMod/Area/pcg.bin")
        self.assertIn("[saved Maps/TestMod/Area/pcg.bin", out)
        target = self.ws / "Maps" / "TestMod" / "Area" / "pcg.bin"
        self.assertEqual(target.read_bytes(), (selecte.default_work_dir("lvl-c") / "lvl-c.bin").read_bytes())
        self.assertEqual([r["name"] for r in cb.get_rooms(cb.read_map(target))], ["room_1", "room_2"])
        self.assertIn("Not generated", s.selecte_generate("lvl-c", nb_rooms=2, output_path="../outside.bin"))
        self.assertIn("Not generated", s.selecte_generate("lvl-c", nb_rooms=2, output_path="Maps/x.txt"))

    def test_problems_block_the_copy_unless_allowed(self):
        self.fake.spawnless = True
        out = s.selecte_generate("lvl-d", nb_rooms=2, output_path="Maps/TestMod/Area/bad.bin")
        self.assertIn("no-spawn", out)
        self.assertIn("Not copied", out)
        self.assertFalse((self.ws / "Maps" / "TestMod" / "Area" / "bad.bin").exists())
        out = s.selecte_generate("lvl-d", nb_rooms=2, output_path="Maps/TestMod/Area/bad.bin", allow_problems=True)
        self.assertIn("[saved", out)
        self.assertTrue((self.ws / "Maps" / "TestMod" / "Area" / "bad.bin").exists())

    def test_protected_target_keeps_the_report(self):
        os.environ["LOENN_MCP_PROTECT"] = "Maps/TestMod/Area/*.bin"
        out = s.selecte_generate("lvl-e", nb_rooms=2, output_path="Maps/TestMod/Area/01.bin")
        self.assertIn("Validation: OK", out)
        self.assertIn("Not copied", out)
        self.assertIn("protected", out.lower())

    def test_generator_failure_and_bad_requests(self):
        self.fake.unrecognized_flag = True
        out = s.selecte_generate("lvl-f", nb_rooms=2, seed=7)
        self.assertIn("Not generated", out)
        self.assertIn("patched seleCte clone", out)
        self.assertIn("unrecognized arguments", out)
        self.fake.unrecognized_flag = False
        self.fake.calls.clear()
        self.assertIn("name must be", s.selecte_generate("bad name", nb_rooms=2))
        out = s.selecte_generate("lvl-g", nb_rooms=2, model="nope")
        self.assertIn("not in", out)
        self.assertIn("pe_d5_full", out)
        self.assertIn("proba", s.selecte_generate("lvl-g", nb_rooms=2, proba=1.5))
        self.assertEqual(self.fake.calls, [])

    def test_stale_rooms_are_cleared_before_a_rerun(self):
        work = selecte.default_work_dir("lvl-h")
        work.mkdir(parents=True)
        (work / "room_9.csv").write_text("stale", encoding="utf-8")
        s.selecte_generate("lvl-h", nb_rooms=2)
        self.assertFalse((work / "room_9.csv").exists())
        self.assertTrue((work / "room_2.csv").exists())


@unittest.skipUnless(os.environ.get("LOENN_MCP_TEST_SELECTE") == "1", "set LOENN_MCP_TEST_SELECTE=1")
class Live(unittest.TestCase):
    def test_real_two_room_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = selecte.run_level("loenn_mcp_live", nb_rooms=2, width=22, height=14, seed=1, tries_limit=2,
                                    work_dir=Path(tmp) / "lvl", timeout=600)
            self.assertTrue(res.bin_path.exists())
            self.assertEqual(res.rooms, 2)
            self.assertTrue(res.validation["ok"], res.validation)


if __name__ == "__main__":
    unittest.main()
