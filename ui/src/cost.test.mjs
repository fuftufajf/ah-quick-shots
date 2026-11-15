import assert from "node:assert/strict";
import {howLong, renderCost, videosOf} from "./desk.js";

// Two takes of one stretch cannot share a video; asked apart, every shot is one.
const shots = [{start: 10, end: 14}, {start: 12, end: 16}, {start: 20, end: 26}];
assert.deepEqual([videosOf(shots, false), videosOf(shots, true), videosOf([], false)], [2, 3, 0]);

// Every effect has its price whether it is ticked or not; the total is the plain render and the ticked ones.
const ask = {film: 30, videos: 2, quality: "final"};
const plain = renderCost(ask), all = renderCost({...ask, blur: true, look: true, haze: true, trails: true});
assert.equal(plain.total, plain.render);
assert.ok(["blur", "look", "haze", "trails"].every(effect => plain[effect] > 0 && plain[effect] === all[effect]));
assert.ok(Math.abs(all.total - (all.render + all.blur + all.look + all.haze + all.trails)) < 1e-9);
assert.ok(Math.abs(renderCost({...ask, look: true}).total - (plain.render + plain.look)) < 1e-9);
// Motion blur is the dearest: the viewer renders the film four times. A lasting trail costs more than a short one,
// a larger picture more than a smaller, more film more than less.
assert.ok(plain.blur > plain.trails && plain.trails > plain.look);
assert.ok(renderCost({...ask, trailSeconds: 30}).trails > plain.trails);
assert.ok(renderCost({...ask, quality: "4k"}).total > plain.total && plain.total > renderCost({...ask, quality: "draft"}).total);
assert.ok(renderCost({...ask, film: 60}).look === 2 * plain.look);
// The desk's pace for this PC scales all of it.
assert.ok(Math.abs(renderCost({...ask, blur: true}, 1.5).total - 1.5 * renderCost({...ask, blur: true}).total) < 1e-9);

assert.deepEqual([3, 47, 89, 95, 160, 3599, 3700, 7260].map(howLong), ["5 s", "45 s", "90 s", "1 min 40 s", "2 min 40 s", "1 h 0 min", "1 h 2 min", "2 h 1 min"]);
console.log("OK: what a render costs, an effect at a time and together");
