"""Regenerate loenn_mcp/data/vanilla_defs.json from a Lönn install.

    python tools/gen_vanilla_defs.py "D:/Celeste/Loenn-v1.0.10"

Reads Lönn's own entity/trigger plugins (Lönn.exe is a zip) and stores each
definition's name, placements and default data. Lönn is MIT licensed
(Copyright (c) 2019-2025 Cruor and Vexatos); see THIRD_PARTY_NOTICES.md.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from loenn_mcp import lua_defs  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    loenn = Path(sys.argv[1])
    defs = lua_defs.vanilla_from_loenn(loenn)
    if not defs:
        sys.exit(f"No Lönn plugins found in {loenn}")
    version = ""
    m = re.search(r"Loenn-v([\d.]+)", str(loenn))
    if m:
        version = m.group(1)
    out = Path(__file__).resolve().parents[1] / "loenn_mcp" / "data" / "vanilla_defs.json"
    out.parent.mkdir(exist_ok=True)
    doc = {
        "generated_from": f"Lönn {version}".strip(),
        "license": "MIT, Copyright (c) 2019-2025 Cruor and Vexatos (CelestialCartographers/Loenn)",
        "definitions": sorted((d.to_json() for d in defs), key=lambda d: (d["kind"], d["name"])),
    }
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(defs)} definitions -> {out}")


if __name__ == "__main__":
    main()
