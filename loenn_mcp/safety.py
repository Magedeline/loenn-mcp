"""
Write policy for every map the MCP touches.

- Before an existing .bin is replaced, a timestamped copy goes to the backup
  store (outside the mod folder, so Everest never sees it and the mod's git
  stays clean): <state>/backups/<workspace name>/<path in workspace>/<stem>.<timestamp>.bin
- Writes are refused while a guard objects (the map is open in Lönn, or the
  path matches LOENN_MCP_PROTECT), so an editor's next save can never silently
  overwrite MCP edits, and protected chapter maps are only changed through a
  copy plus map_commit.
- Every write is recorded, and server tools append "saved ... backup ..." to
  their result, so no write is silent.

State directory: LOENN_MCP_STATE_DIR, default %LOCALAPPDATA%/loenn-mcp (or
~/.local/state/loenn-mcp off Windows).
"""

from __future__ import annotations

import fnmatch
import os
import shutil
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

try:
    from . import celeste_bin as cb
except ImportError:  # run directly from source
    import celeste_bin as cb


class WriteBlocked(RuntimeError):
    """A write was refused by a guard. The message says why and what to do."""


def state_dir() -> Path:
    env = os.environ.get("LOENN_MCP_STATE_DIR")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "loenn-mcp" if base else Path.home() / ".local" / "state" / "loenn-mcp"


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def _rel_parts(path: Path, workspace: Path) -> tuple[str, Path]:
    """(workspace label, path relative to it); outside the workspace the drive becomes the label."""
    path = path.resolve()
    try:
        return workspace.resolve().name or "workspace", path.relative_to(workspace.resolve())
    except ValueError:
        anchor = path.anchor.replace(":", "").strip("\\/") or "root"
        return f"_outside/{anchor}", path.relative_to(path.anchor)


def backup_dir_for(path: Path, workspace: Path) -> Path:
    label, rel = _rel_parts(Path(path), workspace)
    return state_dir() / "backups" / label / rel.parent / rel.name


def make_backup(path: Path, workspace: Path, reason: str = "") -> Path | None:
    """Copy `path` into the backup store. Returns the copy, or None if `path` does not exist."""
    path = Path(path)
    if not path.exists():
        return None
    folder = backup_dir_for(path, workspace)
    folder.mkdir(parents=True, exist_ok=True)
    tag = f".{reason}" if reason else ""
    dest = folder / f"{path.stem}.{timestamp()}{tag}{path.suffix}"
    shutil.copy2(path, dest)
    return dest


def list_backups(path: Path, workspace: Path) -> list[Path]:
    folder = backup_dir_for(Path(path), workspace)
    if not folder.exists():
        return []
    return sorted(folder.glob(f"{Path(path).stem}.*{Path(path).suffix}"), reverse=True)


# ─── Guards ───────────────────────────────────────────────────────────────────

# A guard gets the absolute target path and returns a refusal message or None.
Guard = Callable[[Path], "str | None"]
_GUARDS: list[Guard] = []


def add_guard(guard: Guard) -> None:
    if guard not in _GUARDS:
        _GUARDS.append(guard)


def remove_guard(guard: Guard) -> None:
    if guard in _GUARDS:
        _GUARDS.remove(guard)


def protected_patterns() -> list[str]:
    raw = os.environ.get("LOENN_MCP_PROTECT", "")
    return [p.strip().replace("\\", "/") for p in raw.split(";") if p.strip()]


def is_protected(path: Path, workspace: Path) -> bool:
    try:
        rel = Path(path).resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return False
    return any(fnmatch.fnmatch(rel, pat) for pat in protected_patterns())


def check_writable(path: Path, workspace: Path, allow_protected: bool = False) -> None:
    path = Path(path).resolve()
    if not allow_protected and is_protected(path, workspace):
        raise WriteBlocked(
            f"{path.name} is protected (LOENN_MCP_PROTECT). Edit a copy (map_copy), "
            "then apply it with map_commit.")
    for guard in list(_GUARDS):
        msg = guard(path)
        if msg:
            raise WriteBlocked(msg)


# ─── Write log (per tool call, per thread) ────────────────────────────────────

@dataclass
class WriteRecord:
    path: Path
    backup: Path | None
    size: int


_log = threading.local()


def begin_call() -> None:
    _log.records = []


def end_call() -> list[WriteRecord]:
    records = getattr(_log, "records", [])
    _log.records = []
    return records


def _record(rec: WriteRecord) -> None:
    if not hasattr(_log, "records"):
        _log.records = []
    _log.records.append(rec)


# ─── Writes ───────────────────────────────────────────────────────────────────

def save_bytes(path: Path, payload: bytes, workspace: Path, header: str = "CELESTE MAP",
               allow_protected: bool = False, reason: str = "") -> WriteRecord:
    """Validate, back up (timestamped), then atomically write map bytes."""
    path = Path(path)
    check_writable(path, workspace, allow_protected)
    try:
        cb.read_map_bytes(payload, expected_header=header)
    except Exception as exc:
        raise ValueError(f"refusing to write {path.name}: the new bytes do not parse ({exc})") from exc
    backup = make_backup(path, workspace, reason)
    path.parent.mkdir(parents=True, exist_ok=True)
    cb.write_map_bytes(path, payload, header, backup=False, validate=False)
    rec = WriteRecord(path, backup, len(payload))
    _record(rec)
    return rec


def save_map(path: Path, tree: dict, workspace: Path, header: str = "CELESTE MAP",
             allow_protected: bool = False, reason: str = "") -> WriteRecord:
    return save_bytes(path, cb.serialize_map(tree, header), workspace, header, allow_protected, reason)


def describe_records(records: list[WriteRecord], workspace: Path) -> str:
    def show(p: Path) -> str:
        try:
            return Path(p).resolve().relative_to(workspace.resolve()).as_posix()
        except ValueError:
            return str(p)
    lines = []
    for r in records:
        b = f"; backup {r.backup}" if r.backup else "; new file, nothing to back up"
        lines.append(f"[saved {show(r.path)} ({r.size} bytes){b}]")
    return "\n".join(lines)
