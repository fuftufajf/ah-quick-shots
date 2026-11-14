# How AH Quick Shots works

Requires Film Viewer 3.07 Patch 2 (socket protocol 5) or newer. Start the viewer,
then `AH Quick Shots.bat`; the desk connects by itself.

```
 browser page            local server               Film Viewer
 (ui/, React)   HTTP     (director_api.py,   TCP    (the offline viewer,
 127.0.0.1:8766 <------> director_core.py)  <-----> its documented text
                         one worker thread          socket, 127.0.0.1)
```

The page never talks to the viewer. The server sends the viewer the plain-text
commands its own `help` and `help director` describe (open, seek, cam, shot,
video, tracks, project, events), one at a time, and serves the stills and
videos the viewer writes. Nothing reads the viewer's memory or its film files,
and nothing touches the game. Only this server owns the socket: a second
connection disconnects the first.

## Working down the page

**Moments.** The film library lists the newest film first. "Fights" is one row
per engagement of the recording pilot: their bursts, hits given and taken and
kills, grouped when less than twelve seconds apart, and closed by a kill of
theirs. A row names the kill, or the hits, and its opponent is whoever took the
most hits, because a kill message or a stray round names an aircraft that may
have had little to do with the fight. When the pilot only takes hits, the film
does not say from whom, so the opponent is whoever was firing nearby at that
moment. "Mine" lists the same events one by one; a film's kill list is the
whole arena's, so the other filters are for everything else. Clicking a moment
prepares a shot around it and plays it: a fight from four seconds before its
key moment (the heaviest hits) to three after; a kill, or a fight that ends in
one, from five seconds before the kill message to two and a half past it; a
drop from one second before to fourteen after; anything else from five before
to two after. The film's ends and the saved shots beside it trim that. A
moment inside a saved shot selects that shot instead. Before the clip is made
the desk reads who was around: if the moment's aircraft never comes within
3,000 ft while another passes within 1,500 ft, the nearer one is taken.

**Picture.** The socket has no picture stream, and the viewer renders a video
in about the time it plays, whatever its size (measured: five seconds of film
in five seconds at 480×270 and at 960×540). So the desk's moving picture is a
preview clip: the selected shot rendered once at 960×540 with sound, then
replayed, paused and scrubbed from its file. A still of the moment shows while
it renders. A clip is kept under `clips/` in the output folder (the newest 80)
and reused for as long as the stretch, its cameras and Names are the same, also
after a restart; going back to a camera already tried is instant.
With no shot selected, Play renders the next ten seconds from the playhead.
Dragging the playhead inside the clip moves the picture at once; elsewhere it
fetches a still with the viewer's rough seek as soon as it rests, and the
latest position wins if the viewer is still busy.
Set in and Set out (keys I and O) mark the shot at the playhead; Space plays
and pauses. New shot, with the playhead inside the selected shot, begins another
shot of that same stretch, starting from its camera: the usual next step is the
same moment through other cameras, each saved as a shot of its own and laid over
the others in an editor. The selected shot stays as it was saved. With the
playhead elsewhere, New shot marks six seconds from it. The arrows at either end of the highlighted timeline
range trim that edge, snapping to whole seconds (the film boundary, neighbouring
shots and the 120-second limit take priority). Arrow keys adjust a focused
handle too. The range follows the drag; releasing it shows a still at that
edge, and Play renders the new stretch through the same camera. Close-up
stretches the timeline around the shot and stays steady while a handle is dragged.
Names switches the pilot labels. Trails and gun cam are not in the socket: tick
them in the viewer's Edit tools.

Looking and rendering are kept apart, because a render cannot be interrupted.
Anything you adjust — a drag on the picture, a slider, Names — shows a still of
the playhead moment through the camera as it now is. The film is not moved and
only that camera is sent, so the still is back in about a tenth of a second
(measured: 0.08 s) and a drag is followed almost live. Nothing is rendered until
Play or a moment asks for the clip. A still has the viewer window's
shape and the video's width of view; drawn at full width it sits where it will
in the 16:9 video, which shows more above and below when the window is wider.
A seek can leave the film up to 0.08 s past the moment asked for (measured; the
viewer tells its time in tenths), so the first still may be a frame or two later
than the clip frame it replaces. After any jump the aircraft would also come
out see-through (see "Aircraft fading in" below), so the desk does not jump
straight to a moment: it goes two seconds before it, plays up to it through the
shot's camera and takes the still there, about two seconds later than a bare
jump. Only a moment in the first two seconds of a film is still taken at once,
and taken again once things have rested.

The pilot's view and the cameras on the airframe are heard as the cockpit was
recorded: engine, guns and the pilots' voices. A camera standing in the world
hears the aircraft from where it stands, quieter with distance (whether the
voices reach it was not checked). For about as long as a rendered stretch lasts after its
render, the viewer is still finishing that stretch's sound, and a render started
in that time can come out heard from a point standing still in the sky instead:
quiet, with no engine, guns or voices (measured on an 11.5 s stretch: thin when
the film was touched 2, 5 or 8 s after a render, full every time 12 s or more
had passed, whatever happened in between). So each render, preview or cut, waits
until the stretch rendered before it has had its length and a second and a half
more; the status says so. Watching a clip to its end costs nothing; asking for
the next camera at once costs the rest of that time.

**Cameras.** One click shows the playhead moment through that camera, as a
still to set the view on (drag, wheel, sliders); Play then renders the shot
through it. A camera already played comes back from its file. Nothing is saved
until Add to cut (or Update shot); the draft stands in for any saved shot it
overlaps while you look. Opponent lists the aircraft that come within 3,000 ft
of you in the shot, nearest first with their closest distance: one click makes
another the opponent. A shot whose camera films you alone names no opponent;
the two-aircraft cameras then take whoever comes nearest.

- You in front / Opponent in front — the camera stands on the line through both
  aircraft, beyond the nearer one and swung a little off it, so one dominates
  and the other is seen past it. Variants stand 120, 300 and 700 ft back, 60 or
  30 ft aside of that line, each on the lens that keeps the nearer aircraft a
  quarter of the picture's width: further back on a longer lens, the far
  aircraft grows (at 1,500 ft, from a dot to a tenth of the picture). The camera
  looks between the two, never further from the nearer one than keeps the whole
  of it in the picture; aimed at the point half way between them, a long lens
  lost the nearer aircraft as soon as the other was far.
- Observer — a third aircraft flying with you, carrying the camera. It keeps station off the line
  through both aircraft, so the opponent is beyond you and a little aside, but
  it is heavy: it follows your path, and that line, averaged over one to four
  seconds either side, the further off the heavier (Distance / 130). From
  300 ft it flies a line of its own while the two of you turn about each
  other: you come near, fall away and cross its picture instead of being
  nailed to it. Close by it has to hold tight, and is then little more than
  You in front (measured on a 44 s turning fight: at 120 ft it stayed 75 to
  142 ft from you, a median 21 ft from where You in front stood; at 400 ft,
  228 to 517 ft from you). A station that does not follow every turn loses the
  opponent on a fixed lens, so Lens is the longest it uses: it opens by
  itself, up to 100 degrees, to hold you both (in that fight both were in the
  picture 43% of the time on a fixed lens at 400 ft, 92% on this one). When
  even that cannot hold him, the lens goes back and you stay in the middle.
  Its station follows that line up and down too, so an opponent well above or
  below you is not left out of the picture's height. Looks at chooses whom it
  watches: Both, You alone, or the Opponent alone — a long lens on him from a
  steady seat beside you, which is where a kill's explosion is caught.
- Close on you, Close on opponent — the same ship watching one aircraft alone
  from any side (front quarter first; drag the picture round it). The viewer
  holds that aircraft in the middle of the picture, so it can fill the frame
  and show its detail. An opponent leaves the film at his kill message: after
  it the picture is empty.
- Over your shoulder, From your wing — cameras on your airframe that turn to
  hold the opponent: a piece of your aircraft close, him beyond.
- Behind you — fastened to your airframe behind the tail, so it stays there
  through a loop or a roll: the horizon turns, not the aircraft. (The viewer's
  own `chase` camera, driven through the socket, did not keep the aircraft in
  the picture, in stills or in a clip.)
- Fly-by, Opponent fly-by — a camera standing still beside your path, or behind
  and beside the opponent's on a longer lens: he flies into the frame, and at a
  kill that is where he goes down.
- Pilot's view — the view as recorded, head movements included. Cockpit — your
  seat, looking straight ahead.
- On your nose, Under your belly, At your gear — the viewer's own airframe
  cameras from its director help; the gear cameras are for take-offs and landings.
- Ride the bomb — from just before a release to the blast; offered when a bomb,
  rocket or torpedo leaves during the shot. A drop moment starts with it.

Before a moment's first picture the desk reads who was around, then asks the
viewer's own projection where each aircraft is in every variant of every camera
(45 candidates, about a second), at up to 24 moments across the shot. A variant
reads well when the smaller aircraft is at least 3% of the picture's width (and
better bigger, up to a tenth of it), both are clear of the edges and one is not
over the other. Each camera opens on the variant that reads best (the bigger
foreground on a tie), and its card shows the share of the shot with both
aircraft in frame. Rate cameras measures again after the shot or the opponent
changes. It knows nothing of terrain: watch the shot.
Remote aircraft move as coarsely as the film recorded them. An aircraft that
leaves the film mid-shot (shot down, landed) keeps its last known place, so the
camera holds on where it went down. For the cameras for two, the line through
both then stops turning: followed to that place, it spun round as the other
aircraft flew past it and whipped the camera along (measured: 135° in 2.5 s).
If the nearer aircraft left, the camera stays where it is and turns, over a
second, to the other, which the viewer then holds for it: flying past and away
without a jerk. If the far one left, the camera flies on with the nearer one
as it was. One absent for the whole shot is reported on that shot, which is
then skipped; the rest of the plan keeps working.

The sliders under the cameras (Around, Distance, Height, Lens, Roll) adjust the
one selected. For the cameras that stand free of the airframe — the two
in-front ones, the observer and its close views, Behind you and the fly-bys —
a drag on the picture does the same: sideways walks the camera round the
aircraft, up and down changes its height. Ctrl + wheel zooms the lens on any
camera: the aircraft grows without the camera going nearer, which would distort
it. The plain wheel still scrolls the page. Shot details holds exact times, the
lead aircraft and the name.

**The cut.** Saved shots, each with a tick box. Render selected makes one 16:9
video of the ticked shots with the film's sound, at 1080p60, 4K (2160p60) or as
a quick draft (960×540, 30 fps), leaving out everything between them. Between
shots the picture shows the recorded pilot view. The viewer holds one camera at
a time, so ticked shots of the same stretch cannot share a video: the render
then makes several, the first shots of every stretch in the first, the second
ones in the next. Three shots of one stretch come out as three videos of the
same length, each saved under its shot's name and camera. On the desk, the
first shot of a stretch is the one that plays until another is selected.

Add my kills makes four shots for every kill of yours that has none yet, after
the kill sequence in Steely's Aces High film companion: you closing in (you in
front, him beyond), your guns (over your shoulder at him), him going down (close
on him, from 60 ft above) and you flying on (from ahead, looking back). The guns
are your last burst that hit him within 20 s of the kill message; with none in
the film, the 4 s before it. He is followed down for at most the last 6 s before
the message, where he leaves the film; between the guns and that the cut skips.
Seen on a real kill: he hit the ground about a second before the message, so the
end of his fall shows grass. The cameras stay yours to change.

Take-offs & landings reads your flight path for the whole film, a second
apart (about 5 s for 9 minutes), and adds shots for each take-off and landing
that has none yet. You are the aircraft at the eye of the recorded cockpit view
(3 to 5 ft from it). A take-off starts from standing (under 8 ft/s); the
lift-off is the last moment within 1.5 ft of that height before you are 8 ft
up (measured: parked to 8 s, rolling from 8.5 s, off at 18.5 s). A landing ends
standing; the touchdown is the first moment back from the stop within 2 ft of
that height, after being 20 ft higher in the minute before (measured: down at
531 s at 178 ft/s, stopped at 536 s). The film has no event for either: the
host's landing message comes for other pilots, not reliably for you. The shots:
for a take-off the roll from a camera standing beside the runway ahead, the
wheels, the lift-off from a camera ship that swings 100 degrees round you while
closing from 250 to 100 ft (`sweep` and `closer`), and the climb from a camera
ship that starts ahead of you and 20 ft below, swings 150 degrees round behind
you and falls back from 150 to 500 ft, so you climb away against the sky; for a
landing the approach from a camera ahead of you looking back, the wheels and
the roll-out from a camera ship swinging round. Near the ground every camera is
at or above your height, because the ground's height is known only under your
wheels; the climb is 6 s after lift-off, when a fighter was 160 ft up.

## Under the desk

Each film has a local JSON project under `.quickshots/`, with an integer revision.
Saves are atomic. Every write goes through POST /api/project and must include
the revision it read. A 409 requires rereading and merging. The page keeps an
unsaved shot when the plan changes under it (another tab, a script). Every open page
shows the desk's one picture, but only the page that asked for a clip or a
render plays it: two tabs playing the same clip are heard as a doubled voice
(measured: a second page left open played a 61.6 s clip to its end unasked).
The HTTP API below is the whole contract.

Viewer operations are single background jobs. POST /api/action returns 202
(asked to wait, the finished state: a look at a changed camera is one round
trip); GET /api/state stays responsive, showing message, elapsed time and error. The
API refuses a second operation while one runs; the desk page holds the latest
request and sends it when the viewer is free. A timeout/disconnect is not
retried. Check the viewer before reconnecting. Terrain-download questions
appear in status and must be answered in the viewer. Stop a native render in
Film Viewer if needed.

A shot's camera commands are kept while its geometry is unchanged, and the
aircraft tracks of a stretch are read once for every camera tried on it. The
desk controls only the offline viewer, never the live game.

`.quickshots/current.ahs` is the most recently applied native plan; JSON is the
editable project.

Files go to two places. **Finished videos** are kept in `AH Quick Shots` in
your Videos folder, each under a name that says what it is: the film, where
the shot starts, the shot's name and its camera
(`myfilm 08.08 Kill Somebody - Observer.mp4`; a video of several shots is
`myfilm 05.14-08.16 cut of 3 shots.mp4`; the same thing rendered again gets
`(2)`). Videos folder, under The cut, shows it in Explorer. To keep them
somewhere else, write that folder's absolute path into
`.quickshots/output.txt`; it may have spaces.

Everything else is working material in an `AHQuickShots` folder under the
system temp directory: the stills, the preview clips (`clips/`, the newest 80:
a cache) and the copy of a render that the page plays. The viewer writes there
and takes no spaces in a video path, so when the temp directory has one (a
Windows account name with a space) the desk uses `.quickshots/media` instead,
or `AHQuickShots` under the Public folder; a finished render is then copied to
the videos folder. The desk keeps this folder small: three stills while it
runs, none left from an earlier run, and the page's copies of the last six
renders. Deleting the whole folder loses nothing but the previews' cache.

Checks: `python director_api.py --check`, `node ui/src/desk.test.mjs`,
`npm --prefix ui run build`. No game is needed for automated checks. Live QA
must also check a moment's preview clip, a camera change and the instant return
to the first camera, a drag on the picture, Names, a film change with a shot
selected, a saved shot reopened from the cut and a render of selected shots.
The interface can be clicked through without the viewer (which cannot run
beside the game) on a `Desk` whose `viewer` is a stand-in answering in the
formats of `help`; it does not replace the live check.

Camera work is judged on pictures, not on numbers: `python dev/try_camera.py`
renders unsaved cameras through the running desk, tiles a frame a second, says
where the viewer's projection puts both aircraft and how loud each second is.
Only one Film Viewer runs at a time, so it uses the pilot's own desk:
what it renders shows on their page. To look without touching anything, read
GET /api/state (the open film, saved shots, who is near), `.quickshots/current.ahs`
(the last camera plan sent) and the newest files under `clips/`. Restarting the
server closes the open film and loses an unsaved shot in the page; saved shots
stay.

## The HTTP API

The page uses it, and a script of yours can too. It answers on 127.0.0.1 only,
to requests from the same origin. Never open a second socket to the viewer
yourself: that disconnects the desk.

**GET /api/state** — everything: `connected`, `films`, `project` (`film`,
`length`, `revision`, `shots`, `events`), `planes`, `now`, `preview`, `nearby`,
`proposals`, `problems`, `job` (`running`, `message`, `elapsed`, `expect`,
`error`). Poll it; it never waits for the viewer.

**POST /api/project** `{expected_revision, shots}` — saves the whole plan
atomically. Merge the current shots and keep their ids. A 409 means the plan
changed: read it again and merge; never force a revision.

A shot: `id`, `name`, `start`/`end` (film seconds), `kind`, `hero`/`target`
(aircraft ids), `orbit` (degrees round the aircraft), `distance`/`height`
(feet), `fov`/`roll` (degrees), `side` left|right (which wing, shoulder or gear
leg), `watch` both|hero|target (whom the observer holds in its picture). Shots
may overlap: takes of one stretch through different cameras, each with its own
id. Use the recording pilot as hero when possible; remote tracks can be coarse.

Kinds:

- `pair` — beyond hero on the line through hero and target, swung `orbit`
  degrees off it
- `follow` — fastened to the hero's airframe: orbit 0 behind, 90 off the right
  wing, 180 ahead
- `fixed` — stands still where `follow` would be at mid-shot, turning to watch
- `ambush` — the same beside the target's path, watching the target fly into
  the frame
- `escort` — the observer, a third aircraft flying with the hero along the
  hero's path smoothed over one to four seconds (distance / 130). `watch` both:
  distance from that line, orbit off the line through both, `fov` its longest
  lens, opened by itself up to 100 degrees to hold both. `watch` target: the
  same station, looking straight at the target. `watch` hero: orbit counts
  round the hero's own line of flight, and target may be null
- `recorded` — the pilot's own view as flown, head movements included
- `wing`, `shoulder` (turn to hold the target), `cockpit` (straight ahead),
  `nose`, `belly`, `gear` — cameras on the hero's airframe
- `weapon` — rides the hero's next bomb, rocket or torpedo: start the shot a
  second before its drop event

**POST /api/action** `{action, expected_revision, ...}` — answers 202 at once
(with `wait: true`, 200 with the whole state once the action has finished, at
most 5 s later). One action runs at a time; a second is refused while
`job.running`.

- `connect`; `open` `{film}` — a path from `state.films`
- `seek` `{seconds, rough?}` — a still of that moment; `rough` is the viewer's
  fast approximate jump
- `preview` `{shot_id, seconds?}` — a still through a saved shot
- `clip` `{start, end, still?, hero?}` — a 960×540 video of the stretch with
  sound, at `state.preview.url` when done; an unchanged stretch comes from its
  file. `hero` also fills `state.nearby`
- `icons` `{on, seconds?}` — the pilot name labels; with `seconds` it answers
  with a still
- `around` `{start, end, hero}` — `state.nearby.aircraft`: who comes how close
  to hero in that stretch, nearest first. Pick a shot's target from it, not
  from a kill message
- `suggest` `{shots}` — 1 to 64 candidates sharing one start and end;
  `state.proposals` gets `{id, score, size, quality}` each: `score` the share of
  the shot with both aircraft in frame, `size` the lead's mean width in the
  picture, `quality` (0 to 1) how well it reads. It ignores terrain
- `export_cut` `{shot_ids?, quality}` — draft|final|4k: the chosen saved shots
  (all when omitted) as one 16:9 MP4 with sound, the gaps left out. Overlapping
  shots cannot share a video: `state.preview.files` then lists several,
  `[{url, file, shots}]`; `file` is where the video was kept (`state.videos`
  is that folder). `labels` `{shot id: camera name}` names each shot's camera
  in its file name; without it the shot's `kind` is used
- POST `/api/folder` `{}` shows the videos folder in Explorer

`seek`, `clip` and `icons` also take `shot`, an unsaved shot object: it stands
in for the saved shots it overlaps, so a camera can be looked at before it is
saved. A `seek` with `shot` to the moment already on screen (`state.now`) does
not move the film: the still is back in about a tenth of a second. A still
taken within two seconds of a jump has `preview.fresh` true (the aircraft are
still fading in): ask for the same moment again to get them solid.
`state.problems` maps a shot id to why it could not be filmed (an aircraft
absent for the whole shot); such a shot is skipped in playback and refuses to
render.

## What was measured in the viewer, and what would help

Measured on Film Viewer 3.07 Patch 2 through the socket. The desk works round
each of these; a change in the viewer would make the workaround unnecessary.

- **Sound after a render.** For about as long as a rendered stretch lasts after
  its render, the next `video` can come out without the engine, guns and voices,
  with an identical picture (about 60 renders of one 11.5 s stretch: thin when
  the film was touched 2, 5 or 8 s after a render, full every time 12 s or more
  had passed; after a 4 s stretch 3 s were enough). `open`, a new connection,
  `cams off` or a seek to the start did not help. The desk waits the stretch's
  length and 1.5 s before each render. A render that begins far from where the
  film stands, just after `open`, started with a silent second (-49 dB, 5 of
  5); the desk now seeks to its start and waits 2 s paused first (whole, 2 of 2;
  a seek alone or with a still drawn was not enough).
  Judging the sound: renders back to back keep the viewer from resting, and the
  same shot's level moved by 9 dB from one series to the next (-21 to -30 dB in
  the first second). Compare two ways of rendering only inside one series,
  alternating them, with 30 s of rest before each render.
- **Aircraft fading in after a seek.** Each aircraft fades in the first time
  it is drawn, each level of detail on its own, and after a seek every other
  aircraft is new to the viewer: `shot` catches them see-through, and so did
  the first second of a `video` started after one (the opponent invisible in
  the first frame, half there at 0.5 s, solid at about 1 s). Steely's fix
  (Aces High forum, 14 Nov 2026): play the two seconds before the moment
  through the shot's camera, then a short seek forward, which keeps them drawn.
  The desk does this before every still, with each shot's first camera
  standing in the two seconds before it. Not before a video: on a rested
  viewer, a render that followed that playing lost its engine and guns every
  time (3 of 3; 0 of 2 without). A video instead starts two seconds early,
  through the same camera, and with ffmpeg on the PATH those seconds are cut off
  again (re-encoded, so the cut is frame-exact: H.264 CRF 16, AAC 192 kbit/s,
  close to the viewer's own 21 Mbit/s at 1080p60). Measured: the opponent solid
  from frame 0, the same frame count and picture from then on as a plain render,
  and no thinner sound (3 of 3 each way). Without ffmpeg the video starts at the
  shot, and its first second can show a ghost. A skip inside a cut does not
  bring the fade back. Waiting while paused would fix it too, but a burning
  aircraft's smoke piles up while film time stands still.
- **Where a seek lands.** Up to 0.08 s past the time asked for, while `time`
  answers in tenths of a second.
- **The end of a film.** `open` tells the length rounded to 0.1 s, and `tracks`
  leaves out a snapshot past the real end (a film told as 384.5 s had one at
  384.48 and none at 384.5), so a shot reaching the told length could not be
  filmed. The desk ends every film 0.05 s sooner.
- **The `chase` camera** driven through the socket did not keep the aircraft in
  the picture. The desk fastens a camera to the airframe instead.
- **A render cannot be interrupted** from the socket, only in the viewer.
- **Not in the socket:** a picture stream (the desk renders a preview clip
  instead), the position of the free camera, the trails switch, the aircraft
  type, and the height of the terrain (camera ratings ignore the ground).
