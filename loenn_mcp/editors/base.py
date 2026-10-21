"""
Editor adapters: open a map in a visual editor, notice when the user saved it,
and keep MCP writes from racing the editor.

The tool surface (editor_open / editor_wait / editor_status / editor_close)
only talks to EditorAdapter, so moving it to another editor means writing one
subclass. LoennAdapter is the real one today; RysyAdapter launches Rysy with
the same watching.

How "edit finished" is detected (no editor exposes IPC for this):
  - the .bin's (mtime, size, sha256) changes, no "<map>.bin.saving" temp file
    is present (Lönn writes the temp, verifies it, then renames), and the new
    bytes parse as a map  ->  a completed save;
  - the editor process we started exits  ->  session over.
"""

from __future__ import annotations

import hashlib
import itertools
import os
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

try:
    from .. import celeste_bin as cb
    from .. import mapdiff, safety
except ImportError:  # run directly from source
    import celeste_bin as cb
    import mapdiff
    import safety


class EditorError(RuntimeError):
    """Raised with a message that says what went wrong and what to do."""


@dataclass
class FileSig:
    mtime_ns: int
    size: int
    sha256: str

    @classmethod
    def of(cls, path: Path) -> "FileSig | None":
        try:
            st = path.stat()
            return cls(st.st_mtime_ns, st.st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        except FileNotFoundError:
            return None


@dataclass
class SaveEvent:
    at: float
    sha256: str
    size: int
    diff_text: str


@dataclass
class EditorSession:
    id: str
    editor: str
    map_path: Path
    room: str | None
    proc: subprocess.Popen | None
    started: float
    baseline: bytes                       # bytes when opened, for the "since open" diff
    last_seen: bytes                      # bytes at the last reported save
    last_sig: FileSig | None
    saves: list[SaveEvent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


_counter = itertools.count(1)
SESSIONS: dict[str, EditorSession] = {}


def _session_guard(path: Path) -> str | None:
    for s in SESSIONS.values():
        if s.running() and s.map_path.resolve() == path.resolve():
            return (f"{path.name} is open in {s.editor} (session {s.id}, pid {s.pid}). Its next save would "
                    f"overwrite this write. Save and close it first (editor_close), or write to a copy.")
    return None


safety.add_guard(_session_guard)


class EditorAdapter(ABC):
    """One editor. Subclasses supply the executable and how to launch it."""

    name = "editor"
    supports_room = False

    def __init__(self, workspace: Path):
        self.workspace = workspace

    # ── to implement ──
    @abstractmethod
    def find_executable(self) -> Path:
        """Path of the editor executable; raise EditorError with setup advice if missing."""

    @abstractmethod
    def launch_args(self, exe: Path, map_path: Path, room: str | None) -> list[str]:
        """Full argv; may prepare files (e.g. preselect the room)."""

    def external_guard(self, path: Path) -> str | None:
        """Refusal message if an instance we did not start has `path` open."""
        return None

    # ── shared behaviour ──
    def temp_save_path(self, map_path: Path) -> Path | None:
        return None

    def open(self, map_path: Path, room: str | None = None) -> EditorSession:
        map_path = map_path.resolve()
        if not map_path.exists():
            raise EditorError(f"map not found: {map_path}")
        data = map_path.read_bytes()
        tree = cb.read_map_bytes(data)
        if room:
            names = [str(r.get("name", "")) for r in cb.get_rooms(tree)]
            if room not in names and f"lvl_{room}" not in names:
                raise EditorError(f"room '{room}' is not in {map_path.name}. Rooms: "
                                  + ", ".join(mapdiff.short_name(n) for n in names[:40]))
        for s in SESSIONS.values():
            if s.running() and s.editor == self.name:
                raise EditorError(f"{self.name} is already open from this server (session {s.id} on "
                                  f"{s.map_path.name}). Close it with editor_close first.")
        exe = self.find_executable()
        argv = self.launch_args(exe, map_path, room)
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        proc = subprocess.Popen(argv, cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        sid = f"{self.name}-{next(_counter)}"
        s = EditorSession(sid, self.name, map_path, room, proc, time.time(), data, data, FileSig.of(map_path))
        if room and not self.supports_room:
            s.notes.append(f"{self.name} cannot preselect a room; open '{room}' by hand.")
        SESSIONS[sid] = s
        return s

    def poll(self, s: EditorSession) -> SaveEvent | None:
        """Record and return a completed save since the last poll, if any."""
        sig = FileSig.of(s.map_path)
        if sig is None or sig == s.last_sig:
            return None
        tmp = self.temp_save_path(s.map_path)
        if tmp is not None and tmp.exists():
            return None                       # save in progress
        data = s.map_path.read_bytes()
        try:
            new_tree = cb.read_map_bytes(data)
        except Exception:
            return None                       # half-written; try again next poll
        s.last_sig = sig
        if data == s.last_seen:
            return None
        d = mapdiff.diff_maps(cb.read_map_bytes(s.last_seen), new_tree, s.last_seen, data)
        ev = SaveEvent(time.time(), sig.sha256, sig.size, d.text(limit=60))
        s.last_seen = data
        s.saves.append(ev)
        return ev

    def wait(self, s: EditorSession, timeout: float, interval: float = 0.5) -> tuple[str, SaveEvent | None]:
        """Block until a save, the editor exiting, or the timeout. Returns (reason, event)."""
        end = time.time() + max(0.0, timeout)
        while True:
            ev = self.poll(s)
            if ev:
                return "saved", ev
            if not s.running():
                ev = self.poll(s)
                return ("saved_and_exited", ev) if ev else ("exited", None)
            if time.time() >= end:
                return "timeout", None
            time.sleep(interval)

    def close(self, s: EditorSession, force: bool = False, grace: float = 10.0) -> str:
        if not s.running():
            self.poll(s)
            return "not running"
        pid = s.pid
        if sys.platform == "win32":
            args = ["taskkill", "/PID", str(pid)] + (["/F"] if force else [])
            subprocess.run(args, capture_output=True)
        else:
            s.proc.kill() if force else s.proc.terminate()
        try:
            s.proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            return ("still running: the editor is probably asking about unsaved changes. Answer it in the "
                    "editor window, or call editor_close with force=true to discard them.")
        self.poll(s)
        return "closed" if not force else "killed (unsaved editor changes are lost)"

    def diff_since_open(self, s: EditorSession) -> str:
        cur = s.map_path.read_bytes() if s.map_path.exists() else b""
        if not cur:
            return "map file is missing"
        return mapdiff.diff_bytes(s.baseline, cur).text(limit=80)


def windows_processes(image_substring: str) -> list[tuple[str, int]]:
    """(image name, pid) of running processes whose image contains the substring (Windows only)."""
    if sys.platform != "win32":
        return []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15).stdout
    except Exception:
        return []
    res = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and image_substring.lower() in parts[0].lower():
            try:
                res.append((parts[0], int(parts[1].strip('"'))))
            except ValueError:
                pass
    return res


def env_path(name: str) -> Path | None:
    v = os.environ.get(name)
    return Path(v) if v else None
