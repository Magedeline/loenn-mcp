// Add collectibles to rooms that already exist in a map.
//
//   node js/bin/loenn-author.js js/examples/add-berries.js
// or, through the MCP server (dry run, with a preview):
//   run_author_script("js/examples/add-berries.js", map_path="Maps/MyMod/1-Test.bin")

const { Plan, Entity, Trigger, line } = require('..');

const plan = new Plan();

// One berry, defaults (winged, moon, checkpointID, order) filled from Lönn.
plan.room('a-01').add(Entity.Strawberry({ x: 120, y: 64 }));

// A row of three, evenly spaced.
for (const [x, y] of line(64, 96, 192, 96, 3)) {
	plan.room('a-02').add(Entity.Strawberry({ x, y }));
}

// Namespaced (mod) entities and triggers use the call form.
plan.room('a-03')
	.add(Entity('CEL/GreenBerry', { x: 200, y: 80 }))
	.add(Trigger('CEL/PinkPlatBerryCollectTrigger', { x: 0, y: 0, width: 16, height: 184 }));

module.exports = plan;
