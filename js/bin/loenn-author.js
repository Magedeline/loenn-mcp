#!/usr/bin/env node
'use strict';
/**
 * loenn-author <script.js> [--out file.json]
 *
 * Runs an authoring script and writes its authoring JSON to --out (or stdout).
 * The script can:
 *   - module.exports = plan                      (a Plan, Room, Room[] or plain JSON)
 *   - module.exports = async (api) => plan       (api = this package's exports)
 *   - call plan.emit()
 * Its own console output goes to stderr so stdout stays clean JSON.
 */
const fs = require('fs');
const path = require('path');
const api = require('..');

function usage(msg) {
	if (msg) process.stderr.write(msg + '\n');
	process.stderr.write('usage: loenn-author <script.js> [--out file.json]\n');
	process.exit(2);
}

function normalize(value) {
	if (value instanceof api.Plan) return value.toJSON();
	if (value instanceof api.Room) return new api.Plan([value]).toJSON();
	if (Array.isArray(value) && value.every((r) => r instanceof api.Room)) return new api.Plan(value).toJSON();
	if (value && typeof value === 'object' && (value.rooms || value.room)) return JSON.parse(JSON.stringify(value));
	return null;
}

async function main() {
	const args = process.argv.slice(2);
	let out = null;
	const files = [];
	for (let i = 0; i < args.length; i++) {
		if (args[i] === '--out') out = args[++i];
		else if (args[i] === '-h' || args[i] === '--help') usage();
		else files.push(args[i]);
	}
	if (files.length !== 1) usage('expected exactly one script');
	const script = path.resolve(files[0]);
	if (!fs.existsSync(script)) usage(`no such file: ${script}`);

	// Keep stdout for JSON: route the script's console.log to stderr.
	console.log = (...a) => process.stderr.write(a.map(String).join(' ') + '\n');

	let emitted = null;
	global.__loennAuthorEmit = (plan) => { emitted = plan.toJSON(); };

	let result = require(script);
	if (typeof result === 'function') result = await result(api);
	else if (result && typeof result.then === 'function') result = await result;

	const doc = normalize(result) || emitted;
	if (!doc) usage(`${path.basename(script)} did not export a Plan/Room or call plan.emit()`);
	const text = JSON.stringify(doc, null, 1) + '\n';
	if (out) fs.writeFileSync(out, text, 'utf8');
	else process.stdout.write(text);
}

main().catch((err) => {
	process.stderr.write((err && err.stack) || String(err));
	process.stderr.write('\n');
	process.exit(1);
});
