"""Tests for typed map JSON (map_json): lossless round trips and the refusal rules.

Set LOENN_MCP_TEST_MAPS to a folder of real maps to also round-trip every one
of them through JSON text.
"""
import copy
import glob
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loenn_mcp import celeste_bin as cb  # noqa: E402
from loenn_mcp import map_json as mj  # noqa: E402
from test_celeste_bin import _handmade_map  # noqa: E402


def _via_text(doc):
    return json.loads(mj.dumps(doc))


def _level(doc):
    levels = next(c for c in doc['root']['children'] if c['name'] == 'levels')
    return levels['children'][0]


class Dump(unittest.TestCase):
    def setUp(self):
        self.raw = _handmade_map()
        self.doc = _via_text(mj.bin_to_json(self.raw, 'hand.bin'))

    def test_round_trip_is_byte_identical(self):
        data, notes = mj.json_to_bin(self.doc)
        self.assertEqual(data, self.raw)
        self.assertIn('byte-identical', notes[0])

    def test_value_types_are_kept(self):
        attrs = _level(self.doc)['attrs']
        self.assertEqual(attrs['x'], ['i16', -40])
        self.assertEqual(attrs['width'], ['i32', 320])
        self.assertEqual(attrs['music'], ['string', 'event:/music/lvl1/main'])  # plain string tag
        self.assertEqual(attrs['alpha'][0], 'f32')
        self.assertAlmostEqual(attrs['alpha'][1], 0.3, places=6)
        solids = _level(self.doc)['children'][0]
        self.assertEqual(solids['attrs']['innerText'], ['rle', '11111000'])
        self.assertEqual(self.doc['root']['attrs']['_package'], ['lookup', '_package'])

    def test_room_name_keeps_raw_prefix_and_duplicate_table_entry(self):
        name = _level(self.doc)['attrs']['name']
        self.assertEqual(name[:2], ['lookup', 'lvl_a'])
        self.assertEqual(name[2], {'key_index': 12})  # the file used the duplicate 'name' entry

    def test_header_package_is_separate_from_attributes(self):
        self.assertEqual(self.doc['package'], 'HeaderPackage')
        self.assertEqual(self.doc['header'], 'CELESTE MAP')


class Refusals(unittest.TestCase):
    def setUp(self):
        self.raw = _handmade_map()
        self.doc = _via_text(mj.bin_to_json(self.raw, 'hand.bin'))

    def test_value_that_does_not_fit_its_type(self):
        _level(self.doc)['attrs']['x'] = ['u8', 300]
        with self.assertRaisesRegex(mj.MapJsonError, r'u8 needs an integer in \[0, 255\]'):
            mj.json_to_bin(self.doc)

    def test_unknown_type_and_bad_shapes(self):
        _level(self.doc)['attrs']['x'] = ['int', 3]
        with self.assertRaisesRegex(mj.MapJsonError, "unknown type 'int'"):
            mj.json_to_bin(self.doc)
        with self.assertRaisesRegex(mj.MapJsonError, 'no .root.'):
            mj.json_to_bin({'lookup': []})

    def test_unedited_dump_that_would_not_reproduce_its_source(self):
        self.doc['source']['sha256'] = hashlib.sha256(b'something else').hexdigest()
        with self.assertRaisesRegex(mj.MapJsonError, 'does not round-trip byte-identical'):
            mj.json_to_bin(self.doc)

    def test_nothing_is_written_on_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'map.bin'
            target.write_bytes(self.raw)
            _level(self.doc)['attrs']['x'] = ['i16', 99999]
            with self.assertRaises(mj.MapJsonError):
                mj.write_json_as_map(self.doc, target)
            self.assertEqual(target.read_bytes(), self.raw)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ['map.bin'])


class Edits(unittest.TestCase):
    def setUp(self):
        self.raw = _handmade_map()
        self.doc = _via_text(mj.bin_to_json(self.raw, 'hand.bin'))

    def test_edited_dump_writes_and_keeps_string_table_order(self):
        _level(self.doc)['attrs']['name'] = ['lookup', 'lvl_renamed']
        _level(self.doc)['attrs']['x'] = ['i16', 64]
        data, notes = mj.json_to_bin(self.doc)
        self.assertIn('edited dump', notes[0])
        tree = cb.read_map_bytes(data)
        room = cb.get_rooms(tree)[0]
        self.assertEqual((room['name'], room['x']), ('lvl_renamed', 64))
        self.assertEqual(tree._bin['lookup'][:len(self.doc['lookup'])], self.doc['lookup'])

    def test_f32_input_is_rounded_to_what_the_file_holds(self):
        _level(self.doc)['attrs']['alpha'] = ['f32', 0.1]
        data, _ = mj.json_to_bin(self.doc)
        self.assertAlmostEqual(cb.get_rooms(cb.read_map_bytes(data))[0]['alpha'], 0.1, places=6)

    def test_atomic_write_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'map.bin'
            target.write_bytes(b'old')
            notes = mj.write_json_as_map(self.doc, target)
            self.assertEqual(target.read_bytes(), self.raw)
            self.assertEqual((Path(tmp) / 'map.bin.bak1').read_bytes(), b'old')
            self.assertTrue(any('wrote' in n for n in notes))

    def test_hand_written_document_without_lookup_or_types(self):
        doc = {'package': 'pkg', 'root': {'name': 'Map', 'children': [
            {'name': 'levels', 'children': [
                {'name': 'level', 'attrs': {'name': 'lvl_x', 'x': 0, 'y': -8, 'width': 320,
                                            'height': ['i32', 184], 'dark': True},
                 'children': [{'name': 'solids', 'attrs': {'innerText': '0' * 40 + '\n' + '1' * 40}}]}]}]}}
        data, notes = mj.json_to_bin(doc)
        self.assertEqual(notes, [])
        room = cb.get_rooms(cb.read_map_bytes(data))[0]
        self.assertEqual((room['name'], room['y'], room['height'], room['dark']), ('lvl_x', -8, 184, True))
        again = mj.bin_to_json(data)
        self.assertEqual(_level(again)['attrs']['height'], ['i32', 184])
        self.assertEqual(_level(again)['children'][0]['attrs']['innerText'][0], 'rle')


class TreesBuiltInMemory(unittest.TestCase):
    def test_untagged_values_get_writer_types(self):
        tree = cb.read_map_bytes(_handmade_map())
        room = cb.get_rooms(tree)[0]
        room['newFlag'] = False
        room['bigNumber'] = 70000
        room['__children'].append({'__name': 'entities', '__children': [
            {'__name': 'strawberry', 'id': 1, 'x': 8, 'y': 8, '__children': []}]})
        doc = mj.tree_to_json(tree)
        attrs = _level(doc)['attrs']
        self.assertEqual(attrs['newFlag'], ['bool', False])
        self.assertEqual(attrs['bigNumber'], ['i32', 70000])
        data, _ = mj.json_to_bin(copy.deepcopy(doc))
        self.assertEqual(data, cb.serialize_map(tree))


@unittest.skipUnless(os.environ.get('LOENN_MCP_TEST_MAPS'), 'set LOENN_MCP_TEST_MAPS to a maps folder')
class RealMaps(unittest.TestCase):
    def test_every_map_round_trips_through_json_text(self):
        paths = glob.glob(os.path.join(os.environ['LOENN_MCP_TEST_MAPS'], '**', '*.bin'), recursive=True)
        self.assertTrue(paths)
        for p in paths:
            with self.subTest(map=p):
                raw = open(p, 'rb').read()
                doc = _via_text(mj.bin_to_json(raw, os.path.basename(p)))
                self.assertEqual(mj.json_to_bin(doc)[0], raw)


if __name__ == '__main__':
    unittest.main()
