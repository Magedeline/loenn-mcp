'use strict';
/**
 * loenn-mcp-author: write Celeste map content in JavaScript.
 *
 * Builds the "authoring JSON" that loenn-mcp merges into a map
 * (merge_json_into_map / run_author_script). Nothing here reads or writes
 * .bin files; the Python server does that through its lossless writer.
 *
 * The API shape (new Room(position, data), Entity.Strawberry({ x, y }),
 * new Tiles(text)) follows lucid-dream by BloodSweatAndCode (MIT); see
 * THIRD_PARTY_NOTICES.md. No code is shared with it.
 */

const RESERVED = new Set(['name', 'x', 'y', 'width', 'height', 'nodes', 'placement', 'id']);

/** "Strawberry" -> "strawberry", "GoldenBerry" -> "goldenBerry"; namespaced names pass through. */
function placedName(prop) {
	if (prop.includes('/')) return prop;
	return prop.charAt(0).toLowerCase() + prop.slice(1);
}

function num(v, what) {
	if (typeof v !== 'number' || !Number.isFinite(v)) {
		throw new TypeError(`${what} must be a finite number, got ${JSON.stringify(v)}`);
	}
	return v;
}

class Item {
	/**
	 * @param {'entities'|'triggers'} section
	 * @param {string} name   Placed name, e.g. "strawberry" or "CEL/GreenBerry"
	 * @param {object} data   x, y and any attributes; optional width, height,
	 *                        nodes ([[x, y], ...]), placement, id
	 */
	constructor(section, name, data = {}) {
		if (typeof name !== 'string' || !name) throw new TypeError('an entity/trigger needs a name');
		this.section = section;
		this.name = name;
		this.data = Object.assign({}, data);
		num(this.data.x, `${name}.x`);
		num(this.data.y, `${name}.y`);
	}

	/** Copy with some fields changed: berry.at(64, 32) or berry.with({ winged: true }). */
	with(changes) {
		return new Item(this.section, this.name, Object.assign({}, this.data, changes));
	}

	at(x, y) {
		return this.with({ x, y });
	}

	toJSON() {
		const out = { name: this.name };
		for (const [k, v] of Object.entries(this.data)) {
			if (k === 'nodes') {
				out.nodes = v.map((n, i) => {
					const [nx, ny] = Array.isArray(n) ? n : [n.x, n.y];
					return [num(nx, `${this.name}.nodes[${i}].x`), num(ny, `${this.name}.nodes[${i}].y`)];
				});
			} else if (v !== undefined) {
				out[k] = v;
			}
		}
		return out;
	}
}

/**
 * Entity('CEL/GreenBerry', { x, y }) or new Entity(...), and
 * Entity.Strawberry({ x, y }) for any name (property names are camel-cased
 * to the placed name; use Entity('Mod/Name', ...) for namespaced ones).
 */
function makeFactory(section) {
	function Factory(name, data) {
		return new Item(section, name, data);
	}
	return new Proxy(Factory, {
		get(target, prop, receiver) {
			if (typeof prop !== 'string' || prop in target) return Reflect.get(target, prop, receiver);
			const name = placedName(prop);
			return (data) => new Item(section, name, data);
		},
		construct(target, args) {
			return new Item(section, args[0], args[1]);
		},
	});
}

const Entity = makeFactory('entities');
const Trigger = makeFactory('triggers');

/** A tile grid written as text, one row per line (like lucid-dream's Tiles). */
class Tiles {
	constructor(text = '') {
		this.text = String(text).replace(/^\r?\n/, '').replace(/\r\n/g, '\n').replace(/\n$/, '');
	}

	get rows() {
		return this.text ? this.text.split('\n') : [];
	}

	toString() {
		return this.text;
	}

	toJSON() {
		return this.text;
	}
}

class Room {
	/**
	 * Add to an existing room:   new Room('a-05')
	 * Create a new room:         new Room([x, y], { name: 'a-99', size: [40, 23], fgTiles, entities })
	 *                            (position in pixels, size in tiles, as in lucid-dream;
	 *                            or width/height in pixels)
	 */
	constructor(nameOrPosition, data = {}) {
		this.entities = [];
		this.triggers = [];
		this.attrs = {};
		if (typeof nameOrPosition === 'string') {
			this.name = nameOrPosition;
			this.position = null;
		} else {
			if (!Array.isArray(nameOrPosition) || nameOrPosition.length !== 2) {
				throw new TypeError('Room takes a room name, or an [x, y] position for a new room');
			}
			this.position = nameOrPosition.map((v, i) => num(v, `room position[${i}]`));
			this.name = data.name;
			if (typeof this.name !== 'string' || !this.name) throw new TypeError('a new room needs data.name');
			const size = data.size || [40, 23];
			this.width = data.width !== undefined ? num(data.width, 'width') : size[0] * 8;
			this.height = data.height !== undefined ? num(data.height, 'height') : size[1] * 8;
			this.fgTiles = data.fgTiles || data.solids || null;
			this.bgTiles = data.bgTiles || data.bg || null;
			const skip = new Set(['name', 'size', 'width', 'height', 'fgTiles', 'solids', 'bgTiles', 'bg',
				'entities', 'triggers']);
			for (const [k, v] of Object.entries(data)) if (!skip.has(k)) this.attrs[k] = v;
		}
		for (const e of data.entities || []) this.add(e);
		for (const t of data.triggers || []) this.add(t);
	}

	/** Add Items (entities go to entities, triggers to triggers). Chainable. */
	add(...items) {
		for (const it of items.flat()) {
			if (!(it instanceof Item)) throw new TypeError('Room.add takes Entity(...) / Trigger(...) items');
			(it.section === 'triggers' ? this.triggers : this.entities).push(it);
		}
		return this;
	}

	entity(name, data) {
		return this.add(new Item('entities', name, data));
	}

	trigger(name, data) {
		return this.add(new Item('triggers', name, data));
	}

	toJSON() {
		const out = { room: this.name };
		if (this.position) {
			out.x = this.position[0];
			out.y = this.position[1];
			out.width = this.width;
			out.height = this.height;
			if (this.fgTiles) out.solids = String(this.fgTiles);
			if (this.bgTiles) out.bg = String(this.bgTiles);
			if (Object.keys(this.attrs).length) out.attrs = this.attrs;
		}
		if (this.entities.length) out.entities = this.entities.map((e) => e.toJSON());
		if (this.triggers.length) out.triggers = this.triggers.map((t) => t.toJSON());
		return out;
	}
}

class Plan {
	constructor(rooms = []) {
		this.rooms = [];
		for (const r of rooms) this.addRoom(r);
	}

	addRoom(room) {
		if (!(room instanceof Room)) throw new TypeError('Plan.addRoom takes a Room');
		this.rooms.push(room);
		return room;
	}

	/** The Room for `name`, created on first use (for adding to an existing room). */
	room(name) {
		let r = this.rooms.find((x) => x.name === name);
		if (!r) r = this.addRoom(new Room(name));
		return r;
	}

	toJSON() {
		return { format: 'loenn-mcp/authoring', version: 1, rooms: this.rooms.map((r) => r.toJSON()) };
	}

	toString() {
		return JSON.stringify(this.toJSON(), null, 1);
	}

	/** Print the authoring JSON (or hand it to the loenn-author CLI when run through it). */
	emit() {
		if (typeof global.__loennAuthorEmit === 'function') global.__loennAuthorEmit(this);
		else process.stdout.write(this.toString() + '\n');
		return this;
	}
}

/** Evenly spaced points from (x0, y0) to (x1, y1), inclusive: for rows of berries, spinners, etc. */
function line(x0, y0, x1, y1, count) {
	if (count < 1) return [];
	if (count === 1) return [[x0, y0]];
	return Array.from({ length: count }, (_, i) => {
		const t = i / (count - 1);
		return [Math.round(x0 + (x1 - x0) * t), Math.round(y0 + (y1 - y0) * t)];
	});
}

module.exports = { Plan, Room, Entity, Trigger, Tiles, Item, line, placedName };
