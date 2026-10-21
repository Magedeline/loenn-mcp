"""Tests for Lönn definition parsing (lua_defs), authoring JSON (authoring) and
the MCP tools built on them: merge_json_into_map, json_to_map, run_author_script."""
import json
import os
import shutil
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
except ImportError:  # the tools under test do not need a running MCP server
    stub = types.ModuleType('fastmcp')

    class FastMCP:
        def __init__(self, *a, **k): pass
        def tool(self, *a, **k): return lambda f: f
        def run(self, *a, **k): pass

    stub.FastMCP = FastMCP
    sys.modules['fastmcp'] = stub

from loenn_mcp import authoring as au  # noqa: E402
from loenn_mcp import celeste_bin as cb  # noqa: E402
from loenn_mcp import lua_defs as ld  # noqa: E402
from loenn_mcp import map_json as mj  # noqa: E402
from loenn_mcp import server as s  # noqa: E402

BERRY_LUA = '''
local berry = {}
berry.name = "MyMod/Berry"
berry.depth = -100
berry.nodeLimits = {0, 2}
berry.fieldInformation = { count = { fieldType = "integer" }, speed = {fieldType = "number"} }
--[[ a block comment with name = "Not/AThing" ]]
berry.placements = {
    {name = "normal", data = {count = 1, speed = 2, sound = [[event:/a]], winged = false, neg = -3}},
    {name = "fast", data = {count = 2, speed = 9.5, computed = someFunction()}},
}
return berry
'''

TRIGGER_LUA = '''
local t = {name = "MyMod/CollectTrigger", placements = {name = "collect", data = {}}}
return t
'''

FUNC_LUA = '''
local helper = require("helpers.fake_tiles")
local block = {}
block.name = "MyMod/Block"
function block.placements()
    return {name = "block", data = {tiletype = helper.getPlacementMaterial(), width = 8, height = 8}}
end
return block
'''

MULTI_LUA = 'return {{name = "MyMod/Left", placements = {name = "a", data = {side = "left"}}}, {name = "MyMod/Right"}}'

HELPER_LUA = ('local h = require("x")\nlocal handler = h.createEntityHandler("thing")\n'
              'handler.name = "MyMod/Named"\n-- old.name = "MyMod/Commented"\nreturn handler\n')

SPIKES_LUA = '''
local spikeHelper = require("helpers.spikes")
local spikeOptions = {directionNames = {up = "MyMod/SpikesUp", left = "MyMod/SpikesLeft"}}
return spikeHelper.createEntityHandlers(spikeOptions)
'''


class LuaDefinitions(unittest.TestCase):
    def test_literal_placements_and_field_types(self):
        (d,) = ld.parse_definitions(BERRY_LUA, 'entities', 'berry.lua', 'mod')
        self.assertEqual(d.name, 'MyMod/Berry')
        self.assertEqual(d.defaults(), {'count': 1, 'speed': 2, 'sound': 'event:/a', 'winged': False, 'neg': -3})
        self.assertEqual(d.defaults('fast'), {'count': 2, 'speed': 9.5})
        self.assertEqual(d.placement('fast').computed, ['computed'])
        self.assertEqual(d.field_types, {'count': 'integer', 'speed': 'number'})
        self.assertEqual(d.node_limits, (0, 2))
        with self.assertRaises(KeyError):
            d.defaults('nope')

    def test_table_constructor_and_multi_definition_files(self):
        (t,) = ld.parse_definitions(TRIGGER_LUA, 'triggers', 't.lua', 'mod')
        self.assertEqual((t.name, t.defaults()), ('MyMod/CollectTrigger', {}))
        left, right = ld.parse_definitions(MULTI_LUA, 'entities', 'm.lua', 'mod')
        self.assertEqual(left.defaults(), {'side': 'left'})
        self.assertEqual((right.name, right.placements), ('MyMod/Right', []))

    def test_function_placements_drop_computed_fields(self):
        (d,) = ld.parse_definitions(FUNC_LUA, 'entities', 'b.lua', 'mod')
        self.assertEqual(d.defaults(), {'width': 8, 'height': 8})
        self.assertEqual(d.placement().computed, ['tiletype'])

    def test_helper_built_definitions_keep_their_names(self):
        names = {d.name: d.parsed for d in ld.parse_definitions(HELPER_LUA, 'entities', 'h.lua', 'mod')}
        self.assertEqual(names, {'MyMod/Named': False})  # name known, defaults computed; comments ignored
        spikes = {d.name: d.defaults() for d in ld.parse_definitions(SPIKES_LUA, 'entities', 's.lua', 'mod')}
        self.assertEqual(spikes, {'MyMod/SpikesUp': {'width': 8, 'type': 'default'},
                                  'MyMod/SpikesLeft': {'height': 8, 'type': 'default'}})

    def test_shipped_vanilla_definitions(self):
        cat = ld.Catalog()
        for d in ld.load_vanilla():
            cat.add(d)
        self.assertEqual(cat.get('strawberry').defaults(),
                         {'winged': False, 'moon': False, 'checkpointID': -1, 'order': -1})
        self.assertEqual(cat.get('goldenBerry', 'entities').defaults(), {'winged': False, 'moon': False})
        self.assertIsNotNone(cat.get('eventTrigger', 'triggers'))
        self.assertIsNone(cat.get('strawberry', 'triggers'))


def _room(name, entities=(), triggers=(), solids=None, **attrs):
    kids = []
    if solids is not None:
        kids.append({'__name': 'solids', 'innerText': solids, '__children': []})
    kids += [{'__name': 'entities', '__children': list(entities)},
             {'__name': 'triggers', '__children': list(triggers)}]
    return {'__name': 'level', 'name': name, 'x': 0, 'y': 0, 'width': 320, 'height': 184, **attrs,
            '__children': kids}


def _make_mod(root: Path) -> Path:
    ws = root / 'Mods' / 'MyMod'
    (ws / 'Loenn' / 'entities').mkdir(parents=True)
    (ws / 'Loenn' / 'triggers').mkdir(parents=True)
    (ws / 'Loenn' / 'entities' / 'berry.lua').write_text(BERRY_LUA)
    (ws / 'Loenn' / 'triggers' / 'collect.lua').write_text(TRIGGER_LUA)
    # a room with a solid floor, a solid 3x3 block at tiles 10-12 x 5-7 enclosing tile (11,6)
    rows = ['0' * 40 for _ in range(23)]
    for ty in range(5, 8):
        rows[ty] = '0' * 10 + '111' + '0' * 27
    rows[22] = '1' * 40
    rows_box = ['0' * 40 for _ in range(23)]
    # a sealed pocket: ring of solid tiles around tile (21,11)
    for ty in range(10, 13):
        rows_box[ty] = '0' * 20 + ('101' if ty == 11 else '111') + '0' * 17
    rows_box[22] = '1' * 40
    player = {'__name': 'player', '__children': [], 'id': 7, 'x': 24, 'y': 168}
    data = {'__name': 'Map', '_package': 'test', '__children': [
        {'__name': 'levels', '__children': [
            _room('a-00', entities=[player, {'__name': 'strawberry', '__children': [], 'id': 40, 'x': 50, 'y': 50}],
                  solids='\n'.join(rows)),
            _room('a-01', entities=[dict(player, id=41)], solids='\n'.join(rows_box), x=320),
            {'__name': 'level', 'name': 'a-02', 'x': 640, 'y': 0, 'width': 320, 'height': 184,
             '__children': [{'__name': 'solids', 'innerText': '\n'.join(rows), '__children': []}]},
        ]}]}
    (ws / 'Maps').mkdir()
    cb.write_map(ws / 'Maps' / 'test.bin', data, backup=False)
    return ws


class Authoring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _make_mod(Path(self.tmp.name))
        self.map = self.ws / 'Maps' / 'test.bin'
        self.raw = self.map.read_bytes()
        self.cat = ld.Catalog.load(self.ws)

    def tearDown(self):
        self.tmp.cleanup()

    def merge(self, doc, **kw):
        return au.merge_bytes(self.raw, doc, self.cat, **kw)

    def test_resolve_fills_defaults_and_coerces_types(self):
        plan = au.resolve({'room': 'a-00', 'entities': [
            {'name': 'MyMod/Berry', 'x': 100, 'y': 40, 'count': 4.0, 'speed': 3},
            {'name': 'strawberry', 'x': 1, 'y': 2, 'data': {'winged': True}},
            {'name': 'MyMod/Berry', 'x': 1, 'y': 2, 'placement': 'fast', '# note': 'ignored'}]}, self.cat)
        a, b, c = plan.items()
        self.assertEqual(a.attrs, {'count': 4, 'speed': 3.0, 'sound': 'event:/a', 'winged': False, 'neg': -3})
        self.assertIsInstance(a.attrs['count'], int)
        self.assertIsInstance(a.attrs['speed'], float)
        self.assertEqual(b.attrs, {'winged': True, 'moon': False, 'checkpointID': -1, 'order': -1})
        self.assertEqual((b.origin, a.origin), ('vanilla', 'mod'))
        self.assertEqual(c.attrs['speed'], 9.5)
        self.assertTrue(any('computed' in w for w in c.warnings))
        self.assertFalse(plan.unknown)

    def test_unknown_and_misfiled_names_are_flagged(self):
        plan = au.resolve({'room': 'a-00', 'entities': [{'name': 'MyMod/Nope', 'x': 1, 'y': 1},
                                                        {'name': 'MyMod/CollectTrigger', 'x': 1, 'y': 1}]},
                          self.cat)
        self.assertEqual([it.name for it in plan.unknown], ['MyMod/Nope', 'MyMod/CollectTrigger'])
        self.assertIn('defined as a trigger', plan.unknown[1].warnings[0])
        with self.assertRaisesRegex(au.AuthoringError, 'unknown names'):
            self.merge({'room': 'a-00', 'entities': [{'name': 'MyMod/Nope', 'x': 1, 'y': 1}]})
        new, res = self.merge({'room': 'a-00', 'entities': [{'name': 'MyMod/Nope', 'x': 100, 'y': 100}]},
                              allow_unknown=True)
        self.assertEqual(res.changes[0].element['__name'], 'MyMod/Nope')

    def test_bad_input_is_rejected_before_anything_changes(self):
        cases = [
            ({'room': 'zz', 'entities': [{'name': 'strawberry', 'x': 1, 'y': 1}]}, 'does not exist'),
            ({'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 1}]}, 'needs x and y'),
            ({'room': 'a-00', 'attrs': {'dark': True}}, 'only accepted when creating'),
            ({'room': 'a-00', 'x': 0, 'y': 0, 'width': 8, 'height': 8}, 'already exists'),
            ({'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 1, 'y': 1, 'id': 41}]}, 'id 41 is already used'),
            ({'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 1, 'y': 1, 'tags': [1]}]}, 'string, number or boolean'),
            ({'room': 'a-00', 'entities': [{'name': 'MyMod/Berry', 'x': 1, 'y': 1, 'placement': 'x'}]}, "no placement 'x'"),
            ({'room': 'n-01', 'x': 0, 'y': 0, 'width': 320, 'height': 184}, 'overlaps existing room'),
            ({'rooms': 'a-00'}, "'rooms' must be a list"),
        ]
        for doc, msg in cases:
            with self.subTest(msg=msg), self.assertRaisesRegex(au.AuthoringError, msg):
                self.merge(doc)
        self.assertEqual(self.map.read_bytes(), self.raw)

    def test_ids_are_unique_across_the_map_and_never_reused(self):
        doc = {'rooms': [
            {'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 100, 'y': 100},
                                          {'name': 'strawberry', 'x': 120, 'y': 100, 'id': 500}],
             'triggers': [{'name': 'MyMod/CollectTrigger', 'x': 0, 'y': 0}]},
            {'room': 'lvl_a-01', 'entities': [{'name': 'strawberry', 'x': 100, 'y': 100}]}]}  # prefix-insensitive
        new, res = self.merge(doc)
        ids = [c.element['id'] for c in res.changes]
        self.assertEqual(ids, [42, 500, 501, 502])  # above the map's highest id (41), explicit 500 honoured
        tree = cb.read_map_bytes(new)
        all_ids = sorted(au.map_ids(tree))
        self.assertEqual(all_ids, [7, 40, 41, 42, 500, 501, 502])
        trig = cb.find_child(cb.get_room(tree, 'a-00'), 'triggers')['__children'][0]
        self.assertEqual((trig['width'], trig['height']), (16, 16))  # Lönn's trigger size default

    def test_merge_only_adds(self):
        doc = {'rooms': [{'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 100, 'y': 100,
                                                        'nodes': [[110, 90]]}]},
                         {'room': 'a-02', 'entities': [{'name': 'strawberry', 'x': 160, 'y': 100}]},
                         {'room': 'b-00', 'x': 0, 'y': 400, 'width': 320, 'height': 184,
                          'solids': '1111\n1', 'attrs': {'dark': True},
                          'entities': [{'name': 'player', 'x': 24, 'y': 160}]}]}
        new, res = self.merge(doc)
        old_doc, new_doc = mj.bin_to_json(self.raw), mj.bin_to_json(new)
        added = au.additive_only(old_doc['root'], new_doc['root'])
        self.assertEqual([a[1]['name'] for a in added], ['strawberry', 'entities', 'level'])
        self.assertEqual(new_doc['lookup'][:len(old_doc['lookup'])], old_doc['lookup'])
        tree = cb.read_map_bytes(new)
        room = cb.get_room(tree, 'b-00')
        self.assertEqual((room['dark'], room['width']), (True, 320))
        self.assertEqual(cb.find_child(room, 'solids')['innerText'].split('\n')[1], '1' + '0' * 39)
        berry = cb.find_child(cb.get_room(tree, 'a-00'), 'entities')['__children'][-1]
        self.assertEqual(berry['__children'], [{'__name': 'node', 'x': 110, 'y': 90, '__children': []}])

    def test_additive_check_catches_changes(self):
        old = mj.bin_to_json(self.raw)['root']
        tree = cb.read_map_bytes(self.raw)
        cb.get_room(tree, 'a-00')['x'] = 8
        with self.assertRaisesRegex(au.AuthoringError, 'existing data changed'):
            au.additive_only(old, mj.tree_to_json(tree)['root'])
        tree = cb.read_map_bytes(self.raw)
        cb.find_child(cb.get_room(tree, 'a-00'), 'entities')['__children'].pop()
        with self.assertRaisesRegex(au.AuthoringError, 'removed'):
            au.additive_only(old, mj.tree_to_json(tree)['root'])

    def test_placement_checks(self):
        doc = {'rooms': [
            {'room': 'a-00', 'entities': [
                {'name': 'strawberry', 'x': 92, 'y': 52},     # inside the 3x3 block (tile 11,6)
                {'name': 'strawberry', 'x': 400, 'y': 20},    # outside the room
                {'name': 'strawberry', 'x': 160, 'y': 100}],  # fine
             'triggers': [{'name': 'MyMod/CollectTrigger', 'x': 300, 'y': 0, 'width': 40, 'height': 16}]},
            {'room': 'a-01', 'entities': [{'name': 'strawberry', 'x': 172, 'y': 92}]}]}  # sealed pocket
        _, res = self.merge(doc)
        checks = [c.checks for c in res.changes]
        self.assertIn('sits inside a solid tile', checks[0])
        self.assertIn('outside the room', checks[1][0])
        self.assertEqual(checks[2], [])
        self.assertIn('leaves the room', checks[3][0])
        self.assertTrue(any('cut off' in c for c in checks[4]), checks[4])
        self.assertEqual(len(res.problems), 5)  # the pocket berry also mostly overlaps solid tiles
        text = au.describe(res)
        self.assertIn('! entity strawberry', text)
        self.assertIn('+ trigger' if not checks[3] else '! trigger', text)

    def test_reachability_uses_transitions_and_hazards(self):
        floor = '\n'.join(['0' * 40] * 22 + ['1' * 40])
        room = _room('r', solids=floor, x=320, entities=[
            {'__name': 'player', '__children': [], 'id': 1, 'x': 24, 'y': 176},
            {'__name': 'invisibleBarrier', '__children': [], 'id': 2, 'x': 160, 'y': 0, 'width': 8, 'height': 176}])
        left = _room('left', x=0)  # the only neighbour: touches r's left edge
        tree = {'__name': 'Map', '__children': [{'__name': 'levels', '__children': [left, room]}]}
        reach = au._ReachCache(tree)
        berry = {'__name': 'strawberry', 'x': 240, 'y': 120, '__children': []}
        self.assertIn('cut off', au.check_placement(room, berry, 'entities', reach)[0])
        # without map context every edge is an entrance, so the right side counts as reachable
        self.assertEqual(au.check_placement(room, berry, 'entities'), [])
        near = dict(berry, x=100)
        self.assertEqual(au.check_placement(room, near, 'entities', reach), [])
        # a wall of spinners seals the right side just like the barrier did
        ents = room['__children'][1]['__children']
        ents.pop()
        self.assertEqual(au.check_placement(room, berry, 'entities', au._ReachCache(tree)), [])
        ents += [{'__name': 'spinner', '__children': [], 'id': 10 + i, 'x': 164, 'y': 4 + 8 * i}
                 for i in range(22)]
        self.assertIn('cut off', au.check_placement(room, berry, 'entities', au._ReachCache(tree))[0])

    def test_room_validators_report_issues_the_merge_introduces(self):
        _, res = self.merge({'room': 'c-00', 'x': 0, 'y': 800, 'width': 320, 'height': 184,
                             'entities': [{'name': 'strawberry', 'x': 160, 'y': 100}]})
        self.assertIn("No 'player' spawn entity found", res.validator_issues['c-00'])  # new room: all issues count
        _, res = self.merge({'room': 'a-02', 'entities': [{'name': 'strawberry', 'x': 160, 'y': 100}]})
        self.assertEqual(res.validator_issues, {})  # a-02 already had no spawn: not newly introduced


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = _make_mod(Path(self.tmp.name))
        self.raw = (self.ws / 'Maps' / 'test.bin').read_bytes()
        self._old_ws = s.WORKSPACE
        s.WORKSPACE = self.ws.resolve()
        s._catalog_cache.clear()

    def tearDown(self):
        s.WORKSPACE = self._old_ws
        s._catalog_cache.clear()
        self.tmp.cleanup()

    def test_map_to_json_and_back(self):
        out = s.map_to_json('Maps/test.bin')
        self.assertIn('converts back byte-identical', out)
        dump = self.ws / 'Export' / 'test.json'
        self.assertIn('exists', s.json_to_map('Export/test.json', 'Maps/test.bin'))
        res = s.json_to_map('Export/test.json', 'Maps/copy.bin')
        self.assertIn('byte-identical', res)
        self.assertEqual((self.ws / 'Maps' / 'copy.bin').read_bytes(), self.raw)
        doc = json.loads(dump.read_text(encoding='utf-8'))
        doc['source']['sha256'] = '0' * 64
        dump.write_text(json.dumps(doc), encoding='utf-8')
        self.assertIn('Not written', s.json_to_map('Export/test.json', 'Maps/bad.bin'))
        self.assertFalse((self.ws / 'Maps' / 'bad.bin').exists())

    def test_merge_tool_dry_run_then_write(self):
        plan = json.dumps({'room': 'a-00', 'entities': [{'name': 'MyMod/Berry', 'x': 160, 'y': 100}]})
        out = s.merge_json_into_map('Maps/test.bin', plan)
        self.assertIn('DRY RUN', out)
        self.assertIn('MyMod/Berry id=42', out)
        self.assertIn('Planned additions highlighted: 1', out)
        self.assertEqual((self.ws / 'Maps' / 'test.bin').read_bytes(), self.raw)
        html = (self.ws / 'Temp' / 'map_preview_test_merge.html').read_text(encoding='utf-8')
        self.assertIn('class="ov"', html)
        self.assertIn('NEW entity MyMod/Berry id=42', html)

        out = s.merge_json_into_map('Maps/test.bin', plan, dry_run=False, preview=False)
        self.assertIn('Written', out)
        self.assertIn('[saved Maps/test.bin', out)
        backups = s.safety.list_backups(self.ws / 'Maps' / 'test.bin', s.WORKSPACE)
        self.assertEqual(backups[0].read_bytes(), self.raw)  # timestamped copy of the original
        again = s.merge_json_into_map('Maps/test.bin', plan, preview=False)
        self.assertIn('id=43', again)  # 42 is taken now

    def test_merge_tool_refuses_problems_and_unknowns(self):
        bad = json.dumps({'room': 'a-00', 'entities': [{'name': 'strawberry', 'x': 92, 'y': 52}]})
        out = s.merge_json_into_map('Maps/test.bin', bad, dry_run=False, preview=False)
        self.assertIn('Not written: 1 placement problem', out)
        self.assertEqual((self.ws / 'Maps' / 'test.bin').read_bytes(), self.raw)
        unk = json.dumps({'room': 'a-00', 'entities': [{'name': 'Nope/Nope', 'x': 160, 'y': 100}]})
        self.assertIn('unknown names', s.merge_json_into_map('Maps/test.bin', unk, dry_run=False))

    def test_check_authoring_json(self):
        out = s.check_authoring_json(json.dumps({'room': 'a-00', 'entities': [
            {'name': 'strawberry', 'x': 1, 'y': 1}, {'name': 'Nope/X', 'x': 1, 'y': 1}]}))
        self.assertIn('strawberry [vanilla]', out)
        self.assertIn('defaults: winged, moon, checkpointID, order', out)
        self.assertIn('1 unknown name(s): Nope/X', out)

    def test_render_accepts_typed_json(self):
        s.map_to_json('Maps/test.bin')
        out = s.render_map_html('Export/test.json', prefix='a-0', output_file='p.html')
        self.assertIn('Rooms shown: 3', out)  # prefix matches names without 'lvl_'

    @unittest.skipUnless(shutil.which('node'), 'Node.js not installed')
    def test_run_author_script(self):
        lib = json.dumps(str(Path(__file__).resolve().parents[1] / 'js'))
        (self.ws / 'plan.js').write_text(
            f"const {{Plan, Entity}} = require({lib});\n"
            "const p = new Plan();\np.room('a-00').add(Entity('MyMod/Berry', {x: 160, y: 100}));\n"
            "console.log('built');\nmodule.exports = p;\n")
        out = s.run_author_script('plan.js', map_path='Maps/test.bin')
        self.assertIn('Console:\n  built', out)
        self.assertIn('MyMod/Berry [mod]', out)
        self.assertIn('DRY RUN', out)
        self.assertTrue((self.ws / 'Export' / 'plan.authoring.json').exists())
        self.assertEqual((self.ws / 'Maps' / 'test.bin').read_bytes(), self.raw)
        (self.ws / 'broken.js').write_text('throw new Error("boom")')
        self.assertIn('boom', s.run_author_script('broken.js'))


if __name__ == '__main__':
    unittest.main()
