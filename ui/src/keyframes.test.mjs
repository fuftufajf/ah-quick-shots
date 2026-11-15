import assert from "node:assert/strict";
import {frameOf, glide, keyAt, keyBeside, keyable, looseness, reframe, toggleKey} from "./desk.js";

// The same numbers as the desk's own (test_director.py): held outside the keyframes, through each one, no halt on the way.
assert.deepEqual([1, 2, 3, 4, 5.5, 7, 9].map(t => Math.round(glide([2, 4, 7], [0, 10, 4], t) * 1e4) / 1e4), [0, 0, 4.8, 10, 7.3, 4, 4]);

const ship = {id: "s", kind: "escort", start: 10, end: 20, orbit: 30, distance: 400, height: 50, fov: 40, watch: "both", target: 10};
assert.ok(keyable(ship) && !keyable({kind: "pair"}) && !keyable(null));
assert.equal(frameOf(ship, 15), ship);  // no keyframes: the shot's own camera all along

// The first keyframe keeps the camera as it is; until there are keyframes a change is the whole shot's.
let shot = toggleKey(ship, 12);
assert.deepEqual(shot.keys, [{t: 12, orbit: 30, distance: 400, height: 50, fov: 40, watch: "both"}]);
assert.deepEqual(reframe(ship, 15, {...ship, distance: 900}), {...ship, distance: 900});
// With keyframes, a change further on is a keyframe there; a change at a keyframe changes that one.
shot = reframe(shot, 18, {...frameOf(shot, 18), orbit: -120, distance: 900});
assert.deepEqual(shot.keys.map(k => [k.t, k.orbit, k.distance]), [[12, 30, 400], [18, -120, 900]]);
shot = reframe(shot, 18.2, {...frameOf(shot, 18.2), fov: 70});
assert.deepEqual(shot.keys.map(k => [k.t, k.fov]), [[12, 40], [18, 70]]);
assert.deepEqual([keyAt(shot, 12.2), keyAt(shot, 15), keyAt(shot, 17.8)], [0, -1, 1]);
// Stepping from keyframe to keyframe passes over the one the playhead stands on, and stops at the ends.
assert.deepEqual([[15, -1], [15, 1], [12.2, 1], [12.2, -1], [18, 1], [18.1, -1], [20, -1]].map(([t, step]) => keyBeside(shot, t, step)?.t), [12, 18, 18, undefined, undefined, 12, 18]);
assert.equal(keyBeside(ship, 15, 1), undefined);
// Between them the camera is on its way, the short way round the aircraft; outside them it holds.
assert.deepEqual([10, 12, 15, 18, 20].map(t => frameOf(shot, t).distance), [400, 400, 650, 900, 900]);
assert.equal(frameOf(shot, 15).orbit, -45);
const round = {...ship, keys: [{t: 12, orbit: 170, distance: 400, height: 50, fov: 40}, {t: 14, orbit: -170, distance: 400, height: 50, fov: 40}]};
assert.equal(Math.abs(frameOf(round, 13).orbit), 180);
// Taken away one by one: the last one's camera stays with the shot, flying with the aircraft again.
shot = toggleKey({...shot, path: "own"}, 12);
assert.equal(shot.keys.length, 1);
shot = toggleKey(shot, 18);
assert.deepEqual([shot.keys, shot.path, shot.distance, shot.orbit, shot.fov], [undefined, undefined, 900, -120, 70]);

// Whom it watches is the keyframe's too: changed at the playhead it is that keyframe's, the shot's own stays for
// keyframes that do not say, and on the way from one to the next the sliders show the nearer one's.
shot = reframe({...ship, keys: [{t: 12, orbit: 30, distance: 400, height: 50, fov: 40}]}, 16, {...ship, watch: "target"});
assert.deepEqual([shot.watch, shot.keys.map(k => k.watch)], ["both", [undefined, "target"]]);
assert.deepEqual([11, 12, 13.9, 14.1, 16, 19].map(t => frameOf(shot, t).watch), ["both", "both", "both", "target", "target", "target"]);
assert.deepEqual([looseness(shot), looseness({...shot, watch: "hero", keys: [{...shot.keys[0], watch: "hero"}]})], [2, 1]);
assert.equal(toggleKey(toggleKey(shot, 12), 16).watch, "target");  // the last one taken away leaves the shot its own

assert.deepEqual([looseness(ship),looseness({...ship, loose: 7}), looseness({...ship, watch: "hero"}), looseness({...ship, distance: 900})], [2, 7, 1, 2]);
console.log("OK: the observer's keyframes at the playhead");
