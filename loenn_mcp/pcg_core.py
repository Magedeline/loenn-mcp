"""
Python side of the shared PCG core (csharp/CelPcg.Core, a netstandard2.0 library).

The MCP does not reimplement generation: it calls the `celpcg` command-line
host (csharp/CelPcg.Cli), which uses the same library the CEL mod and Summer
Engine can reference. Output is a celpcg-room document; this module turns it
into the gray-box Conversion that sf_import / summer_export_room already write
safely (placement checks, timestamped backups, dry runs).

The CLI is built on first use (`dotnet build -c Release`) and rebuilt when a
C# source is newer than the built DLL. Set CELPCG_CLI to use another build.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from . import sprite_fusion as sf
except ImportError:  # run directly from source
    import sprite_fusion as sf


class PcgError(RuntimeError):
    """celpcg is missing, failed to build, or produced no valid room."""


ROOT = Path(__file__).resolve().parents[1] / "csharp"
CLI_PROJECT = ROOT / "CelPcg.Cli" / "CelPcg.Cli.csproj"
CLI_DLL = ROOT / "CelPcg.Cli" / "bin" / "Release" / "net8.0" / "celpcg.dll"


def fnv1a64(text: str) -> int:
    """Same as CelPcg.Seed.Fnv1a64, so default seeds match what the core derives."""
    h = 14695981039346656037
    for b in text.encode("utf-8"):
        h ^= b
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h


def default_seed(map_id: str, room: str) -> int:
    return fnv1a64(f"{map_id}|{room}")


def _dotnet() -> str:
    exe = shutil.which("dotnet")
    if not exe:
        raise PcgError("dotnet not found; install the .NET 8 SDK (or later) to build celpcg")
    return exe


def _newest_source() -> float:
    newest = 0.0
    for d in ("CelPcg.Core", "CelPcg.Cli"):
        for p in (ROOT / d).rglob("*"):
            if p.suffix in (".cs", ".csproj") and "obj" not in p.parts and "bin" not in p.parts:
                newest = max(newest, p.stat().st_mtime)
    return newest


def cli_command() -> list[str]:
    env = os.environ.get("CELPCG_CLI")
    if env:
        p = Path(env)
        return [_dotnet(), str(p)] if p.suffix == ".dll" else [str(p)]
    if not CLI_DLL.exists() or CLI_DLL.stat().st_mtime < _newest_source():
        proc = subprocess.run([_dotnet(), "build", str(CLI_PROJECT), "-c", "Release", "-nologo", "-v", "q"],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        if proc.returncode != 0 or not CLI_DLL.exists():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-15:])
            raise PcgError(f"building celpcg failed:\n{tail}")
    return [_dotnet(), str(CLI_DLL)]


def generate(request: dict, timeout: int = 120) -> dict:
    """Run `celpcg generate` with a request dict; returns the celpcg-room document."""
    cmd = cli_command()
    with tempfile.TemporaryDirectory(prefix="celpcg-") as tmp:
        req = Path(tmp) / "request.json"
        out = Path(tmp) / "room.json"
        req.write_text(json.dumps(request), encoding="utf-8")
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.run(cmd + ["generate", "--request", str(req), "--out", str(out)], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=flags)
        if proc.returncode != 0 or not out.exists():
            raise PcgError((proc.stderr or proc.stdout).strip() or f"celpcg exited {proc.returncode}")
        return json.loads(out.read_text(encoding="utf-8"))


def to_conversion(doc: dict) -> "sf.Conversion":
    fg = doc["solids"].split("\n")
    bg = doc["bg"].split("\n")
    conv = sf.Conversion(len(fg[0]), len(fg), fg, bg, [dict(e) for e in doc["entities"]])
    v = doc.get("validation", {})
    conv.notes.append(f"celpcg: seed {doc.get('seed')} (attempt seed {doc.get('attempt_seed')}, "
                      f"{doc.get('attempts')} attempt(s)), difficulty {doc.get('difficulty')}; reach check "
                      f"{'passed' if v.get('ok') else 'FAILED'}: exits reached {', '.join(v.get('exits_reached', [])) or 'none'}")
    conv.attrs = {"celpcg_seed": str(doc.get("seed")), "celpcg_generator": str(doc.get("generator")),
                  "celpcg_difficulty": int(doc.get("difficulty") or 0)}
    return conv
