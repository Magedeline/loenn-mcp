"""
seleCte (github.com/robilouis/seleCte) as a whole-level generator.

seleCte learned the tile patterns of Celeste's vanilla rooms with a
multi-dimensional Markov chain. It draws a level layout, fills each room by
sampling from the model, and a Julia script (on the Maple library) encodes
the result as a .bin that starts on a spawn and ends on a crystal heart:

    uv run python -m seleCte.pcg.level_generation --nb-rooms N --proba P
        --room-size W H --tries-limit T [--reset-skeleton] [--seed S]
        [--dict-proba-estimation MODEL] --level-name <level dir>
    julia --project=<seleCte> src/seleCte/room_encoder/data_to_level.jl
        <level dir> <name> <out dir>

The first writes <level dir>/data.json and room_<n>.csv, the second
<out dir>/<name>.bin. `run_level` runs both, then checks the map with
pcgscene's readiness checklist. The seleCte clone is only ever run, never
written to: a level's files land in loenn-mcp's state dir (or `work_dir`).

Where things are: SELECTE_DIR, else a `seleCte` folder next to this
repository, else ~/seleCte. `uv` and `julia` come from UV_EXE / JULIA_EXE or
PATH. `--seed` and `--dict-proba-estimation` exist only in a patched clone
(upstream has neither); the generator's own error is reported when a clone
lacks them.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from . import celeste_bin as cb
    from . import cli as pcgscene
    from . import safety
except ImportError:  # run directly from source
    import celeste_bin as cb
    import cli as pcgscene
    import safety


class SelecteError(RuntimeError):
    """seleCte, uv or julia is missing, a request is invalid, or a step failed."""


ENCODER = Path("src") / "seleCte" / "room_encoder" / "data_to_level.jl"
MODELS = Path("src") / "seleCte" / "pcg" / "preprocessing" / "probability_estimation_dicts"
DEFAULT_MODEL = "pe_d5_full"
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")
_TAIL = 15


# ─── Locations ────────────────────────────────────────────────────────────────

def find_dir() -> Path:
    env = os.environ.get("SELECTE_DIR")
    cands = [Path(env)] if env else []
    cands += [Path(__file__).resolve().parents[2] / "seleCte", Path.home() / "seleCte"]
    for c in cands:
        if (c / ENCODER).exists() and (c / "pyproject.toml").exists():
            return c
    raise SelecteError("seleCte not found. Clone github.com/robilouis/seleCte, run `uv sync` and "
                       "`julia --project=. -e \"using Pkg; Pkg.instantiate()\"` in it, or set SELECTE_DIR. "
                       "Looked in: " + "; ".join(str(c) for c in cands))


_HINTS = {"uv": "install it from https://docs.astral.sh/uv/",
          "julia": "seleCte's encoder needs Julia 1.11 or later, https://julialang.org/"}


def _exe(name: str, env: str) -> str:
    val = os.environ.get(env)
    if val:
        if Path(val).exists():
            return val
        raise SelecteError(f"{env}={val} does not exist")
    found = shutil.which(name)
    if not found:
        raise SelecteError(f"{name} not found on PATH (or set {env}): {_HINTS[name]}")
    return found


def models(root: Path) -> list[str]:
    """Probability-estimation dictionaries the clone ships (stems for --dict-proba-estimation)."""
    return sorted(p.stem for p in (root / MODELS).glob("*.json"))


def default_work_dir(name: str) -> Path:
    return safety.state_dir() / "selecte" / name


# ─── Running the two steps ────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: Path, timeout: int) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, creationflags=flags)


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-_TAIL:])


def generate(root: Path, level_dir: Path, nb_rooms: int, proba: float, width: int, height: int, tries_limit: int,
             reset_skeleton: bool, seed: int | None, model: str, timeout: int) -> tuple[str, float]:
    """Run seleCte's level generator; returns its log and the seconds it took."""
    uv = _exe("uv", "UV_EXE")
    cmd = [uv, "run", "--frozen", "python", "-m", "seleCte.pcg.level_generation",
           "--nb-rooms", str(nb_rooms), "--proba", str(proba), "--room-size", str(width), str(height),
           "--tries-limit", str(tries_limit), "--level-name", str(level_dir)]
    if reset_skeleton:
        cmd.append("--reset-skeleton")
    if seed is not None:
        cmd += ["--seed", str(seed)]
    if model and model != DEFAULT_MODEL:
        cmd += ["--dict-proba-estimation", model]
    level_dir.parent.mkdir(parents=True, exist_ok=True)
    if level_dir.exists():
        for stale in [level_dir / "data.json", *level_dir.glob("room_*.csv")]:
            if stale.exists():
                stale.unlink()
    t0 = time.monotonic()
    try:
        proc = _run(cmd, root, timeout)
    except subprocess.TimeoutExpired:
        raise SelecteError(f"seleCte generation did not finish within {timeout} s (fewer or smaller rooms are "
                           "faster, or raise timeout)") from None
    seconds = time.monotonic() - t0
    log = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0 or not (level_dir / "data.json").exists():
        hint = (" (--seed and --dict-proba-estimation need a patched seleCte clone; upstream has neither)"
                if "unrecognized arguments" in log else "")
        raise SelecteError(f"seleCte generation failed (exit {proc.returncode}){hint}:\n{_tail(log)}")
    return log, seconds


def encode(root: Path, level_dir: Path, name: str, out_dir: Path, timeout: int) -> tuple[Path, float, str]:
    """Run the Julia encoder on a level folder; returns the .bin path, seconds, and the log."""
    julia = _exe("julia", "JULIA_EXE")
    cmd = [julia, f"--project={root}", str(root / ENCODER), str(level_dir), name, str(out_dir)]
    t0 = time.monotonic()
    try:
        proc = _run(cmd, root, timeout)
    except subprocess.TimeoutExpired:
        raise SelecteError(f"seleCte's encoder did not finish within {timeout} s") from None
    seconds = time.monotonic() - t0
    log = (proc.stdout or "") + (proc.stderr or "")
    out = out_dir / f"{name}.bin"
    if proc.returncode != 0 or not out.exists():
        raise SelecteError(f"seleCte encoder failed (exit {proc.returncode}):\n{_tail(log)}")
    return out, seconds, log


# ─── Log summary ──────────────────────────────────────────────────────────────

@dataclass
class GenSummary:
    generated: list[str]        # rooms seleCte accepted, in order
    unplayable: list[str]       # rooms kept after tries_limit failures (final layout only)
    skeleton_redraws: int       # layouts thrown away (--reset-skeleton)
    off_path: list[str]         # rooms filled without a playability check


def summarize_log(text: str) -> GenSummary:
    gen: list[str] = []
    failed: list[str] = []
    off: list[str] = []
    redraws = 0
    for line in text.splitlines():
        m = re.search(r"Generated (\S+) in \d+ iterations", line)
        if m:
            gen.append(m.group(1))
            continue
        m = re.search(r"([^\s:]+) generation failed in \d+ tries", line)    # [^:] skips the log prefix
        if m:
            failed.append(m.group(1))
            continue
        m = re.search(r"Filling (\S+), off the traversal path", line)
        if m:
            off.append(m.group(1))
            continue
        if "Re-generating a skeleton" in line:
            redraws += 1
            gen.clear()
            failed.clear()
            off.clear()
    return GenSummary(gen, failed, redraws, off)


# ─── Whole run ────────────────────────────────────────────────────────────────

@dataclass
class LevelResult:
    name: str
    root: Path
    level_dir: Path
    bin_path: Path
    rooms: int
    gen_seconds: float
    enc_seconds: float
    summary: GenSummary
    validation: dict
    model: str


def run_level(name: str, nb_rooms: int = 7, proba: float = 0.3, width: int = 22, height: int = 14,
              seed: int | None = None, tries_limit: int = 4, reset_skeleton: bool = False, model: str = "",
              work_dir: Path | None = None, timeout: int = 900) -> LevelResult:
    """Generate, encode and validate one level. Files: <work_dir>/{data.json, room_<n>.csv, generate.log,
    encode.log, <name>.bin}; work_dir defaults to <state dir>/selecte/<name>."""
    if not NAME_RE.fullmatch(name or ""):
        raise SelecteError("name must be letters, digits, '-' or '_' (it names the .bin and the work folder)")
    if nb_rooms < 1:
        raise SelecteError("nb_rooms must be at least 1")
    if not 0.0 <= proba <= 1.0:
        raise SelecteError("proba must be between 0 (corridor) and 1 (labyrinth)")
    if width < 1 or height < 1:
        raise SelecteError("width and height are room sizes in tiles and must be positive")
    if tries_limit < 1:
        raise SelecteError("tries_limit must be at least 1")
    root = find_dir()
    model = model or DEFAULT_MODEL
    known = models(root)
    if model not in known:
        raise SelecteError(f"model '{model}' is not in {root / MODELS}. Available: {', '.join(known) or 'none'}")
    level_dir = Path(work_dir) if work_dir else default_work_dir(name)
    log, gen_s = generate(root, level_dir, nb_rooms, proba, width, height, tries_limit, reset_skeleton, seed, model,
                          timeout)
    level_dir.mkdir(parents=True, exist_ok=True)
    (level_dir / "generate.log").write_text(log, encoding="utf-8")
    bin_path, enc_s, elog = encode(root, level_dir, name, level_dir, timeout)
    (level_dir / "encode.log").write_text(elog, encoding="utf-8")
    tree = cb.read_map(bin_path)
    return LevelResult(name, root, level_dir, bin_path, len(cb.get_rooms(tree)), gen_s, enc_s, summarize_log(log),
                       pcgscene.validate_map(tree), model)
