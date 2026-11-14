import {useEffect, useMemo, useRef, useState} from "react";
import {ArrowClockwise, ArrowRight, CaretLeft, CaretRight, FilmSlate, FilmStrip, FolderOpen, MagnifyingGlass, Pause, Play, Plus, Sparkle, Trash, X} from "@phosphor-icons/react";
import {ANGLES, FREE, angleOf, bestVariant, candidates, castOf, clipKey, dragCamera, fights, filterMoments, fitShot, formatTime, killSequence, landingSequence, sequenceOf, takeoffSequence, turnable, lens, momentLabel, newShot, ownKills, ratingLabel, request, trimShot, zoomCamera} from "./desk.js";

const KINDS = [["fight", "Fights"], ["mine", "Mine"], ["kill", "Kills"], ["drop", "Drops"], ["", "All"]];
// key, label, unit, min, max, step, the cameras it moves (null: every camera with a lens of its own)
const TUNING = [["orbit", "Around", "°", -180, 180, 1, FREE], ["distance", "Distance", "ft", 20, 1500, 5, FREE],
  ["height", "Height", "ft", -300, 600, 1, [...FREE, "wing"]], ["fov", "Lens (zoom)", "°", 6, 120, 1, null], ["roll", "Roll", "°", -45, 45, 1, null],
  // Only on the shots made with them (a lift-off, a roll-out): the camera ship swings round and closes in.
  ["sweep", "Swing round", "°", -180, 180, 5, ["escort"], true], ["closer", "Ends at", "ft", 20, 1500, 5, ["escort"], true],
  // Cameras on the airframe that never lock on him: where they look (a drag on the picture does the same).
  ["turn", "Turn (left +)", "°", -180, 180, 1, ["cockpit", "nose", "belly", "gear"]], ["tilt", "Tilt (up +)", "°", -89, 89, 1, ["cockpit", "nose", "belly", "gear"]]];
const AS_FLOWN = ["recorded", "weapon"];  // the pilot's own view and the bomb's ride: nothing to adjust
const CHUNK = 10;  // seconds of film per preview when no shot is selected
const NEAR = 3000;  // feet: an aircraft that never comes this close is a dot, not someone to film

export function App() {
  const [desk, setDesk] = useState({films: [], planes: [], proposals: []});
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState("fight");
  const [filmChoice, setFilmChoice] = useState("");
  const [draft, setDraft] = useState(null);
  const [cast, setCast] = useState({lead: null, other: null});
  const [dirty, setDirty] = useState(false);
  const [baseRevision, setBaseRevision] = useState(null);
  const [moment, setMoment] = useState(null);
  const [cursor, setCursor] = useState(0);
  const [wanted, setWanted] = useState(null);
  const [asked, setAsked] = useState("");
  const [rolling, setRolling] = useState(false);
  const [zoom, setZoom] = useState(false);
  const [rated, setRated] = useState("");
  const [skipped, setSkipped] = useState(() => new Set());
  const [forgetting, setForgetting] = useState(null);  // {ids, label}: shots asked to be removed, waiting for a choice
  const [trashing, setTrashing] = useState(null);  // a replay asked to go to the Recycle Bin, waiting for a yes
  const [quality, setQuality] = useState("final");
  const [apart, setApart] = useState(false);
  const [withOverlay, setWithOverlay] = useState(false);
  const [withBlur, setWithBlur] = useState(false);
  const [withLook, setWithLook] = useState(false);
  const [withHaze, setWithHaze] = useState(false);
  const [hidden, setHidden] = useState("");
  const [closedVideo, setClosedVideo] = useState("");
  const [sending, setSending] = useState(false);
  const [looking, setLooking] = useState(false);  // a quick look in flight: it holds the next one back and shows nothing
  const scrubbing = useRef(false);
  const autoConnect = useRef(true);
  const casting = useRef(null);
  const airfieldAsked = useRef(false);  // Take-offs & landings waits for the desk to read your flight path  // a moment waiting to learn who was around, then which framing reads best
  // What this page asked to see moving: a clip's [start, end], or "cut". The desk is shared, so a second open page
  // (another tab or window) gets the same picture; playing it there too is heard as a doubled voice.
  const asking = useRef(null);
  const film = useRef(null);  // the preview clip's <video>
  const screen = useRef(null);
  const grab = useRef(null);  // a drag on the picture: where it began and the camera as it was
  const trimGrab = useRef(null);
  const dragged = useRef(false);
  const latest = useRef(null);  // the shot as last changed: wheel notches arrive faster than renders
  const onWheel = useRef(null);
  const you = useRef(null);  // the recorder, remembered while they are between sorties
  you.current = desk.planes.find(p => p.recorder) || you.current;
  latest.current = draft;

  const project = desk.project;
  const shots = project?.shots || [];
  const events = project?.events || [];
  const length = project?.length || 1;
  const running = Boolean(desk.job?.running);
  const busy = running || sending || looking;  // the viewer cannot take another request yet
  // Worth showing: a look at a changed camera is back before a spinner could be read.
  const working = sending || (running && (!["seek", "icons"].includes(desk.job.action) || (desk.job.elapsed || 0) >= .5));
  const failure = !working && desk.job?.error && desk.job.id !== hidden ? desk.job.error : "";
  const problems = desk.problems || {};
  const isSaved = Boolean(draft) && shots.some(s => s.id === draft.id);
  const conflict = dirty && isSaved && baseRevision !== project?.revision;
  const clip = desk.preview?.kind === "clip" ? desk.preview : null;
  const sequence = draft && isSaved ? sequenceOf(draft, shots) : [];
  const flags = [Boolean(desk.icons)];
  // A shot's camera stops at its end, and a seek can land a little late (measured: up to 0.08 s): a still asked
  // for at the very end would show the pilot's view instead.
  const inShot = shot => shot ? Math.min(shot.end - .1, Math.max(cursor, shot.start)) : cursor;

  useEffect(() => {
    let alive = true, timer;
    async function poll() {
      let working = false;
      try {
        const state = await request("/api/state");
        working = state.job?.running;
        if (alive) setDesk(state);
      } catch (e) { if (alive) setError(e.message); }
      if (alive) timer = setTimeout(poll, working ? 250 : 600);
    }
    poll();
    return () => {alive = false; clearTimeout(timer);};
  }, []);

  useEffect(() => {
    if (autoConnect.current && desk.connected === false && !desk.job) { autoConnect.current = false; action("connect"); }
  }, [desk.connected, desk.job]);

  useEffect(() => {
    if (dirty) {setError("The active film changed. Your unsaved shot is kept until you discard it."); return;}
    setDraft(null); setMoment(null); setCursor(0); setRated(""); setSkipped(new Set());
    you.current = null;
  }, [project?.film]);

  useEffect(() => {
    function beforeUnload(e) {if (dirty) {e.preventDefault(); e.returnValue = "";}}
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  // A saved shot follows the shared plan until you start changing it.
  useEffect(() => {
    const shot = !dirty && draft && shots.find(s => s.id === draft.id);
    if (shot) setDraft({...shot});
  }, [project?.revision]);

  useEffect(() => { if (desk.now != null && !scrubbing.current && wanted == null && !clip) setCursor(desk.now); }, [desk.now]);

  // Outside the clip on screen, dragging the playhead fetches a still as soon as it rests.
  useEffect(() => {
    if (!scrubbing.current) return;
    const timer = setTimeout(() => { scrubbing.current = false; goTo(cursor); }, 250);
    return () => clearTimeout(timer);
  }, [cursor]);
  // After a jump in the film the viewer fades the aircraft in over a couple of seconds, so the first still shows
  // them see-through. Once things rest it is taken again, without moving the film.
  useEffect(() => {
    if (!desk.preview?.fresh || busy || wanted || scrubbing.current || trimGrab.current) return;
    const timer = setTimeout(() => setWanted({action: "seek", body: {seconds: desk.now, rough: true, wait: true, ...(draft ? {shot: draft} : {})}}), 2000);
    return () => clearTimeout(timer);
  }, [desk.preview?.url, desk.preview?.fresh, busy, wanted]);
  // One request waits for the viewer; a newer one replaces it.
  useEffect(() => {
    if (!wanted || busy || (!project && wanted.action !== "open")) return;
    setWanted(null);
    action(wanted.action, wanted.body);
  }, [wanted, busy]);

  useEffect(() => {
    const job = desk.job, player = film.current;
    if (!job || job.running) return;
    const own = clip && Array.isArray(asking.current) && clip.start === asking.current[0] && clip.end === asking.current[1];
    if (job.action === "clip" && !job.error && own && player && !trimGrab.current && (!draft || asking.current[2] === "sequence" || (clip.start === draft.start && clip.end === draft.end))) { asking.current = null; player.currentTime = 0; player.play().catch(() => {}); }
    if (job.action === "airfield" && airfieldAsked.current) {
      airfieldAsked.current = false;
      if (!job.error) airfieldReel(desk.airfield?.moments || []);
    }
    const want = casting.current;
    if (!want || !["around", "suggest"].includes(job.action)) return;
    if (!draft || draft.id !== want.id) { casting.current = null; return; }
    if (job.action === "around") {
      // The moment's own aircraft may be a stray hit or a kill message from elsewhere: prefer who was really there.
      const around = job.error ? [] : (desk.nearby?.aircraft || []).filter(a => a.id !== cast.lead);
      const chosen = around.find(a => a.id === cast.other), nearest = around[0];
      let next = draft, now = cast;
      if (nearest && nearest.closest <= 1500 && (!chosen || chosen.closest > NEAR)) {
        now = {...cast, other: nearest.id};
        next = {...draft, ...ANGLES[0].make(now)[0]};
        setCast(now); setDraft(next);
      } else if (nearest && cast.other == null && nearest.closest <= NEAR) {
        // A moment that names nobody keeps its camera on you, but the cameras for two are measured with whoever is near.
        now = {...cast, other: nearest.id};
        setCast(now);
      }
      if (now.other == null) { casting.current = null; return show(next, want.still, now.lead); }
      // Before the first picture, every camera is measured over the stretch: each then opens on its best framing.
      want.rating = true;
      setRated(JSON.stringify([next.start, next.end, now]));
      setWanted({action: "suggest", body: {shots: candidates(next, now)}});
    } else if (want.rating) {
      casting.current = null;
      const found = job.error ? {} : Object.fromEntries(desk.proposals.map(p => [p.id, p]));
      const next = {...draft, ...bestVariant(ANGLES.find(a => a.key === angleOf(draft, cast)), cast, found).variant};
      setDraft(next); show(next, want.still);
    }
  }, [desk.job?.id, desk.job?.running]);

  useEffect(() => {
    function key(e) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.target.matches("input:not([type=range]),select,textarea,summary,a")) return;
      if (e.key === "i") mark("start");
      else if (e.key === "o") mark("end");
      else if (e.key === " ") { e.preventDefault(); play(); }  // also when a button still has focus from the last click
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  // The wheel over the picture zooms the camera's lens; with nothing to zoom it scrolls the page as usual.
  useEffect(() => {
    const element = screen.current;
    let turned = 0;
    function wheel(e) {
      if (!onWheel.current) return;
      e.preventDefault();
      turned += e.deltaY;
      if (Math.abs(turned) < 50) return;  // a touchpad sends many small moves for one notch
      onWheel.current(turned < 0);
      turned = 0;
    }
    element.addEventListener("wheel", wheel, {passive: false});
    return () => element.removeEventListener("wheel", wheel);
  }, []);

  async function action(name, extra = {}) {
    const quick = extra.wait === true, flying = quick ? setLooking : setSending;
    setError(""); flying(true);
    try {
      const result = await request("/api/action", {action: name, expected_revision: project?.revision, ...extra});
      // A quick look answers with the finished state. Otherwise the job has begun; a poll may already know more.
      setDesk(s => quick ? result : s.job?.id === result.id ? s : {...s, job: result});
    } catch (e) { setError(e.message); }
    finally { flying(false); }
  }
  async function save(list, expected = project.revision) {
    setSending(true); setError("");
    try {
      const saved = await request("/api/project", {expected_revision: expected, shots: list});
      setDesk(s => ({...s, project: saved}));
      return saved;
    } catch (e) { setError(e.message); }
    finally { setSending(false); }
  }

  // A still of one moment, through the selected shot's camera when it falls inside it.
  function goTo(seconds) {
    setCursor(seconds);
    setWanted({action: "seek", body: {seconds, rough: true, wait: true, ...(draft ? {shot: draft} : {})}});
  }
  // The moving picture: a shot (saved or not) rendered as a small clip, or the next stretch of film.
  function show(shot, still, lead = cast.lead) {
    const body = shot ? {start: shot.start, end: shot.end, shot, ...(lead != null ? {hero: lead} : {}), still: Math.min(shot.end - .1, Math.max(still, shot.start + .3))}
      : {start: cursor, end: Math.min(length, cursor + CHUNK)};
    asking.current = [body.start, body.end];
    setAsked(clipKey(body.start, body.end, shot, flags)); setWanted({action: "clip", body});
  }
  // What you just changed, at once: a still of the playhead moment as it now looks. Play then shows it moving.
  function look(shot, switched = {}) {
    setWanted({action: switched.action || "seek", body: {seconds: inShot(shot), rough: true, wait: true, ...switched.body, ...(shot ? {shot} : {})}});
  }
  function play() {
    if (!project) return;
    const player = film.current;
    if (player && !player.paused && !player.ended) return player.pause();
    // A saved shot of a kill, take-off or landing: Play shows the whole sequence, as the cut renders it (clicking
    // the shot in the cut already plays the shot alone). Changed and not yet updated, it plays that shot.
    if (sequence.length > 1 && !dirty) {
      if (player && clip && asked === "sequence" && clip.start === sequence[0].start && clip.end === sequence.at(-1).end) {
        if (player.ended) player.currentTime = 0;
        return player.play();
      }
      return playSequence();
    }
    if (player && (draft ? asked === clipKey(draft.start, draft.end, draft, flags) : !player.ended)) {
      if (player.ended) player.currentTime = 0;
      return player.play();
    }
    show(draft, cursor);
  }

  // The selected shot's sequence (a kill, a take-off, a landing): its saved shots in a row, as the cut renders them.
  // At once and with its sound, in Film Viewer's own window: nothing rendered. What Play would show.
  function playLive() {
    film.current?.pause();
    const body = sequence.length > 1 && !dirty ? {shot_ids: sequence.map(s => s.id)}
      : draft ? {start: draft.start, end: draft.end, shot: draft} : {start: cursor, end: Math.min(length, cursor + CHUNK)};
    setWanted({action: "live", body});
  }
  function playSequence() {
    asking.current = [sequence[0].start, sequence.at(-1).end, "sequence"];
    setAsked("sequence"); setWanted({action: "clip", body: {shot_ids: sequence.map(s => s.id)}});
  }

  function open(shot, changed) {
    setDraft(shot); setCast(castOf(shot, desk.planes)); setDirty(changed); setBaseRevision(project.revision); setRated("");
  }
  function change(next) {
    if (!dirty) setBaseRevision(project.revision);
    latest.current = next; setDraft(next); setDirty(true);
  }
  function tune(next) { change(next); look(next); }  // the camera moved
  function keepDraft() { return dirty && !window.confirm("Discard the unsaved changes to this shot?"); }
  // Another film, or this one again: the unsaved shot goes after asking, and the request waits for the viewer like any other.
  function load() {
    if (keepDraft()) return;
    setDraft(null); setDirty(false);
    setWanted({action: "open", body: {film: filmChoice || project.film}});
  }

  function pick(e) {
    setMoment(e);
    if (keepDraft()) return goTo(e.time);
    const inside = shots.find(s => e.time >= s.start && e.time <= s.end);
    const shot = inside ? {...inside} : fitShot(newShot(project, desk.planes, e, e.time), shots);
    const lead = castOf(shot, desk.planes).lead;
    open(shot, false); setCursor(e.time);
    if (inside || shot.hero == null || AS_FLOWN.includes(shot.kind)) return show(shot, e.time, lead);
    casting.current = {id: shot.id, still: e.time};
    setWanted({action: "around", body: {start: shot.start, end: shot.end, hero: lead}});
  }
  // A sequence just added: its first shot selected and shown as a still, so Play shows the whole sequence.
  function select(shot) {
    open({...shot}, false); setCursor(shot.start); look(shot);
  }
  function choose(shot) {
    if (keepDraft()) return;
    open({...shot}, false); setCursor(shot.start); show(shot, shot.start, castOf(shot, desk.planes).lead);
  }
  function fresh(start, end) {
    if (keepDraft()) return;
    setMoment(null);
    open(fitShot({...newShot(project, desk.planes, null, start), start, end}, shots), true);
  }
  // The usual next step is the same stretch through another camera: with the playhead inside the selected shot,
  // a new shot keeps its stretch and starts from its camera, and the selected one stays as it was saved.
  // (Half a second of slack: a clip played to its end leaves the playhead a frame or two past the shot.)
  const again = Boolean(draft) && cursor >= draft.start - .5 && cursor <= draft.end + .5;
  function another() {
    const at = Math.round(cursor * 10) / 10;
    if (!again) return fresh(at, Math.min(project.length, at + 6));
    setDraft({...draft, id: crypto.randomUUID()}); setDirty(true); setBaseRevision(project.revision);
  }
  function mark(edge) {
    if (!project) return;
    const at = Math.round(cursor * 10) / 10;
    let {start, end} = draft || {start: at, end: at};
    if (edge === "start") { start = at; if (end < start + .5) end = Math.min(project.length, start + 6); }
    else { end = at; if (start > end - .5) start = Math.max(0, end - 6); }
    if (draft) change({...draft, start, end}); else fresh(start, end);
  }
  function angle(a) {
    const next = {...draft, roll: 0, watch: "both", ...bestVariant(a, stage, scores).variant};
    if (a.pair) setCast(stage);
    film.current?.pause(); tune(next);  // a still to set the view on first: the clip is rendered when Play asks for it
  }
  // Whom the camera ship keeps in its picture: both of you, you alone (then from any side), or him alone on a
  // long lens from its steady seat beside you. The lens closes on him at once when it was wider than he needs.
  function watch(who) {
    const far = near.find(a => a.id === stage.other);
    const next = {...draft, kind: "escort", hero: cast.lead, target: stage.other, watch: who === "you" ? "hero" : who === "them" ? "target" : "both",
      ...(who === "them" && far ? {fov: Math.max(6, Math.min(draft.fov, lens(far.typical)))} : {})};
    if (who !== "you" && cast.other == null) setCast(stage);
    tune(next);
  }
  // A new opponent keeps the camera when it is one of the two-aircraft ones, else it becomes "You in front".
  function recast(next) {
    const key = angleOf(draft, cast), now = ANGLES.find(a => a.key === key);
    const preset = next.other == null ? (now.pair ? ANGLES.find(a => a.key === "behind") : now) : now.pair ? now : ANGLES[0];
    const variant = preset.make(next)[0];
    const shot = {...draft, ...(preset.key === key ? {hero: variant.hero, target: variant.target} : variant)};
    setCast(next); setRated(""); change(shot); show(shot, cursor, next.lead);
  }

  // A drag on the picture moves a free camera round the aircraft; a plain click still plays and pauses.
  function down(e) {
    if (movable && e.button === 0 && !e.target.closest("button")) grab.current = {x: e.clientX, y: e.clientY, shot: draft, moved: false};
  }
  function move(e) {
    const held = grab.current;
    if (!held) return;
    const dx = e.clientX - held.x, dy = e.clientY - held.y;
    if (!held.moved && Math.hypot(dx, dy) < 5) return;
    if (!held.moved) { held.moved = true; e.currentTarget.setPointerCapture(e.pointerId); film.current?.pause(); }
    tune(dragCamera(held.shot, dx, dy, e.currentTarget.clientWidth));
  }
  function up() {
    if (grab.current?.moved) { dragged.current = true; setTimeout(() => { dragged.current = false; }, 0); }
    grab.current = null;
  }

  function trim(edge, seconds) {
    const shot = latest.current;
    if (!shot) return null;
    const next = trimShot(shot, edge, seconds, length, shots);
    if (next[edge] === shot[edge]) return null;
    film.current?.pause(); casting.current = null;
    change(next); setRated("");
    return next;
  }
  function trimLook(shot, edge) {
    const at = edge === "start" ? shot.start : Math.max(shot.start, shot.end - .1);
    setCursor(at); look(shot, {body: {seconds: at}});
  }
  function trimDown(e, edge) {
    if (e.button !== 0) return;
    e.preventDefault(); e.currentTarget.focus(); film.current?.pause();
    trimGrab.current = {edge, x: e.clientX, at: draft[edge], view: [from, to], width: e.currentTarget.parentElement.clientWidth};
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function trimMove(e) {
    const held = trimGrab.current;
    if (held) trim(held.edge, held.at + (e.clientX - held.x) / held.width * (held.view[1] - held.view[0]));
  }
  function trimUp() {
    const held = trimGrab.current;
    trimGrab.current = null;
    if (held && latest.current && latest.current[held.edge] !== held.at) trimLook(latest.current, held.edge);
  }
  function trimKey(e, edge) {
    const value = draft[edge], at = {ArrowLeft: Math.ceil(value) - 1, ArrowDown: Math.ceil(value) - 1,
      ArrowRight: Math.floor(value) + 1, ArrowUp: Math.floor(value) + 1, Home: 0, End: length}[e.key];
    if (at == null) return;
    e.preventDefault(); e.stopPropagation();
    const next = trim(edge, at);
    if (next) trimLook(next, edge);
  }

  async function commit() {
    const saved = await save([...shots.filter(s => s.id !== draft.id), draft], isSaved && dirty ? baseRevision : project.revision);
    if (saved) { setDraft(saved.shots.find(s => s.id === draft.id)); setDirty(false); setBaseRevision(saved.revision); }
  }
  function remove() { setForgetting({ids: [draft.id], label: draft.name}); }
  async function deleteFilm() {
    const film = trashing;
    setSending(true); setError("");
    try {
      const result = await request("/api/delete-film", {film: film.path});
      setDesk(s => ({...s, films: result.films}));
      setFilmChoice("");
    } catch (e) { setError(e.message); }
    finally { setSending(false); setTrashing(null); }
  }
  // The finished videos (and their overlays) the desk kept of these shots and that are still on disk.
  function filesOf(ids) {
    return (desk.renders || []).filter(r => r.shots.some(id => ids.includes(id))).flatMap(r => [r.file, ...(r.overlays || [])]);
  }
  async function forget(files) {
    const ids = forgetting.ids;
    setSending(true); setError("");
    try {
      const result = await request("/api/forget", {expected_revision: project.revision, shot_ids: ids, files});
      setDesk(s => ({...s, project: result.project, renders: result.renders}));
      if (draft && ids.includes(draft.id)) { setDraft(null); setDirty(false); }
    } catch (e) { setError(e.message); }
    finally { setSending(false); setForgetting(null); }
  }
  // One moment straight into the cut: a kill (or a fight you won) as its four shots, anything else as one shot.
  async function addMoment(e) {
    if (keepDraft()) return;
    const kill = e.kind === "kill" ? e : e.fight?.kills.length ? ownKills(events, you.current).find(k => Math.abs(k.time - e.time) < .05) : null;
    const made = [];
    for (const part of kill ? killSequence(project, desk.planes, kill, events) : [newShot(project, desk.planes, e, e.time)]) {
      const shot = fitShot(part, [...shots, ...made]);
      if (shot.end - shot.start >= .5) made.push(shot);
    }
    if (!made.length) return setError("This moment is already in the cut");
    if (await save([...shots, ...made])) select(made[0]);
  }
  // A kill reel in one click: a shot for every kill of yours that has none yet. Cameras stay yours to pick.
  // The new desk takes the port a moment after the old one lets it go; the page is then read again, new too.
  async function restartDesk() {
    try { await request("/api/restart", {}); setTimeout(() => window.location.reload(), 3000); }
    catch (e) { setError(e.message); }
  }

  // Your take-offs and landings that have no shot yet, a few shots each. The flight path is read once per film.
  function airfield() {
    if (desk.airfield?.film === project.film) return airfieldReel(desk.airfield.moments);
    airfieldAsked.current = true;
    action("airfield");
  }
  async function airfieldReel(found) {
    const made = [];
    for (const m of found) {
      if (shots.some(s => m.time >= s.start && m.time <= s.end)) continue;
      for (const part of (m.kind === "takeoff" ? takeoffSequence : landingSequence)(project, m)) {
        const shot = fitShot(part, [...shots, ...made]);
        if (shot.end - shot.start >= .5) made.push(shot);
      }
    }
    if (made.length && await save([...shots, ...made])) select(made[0]);
    else if (!made.length) setError(found.length ? "Your take-offs and landings in this film already have shots" : "No take-off or landing of yours in this film");
  }

  async function reel() {
    const made = [];
    for (const e of uncovered) {
      for (const part of killSequence(project, desk.planes, e, events)) {
        const shot = fitShot(part, [...shots, ...made]);
        if (shot.end - shot.start >= .5) made.push(shot);
      }
    }
    if (made.length && await save([...shots, ...made])) select(made[0]);
  }

  const battles = useMemo(() => fights(events, you.current), [project?.film, events.length, you.current?.id, you.current?.name]);
  const moments = kind === "fight" ? filterMoments(battles, query, "") : filterMoments(events, query, kind, you.current);
  const uncovered = ownKills(events, you.current).filter(e => !shots.some(s => e.time >= s.start && e.time <= s.end));
  const [from, to] = trimGrab.current?.view || (zoom && draft ? [Math.max(0, draft.start - 30), Math.min(length, draft.end + 30)] : [0, length]);
  const place = t => Math.max(0, Math.min(100, (t - from) / (to - from) * 100));
  const span = s => ({left: `${place(s.start)}%`, width: `${place(s.end) - place(s.start)}%`});
  const chosen = shots.filter(s => !skipped.has(s.id)).map(s => s.id);
  const video = desk.preview?.kind === "video" && desk.preview.url !== closedVideo ? desk.preview : null;
  const still = desk.preview?.kind === "image" ? desk.preview : null;
  const status = working ? desk.job?.message || "Submitting operation" : desk.job?.error || (desk.connected ? "Ready" : "Start Film Viewer, then connect");
  const called = id => {
    const plane = desk.planes.find(p => p.id === id);
    if (plane) return plane.name;
    const kill = events.find(e => e.kind === "kill" && (e.fields[3] === String(id) || e.fields[4] === String(id)));
    return kill ? kill.fields[kill.fields[3] === String(id) ? 0 : 1] : `Aircraft ${id}`;
  };
  const camera = s => ANGLES.find(a => a.key === angleOf(s, castOf(s, desk.planes))).label;
  // Finished videos are kept in one folder, each under its film, time, shot and camera. A page cannot open a
  // folder, so the desk shows it in Explorer.
  const openVideos = () => request("/api/folder", {}).catch(e => setError(e.message));
  const baseName = path => String(path || "").split(/[\\/]/).pop();
  const planes = [...desk.planes];
  if (cast.lead != null && !planes.some(p => p.id === cast.lead)) planes.push({id: cast.lead, name: `${called(cast.lead)} · not at the playhead`});
  // Who to film you with: the aircraft that come near in this shot, nearest first. A name comes from the viewer,
  // else from a kill message that carried the id.
  const near = draft && desk.nearby && desk.nearby.hero === cast.lead && desk.nearby.start < draft.end && desk.nearby.end > draft.start
    ? desk.nearby.aircraft.filter(a => a.id !== cast.lead && (a.closest <= NEAR || a.id === cast.other)) : [];
  // A shot whose camera films you alone (saved from your tail, say) names no opponent: the two-aircraft cameras
  // then offer whoever comes nearest, instead of going grey.
  const stage = cast.other == null && near.length ? {...cast, other: near[0].id} : cast;
  const rateKey = draft ? JSON.stringify([draft.start, draft.end, stage]) : "";
  const scores = rated === rateKey ? Object.fromEntries(desk.proposals.map(p => [p.id, p])) : {};
  const movable = Boolean(draft) && (FREE.includes(draft.kind) || turnable(draft)) && !video && !conflict;
  const adjustable = Boolean(draft) && !AS_FLOWN.includes(draft.kind) && !video && !conflict;
  const tuning = adjustable ? TUNING.filter(row => (row[6] ? row[6].includes(draft.kind) : true) && (!row[7] || draft[row[0]] != null)) : [];
  const dropInShot = draft && events.some(e => e.kind === "drop" && e.time >= draft.start - 1 && e.time <= draft.end);
  // The ship flying with you may watch both of you or one alone; flying with him it watches him.
  const watching = draft?.kind === "escort" && draft.hero === cast.lead && !conflict ? {hero: "you", target: "them"}[draft.watch] || "both" : null;
  onWheel.current = adjustable ? closer => { film.current?.pause(); tune(zoomCamera(latest.current, closer)); } : null;

  return <main className="desk">
    <header className="topbar">
      <div className="brand"><FilmStrip size={26} weight="duotone"/><h1>AH <span>QUICK SHOTS</span></h1></div>
      <div className="open-film"><select aria-label="Film" value={filmChoice || project?.film || ""} onChange={e => setFilmChoice(e.target.value)}><option value="">Choose a replay</option>{desk.films.map(f => <option key={f.path} value={f.path}>{f.name}{f.modified ? ` · ${new Date(f.modified * 1000).toLocaleDateString()}` : ""}</option>)}</select><button disabled={!(filmChoice || project?.film) || (running && desk.job.action === "export_cut")} onClick={load} title="Open this replay; an operation in progress finishes first">Open</button>
        <button className="danger" aria-label="Delete this replay" disabled={!filmChoice || filmChoice === project?.film || working} onClick={() => setTrashing(desk.films.find(f => f.path === filmChoice))} title={filmChoice && filmChoice === project?.film ? "Open another replay first: Film Viewer holds the one that is open" : "Move the chosen replay to the Recycle Bin"}><Trash/></button></div>
      <div className={`operation ${working ? "working" : desk.job?.error ? "failed" : ""}`} role="status" aria-live="polite">
        <span className={working ? "spinner" : "status-dot"}/><strong>{status}</strong>{working && <span className="elapsed">{Math.floor(desk.job?.elapsed || 0)}s</span>}
      </div>
      <button disabled={working} onClick={() => action("connect")}><ArrowClockwise/> {desk.connected ? "Reconnect" : "Connect viewer"}</button>
    </header>
    {desk.stale && <div className="notice" role="alert">AH Quick Shots was updated while it was running: the desk still runs the old version, and the page may ask it for things it cannot do. An unsaved shot is lost on restart.<button onClick={restartDesk}>Restart the desk</button></div>}
    {(error || conflict) && <div className="notice" role="alert">{conflict ? "The shared plan changed while you were editing. Discard your changes to load it, then redo them." : error}{!conflict && <button onClick={() => setError("")} aria-label="Dismiss message">×</button>}</div>}

    <section className="workspace">
      <aside className="moments panel">
        <div className="panel-heading"><h2>1 <span>Moments</span></h2><span>{moments.length}</span></div>
        <label className="search"><MagnifyingGlass/><input aria-label="Search moments" placeholder="Pilot, weapon or 03:15…" value={query} onChange={e => setQuery(e.target.value)}/></label>
        <div className="filters" aria-label="Event type">{KINDS.map(([value, label]) => <button key={value} aria-pressed={kind === value} onClick={() => setKind(value)}>{label}</button>)}</div>
        <div className="moment-list">{moments.slice(0, 300).map(e => <div key={e.id} className="moment-row"><button className={`moment ${moment?.id === e.id ? "selected" : ""}`} onClick={() => pick(e)}><time>{formatTime(e.time)}</time><span><b>{e.kind}</b><small>{momentLabel(e)}</small></span></button>
          {!shots.some(s => e.time >= s.start && e.time <= s.end) && <button className="add-take" disabled={working} onClick={() => addMoment(e)} aria-label={`Add ${momentLabel(e)} to the cut`} title={e.kind === "kill" || e.fight?.kills.length ? "Add this kill to the cut: closing in, the guns, him going down, flying on" : "Add this moment to the cut as one shot"}><ArrowRight weight="bold"/></button>}</div>)}
          {!moments.length && <p className="empty-small">{!project ? "Start Film Viewer and open a replay. Its moments appear here." : kind === "fight" ? "No fights of yours in this film. Try All, or press New shot at the playhead." : "No matching moments. Try another filter, or press New shot at the playhead."}</p>}
          {moments.length > 300 && <p className="hint">Showing 300 of {moments.length}. Narrow your search.</p>}
        </div>
      </aside>

      <section className="stage">
        <div className="picture panel" aria-busy={working}>
          <div className="panel-heading"><h2>2 <span>Picture</span></h2>
            <div className="chips"><span className="note">Trails: tick them in Film Viewer</span><button aria-pressed={flags[0]} disabled={!project} onClick={() => look(draft, {action: "icons", body: {on: !desk.icons}})}>Names</button></div>
          </div>
          <div ref={screen} className={`screen ${movable ? "movable" : ""}`} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
            onClick={e => { if (!dragged.current && clip && !video && !e.target.closest("button")) play(); }}>
            {video ? <video key={video.url} src={video.url} controls autoPlay={asking.current === "cut"} onLoadedData={() => { asking.current = null; }} playsInline/>
            : clip ? <video key={clip.url} ref={film} src={clip.url} playsInline onPlay={() => setRolling(true)} onPause={() => setRolling(false)} onEnded={() => setRolling(false)} onTimeUpdate={e => { if (!scrubbing.current && !trimGrab.current) setCursor(clip.start + e.target.currentTime); }}/>
            : still ? <img src={still.url} draggable={false} alt={`Film Viewer frame at ${formatTime(still.time)}`}/>
            : <div className="empty-preview"><FilmStrip size={44} weight="thin"/><h2>See the film here.</h2><p>Open a replay and click a moment.<br/>The desk renders that shot as a short clip and plays it.</p></div>}
            {working && <div className="preview-busy"><span className="spinner"/><span>{desk.job?.message || "Working…"}</span>{desk.job?.expect && <span className="meter"><i style={{width: `${Math.min(100, (desk.job.elapsed || 0) / desk.job.expect * 100)}%`}}/></span>}</div>}
            {failure && <div className="preview-busy failed" role="alert"><span>{failure}</span><button onClick={() => setHidden(desk.job.id)} aria-label="Dismiss message">×</button></div>}
          </div>
          <div className="caption"><span title={video ? (video.files || [video]).map(f => baseName(f.file)).join("\n") : undefined}>{video ? `Rendered by Film Viewer · original sound · ${(video.files || []).length > 1 ? `${video.files.length} videos saved` : `saved as ${baseName(video.file)}`}${(video.files || []).some(f => f.overlays) ? (desk.overlays?.running ? ` · ${desk.overlays.message} (${desk.overlays.left} left), the desk goes on meanwhile ·` : desk.overlays?.error ? ` · data overlay failed: ${desk.overlays.message} ·` : " with data overlays") : ""} in ${desk.videos}`
            : clip ? `Preview ${formatTime(clip.start)} — ${formatTime(clip.end)}${movable ? " · drag the picture to move the camera" : ""}`
            : still && draft ? `Still at ${formatTime(still.time)} through this camera${movable ? ` · drag to ${turnable(draft) ? "turn" : "move"} it, wheel to zoom` : ""} · Play shows it moving`
            : still ? `Still at ${formatTime(still.time)} · press Play for a moving preview` : "A preview takes about as long to render as it plays."}</span>{video && <span><button onClick={openVideos}>Open folder</button><button onClick={() => setClosedVideo(video.url)}>Back to the film</button></span>}</div>

          <div className="timeline">
            <div className="lane">
              {shots.filter(s => s.id !== draft?.id && s.end > from && s.start < to).map(s => <button key={s.id} className="block" style={span(s)} title={s.name} aria-label={`Shot ${s.name}`} onClick={() => choose(s)}/>)}
              {draft && <><span className="block current" style={span(draft)}/>
                {["start", "end"].map(edge => <button key={edge} className={'trim-handle ' + edge} style={{left: place(draft[edge]) + "%"}}
                  role="slider" aria-label={edge === "start" ? "Shot start" : "Shot end"} aria-orientation="horizontal"
                  aria-valuemin={trimShot(draft, edge, 0, length, shots)[edge]} aria-valuemax={trimShot(draft, edge, length, length, shots)[edge]}
                  aria-valuenow={draft[edge]} aria-valuetext={formatTime(draft[edge])} disabled={conflict}
                  title={(edge === "start" ? "Drag the start" : "Drag the end") + " · snaps to 1 s · arrow keys also work"}
                  onPointerDown={e => trimDown(e, edge)} onPointerMove={trimMove} onPointerUp={trimUp} onPointerCancel={trimUp} onLostPointerCapture={trimUp}
                  onKeyDown={e => trimKey(e, edge)}>{edge === "start" ? <CaretLeft weight="bold"/> : <CaretRight weight="bold"/>}</button>)}
              </>}
              {moments.filter(e => e.time >= from && e.time <= to).slice(0, 300).map(e => <button key={e.id} className={`dot ${moment?.id === e.id ? "selected" : ""}`} style={{left: `${place(e.time)}%`}} title={`${formatTime(e.time)} · ${momentLabel(e)}`} aria-label={`${e.kind} at ${formatTime(e.time)}`} onClick={() => pick(e)}/>)}
            </div>
            <input type="range" aria-label="Film playhead" disabled={!project} min={from} max={to} step="0.1" value={cursor} onChange={e => {
              const at = Number(e.target.value), player = film.current;
              setCursor(at);
              // Inside the clip on screen the picture follows at once; elsewhere a still is fetched.
              if (player && at >= clip.start && at <= clip.end) { player.pause(); player.currentTime = at - clip.start; }
              else scrubbing.current = true;
            }}/>
          </div>
          <div className="transport">
            <button className="play" disabled={!project} onClick={play} aria-label={rolling ? "Pause" : "Play"} title={sequence.length > 1 && !dirty ? `Play the whole sequence, ${sequence.length} shots (Space)` : draft ? "Play this shot (Space)" : `Play the next ${CHUNK} seconds (Space)`}>{rolling ? <Pause weight="fill"/> : <Play weight="fill"/>}</button>
            <button disabled={!project || working} onClick={playLive} title="Play it now in Film Viewer's own window (keep that window on screen), with its sound and nothing rendered. A render after it waits about 12 s, so its sound stays whole."><Play/> In Film Viewer</button>
            <strong>{formatTime(cursor)}</strong><span>/ {formatTime(project?.length)}</span>
            <button disabled={!project || conflict} onClick={another} title={again ? "Another shot of this same stretch: pick a camera, then Add to cut" : "Start a new six-second shot at the playhead"}><Plus/> New shot</button>
            <button disabled={!project} onClick={() => mark("start")} title="Shot starts at the playhead (I)">Set in</button>
            <button disabled={!project} onClick={() => mark("end")} title="Shot ends at the playhead (O)">Set out</button>
            {draft && <span className="range">{formatTime(draft.start)} — {formatTime(draft.end)} · {(draft.end - draft.start).toFixed(1)} s · drag arrows: 1 s</span>}
            <button className="zoom" aria-pressed={zoom && Boolean(draft)} disabled={!draft} onClick={() => setZoom(!zoom)} title="Stretch the timeline around this shot">Close-up</button>
          </div>
        </div>

        <div className="angles panel">
          <div className="panel-heading"><h2>3 <span>Cameras</span></h2>
            <div className="chips"><button disabled={working || !draft || cast.lead == null || draft.end - draft.start < .5} onClick={() => { setRated(rateKey); action("suggest", {shots: candidates(draft, stage)}); }} title="Measure every camera over the whole shot: each then opens on the framing that reads best"><Sparkle/> Rate cameras</button><button className="primary" disabled={working || conflict || !draft || (isSaved && !dirty)} onClick={commit}><Plus/> {isSaved ? "Update shot" : "Add to cut"}</button></div>
          </div>
          {draft ? <>
            {problems[draft.id] && <p className="problem" role="alert">{problems[draft.id]}</p>}
            <div className="around" aria-label="The other aircraft in this shot"><span>Opponent · closest approach</span>
              {near.map(a => <button key={a.id} aria-pressed={cast.other === a.id} disabled={conflict} onClick={() => recast({...cast, other: a.id})} title={`Closest ${a.closest.toLocaleString()} ft, mostly ${a.typical.toLocaleString()} ft away in this shot`}>{a.name || called(a.id)} · {a.closest.toLocaleString()} ft</button>)}
              {cast.other != null && !near.some(a => a.id === cast.other) && <button aria-pressed="true">{called(cast.other)}</button>}
              {cast.other != null && <button disabled={conflict} onClick={() => recast({...cast, other: null})}>Nobody</button>}
              {cast.other == null && !near.length && <span className="note">{desk.nearby?.hero === cast.lead ? `Nobody comes within ${NEAR.toLocaleString()} ft of you in this shot` : "Play the shot to see who is near"}</span>}
            </div>
            <div className="angle-list">{ANGLES.map(a => {
              const why = stage.lead == null || (a.pair && stage.other == null) ? "No other aircraft near in this shot" : a.drop && !dropInShot ? "Nothing is dropped in this shot" : "";
              return <button key={a.key} className={`angle ${angleOf(draft, cast) === a.key ? "selected" : ""}`} disabled={conflict || Boolean(why)} title={why || a.hint} onClick={() => angle(a)}><strong>{a.label}</strong><small>{why || ratingLabel(a, stage, bestVariant(a, stage, scores).rating)}</small></button>;
            })}</div>
            {watching && <div className="around" aria-label="Whom the observer watches"><span>Looks at</span>
              <button aria-pressed={watching === "both"} disabled={conflict || stage.other == null} onClick={() => watch("both")}>Both</button>
              <button aria-pressed={watching === "you"} disabled={conflict} onClick={() => watch("you")} title="Your aircraft alone, from any side">You</button>
              <button aria-pressed={watching === "them"} disabled={conflict || stage.other == null} onClick={() => watch("them")} title="Him alone, on a long lens from a steady seat beside you">Opponent</button>
            </div>}
            {adjustable ? <div className="adjust">
              {tuning.map(([key, label, unit, min, max, step]) => <label key={key}>{label} · {Math.round(draft[key] ?? 0)}{unit}<input type="range" min={min} max={max} step={step} value={draft[key] ?? 0} onChange={e => tune({...draft, [key]: Number(e.target.value)})}/></label>)}
              {["wing", "shoulder", "gear"].includes(draft.kind) && <label>Side<select value={draft.side} onChange={e => tune({...draft, side: e.target.value})}><option value="right">right</option><option value="left">left</option></select></label>}
            </div> : !conflict && !video && <p className="hint">This camera shows the flight as it was: there is nothing to adjust.</p>}
            {adjustable && <p className="hint">{movable && turnable(draft) ? "Or drag the picture to turn this camera where it looks; the wheel over the picture zooms the lens." : movable ? "Or drag the picture: sideways walks the camera round the aircraft, up and down changes its height; the wheel over the picture zooms the lens." : "The wheel over the picture zooms the lens."}{watching === "both" && " The observer opens its lens to hold you both; further off, it flies more of a line of its own."}</p>}
            <details className="tune"><summary>Shot details{dirty ? " · not in the cut yet" : ""}</summary>
              <div className="fields">
                <label>In · seconds<input type="number" step="0.1" min="0" value={draft.start} onChange={e => change({...draft, start: Number(e.target.value)})}/></label>
                <label>Out · seconds<input type="number" step="0.1" min="0" value={draft.end} onChange={e => change({...draft, end: Number(e.target.value)})}/></label>
                <label>Lead aircraft<select value={cast.lead ?? ""} onChange={e => e.target.value !== "" && recast({...cast, lead: Number(e.target.value)})}>{cast.lead == null && <option value="">Choose</option>}{planes.filter(p => p.id !== cast.other).map(p => <option key={p.id} value={p.id}>{p.name}{p.recorder ? " · recorder" : ""}</option>)}</select></label>
                <label className="wide">Shot name<input value={draft.name} onChange={e => change({...draft, name: e.target.value})}/></label>              </div>
            </details>
            <div className="tune-actions"><button onClick={() => { const shot = shots.find(s => s.id === draft.id); if (shot) open({...shot}, false); else { setDraft(null); setDirty(false); } }}>Discard changes</button>{isSaved && <button className="danger" disabled={working} onClick={remove}><Trash/> Remove shot</button>}</div>
          </> : <p className="empty-small">Click a moment, or press New shot at the playhead. The cameras for that shot appear here.</p>}
        </div>
      </section>

      <aside className="cut panel" aria-label="Shots to render">
        <div className="panel-heading"><h2>4 <span>The cut</span></h2><span className="heading-actions">{shots.length} shots<button className="danger small" disabled={working || !shots.length} onClick={() => setForgetting({ids: shots.map(s => s.id), label: "all"})} title="Remove every shot from the cut, and choose whether their videos go too"><Trash/> Clear</button></span></div>
        <div className="cut-list">{shots.map(s => <div key={s.id} className={`cut-row ${draft?.id === s.id ? "selected" : ""}`}>
          <input type="checkbox" aria-label={`Render ${s.name}`} checked={!skipped.has(s.id)} onChange={() => setSkipped(old => { const next = new Set(old); if (!next.delete(s.id)) next.add(s.id); return next; })}/>
          <button onClick={() => choose(s)}><strong>{s.name}</strong><small>{formatTime(s.start)} — {formatTime(s.end)} · {camera(s)}</small>{problems[s.id] && <small className="problem">Cannot be filmed: open it for the reason</small>}</button>
          <button className="forget" disabled={working} onClick={() => setForgetting({ids: [s.id], label: s.name})} aria-label={`Remove ${s.name}`} title="Remove this shot from the cut"><X weight="bold"/></button>
        </div>)}
          {!shots.length && <p className="hint">Shots you add appear here. Tick the ones to render.</p>}
        </div>
        <div className="cut-actions">
          <button disabled={working || dirty || !uncovered.length} onClick={reel} title="Four shots for every kill of yours that has none yet: closing in, the guns, him going down, you flying on">Add my kills ({uncovered.length})</button>
          <button disabled={working || dirty || !project} onClick={airfield} title="A few shots for every take-off and landing of yours that has none yet, found in your flight path">Take-offs & landings</button>
          <select aria-label="Render quality" value={quality} onChange={e => setQuality(e.target.value)}><option value="final">1080p60</option><option value="4k">4K · 2160p60</option><option value="draft">Quick draft</option></select>
          <select aria-label="Render as" value={apart ? "apart" : "one"} onChange={e => setApart(e.target.value === "apart")} title="One video with the gaps left out, or a file for every shot to cut in an editor"><option value="one">One video</option><option value="apart">A file per shot</option></select>
          <label className="option" title={desk.ffmpeg ? "Beside every video, a transparent film of your speed, height, climb and G and the range to your opponent, to lay over it in an editor: one for a 16:9 timeline, one for a 9:16 reel" : "Needs ffmpeg on the PATH"}><input type="checkbox" checked={withOverlay && !!desk.ffmpeg} disabled={!desk.ffmpeg} onChange={e => setWithOverlay(e.target.checked)}/> Data overlay (16:9 and 9:16)</label>
          <label className="option" title={desk.ffmpeg ? "Film Viewer renders the video four times, a quarter of a frame apart, and ffmpeg blends them like a camera's shutter: real blur on fast motion. Takes about four times as long" : "Needs ffmpeg on the PATH"}><input type="checkbox" checked={withBlur && !!desk.ffmpeg} disabled={!desk.ffmpeg} onChange={e => setWithBlur(e.target.checked)}/> Motion blur</label>
          <label className="option" title={desk.ffmpeg ? "Colour, bloom, a red glow round highlights, a vignette and grain, like a film camera: fitted to a DaVinci Resolve grade of this footage" : "Needs ffmpeg on the PATH"}><input type="checkbox" checked={withLook && !!desk.ffmpeg} disabled={!desk.ffmpeg} onChange={e => setWithLook(e.target.checked)}/> Film look</label>
          <label className="option" title={desk.ffmpeg ? "Mist along the horizon: the far ground and the sky just above it fade into haze, found by colour. Aircraft against the sky and the cockpit stay clear" : "Needs ffmpeg on the PATH"}><input type="checkbox" checked={withHaze && !!desk.ffmpeg} disabled={!desk.ffmpeg} onChange={e => setWithHaze(e.target.checked)}/> Haze</label>
          <button className="primary" disabled={working || dirty || !chosen.length} onClick={() => { asking.current = "cut"; action("export_cut", {shot_ids: chosen, quality, apart, overlay: withOverlay && !!desk.ffmpeg, blur: withBlur && !!desk.ffmpeg, look: withLook && !!desk.ffmpeg, haze: withHaze && !!desk.ffmpeg, labels: Object.fromEntries(shots.map(s => [s.id, camera(s)]))}); }} title={dirty ? "Add or update the shot first" : apart ? "A video of each ticked shot, in its own file" : "One video of the ticked shots, gaps left out; shots of the same stretch get a video each"}><FilmSlate/> Render selected ({chosen.length})</button>
          <button onClick={openVideos} title={`Finished videos are kept in ${desk.videos}`}><FolderOpen/> Videos folder</button></div>
      </aside>
    </section>
    {trashing && <div className="modal" role="dialog" aria-modal="true" aria-labelledby="trash-title" onKeyDown={e => e.key === "Escape" && setTrashing(null)}>
      <div className="modal-box">
        <h2 id="trash-title">Move the replay “{trashing.name}” to the Recycle Bin?</h2>
        <p>{trashing.mb} MB. Its saved shots stay with the desk, and videos made from it stay in your Videos folder. You can restore it from the Recycle Bin.</p>
        <div className="modal-actions">
          <button autoFocus onClick={() => setTrashing(null)}>Cancel</button>
          <button className="danger" disabled={sending} onClick={deleteFilm}><Trash/> Move to the Recycle Bin</button>
        </div>
      </div>
    </div>}
    {forgetting && (() => {
      const files = filesOf(forgetting.ids), many = forgetting.ids.length > 1;
      return <div className="modal" role="dialog" aria-modal="true" aria-labelledby="forget-title" onKeyDown={e => e.key === "Escape" && setForgetting(null)}>
        <div className="modal-box">
          <h2 id="forget-title">{many ? `Remove all ${forgetting.ids.length} shots from the cut?` : `Remove “${forgetting.label}” from the cut?`}</h2>
          {files.length ? <><p>{many ? "Their" : "Its"} finished videos on disk{files.length > 1 ? ` (${files.length} files)` : ""}:</p><ul>{files.map(f => <li key={f}>{baseName(f)}</li>)}</ul></>
            : <p className="hint">No finished video of {many ? "them" : "it"} is on record. Videos rendered before this version are not tracked: delete them in the Videos folder.</p>}
          <div className="modal-actions">
            <button autoFocus onClick={() => setForgetting(null)}>Cancel</button>
            <button className={files.length ? "" : "danger"} disabled={sending} onClick={() => forget(false)}>{files.length ? "Remove, keep the files" : "Remove"}</button>
            {files.length > 0 && <button className="danger" disabled={sending} onClick={() => forget(true)}><Trash/> Remove and move {files.length === 1 ? "the file" : `${files.length} files`} to the Recycle Bin</button>}
          </div>
        </div>
      </div>;
    })()}
  </main>;
}
