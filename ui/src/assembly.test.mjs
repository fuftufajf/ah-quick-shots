import assert from "node:assert/strict";
import {DISSOLVE, cutAt, grouped, laid, lengthOf, moved, musicGain, placed, stateOf, ticked, trimmed, under, without} from "./assembly.js";

const shots = [{id: "a", name: "Kill: Bandit · closing in", start: 10, end: 14}, {id: "b", name: "Kill: Bandit · guns", start: 14, end: 19},
  {id: "c", name: "Shot at 00:40.0", start: 40, end: 46}, {id: "d", name: "Take-off 00:02.0 · rolling", start: 2, end: 8}];
const video = (id, seconds, changed = false) => ({id, seconds, changed, file: `${id}.mp4`});
const versions = {a: [video("a2", 4), video("a1", 4)], b: [video("b1", 5, true)], c: [video("c1", 6)]};
const empty = {clips: [], off: [], music: null, sound: 1};

// A shot's state is its newest video's; one without a video of its own is still to render.
assert.deepEqual(shots.map(s => stateOf(s, versions)), ["rendered", "changed", "rendered", "none"]);

// Rendered shots come onto the timeline by themselves, in film order; a shot taken off stays off; a saved order is
// kept, and a shot rendered later goes after the last clip that begins no later in the film.
assert.deepEqual(placed(empty, shots, versions).map(c => c.shot), ["a", "b", "c"]);
assert.deepEqual(placed({...empty, off: ["b"]}, shots, versions).map(c => c.shot), ["a", "c"]);
const saved = {...empty, clips: [{shot: "c", video: null, start: 0, end: 0, join: "cut", sound: 1}, {shot: "a", video: "a1", start: 1, end: 0, join: "cut", sound: 1}]};
assert.deepEqual(placed(saved, shots, versions).map(c => c.shot), ["c", "a", "b"]);
assert.deepEqual(placed(saved, shots, {c: versions.c}).map(c => c.shot), ["c"]);  // a clip whose videos are gone drops out

// Each clip plays what trimming leaves of its video, one after another; a dissolve shares its length with the next.
const clips = placed(saved, shots, versions);
let rows = laid(clips, versions);
assert.deepEqual(rows.map(r => [r.version.id, r.seconds, r.at]), [["c1", 6, 0], ["a1", 3, 6], ["b1", 5, 9]]);
assert.equal(lengthOf(rows), 14);
rows = laid(clips.map((c, i) => i === 0 ? {...c, join: "dissolve"} : c), versions);
assert.deepEqual(rows.map(r => r.at), [0, 6 - DISSOLVE, 9 - DISSOLVE]);
assert.equal(laid([{...clips[0], join: "dissolve", end: 5.5}, clips[1]], versions)[1].at, .5);  // too short to dissolve: a cut
assert.deepEqual([under(rows, 0), under(rows, 5.8), under(rows, 99), under([], 1)], [0, 1, 2, -1]);

// Cut at the playhead: two clips of the same video, meeting where it was cut; never a sliver.
rows = laid(clips, versions);
const cut = cutAt(clips, rows, 7.5);
assert.deepEqual(cut.map(c => [c.shot, c.start, c.end]), [["c", 0, 0], ["a", 1, 1.5], ["a", 2.5, 0], ["b", 0, 0]]);
assert.equal(lengthOf(laid(cut, versions)), 14);
assert.equal(cutAt(clips, rows, 6.1), null);

// Trimming stops at the video's ends and leaves a fifth of a second.
assert.equal(trimmed(clips, rows, 0, "start", 2.5)[0].start, 2.5);
assert.equal(trimmed(clips, rows, 0, "start", 99)[0].start, 5.8);
assert.equal(trimmed(clips, rows, 0, "end", -1.5)[0].end, 1.5);
assert.equal(trimmed(clips, rows, 1, "start", -5)[1].start, 0);

assert.deepEqual(moved(clips, 2, 0).map(c => c.shot), ["b", "c", "a"]);
// The last clip of a shot taken off keeps the shot off; one of two leaves it on.
assert.deepEqual(without(empty, clips, 0).off, ["c"]);
assert.deepEqual(without(empty, cut, 1).off, []);
assert.deepEqual(ticked({...empty, off: ["b"]}, clips, "b", true).off, []);
assert.deepEqual(ticked(empty, cut, "a", false).clips.map(c => c.shot), ["c", "b"]);

const music = {in: 1, out: 3, level: .5};
assert.deepEqual([0, .5, 5, 12.5, 14].map(t => musicGain(music, t, 14)), [0, .5, 1, .5, 0]);
assert.equal(musicGain({in: 0, out: 0}, 0, 14), 1);

assert.deepEqual(grouped(shots).map(g => [g.head, g.shots.map(s => s.part)]),
  [["Take-off 00:02.0", ["rolling"]], ["Kill: Bandit", ["closing in", "guns"]], ["", ["Shot at 00:40.0"]]]);
console.log("OK: the assembly's clips, joins, cuts, trims and music");
