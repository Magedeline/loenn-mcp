'use strict';
// Plain node:assert tests (no dependencies): node js/test/test.js
const assert = require('assert');
const { execFileSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { Plan, Room, Entity, Trigger, Tiles, line, placedName } = require('..');

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('property names map to placed names', () => {
	assert.strictEqual(placedName('Strawberry'), 'strawberry');
	assert.strictEqual(placedName('GoldenBerry'), 'goldenBerry');
	assert.strictEqual(Entity.Strawberry({ x: 1, y: 2 }).name, 'strawberry');
	assert.strictEqual(Entity('CEL/GreenBerry', { x: 1, y: 2 }).name, 'CEL/GreenBerry');
	assert.strictEqual(new Entity('player', { x: 1, y: 2 }).name, 'player');
	assert.strictEqual(Trigger.EventTrigger({ x: 0, y: 0 }).section, 'triggers');
});

test('positions are required and must be numbers', () => {
	assert.throws(() => Entity.Strawberry({ x: 1 }), /y must be a finite number/);
	assert.throws(() => Entity.Strawberry({ x: 'a', y: 1 }), /x must be/);
});

test('existing-room plan serializes to authoring JSON', () => {
	const plan = new Plan();
	plan.room('a-05').add(Entity.Strawberry({ x: 120, y: 64, winged: true }));
	plan.room('a-05').trigger('CEL/PinkPlatBerryCollectTrigger', { x: 0, y: 0, width: 16, height: 184 });
	plan.room('b-01').entity('refill', { x: 8, y: 8, nodes: [[16, 16], { x: 24, y: 24 }] });
	assert.deepStrictEqual(plan.toJSON(), {
		format: 'loenn-mcp/authoring', version: 1, rooms: [
			{ room: 'a-05',
				entities: [{ name: 'strawberry', x: 120, y: 64, winged: true }],
				triggers: [{ name: 'CEL/PinkPlatBerryCollectTrigger', x: 0, y: 0, width: 16, height: 184 }] },
			{ room: 'b-01', entities: [{ name: 'refill', x: 8, y: 8, nodes: [[16, 16], [24, 24]] }] },
		],
	});
});

test('new rooms take a pixel position and a tile size, lucid-dream style', () => {
	const r = new Room([320, 0], { name: 'x-01', size: [40, 23], fgTiles: new Tiles('\n11\n10\n'), dark: true,
		entities: [Entity.Player({ x: 8, y: 16 })] });
	assert.deepStrictEqual(r.toJSON(), {
		room: 'x-01', x: 320, y: 0, width: 320, height: 184, solids: '11\n10', attrs: { dark: true },
		entities: [{ name: 'player', x: 8, y: 16 }],
	});
});

test('line spaces points evenly', () => {
	assert.deepStrictEqual(line(0, 0, 100, 50, 3), [[0, 0], [50, 25], [100, 50]]);
	assert.deepStrictEqual(line(5, 5, 9, 9, 1), [[5, 5]]);
});

test('items are copied, not shared', () => {
	const a = Entity.Strawberry({ x: 1, y: 1 });
	const b = a.at(5, 6).with({ winged: true });
	assert.deepStrictEqual(a.toJSON(), { name: 'strawberry', x: 1, y: 1 });
	assert.deepStrictEqual(b.toJSON(), { name: 'strawberry', x: 5, y: 6, winged: true });
});

test('CLI runs exported plans, async factories and emit()', () => {
	const cli = path.join(__dirname, '..', 'bin', 'loenn-author.js');
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'loenn-author-'));
	const lib = JSON.stringify(path.join(__dirname, '..'));
	const scripts = {
		'exported.js': `const {Plan,Entity}=require(${lib});const p=new Plan();p.room('a').add(Entity.Strawberry({x:1,y:2}));console.log('hello');module.exports=p;`,
		'factory.js': `module.exports=async({Plan,Room,Entity})=>new Plan([new Room('a').add(Entity.Strawberry({x:1,y:2}))]);`,
		'emit.js': `const {Plan,Entity}=require(${lib});const p=new Plan();p.room('a').add(Entity.Strawberry({x:1,y:2}));p.emit();`,
	};
	for (const [file, src] of Object.entries(scripts)) {
		const script = path.join(dir, file);
		fs.writeFileSync(script, src);
		const out = path.join(dir, file + '.json');
		execFileSync(process.execPath, [cli, script, '--out', out], { stdio: 'pipe' });
		const doc = JSON.parse(fs.readFileSync(out, 'utf8'));
		assert.deepStrictEqual(doc.rooms, [{ room: 'a', entities: [{ name: 'strawberry', x: 1, y: 2 }] }], file);
	}
	// stdout carries only JSON even when the script logs
	const stdout = execFileSync(process.execPath, [cli, path.join(dir, 'exported.js')], { stdio: ['pipe', 'pipe', 'pipe'] });
	assert.strictEqual(JSON.parse(stdout.toString()).rooms.length, 1);
	fs.rmSync(dir, { recursive: true, force: true });
});

test('CLI fails clearly when a script exports nothing', () => {
	const cli = path.join(__dirname, '..', 'bin', 'loenn-author.js');
	const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'loenn-author-'));
	const script = path.join(dir, 'empty.js');
	fs.writeFileSync(script, 'module.exports = 42;');
	assert.throws(() => execFileSync(process.execPath, [cli, script], { stdio: 'pipe' }));
	fs.rmSync(dir, { recursive: true, force: true });
});

let failed = 0;
for (const [name, fn] of tests) {
	try {
		fn();
		console.log(`ok   ${name}`);
	} catch (err) {
		failed++;
		console.log(`FAIL ${name}\n${err.stack}`);
	}
}
console.log(`${tests.length - failed}/${tests.length} passed`);
process.exit(failed ? 1 : 0);
