# Third-party notices

loenn-mcp is MIT licensed (see `LICENSE`). It credits the projects below.

## lucid-dream (MIT)

<https://www.npmjs.com/package/lucid-dream>, Copyright (c) 2020 BloodSweatAndCode.

The JavaScript authoring package in `js/` follows lucid-dream's API shape:
`new Room([x, y], { name, size, fgTiles, entities })`, `Entity.Strawberry({ x, y })`,
and `new Tiles(text)`. It is a new implementation with no code copied: it
writes loenn-mcp's authoring JSON instead of `.bin` files, and has no
dependencies.

```
MIT License

Copyright (c) 2020 BloodSweatAndCode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Lönn (MIT)

<https://github.com/CelestialCartographers/Loenn>, Copyright (c) 2019-2025 Cruor and Vexatos.

`loenn_mcp/data/vanilla_defs.json` lists the names, placements and default
attribute values of Lönn's built-in entity and trigger plugins. It is generated
from a Lönn install by `tools/gen_vanilla_defs.py`; no Lönn code is included.

```
MIT License

Copyright (c) 2019-2025 Cruor and Vexatos

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## maddievision/celestial (no license; ideas only)

<https://github.com/maddievision/celestial> by maddievision.

That repository has no license, so none of its code is used or copied here.
Two of its ideas inspired features that were written from scratch:

- a JSON form of a map that records each attribute's binary value type
  (`map2json.rb`), which became `map_to_json` / `json_to_map`;
- a map viewer driven by that JSON (`viewer.js`), which became `render_map_html`
  accepting a typed JSON dump and highlighting planned additions.
