"""Phase 1: backup store and guards, copy/commit/restore, semantic diff, editor
adapters (with a fake editor process), asset checks and Sprite Fusion import.

Optional live checks:
  LOENN_MCP_TEST_LOENN=1   launch the real Lönn on a temp map and close it again
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("LOENN_MCP_STATE_DIR", tempfile.mkdtemp(prefix="loenn-mcp-test-state-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loenn_mcp import assets, mapdiff, safety, sprite_fusion as sf  # noqa: E402
from loenn_mcp import celeste_bin as cb  # noqa: E402
from loenn_mcp import editors  # noqa: E402
from loenn_mcp.editors import loenn as loenn_mod  # noqa: E402
from loenn_mcp import server as s  # noqa: E402


def _room(name, x=0, y=0, w=320, h=184, spawn=True, floor=True):
    tw, th = w // 8, h // 8
    rows = ["0" * tw] * (th - 2) + (["1" * tw] * 2 if floor else ["0" * tw] * 2)
    ents = [{"__name": "player", "id": 1, "x": 24, "y": h - 16, "__children": []}] if spawn else []
    return {"__name": "level", "name": name, "x": x, "y": y, "width": w, "height": h, "music": "",
            "__children": [
                {"__name": "solids", "innerText": "\n".join(rows), "__children": []},
                {"__name": "bg", "innerText": "", "__children": []},
                {"__name": "entities", "__children": ents},
                {"__name": "triggers", "__children": []},
                {"__name": "fgdecals", "__children": []},
                {"__name": "bgdecals", "__children": []},
            ]}


def _map(*rooms, package="Test/Map"):
    return {"__name": "Map", "_package": package, "__children": [
        {"__name": "levels", "__children": list(rooms)},
        {"__name": "Filler", "__children": []},
        {"__name": "Style", "__children": [{"__name": "Backgrounds", "__children": []},
                                           {"__name": "Foregrounds", "__children": []}]},
    ]}


class Workspace(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "Mods" / "TestMod"
        (self.ws / "Maps" / "TestMod" / "Area").mkdir(parents=True)
        self.map = self.ws / "Maps" / "TestMod" / "Area" / "01.bin"
        cb.write_map(self.map, _map(_room("lvl_a-00"), _room("lvl_a-01", x=320)), backup=False)
        self._old = s.WORKSPACE
        s.WORKSPACE = self.ws.resolve()
        s._catalog_cache.clear()
        self.state = Path(tempfile.mkdtemp(prefix="state-"))
        self._old_state = os.environ.get("LOENN_MCP_STATE_DIR")
        os.environ["LOENN_MCP_STATE_DIR"] = str(self.state)

    def tearDown(self):
        s.WORKSPACE = self._old
        os.environ["LOENN_MCP_STATE_DIR"] = self._old_state or ""
        for sess in list(editors.SESSIONS.values()):
            if sess.running():
                sess.proc.kill()
        editors.SESSIONS.clear()
        os.environ.pop("LOENN_MCP_PROTECT", None)
        shutil.rmtree(self.state, ignore_errors=True)
        self.tmp.cleanup()

    rel = "Maps/TestMod/Area/01.bin"


class SafetyTests(Workspace):
    def test_every_write_keeps_a_timestamped_backup_and_says_so(self):
        before = self.map.read_bytes()
        out = s.add_entity(self.rel, "a-00", "strawberry", 100, 100)
        self.assertIn("[saved Maps/TestMod/Area/01.bin", out)
        self.assertIn("backup", out)
        backups = safety.list_backups(self.map, self.ws)
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        self.assertFalse(str(backups[0]).startswith(str(self.ws)), "backups must live outside the mod folder")
        self.assertFalse(list(self.map.parent.glob("*.bak*")), "no rolling .bak files next to the map")

    def test_protected_maps_need_a_copy_and_commit(self):
        os.environ["LOENN_MCP_PROTECT"] = "Maps/*/Area/*.bin"
        before = self.map.read_bytes()
        out = s.add_entity(self.rel, "a-00", "strawberry", 100, 100)
        self.assertIn("Not written", out)
        self.assertIn("protected", out)
        self.assertEqual(self.map.read_bytes(), before)

        out = s.map_copy(self.rel)
        self.assertIn("Maps/TestMod/wip/01.copy.bin", out)
        copy = "Maps/TestMod/wip/01.copy.bin"
        self.assertIn("Added", s.add_entity(copy, "a-00", "strawberry", 100, 100))
        dry = s.map_commit(copy, self.rel)
        self.assertIn("DRY RUN", dry)
        self.assertIn("entity added: strawberry", dry)
        self.assertEqual(self.map.read_bytes(), before)
        done = s.map_commit(copy, self.rel, dry_run=False)
        self.assertIn("[saved Maps/TestMod/Area/01.bin", done)
        self.assertEqual(self.map.read_bytes(), (self.ws / copy).read_bytes())

    def test_commit_selected_rooms_and_refuse_spawnless(self):
        copy = "Maps/TestMod/wip/work.bin"
        s.map_copy(self.rel, copy)
        s.add_room(copy, "a-02", x=640, y=0)          # no spawn
        s.add_entity(copy, "a-01", "refill", 160, 100)
        out = s.map_commit(copy, self.rel, rooms="a-01,a-02", dry_run=False)
        self.assertIn("Not written", out)
        self.assertIn("a-02", out)
        out = s.map_commit(copy, self.rel, rooms="a-01", dry_run=False)
        self.assertIn("replaced a-01", out)
        tree = cb.read_map(self.map)
        self.assertEqual([r["name"] for r in cb.get_rooms(tree)], ["lvl_a-00", "lvl_a-01"])
        names = [e["__name"] for e in cb.find_child(cb.get_room(tree, "a-01"), "entities")["__children"]]
        self.assertIn("refill", names)

    def test_restore_and_diff_against_backup(self):
        original = self.map.read_bytes()
        s.add_entity(self.rel, "a-00", "strawberry", 100, 100)
        d = s.map_diff(self.rel)
        self.assertIn("entity added: strawberry", d)
        self.assertIn("DRY RUN", s.map_restore(self.rel))
        out = s.map_restore(self.rel, dry_run=False)
        self.assertIn("Restored", out)
        self.assertEqual(self.map.read_bytes(), original)
        self.assertEqual(len(safety.list_backups(self.map, self.ws)), 2)   # the edited version was kept too


class DiffTests(unittest.TestCase):
    def test_reencoded_map_is_semantically_identical(self):
        tree = _map(_room("lvl_a"), _room("lvl_b", x=320))
        a = cb.serialize_map(tree)
        b = cb.serialize_map(json.loads(json.dumps(tree)))   # fresh string table, same meaning
        d = mapdiff.diff_bytes(a, b)
        self.assertTrue(d.identical)

    def test_changes_are_reported_by_kind(self):
        a = _map(_room("lvl_a"), _room("lvl_b", x=320))
        b = json.loads(json.dumps(a))
        rb = b["__children"][0]["__children"]
        rb[0]["name"] = "a"                                   # lvl_ prefix dropped: same room
        rows = rb[0]["__children"][0]["innerText"].split("\n")
        rows[5] = "1" * 5 + rows[5][5:]
        rb[0]["__children"][0]["innerText"] = "\n".join(rows)
        rb[0]["__children"][2]["__children"][0]["x"] = 40
        rb[1]["__children"][2]["__children"].append({"__name": "spring", "id": 7, "x": 80, "y": 160, "__children": []})
        rb[1]["music"] = "event:/music/lvl1/main"
        d = mapdiff.diff_maps(a, b)
        text = d.text()
        self.assertEqual(d.rooms_added + d.rooms_removed, [])
        self.assertIn("fg tiles: 5 cell(s) changed", text)
        self.assertIn("entity changed: player id=1: x: 24 -> 40", text)
        self.assertIn("entity added: spring id=7", text)
        self.assertIn("attr music", text)


FAKE_EDITOR = textwrap.dedent("""
    import sys, time, os, json
    sys.path.insert(0, {root!r})
    from loenn_mcp import celeste_bin as cb
    path = sys.argv[-1]
    time.sleep(1.0)
    m = cb.read_map(path)
    room = cb.get_rooms(m)[0]
    cb.find_child(room, "entities")["__children"].append({{"__name": "refill", "id": 99, "x": 100, "y": 80, "__children": []}})
    tmp = path + ".saving"
    open(tmp, "wb").write(cb.serialize_map(json.loads(json.dumps(m))))
    time.sleep(0.6)
    os.remove(path); os.rename(tmp, path)
    time.sleep(30)
""")


class FakeAdapter(editors.EditorAdapter):
    name = "fake"
    script: Path

    def find_executable(self):
        return Path(sys.executable)

    def launch_args(self, exe, map_path, room):
        return [str(exe), str(self.script), str(map_path)]

    def temp_save_path(self, map_path):
        return map_path.with_name(map_path.name + ".saving")


class EditorTests(Workspace):
    def test_watch_detects_a_completed_save_and_guards_writes(self):
        script = self.state / "fake_editor.py"
        script.write_text(FAKE_EDITOR.format(root=str(Path(__file__).resolve().parents[1])), encoding="utf-8")
        ad = FakeAdapter(self.ws)
        ad.script = script
        sess = ad.open(self.map, "a-00")
        self.assertIn("cannot preselect", sess.notes[0])
        out = s.add_entity(self.rel, "a-00", "strawberry", 100, 100)
        self.assertIn("Not written", out)
        self.assertIn("is open in fake", out)
        reason, ev = ad.wait(sess, timeout=20, interval=0.1)
        self.assertEqual(reason, "saved")
        self.assertIn("entity added: refill id=99", ev.diff_text)
        self.assertIn("killed", ad.close(sess, force=True))
        self.assertIn("refill", ad.diff_since_open(sess))
        self.assertIn("Added", s.add_entity(self.rel, "a-00", "strawberry", 100, 100))

    def test_open_rejects_unknown_room(self):
        ad = FakeAdapter(self.ws)
        ad.script = Path("unused.py")
        with self.assertRaises(editors.EditorError):
            ad.open(self.map, "nope")

    def test_persistence_keys_are_set_without_losing_others(self):
        conf = self.state / "persistence.conf"
        conf.write_text('{\n    cameraPositionX = 33142.75,\n    lastSelectedRoomName = "lvl_center-10",\n'
                        '    recentFiles = {\n        "C:\\\\a.bin"\n    },\n    windowWidth = 1280\n}\n', encoding="utf-8")
        loenn_mod.set_persistence_keys(conf, {"lastSelectedRoomName": "lvl_a-01", "cameraPositionX": 288,
                                              "cameraPositionScale": 1})
        text = conf.read_text(encoding="utf-8")
        self.assertIn('lastSelectedRoomName = "lvl_a-01",', text)
        self.assertIn("cameraPositionX = 288,", text)
        self.assertIn("cameraPositionScale = 1,", text)
        self.assertIn("windowWidth = 1280", text)
        self.assertEqual(loenn_mod.read_persistence_value(conf, "lastSelectedRoomName"), "lvl_a-01")

    def test_loenn_launch_args_use_isolated_storage_and_preselect_room(self):
        user = self.state / "UserLocal"
        (user / "Loenn" / "Plugins" / "X").mkdir(parents=True)
        (user / "Loenn" / "settings.conf").write_text("{\n}\n", encoding="utf-8")
        old = os.environ.get("LOCALAPPDATA")
        os.environ["LOCALAPPDATA"] = str(user)
        try:
            ad = editors.LoennAdapter(self.ws)
            args = ad.launch_args(Path("C:/x/Lönn.exe"), self.map, "a-01")
        finally:
            os.environ["LOCALAPPDATA"] = old or ""
        storage = safety.state_dir() / "loenn-storage"
        self.assertEqual(args[1:3], ["--storage-directory", str(storage)])
        self.assertEqual(args[-1], str(self.map))
        self.assertTrue((storage / "settings.conf").exists())
        self.assertTrue((storage / "Plugins" / "X").is_dir())
        self.assertEqual(loenn_mod.read_persistence_value(storage / "persistence.conf", "lastSelectedRoomName"), "lvl_a-01")

    @unittest.skipUnless(os.environ.get("LOENN_MCP_TEST_LOENN") == "1", "set LOENN_MCP_TEST_LOENN=1 to launch Lönn")
    def test_real_loenn_opens_the_map_on_the_room(self):
        out = s.editor_open(self.rel, room="a-01")
        self.assertIn("Opened", out)
        sess = list(editors.SESSIONS.values())[-1]
        conf = safety.state_dir() / "loenn-storage" / "persistence.conf"
        deadline = time.time() + 120
        while time.time() < deadline:
            last = loenn_mod.read_persistence_value(conf, "lastLoadedFilename")
            if last and Path(last).resolve() == self.map.resolve():
                break
            time.sleep(1)
        self.assertEqual(Path(loenn_mod.read_persistence_value(conf, "lastLoadedFilename")).resolve(), self.map.resolve())
        self.assertEqual(loenn_mod.read_persistence_value(conf, "lastSelectedRoomName"), "lvl_a-01")
        self.assertIn("Not written", s.add_entity(self.rel, "a-00", "strawberry", 100, 100))
        self.assertIn("killed", s.editor_close(sess.id, force=True))


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Path(self.tmp.name) / "Mods" / "M"
        g = self.ws / "Graphics" / "Atlases" / "Gameplay"
        for f in ("characters/m/Idle00.png", "characters/m/idle01.png", "objects/core/phase100.png",
                  "decals/m/rock.png"):
            (g / f).parent.mkdir(parents=True, exist_ok=True)
            (g / f).write_bytes(b"")
        (self.ws / "Graphics" / "Sprites.xml").write_text(textwrap.dedent("""
            <Sprites>
              <guy path="characters/m/" start="idle"><Loop id="idle" path="idle" delay="0.1"/><Anim id="run" path="run"/></guy>
              <core path="objects/core/"><Loop id="p1" path="phase1"/></core>
              <core path="objects/core/"><Loop id="p1" path="phase1"/></core>
              <nopath><Loop id="a" path="a"/></nopath>
            </Sprites>"""), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_sprites_xml_case_insensitive_frames_duplicates(self):
        idx = assets.build_index(self.ws, installed=False, vanilla=False, refresh=True)
        n, issues = assets.check_sprites_xml(self.ws / "Graphics" / "Sprites.xml", idx)
        problems = [(i.sprite, i.anim, i.problem.split(" ")[0]) for i in issues]
        self.assertIn(("guy", "run", "no"), problems)            # missing run frames
        self.assertNotIn(("guy", "idle", "no"), problems)        # Idle00.png matches 'idle' (case-insensitive)
        self.assertFalse(any(p[0] == "core" and p[1] == "p1" for p in problems))   # phase1 + 00
        self.assertTrue(any(p[0] == "core" and "defined" in i.problem for p, i in zip(problems, issues)))
        self.assertTrue(any(p[0] == "nopath" for p in problems))

    def test_map_assets(self):
        idx = assets.build_index(self.ws, installed=False, vanilla=False, refresh=True)
        tree = _map(_room("lvl_a"))
        room = cb.get_rooms(tree)[0]
        cb.find_child(room, "fgdecals")["__children"] += [
            {"__name": "decal", "texture": "m/rock.png", "x": 8, "y": 8, "__children": []},
            {"__name": "decal", "texture": "m/missing", "x": 8, "y": 8, "__children": []}]
        cb.find_child(room, "entities")["__children"].append({"__name": "Mod/Nope", "id": 5, "x": 0, "y": 0, "__children": []})
        rep = assets.check_map_assets(tree, idx, lambda n, k: n == "player")
        self.assertEqual(rep.missing_textures, ["lvl_a: fgdecal decals/m/missing"])
        self.assertEqual(rep.unknown_names, ["entity Mod/Nope (first in lvl_a)"])

    def test_vanilla_atlas_meta_is_readable(self):
        root = assets.game_root_for(Path(os.environ.get("LOENN_MCP_TEST_WORKSPACE", "C:/Users/Owner/Desktop/Celeste/celeste/Mods/CEL")))
        if root is None:
            self.skipTest("no Celeste install found")
        names = assets.read_atlas_meta(root / "Content" / "Graphics" / "Atlases" / "Gameplay.meta")
        self.assertGreater(len(names), 1000)
        self.assertIn("characters/player/sweat/climb07", names)


def _graybox_export(path: Path, shuffle: bool = False, sheet: bool = True) -> Path:
    W, H = 40, 23
    ids = {k: str(i) for i, k in enumerate(sf.GRAYBOX_ORDER)}
    order = list(range(len(sf.GRAYBOX_ORDER)))
    if shuffle:   # ids index the *exported* sheet; reorder it to prove pixel matching
        order = order[::-1]
        ids = {k: str(order.index(i)) for k, i in zip(sf.GRAYBOX_ORDER, range(len(order)))}
    t = lambda kind, x, y: {"id": ids[kind], "x": x, "y": y}  # noqa: E731
    ground = [t("solid", x, y) for x in range(W) for y in range(20, H)]
    game = [t("player", 4, 19), t("spikesUp", 12, 19), t("spikesUp", 13, 19), t("spring", 20, 19),
            t("jumpThru", 30, 14), t("jumpThru", 31, 14), t("dreamBlock", 16, 6), t("dreamBlock", 17, 6),
            t("dreamBlock", 16, 7), t("dreamBlock", 17, 7), t("refill", 26, 12)]
    doc = {"tileSize": 16, "mapWidth": W, "mapHeight": H, "layers": [
        {"name": "Blocks", "collider": True, "tiles": ground},
        {"name": "bg", "collider": False, "tiles": [t("bg", x, 15) for x in range(5, 10)]},
        {"name": "Gameplay", "collider": False, "tiles": game},
        {"name": "Deco", "collider": False, "tiles": [{"id": "99", "x": 1, "y": 1}]},
    ]}
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("map.json", json.dumps(doc))
        if sheet:
            from PIL import Image
            png, w, h = sf.graybox_png()
            import io
            src = Image.open(io.BytesIO(png)).convert("RGBA")
            out = Image.new("RGBA", (len(order) * 16, 16))
            for new_i, old_i in enumerate(order):
                out.paste(src.crop(((old_i % 4) * 16, (old_i // 4) * 16, (old_i % 4) * 16 + 16, (old_i // 4) * 16 + 16)),
                          (new_i * 16, 0))
            buf = io.BytesIO()
            out.save(buf, "PNG")
            z.writestr("spritesheet.png", buf.getvalue())
    return path


class SpriteFusionTests(Workspace):
    def check_conversion(self, conv):
        names = sorted((e["name"], e["x"], e["y"], e.get("width"), e.get("height")) for e in conv.entities)
        self.assertEqual(names, sorted([
            ("player", 36, 160, None, None), ("spikesUp", 96, 160, 16, None), ("spring", 164, 160, None, None),
            ("jumpThru", 240, 112, 16, None), ("dreamBlock", 128, 48, 16, 16), ("refill", 212, 100, None, None)]))
        self.assertEqual(conv.fg[20], "1" * 40)
        self.assertEqual(conv.fg[19], "0" * 40)
        self.assertEqual(conv.bg[15][5:10], "11111")
        self.assertEqual(conv.decoration, 1)

    def test_pixel_matching_survives_a_reordered_spritesheet(self):
        p = _graybox_export(self.state / "shuffled.zip", shuffle=True)
        self.check_conversion(sf.convert(sf.load_export(p), sf.load_legend(None)))

    def test_id_fallback_without_spritesheet(self):
        p = _graybox_export(self.state / "nosheet.zip", sheet=False)
        conv = sf.convert(sf.load_export(p), sf.load_legend(None))
        self.check_conversion(conv)
        self.assertTrue(any("matched by id" in n for n in conv.notes))

    def test_tileset_files(self):
        files = sf.make_tileset(self.state / "ts")
        self.assertEqual([f.name for f in files], ["celeste_graybox.png", "celeste_graybox.legend.json", "README.txt"])
        self.assertTrue(files[0].read_bytes().startswith(b"\x89PNG"))

    def test_import_new_room_into_a_copy_then_replace(self):
        p = _graybox_export(self.state / "map.zip", shuffle=True)
        before = self.map.read_bytes()
        out_rel = "Maps/TestMod/wip/sf.bin"
        dry = s.sf_import(str(p), self.rel, "sf-01", output_path=out_rel)
        self.assertIn("DRY RUN", dry)
        self.assertIn("new room lvl_sf-01 at (704, 0)", dry)
        self.assertFalse((self.ws / out_rel).exists())
        done = s.sf_import(str(p), self.rel, "sf-01", output_path=out_rel, dry_run=False)
        self.assertIn("[saved Maps/TestMod/wip/sf.bin", done)
        self.assertEqual(self.map.read_bytes(), before, "the source map is never touched")
        tree = cb.read_map(self.ws / out_rel)
        room = cb.get_room(tree, "sf-01")
        ents = {e["__name"] for e in cb.find_child(room, "entities")["__children"]}
        self.assertEqual(ents, {"player", "spikesUp", "spring", "jumpThru", "dreamBlock", "refill"})
        self.assertIn("Everything exists", s.check_assets(out_rel))

        again = s.sf_import(str(p), out_rel, "sf-01")
        self.assertIn("already exists", again)
        rep = s.sf_import(str(p), out_rel, "sf-01", replace=True, dry_run=False)
        self.assertIn("replaced tiles and 6 entities with 6", rep)
        self.assertEqual(len(cb.get_rooms(cb.read_map(self.ws / out_rel))), 3)

    def test_unknown_attribute_entity_is_refused(self):
        p = self.state / "attr.zip"
        doc = {"tileSize": 16, "mapWidth": 40, "mapHeight": 23, "layers": [
            {"name": "Blocks", "collider": True, "tiles": [{"id": "0", "x": x, "y": 22} for x in range(40)]},
            {"name": "Obj", "collider": False, "tiles": [
                {"id": "5", "x": 3, "y": 21, "attributes": {"entity": "player", "anchor": "feet"}},
                {"id": "5", "x": 8, "y": 21, "attributes": {"entity": "Mod/DoesNotExist"}}]}]}
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("map.json", json.dumps(doc))
        out = s.sf_import(str(p), self.rel, "x-01", output_path="Maps/TestMod/wip/x.bin")
        self.assertIn("Mod/DoesNotExist", out)
        self.assertIn("Not imported", out)


if __name__ == "__main__":
    unittest.main()
