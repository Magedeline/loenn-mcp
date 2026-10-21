# itch.io launch plan — loenn-mcp Pro

Draft plan following abyo software's playbook: ship what exists now, keep GitHub/PyPI
as the free Lite tier, sell Pro as a one-time purchase on itch.io. Nothing here has
been published — it's a draft for you to edit, then paste into the itch.io editor
under **MaggyStudio1234**.

## What I can't do for you

I don't have itch.io account access, so I can't create the listing, upload a build,
or post to Discord/GameBanana/Reddit. Everything below is copy and a proposed split
for you to review, adjust, and paste in yourself.

---

## 1. Lite / Pro split

Principle: **Lite is the manual editing toolkit** (read, edit, validate — commodity
CRUD that every map editor needs). **Pro is the generative + AI layer** (the actual
hard-to-build part — MdMC/WFC procedural generation, Claude-powered analysis,
pattern/terrain/image generation). This mirrors abyo's "free utility, paid power
tool" model and gives Lite users a real reason to upgrade instead of a crippled demo.

No code changes are needed to ship this split for v1 — GitHub/PyPI keeps shipping the
full server as-is (that's already your Lite tier). For itch.io Pro, package the same
repo as a zip/installer with the pitch below. If you later want to *enforce* the split
(e.g. Pro-only tools disabled without a license key), that's a separate follow-up —
happy to help design that once the split itself is validated by sales.

| Tier | Categories | Tool count (approx.) |
|---|---|---|
| **Lite** (free — GitHub + PyPI, MIT) | Reading & Querying, Editing, Decals & Effects, Definitions & Catalog, Basic Analysis (`analyze_map`, `visualize_map_layout`, `preview_map_section`), Rendering, core Project Management (`get_mod_info`, `validate_map`, `batch_validate_and_fix`, `export_room_json`/`import_room_json`) | ~48 |
| **Pro** ($7 one-time — itch.io) | Advanced Analysis (`analyze_entity_usage`, `analyze_difficulty`, `find_entity_references`, `detect_map_patterns`, `analyze_room_connectivity`, `suggest_improvements`, `compare_maps`), Wiki/Caching, Diffing, AI-Powered tools (`ai_analyze_map`, `ai_describe_room`, `ai_suggest_entities`, `analyze_map_assets`), all Procedural Generation (pattern-based, image/terrain, PCGHelper MdMC/WFC pipeline) | ~35 |

Adjust freely — this is a starting cut, not a final answer. The one thing I'd
protect: keep the PCG/AI tools Pro-only. They're the parts backed by real research
(the MdMC/WFC paper implementation, the density-budget entity placement we just
fixed) and are what actually took you months to build.

---

## 2. itch.io store page copy

**Title:** loenn-mcp Pro — AI Map Generation for Celeste

**Tagline (one line, under store title):**
Generate, analyze, and auto-improve Celeste maps with Claude — MdMC/WFC procedural
generation, AI design feedback, and 80+ MCP tools for Lönn.

**Price:** $7 one-time (no subscription)

**Short description (appears in search/browse):**
An MCP server that lets Claude (or any MCP client) read, edit, and *generate* your
Celeste `.bin` maps — procedural room filling, AI design critique, and terrain/image
map generation, on top of full manual map editing.

**Long description:**

> loenn-mcp brings a full Celeste map editor to your AI assistant. The free/Lite
> version (also on GitHub) covers everyday editing: read and write rooms, place
> entities and decals, manage stylegrounds, validate playability.
>
> **Pro adds the generative layer:**
> - **Procedural generation** — MdMC/WFC tile filling trained on your own rooms,
>   skeleton layout generation, one-shot end-to-end pipelines
> - **AI design feedback** — Claude-powered map analysis, room descriptions, and
>   entity placement suggestions (bring your own Anthropic API key)
> - **Terrain & image-to-map** — Perlin/Voronoi terrain generation, convert a PNG
>   sketch straight into a playable map
> - **Deep analysis** — difficulty scoring, connectivity graphs, pattern detection,
>   structural diffing between map versions
>
> Density-budget entity placement means generated rooms get a sane number of
> strawberries, spikes, and springs regardless of room size — no more empty rooms
> or berry floors.
>
> Works with [Everest](https://github.com/EverestAPI/Everest), and maps from
> [Lönn](https://github.com/CelestialCartographers/Loenn) or
> [Ahorn](https://github.com/CelestialCartographers/Ahorn). No Celeste installation
> required to run the server itself.
>
> Free/Lite version (manual editing only, MIT licensed): [github.com/Maggy-Studio/loenn-mcp](https://github.com/Maggy-Studio/loenn-mcp)

**Feature table (Lite vs Pro) — paste directly on the store page:**

| | Lite (free, GitHub) | Pro ($7, itch.io) |
|---|---|---|
| Read/edit rooms, entities, triggers, decals | ✅ | ✅ |
| Stylegrounds, room settings, metadata | ✅ | ✅ |
| Playability validation & auto-fix | ✅ | ✅ |
| HTML map preview/rendering | ✅ | ✅ |
| Procedural generation (MdMC/WFC, terrain, image-to-map) | ❌ | ✅ |
| AI design feedback & suggestions (Claude) | ❌ | ✅ |
| Advanced analysis (difficulty, connectivity, patterns, diffing) | ❌ | ✅ |
| Wiki/cache for persisting analysis across sessions | ❌ | ✅ |

**Tags:** `celeste`, `modding`, `map-editor`, `mcp`, `ai-tools`, `procedural-generation`,
`level-design`, `developer-tools`

**Still needed before publishing (your side):** a 60–90s demo GIF/video (screen
recording of `pcg_pipeline` generating a room, or `ai_analyze_map` giving feedback,
is the highest-leverage 90 seconds you can spend), 3–5 screenshots of `render_map_html`
output, and the actual Pro build/zip.

---

## 3. Announcement post draft (Discord / GameBanana / Reddit)

One honest "I made this" post, per abyo's advice — same core text, trimmed per platform:

> Been building an MCP server that lets Claude edit and *generate* Celeste maps —
> read/write `.bin` files, place entities, and now full procedural generation
> (MdMC/WFC tile filling trained on your own rooms, AI design feedback, terrain
> generation from noise). Free/Lite version's been on GitHub/PyPI for a while
> (github.com/Maggy-Studio/loenn-mcp); just put together a Pro version with the
> generation + AI tools on itch.io for anyone who wants to support it: [link].
> Happy to answer questions or take feature requests.

Post it once per venue (Celeste modding Discord, GameBanana, r/celestegame), not
cross-posted verbatim in a way that reads as spam — tweak the opener per audience.

---

## Open questions for you

- Does the Lite/Pro split above match what you want gated? (I defaulted to
  "manual editing free, generation+AI paid" — see §1.)
- Do you want the Pro package to literally be the same codebase (simplest — just
  document the split, don't remove code from GitHub) or an actually-separate lite
  build with Pro tools stripped out?
