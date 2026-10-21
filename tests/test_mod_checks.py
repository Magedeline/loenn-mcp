"""Tests for the mod-level checks: definition lookup by name, missing definitions,
plugin coverage and audio validation. Builds a throwaway mod workspace."""
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

# Keep timestamped backups and editor storage out of the real state folder.
os.environ.setdefault("LOENN_MCP_STATE_DIR", tempfile.mkdtemp(prefix="loenn-mcp-test-state-"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

try:
    import fastmcp  # noqa: F401
except ImportError:  # the checks under test do not need a running MCP server
    stub = types.ModuleType('fastmcp')

    class FastMCP:
        def __init__(self, *a, **k): pass
        def tool(self, *a, **k): return lambda f: f
        def run(self, *a, **k): pass

    stub.FastMCP = FastMCP
    sys.modules['fastmcp'] = stub

from loenn_mcp import celeste_bin as cb  # noqa: E402
from loenn_mcp import server as s  # noqa: E402


def _room(name, entities=(), triggers=(), **attrs):
    return {'__name': 'level', 'name': name, 'x': 0, 'y': 0, 'width': 320, 'height': 184, **attrs,
            '__children': [{'__name': 'entities', '__children': list(entities)},
                           {'__name': 'triggers', '__children': list(triggers)}]}


class ModChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ws = Path(self.tmp.name) / 'Mods' / 'MyMod'
        (ws / 'Loenn' / 'entities').mkdir(parents=True)
        (ws / 'Loenn' / 'entities' / 'cloudBoss.lua').write_text(
            'local boss = {}\nboss.name = "MyMod/CloudBoss"\nboss.placements = {name = "normal", data = {health = 3}}\nreturn boss\n')
        (ws / 'Loenn' / 'entities' / 'pair.lua').write_text(
            'return {{name = "MyMod/Left", placements = {name = "a"}}, {name = "MyMod/Right", placements = {name = "b"}}}\n')
        src = ws / 'Source'
        (src / 'Old').mkdir(parents=True)
        (src / 'Mod.csproj').write_text(
            '<Project><PropertyGroup><EnableDefaultCompileItems>false</EnableDefaultCompileItems></PropertyGroup>'
            '<ItemGroup><Compile Include="Boss.cs" /></ItemGroup></Project>')
        (src / 'Boss.cs').write_text('[CustomEntity("MyMod/CloudBoss", "MyMod/Spark=Load")] class A {}')
        (src / 'Old' / 'Gone.cs').write_text('[CustomEntity("MyMod/NotBuilt")] class B {}')
        (ws / 'GUIDs.txt').write_text('{00000000-0000-0000-0000-000000000000} event:/MyMod/music/main\n'
                                      '{00000000-0000-0000-0000-000000000001} event:/MyMod/ui/postcard_ch1_in\n')
        (ws / 'Maps').mkdir()
        data = {'__name': 'Map', '_package': 'test', '__children': [
            {'__name': 'levels', '__children': [
                _room('lvl_a', entities=[{'__name': 'MyMod/CloudBoss', '__children': [], 'id': 1, 'x': 8, 'y': 8},
                                         {'__name': 'OldHelper/Ghost', '__children': [], 'id': 2, 'x': 8, 'y': 8},
                                         {'__name': 'spikesUp', '__children': [], 'id': 3, 'x': 8, 'y': 8}],
                      music='event:/MyMod/music/main'),
                _room('lvl_b', triggers=[{'__name': 'MyMod/MusicTrigger', '__children': [], 'id': 4,
                                          'x': 0, 'y': 0, 'track': 'event:/MyMod/music/mian'}]),
            ]},
            {'__name': 'meta', 'PostcardSoundID': 'event:/MyMod/ui/postcard_ch1', '__children': []},
        ]}
        cb.write_map(ws / 'Maps' / 'test.bin', data, backup=False)
        self._old_ws = s.WORKSPACE
        s.WORKSPACE = ws.resolve()
        s._installed_names_cache.clear()

    def tearDown(self):
        s.WORKSPACE = self._old_ws
        s._installed_names_cache.clear()
        self.tmp.cleanup()

    def test_definition_lookup_by_entity_name(self):
        self.assertIn('MyMod/CloudBoss', s.get_entity_definition('MyMod/CloudBoss'))
        self.assertIn('MyMod/Right', s.list_entity_definitions('Right'))
        self.assertIn('No Lönn definition', s.get_entity_definition('MyMod/Nope'))

    def test_missing_definitions_ignores_vanilla(self):
        out = s.find_missing_definitions('Maps/test.bin')
        self.assertIn('OldHelper/Ghost', out)
        self.assertIn('MyMod/MusicTrigger', out)
        self.assertNotIn('MyMod/CloudBoss  ', out)
        self.assertNotIn('spikesUp', out)

    def test_plugin_coverage_uses_compile_list(self):
        out = s.audit_plugin_coverage('Source', 'MyMod/')
        self.assertIn('MyMod/Spark', out)        # coded, no plugin
        self.assertNotIn('MyMod/NotBuilt', out)  # not in the Include list
        self.assertIn('MyMod/Left', out)         # plugin, no code

    def test_audio_validation(self):
        out = s.validate_audio('Maps/test.bin')
        self.assertIn('event:/MyMod/music/mian', out)
        self.assertIn('closest existing: event:/MyMod/music/main', out)
        self.assertNotIn('✗ event:/MyMod/ui/postcard_ch1', out)  # _in suffix exists
        self.assertIn('Missing from the banks: 1', out)

    def test_map_preview_draws_tiles_and_entities(self):
        room = cb.get_room(cb.read_map(s.WORKSPACE / 'Maps' / 'test.bin'), 'a')
        room['__children'].append({'__name': 'solids', '__children': [], 'innerText': '0011\n1111'})
        svg = s._room_detail_svg(room, 100, 200)
        self.assertIn('M116 200h16v8h-16z', svg)   # merged run of two solid tiles
        self.assertIn('M100 208h32v8h-32z', svg)
        self.assertIn('<title>MyMod/CloudBoss</title>', svg)
        out = s.render_map_html('Maps/test.bin', output_file='preview.html')
        html = (s.WORKSPACE / 'preview.html').read_text(encoding='utf-8')
        self.assertIn('fitToWindow(); apply();\n})();', html)  # init runs after the minimap exists
        self.assertIn('Rooms shown', out)

    def test_game_tools_without_a_running_game(self):
        old = s.DEBUGRC
        s.DEBUGRC = 'http://127.0.0.1:9'  # nothing listens here
        try:
            self.assertEqual(s._map_sid('Maps/test.bin'), 'test')
            self.assertIn('Cannot reach the game', s.game_status())
            self.assertIn('Cannot reach the game', s.game_teleport('Maps/test.bin', 'lvl_a'))
            self.assertIn('Cannot reach the game', s.game_play_tas('Maps/test.bin', 'a', '10,R'))
        finally:
            s.DEBUGRC = old


if __name__ == '__main__':
    unittest.main()
