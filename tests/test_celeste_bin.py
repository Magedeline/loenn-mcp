"""Tests for the lossless .bin reader/writer.

Run:  python -m unittest discover -s tests
Set LOENN_MCP_TEST_MAPS to a folder of real maps (e.g. a mod's Maps/ or the
game's Content/Maps/) to also check that every one of them round-trips
byte-for-byte. Real maps are not shipped with the repo.
"""
import copy
import glob
import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from loenn_mcp import celeste_bin as cb  # noqa: E402


def _str(s: str) -> bytes:
    b = s.encode('utf-8')
    out, n = bytearray(), len(b)
    while n > 127:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out) + b


def _handmade_map() -> bytes:
    """A map written the way Celeste/Lönn do, with quirks a sorted rewrite would lose:
    an unsorted lookup table, a duplicate entry, a root _package attribute, a raw
    string, an RLE string and a float."""
    lut = ['Map', '_package', 'levels', 'level', 'name', 'lvl_a', 'solids', 'innerText',
           'x', 'width', 'music', 'event:/music/lvl1/main', 'name', 'alpha']
    out = bytearray(_str('CELESTE MAP') + _str('HeaderPackage'))
    out += struct.pack('<h', len(lut))
    for s in lut:
        out += _str(s)

    def el(name, attrs, kids):
        b = bytearray(struct.pack('<H', name)) + bytes([len(attrs)])
        for key, enc in attrs:
            b += struct.pack('<H', key) + enc
        b += struct.pack('<H', len(kids))
        for k in kids:
            b += k
        return bytes(b)

    rle = bytes([5, ord('1'), 3, ord('0')])
    solids = el(6, [(7, bytes([7]) + struct.pack('<h', len(rle)) + rle)], [])
    level = el(3, [(12, bytes([5]) + struct.pack('<h', 5)),          # name via the duplicate key entry
                   (8, bytes([2]) + struct.pack('<h', -40)),
                   (9, bytes([3]) + struct.pack('<i', 320)),
                   (10, bytes([6]) + _str('event:/music/lvl1/main')),  # raw string although in table
                   (13, bytes([4]) + struct.pack('<f', 0.3))], [solids])
    root = el(0, [(1, bytes([5]) + struct.pack('<h', 1))], [el(2, [], [level])])
    return bytes(out) + root


class RoundTrip(unittest.TestCase):
    def test_handmade_map_is_byte_identical(self):
        raw = _handmade_map()
        self.assertEqual(cb.serialize_map(cb.read_map_bytes(raw)), raw)

    def test_values_read_correctly(self):
        d = cb.read_map_bytes(_handmade_map())
        room = cb.get_room(d, 'a')
        self.assertEqual(room['x'], -40)
        self.assertEqual(room['width'], 320)
        self.assertAlmostEqual(room['alpha'], 0.3, places=6)
        self.assertEqual(cb.find_child(room, 'solids')['innerText'], '11111000')

    def test_root_package_attribute_is_not_confused_with_header(self):
        d = cb.read_map_bytes(_handmade_map())
        self.assertEqual(d['_package'], '_package')  # the attribute's own value
        out = cb.serialize_map(d)
        self.assertTrue(out.startswith(_str('CELESTE MAP') + _str('HeaderPackage')))

    def test_header_package_is_not_added_as_attribute(self):
        raw = _handmade_map()
        d = cb.read_map_bytes(raw)
        del d['_package']  # simulate a map whose root has no _package attribute
        d._bin['t'].pop('_package')
        again = cb.read_map_bytes(cb.serialize_map(d))
        self.assertEqual(again['_package'], 'HeaderPackage')  # mirrored for display only
        self.assertNotIn('_package', again._bin['t'])


class Edits(unittest.TestCase):
    def setUp(self):
        self.raw = _handmade_map()
        self.d = cb.read_map_bytes(self.raw)

    def test_edit_appends_to_string_table_only(self):
        room = cb.get_room(self.d, 'a')
        room['music'] = 'event:/new/song'
        room['x'] = 100000  # no longer fits int16: re-encoded as int32
        out = cb.read_map_bytes(cb.serialize_map(self.d))
        self.assertEqual(cb.get_room(out, 'a')['music'], 'event:/new/song')
        self.assertEqual(cb.get_room(out, 'a')['x'], 100000)
        old = self.d._bin['lookup']
        self.assertEqual(out._bin['lookup'][:len(old)], old)

    def test_new_elements_and_clones(self):
        levels = cb.find_child(self.d, 'levels')
        clone = copy.deepcopy(cb.get_room(self.d, 'a'))
        clone['name'] = 'lvl_b'
        levels['__children'].append(clone)
        levels['__children'].append({'__name': 'level', '__children': [], 'name': 'lvl_c', 'x': 0})
        out = cb.read_map_bytes(cb.serialize_map(self.d))
        self.assertEqual([r['name'] for r in cb.get_rooms(out)], ['lvl_a', 'lvl_b', 'lvl_c'])

    def test_elements_stay_plain_dicts_for_tools(self):
        room = cb.get_room(self.d, 'a')
        self.assertIsInstance(room, dict)
        self.assertNotIn('_bin', json.dumps(room))
        self.assertEqual(set(room) - {'__name', '__children'}, {'name', 'x', 'width', 'music', 'alpha'})


@unittest.skipUnless(os.environ.get('LOENN_MCP_TEST_MAPS'), 'set LOENN_MCP_TEST_MAPS to a maps folder')
class RealMaps(unittest.TestCase):
    def test_every_map_round_trips(self):
        paths = glob.glob(os.path.join(os.environ['LOENN_MCP_TEST_MAPS'], '**', '*.bin'), recursive=True)
        self.assertTrue(paths)
        for p in paths:
            with self.subTest(map=p):
                raw = open(p, 'rb').read()
                self.assertEqual(cb.serialize_map(cb.read_map_bytes(raw)), raw)


if __name__ == '__main__':
    unittest.main()
