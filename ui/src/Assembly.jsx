import {useEffect, useRef, useState} from "react";
import {CaretLeft, CaretLineLeft, CaretLineRight, CaretRight, FilmSlate, FolderOpen, MusicNotes, Pause, Play, Scissors, SkipBack, SkipForward, Stop, X} from "@phosphor-icons/react";
import {formatTime, request} from "./desk.js";
import {cutAt, cutBeside, grouped, laid, lengthOf, moved, musicGain, placed, stateOf, ticked, trimmed, under, waveLine, without} from "./assembly.js";

const EMPTY = {clips: [], off: [], music: null, sound: 1};
const QUALITY = {draft: "Draft", final: "1080p", "4k": "4K"};
const EXTRAS = {blur: "motion blur", look: "film look", haze: "haze", trails: "trails"};
// A video of a shot, told from its others: how it was rendered, and when. One recorded before videos kept their
// frame rate was rendered at the rate its size had then.
const told = v => [QUALITY[v.quality] || "Video", ...(v.quality ? [`${v.fps || (v.quality === "draft" ? 30 : 60)} fps`] : []), ...v.extras.map(e => EXTRAS[e] || e),
  v.made ? new Date(v.made * 1000).toLocaleString([], {day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"}) : "earlier",
  ...(v.changed ? ["before its camera changed"] : [])].join(" · ");

// A stretch of a sound's waveform (waveLine), filling its box, mirrored round the middle.
function Wave({wave, from, seconds, gain}) {
  const line = wave?.peaks.length ? waveLine(wave, from, seconds, gain) : [];
  if (line.length < 2) return null;
  const at = (v, i) => `${i},${(50 - v * 48).toFixed(1)}`, below = (v, i) => `${i},${(50 + v * 48).toFixed(1)}`;
  return <svg className="wave" viewBox={`0 0 ${line.length - 1} 100`} preserveAspectRatio="none" aria-hidden="true">
    <polygon points={[...line.map(at), ...line.map(below).reverse()].join(" ")}/></svg>;
}

// A track's level in percent, typed or stepped by 5: 100% as recorded, up to 200%. The page plays at most at full,
// so above 100% is heard in the export only.
const level = (value, set, name) => <label className="level" title={`${name}: 100% is as recorded, up to 200% in the export (the page plays at most 100%)`}>
  <input type="number" min="0" max="200" step="5" value={Math.round(value * 100)} aria-label={`${name} level, percent`} onFocus={e => e.target.select()}
    onChange={e => { const n = e.target.valueAsNumber; if (!Number.isNaN(n)) set(Math.max(0, Math.min(200, n)) / 100); }}/>%</label>;

// The second view: the film's rendered shots in a row, trimmed, cut and set to music, then exported as one video by
// ffmpeg. The viewer has no part in it. What it shows is worked out in assembly.js; the desk keeps it per film.
export function Assembly({desk, busy, cameraOf, onRender, onOpenShot, onError, openVideos}) {
  const project = desk.project, shots = project?.shots || [], versions = desk.versions || {};
  const [mine, setMine] = useState(null);  // the assembly as changed on this page; the desk's until then
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(0);
  const [at, setAt] = useState(0);  // the playhead, in seconds of the assembly
  const [playing, setPlaying] = useState(false);
  const [sought, setSought] = useState(0);  // counts the playhead's jumps: the players follow each
  const [quality, setQuality] = useState("final");
  const [fps, setFps] = useState(30);
  const [trimming, setTrimming] = useState(null);  // the clips while an end is dragged
  const [widest, setWidest] = useState(0);  // the longest the assembly has been on this page: the timeline's scale
  const [waves, setWaves] = useState({});  // "video/<id>" or "music/<file>": its sound's waveform, as the desk measured it
  const waveAsked = useRef(new Set());
  const film = useRef(null), song = useRef(null), lane = useRef(null), grab = useRef(null), picker = useRef(null), scrubbing = useRef(false);

  useEffect(() => { setMine(null); setAt(0); setSelected(0); setPlaying(false); setWidest(0); }, [project?.film]);

  const assembly = mine ?? desk.assembly ?? EMPTY, music = assembly.music;
  const clips = trimming ?? placed(assembly, shots, versions);
  const rows = laid(clips, versions), length = lengthOf(rows);
  // The timeline never zooms in on what is left: a clip that is shortened is seen to get shorter, and leaves room
  // behind it. Fitted to the assembly, one clip filled the lane whatever was cut off it.
  const span = Math.max(widest, length);
  useEffect(() => { if (length > widest) setWidest(length); }, [length]);
  const index = under(rows, Math.min(at, Math.max(0, length - .01))), row = rows[index];
  const pick = Math.min(selected, rows.length - 1), chosen = rows[pick];
  const byId = new Map(shots.map(s => [s.id, s]));
  const making = desk.assembling;
  const stale = rows.filter(r => r.version.changed).length;
  const toRender = shots.filter(s => stateOf(s, versions) !== "rendered").map(s => s.id);

  function commit(next) {
    setMine(next);
    request("/api/assembly", {assembly: next}).catch(e => onError(e.message));
  }
  const change = next => commit({...assembly, clips: next});
  function go(time) { setAt(Math.max(0, Math.min(length, time))); setSought(n => n + 1); }
  function play() {
    if (!rows.length) return;
    if (!playing && at >= length - .05) go(0);
    setPlaying(!playing);
  }

  // Each video's and the music's waveform, asked of the desk once (ffmpeg reads the sound there).
  useEffect(() => {
    for (const key of [...rows.map(r => `video/${r.version.id}`), ...(music ? [`music/${music.file}`] : [])]) {
      if (waveAsked.current.has(key)) continue;
      waveAsked.current.add(key);
      request(`/api/wave/${key}`).then(wave => setWaves(old => ({...old, [key]: wave}))).catch(() => waveAsked.current.delete(key));
    }
  });

  // The players follow the playhead: the clip under it at the right moment of its video, the music beside it.
  useEffect(() => {
    const player = film.current, tune = song.current;
    if (!player || !row) return;
    const want = row.start + Math.max(0, at - row.at);
    if (player.dataset.video !== row.version.id) { player.dataset.video = row.version.id; player.src = `/media/video/${row.version.id}`; player.currentTime = want; }
    else if (Math.abs(player.currentTime - want) > .25) player.currentTime = want;
    player.volume = Math.min(1, row.sound * assembly.sound);
    if (playing) player.play().catch(() => {}); else player.pause();
    if (tune && music) {
      if (Math.abs(tune.currentTime - (music.from + at)) > .25) tune.currentTime = music.from + at;
      tune.volume = Math.min(1, music.level * musicGain(music, at, length));
      if (playing) tune.play().catch(() => {}); else tune.pause();
    }
  }, [row?.version.id, index, playing, sought, row?.start, row?.sound, assembly.sound, music?.file, music?.from, music?.level]);

  // While it plays the playhead follows the clip's own clock, and steps to the next clip at this one's end.
  useEffect(() => {
    if (!playing) return;
    let frame = requestAnimationFrame(function step() {
      const player = film.current;
      if (player && row) {
        const local = player.currentTime - row.start;
        if (local >= row.seconds - .04 || player.ended) {
          if (index + 1 < rows.length) setAt(rows[index + 1].at + .001); else { setPlaying(false); setAt(length); }
        } else setAt(row.at + Math.max(0, local));
        if (song.current && music) song.current.volume = Math.min(1, music.level * musicGain(music, at, length));
      }
      frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  });

  useEffect(() => {
    function key(e) {
      if (e.ctrlKey || e.metaKey || e.altKey || e.target.matches("input:not([type=range]):not([type=checkbox]),select,textarea,a")) return;
      // Home and End, the start and end of the whole; Up and Down, the cut before and after, as in an editor.
      const to = {Home: 0, End: length, ArrowUp: cutBeside(rows, at, -1), ArrowDown: cutBeside(rows, at, 1)}[e.key];
      if (e.key === " ") { e.preventDefault(); play(); }
      else if (e.key === "s") cut();
      else if (e.key in {Home: 1, End: 1, ArrowUp: 1, ArrowDown: 1} && !e.target.matches("input") && rows.length) { e.preventDefault(); if (to != null) go(to); }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  });

  function cut() {
    const next = cutAt(clips, rows, at);
    if (next) { change(next); setSelected(index + 1); }
  }
  function remove(i) { commit(without(assembly, clips, i)); setSelected(Math.max(0, i - 1)); }
  const update = (i, fields) => change(clips.map((c, n) => n === i ? {...c, ...fields} : c));

  // An end of the selected clip dragged: the timeline's scale is the one the drag began on.
  function trimDown(e, i, edge) {
    e.stopPropagation(); e.preventDefault();
    grab.current = {i, edge, x: e.clientX, clips, rows, scale: span / lane.current.clientWidth};
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* no such pointer: the drag still follows while over the grip */ }
    setPlaying(false);
  }
  function trimMove(e) {
    const held = grab.current;
    if (!held) return;
    const next = trimmed(held.clips, held.rows, held.i, held.edge, (e.clientX - held.x) * held.scale), now = laid(next, versions)[held.i];
    setTrimming(next);
    // The picture shows the frame the clip now begins or ends on: what is cut is seen while it is cut.
    setAt(held.edge === "start" ? now.at + .01 : now.at + now.seconds - .05); setSought(n => n + 1);
  }
  function trimUp() {
    if (grab.current && trimming) change(trimming);
    grab.current = null; setTrimming(null);
  }
  // A moment of the timeline from a pointer: inside the lanes' border, where the clips are laid.
  const timeAt = e => { const el = lane.current, box = el.getBoundingClientRect(); return (e.clientX - box.left - el.clientLeft) / el.clientWidth * span; };
  // The playhead is dragged, or the ruler: the picture follows. The arrow keys move it a tenth of a second (Shift: one).
  function scrubDown(e) {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    e.currentTarget.setPointerCapture(e.pointerId); scrubbing.current = true;
    setPlaying(false); go(timeAt(e));
  }
  const scrubMove = e => { if (scrubbing.current) go(timeAt(e)); };
  const scrubUp = () => { scrubbing.current = false; };
  function scrubKey(e) {
    const step = {ArrowLeft: -1, ArrowRight: 1}[e.key];
    if (!step) return;
    e.preventDefault(); e.stopPropagation(); setPlaying(false); go(at + step * (e.shiftKey ? 1 : .1));
  }

  async function chooseMusic(e) {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      const response = await fetch("/api/music", {method: "POST", headers: {"X-Name": encodeURIComponent(file.name)}, body: file});
      const kept = await response.json();
      if (!response.ok) throw new Error(kept.message || "The music could not be kept");
      commit({...assembly, clips, music: {...kept, from: 0, level: .45, in: 1, out: 3}});
    } catch (error) { onError(error.message); }
  }
  async function exportVideo() {
    setPlaying(false);
    try { await request("/api/assemble", {assembly: {...assembly, clips}, quality, fps}); setMine({...assembly, clips}); }
    catch (e) { onError(e.message); }
  }

  if (!project) return <section className="assembly"><p className="empty-small">Open a replay in Shots first: its rendered shots line up here.</p></section>;
  const step = span <= 60 ? 10 : span <= 300 ? 30 : 60;
  const share = seconds => `${span ? seconds / span * 100 : 0}%`;
  const held = trimming && grab.current ? {...grab.current, row: rows[grab.current.i]} : null;
  const partOf = id => { const shot = byId.get(id); return shot.name.includes(" · ") ? shot.name.split(" · ").slice(1).join(" · ") : shot.name; };

  return <section className="assembly">
    <aside className="panel" aria-label="The film's shots">
      <div className="panel-heading"><h2>1 <span>Shots</span></h2><span>{shots.length} · {new Set(clips.map(c => c.shot)).size} on the timeline</span></div>
      <div className="filters" aria-label="Which shots">{[["all", "All"], ["rendered", "Rendered"], ["render", "To render"]].map(([value, label]) =>
        <button key={value} aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div>
      <div className="shot-list">
        {grouped(shots).map(group => {
          const shown = group.shots.filter(s => filter === "all" || (stateOf(s, versions) === "rendered") === (filter === "rendered")
            || (filter === "rendered" && stateOf(s, versions) === "changed"));  // a changed shot has a video, and wants another
          return shown.length > 0 && <div key={group.shots[0].id}>
            {group.head && <p className="group"><span>{group.head}</span><span>{formatTime(group.time)}</span></p>}
            {shown.map(s => {
              const state = stateOf(s, versions), first = clips.findIndex(c => c.shot === s.id);
              return <div key={s.id} className={`shot ${chosen?.shot === s.id ? "selected" : ""}`}>
                <input type="checkbox" aria-label={`${s.name} on the timeline`} disabled={state === "none"} checked={first >= 0}
                  onChange={e => commit(ticked(assembly, clips, s.id, e.target.checked))}/>
                <button className="what" disabled={first < 0} onClick={() => { setSelected(first); go(rows[first].at); }}>
                  <strong>{s.part}</strong><small>{formatTime(s.start)} — {formatTime(s.end)} · {cameraOf(s)}</small></button>
                {state === "none" ? <button className="small" disabled={busy} onClick={() => onRender([s.id])} title="Render this shot as a video of its own">Render</button>
                  : <span className={`mark ${state}`} title={state === "changed" ? "Its camera or stretch was changed after this video was made: render it again" : `${versions[s.id].length} video${versions[s.id].length > 1 ? "s" : ""} of this shot`}>
                    {state === "changed" ? "Changed" : versions[s.id].length > 1 ? `Rendered · ${versions[s.id].length}` : "Rendered"}</span>}
              </div>;
            })}
          </div>;
        })}
        {!shots.length && <p className="hint">Shots you add in Shots appear here. Rendered, each as a file of its own, they line up on the timeline.</p>}
      </div>
      <div className="list-actions">
        <button disabled={busy || !toRender.length} onClick={() => onRender(toRender)} title="Renders every shot that has no video of its own, or whose camera changed since, each as a file of its own, with the options set under The cut">Render what is missing ({toRender.length})</button>
      </div>
    </aside>

    <section className="middle">
      <div className="panel">
        <div className="panel-heading"><h2>2 <span>Picture</span></h2><span>The clips one after another, as they will be exported; a dissolve shows here as a cut</span></div>
        <div className="screen">
          {rows.length ? <video ref={film} playsInline onClick={play}/>
            : <div className="empty-preview"><FilmSlate size={44} weight="thin"/><h2>Nothing on the timeline yet.</h2><p>Render shots, each as a file of its own.<br/>They line up here in film order.</p></div>}
          {music && <audio ref={song} src={`/media/music/${music.file}`} preload="auto"/>}
        </div>
        <div className="transport">
          <button className="play" disabled={!rows.length || at <= 0} onClick={() => go(0)} aria-label="To the start" title="To the start (Home)"><SkipBack weight="fill"/></button>
          <button className="play" disabled={!rows.length || cutBeside(rows, at, -1) == null} onClick={() => go(cutBeside(rows, at, -1))} aria-label="Previous cut" title="To the cut before: where a clip starts or ends (Up arrow)"><CaretLineLeft weight="bold"/></button>
          {playing ? <button className="play" onClick={() => setPlaying(false)} aria-label="Pause" title="Pause (Space)"><Pause weight="fill"/></button>
            : <button className="play" disabled={!rows.length} onClick={play} aria-label="Play" title="Play the assembly (Space)"><Play weight="fill"/></button>}
          <button className="play" disabled={!rows.length || (!playing && at === 0)} onClick={() => { setPlaying(false); go(0); }} aria-label="Stop" title="Stop and go back to the start"><Stop weight="fill"/></button>
          <button className="play" disabled={!rows.length || cutBeside(rows, at, 1) == null} onClick={() => go(cutBeside(rows, at, 1))} aria-label="Next cut" title="To the next cut: where a clip starts or ends (Down arrow)"><CaretLineRight weight="bold"/></button>
          <button className="play" disabled={!rows.length || at >= length} onClick={() => go(length)} aria-label="To the end" title="To the end (End)"><SkipForward weight="fill"/></button>
          <strong>{formatTime(at)}</strong><span>/ {formatTime(length)}</span>
          <button disabled={!cutAt(clips, rows, at)} onClick={cut} title="Cuts the clip under the playhead in two (S)"><Scissors/> Cut at the playhead</button>
          <button disabled={!chosen} onClick={() => remove(pick)} title="Takes the selected clip off the timeline; its video stays in the Videos folder">Remove clip</button>
          <span className="count">{rows.length} clip{rows.length === 1 ? "" : "s"}</span>
        </div>
      </div>

      <div className="panel">
        <div className="panel-heading"><h2>3 <span>Timeline</span></h2><span>Drag the playhead or the ruler · drag a clip to move it · drag the arrows at the ends of the selected one to shorten it</span></div>
        <div className="lanes">
          <span/>
          <div className="ruler" onPointerDown={scrubDown} onPointerMove={scrubMove} onPointerUp={scrubUp} onPointerCancel={scrubUp}>{Array.from({length: Math.floor(span / step) + 1}, (_v, n) => <span key={n} style={{left: share(n * step)}}>{formatTime(n * step).slice(0, 5)}</span>)}</div>
          <span className="lane-name"><b>Video</b>the shots, in order</span>
          <div className="lane" ref={lane} onClick={e => go(timeAt(e))}>
            {rows.map((r, i) => <button key={i} className={`clip ${i === pick ? "current" : ""} ${r.version.changed ? "changed" : ""}`}
              style={{left: share(r.at), width: share(r.seconds)}} title={`${byId.get(r.shot).name} · ${told(r.version)}`}
              draggable onDragStart={e => { if (grab.current) return e.preventDefault(); e.dataTransfer.setData("text/plain", String(i)); e.dataTransfer.effectAllowed = "move"; }}
              onDragOver={e => e.preventDefault()}
              onDrop={e => { e.preventDefault(); const from = Number(e.dataTransfer.getData("text/plain")); if (Number.isInteger(from) && from !== i) { change(moved(clips, from, i)); setSelected(i); } }}
              onClick={e => { e.stopPropagation(); setSelected(i); go(timeAt(e)); }}>
              {i === pick && ["start", "end"].map(edge => <span key={edge} className={`trim ${edge}`} title="Drag to trim this end"
                onPointerDown={e => trimDown(e, i, edge)} onPointerMove={trimMove} onPointerUp={trimUp} onPointerCancel={trimUp} onClick={e => e.stopPropagation()}>{edge === "start" ? <CaretLeft weight="bold"/> : <CaretRight weight="bold"/>}</span>)}
              <b>{partOf(r.shot)}</b><i>{r.seconds.toFixed(1)} s{r.seconds < r.version.seconds - .005 ? ` of ${r.version.seconds.toFixed(1)}` : ""}{r.version.changed ? " · changed" : ""}</i>
            </button>)}
            {held && <span className="trim-note" style={{left: share(held.edge === "start" ? held.row.at : held.row.at + held.row.seconds)}}>
              {held.edge === "start" ? `Starts ${held.row.start.toFixed(1)} s later` : `Ends ${held.row.end.toFixed(1)} s earlier`} · {held.row.seconds.toFixed(1)} s of {held.row.version.seconds.toFixed(1)} left</span>}
          </div>
          <span className="lane-name" title="The film's sound: engine, guns, radio"><b>Film sound</b>{level(assembly.sound, sound => commit({...assembly, clips, sound}), "Film sound")}</span>
          {/* The film's sound is cut with the picture: each clip's own stretch of its waveform, as loud as it is set. */}
          <div className="lane thin" onClick={e => go(timeAt(e))}>{rows.map((r, i) => <span key={i} className={`sound ${i === pick ? "current" : ""}`} style={{left: share(r.at), width: share(r.seconds)}}>
            <Wave wave={waves[`video/${r.version.id}`]} from={r.start} seconds={r.seconds} gain={() => r.sound * assembly.sound}/></span>)}</div>
          <span className="lane-name"><b>Music</b>{music ? level(music.level, value => commit({...assembly, clips, music: {...music, level: value}}), "Music") : "your own file"}</span>
          <div className="lane thin" onClick={e => go(timeAt(e))}>
            {music && <span className="music" style={{width: share(length)}}><Wave wave={waves[`music/${music.file}`]} from={music.from} seconds={length} gain={t => music.level * musicGain(music, t, length)}/>
              <span>{music.name} · from {formatTime(music.from)} · in {music.in} s · out {music.out} s</span></span>}
          </div>
          {/* One playhead through the ruler and every lane, over all of them: the arrow and the line are one element. */}
          {rows.length > 0 && <div className="scrub"><span className="playhead" style={{left: share(at)}} role="slider" tabIndex={0} aria-label="Playhead"
            aria-valuemin={0} aria-valuemax={length} aria-valuenow={at} aria-valuetext={formatTime(at)} title="Drag the playhead; arrow keys move it a tenth of a second, with Shift a second"
            onPointerDown={scrubDown} onPointerMove={scrubMove} onPointerUp={scrubUp} onPointerCancel={scrubUp} onKeyDown={scrubKey}/></div>}
        </div>
        <div className="lane-tools">
          {music && <label>From · seconds <input type="number" min="0" step="1" value={music.from} aria-label="Where in the music it starts" onChange={e => commit({...assembly, clips, music: {...music, from: Math.max(0, Number(e.target.value) || 0)}})}/></label>}
          <input ref={picker} type="file" accept=".mp3,.wav,.m4a,.aac,.flac,.ogg,audio/*" hidden onChange={chooseMusic}/>
          <button onClick={() => picker.current.click()} title="An mp3, wav, m4a, aac, flac or ogg file of your own: it plays under the film's sound, fades in, and fades out with the last clip"><MusicNotes/> {music ? "Other music…" : "Choose music…"}</button>
          {music && <button onClick={() => commit({...assembly, clips, music: null})} aria-label="Take the music off" title="Take the music off"><X/></button>}
        </div>
      </div>
    </section>

    <aside className="side">
      <div className="panel">
        <div className="panel-heading"><h2>4 <span>Clip</span></h2><span>{chosen ? `${pick + 1} of ${rows.length}` : ""}</span></div>
        {chosen ? <>
          <p className="clip-name"><strong>{byId.get(chosen.shot).name}</strong><small>{formatTime(byId.get(chosen.shot).start)} — {formatTime(byId.get(chosen.shot).end)} · {cameraOf(byId.get(chosen.shot))}</small></p>
          <div className="fields">
            <label className="wide">Video of this shot
              <select value={chosen.version.id} onChange={e => update(pick, {video: e.target.value})}>{versions[chosen.shot].map(v => <option key={v.id} value={v.id}>{told(v)}</option>)}</select></label>
            <label>Starts later by · s<input type="number" min="0" step="0.1" value={chosen.start} onChange={e => change(trimmed(clips, rows, pick, "start", (Number(e.target.value) || 0) - chosen.start))}/></label>
            <label>Ends earlier by · s<input type="number" min="0" step="0.1" value={chosen.end} onChange={e => change(trimmed(clips, rows, pick, "end", chosen.end - (Number(e.target.value) || 0)))}/></label>
            <label className="wide">Joins the next clip with
              <select value={chosen.join} disabled={pick === rows.length - 1} onChange={e => update(pick, {join: e.target.value})}><option value="cut">A cut</option><option value="dissolve">A dissolve of 0.3 s</option></select></label>
            <label className="wide">Its film sound · {Math.round(chosen.sound * 100)}%<input type="range" min="0" max="100" value={Math.round(chosen.sound * 100)} onChange={e => update(pick, {sound: e.target.value / 100})}/></label>
          </div>
          <div className="row-actions"><button onClick={() => onOpenShot(byId.get(chosen.shot))} title="Back to Shots with this shot selected: change its camera, render it again">Open the shot</button>
            <button className="danger" onClick={() => remove(pick)}>Remove clip</button></div>
        </> : <p className="hint">Click a clip on the timeline: its video, its ends and its sound are set here.</p>}
      </div>

      <div className="panel">
        <div className="panel-heading"><h2>5 <span>Export</span></h2><span>{formatTime(length)}</span></div>
        <div className="export">
          <div className="pair"><select aria-label="Quality" value={quality} onChange={e => setQuality(e.target.value)}><option value="final">1080p</option><option value="4k">4K · 2160p</option><option value="draft">Quick draft · 540p</option></select>
            <select aria-label="Frame rate" value={fps} onChange={e => setFps(Number(e.target.value))} title="The assembly's frame rate: the clips' own (30 fps unless rendered at 60) keeps every frame as it was"><option value={30}>30 fps</option><option value={60}>60 fps</option></select></div>
          {stale > 0 && <p className="hint">{stale === 1 ? "One clip was" : `${stale} clips were`} rendered before {stale === 1 ? "its" : "their"} camera changed. Export uses the video {stale === 1 ? "it has" : "each has"}.</p>}
          {making?.running ? <p className="hint working"><span className="spinner"/> {making.message}. The desk goes on meanwhile.</p>
            : making?.error ? <p className="hint problem" role="alert">{making.error}</p>
            : making?.file && <p className="hint">Saved as {making.file.split(/[\\/]/).pop()} in {desk.videos}. <a href="/media/assembly" target="_blank" rel="noreferrer">Play it</a></p>}
          <button className="primary" disabled={!rows.length || making?.running || !desk.ffmpeg} onClick={exportVideo} title={desk.ffmpeg ? "One video of the timeline, with the music, made by ffmpeg" : "Needs ffmpeg on the PATH"}><FilmSlate/> Export video</button>
          <button onClick={openVideos} title={`Finished videos are kept in ${desk.videos}`}><FolderOpen/> Videos folder</button>
        </div>
      </div>
    </aside>
  </section>;
}
