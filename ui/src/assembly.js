// The assembly: the film's rendered shots laid in a row, trimmed, cut and set to music. Everything here is plain
// arithmetic on {clips, off, music, sound} (see Desk._assembly); the page and its tests share it.
export const DISSOLVE = .3;  // seconds two clips share when one dissolves into the next (finishing.py has the same)
export const SHORTEST = .2;  // what trimming leaves of a clip at least
const tidy = seconds => Math.round(seconds * 100) / 100;

// Where a shot stands: "none" (no video of its own yet), "rendered", or "changed" (its newest video shows another
// camera or stretch than the shot now has).
export function stateOf(shot, versions) {
  const mine = versions[shot.id];
  return !mine?.length ? "none" : mine[0].changed ? "changed" : "rendered";
}

// The clips on the timeline: the saved ones whose shot still has a video, and every rendered shot that is neither
// among them nor taken off, put where it falls in the film. So a shot rendered a moment ago is on the timeline.
export function placed(assembly, shots, versions) {
  const starts = new Map(shots.map(s => [s.id, s.start]));
  const clips = (assembly?.clips || []).filter(c => starts.has(c.shot) && versions[c.shot]?.length);
  const off = new Set(assembly?.off || []), there = new Set(clips.map(c => c.shot));
  for (const shot of [...shots].sort((a, b) => a.start - b.start)) {
    if (there.has(shot.id) || off.has(shot.id) || !versions[shot.id]?.length) continue;
    let at = 0;  // after the last clip whose shot begins no later
    clips.forEach((c, i) => { if (starts.get(c.shot) <= shot.start) at = i + 1; });
    clips.splice(at, 0, {shot: shot.id, video: null, start: 0, end: 0, join: "cut", sound: 1});
  }
  return clips;
}

// Each clip with its video and its place: {...clip, version, seconds (what plays of it), at (where it begins on the
// timeline)}. A dissolve shares DISSOLVE with the next clip, unless either is too short for it: then it is a cut.
export function laid(clips, versions) {
  const rows = clips.map(clip => {
    const mine = versions[clip.shot] || [], version = mine.find(v => v.id === clip.video) || mine[0];
    return {...clip, version, seconds: Math.max(0, tidy(version.seconds - clip.start - clip.end))};
  });
  let at = 0;
  rows.forEach((row, i) => {
    row.at = at;
    const next = rows[i + 1];
    at = tidy(at + row.seconds - (next && row.join === "dissolve" && Math.min(row.seconds, next.seconds) > 2 * DISSOLVE ? DISSOLVE : 0));
  });
  return rows;
}

export const lengthOf = rows => rows.length ? tidy(rows.at(-1).at + rows.at(-1).seconds) : 0;

// The clip under a moment of the timeline (the later one where two share a dissolve); -1 with no clips.
export function under(rows, time) {
  for (let i = rows.length - 1; i >= 0; i--) if (rows[i].at <= time + 1e-6) return i;
  return rows.length ? 0 : -1;
}

// Where the playhead jumps from `time`, back (step -1) or on (1): to the cut before or after it, every clip's start
// and end, and the start and end of the whole. undefined when there is none that way.
export function cutBeside(rows, time, step) {
  const marks = [...new Set([0, ...rows.flatMap(r => [r.at, tidy(r.at + r.seconds)]), lengthOf(rows)])].sort((a, b) => a - b);
  return step < 0 ? marks.findLast(m => m < time - .01) : marks.find(m => m > time + .01);
}

// The clip under the playhead cut in two there; null when the cut would leave a piece shorter than SHORTEST.
export function cutAt(clips, rows, time) {
  const i = under(rows, time), row = rows[i];
  if (!row) return null;
  const local = tidy(time - row.at);
  if (local < SHORTEST || row.seconds - local < SHORTEST) return null;
  const first = {...clips[i], end: tidy(row.version.seconds - clips[i].start - local), join: "cut"};
  return [...clips.slice(0, i), first, {...clips[i], start: tidy(clips[i].start + local)}, ...clips.slice(i + 1)];
}

// One end of a clip moved by `seconds` (later is positive), never past what its video holds or under SHORTEST.
export function trimmed(clips, rows, index, edge, seconds) {
  const clip = clips[index], room = rows[index].version.seconds - SHORTEST;
  const value = edge === "start" ? Math.min(room - clip.end, Math.max(0, clip.start + seconds))
    : Math.min(room - clip.start, Math.max(0, clip.end - seconds));
  return clips.map((c, i) => i === index ? {...c, [edge]: tidy(value)} : c);
}

export function moved(clips, from, to) {
  const next = [...clips], [clip] = next.splice(from, 1);
  next.splice(to, 0, clip);
  return next;
}

// A clip taken off the timeline. Its shot is kept off (not put back by `placed`) once no clip of it is left.
export function without(assembly, clips, index) {
  const next = clips.filter((_c, i) => i !== index), shot = clips[index].shot;
  const off = next.some(c => c.shot === shot) ? assembly.off : [...new Set([...assembly.off, shot])];
  return {...assembly, clips: next, off};
}

// A shot put on the timeline or taken off it whole (the tick beside it in the list).
export function ticked(assembly, clips, shot, on) {
  return on ? {...assembly, clips, off: assembly.off.filter(id => id !== shot)}
    : {...assembly, clips: clips.filter(c => c.shot !== shot), off: [...new Set([...assembly.off, shot])]};
}

// How loud the music is at a moment, 0 to 1 of its level: faded in at the start and out at the end of the video.
export function musicGain(music, time, length) {
  const up = music.in > 0 ? Math.min(1, time / music.in) : 1;
  const down = music.out > 0 ? Math.min(1, (length - time) / music.out) : 1;
  return Math.max(0, Math.min(up, down));
}

// The shots in film order under the name they share before " · " (a kill's four shots, a take-off's): [{head, time, shots}].
export function grouped(shots) {
  const groups = [];
  for (const shot of [...shots].sort((a, b) => a.start - b.start)) {
    const [head, part] = shot.name.includes(" · ") ? [shot.name.split(" · ")[0], shot.name.split(" · ").slice(1).join(" · ")] : ["", shot.name];
    if (!groups.length || groups.at(-1).head !== head || !head) groups.push({head, time: shot.start, shots: []});
    groups.at(-1).shots.push({...shot, part});
  }
  return groups;
}
