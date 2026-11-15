# How AH Quick Shots works

Requires Film Viewer 3.07 Patch 2 (socket protocol 5) or newer. Start the viewer,
then `AH Quick Shots.bat`; the desk connects by itself. The batch file starts
`director_api.py --page`, which opens the page once the server listens. If the
port is held by a desk of this folder, or by one busy rendering, that page opens
instead; a desk of another folder is stopped first (found with `netstat`).

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

**Moments.** The film library lists the replays opened lately (up to eight),
then all of them, the newest first; the bin beside Open moves the chosen
replay to the Recycle Bin (its saved shots and videos stay). Film Viewer
holds the open replay and has no command to let go of a film, only to open
another; Windows then refuses to delete or rename the file (checked on the
viewer: a sharing violation). So the bin only marks the open one, the count
of marked replays shows on it, and the desk moves them to the Recycle Bin
when another replay is opened; a second press takes the mark off. The open
replay's name stands large above the picture: type another and press Enter
(Rename), and its file is renamed with its saved shots, the record of its
videos and its assembly. For that the desk has the viewer open another
replay, renames the file and opens it again, so it needs a second replay in
the films folder. "Fights" is one row
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
moment inside a saved shot selects that shot instead. The playhead goes to
where the moment begins, inside that shot: a fight at its first burst or hit,
anything else where its own shot begins, so Play shows it whole (at a fight's
time, its kill message, Play went on after the kill). Before the clip is made
the desk reads who was around: if the moment's aircraft never comes within
3,000 ft while another passes within 1,500 ft, the nearer one is taken.
Who was around is measured over the shot's own stretch, again whenever its ends
have moved and rest: Opponent offers whoever comes within 3,000 ft there. The
observer and Rate cameras frame you and that one opponent only; a third
aircraft is in the picture by chance. A shot that spans fights with two
opponents is better made as two shots.

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
and pauses. With the playhead inside a saved shot, New shot is Duplicate shot:
a copy of that stretch with its camera and keyframes, named "(copy)", to change
and then Add to cut: the usual next step is the same moment through other
cameras, each saved as a shot of its own and laid over the others in an editor.
The selected shot stays as it was saved. The copy icon beside a shot in The cut
does the same. With the playhead elsewhere, New shot marks six seconds from it.
A sequence (a kill, a take-off) plays and renders past such a copy: it is
another take of its stretch. A shot with a change not saved yet says so: the
frame of Update shot (Add to cut) breathes amber, and its row in The cut has an
amber dot. Keyframes are saved only with the shot. The arrows at either end of the highlighted timeline
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
- Observer — the first camera, and the one a new shot of two opens on. A third
  aircraft flying with you, carrying the camera. It keeps station off the line
  through both aircraft, so the opponent is beyond you and a little aside, but
  it is heavy: it follows your path, and that line, averaged over one to two
  seconds either side, the further off the heavier (Distance / 200; Looseness
  sets it by hand). Its settings come from three fights whose tracks were
  kept (118 s, 37 s, 10 s), each shown through every candidate by the desk's
  own maths: where both aircraft stood in the picture every tenth of a
  second, how large, how fast the picture swung. As it was (400 ft, a 35
  degree lens, 35 degrees aside, three seconds heavy) it kept both in the
  picture 81, 88 and 100% of the time with the lead aircraft 9, 7 and 12% of
  the picture's width; heavier than two seconds it lost the opponent in a
  turning fight (81% at three seconds, 95% at two, the same station). Its
  first variant is now 300 ft, a 28 degree lens, 20 degrees aside: 98, 100
  and 100%, the lead 16, 14 and 18% of the width. You in front, on the line
  itself, held both 97 to 100% with the lead a quarter of the width, but one
  aircraft covered the other for 4.7 s of the long fight (1.8 s here), the
  picture swung at over 100 degrees a second where the observer's stays
  under 65, and each time the opponent was 80 degrees above or below, it
  looked straight up or down and lost him. The observer's station follows
  that line up or down no steeper than 63 degrees (it was 52: in a vertical
  fight the lens then had to open to 92 degrees to hold him, now to 80). From
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
  When he leaves the film (shot down), the observer lets go of him gently: for
  a second and a half it goes on as if he flew on, then holds you alone, and
  its lens comes back to yours over three seconds. Held at the place he was
  last seen, he stopped its turn dead (27 degrees a second, then 0.6) and the
  lens snapped back from 31 to 9 degrees.
  The observer's swing. When the two pass close by, the line through both
  swings right round in half a second (measured: 150 degrees, passing at
  76 ft), and the ship swings round you after it over about four seconds.
  With that line averaged once the swing began and ended at once, and the
  picture jerked there: on that fight, at 400 ft, the ship's turn changed
  by 31 degrees a second from one half second to the next and its speed by
  145 ft/s; the finished video showed it at the same two moments, sixty
  times the change between frames elsewhere. The line is now averaged, and
  then once more over a second either side, so the swing gathers and loses
  its pace: 11 degrees a second and 50 ft/s on that shot. Over nine fights
  whose tracks were kept (24 to 118 s, the two aircraft passing at 66 to
  3,818 ft), each at 300 and at 400 ft, the worst change of the turn was on
  average 16.9 and 14.5 degrees a second before and is 14.3 and 10.6; of the
  ship's speed, 59 and 63 ft/s before, 43 and 44 now; both aircraft were in
  the picture at least 98.3 and 95.8% of the time before, 98.7 and 95.5%
  now. Averaged twice over 0.7 of the reach instead, it was worse at 300 ft
  (17.7 degrees a second); once more over half a second only, a little less
  good (14.6 and 11.1). Not smoothed by this: the look itself when two
  aircraft pass head on, which turns as fast as they cross.
  Its station follows that line up and down too, so an opponent well above or
  below you is not left out of the picture's height. Looks at chooses whom it
  watches: Both, You alone, or the Opponent alone — a long lens on him from a
  steady seat beside you, which is where a kill's explosion is caught.
  The observer can be directed, in three steps that each stand alone.
  Looseness is how many seconds of your path it flies the average of (1 to
  10; by itself it takes its distance / 200, one to two): more, and it
  follows you less closely. Keyframe (K) keeps the framing at the playhead:
  round the aircraft, distance, height and lens. Move the playhead, set
  another framing, and the observer glides from one to the next: held before
  the first and after the last, through each one on a cubic that is flat at
  the two ends, so a move starts and stops gently and does not halt at a
  keyframe on the way; round the aircraft it goes the short way. Before the
  first keyframe a change is the whole shot's; once there are keyframes,
  every change is a keyframe at the playhead (the one within a quarter of a
  second, or a new one), and the sliders show the camera as it is there.
  Looks at is kept in the keyframe too: Both at one, the Opponent at the
  next, and between them the observer turns from the one to the other,
  changing its seat, its look and its lens together, gently at both ends.
  The turn takes the time between the two keyframes: set them a second or
  two apart for a quick turn of the head, and a keyframe before them to hold
  the first look until then. Going to or from You alone it flies round you
  from one seat to the other, the short way as the move begins, keeping its
  distance (through you is no way). Not yet measured on a real fight: on
  made-up tracks (a steady 12 s turn, three keyframes 3 s apart) the picture
  swung at most 54 degrees a second and the ship stayed 290 ft from an
  aircraft it was set 300 ft from. The viewer
  holds an aircraft in the middle only where the observer watches it alone
  at both ends of a half-second move; through a turn the desk's own points
  say where it looks. A keyframe saved before this watches whom the shot
  does.
  The keyframe the playhead stands on is the selected one, drawn larger and
  hollow: click a diamond or step with the arrows beside the button ([ and ])
  to select it, change the camera to change it, Delete (or K) to take it
  away. Close-up fills the timeline with the shot (a tenth of its length
  left either side), so keyframes close together can be told apart; the
  button before Play (Home) goes back to the shot's start.
  Flies, With you or Its own path: on its own path each keyframe pins the
  observer to the sky where it then sits, and it flies a smooth line of its
  own through those places, standing at the first before it and at the last
  after, while the aircraft come by, pass and leave; it goes on looking at
  whom it watches. One keyframe on its own path is a camera standing still
  where you framed it. Between pins the line is the camera's, not the
  aircraft's: it can come nearer to them or fall further off than either
  keyframe was. The desk still sends the viewer a move every half second
  (`curve`), as for every moving camera; the keyframes only say where.
- Tripod — the observer standing still: pinned to the sky where it is at the
  playhead when you choose it (a path of its own with one keyframe), looking
  at you alone, or at whom Looks at says. It follows by turning and by its
  lens only: Lens is what it uses from Distance, and from further or nearer
  the lens closes or opens to keep the aircraft that size (`zoom` "hold"),
  down to 6 degrees; further off than that reaches, the aircraft does grow
  small. A change (a drag, a slider) moves the tripod where it stands,
  wherever the playhead is; a second keyframe (K) makes it a moving camera
  again. Checked on made-up tracks only, not yet on a real film.
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
- Cockpit — your seat, looking straight ahead; its switch As recorded shows
  the pilot's view as it was in the fight, head movements included (the kind
  `recorded`, called Pilot's view in a video's name). Observer and Tripod,
  the two cameras that are directed, come first in the list.
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
aircraft, up and down changes its height; on a camera fastened to the airframe
(cockpit, nose, belly, gear, and wing or shoulder when not locked on him) it
turns where the camera looks (`turn` left, `tilt` up, in degrees). The wheel zooms the lens on any
camera: the aircraft grows without the camera going nearer, which would distort
it; with nothing to zoom, the wheel scrolls the page. A shot opened or changed shows as a still (the
clip action asked `bare`: its cameras and a still, no render). Play plays the
shot (or its sequence) at once in the viewer's own window, with its sound and
nothing rendered (the viewer's window must be on screen to be seen); Render
and play renders it as a small clip and plays that on the page; a render
after it waits at least 12 s, because playing just before a render thins its
sound (measured with that wait: the same sound as a rested render, 2 of 2). Shot details holds exact times, the lead
aircraft and the name.

**The cut.** Saved shots, each with a tick box. The x beside a shot (where the
pointer is) and Clear above the list take shots out of it; when videos of them
were rendered, the page lists the files and asks whether they go too, to the
Recycle Bin, or stay. The desk keeps a record of every finished video and the
shots in it beside the plan (`.quickshots/<film>.renders.json`); a video of
several shots goes with any of them, and videos rendered before that record
existed are not on it. The green arrow beside a moment adds it to the cut
without opening it: a kill, or a fight you won, as the four shots of Add my
kills, anything else as one shot with its first camera. Render selected makes one 16:9
video of the ticked shots with the film's sound, at 1080p, 4K (2160p) or as
a quick draft (960×540), 30 frames a second unless 60 are chosen (the
operator's choice: more like film), leaving out everything between them; set to
A file per shot, it makes a video of every ticked shot instead, each starting
and ending where its shot does, for cutting in an editor (each render waits for
the viewer to finish the last one's sound, so this takes longer). Between
shots the picture shows the recorded pilot view. The viewer holds one camera at
a time, so ticked shots of the same stretch cannot share a video: the render
then makes several, the first shots of every stretch in the first, the second
ones in the next. Three shots of one stretch come out as three videos of the
same length, each saved under its shot's name and camera. On the desk, the
first shot of a stretch is the one that plays until another is selected.

Each shot keeps the effects it is rendered with (`effects` in the plan: the
data overlay, motion blur, the film look and its haze, trails with their
colours, length, tail trail and exact path), so a refresh no longer loses
them. The options under The cut show and set the selected shot's, kept with it
when it is added or updated; with no shot selected, those a new shot gets,
which are always the last ones set (motion blur alone at first). A shot saved
before shots kept their effects has that first set, motion blur alone, until it
is given others: setting another shot's effects does not touch it. One video renders every shot its own
way: it is cut wherever the effects change, so shots of other effects come out
as videos of their own and those alike as one. Effects are not the camera:
changing them renders no preview again and does not mark a shot's video as
changed.

A released package offers motion blur and the data overlay. The film look
with its haze and the trails are still being made: the page offers them when
the desk runs from the source code (its version is then `source`), and not
with `?release` in the page's address, which shows the page as a release does.

Motion blur, the slowest effect and the finest, renders each video four times
at 120 fps, the viewer's most, each pass a quarter of a frame (1/480 s) after
the one before, and ffmpeg weaves the passes and makes each frame the mean of
all the frames in its time, as a 360° shutter does: eight frames a frame at
60 fps, sixteen at 30. The blur is made of the viewer's own frames: nothing is
guessed. It is ticked from the start, since 30 frames without it look jerky.
A film camera's 180° (half of them) left too little to see; two a frame at
120 fps gave a double image instead of a streak. At 30 fps, on a fast roll (a
15 s render at 1080p30), one pass drew the aircraft's edges as four steps and
two passes left a faint fan at the wingtip: the operator chose four, slow and
beautiful. ffmpeg's motion interpolation took 130 times the film's length. The sound is
the first pass's. Measured on a 7 s shot at 1080p60: 67 s with blur, 17 s
without.

Film look (experimental) is applied by ffmpeg in the same step (`look()` in director_core.py).
Its colour is `looks/film.cube`, a 17-point LUT fitted to three stills of this
footage before and after a DaVinci Resolve grade (Film Look Creator, Cinematic):
on a still it was not fitted to, it took the mean difference from that grade
from 7.5-8.4 to 4.2-5.8 (of 255); the rest is the grade's haze on the horizon,
which follows depth, not colour. That grade mixes its colour in at half and
could hardly be seen, so the LUT holds the whole of it, and the bloom, the red
halation round highlights, the vignette (corners about 20% darker) and the grain
are stronger than the grade's; all are sized to the picture's height. The same
7 s shot: 82 s with the look and motion blur, a mean change of 6.6 of 255 from
the plain render.

Haze (experimental, `haze()`) lays mist along the horizon, as the same grade does through a
sky mask and an AI depth map. The viewer tells nothing of its camera to work
depth out from (and the pilot's view follows a head no one recorded), so the
horizon is found in the picture: at an eighth of its size, sky (bluish) and
ground (coloured, not bluish) are told apart by colour, patches of ground too
small to be land (an aircraft) are opened away, both are blurred, and where both
are present the picture goes towards a grey-blue that the film look turns into
the grade's mist. Dark things, such as a canopy frame, keep clear. On the
grade's stills the far ground and the sky just above it misted much as in
Resolve; an aircraft against the sky and clouds stayed as they were. An aircraft
in front of the horizon is misted with it. Haze and film look on 5 s of 4K:
53 s.

Trails (experimental, `trails.py`) draws two soft lines of vapour from the
wingtips of each shot's aircraft: the lead aircraft in one colour, his opponent
in another. A trail has a bright part and a faint remainder. The bright part is
sized to the picture, not to the clock: it lasts as long as the aircraft takes
to cross three tenths of the picture's width, so it reads the same from a
camera 120 ft off the wing and from one half a mile away. Timed alone, a close
camera saw only the trail's newest quarter of a second, as bright at the
picture's edge as at the wingtip: two wires across the frame (seen on a render
of a pair shot, where the offline drawing matched the viewer's own). The
remainder stays as far back as the trail reaches (2.5 s short, up to a minute
lasting) and fades to nothing there; a lasting trail keeps more of it, so the
fight's ribbons stay in the sky for a wide shot afterwards (halfway back a
minute-long trail is still three quarters as bright as its remainder begins).
The trail is as wide as vapour, in feet: an inch and a half at the wingtip,
spreading a foot and a half a second, so it is thicker near the camera, never
thinner than a pixel, and within 80 ft of the lens it comes in gently instead
of cutting the frame. Its brightness falls in equal steps, so none shows as a
dash. The newest 1.5 s are drawn through every kept step,
the rest through a point every 0.04 s (3000 at most) fixed in film time, so a
long trail keeps every move of the stick and does not shimmer (measured on two
aircraft circling at 1080p60: 0.3 to 0.6 s more script a second of film than
300 points took, ffmpeg the same). Before the first render the desk keeps
every aircraft's track from the trail's length before the video, and asks the viewer's `project` where the
shot's camera stands at every frame: a moving camera is first placed on its
curve (a uniform Catmull-Rom through the shot's points, as the viewer moves it:
within 0.05 ft halfway between points), then projected as a still one. Its eye
puts the aircraft where the viewer does to 0.7 px (median, 1080p), and to
0.15 px on airframe cameras rolled up to 70 degrees. The wingtips are 0.88 of
the aircraft's visible radius out (a Yak's tips matched at 75 ft). libass draws
the lines; ffmpeg blurs a copy for the glow and lays the light over the picture.
A 7 s shot at 1080p60: 65 s with trails, 17 s without. Not in the pilot's view
or the weapon ride, which the projection does not know; a trail is not hidden
behind what stands in front of it. The script is written line by line and
grows with the video (measured: 1.5 to 3.4 MB a second), so trails go on one
video of up to 120 s whose shots lie within ten minutes of film; beyond that
the desk asks for A file per shot.

**What a render costs.** The page says it before the render is asked for:
beside each effect what it would add, above Render selected the whole
(`renderCost` in `ui/src/desk.js`). While it renders, the wait is said large
over the picture, as every wait is: what is being done, which video of how
many and which step of how many
(`step`, `steps` in the job: for each video the viewer's render, each pass of
motion blur, then ffmpeg's; before them the trails' cameras), the time gone
and the time expected for all of it. ffmpeg's steps say how far they are (`share`, from the
file ffmpeg writes with `-progress`); the viewer says nothing of its own
render, so that step's share of the bar pulses. The bar is never full while
work goes on. Cancel, large under it, stops ffmpeg at once and the render
after the pass Film Viewer is rendering, which only the viewer's own window can
stop at once; the wait says so. Each video's record keeps how long it took (`took`, seconds). Seconds for one second of film, measured on one PC (a 7 s pair
shot of film13):

| | 1080p60 | 4K |
| --- | --- | --- |
| Film Viewer's own render | 1.4 | not timed: 2.9 assumed |
| one of motion blur's four passes (120 fps) | 1.7 | not timed: 3.4 assumed |
| ffmpeg cutting the lead-in off | 0.22 | 0.85 |
| ffmpeg blending the four passes | 0.4 | not timed: 1.6 assumed |
| film look | + 1.0 | + 4.8 |
| haze | + 1.1 | + 4.0 |
| trails, short (ffmpeg 3.1, the script 0.1) | + 3.2 | + 13.2 |
| trails, half a minute long | + 1.0 more | + 6.0 more |

At 30 fps the page takes ffmpeg's share and the trails' as half of these, and
the viewer's own render and motion blur (the same four passes) as at 60: worked
out, not timed. Timed on 15 s of film at 1080p30 with motion blur: with one pass
46 s in all (about 5 s of it waiting for the last clip's sound), a third more
than said; with two passes 74 s, 6% more, each pass of the viewer about 1.9 s
for a second rendered (1.7 assumed) and ffmpeg 0.23. With four passes, 24 s of
film in 202 s against 220 said: each pass 1.7 s for a second rendered, as
assumed, and ffmpeg 0.35; the edges smooth, without steps or a fan. The desk's
pace takes each render in.

The effects add up nearly as a sum (look, haze and lasting trails together:
6.8 against 6.4). A lasting trail is cut off a picture's width outside the
picture: with its far-off points drawn, two aircraft circling with 30 s trails
took 34 s of ffmpeg a second of film instead of 1.2 (a 58 s shot with every
effect on: 45 minutes against the 19 the page had said). Motion blur is the dearest because the viewer renders the
film four times; the trails' share is libass drawing and ffmpeg's glow, and
what the viewer takes to tell where its camera is at every frame has not been
timed apart (1.5 is assumed). A draft is taken as a sixth of 1080p's ffmpeg
work, the viewer at about real time. Each video adds its two seconds of
lead-in and about four of waiting. Another PC is faster or slower, so the desk
keeps a pace for each size (`.quickshots/pace.json`, `state.pace`): a render
that took `r` times its estimate moves the pace halfway there, and the page
multiplies its next estimates by it. Until a size has been rendered once, the
page says the estimate was measured on another PC.

Data overlay (16:9 and 9:16) adds, beside every rendered video, two transparent
films of the same length and frame rate to lay over it in an editor: a card with
the lead aircraft's true airspeed (mph), height (ft), climb (ft/min) and G, and,
when the shot has an opponent, his range (ft) and how fast you close on him (mph).
`... - overlay 16x9.mov` is the video's size, the card in the lower left corner;
`... - overlay 9x16.mov` is 1080×1920 for a reel, the card at the top, clear of
the apps' own bars, and half as large again to be read on a phone. In DaVinci
Resolve put the video on V1 and the overlay that fits the timeline on V2: on a
9:16 timeline the overlay stays put however the video under it is moved or
cropped. They are ProRes 4444 with transparency (Resolve reads it as Straight),
BT.709, drawn by ffmpeg with libass in Source Sans Pro from the app's `fonts`
folder (Adobe's release 2.045: the card's sizes are measured on its metrics);
without ffmpeg the option is off. The numbers change ten times a second; while
the opponent is out of the film his are a dash. The flights are read before the
first render (see "Flight data" below), because touching the film between
renders can thin the next one's sound. The overlays are drawn after each render
by ffmpeg alone, at below-normal priority, while the desk goes on with the next
camera or render (drawn in the render's own job, they held the viewer for 80 s
after a 37 s render at 4K); the line under the picture says how many are left,
and Restart the desk waits for them. Drawing takes about half as long as the
video plays at 1080p and twice as long at 4K; they take about 6 MB a second at
1080p60.

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
landing the approach from a camera ahead of you looking back, the wheels, and
the roll-out like the climb: from ahead, 15 ft above you, swinging 120 degrees
round and falling back from 120 to 300 ft, on to 2 s past the stop when the
film runs on. Near the ground every camera is
at or above your height, because the ground's height is known only under your
wheels; the climb is 6 s after lift-off, when a fighter was 160 ft up.

## The assembly

The second view lays the film's rendered shots in a row and makes one video
of them. It works on what the desk already has: the plan's shots and the
record of finished videos (`.renders.json`: each video's id, when and how it
was rendered, its length, and the camera of each shot in it). A shot's
**versions** are the videos of that shot alone, newest first (a video of
several shots is nobody's clip): `state.versions`. A version is `changed` when
the shot's camera or stretch is no longer what the video shows; the shot is
then marked Changed and Render what is missing renders it again, as a file of
its own, with the options set under The cut.

The assembly itself is small (`.assembly.json` beside the plan, `state.assembly`):
`clips` in order, each `{shot, video, start, end, join, sound}` (a version of
a shot, `video` null for the newest, less `start` seconds at its beginning and
`end` at its end, joining the next with a cut or a dissolve of 0.3 s, at
`sound` times its loudness), `off` (the shots kept off the timeline), `sound`
(the film's loudness) and `music` (`{file, name, from, level, in, out}`).
The page works out what is on the timeline (`ui/src/assembly.js`): the saved
clips whose shot still has a video, and every rendered shot that is neither
among them nor in `off`, put where it falls in the film. So a shot rendered
a moment ago is on the timeline, and a clip keeps pointing at a shot that can
be rendered again: change its camera in Shots, render, and the clip shows the
new video. The picture plays the clips one after another from their files,
the music beside them; a dissolve shows there as a cut.

One playhead runs over the ruler and the three lanes: an arrow and a line of
one element, centred on its moment and laid where the clips are, above them.
It is dragged (the ruler too), and the picture follows; focused, the arrow keys
move it a tenth of a second, a second with Shift. Beside Play the buttons jump
to the start (Home), the cut before (Up), the next cut (Down) and the end
(End): a cut is where a clip starts or ends, so a dissolve has two.

Under the name of each sound lane is its level in percent (`sound`, the music's
`level`), typed or stepped by 5: 100% is as recorded, up to 200%. The page
plays at most at full, so above 100% is heard in the export only. Each clip
keeps its own sound as well. That is all the sound directing there is, by the
operator's choice.

The Film sound lane shows each clip's own stretch of its video's waveform, so
the sound is seen cut with the picture; the Music lane shows the music's from
where it starts, with its fades. Both are drawn as loud as they are set (the
clip's sound, the lane's level), in decibels with 48 dB below full at the bottom,
as an editor draws them: on a straight scale a sine at -15 dB was a line. The
desk measures a waveform with ffmpeg the first time the page asks (GET
`/api/wave/...`: the loudest sample of every 20 ms) and keeps it while it runs.

Export video is ffmpeg's work alone (`assemble()` in finishing.py): each clip
trimmed and brought to one size and frame rate, joined in turn by `concat` or
`xfade` and `acrossfade` (a dissolve between clips too short to share it is a
cut), the music cut to the video's length, faded in and out and mixed under
the film's sound without lowering it. The viewer has no part in it, so the
desk goes on meanwhile; `state.assembling` says how it stands. Music is kept
in `.quickshots/music` under a name made of its content.

Not there yet: a vertical 9:16 export that keeps the lead aircraft in the
middle (it needs a live trial of framing by `project`), a timeline file for
an editor, and assembling without a film open in the viewer.

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
editable project. `.quickshots/errors.log` gets every operation that failed,
with where in the code it failed: the desk has no window to print it in, so
this is the file to send with a report, together with the version the page
shows beside its name (`state.version`: the release of a Windows package,
`source` for a copy of the code).

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
a cache), the copy of a render that the page plays, and the small files the
viewer's `project` reads and writes. The viewer writes there
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
`error`), `stale` (the code on disk is newer), `folder` (where this desk
runs from) and `version`. Poll it; it never waits for the viewer.

**POST /api/project** `{expected_revision, shots}` — saves the whole plan
atomically. Merge the current shots and keep their ids. A 409 means the plan
changed: read it again and merge; never force a revision.

A shot: `id`, `name`, `start`/`end` (film seconds), `kind`, `hero`/`target`
(aircraft ids), `orbit` (degrees round the aircraft), `distance`/`height`
(feet), `fov`/`roll` (degrees), `side` left|right (which wing, shoulder or gear
leg), `watch` both|hero|target (whom the observer holds in its picture). The
observer (`escort`) also takes `loose` (1 to 12 seconds), `keys` (up to 40
keyframes `{t, orbit, distance, height, fov, watch}`; `watch` may be left out
and is then the shot's; one outside the shot is dropped), with keyframes
`path` "own", and `zoom` "hold" (the lens keeps an aircraft watched alone the
size `fov` shows it from `distance`). Shots
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
  hero's path smoothed over one to two seconds (distance / 200, or the shot's `loose`). `watch` both:
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
- `icons` `{on, seconds?}` — the pilot name labels, on when the desk starts; with `seconds` it answers
  with a still
- `around` `{start, end, hero}` — `state.nearby.aircraft`: who comes how close
  to hero in that stretch, nearest first. Pick a shot's target from it, not
  from a kill message
- `flight` `{start, end, hero?, target?, step?}` — what every aircraft did in a stretch of up to 600 s
  (see "Flight data" below). `state.flight` says what was read (`aircraft`: id, name, radius, quality,
  update); GET `/api/flight` has all of it: per aircraft `samples` every `step` (0.02 s unless asked) with
  `t`, `x`/`alt`/`z`, `yaw`/`pitch`/`roll`, `tas`, `climb`, `path`, `g`, `turn`, `radius`, `energy`,
  `power`, and, with hero and target, `pair` (`range`, `closure`, `above`, `off_nose`, `off_tail`,
  `crossing`, `behind`). A stretch already read is not read again; another hero or target is
- `suggest` `{shots}` — 1 to 64 candidates sharing one start and end;
  `state.proposals` gets `{id, score, size, quality}` each: `score` the share of
  the shot with both aircraft in frame, `size` the lead's mean width in the
  picture, `quality` (0 to 1) how well it reads. It ignores terrain
- `export_cut` `{shot_ids?, quality, fps?, apart?, effects?}` — draft|final|4k, `fps` 30 (when omitted) or 60: the chosen saved shots
  (all when omitted) as one 16:9 MP4 with sound, the gaps left out; with
  `apart: true` an MP4 of each shot, in time order. Overlapping
  shots cannot share a video, nor can shots of other effects: `state.preview.files` then lists several,
  `[{url, file, shots}]`; `file` is where the video was kept (`state.videos`
  is that folder). `labels` `{shot id: camera name}` names each shot's camera
  in its file name; without it the shot's `kind` is used. Each shot is rendered with its own `effects` (a shot's
  field in the plan); `effects` here is for a shot that has none. `effects` is `{overlay?, blur?, look?, trails?}`,
  each needing ffmpeg: `blur: true` motion blur, `look: true` the film look with its haze along the horizon,
  `trails` wingtip trails: `true`, or `{lead, opponent, seconds, tail?, exact?}` with colours as `#rrggbb` and 1 to 120 seconds; `tail` is the colour of a third trail from the tip of the fin (placed by the aircraft's proportions: 0.9 of its visible radius behind its middle, 0.25 above), `exact: true` draws the path as flown, nothing averaged.
  With `overlay: true` (needs ffmpeg, `state.ffmpeg`)
  every entry of `files` also has `overlays`: the 16:9 and the 9:16 data overlay kept beside it
- POST `/api/folder` `{}` shows the videos folder in Explorer
- POST `/api/delete-film` `{film}` moves a replay from `state.films` to the Recycle Bin. The open one,
  which Film Viewer holds, is marked in `state.waiting` instead (asked again: unmarked) and goes when another
  replay is opened. Answers `{films, waiting}`. `state.recent` lists the replays opened last, newest first
- POST `/api/action` `{action: "rename", film, name}` renames a replay's file (`name` without `.ahf`) and
  takes its saved shots along; the open one is opened again under its new name
- POST `/api/forget` `{expected_revision, shot_ids, files}` takes shots out of the plan; with `files: true`
  their finished videos and overlays on record (`state.renders`: `file`, `shots`, `overlays`) go to the
  Recycle Bin. Answers `{project, renders, recycled}`
- POST `/api/halt` `{}` stops a stretch that is playing in Film Viewer's own window (`live`) where it is: the
  viewer pauses and `state.now` says the moment, which `live` takes back as `from` to go on. The page's Pause and
  Stop: Pause holds the clip on the page or the viewer's playing, Stop also goes back to the start. During a
  render (`export_cut`, `clip`) it is Cancel: `job.cancelling` turns true, ffmpeg is stopped at once (its file
  deleted), the viewer's render is let finish its pass (the socket cannot stop it) and thrown away, and nothing
  more is begun; the job ends with the error "Render cancelled", or how many videos were finished and kept.
  A render's job also says which video it is making: `video` of `videos`
- POST `/api/assembly` `{assembly}` keeps the open film's assembly (see "The assembly"); answers `{assembly}`
  as kept. POST `/api/assemble` `{assembly, quality, fps?}` (30 when omitted, or 60) keeps it and makes its video in the videos folder:
  202 at once, `state.assembling` (`running`, `message`, `error`, `file`) says how it stands
- POST `/api/music` with the file as the body and its name, percent-encoded, in `X-Name` (mp3, wav, m4a, aac,
  flac or ogg, up to 100 MB) keeps a piece of music; answers `{file, name}` for the assembly's `music`
- GET `/media/video/<id>` is a finished video on record (`state.versions`), `/media/music/<file>` a kept
  piece of music, `/media/assembly` the assembly made last
- GET `/api/wave/video/<id>` and `/api/wave/music/<file>` are the waveform of that video's or music's sound,
  `{rate, peaks}`: `rate` (50) values a second, each the loudest sample of its step, 0 to 255; `[]` without sound

`seek`, `clip` and `icons` also take `shot`, an unsaved shot object: it stands
in for the saved shots it overlaps, so a camera can be looked at before it is
saved. A `seek` with `shot` to the moment already on screen (`state.now`) does
not move the film: the still is back in about a tenth of a second. A still
taken within two seconds of a jump has `preview.fresh` true (the aircraft are
still fading in): ask for the same moment again to get them solid.
`state.problems` maps a shot id to why it could not be filmed (an aircraft
absent for the whole shot); such a shot is skipped in playback and refuses to
render.

## Flight data

`flight_data.py` reads the file the viewer writes for `tracks ... keep speed on pos double` (its layout is
in the viewer's `help`, for reading directly) and works out, for every aircraft and snapshot: true airspeed
(`tas`, ft/s), `climb` (ft/s) and the flight path's angle, the load factor along the lift (`g`, from the
change of velocity less gravity's, so a push shows below zero), how fast the flight path turns (`turn`,
degrees a second) and its `radius` (ft), specific energy (`energy` = height + v²/2g, ft) and its rate
(`power`, ft/s: below zero, energy bleeds). For two aircraft, `pair` gives the range, the closure
(positive while closing), how far above the hero the target is, the target's angle off the hero's nose,
the hero's angle off the target's tail (0 dead astern), the angle between their noses and whether the hero
is behind the target's wing line. Angles follow the viewer: yaw grows to the left, pitch up and roll right
are positive (roll checked on a kept track: the lift leaned the way of a positive roll in 186 of 186 hard
turns of the two aircraft told smoothly).

What the film tells, measured:

- **Read at 0.02 s, not 0.1 s.** The cameras' paths are read so too, and the snapshots they need picked
  out: read every 0.5 s, the recording pilot was 25 to 51 ft off his path, 7 degrees from an observer
  390 ft away, which shook a long lens. At a 0.1 s step the recording pilot's own track jittered (a Me 163 at
  618 ft/s moved 0.3 to 114 ft a snapshot, its stored speed 356 to 889 ft/s); at 0.02 s it was smooth,
  12.3 ft every snapshot, the speed steady. The first two snapshots of a read were off too, so the desk
  reads a tenth of a second early and leaves them out.
- **Far aircraft are told coarsely.** Their speed was held, then changed over 0.3 to 0.4 s, every 2 s;
  near ones changed every snapshot. Accelerations are taken over half a second, or over two of the
  aircraft's own updates when they come rarer, and such an aircraft is marked `coarse`: its speed and
  height are good, its `g`, `turn` and `radius` only an average over seconds.
- **Not in the film:** indicated airspeed, throttle, fuel, ammunition left, flaps, gear, angle of attack,
  damage, the controls, the aircraft type and side, the terrain's height, the wind, and other players'
  rounds and hits.

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
- **A render cannot be interrupted** from the socket, only in the viewer: Cancel
  stops ffmpeg at once and the viewer after the pass it is rendering.
- **Not in the socket:** a picture stream (the desk renders a preview clip
  instead), the position of the free camera, the trails switch, the aircraft
  type, and the height of the terrain (camera ratings ignore the ground).
