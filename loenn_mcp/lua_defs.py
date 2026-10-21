"""
Read Lönn plugin definitions (Loenn/entities/*.lua, Loenn/triggers/*.lua)
without running Lua.

Plugins are Lua programs, but the parts that matter for placing objects are
almost always literal tables:

    local berry = {}
    berry.name = "CEL/GreenBerry"
    berry.placements = {name = "green_berry", data = {collectSound = "Original"}}
    berry.fieldInformation = {count = {fieldType = "integer"}}
    return berry

This module tokenizes the file and evaluates literal expressions (strings,
numbers, booleans, nested tables, and references to earlier local literals).
Anything computed (function calls, string concatenation, loops) evaluates to
UNKNOWN and is skipped, so a definition built entirely by helper functions
still yields its name, just without defaults.
"""

from __future__ import annotations

import json
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


class _Unknown:
    def __repr__(self) -> str:
        return "UNKNOWN"


UNKNOWN = _Unknown()

# ─── Tokenizer ────────────────────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<comment>--\[(?P<ceq>=*)\[.*?\](?P=ceq)\]|--[^\n]*)
  | (?P<long>\[(?P<leq>=*)\[.*?\](?P=leq)\])
  | (?P<str>"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*')
  | (?P<num>0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)
  | (?P<name>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>\.\.\.|\.\.|==|~=|<=|>=|::|[-+*/%^\#&~|<>=(){}\[\];:,.])
""", re.S | re.X)

_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
            "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def _unescape(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        c = body[i]
        if c != "\\" or i + 1 >= len(body):
            out.append(c)
            i += 1
            continue
        n = body[i + 1]
        if n in _ESCAPES:
            out.append(_ESCAPES[n])
            i += 2
        elif n.isdigit():
            m = re.match(r"\d{1,3}", body[i + 1:])
            out.append(chr(int(m.group())))
            i += 1 + len(m.group())
        else:
            out.append(n)
            i += 2
    return "".join(out)


def tokenize(src: str) -> list[tuple[str, Any]]:
    toks: list[tuple[str, Any]] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            pos += 1  # stray character: skip it rather than fail the whole file
            continue
        pos = m.end()
        kind = m.lastgroup
        if kind in ("ws", "comment", "ceq", "leq"):
            continue
        text = m.group(kind)
        if kind == "str":
            toks.append(("str", _unescape(text[1:-1])))
        elif kind == "long":
            body = text[len(m.group("leq")) + 2:-(len(m.group("leq")) + 2)]
            toks.append(("str", body[1:] if body.startswith("\n") else body))
        elif kind == "num":
            if text.lower().startswith("0x"):
                toks.append(("num", int(text, 16)))
            elif re.fullmatch(r"\d+", text):
                toks.append(("num", int(text)))
            else:
                toks.append(("num", float(text)))
        elif kind == "name":
            toks.append(("name", text))
        else:
            toks.append(("op", text))
    toks.append(("eof", None))
    return toks


# ─── Literal evaluator ────────────────────────────────────────────────────────

_KEYWORDS = {"true": True, "false": False, "nil": None}
_BINOPS = {"..", "+", "-", "*", "/", "%", "^", "==", "~=", "<", ">", "<=", ">=", "and", "or"}


class _Parser:
    def __init__(self, toks: list, env: dict[str, Any]):
        self.t = toks
        self.env = env

    def peek(self, i: int, k: int = 0):
        return self.t[min(i + k, len(self.t) - 1)]

    def skip_balanced(self, i: int) -> int:
        """i points at an opening bracket; return index after its match."""
        depth = 0
        while self.t[i][0] != "eof":
            kind, v = self.t[i]
            if kind == "op" and v in "([{":
                depth += 1
            elif kind == "op" and v in ")]}":
                depth -= 1
                if depth == 0:
                    return i + 1
            i += 1
        return i

    def skip_expr_rest(self, i: int) -> int:
        """Skip to the end of the current expression (next , ; } ) at depth 0)."""
        while True:
            kind, v = self.t[i]
            if kind == "eof" or (kind == "op" and v in (",", ";", "}", ")", "]")):
                return i
            if kind == "op" and v in "([{":
                i = self.skip_balanced(i)
            elif kind == "name" and v in ("function",):
                return self.skip_function(i)
            else:
                i += 1

    def skip_function(self, i: int) -> int:
        """i points at 'function'; skip to after its matching 'end'."""
        depth = 0
        while self.t[i][0] != "eof":
            kind, v = self.t[i]
            if kind == "name":
                # function/do/repeat and 'if ... then' open a block; 'elseif ... then' does not
                if v in ("function", "do", "repeat") or (v == "then" and not self._then_is_elseif(i)):
                    depth += 1
                elif v in ("end", "until"):
                    depth -= 1
                    if depth == 0:
                        return i + 1
            i += 1
        return i

    def _then_is_elseif(self, i: int) -> bool:
        j = i - 1
        while j >= 0 and not (self.t[j][0] == "name" and self.t[j][1] in ("if", "elseif", "then", "end", "do")):
            j -= 1
        return j >= 0 and self.t[j][1] == "elseif"

    def expr(self, i: int) -> tuple[Any, int]:
        val, i = self.simple(i)
        kind, v = self.t[i]
        if (kind == "op" and v in _BINOPS) or (kind == "name" and v in ("and", "or")):
            return UNKNOWN, self.skip_expr_rest(i)
        return val, i

    def simple(self, i: int) -> tuple[Any, int]:
        kind, v = self.t[i]
        if kind in ("str", "num"):
            return v, i + 1
        if kind == "op" and v == "-" and self.t[i + 1][0] == "num":
            return -self.t[i + 1][1], i + 2
        if kind == "op" and v == "{":
            return self.table(i)
        if kind == "name" and v in _KEYWORDS:
            return _KEYWORDS[v], i + 1
        if kind == "name" and v == "function":
            return UNKNOWN, self.skip_function(i)
        if kind == "name":
            # dotted reference, maybe followed by a call or index
            path = [v]
            i += 1
            while self.t[i] == ("op", ".") and self.t[i + 1][0] == "name":
                path.append(self.t[i + 1][1])
                i += 2
            dotted = ".".join(path)
            if self.t[i][0] == "op" and self.t[i][1] in ("(", "{") or self.t[i][0] == "str":
                # A call. deepcopy/shallowcopy of a literal keeps its value.
                if path[-1] in ("deepcopy", "shallowcopy", "copy") and self.t[i] == ("op", "("):
                    inner, j = self.expr(i + 1)
                    if self.t[j] == ("op", ")"):
                        return inner, j + 1
                end = self.skip_balanced(i) if self.t[i][0] == "op" else i + 1
                while self.t[end][0] == "op" and self.t[end][1] in (".", ":", "(", "["):
                    end = self.skip_expr_rest(end)
                return UNKNOWN, end
            if self.t[i] == ("op", "["):
                return UNKNOWN, self.skip_balanced(i)
            return self.env.get(dotted, UNKNOWN), i
        if kind == "op" and v == "(":
            inner, j = self.expr(i + 1)
            if self.t[j] == ("op", ")"):
                return inner, j + 1
            return UNKNOWN, self.skip_balanced(i)
        if kind == "op" and v in ("#", "not", "-"):
            return UNKNOWN, self.skip_expr_rest(i + 1)
        return UNKNOWN, i + 1

    def table(self, i: int) -> tuple[Any, int]:
        assert self.t[i] == ("op", "{")
        i += 1
        arr: list[Any] = []
        hsh: dict[Any, Any] = {}
        while True:
            kind, v = self.t[i]
            if kind == "eof":
                return UNKNOWN, i
            if kind == "op" and v == "}":
                i += 1
                break
            if kind == "op" and v in (",", ";"):
                i += 1
                continue
            if kind == "name" and self.t[i + 1] == ("op", "="):
                val, i = self.expr(i + 2)
                hsh[v] = val
            elif kind == "op" and v == "[":
                key, j = self.expr(i + 1)
                if self.t[j] == ("op", "]") and self.t[j + 1] == ("op", "="):
                    val, i = self.expr(j + 2)
                    if key is not UNKNOWN:
                        hsh[key] = val
                else:
                    i = self.skip_expr_rest(i)
            else:
                val, i = self.expr(i)
                arr.append(val)
        if arr and not hsh:
            return arr, i
        for n, val in enumerate(arr, 1):
            hsh.setdefault(n, val)
        return hsh, i


# ─── Definitions ──────────────────────────────────────────────────────────────

@dataclass
class Placement:
    name: str
    data: dict[str, Any]
    computed: list[str] = field(default_factory=list)  # fields whose default is computed in Lua


@dataclass
class Definition:
    name: str
    kind: str                       # "entities" | "triggers"
    source: str                     # file (or archive member) the definition came from
    origin: str                     # "mod" | "installed" | "vanilla"
    placements: list[Placement] = field(default_factory=list)
    field_types: dict[str, str] = field(default_factory=dict)
    node_limits: tuple[int, int] | None = None
    parsed: bool = True             # False: name found but placements were not literal

    def placement(self, name: str = "") -> Placement | None:
        """A placement by name (the first one when no name is given)."""
        if name:
            chosen = next((p for p in self.placements if p.name == name), None)
            if chosen is None:
                raise KeyError(name)
            return chosen
        return self.placements[0] if self.placements else None

    def defaults(self, placement: str = "") -> dict[str, Any]:
        """Default attributes of a placement (the first one unless named)."""
        chosen = self.placement(placement)
        return dict(chosen.data) if chosen else {}

    def to_json(self) -> dict:
        return {"name": self.name, "kind": self.kind, "source": self.source, "origin": self.origin,
                "placements": [{"name": p.name, "data": p.data, **({"computed": p.computed} if p.computed else {})}
                               for p in self.placements],
                "fieldTypes": self.field_types,
                "nodeLimits": list(self.node_limits) if self.node_limits else None,
                "parsed": self.parsed}

    @classmethod
    def from_json(cls, d: dict) -> "Definition":
        return cls(d["name"], d["kind"], d.get("source", ""), d.get("origin", "vanilla"),
                   [Placement(p["name"], p.get("data", {}), p.get("computed", [])) for p in d.get("placements", [])],
                   d.get("fieldTypes", {}), tuple(d["nodeLimits"]) if d.get("nodeLimits") else None,
                   d.get("parsed", True))


def _clean(value: Any) -> Any:
    """Drop UNKNOWN leaves; keep only values a .bin attribute can hold."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()
                if isinstance(k, str) and v is not UNKNOWN and _clean(v) is not UNKNOWN}
    if isinstance(value, (bool, int, float, str)):
        return value
    return UNKNOWN


def _placements(raw: Any) -> list[Placement]:
    if isinstance(raw, dict) and ("name" in raw or "data" in raw):
        raw = [raw]
    if isinstance(raw, dict):  # {default = {...}, ...} keyed form
        raw = [dict(v, name=v.get("name", k)) for k, v in raw.items() if isinstance(v, dict)]
    out = []
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            continue
        raw_data = p.get("data", {})
        data = _clean(raw_data) if isinstance(raw_data, dict) else {}
        computed = [k for k in raw_data if isinstance(k, str) and k not in data] if isinstance(raw_data, dict) else []
        name = p.get("name") if isinstance(p.get("name"), str) else f"placement{len(out) + 1}"
        out.append(Placement(name, data, computed))
    return out


def _field_types(raw: Any) -> dict[str, str]:
    out = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, dict) and isinstance(v.get("fieldType"), str):
                out[k] = v["fieldType"]
    return out


# `handler.name = "x"` anywhere, or a namespaced `name = "Mod/X"` in a table.
# A bare `name = "normal"` is usually a placement name, not a definition.
_NAME_RE = re.compile(r"""\w\.name\s*=\s*["']([^"'\s]+)["']|\bname\s*=\s*["']([^"'\s]+/[^"'\s]+)["']""")


def _strip_comments(src: str) -> str:
    """Source with Lua comments removed (strings are kept intact)."""
    out, pos = [], 0
    while pos < len(src):
        m = _TOKEN_RE.match(src, pos)
        if not m:
            out.append(src[pos])
            pos += 1
            continue
        if m.lastgroup not in ("comment", "ceq"):
            out.append(m.group())
        pos = m.end()
    return "".join(out)


def _first_return(toks: list, start: int, end: int) -> int | None:
    """Index of the expression after the first top-level `return` in a function body."""
    depth = 0
    for j in range(start, end):
        kind, v = toks[j]
        if kind != "name":
            continue
        if v == "function":
            depth += 1
        elif v == "end" and depth:
            depth -= 1
        elif v == "return" and depth == 0:
            return j + 1
    return None


def parse_definitions(src: str, kind: str, source: str, origin: str) -> list[Definition]:
    """All definitions declared in one plugin file."""
    toks = tokenize(src)
    env: dict[str, Any] = {}
    handlers: dict[str, dict[str, Any]] = {}  # variable -> fields
    order: list[Any] = []
    p = _Parser(toks, env)
    i = 0
    returned: Any = None
    while toks[i][0] != "eof":
        kind_i, v = toks[i]
        # local NAME = expr   /   NAME = expr
        if kind_i == "name" and v == "local" and toks[i + 1][0] == "name" and toks[i + 2] == ("op", "="):
            var = toks[i + 1][1]
            val, i = p.expr(i + 3)
            env[var] = val
            if isinstance(val, dict):
                handlers[var] = dict(val)
                order.append(var)
            continue
        # NAME.field = expr
        if (kind_i == "name" and toks[i + 1] == ("op", ".") and toks[i + 2][0] == "name"
                and toks[i + 3] == ("op", "=") and (i == 0 or toks[i - 1] != ("op", "."))):
            var, fld = v, toks[i + 2][1]
            val, i = p.expr(i + 4)
            env[f"{var}.{fld}"] = val
            if isinstance(env.get(var), dict):
                env[var][fld] = val
            handlers.setdefault(var, {})[fld] = val
            if var not in order:
                order.append(var)
            continue
        if kind_i == "name" and v == "return":
            returned, i = p.expr(i + 1)
            continue
        if kind_i == "name" and v == "function":
            end = p.skip_function(i)
            # function handler.placements() return {...} end
            if (toks[i + 1][0] == "name" and toks[i + 2] == ("op", ".")
                    and toks[i + 3] == ("name", "placements")):
                ret = _first_return(toks, i + 4, end)
                if ret is not None:
                    var = toks[i + 1][1]
                    val, _ = p.expr(ret)
                    handlers.setdefault(var, {})["placements"] = val
                    if isinstance(env.get(var), dict):
                        env[var]["placements"] = val
                    if var not in order:
                        order.append(var)
            i = end
            continue
        i += 1

    tables: list[dict] = []
    if isinstance(returned, list):
        tables += [t for t in returned if isinstance(t, dict)]
    elif isinstance(returned, dict) and "name" in returned:
        tables.append(returned)
    tables += [handlers[var] for var in order]

    defs: dict[str, Definition] = {}
    for t in tables:
        name = t.get("name")
        if not isinstance(name, str) or name in defs:
            continue
        raw_pl = t.get("placements", UNKNOWN)
        limits = t.get("nodeLimits")
        defs[name] = Definition(
            name=name, kind=kind, source=source, origin=origin,
            placements=_placements(raw_pl) if raw_pl is not UNKNOWN else [],
            field_types=_field_types(t.get("fieldInformation")),
            node_limits=(tuple(limits) if isinstance(limits, list) and len(limits) == 2
                         and all(isinstance(x, int) for x in limits) else None),
            parsed=raw_pl is not UNKNOWN)
    # Spike-style handlers made by a helper from a directionNames table.
    for val in list(env.values()):
        names = val.get("directionNames") if isinstance(val, dict) else None
        if isinstance(names, dict):
            trigger = val.get("triggerSpike") is True
            for direction, name in names.items():
                if isinstance(name, str) and name not in defs:
                    data: dict[str, Any] = {"width": 8} if direction in ("up", "down") else {"height": 8}
                    if not trigger:
                        data["type"] = "default"
                    defs[name] = Definition(name, kind, source, origin, [Placement(direction, data)])
    # Names only a regex can see (built by helper functions): keep them, without defaults.
    for dotted, namespaced in _NAME_RE.findall(_strip_comments(src)):
        name = dotted or namespaced
        if name not in defs:
            defs[name] = Definition(name, kind, source, origin, parsed=False)
    return list(defs.values())


# ─── Catalog ──────────────────────────────────────────────────────────────────

VANILLA_DEFS_FILE = Path(__file__).with_name("data") / "vanilla_defs.json"
KINDS = ("entities", "triggers")


def _iter_plugin_files(root: Path) -> Iterable[tuple[str, str, str]]:
    """(kind, display path, source) for every entity/trigger plugin under a Loenn/ folder."""
    for kind in KINDS:
        base = root / kind
        if base.is_dir():
            for f in sorted(base.rglob("*.lua")):
                try:
                    yield kind, str(f), f.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue


def _iter_zip_plugins(zpath: Path, prefix: str = "loenn/") -> Iterable[tuple[str, str, str]]:
    try:
        with zipfile.ZipFile(zpath) as z:
            for n in z.namelist():
                low = n.lower()
                for kind in KINDS:
                    if low.startswith(f"{prefix}{kind}/") and low.endswith(".lua"):
                        yield kind, f"{zpath.name}:{n}", z.read(n).decode("utf-8", "replace")
    except (OSError, zipfile.BadZipFile):
        return


class Catalog:
    """Every known entity/trigger definition, by placed name.

    Lookup order: the workspace mod's own Loenn/ plugins, then other installed
    mods (sibling folders and zips when the workspace is in Celeste/Mods), then
    vanilla (Lönn's built-in plugins, shipped as data/vanilla_defs.json or read
    from a Lönn install named by LOENN_MCP_LOENN).
    """

    def __init__(self) -> None:
        self.defs: dict[str, dict[str, Definition]] = {k: {} for k in KINDS}

    def add(self, d: Definition) -> None:
        self.defs[d.kind].setdefault(d.name, d)

    def add_source(self, kind: str, path: str, src: str, origin: str) -> None:
        for d in parse_definitions(src, kind, path, origin):
            self.add(d)

    def get(self, name: str, kind: str | None = None) -> Definition | None:
        for k in ([kind] if kind else KINDS):
            d = self.defs[k].get(name)
            if d:
                return d
        return None

    def names(self, kind: str) -> list[str]:
        return sorted(self.defs[kind])

    def __len__(self) -> int:
        return sum(len(v) for v in self.defs.values())

    @classmethod
    def load(cls, workspace: Path | None = None, installed: bool = True, vanilla: bool = True) -> "Catalog":
        cat = cls()
        if workspace is not None:
            for kind, path, src in _iter_plugin_files(workspace / "Loenn"):
                cat.add_source(kind, path, src, "mod")
            mods = workspace.parent
            if installed and mods.name.lower() == "mods" and mods.is_dir():
                for entry in sorted(mods.iterdir()):
                    if entry.resolve() == workspace.resolve():
                        continue
                    if entry.is_dir():
                        for kind, path, src in _iter_plugin_files(entry / "Loenn"):
                            cat.add_source(kind, path, src, "installed")
                    elif entry.suffix.lower() == ".zip":
                        for kind, path, src in _iter_zip_plugins(entry):
                            cat.add_source(kind, path, src, "installed")
        if vanilla:
            for d in load_vanilla():
                cat.add(d)
        return cat


def vanilla_from_loenn(loenn: Path) -> list[Definition]:
    """Parse Lönn's own entity/trigger plugins from an install (Lönn.exe is a zip) or a source folder."""
    defs: list[Definition] = []
    if loenn.is_dir():
        exe = next((p for p in loenn.glob("*.exe") if zipfile.is_zipfile(p)), None)
        if exe is None:
            src_root = loenn / "src" if (loenn / "src").is_dir() else loenn
            for kind, path, src in _iter_plugin_files(src_root):
                defs += parse_definitions(src, kind, Path(path).name, "vanilla")
            return defs
        loenn = exe
    for kind, path, src in _iter_zip_plugins(loenn, prefix=""):
        defs += parse_definitions(src, kind, path.split(":", 1)[1], "vanilla")
    return defs


def load_vanilla() -> list[Definition]:
    env = os.environ.get("LOENN_MCP_LOENN")
    if env and Path(env).exists():
        return vanilla_from_loenn(Path(env))
    if VANILLA_DEFS_FILE.exists():
        data = json.loads(VANILLA_DEFS_FILE.read_text(encoding="utf-8"))
        return [Definition.from_json(d) for d in data["definitions"]]
    return []
