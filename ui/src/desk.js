export function formatTime(value) {
  const n = Math.max(0, Number(value) || 0);
  return `${String(Math.floor(n / 60)).padStart(2, "0")}:${(n % 60).toFixed(1).padStart(4, "0")}`;
}

const idValue = value => /^\d+$/.test(String(value)) ? Number(value) : null;

export function newShot(project, planes, moment, now = 0) {
  const at = moment?.time ?? now, kind = moment?.kind, f = moment?.fields || [], fight = moment?.fight;
  const recorder = planes.find(p => p.recorder)?.id ?? planes[0]?.id ?? null;
  // An event carries the ids as they were at that moment; the recorder's own id changes between sorties.
  const hero = (kind === "kill" ? idValue(f[3]) : ["impact", "hit", "drop"].includes(kind) ? idValue(f[0]) : fight?.me) ?? recorder;
  const target = kind === "kill" ? idValue(f[4]) : kind === "impact" ? idValue(f[1]) : fight?.opponent ?? null;
  // A kill is held past its message: the victim's last place stays in frame while he goes down.
  // A bomb is ridden from just before its release to the blast.
  const [before, after] = !moment ? [0, 2] : kind === "drop" ? [1, 14] : kind === "kill" || fight?.kills.length ? [5, 2.5] : fight ? [4, 3] : [5, 2];
  const tidy = seconds => Math.round(seconds * 100) / 100;  // 64.1 - 5 is 59.099999999999994
  const start = tidy(Math.min(Math.max(0, at - before), Math.max(0, project.length - .1)));
  const camera = ANGLES.find(a => a.key === (kind === "drop" ? "weapon" : target != null ? "you" : "behind")).make({lead: hero, other: target})[0];
  return {id: crypto.randomUUID(),
    name: kind === "kill" ? `${f[0]} → ${f[1]}` : fight ? fight.title : kind === "drop" ? `Bomb run ${formatTime(at)}` : `Shot at ${formatTime(at)}`,
    start, end: tidy(Math.min(project.length, at + after)),
    side: "right", orbit: 0, distance: 120, height: 24, fov: 60, roll: 0, watch: "both", ...camera};
}

// The shots a kill usually wants, after Steely's kill sequence (Aces High film companion): closing in, the
// shooting, him going down, you flying on. The victim leaves the film at the kill message, so the camera on him
// ends there. The shooting is your last burst that hit him (on a real kill it came 11 s before the message: he
// crashed later); a film without one gets Steely's rule, the fatal hits about 3 s before the message. His cameras
// were swapped for ones that keep the victim in the picture (seen live: the pilot's view looked at the sky as he
// flew it, the wingtip camera lost the victim, and neither your seat nor your shoulder showed him while you closed in):
// you in front with him beyond, over your shoulder at him for the guns, and close on him from above as he falls.
export function killSequence(project, planes, kill, events = []) {
  const base = newShot(project, planes, kill, kill.time), t = kill.time, you = base.hero, him = base.target;
  const tidy = seconds => Math.round(Math.min(Math.max(0, seconds), project.length) * 100) / 100;
  const mine = (e, kind) => e.kind === kind && idValue(e.fields[kind === "guns" ? 1 : 0]) === you;
  const hit = events.filter(e => mine(e, "impact") && idValue(e.fields[1]) === him && e.time <= t && e.time >= t - 20).at(-1);
  const opened = hit && events.filter(e => mine(e, "guns") && e.fields[0] === "start" && e.time <= hit.time).at(-1);
  const ceased = hit && events.find(e => mine(e, "guns") && e.fields[0] === "stop" && e.time >= hit.time);
  const fire = hit ? opened?.time ?? hit.time - .5 : t - 4, held = hit ? ceased?.time ?? hit.time + .5 : t - 2;
  const a = tidy(fire - 4), b = tidy(Math.max(a + 1, fire)), c = tidy(Math.max(b + 1.5, held + 1)), down = tidy(Math.max(c, t - 6));
  const end = tidy(Math.max(down, t)), away = tidy(end + 2.5);
  const shot = (part, start, stop, camera) => ({...base, id: crypto.randomUUID(), name: `${base.name} · ${part}`, start, end: stop, ...camera});
  return [
    shot("closing in", a, b, him == null ? {kind: "follow", target: null, orbit: 0, distance: 120, height: 15, fov: 50}
      : ANGLES[0].make({lead: you, other: him})[0]),
    shot("guns", b, c, him == null ? {kind: "cockpit", target: null, fov: 69} : {kind: "shoulder", target: him, side: "right", fov: 66}),
    // Well above him: he may hit the ground before the message, and a camera at his height ends up in the grass.
    ...(him == null ? [] : [shot("going down", down, end,
      {kind: "escort", watch: "hero", hero: him, target: you, orbit: 145, distance: 110, height: 60, fov: 28})]),
    shot("flying on", end, away, {kind: "follow", target: null, orbit: 160, distance: 160, height: 25, fov: 55}),
  ].filter(s => s.end - s.start >= .5);
}

// A take-off and a landing found in your flight path by the desk ({kind, time, roll or stop, hero}), each as a
// few shots: the take-off rolling past a camera beside the runway ahead, the wheels, the lift-off with a camera
// swinging round you and closing in, the climb seen from a camera falling back behind you; the landing coming at a camera ahead of you, the
// wheels touching, the roll-out with a camera swinging round and falling back. Near the ground every camera is at or above your
// own height, so it stays out of the ground (whose height is known only under your wheels).
function airfieldShots(project, moment, name, parts) {
  const base = {side: "right", orbit: 0, distance: 120, height: 24, fov: 60, roll: 0, watch: "both", hero: moment.hero, target: null};
  const tidy = seconds => Math.round(Math.min(Math.max(0, seconds), project.length) * 100) / 100;
  return parts.map(([part, start, end, camera]) => ({...base, id: crypto.randomUUID(), name: `${name} · ${part}`,
    start: tidy(start), end: tidy(end), ...camera})).filter(s => s.end - s.start >= .5);
}
export function takeoffSequence(project, moment) {
  const t = moment.time, a = Math.max(moment.roll, t - 14), b = Math.max(a, t - 6), c = Math.max(b, t - 1);
  return airfieldShots(project, moment, `Take-off ${formatTime(t)}`, [
    ["rolling", a, b, {kind: "fixed", orbit: 150, distance: 250, height: 8, fov: 35}],
    ["wheels", b, c, {kind: "gear", fov: 66}],
    ["lift-off", c, t + 6, {kind: "escort", watch: "hero", orbit: 60, sweep: 100, distance: 250, closer: 100, height: 15, fov: 40}],
    // From ahead and a little below, swinging round behind you and falling back: you climb away against the sky.
    // 20 ft below is safe here: 6 s after lift-off a fighter was 160 ft up (measured).
    ["climbing out", t + 6, t + 12, {kind: "escort", watch: "hero", orbit: 150, sweep: -150, distance: 150, closer: 500, height: -20, fov: 35}],
  ]);
}
export function landingSequence(project, moment) {
  // The roll-out runs on 2 s past the stop when the film does: the camera finishes its swing round you standing.
  const t = moment.time, end = Math.max(t + 4.5, Math.min(moment.stop + 2, t + 10));
  return airfieldShots(project, moment, `Landing ${formatTime(t)}`, [
    ["approach", t - 12, t - 2, {kind: "follow", orbit: 150, distance: 160, height: 10, fov: 50}],
    ["touchdown", t - 2, t + 1.5, {kind: "gear", fov: 66}],
    // Like the climb after a take-off: from ahead, swinging round and falling back; above you, you are on the ground.
    ["roll-out", t + 1.5, end, {kind: "escort", watch: "hero", orbit: 160, sweep: -120, distance: 120, closer: 300, height: 15, fov: 40}],
  ]);
}

// The saved shots of a sequence that `shot` belongs to: the same name before " · ", following one another with
// gaps of under 20 s (a kill's guns and his fall can be 14 s apart; two kills of one pilot are further).
export function sequenceOf(shot, shots) {
  const head = shot.name.split(" · ")[0];
  if (!shot.name.includes(" · ")) return [];
  const same = shots.filter(s => s.name.split(" · ")[0] === head).sort((a, b) => a.start - b.start);
  let at = same.findIndex(s => s.id === shot.id), from = at, to = at;
  if (at < 0) return [];
  while (from > 0 && same[from].start - same[from - 1].end < 20 && same[from - 1].end <= same[from].start + .01) from--;
  while (to < same.length - 1 && same[to + 1].start - same[to].end < 20 && same[to + 1].start >= same[to].end - .01) to++;
  return same.slice(from, to + 1);
}

// What a preview clip shows: the stretch, the shot's camera (not its name) and the picture switches.
export function clipKey(start, end, shot, flags) {
  const {id, name, ...camera} = shot || {};
  // Sorted: a shot read back from the plan lists the same fields in another order.
  return JSON.stringify([start, end, shot ? Object.keys(camera).sort().map(key => [key, camera[key]]) : null, flags]);
}

// A shot begun at a new moment stands beside the saved ones, not on top of them. (Another take of a saved
// stretch is made on purpose, with New shot, and keeps that stretch whole.)
export function fitShot(shot, shots) {
  let {start, end} = shot;
  for (const s of shots) {
    if (s.id === shot.id || s.end <= start || s.start >= end) continue;
    if (s.start <= start) start = s.end; else end = s.start;
  }
  return {...shot, start, end};
}

// Drag only one edge, snapping to full seconds; the film, neighbours and native 120 s limit take priority.
export function trimShot(shot, edge, seconds, length, shots) {
  const gap = Math.min(.5, shot.end - shot.start);
  let low = edge === "start" ? Math.max(0, shot.end - 120) : shot.start + gap;
  let high = edge === "start" ? shot.end - gap : Math.min(length, shot.start + 120);
  for (const other of shots) {
    if (other.id === shot.id) continue;
    if (edge === "start" && other.end <= shot.start) low = Math.max(low, other.end);
    if (edge === "end" && other.start >= shot.end) high = Math.min(high, other.start);
  }
  return {...shot, [edge]: clamp(Math.round(seconds), low, high)};
}

// "You" is the recording pilot whenever they are one of the shot's two aircraft.
export function castOf(shot, planes) {
  const you = planes.find(p => p.recorder)?.id;
  return shot.target != null && shot.target === you ? {lead: shot.target, other: shot.hero} : {lead: shot.hero, other: shot.target};
}

// Cameras that stand anywhere round an aircraft: `orbit` degrees round it, `distance` and `height` feet.
export const FREE = ["pair", "follow", "fixed", "ambush", "escort"];
// The lens that makes a 38 ft fighter a quarter of the picture's width from that far.
export const lens = distance => Math.round(2 * Math.atan(38 / (distance * .5)) * 180 / Math.PI);
// Close first: unrated, the nearer aircraft should dominate the picture. Further back the lens grows longer, so
// the aircraft in front stays as big while the one beyond it grows: a long lens is what makes a far opponent read.
// The camera stands the same few feet aside of the line through both, and above it, from every distance: that
// keeps the nearer aircraft at the same place in the picture, and the far one inside even the longest lens.
const PAIR = [120, 300, 700].flatMap(distance => [[60, 20], [-60, 20], [30, 12], [-30, 12]].map(([aside, height]) =>
  ({orbit: Math.round(Math.asin(aside / distance) * 1800 / Math.PI) / 10, distance, height, fov: lens(distance)})));
const SIDES = ["right", "left"];
// Each camera offers a few variants; rating picks the one that reads best. `rate: false` cameras look at your own
// aircraft, so there is nothing to measure; `drop` needs a bomb, rocket or torpedo leaving in the shot.
export const ANGLES = [
  {key: "you", label: "You in front", hint: "You big, opponent beyond", pair: true,
    make: c => PAIR.map(variant => ({kind: "pair", hero: c.lead, target: c.other, ...variant}))},
  {key: "them", label: "Opponent in front", hint: "Opponent big, you beyond", pair: true,
    make: c => PAIR.map(variant => ({kind: "pair", hero: c.other, target: c.lead, ...variant}))},
  // A third aircraft flying with you on a line of its own, the further off the heavier, stationed so that the
  // opponent is beyond you and a little aside. `fov` is its longest lens: it opens by itself to hold both.
  // From 300 ft it stops following your every turn (measured on a 44 s turning fight: at 120 ft it stayed
  // 75 to 142 ft from you, at 400 ft 228 to 517 ft, with both in the picture 92% of the time).
  {key: "escort", label: "Observer", hint: "A third aircraft on its own line, filming you both", pair: true,
    make: c => [35, -35, 70, -70].flatMap(orbit => [[400, 35], [300, 45], [550, 28]].map(([distance, fov]) =>
      ({kind: "escort", hero: c.lead, target: c.other, orbit, distance, height: Math.round(distance * .12), fov})))},
  // The same ship watching one aircraft alone, from any side and as close as the lens likes: the viewer holds
  // that aircraft in the middle of the picture, so its detail fills the frame. Front quarter first.
  {key: "closeYou", label: "Close on you", hint: "Your aircraft big, from any side", rate: false,
    make: c => [145, -145, 35, -35, 90, -90].map(orbit =>
      ({kind: "escort", watch: "hero", hero: c.lead, target: c.other ?? null, orbit, distance: 90, height: 12, fov: 30}))},
  {key: "closeThem", label: "Close on opponent", hint: "His aircraft big, from any side", pair: true, rate: false,
    make: c => [145, -145, 35, -35, 90, -90].map(orbit =>
      ({kind: "escort", watch: "hero", hero: c.other, target: c.lead, orbit, distance: 110, height: 14, fov: 28}))},
  {key: "shoulder", label: "Over your shoulder", hint: "Your canopy close, him beyond", pair: true,
    make: c => SIDES.map(side => ({kind: "shoulder", hero: c.lead, target: c.other, side, fov: 66}))},
  {key: "wing", label: "From your wing", hint: "Wingtip camera locked on him", pair: true,
    make: c => SIDES.map(side => ({kind: "wing", hero: c.lead, target: c.other, side, height: 3, fov: 52}))},
  {key: "behind", label: "Behind you", hint: "On your tail through every turn", rate: false,
    make: c => [{kind: "follow", hero: c.lead, target: null, orbit: 0, distance: 80, height: 16, fov: 65}]},
  {key: "flyby", label: "Fly-by", hint: "Camera stands still as you pass",
    make: c => [90, -90].map(orbit => ({kind: "fixed", hero: c.lead, target: null, orbit, distance: 140, height: 10, fov: 60}))},
  // Hung behind and beside where he will be at mid-shot, a little above, on a longer lens: he flies into the
  // frame, and at a kill that is where he goes down.
  {key: "ambush", label: "Opponent fly-by", hint: "Camera waits on his path", pair: true,
    make: c => [17, -17].map(orbit => ({kind: "ambush", hero: c.lead, target: c.other, orbit, distance: 470, height: 70, fov: 40}))},
  {key: "recorded", label: "Pilot's view", hint: "As you saw it, head movements and all", rate: false,
    make: c => [{kind: "recorded", hero: c.lead, target: null}]},
  {key: "cockpit", label: "Cockpit", hint: "Your seat, looking straight ahead",
    make: c => [{kind: "cockpit", hero: c.lead, target: c.other, fov: 69}]},
  {key: "nose", label: "On your nose", hint: "Looking back at you", rate: false,
    make: c => [{kind: "nose", hero: c.lead, target: null, side: "right", fov: 77}]},
  {key: "belly", label: "Under your belly", hint: "The ground rushing below", rate: false,
    make: c => [{kind: "belly", hero: c.lead, target: null, side: "right", fov: 72}]},
  {key: "gear", label: "At your gear", hint: "Take-offs and landings", rate: false,
    make: c => SIDES.map(side => ({kind: "gear", hero: c.lead, target: null, side, fov: 66}))},
  {key: "weapon", label: "Ride the bomb", hint: "From the release to the blast", rate: false, drop: true,
    make: c => [{kind: "weapon", hero: c.lead, target: null}]},
];

export function angleOf(shot, cast) {
  if (shot.kind === "pair") return shot.hero === cast.lead ? "you" : "them";
  if (shot.kind === "escort" && shot.watch === "hero") return shot.hero === cast.lead ? "closeYou" : "closeThem";
  return {follow: "behind", fixed: "flyby"}[shot.kind] || shot.kind;
}

const clamp = (value, low, high) => Math.max(low, Math.min(high, value));
// A drag on the picture moves the camera: the scene follows the hand, so the camera goes the other way round the
// aircraft, and up when the hand goes down. A long lens moves finer. `width` is the picture's in pixels.
export function dragCamera(shot, dx, dy, width) {
  // Off the line through both aircraft, degrees count the other way round than round one aircraft's line of flight.
  const way = shot.kind === "pair" || (shot.kind === "escort" && shot.watch !== "hero") ? 1 : -1;
  const orbit = shot.orbit + way * dx * shot.fov / (width * .4);
  return {...shot, orbit: Math.round(((((orbit + 180) % 360) + 360) % 360 - 180) * 10) / 10,
    height: Math.round(clamp(shot.height + dy * shot.distance / (width * .35), -2000, 5000))};
}
// One notch of the wheel zooms the lens: the aircraft grows without the camera going nearer, which would distort it.
export function zoomCamera(shot, closer) {
  const fov = shot.fov * (closer ? .9 : 1.1);
  return {...shot, fov: clamp(Math[closer ? "floor" : "ceil"](fov), 6, 140)};  // a whole degree at least, also on a long lens
}

export function candidates(shot, cast) {
  return ANGLES.filter(a => a.rate !== false && (!a.pair || cast.other != null))
    .flatMap(a => a.make(cast).map((variant, i) => ({...shot, ...variant, id: `${a.key}:${i}`})));
}

// scores: {candidate id: {score, size, quality}} from the desk's proposals. The variant that reads best wins
// (both aircraft clear, apart and big enough); on a tie, the one with the bigger aircraft in front.
export function bestVariant(angle, cast, scores) {
  const variants = angle.make(cast), at = i => scores[`${angle.key}:${i}`], worth = rating => rating.quality ?? rating.score;
  let best = 0;
  variants.forEach((_variant, i) => {
    const a = at(i), b = at(best);
    if (a && b && (worth(a) > worth(b) || (worth(a) === worth(b) && a.size > b.size))) best = i;
  });
  return {variant: variants[best], rating: at(best)};
}

export function ratingLabel(angle, cast, rating) {
  if (rating?.score == null) return angle.hint;
  const what = ["cockpit", "wing", "shoulder", "ambush"].includes(angle.key) ? "Opponent in view" : !angle.pair || cast.other == null ? "You in frame" : "Both in frame";
  return `${what} ${Math.round(rating.score * 100)}%`;
}

// The recording pilot's own fight: their kills and deaths, hits given and taken, their drops and landings.
// A film's kill list is the whole arena's; most of it happened far from the camera.
export function isMine(event, you) {
  const f = event.fields, me = value => value === "you" || (you && (value === you.name || value === String(you.id)));
  if (event.kind === "kill") return me(f[0]) || me(f[1]) || me(f[3]) || me(f[4]);
  if (event.kind === "drop") return f[3] === "-";
  if (event.kind === "land") return me(f[0]);
  return event.kind === "impact" || event.kind === "hit";
}

const GAP = 12;  // seconds of quiet that end one fight and begin the next
// One row per engagement instead of one per burst. The film lists every burst, hit and kill message apart, and
// a kill message names one aircraft while the shooting was at another: grouped, a fight knows who was hit most.
export function fights(events, you) {
  const ids = new Set(events.filter(e => e.kind === "impact" || e.kind === "hit").map(e => e.fields[0]));
  if (you) ids.add(String(you.id));
  // Names when we have one: an aircraft id is handed to another pilot once its owner leaves.
  const me = (name, id) => name === "you" || (you?.name ? name === you.name : ids.has(id));
  const own = events.filter(e => { const f = e.fields; return e.kind === "impact" || e.kind === "hit"
    || (e.kind === "kill" && (me(f[0], f[3]) || me(f[1], f[4])))
    || (e.kind === "guns" && (f[0] === "stop" ? f[2] !== "-" : ids.has(f[1]))); });
  // The film does not say who hit you, but it does say who was firing near you at that moment.
  const firing = [], began = new Map();
  for (const e of events) {
    if (e.kind !== "guns" || ids.has(e.fields[1])) continue;
    if (e.fields[0] === "start") began.set(e.fields[1], e.time);
    else { firing.push({id: e.fields[1], start: began.get(e.fields[1]) ?? e.time - 2, stop: e.time}); began.delete(e.fields[1]); }
  }
  for (const [id, start] of began) firing.push({id, start, stop: Infinity});  // still firing when the film ends
  // A kill of yours ends the fight: what comes more than a moment later is the next one, however soon.
  const won = e => e.kind === "kill" && me(e.fields[0], e.fields[3]);
  const groups = [];
  for (const e of own) {
    const last = groups.at(-1), kill = last?.find(won);
    if (last && e.time - last.at(-1).time <= GAP && !(kill && e.time - kill.time > 3)) last.push(e); else groups.push([e]);
  }
  return groups.map((group, index) => {
    const tally = new Map(), kills = [], add = (id, n) => tally.set(id, (tally.get(id) || 0) + n);
    let hits = 0, taken = 0, bursts = 0, lost = null, most = null, mine = null, killed = null, struck = null;
    for (const e of group) {
      const f = e.fields;
      if (e.kind === "impact") { const n = Number(f[3]) || 0; hits += n; add(f[1], n); mine ??= f[0]; if (!most || n > most.n) most = {n, time: e.time}; }
      else if (e.kind === "hit") {
        const n = Number(f[1]) || 0; taken += n; mine ??= f[0];
        if (!struck || n > struck.n) struck = {n, time: e.time};
        // Half the weight of your own hits: when you hit one aircraft and another hit you, the fight is with yours.
        for (const burst of firing) if (burst.start <= e.time && e.time <= burst.stop + 1) add(burst.id, n / 2);
      }
      else if (e.kind === "guns") bursts += f[0] === "stop";
      else if (me(f[0], f[3])) { kills.push(f[1]); add(f[4], 1000); mine ??= f[3]; killed = e.time; }
      else lost = {by: f[0], id: f[3], time: e.time};
    }
    const top = [...tally].sort((a, b) => b[1] - a[1])[0]?.[0];
    const title = kills.length ? `Kill: ${kills.join(", ")}` : lost ? `Shot down by ${lost.by}` : hits ? `${hits} hits on target` : taken ? `${taken} hits taken` : `${bursts} burst${bursts === 1 ? "" : "s"}, no hits`;
    const start = group[0].time, end = group.at(-1).time;
    const detail = [`${Math.max(1, Math.round(end - start))} s`, kills.length && hits ? `${hits} hits` : "", taken && (kills.length || hits || lost) ? `${taken} taken` : ""].filter(Boolean).join(" · ");
    return {id: `fight:${index}`, kind: "fight", time: killed ?? most?.time ?? lost?.time ?? struck?.time ?? end, start, end, fields: [], text: `${title} · ${detail}`,
      fight: {title, kills, hits, taken, bursts, me: idValue(mine), opponent: idValue(top) ?? idValue(lost?.id)}};
  });
}

// Your own kills, for a kill reel in one click.
export function ownKills(events, you) {
  return events.filter(e => e.kind === "kill" && (e.fields[0] === "you" || (you && (e.fields[0] === you.name || e.fields[3] === String(you.id)))));
}

export function filterMoments(events, query, kind, you) {
  const words = query.toLowerCase().trim().split(/\s+/);
  return events.filter(e => (kind === "mine" ? isMine(e, you) : !kind || e.kind === kind) && words.every(word =>
    `${e.kind} ${e.text} ${formatTime(e.time)}`.toLowerCase().includes(word)));
}

export function momentLabel(event) {
  const f = event.fields;
  if (event.kind === "kill") return `${f[0]} → ${f[1]}`;
  if (event.kind === "impact") return `${f[2]} · ${f[3]} impacts`;
  if (event.kind === "hit") return `${f[1]} hits received`;
  if (event.kind === "guns") return f[0] === "start" ? "Burst begins" : `Burst ends${f[2] !== "-" ? ` · ${f[2]} rounds` : ""}`;
  if (event.kind === "drop") return `${f[1]} released · ${f[2]}`;
  return event.text;
}

export async function request(path, body) {
  const response = await fetch(path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {});
  const result = await response.json();
  if (!response.ok) throw new Error(result.message || `Request failed (${response.status})`);
  return result;
}
