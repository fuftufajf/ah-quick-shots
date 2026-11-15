import assert from "node:assert/strict";
import {cutCost, howLong, renderCost, videoRows} from "./desk.js";

// Two takes of one stretch cannot share a video; asked apart, every shot is one.
const shots = [{start: 10, end: 14}, {start: 12, end: 16}, {start: 20, end: 26}].map(s => ({...s, effects: {}}));
assert.deepEqual([videoRows(shots, false).length, videoRows(shots, true).length, videoRows([], false).length], [2, 3, 0]);
// A video is cut where the effects change (as the desk deals them): the plain shot after the blurred one is a video of
// its own, and each video costs what its own effects do.
const mixed = [{start: 0, end: 4, effects: {blur: true}}, {start: 4, end: 6, effects: {blur: true, overlay: false}}, {start: 6, end: 9, effects: {}}];
assert.deepEqual(videoRows(mixed, false).map(row => row.map(s => s.start)), [[0, 4], [6]]);
const blurred = renderCost({film: 6, videos: 1, quality: "final", blur: true}), bare = renderCost({film: 3, videos: 1, quality: "final"});
const both = cutCost(videoRows(mixed, false), "final", 30, true);
assert.deepEqual([both.film, both.videos], [9, 2]);
assert.ok(Math.abs(both.total - blurred.total - bare.total) < 1e-9 && Math.abs(both.render - blurred.render - bare.render) < 1e-9);
assert.equal(cutCost(videoRows(mixed, false), "final", 30, false).total, both.render);  // without ffmpeg no effect is made

// Every effect has its price whether it is ticked or not; the total is the plain render and the ticked ones.
const ask = {film: 30, videos: 2, quality: "final", fps: 60};
const plain = renderCost(ask), all = renderCost({...ask, blur: true, look: true, haze: true, trails: true});
assert.equal(plain.total, plain.render);
assert.ok(["blur", "look", "haze", "trails"].every(effect => plain[effect] > 0 && plain[effect] === all[effect]));
assert.ok(Math.abs(all.total - (all.render + all.blur + all.look + all.haze + all.trails)) < 1e-9);
assert.ok(Math.abs(renderCost({...ask, look: true}).total - (plain.render + plain.look)) < 1e-9);
// At 60 fps motion blur is the dearest: the viewer renders the film four times. A lasting trail costs more than a
// short one, a larger picture more than a smaller, more film more than less.
assert.ok(plain.blur > plain.trails && plain.trails > plain.look);
assert.ok(renderCost({...ask, trailSeconds: 30}).trails > plain.trails);
assert.ok(renderCost({...ask, quality: "4k"}).total > plain.total && plain.total > renderCost({...ask, quality: "draft"}).total);
assert.ok(renderCost({...ask, film: 60}).look === 2 * plain.look);
// At 30 fps, the default, ffmpeg's work and the trails' are half, and motion blur is two passes of the viewer.
const film = renderCost({...ask, fps: 30});
assert.ok(Math.abs(film.look - plain.look / 2) < 1e-9 && Math.abs(film.trails - plain.trails / 2) < 1e-9);
assert.ok(film.blur > 0 && film.blur < plain.blur / 2 && film.render < plain.render);
// The desk's pace for this PC scales all of it.
assert.ok(Math.abs(renderCost({...ask, blur: true}, 1.5).total - 1.5 * renderCost({...ask, blur: true}).total) < 1e-9);

assert.deepEqual([3, 47, 89, 95, 160, 3599, 3700, 7260].map(howLong), ["5 s", "45 s", "90 s", "1 min 40 s", "2 min 40 s", "1 h 0 min", "1 h 2 min", "2 h 1 min"]);
console.log("OK: what a render costs, an effect at a time and together");
