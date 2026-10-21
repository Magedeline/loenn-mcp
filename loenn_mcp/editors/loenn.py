"""
Lönn adapter. Verified against Lönn 1.0.10 (LÖVE 11.5) on Windows:

  Lönn.exe [--storage-directory DIR] <map.bin>
    - the positional argument is the map to open (launch_arguments.lua,
      scenes/loading.lua); without it Lönn reopens persistence.lastLoadedFilename;
    - --storage-directory moves settings.conf, persistence.conf, Backups/,
      Plugins/ and run.log to DIR (file_locations.lua).
  There is no room argument. scenes/loading.lua passes
  persistence.lastSelectedRoomName to loadFile, so writing that key (and the
  camera keys) into persistence.conf before launch opens the map on that room.
  There is no IPC and no single-instance lock: a running Lönn cannot be told to
  open or reload a file, and a second launch is a second window.
  Saving writes "<map>.bin.saving", verifies it, deletes the map and renames.

Storage modes:
  isolated (default): <state>/loenn-storage, seeded once from the user's
    settings.conf and Plugins/. The user's recent files and window state are
    untouched, and the room can always be preselected.
  user: the user's own %LOCALAPPDATA%/Loenn. Preselecting a room edits their
    persistence.conf, so it is refused while another Lönn is running.
"""

from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

try:
    from .. import celeste_bin as cb
    from .. import safety
except ImportError:  # run directly from source
    import celeste_bin as cb
    import safety

from .base import EditorAdapter, EditorError, env_path, windows_processes


_PROC_CACHE: tuple[float, list[tuple[str, int]]] = (0.0, [])


def running_loenn(max_age: float = 3.0) -> list[tuple[str, int]]:
    """Running Lönn processes (cached for a few seconds: every map write checks this)."""
    global _PROC_CACHE
    now = time.time()
    if now - _PROC_CACHE[0] > max_age:
        procs = [p for p in windows_processes("nn.exe") if p[0].lower() in ("lönn.exe", "lonn.exe", "loenn.exe")]
        _PROC_CACHE = (now, procs)
    return _PROC_CACHE[1]


def user_storage() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "Loenn"


def isolated_storage() -> Path:
    return safety.state_dir() / "loenn-storage"


def _lua_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_persistence_keys(conf: Path, values: dict[str, object]) -> None:
    """Set top-level keys in a Lönn persistence.conf (a serialized Lua table), keeping the rest."""
    text = conf.read_text(encoding="utf-8") if conf.exists() else "{\n}\n"
    if "{" not in text:
        text = "{\n}\n"
    for key, value in values.items():
        lit = _lua_string(value) if isinstance(value, str) else repr(value)
        pat = re.compile(rf"^(\s*){re.escape(key)}\s*=\s*[^\n]*?(,?)\s*$", re.M)
        if pat.search(text):
            text = pat.sub(lambda m: f"{m.group(1)}{key} = {lit}{m.group(2) or ','}", text, count=1)
        else:
            i = text.index("{") + 1
            text = text[:i] + f"\n    {key} = {lit}," + text[i:]
    conf.write_text(text, encoding="utf-8")


def read_persistence_value(conf: Path, key: str) -> str | None:
    if not conf.exists():
        return None
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?),?\s*$", conf.read_text(encoding="utf-8", errors="replace"), re.M)
    if not m:
        return None
    v = m.group(1).strip()
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


class LoennAdapter(EditorAdapter):
    name = "loenn"
    supports_room = True

    def __init__(self, workspace: Path, storage: str | None = None):
        super().__init__(workspace)
        self.storage_mode = (storage or os.environ.get("LOENN_MCP_LOENN_STORAGE", "isolated")).lower()
        if self.storage_mode not in ("isolated", "user"):
            raise EditorError("storage must be 'isolated' or 'user'")

    def candidates(self) -> list[Path]:
        out = []
        if env_path("LOENN_EXE"):
            out.append(env_path("LOENN_EXE"))
        local = os.environ.get("LOCALAPPDATA")
        if local:
            out.append(Path(local) / "Olympus" / "loenn" / "Lönn.exe")
            out.append(Path(local) / "Olympus" / "loenn" / "Lonn.exe")
        return out

    def find_executable(self) -> Path:
        for p in self.candidates():
            if p and p.exists():
                return p
        raise EditorError("Lönn.exe not found. Install Lönn from Olympus, or set LOENN_EXE to its path. "
                          "Looked in: " + "; ".join(str(p) for p in self.candidates()))

    def storage_dir(self) -> Path:
        if self.storage_mode == "user":
            return user_storage()
        iso = isolated_storage()
        iso.mkdir(parents=True, exist_ok=True)
        src = user_storage()
        if not (iso / "settings.conf").exists() and (src / "settings.conf").exists():
            shutil.copy2(src / "settings.conf", iso / "settings.conf")
        if not (iso / "Plugins").exists() and (src / "Plugins").exists():
            shutil.copytree(src / "Plugins", iso / "Plugins")
        return iso

    def temp_save_path(self, map_path: Path) -> Path:
        return map_path.with_name(map_path.name + ".saving")

    def running_instances(self) -> list[tuple[str, int]]:
        return running_loenn()

    def launch_args(self, exe: Path, map_path: Path, room: str | None) -> list[str]:
        storage = self.storage_dir()
        if room:
            if self.storage_mode == "user" and self.running_instances():
                raise EditorError("another Lönn is running with your own storage, so the room cannot be "
                                  "preselected safely. Close it, or use storage='isolated'.")
            tree = cb.read_map(map_path)
            target = next(r for r in cb.get_rooms(tree) if r.get("name") in (room, f"lvl_{room}"))
            set_persistence_keys(storage / "persistence.conf", {
                "lastSelectedRoomName": str(target.get("name")),
                "cameraPositionX": int(target.get("x", 0)) - 32,
                "cameraPositionY": int(target.get("y", 0)) - 32,
                "cameraPositionScale": 1,
            })
        args = [str(exe)]
        if self.storage_mode == "isolated":
            args += ["--storage-directory", str(storage)]
        return args + [str(map_path)]

    def external_guard(self, path: Path) -> str | None:
        """A Lönn we did not start that has this map open (per the user's persistence)."""
        if not self.running_instances():
            return None
        # Only the user's own storage: Lönn leaves currentlyRunning = true after it exits,
        # so the isolated storage could point at a stale map while an unrelated Lönn runs.
        # Instances started from here are covered by the session guard instead.
        for storage in (user_storage(),):
            conf = storage / "persistence.conf"
            last = read_persistence_value(conf, "lastLoadedFilename")
            running = read_persistence_value(conf, "currentlyRunning")
            if last and running == "true":
                try:
                    if Path(last).resolve() == path.resolve():
                        return (f"{path.name} looks open in a running Lönn (from {storage}). Save and close "
                                "Lönn first, or write to a copy.")
                except OSError:
                    pass
        return None


def _external_guard(path: Path) -> str | None:
    if path.suffix.lower() != ".bin":
        return None
    return LoennAdapter(Path("."), "isolated").external_guard(path)


safety.add_guard(_external_guard)
