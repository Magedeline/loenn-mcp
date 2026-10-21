"""
Rysy adapter. From Rysy's CommandlineArguments.cs: `Rysy.exe --map <file.bin>`
(or a .bin as the last argument) opens the map; there is no room argument.
Saving is watched the same way as for Lönn (file signature + parse).

Executable: RYSY_EXE, else the newest Rysy.exe under
%USERPROFILE%/Documents/GitHub/Rysy/Rysy/bin.

Note: the Rysy repository has no license file, so a fork of it cannot be
redistributed. Launching it from here is fine.
"""

from __future__ import annotations

from pathlib import Path

from .base import EditorAdapter, EditorError, env_path


class RysyAdapter(EditorAdapter):
    name = "rysy"
    supports_room = False

    def find_executable(self) -> Path:
        p = env_path("RYSY_EXE")
        if p and p.exists():
            return p
        root = Path.home() / "Documents" / "GitHub" / "Rysy" / "Rysy" / "bin"
        found = sorted(root.rglob("Rysy.exe"), key=lambda q: q.stat().st_mtime, reverse=True) if root.exists() else []
        if found:
            return found[0]
        raise EditorError("Rysy.exe not found. Build Rysy, or set RYSY_EXE to its path.")

    def launch_args(self, exe: Path, map_path: Path, room: str | None) -> list[str]:
        return [str(exe), "--map", str(map_path)]
