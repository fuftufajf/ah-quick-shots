import assert from 'node:assert/strict';
import {ANGLES, angleOf, bestVariant, candidates, castOf, clipKey, copyName, dollyCamera, dragCamera, fights, lastVideo, takeName, fitShot, killSequence, landingSequence, ownKills, sequenceOf, turnable, takeoffSequence, formatTime, filterMoments, isMine, momentLabel, newShot, ratingLabel, trimShot, zoomCamera, dayOf, killShot, momentSentence} from './desk.js';
const event = {time:199, kind:'kill', text:'Hawk Raven 1 19 10', fields:['Hawk','Raven','1','19','10']};
assert.equal(formatTime(195.3), '03:15.3');
assert.equal(filterMoments([event], 'Raven 03:19', 'kill').length, 1);
assert.equal(filterMoments([event], 'Raven', 'impact').length, 0);
const shot = newShot({length:294.8}, [], event);
assert.equal(shot.hero,19); assert.equal(shot.target,10);
assert.equal(shot.start,194); assert.equal(shot.end,201.5);
assert.equal(clipKey(1, 2, {...shot, name:'x', id:'z'}, [false, false]), clipKey(1, 2, shot, [false, false]));
assert.equal(clipKey(1, 2, Object.fromEntries(Object.entries(shot).reverse()), [false, false]), clipKey(1, 2, shot, [false, false]));
assert.notEqual(clipKey(1, 2, {...shot, side:'left'}, [false, false]), clipKey(1, 2, shot, [false, false]));
assert.notEqual(clipKey(1, 2, shot, [true, false]), clipKey(1, 2, shot, [false, false]));
assert.equal(momentLabel(event), 'Hawk → Raven');
// A copy goes into the cut named by its camera; one already ending its name gives way, and a name taken gets a number.
assert.equal(takeName('Kill: Bandit · guns (copy)', 'Tripod', []), 'Kill: Bandit · guns · Tripod');
assert.equal(takeName('Kill: Bandit · observer (copy 2)', 'Tripod', [{name: 'Kill: Bandit · Tripod'}]), 'Kill: Bandit · Tripod 2');
assert.equal(takeName("Shot at 00:40.0 · Pilot's view (copy)", 'Cockpit', []), 'Shot at 00:40.0 · Cockpit');
// The video a render made again takes the place of: the last of exactly those shots.
const made = [{id: 'a', shots: ['one']}, {id: 'b', shots: ['two', 'one']}, {id: 'c', shots: ['one']}];
assert.deepEqual([lastVideo(made, ['one'])?.id, lastVideo(made, ['one', 'two'])?.id, lastVideo(made, ['three']), lastVideo(undefined, ['one'])], ['c', 'b', null, null]);
assert.ok(newShot({length:20}, [{id:19,recorder:true}], null, 19.99).end <= 20);
// A kill as four shots in a row: closing in, the guns, him going down (ending at the message), flying on.
// Without your burst in the film, the fatal hits are taken as about 3 s before the message.
const sequence = killSequence({length:294.8}, [], event);
assert.deepEqual(sequence.map(s => [s.kind, s.start, s.end]),
  [['pair', 191, 195], ['shoulder', 195, 198], ['escort', 198, 199], ['follow', 199, 201.5]]);
assert.deepEqual(sequence.map(s => [s.hero, s.target]), [[19, 10], [19, 10], [10, 19], [19, null]]);
assert.equal(new Set(sequence.map(s => s.id)).size, 4);
assert.ok(sequence[0].name.startsWith('Hawk → Raven · '));
// With it, the shooting is your last burst that hit him, and he is followed down for the last 6 s before the message.
const burst = [{time:180.4, kind:'guns', fields:['start','19']}, {time:180.6, kind:'impact', fields:['19','10','CAN','7']},
  {time:181.1, kind:'guns', fields:['stop','19','27']}, {time:185, kind:'impact', fields:['7','10','MG','2']}];
assert.deepEqual(killSequence({length:294.8}, [], event, burst).map(s => [s.kind, s.start, s.end]),
  [['pair', 176.4, 180.4], ['shoulder', 180.4, 182.1], ['escort', 193, 199], ['follow', 199, 201.5]]);
// So near the start of the film that the fall has no room: it is left out, and nothing overlaps.
assert.deepEqual(killSequence({length:20}, [], {...event, time:1.5}).map(s => [s.kind, s.start, s.end]),
  [['pair', 0, 1], ['shoulder', 1, 2.5], ['follow', 2.5, 5]]);

// A take-off and a landing as the desk finds them in your flight path.
assert.deepEqual(takeoffSequence({length:536}, {kind:'takeoff', time:18, roll:9, hero:88}).map(s => [s.kind, s.start, s.end]),
  [['fixed', 9, 12], ['gear', 12, 17], ['escort', 17, 24], ['escort', 24, 30]]);
const lift = takeoffSequence({length:536}, {kind:'takeoff', time:18, roll:9, hero:88})[2];
assert.deepEqual([lift.watch, lift.sweep, lift.closer, lift.hero, lift.target], ['hero', 100, 100, 88, null]);
// Rolled for less than a second: no picture of the roll, the rest as usual.
assert.deepEqual(takeoffSequence({length:536}, {kind:'takeoff', time:18, roll:17.6, hero:88}).map(s => s.kind), ['escort', 'escort']);
assert.deepEqual(landingSequence({length:536}, {kind:'touchdown', time:531, stop:536, hero:88}).map(s => [s.kind, s.start, s.end]),
  [['follow', 519, 529], ['gear', 529, 532.5], ['escort', 532.5, 536]]);
assert.ok(landingSequence({length:536}, {kind:'touchdown', time:531, stop:536, hero:88})[0].name.startsWith('Landing 08:51'));
// A film that runs on after the stop: the roll-out ends 2 s after it.
assert.equal(landingSequence({length:600}, {kind:'touchdown', time:531, stop:536, hero:88})[2].end, 538);

// A sequence is the shots of one name in a row; another kill of the same pilot later is another sequence.
const takeoff = takeoffSequence({length:536}, {kind:'takeoff', time:18, roll:9, hero:88});
const again = killSequence({length:294.8}, [], event).map(s => ({...s, start: s.start + 60, end: s.end + 60}));
const reel = [...killSequence({length:294.8}, [], event), ...again, ...takeoff, {id:'lone', name:'Shot at 01:00', start:60, end:62}];
assert.deepEqual(sequenceOf(reel[1], reel).map(s => s.id), reel.slice(0, 4).map(s => s.id));
assert.deepEqual(sequenceOf(again[3], reel).map(s => s.id), again.map(s => s.id));
assert.equal(sequenceOf(takeoff[2], reel).length, 4);
assert.deepEqual(sequenceOf(reel.at(-1), reel), []);
// A duplicate is another take of its stretch: the sequence passes over it, and the duplicate's own goes on round it.
const copy = {...reel[1], id: 'copy', name: copyName(reel[1].name, reel)}, copied = [...reel, copy];
assert.deepEqual(sequenceOf(reel[0], copied).map(s => s.id), reel.slice(0, 4).map(s => s.id));
assert.deepEqual(sequenceOf(copy, copied).map(s => s.id), [reel[0].id, 'copy', reel[2].id, reel[3].id]);
assert.deepEqual([copy.name, copyName(copy.name, copied)], [`${reel[1].name} (copy)`, `${reel[1].name} (copy 2)`]);

// A camera on the airframe turns where it looks under a drag; one locked on him keeps him and does not turn.
const seat = {kind:'cockpit', fov:60, target:10};
assert.deepEqual([dragCamera(seat, 100, -50, 600).turn, dragCamera(seat, 100, -50, 600).tilt], [10, -5]);
assert.ok(turnable(seat) && turnable({kind:'gear', target:null}) && !turnable({kind:'wing', target:10}) && !turnable({kind:'pair'}));

const me = {id:19, name:'Hawk'}, elsewhere = {kind:'kill', text:'', fields:['Falcon','Kestrel','1','28','34']};
assert.deepEqual(filterMoments([event, elsewhere, {kind:'impact', text:'', fields:[]}, {kind:'guns', text:'', fields:[]}], '', 'mine', me).map(e => e.kind), ['kill', 'impact']);
assert.ok(isMine({kind:'kill', fields:['Kestrel','you','1','34','-']}, null));
assert.ok(isMine({kind:'drop', fields:['19','bomb','1000 lb','-']}, me) && !isMine({kind:'drop', fields:['7','bomb','1000 lb','2']}, me));

const planes = [{id:19, recorder:true}, {id:10}];
const cast = castOf(shot, planes);
assert.deepEqual(cast, {lead:19, other:10});
assert.deepEqual(castOf({hero:10, target:19}, planes), cast);
assert.equal(angleOf(shot, cast), 'escort');  // a new shot of two is the observer's
assert.deepEqual([angleOf({...shot, kind:'pair'}, cast), angleOf({...shot, kind:'pair', hero:10, target:19}, cast)], ['you', 'them']);
const all = candidates(shot, cast);
assert.equal(all.length, 45);
assert.ok(all.every(c => c.start === shot.start && c.end === shot.end && c.hero != null));
assert.deepEqual(candidates(shot, {lead:19, other:null}).map(c => c.kind), ['fixed', 'fixed', 'cockpit']);
const you = ANGLES.find(a => a.key === "you");
assert.deepEqual([shot.kind, shot.orbit, shot.distance, shot.fov, shot.watch], ['escort', 20, 300, 28, 'both']);  // a new shot opens on the observer's first variant
assert.equal(bestVariant(you, cast, {}).variant.distance, 120);
// The variant that reads best wins, not the one that merely keeps both in frame; on a tie, the bigger foreground.
const scores = {'you:0': {score:1, quality:.4, size:.3}, 'you:4': {score:.9, quality:.9, size:.05}, 'you:5': {score:.9, quality:.9, size:.2}, 'you:8': {score:.2, quality:.2, size:.3}};
assert.deepEqual([bestVariant(you, cast, scores).variant.distance, bestVariant(you, cast, scores).variant.orbit], [300, -11.5]);  // 60 ft aside from 300 ft
assert.ok(bestVariant(you, cast, scores).variant.fov < 35);  // further back on a longer lens
assert.equal(ratingLabel(you, cast, bestVariant(you, cast, scores).rating), 'Both in frame 90%');
// A drag on the picture: right swings a camera on your tail round to your left; down lifts it. The wheel comes closer.
const tail = {kind:'follow', orbit:0, distance:80, height:16, fov:65};
assert.ok(dragCamera(tail, 100, 0, 960).orbit < 0 && dragCamera({...tail, kind:'pair'}, 100, 0, 960).orbit > 0);
assert.ok(dragCamera(tail, 0, 100, 960).height > 16 && Math.abs(dragCamera({...tail, orbit:175}, -400, 0, 960).orbit) <= 180);
assert.deepEqual([zoomCamera(tail, true).fov, zoomCamera(tail, true).distance, zoomCamera({...tail, fov:7}, true).fov, zoomCamera({...tail, fov:6}, false).fov], [58, 80, 6, 7]);
assert.deepEqual([dollyCamera(tail, true).distance, dollyCamera(tail, true).fov, dollyCamera(tail, false).distance, dollyCamera({...tail, distance:21}, true).distance, dollyCamera({...tail, distance:59000}, false).distance], [72, 65, 88, 20, 60000]);
// A camera ship watching one aircraft circles that aircraft's own line of flight, like the camera on your tail.
const close = ANGLES.find(a => a.key === 'closeThem').make(cast)[0];
assert.deepEqual([close.kind, close.watch, close.hero, close.target], ['escort', 'hero', 10, 19]);
assert.deepEqual([angleOf(close, cast), angleOf({...close, hero:19, target:10}, cast), angleOf({...close, watch:'target'}, cast)], ['closeThem', 'closeYou', 'escort']);
assert.ok(dragCamera({...tail, kind:'escort', watch:'hero'}, 100, 0, 960).orbit < 0 && dragCamera({...tail, kind:'escort', watch:'both'}, 100, 0, 960).orbit > 0);
assert.deepEqual([newShot({length:20}, [{id:19,recorder:true}], null, 5).kind, newShot({length:20}, [{id:19,recorder:true}], null, 5).orbit], ['follow', 0]);
assert.equal(ratingLabel(you, cast, undefined), you.hint);
const fitted = fitShot({id:'new', start:4, end:12}, [{id:'a', start:0, end:6}, {id:'b', start:10, end:14}]);
assert.deepEqual([fitted.start, fitted.end], [6, 10]);

// Trimming snaps only the moved edge and preserves the camera, cast and saved shot.
const edit = {...shot, id:'edit', start:10.2, end:17.7};
assert.deepEqual(trimShot(edit, 'start', 9.6, 300, []), {...edit, start:10});
assert.deepEqual(trimShot(edit, 'end', 19.6, 300, []), {...edit, end:20});
assert.equal(trimShot(edit, 'start', 99, 300, []).start, 17.2);
assert.equal(trimShot(edit, 'end', -99, 300, []).end, 10.7);
assert.equal(trimShot(edit, 'start', -99, 300, []).start, 0);
assert.equal(trimShot(edit, 'end', 999, 28.3, []).end, 28.3);
assert.equal(trimShot({...edit, end:200}, 'start', -99, 300, []).start, 80);
assert.equal(trimShot(edit, 'end', 999, 300, []).end, 130.2);
const neighbours = [{id:'before', start:0, end:8.3}, {...edit}, {id:'after', start:23.6, end:30}];
assert.equal(trimShot(edit, 'start', -99, 300, neighbours).start, 8.3);
assert.equal(trimShot(edit, 'end', 999, 300, neighbours).end, 23.6);
assert.equal(trimShot({...edit, start:0, end:.2}, 'start', -99, 300, []).start, 0);
assert.deepEqual([edit.start, edit.end], [10.2, 17.7]);

// Real lines from a film: someone else's burst, then the recorder's two bursts, six hits and two kills.
const line = (time, kind, ...fields) => ({id: `${time}`, time, kind, fields: fields.map(String), text: fields.join(' ')});
const film = [line(188.6, 'guns', 'start', 10), line(189.4, 'guns', 'stop', 10, '-'), line(195.9, 'guns', 'start', 19), line(196.3, 'guns', 'stop', 19, 12),
  line(197.5, 'guns', 'start', 19), line(197.8, 'impact', 19, 10, 'CAN', 6), line(198.1, 'guns', 'stop', 19, 20), line(198.4, 'impact', 19, 22, 'CAN', 1),
  line(199.0, 'kill', 'Hawk', 'Raven', 1, 19, 10), line(203, 'kill', 'Falcon', 'Kestrel', 1, 28, 34), line(210.6, 'kill', 'Hawk', 'Merlin', 2, 19, 17),
  line(250, 'hit', 19, 3), line(300, 'kill', 'Osprey', 'Hawk', 1, 37, 19)];
const fought = fights(film, me);
assert.deepEqual(fought.map(f => [f.time, f.fight.title, f.fight.opponent]), [[199, 'Kill: Raven', 10], [210.6, 'Kill: Merlin', 17], [250, '3 hits taken', null], [300, 'Shot down by Osprey', 37]]);
assert.equal(fought[0].text, 'Kill: Raven · 3 s · 7 hits');
assert.deepEqual([fought[0].fight.me, fought[0].fight.bursts, fought[0].start, fought[0].end], [19, 2, 195.9, 199]);
// Without a kill message the opponent is whoever took the most hits, not the aircraft a stray round touched.
assert.equal(fights(film.filter(e => e.kind !== 'kill'), null)[0].fight.opponent, 10);
const duel = newShot({length:400}, [], fought[0], fought[0].time);
assert.deepEqual([duel.hero, duel.target, duel.kind, duel.start, duel.end, duel.name], [19, 10, 'escort', 194, 201.5, 'Kill: Raven']);
const bomb = newShot({length:400}, planes, line(120, 'drop', 19, 'bomb', '1000 lb', '-'), 120);
assert.deepEqual([bomb.kind, bomb.hero, bomb.start, bomb.end], ['weapon', 19, 119, 134]);
assert.deepEqual(ownKills(film, me).map(e => e.time), [199, 210.6]);
// A real take-off under attack: no hits of his own, twelve taken while aircraft 22 was firing. That is the opponent.
const vulch = fights([line(9.2, 'guns', 'start', 66), line(11, 'guns', 'stop', 66, 12), line(12.5, 'guns', 'start', 45), line(15, 'guns', 'stop', 45, '-'),
  line(21.7, 'guns', 'start', 22), line(21.9, 'guns', 'start', 66), line(24.1, 'guns', 'stop', 66, 16), line(25.3, 'hit', 66, 12), line(26.2, 'guns', 'stop', 22, '-')], {id: 66, name: 'Hawk'});
assert.deepEqual([vulch.length, vulch[0].time, vulch[0].fight.opponent, vulch[0].fight.title], [1, 25.3, 22, '12 hits taken']);
assert.equal(ratingLabel(ANGLES.find(a => a.key === 'behind'), cast, {score: 1}), 'You in frame 100%');
assert.equal(newShot({length:400}, [], line(64.1, 'kill', 'Hawk', 'Raven', 1, 19, 10), 64.1).start, 59.1);
assert.equal(fights([line(5, 'guns', 'start', 19), line(5.5, 'guns', 'stop', 19, 9)], me)[0].fight.title, '1 burst, no hits');
// The simple desk says the same fights as sentences, and anything else as the full desk labels it.
assert.deepEqual(fought.map(momentSentence), [['You shot down Raven', '7 hits in 3 seconds'], ['You shot down Merlin', '1 second of shooting'],
  ['You took 3 hits', '1 second'], ['Osprey shot you down', '1 second']]);
assert.deepEqual(momentSentence(fights([line(5, 'guns', 'start', 19), line(5.5, 'guns', 'stop', 19, 9)], me)[0]), ['You fired, no hits', '1 burst, 1 second']);
assert.deepEqual([momentSentence(film[8]), momentSentence(film[11])], [['Hawk shot down Raven', ''], ['3 hits received', '']]);
// A replay's day: today and yesterday by name, the weekday within a week, then the date.
const noon = new Date(2026, 10, 19, 12).getTime(), day = (y, m, d, h = 9) => new Date(y, m, d, h).getTime() / 1000;
assert.match(dayOf(day(2026, 10, 19), noon), /^Today, /);
assert.match(dayOf(day(2026, 10, 18, 23), noon), /^Yesterday, /);
assert.deepEqual([dayOf(day(2026, 10, 16), noon), dayOf(day(2026, 10, 12), noon)], ['Monday', 'November 12, 2026']);
// A kill as one shot: the observer on both, from where the kill sequence begins to where it ends.
const parts = killSequence({length:400}, [], film[8], film), whole = killShot({length:400}, [], film[8], film);
assert.deepEqual([whole.kind, whole.hero, whole.target, whole.start, whole.end], ['escort', 19, 10, parts[0].start, parts.at(-1).end]);
assert.deepEqual(['shoulder', 'nose', 'belly', 'gear', 'weapon', 'wing', 'cockpit', 'recorded', 'ambush', 'escort', 'follow', 'fixed'].map(kind => angleOf({kind}, cast)), ['shoulder', 'nose', 'belly', 'gear', 'weapon', 'wing', 'cockpit', 'cockpit', 'ambush', 'escort', 'behind', 'flyby']);
assert.deepEqual(ANGLES.slice(0, 2).map(a => a.key), ['escort', 'tripod']);  // the two cameras that are directed come first
assert.ok(ANGLES.every(a => ANGLES.some(b => b.key === angleOf({...shot, ...a.make(cast)[0]}, cast))));  // every camera finds its own card
assert.ok(ANGLES.every(a => a.make(cast).every(v => v.hero === 19 || v.hero === 10)));
console.log('OK: moment search, fights, film times, shot creation and angle presets');
await import("./assembly.test.mjs");
await import("./cost.test.mjs");
await import("./keyframes.test.mjs");
