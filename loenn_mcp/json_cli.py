"""
loenn-json: the JSON round-trip and authoring tools from the command line.

    loenn-json map2json  Maps/My/1-A.bin [-o out.json]
    loenn-json json2map  out.json Maps/My/1-A.bin [--overwrite]
    loenn-json merge     Maps/My/1-A.bin plan.json [--write] [--allow-unknown] [--allow-problems]
    loenn-json author    script.js [-o plan.json] [--map Maps/My/1-A.bin [--write]]

`merge` and `author --map` are dry runs unless --write is given. Definitions
are read from the mod folder given by --mod (default: the current directory).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from . import authoring, celeste_bin as cb, map_json, lua_defs
except ImportError:  # run directly from source
    import authoring
    import celeste_bin as cb
    import map_json
    import lua_defs


def _merge(map_path: Path, doc, mod: Path, write: bool, allow_unknown: bool, allow_problems: bool) -> int:
    original = map_path.read_bytes()
    new_bytes, res = authoring.merge_bytes(original, doc, lua_defs.Catalog.load(mod), allow_unknown=allow_unknown)
    print(authoring.describe(res))
    if not write:
        print("\nDry run: nothing written (add --write).")
        return 0
    if res.problems and not allow_problems:
        print(f"\nNot written: {len(res.problems)} placement problem(s) (add --allow-problems to write anyway).")
        return 1
    cb.write_map_bytes(map_path, new_bytes)
    print(f"\nWritten {map_path} ({len(original)} -> {len(new_bytes)} bytes); previous kept as .bak1")
    return 0


def run_script(script: Path) -> dict:
    node = os.environ.get("LOENN_MCP_NODE") or shutil.which("node")
    if not node:
        raise SystemExit("Node.js not found (install it, or set LOENN_MCP_NODE)")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "plan.json"
        proc = subprocess.run([node, str(authoring.js_cli_path()), str(script), "--out", str(out)], cwd=str(script.parent))
        if proc.returncode != 0:
            raise SystemExit(proc.returncode)
        return json.loads(out.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="loenn-json", description=__doc__.split("\n\n")[0])
    ap.add_argument("--mod", type=Path, default=Path("."), help="mod folder with Loenn/ plugins (default: .)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("map2json", help=".bin -> typed JSON")
    p.add_argument("map", type=Path)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--indent", type=int, default=1)

    p = sub.add_parser("json2map", help="typed JSON -> .bin")
    p.add_argument("json", type=Path)
    p.add_argument("map", type=Path)
    p.add_argument("--overwrite", action="store_true")

    p = sub.add_parser("merge", help="merge authoring JSON into a map (dry run by default)")
    p.add_argument("map", type=Path)
    p.add_argument("plan", type=Path)
    p.add_argument("--write", action="store_true")
    p.add_argument("--allow-unknown", action="store_true")
    p.add_argument("--allow-problems", action="store_true")

    p = sub.add_parser("author", help="run a JS authoring script")
    p.add_argument("script", type=Path)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--map", type=Path)
    p.add_argument("--write", action="store_true")
    p.add_argument("--allow-unknown", action="store_true")
    p.add_argument("--allow-problems", action="store_true")

    a = ap.parse_args(argv)
    try:
        if a.cmd == "map2json":
            doc = map_json.map_to_json(a.map)
            out = a.out or a.map.with_suffix(".json")
            out.write_text(map_json.dumps(doc, indent=a.indent or None), encoding="utf-8")
            print(f"{a.map} -> {out} (verified byte-identical round trip)")
        elif a.cmd == "json2map":
            if a.map.exists() and not a.overwrite:
                print(f"{a.map} exists; add --overwrite (a .bak copy is kept)", file=sys.stderr)
                return 1
            notes = map_json.write_json_as_map(json.loads(a.json.read_text(encoding="utf-8")), a.map)
            print("\n".join(notes))
        elif a.cmd == "merge":
            doc = json.loads(a.plan.read_text(encoding="utf-8"))
            return _merge(a.map, doc, a.mod, a.write, a.allow_unknown, a.allow_problems)
        elif a.cmd == "author":
            doc = run_script(a.script.resolve())
            text = json.dumps(doc, indent=1, ensure_ascii=False)
            if a.out:
                a.out.write_text(text, encoding="utf-8")
                print(f"authoring JSON -> {a.out}")
            elif not a.map:
                print(text)
            if a.map:
                return _merge(a.map, doc, a.mod, a.write, a.allow_unknown, a.allow_problems)
    except (map_json.MapJsonError, authoring.AuthoringError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
