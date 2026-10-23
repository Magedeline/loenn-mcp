"""Build the loenn-mcp MCPB bundle (uv runtime).

    python tools/build_mcpb.py                    # dist/loenn-mcp-<version>.mcpb
    python tools/build_mcpb.py --version 7.2.0 --out dist/loenn-mcp-7.2.0.mcpb

The bundle is a zip: mcpb/manifest.json and mcpb/pyproject.toml (version set
to the package version), mcpb/src/server.py as the entry point, the loenn_mcp
package, the js/ authoring helper, the C# PCG core sources (no bin/obj, no
tests), README, LICENSE and THIRD_PARTY_NOTICES. A host with uv installs the
dependencies from pyproject.toml; no PyPI release is involved.

Prints the bundle path and its SHA-256 (server.json needs it as fileSha256).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mcpb"
TREES = ["loenn_mcp", "js", "csharp/CelPcg.Core", "csharp/CelPcg.Cli"]
FILES = ["csharp/CelPcg.slnx", "LICENSE", "README.md", "THIRD_PARTY_NOTICES.md"]
SKIP_DIRS = {"__pycache__", "node_modules", ".venv"}
CSHARP_SKIP = {"bin", "obj"}          # build output; js/bin is a real folder and stays


def package_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not m:
        sys.exit("pyproject.toml has no version")
    return m.group(1)


def manifest(version: str) -> str:
    doc = json.loads((SRC / "manifest.json").read_text(encoding="utf-8"))
    doc["version"] = version
    return json.dumps(doc, indent=2, ensure_ascii=False) + "\n"


def bundle_pyproject(version: str) -> str:
    text = (SRC / "pyproject.toml").read_text(encoding="utf-8")
    return re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', text, count=1, flags=re.M)


def tree_files(top: Path):
    skip = SKIP_DIRS | (CSHARP_SKIP if top.relative_to(ROOT).parts[0] == "csharp" else set())
    for p in sorted(top.rglob("*")):
        if p.is_dir():
            continue
        rel = p.relative_to(ROOT)
        if any(part in skip for part in rel.parts) or p.suffix == ".pyc":
            continue
        yield p, rel.as_posix()


def build(version: str, out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", manifest(version))
        z.writestr("pyproject.toml", bundle_pyproject(version))
        z.write(SRC / "src" / "server.py", "src/server.py")
        for top in TREES:
            for path, arc in tree_files(ROOT / top):
                z.write(path, arc)
        for f in FILES:
            z.write(ROOT / f, f)
    return hashlib.sha256(out.read_bytes()).hexdigest()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--version", default=None, help="bundle version; default: the package version in pyproject.toml")
    ap.add_argument("--out", default=None, help="output path; default dist/loenn-mcp-<version>.mcpb")
    args = ap.parse_args()
    version = args.version or package_version()
    out = Path(args.out) if args.out else ROOT / "dist" / f"loenn-mcp-{version}.mcpb"
    digest = build(version, out)
    print(f"{out} {out.stat().st_size} bytes")
    print(f"sha256 {digest}")


if __name__ == "__main__":
    main()
