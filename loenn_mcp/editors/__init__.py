"""Editor adapters (Lönn now, Rysy later) behind one interface."""

from pathlib import Path

from .base import SESSIONS, EditorAdapter, EditorError, EditorSession, SaveEvent
from .loenn import LoennAdapter
from .rysy import RysyAdapter

ADAPTERS = {"loenn": LoennAdapter, "rysy": RysyAdapter}


def get_adapter(name: str, workspace: Path, **kw) -> EditorAdapter:
    key = (name or "loenn").lower().replace("ö", "oe").replace("lonn", "loenn")
    if key not in ADAPTERS:
        raise EditorError(f"unknown editor '{name}'; use one of: {', '.join(ADAPTERS)}")
    cls = ADAPTERS[key]
    return cls(workspace, **kw) if kw else cls(workspace)


__all__ = ["ADAPTERS", "SESSIONS", "EditorAdapter", "EditorError", "EditorSession", "SaveEvent",
           "LoennAdapter", "RysyAdapter", "get_adapter"]
