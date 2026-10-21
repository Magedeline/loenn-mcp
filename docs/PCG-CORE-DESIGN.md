# One PCG core for Metroidvania Studio, the map editor and the running game

Design for CELESTELLARIA (mod folder `CEL`). Draft 1, 2026-10-21. Read `AUDIT-2026-10-21.md` first; this builds on what it verified.

## 0. Recommendation

Build **one small C# class library, `CelPcg.Core` (netstandard2.0, zero dependencies)**, and give it three thin hosts:

1. a **`celpcg` command-line tool** (net8) that `loenn-mcp` calls; the MCP stays the owner of `.bin` I/O, Lönn/Rysy launching, validation glue and the chapter "commit" step;
2. the **CEL mod** (net8) referencing the same DLL, with one Everest hook that injects generated rooms into `MapData` at load time, always behind a baked fallback;
3. **Metroidvania Studio** fed through its existing automation API (`studio_apply` batches emitted by the CLI); a direct DLL reference in your fork only if that ever becomes necessary.

The intermediate representation is a **JSON room document (`celpcg-room`) that is a superset of the authoring JSON `merge_json_into_map` already accepts**, plus a **world document (`celpcg-world`) holding the mission graph and layout**. The pipeline is: hand-authored mission graph → grid layout → room content from hand-made templates first and constrained MdMC/WFC fill second → repair → validator → bake. **Do not build a GAN.** Determinism comes from an own PCG32 generator and a derived seed carried in every document.

Why C# and not Python: the runtime host is C#, and a core that cannot run in-game is not a shared core. Why not the recovered `MarkovRoomGenerator`: see §2.

## 1. Architecture

```
                          ┌──────────────────────────────────────────────────┐
                          │  CelPcg.Core  (C#, netstandard2.0, no deps)       │
                          │  ─ Ir: RoomDoc, WorldDoc, Exit, Gate, Ability     │
                          │  ─ Rng: Pcg32, SeedDerivation                     │
                          │  ─ Mission: grammar rules, key-before-lock check  │
                          │  ─ Layout: grid placer (cells 320x184)            │
                          │  ─ Fill: TemplateFill, MdmcFill, WfcFill, Repair  │
                          │  ─ Validate: schema, exits, reach model, catalog  │
                          └───────────┬───────────────┬───────────────┬──────┘
                                      │               │               │
                   ┌──────────────────┴──┐   ┌────────┴────────┐  ┌───┴──────────────────────┐
                   │ celpcg CLI (net8)   │   │ CEL mod (net8)  │  │ Metroidvania Studio fork │
                   │ generate / validate │   │ CelPcg.Celeste: │  │ (optional, later): menu  │
                   │ / to-studio-batch   │   │ IR → Element →  │  │ "Generate room" calling  │
                   │ JSON in, JSON out   │   │ LevelData at    │  │ the DLL directly         │
                   └─────────┬───────────┘   │ MapData.Load    │  └──────────▲───────────────┘
                             │               └────────▲────────┘             │
                             ▼                        │                      │ studio_apply batch
┌────────────────────────────┴──────────────────────────────────────────────┴───────────────┐
│ loenn-mcp (Python)                                                                        │
│  engine: celeste_bin (lossless .bin) · map_json (typed JSON) · authoring (merge, defaults) │
│  pcg_* tools: generate_room → RoomDoc → merge into a COPY of a .bin → validate → commit   │
│  editors/: EditorAdapter → LoennAdapter (launch, room, watch, reload) · RysyAdapter later  │
│  game_*: DebugRC teleport/reload, CelesteTAS proof (T2)                                   │
│  studio bridge (celeste-studio-mcp): splice_rooms, mvs2bin, studio_* proxy                 │
└───────────────────────────────────────────────────────────────────────────────────────────┘
                 ▲                                   ▲
                 │ Tiled .tmx templates               │ Metroidvania Studio map JSON
                 │ (TiledCS → RoomDoc)                │ (mvs2bin tables → RoomDoc)
```

Three integration points, one core, one IR. Each host converts IR to its native form at the last moment; nothing but `celeste_bin.py`, `CelesteMapIO.dll` (the studio fork) and Lönn itself ever touch the binary format.

## 2. Can the runtime RoomGenerator become the core?

**No, and it should not be revived.** What exists is `Code/ProceduralGeneration/RoomGeneration/MarkovRoomGenerator.cs` (580 lines) in CEL commit `e2c857df`, deleted the same day by `7194c9a7`. Recovered copy: `scratchpad/recovered_pcg/`.

What breaks if you pull it into a netstandard library:

| Problem | Detail |
|---|---|
| Game dependencies | `using Celeste; using Monocle; using Microsoft.Xna.Framework;` in the generator **and** in `MarkovChain.cs`/`MarkovChainManager.cs`. `Vector2` appears 14 times, `Level` 13 times. |
| No tile output | `GeneratedRoom` holds `Exits`, `Checkpoints` (Vector2), `Entities`, `Hazards`, `GeneratedEnemies`, `GeneratedPlatforms`. There is no `char[]` or tile grid anywhere in the file. The "char[] / difficulty 1–10" generator the Tiled legend refers to is not on this machine in any repo, zip or DLL; only `DifficultyTier` (1–10) survives. |
| Coupled to removed systems | It references `EnemyGeneration.EnemyVariant` and `PlatformGeneration.DynamicPlatform` (Nez/Verlet based), both deleted in the same consolidation. |
| Seeding | `System.Random` from an int seed; not portable across .NET versions and not mirrored in Python. |

What to keep from it: the `DifficultyTier` 1–10 scale and the idea of a Markov chain over **room types** (intro → challenge → rest …) for sequencing. Everything else is cheaper to rewrite than to untangle.

What to use as the seed of the core instead: **CelesteProGen's `Core/` + `Generators/`** (pure C#, `char[,]` grid, 14 generators, no game dependency; swap its `System.Random` for `Pcg32`) and **PCGHelper's algorithms** (your FDG '25 pipeline, already ported line-for-line to Python in the delta's `pcg_helper.py`, so a C# port is mechanical and can be golden-tested against the Python one).

## 3. Intermediate representation

### 3.1 `celpcg-room` (one room)

A superset of loenn-mcp's authoring JSON, so the MCP merge path, defaults filling and placement checks work unchanged.

```jsonc
{
  "format": "celpcg-room", "version": 1,
  "id": "ruins-c03",                       // stable id inside a world; becomes the level name
  "seed": 3141592653, "generator": "celpcg/0.1/template+mdmc",
  "difficulty": 4, "theme": "ruins", "tags": ["challenge", "vertical"],
  "room": "gen-ruins-c03",                 // authoring JSON: level name (lvl_ added at write)
  "x": 640, "y": -368, "width": 320, "height": 368,       // pixels, world space
  "fg": ["1111111111…", "1000000000…", …], // rows of Celeste tileset ids, top row first (320/8 = 40 chars)
  "bg": ["…"],
  "exits": [
    {"side": "left",  "offset": 20, "size": 3, "kind": "walk",  "to": "ruins-c02", "requires": []},
    {"side": "top",   "offset": 12, "size": 4, "kind": "climb", "to": "ruins-c04", "requires": ["dash"]}
  ],                                       // tile units; side ∈ left|right|top|bottom; kind ∈ walk|jump|climb|drop|launch
  "entities": [ {"name": "player", "x": 24, "y": 344},
                {"name": "spikesUp", "x": 96, "y": 344, "width": 32} ],   // authoring items; defaults come from Lönn plugins
  "triggers": [], "decals": {"fg": [], "bg": []},
  "requires": ["dash"],                    // abilities needed to traverse at all
  "grants": [],                            // ability / key this room gives (pickup room)
  "attrs": {"music": "", "dark": false}    // level attributes, same names as the .bin
}
```

Rules: tiles are Celeste tileset ids (what `solids` holds), not an abstract legend; a converter maps Tiled gids to them. Exits are in tiles on an 8 px grid and reference the neighbour by id, so connectivity is explicit and checkable without geometry. `entities[]` is exactly an authoring item (name, x, y, width/height, nodes, placement, extra attrs).

### 3.2 `celpcg-world` (a region or chapter)

```jsonc
{
  "format": "celpcg-world", "version": 1,
  "seed": 2026102101, "generator": "celpcg/0.1",
  "abilities": ["dash", "dreamdash", "kirby.float", "kirby.inhale", "kirby.sword"],   // the gating vocabulary, from CEL's inventory
  "start": "hub",
  "nodes": [
    {"id": "hub",      "kind": "hub",     "template": "hub_v2",     "cell": [0, 0]},
    {"id": "sword",    "kind": "pickup",  "grants": "kirby.sword",  "cell": [2, 0]},
    {"id": "gate-a",   "kind": "lock",    "requires": ["kirby.sword"], "fill": "mdmc:ruins", "difficulty": 5},
    {"id": "boss-1",   "kind": "boss",    "template": "arena_wide"}
  ],
  "edges": [ {"from": "hub", "to": "sword", "kind": "walk"}, {"from": "hub", "to": "gate-a", "kind": "climb"} ],
  "rooms": { "hub": { …celpcg-room… }, … }   // filled in by the layout + fill stages
}
```

`nodes` + `edges` is the **mission graph** (Dormans' terms: mission first, space second). `cell` is the **layout** (one cell = 320×184, `y` grows upward exactly as in `Tools/Maps/overworld_gen`). `rooms` is the **space**.

### 3.3 Converters

| From → To | Where | Status |
|---|---|---|
| `celpcg-room` → `.bin` (copy) | loenn-mcp `authoring.merge_bytes` (join `fg` rows → `solids`), then `celeste_bin.write_map_bytes` | exists, needs the `fg/bg` row projection (10 lines) |
| `celpcg-room` → in-game `LevelData` | CEL mod, `CelPcg.Celeste.IrToElement`: build `BinaryPacker.Element` tree (`level` → `solids`/`bg`/`entities`/`triggers`…) → `new LevelData(element)` → add to `MapData.Levels` | to write (~150 lines). CEL already constructs backdrops from `BinaryPacker.Element` and hooks `On.Celeste.MapData.Reload`, so the pattern is in-house; CelesteRandomizer (MIT) is the public precedent for assembling maps from `LevelData` at runtime |
| `celpcg-world` → Metroidvania Studio | `celpcg to-studio-batch`: `room.add`, `tiles.rectangle`/`tiles.paint`, `object.add` with `cel:` ids from `.studio/celeste-defs.json`; applied via `studio_apply` with `expectedRevision` | to write; the studio's own API is versioned and dry-runnable |
| Metroidvania Studio map → `celpcg-room` | reuse the tables in `mvs2bin.py` (cell → tile char, definition → entity, anchor rules) but emit IR instead of binary | to write; then `mvs2bin`'s private binary writer can be retired |
| Tiled `.tmx` → `celpcg-room` | TiledCS (MIT) in the CLI; gid → tile char from a per-tileset legend; objects → entities; map properties `difficulty`, `biome`, `room_name`, exits from `door_next` objects | to write (~120 lines) |
| `.bin` → `celpcg-room` | loenn-mcp `map_json` + a projection (for "regenerate this room" and for mining templates from existing chapters) | to write (~60 lines) |

## 4. Templates, algorithms, and learning: how they fit

Priority order inside the `Fill` stage, per node:

1. **Templates** (hand-made, highest quality): Tiled rooms, Metroidvania Studio rooms, or rooms mined from existing CEL `.bin`s by tag. Matched on exits (side/offset/size) and `requires`; mirrored horizontally when allowed. This is where your authored feel comes from and it is the realistic default for a solo dev.
2. **Constrained generation** for filler, corridors and challenge gauntlets: **MdMC/WFC trained on your own rooms** (the FDG '25 pipeline you already implemented in PCGHelper), with the exit corridors and a floor band fixed as hard constraints before sampling, then BFS repair (`platformer_repair`). CelesteProGen's BSP/tunneler/cellular generators cover caves and large organic rooms.
3. **Grammar** for the mission graph only: a handful of rewrite rules (insert pickup-before-lock, add optional side loop with reward, split long chain with rest room) applied to a hand-written seed graph. Not shape grammars for geometry; the grid layout does that.

**GAN verdict: not worth it for this project.** Reasons, stated plainly:

- The two attempts already on disk show where it lands: `celestegen` (delta) is a 20×32, 5-token model; "Loenn but Better" ships per-biome ONNX files whose actual room structure comes from Lua procedural code with a "guarantee a corridor" post-pass. The learned part adds texture, not level design.
- A GAN gives no playability or gating guarantee, so every validator in §5 is needed anyway; MdMC gives the same "learned from your maps" property with a model under 4 KB, deterministic sampling, and constraints you can impose during sampling (Snodgrass & Ontañón's constrained MdMC).
- Mario GAN (Volz et al. 2018) relied on a Mario A* agent for fitness; the Celeste equivalent is the CelesteTAS T2 gate, which is seconds per room, not milliseconds, so latent-space search is impractical here.
- The DagstuhlGAN repository has **no license**; its code cannot be reused.

If you still want a learned component later, the slot for it is "fill strategy for a node", behind the same validator, and the training corpus is the RoomDoc export of your own chapters.

## 5. Metroidvania constraints and the validator

### 5.1 The abilities vocabulary

The gating model is a set of ability strings (`dash`, `dreamdash`, `kirby.float`, …) taken from CEL's `PlayerInventoryManager` / `StartingInventory` and the Kirby controller; keys and switches (`TorchGate`, `DashCodeGate`, `DefeatSwitchGate`, `CrystalHeartGate` …) are modelled the same way as abilities granted by a node. The validator never looks at C# types, only at these strings and at which entity names map to which gate/pickup.

### 5.2 Tiers and where they run

| Tier | Checks | Runs in |
|---|---|---|
| **0 Static** | schema; every room has `player`; dimensions multiple of 8; `fg` rows = width/8; exits open (air at the exit cells on both rooms) and aligned to the neighbour's exit; entity/trigger names exist in the Lönn catalog **and** their textures / `Sprites.xml` ids exist (sprites-first); ids unique map-wide | `CelPcg.Core` (C#) and mirrored in loenn-mcp (`authoring.check_placement` + a new `check_assets`) |
| **1 Reachability** | per room: from every entry exit, every other exit reachable under the room's `requires`, using the tile movement model of `Tools/Maps/overworld_gen/reach.py` (jump 3 up / 6 across, dash 4, springs, refills, dream blocks; climbing deliberately not modelled so results err safe), ported to C#; per world: BFS over the mission graph with the ability set, so every lock is reached only after its key (Cooper & Bazzaz's rule), every pickup is reachable, the boss and heart are reachable, no dead-end branch without a reward | `CelPcg.Core`; exposed as `celpcg validate` and MCP `pcg_verify`; run in-game before injection |
| **2 Proof by play** | `game_teleport` + `game_play_tas` (existing), or the delta's `verify/` beam search over a macro-action alphabet with Madeline/Kirby capability sets. Required before a generated room is committed to a chapter folder; optional at runtime (never) | MCP only, with the game running |

### 5.3 "Verify before committing to a chapter folder"

MCP tool `pcg_commit(wip_map, chapter_map, rooms, require_tier=1|2)`: copies the chapter map to a timestamped file, splices the rooms (logic from `celeste-studio-mcp.bridge.splice`), re-runs tier 0+1 **against the real neighbours in the chapter**, optionally tier 2, and only then replaces the chapter map (still through the atomic, backed-up writer). A failed check leaves the chapter untouched and returns the report. Nothing else writes into `Maps/CEL/Area` and the side folders.

### 5.4 In-game

The mod runs tier 0+1 on every room it generates. On failure it uses the baked fallback room that is always present in the `.bin` and logs `celpcg: fallback <room> seed <n> reason <…>` to the Everest log (readable through `game_log`). A console command `celpcg_verify <room>` prints the same report for a loaded level.

## 6. Determinism

- **PRNG:** PCG32 implemented in `CelPcg.Core` and mirrored in `loenn_mcp/pcg_rng.py`; golden tests assert identical streams. Never `System.Random`, never Python's `random`.
- **Seed derivation:** `roomSeed = fnv1a64(worldSeed, mapSID, roomId, generatorVersion)`. Changing the generator version intentionally changes rooms; everything else reproduces.
- **Every document carries `seed` and `generator`.** A baked room keeps them in `attrs` (`celpcg_seed`, `celpcg_generator`) so "regenerate this room with the same seed" works from the `.bin` alone, in Lönn or in-game.
- **Runtime seed policy** per procedural room: `fixed` (same as baked), `per-save` (seed stored in `CELModuleSaveData`), `daily`. The policy is an attribute on the marker entity, so it is editable in Lönn.

## 7. Bake vs runtime

| Bake to `.bin` (default) | Generate on load |
|---|---|
| All campaign rooms, anything with story triggers, cutscenes, stylegrounds, music changes, collectibles that count | Rooms explicitly marked with a `CEL/ProcRoom` entity (gauntlets, DX-side challenge chains, training halls) |
| Needed for Lönn/Rysy editing, decoration, TAS proof, playtesting trace logs | Only after the same seed has been baked and played at least once |
| Deterministic by construction | Deterministic by seed policy; always has the baked room as fallback |

Mechanism at runtime: `On.Celeste.MapData.Load` → after `orig`, for each level with a `CEL/ProcRoom` marker, generate the RoomDoc with the derived seed, validate tier 0+1, convert to `LevelData`, replace the level in `MapData.Levels` keeping name, position and size (so neighbours and stylegrounds `only`/`exclude` lists are unaffected). Rooms keep their `lvl_`-stripped names.

## 8. Phased plan

| Phase | Deliverable | Depends on |
|---|---|---|
| **1. MCP rework** (Part 1 of your task) | `editors/` adapter + `LoennAdapter` (`editor_open`, `editor_wait`, `editor_status`, `editor_close`), timestamped backup store + `output_path`/copy mode on every write, `map_diff` (semantic), `check_assets` (sprites-first), tool surface trimmed and descriptions shortened, README listing every tool, tests incl. real-map round trips and a Lönn launch smoke test | commit of the current uncommitted work |
| **2. Vertical slice** | `CelPcg.Core` skeleton (IR types, Pcg32, `TemplateFill` + simple floor/exit fill, validator tier 0+1), `celpcg` CLI, MCP `pcg_generate_room` → merge into a copy of `wip/03_precision.bin` → `editor_open` → `game_teleport` | phase 1 |
| **3. In-game** | `CelPcg.Celeste` in the CEL mod: `ProcRoom` marker entity + Lönn plugin, `MapData.Load` hook, fallback, `celpcg_verify` command | phase 2 |
| **4. Content pipeline** | TMX → IR, `.bin` → IR template mining, MdMC/WFC port with constraints + repair (golden-tested against `pcg_helper.py`), mission grammar + grid layout (port of `overworld_gen`), `pcg_commit` with tier 2 | phase 2 |
| **5. Studio + Rysy** | `to-studio-batch` emitter and `studio_apply` round trip; `RysyAdapter` (`--map`, `--script` headless reload) | phases 1, 4 |

## 9. Risks

- **Rysy has no license** (no `LICENSE` in the repo, GitHub reports none). A "rysy mcp ultra" fork cannot be redistributed until the author adds one. The adapter only launches the exe, which is fine; forking is not.
- **Lönn plugin APIs are internal.** The phase-2 reload plugin depends on `inputDevice.newInputDevice` and `loadedState.loadFile`, both stable since 0.7 but undocumented.
- **Three binary-format implementations still in play** (engine, `CelesteMapIO.dll` in the studio fork, `mvs2bin.py`). The IR removes the need for the last one; the studio fork's is unavoidable while you edit `.bin` inside the studio.
- **Reachability model is conservative**, so it will reject some rooms a human can clear. That is the right side to err on for automated commits; tier 2 is the escape hatch.
- **MdMC trained on 82 maps of mostly unique story rooms** may produce bland filler. Templates first, generation for connective tissue, decorate last.

## 10. References and reuse

| Work | Use here | License / reuse |
|---|---|---|
| Lönn 1.0.10 (CelestialCartographers) | launch args, persistence, save semantics, plugin folders | MIT; read, not vendored |
| Maple (CelestialCartographers) | reference for the binary format | MIT |
| CelesteRandomizer (rhelmot) | public precedent for assembling maps from `LevelData` at runtime; per-room exit metadata model | MIT, reusable |
| CelesteTAS-EverestInterop | tier 2 proof via `game_play_tas` | MIT |
| Metroidvania Studio (LBarimi) 1.3.2 + your celeste-bin v3 patch | design-time authoring, seeded Lua, `studio_apply` | MIT |
| PCGHelper (yours) / Robinet, Gómez-Maureira & Preuss, "Towards a Celeste AI Framework", FDG '25, doi:10.1145/3723498.3723796 | MdMC/WFC/skeleton/repair/scoring; Python port in the delta | yours |
| Snodgrass & Ontañón: "A Hierarchical MdMC Approach to 2D Video Game Map Generation" (AIIDE 2015); "Procedural level generation using multi-layer level representations with MdMCs" (CIG 2017); constrained MdMC for Lode Runner (AIIDE 2017 workshop) | the learned component, with playability constraints during sampling | papers |
| Dormans, "Adventures in Level Design: Generating Missions and Spaces for Action Adventure Games" (PCG workshop 2010); Dormans & Bakkes on lock-and-key grammars; Cooper & Bazzaz's reproduction note that keys must not end up behind locks | mission graph before space; key-before-lock check | papers |
| Facey & Cooper, "Toward Space-Time WaveFunctionCollapse for Level and Solution Generation" (AIIDE 2024); Cheng et al., controllable WFC (ICEC 2020) | why plain WFC needs global constraints; approach for exit/corridor constraints in the fill stage | papers |
| mxgmn/WaveFunctionCollapse | reference WFC | MIT |
| Edgar-DotNet (Nepožitek) | optional graph-to-layout engine if the grid placer proves too rigid | MIT (the Unity package you have is MIT but forbids redistribution; the .NET library is what a core would use) |
| Volz et al., "Evolving Mario Levels in the Latent Space of a DCGAN" (GECCO 2018); DagstuhlGAN repo | the GAN idea; rejected (§4) | paper citeable; repo has **no license** |
| CelesteProGen `Core/` + `Generators/` (yours) | seed of `CelPcg.Core` generators (`char[,]`, BSP, tunneler, cellular, perlin, scatter …) | yours |
| TiledCS / TiledSharp | TMX templates | MIT / Apache-2.0 |
| `Tools/Maps/overworld_gen` + `reach.py` (yours) | grid layout and the tile movement model, to port | yours |
