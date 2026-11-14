"""Shared editing desk. One worker owns the viewer; HTTP only reads snapshots."""
import bisect
from collections import deque
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid

from flight_data import derive, pair, read_kept
import overlay
import trails
from shot_plan import RIDES, compile_shot, filmer_path, ground_moments, nearby, passes, probe_cameras, track_rows, validate_plan, validate_shot
from viewer_socket import Viewer, film_rows, number, plane_rows

ACTIONS = ("connect", "open", "seek", "preview", "clip", "live", "around", "airfield", "flight", "icons", "suggest", "export_cut")
RENDERS = {"draft": "960 540 30", "final": "1920 1080 60", "4k": "3840 2160 60"}
# The viewer renders in about real time whatever the size (measured: 5 s of film in 5 s at 480x270 and at 960x540).
CLIP = "960 540 30"
# With motion blur a video is rendered PASSES times at the viewer's most, FASTEST frames a second ("fps 1 to 120", it
# answers to more), each pass a quarter of a frame later, and ffmpeg weaves them and makes each frame the mean of all
# the frames in its time, as a 360° shutter does: real blur from the viewer's own frames. Half of them (a film camera's
# 180°) left too little to see; two frames a frame at 120 showed a double image, not a streak; ffmpeg's motion
# interpolation took 130 times the film's length.
FASTEST = 120
PASSES = 4
# The film look: the colour of a DaVinci Resolve grade (Film Look Creator), fitted as a LUT to stills of the same
# footage before and after it, then bloom, a red halation round highlights, a vignette and grain. As faint as the grade
# (its colour mixed in at half, corners 10% darker, grain about 1/255) it could not be seen, so all are stronger: the
# colour whole, corners about 20% darker, grain you see. Sizes scale with the picture's height.
LOOK = Path(__file__).resolve().parent / "looks" / "film.cube"
# Haze along the horizon. The Resolve grade lays it through a sky mask by an AI depth map; the viewer tells nothing of
# its camera to work depth out from, so here the horizon is where ground meets sky. Both are told apart by colour at an
# eighth of the size (sky bluish, ground coloured and not bluish), patches of ground too small to be land (an aircraft)
# are opened away, both are blurred, and where both are present the picture goes towards this grey-blue, which the film
# look turns into the grade's mist. Dark things, a canopy frame, keep clear. Tried on stills: the far ground and the
# sky just above it misted much as in Resolve; an aircraft against the sky and clouds left as they were.
HAZE = "0x808F9F"
# After a jump in the film the viewer fades the aircraft in over about two seconds of real time, however many frames
# it draws (measured: nearly invisible at once, solid after 1.7 s). A still taken sooner shows them see-through.
FADE = 2.0
SETTLE = 1.0  # what a picture that stays on screen for a while waits of that: mostly solid, without doubling the wait
# Each aircraft fades in the first time it is drawn, each level of detail on its own, and after a seek every other
# aircraft is new to the viewer. Playing the seconds before a moment through the shot's camera draws them, and a
# short seek forward keeps them drawn: a still starts solid (Steely, Aces High forum, 14 Nov 2026). Not before a
# video: a render that follows that playing came out without the engine and guns in 10 renders of 10 (4 of 10 without).
# A video starts LEAD seconds early instead, and with ffmpeg on the PATH those seconds are cut off again.
LEAD = 2.0
# For about as long as a rendered stretch lasts after its render ends, the viewer is still finishing that stretch's
# sound. A render started in that time can be heard from a point standing still in the sky instead of from the
# aircraft: no engine, guns or pilots' voices. Measured on an 11.5 s stretch: with a seek, a still or a read of
# the tracks 2, 5 or 8 s after a render, the next render is thin, and so is one started at once after a render
# that followed a seek; thin renders keep coming until the viewer has rested. Every render started 12 s or more
# after the one before was full, whatever happened in between (6 of 6). After a 4 s stretch, 3 s were enough.
TAIL = 1.5  # seconds added to the last rendered stretch's length before the next render may start
FINE = .02  # the step aircraft paths are read at (see Desk._tracks)
CAMERAS = 9  # raise when a camera's maths or the way a clip is rendered change: a kept clip made the old way must not pass for a new one


def to_recycle_bin(paths):
    """Move files to the Windows Recycle Bin, from where a deletion on the page can be undone."""
    import ctypes
    from ctypes import wintypes

    class Operation(ctypes.Structure):  # SHFILEOPSTRUCTW
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_void_p)]
    names = ctypes.create_unicode_buffer("".join(str(Path(p).resolve()) + "\0" for p in paths) + "\0")
    # FO_DELETE with FOF_ALLOWUNDO (to the bin), no dialog, no confirmation, no error box
    operation = Operation(None, 3, ctypes.cast(names, ctypes.c_void_p), None, 0x40 | 0x4 | 0x10 | 0x400, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) or operation.fAnyOperationsAborted:
        raise OSError("Windows did not move the files to the Recycle Bin")


class Conflict(ValueError):
    pass


def camera_key(shot):
    return json.dumps({k: v for k, v in shot.items() if k not in ("id", "name")}, sort_keys=True)


def look(height):
    """ffmpeg filters giving a video `height` pixels high the film look (see LOOK): one input, one output."""
    cube = LOOK.as_posix().replace(":", r"\:")  # a filter graph reads a colon as the end of an option
    return (f"lut3d=file='{cube}':interp=tetrahedral,format=gbrp,split=3[lk0][lk1][lk2];"
            f"[lk1]curves=all='0/0 0.6/0 1/1',gblur=sigma={height / 108:.1f}[lkb];"
            f"[lk2]curves=all='0/0 0.75/0 1/1',gblur=sigma={height / 270:.1f},colorchannelmixer=gg=.4:bb=.15[lkh];"
            f"[lk0][lkb]blend=all_mode=screen:all_opacity=.3[lkm];[lkm][lkh]blend=all_mode=screen:all_opacity=.35,"
            f"vignette=angle=.3,format=yuv420p,noise=c0s=6:c0f=t+u")


def _step(x, a, c):
    """A smooth step of expression `x` from `a` to `c`, in ffmpeg's expressions."""
    return f"(st(9,clip(({x}-{a:g})/{c - a:g},0,1));ld(9)*ld(9)*(3-2*ld(9)))"


def haze(width, height):
    """ffmpeg filters laying haze along the horizon of a `width` x `height` video (see HAZE): one input, one output."""
    r, g, b = "r(X,Y)/255", "g(X,Y)/255", "b(X,Y)/255"
    light, blue = f"(.2126*{r}+.7152*{g}+.0722*{b})", f"({b}-{r})"
    most = f"max(max({r},{g}),{b})"
    colour = f"(({most}-min(min({r},{g}),{b}))/max({most},.001))"
    sky = f"{_step(blue, .02, .1)}*{_step(light, .12, .2)}"
    ground = f"(1-{_step(blue, 0, .06)})*{_step(colour, .05, .12)}*{_step(light, .1, .18)}"
    grey = lambda e: f"geq=r='255*{e}':g='255*{e}':b='255*{e}'"
    both = "0.85*clip(6*val/maxval,0,1)*maxval"  # full haze where sky and ground are each about a sixth
    sigma = height / 8 * .08
    return (f"format=gbrp,split=4[hz0][hz1][hz2][hz3];[hz2]scale={width // 8}:{height // 8}:flags=area,split[hs][hg];"
            f"[hs]{grey(sky)},gblur=sigma={sigma:.1f}[hsb];"
            f"[hg]{grey(ground)},{'erosion,' * 4}{'dilation,' * 4}gblur=sigma={sigma:.1f}[hgb];"
            f"[hsb][hgb]blend=all_mode=multiply,lutrgb=r='{both}':g='{both}':b='{both}',scale={width}:{height}[hb];"
            f"[hz3]format=gray,lut=c0='maxval*{_step('val/maxval', .2, .35)}',format=gbrp[hl];"
            f"[hb][hl]blend=all_mode=multiply[hm];[hz1]drawbox=c={HAZE}:t=fill[hc];[hz0][hc][hm]maskedmerge")


def style(width, height, film=False, hazy=False):
    """The filters for the chosen looks, the haze first as in the grade; "" for none."""
    return ",".join(([haze(width, height)] if hazy else []) + ([look(height)] if film else []))


def trim(source, target, seconds, filters=""):
    """`source` without its first `seconds`, as `target`, through `filters` (see style). Re-encoded so the cut is
    frame-exact (a stream copy cuts at keyframes), close to the viewer's own quality (about 21 Mbit/s at 1080p60,
    AAC 192 kbit/s)."""
    encode(["-ss", f"{seconds:.6f}", "-i", str(source)], ["-vf", filters] if filters else [], [source], target,
           "cut the lead-in off the video")


def blend(sources, target, seconds, fps, filters=""):
    """The passes of a blurred video (see PASSES), as `target` at `fps` with the first pass's sound: as `trim`."""
    rate = FASTEST * len(sources)
    each = rate // fps  # woven frames in one frame's time: all of them make it
    weave = "".join(f"[{n}:v]setpts=PTS+{n}/{rate}/TB[w{n}];" for n in range(len(sources)))
    graph = (weave + "".join(f"[w{n}]" for n in range(len(sources))) + f"interleave=nb_inputs={len(sources)},"
             f"tmix=frames={each},select='eq(mod(n,{each}),{each - 1})',setpts=N/{fps}/TB"
             + (f",{filters}" if filters else "") + "[v]")
    encode([a for s in sources for a in ("-ss", f"{seconds:.6f}", "-i", str(s))],
           ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-r", str(fps)], sources, target, "blend the motion blur")


def encode(inputs, graph, sources, target, what):
    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, *graph, "-c:v", "libx264", "-crf", "16",
             "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        for source in sources:
            source.unlink(missing_ok=True)
    if done.returncode:
        target.unlink(missing_ok=True)
        raise ValueError(f"ffmpeg could not {what}: " + (done.stderr.strip().splitlines() or ["no reason given"])[-1])


def lead(command, start):
    """The shot's first camera, standing from `start` to the shot's start, for drawing the aircraft before it
    (see LEAD): a moving camera holds its first place. The pilot's view and the weapon ride need none."""
    _cam, at, _duration, rest = command.split(" ", 3)
    if float(at) - start < .05 or rest.startswith(("recorded", "weapon")):
        return []
    if rest.startswith("fixed ") and " to " in rest:
        rest = rest.split(" to ", 1)[0]
    return [f"cam {start:g} {float(at) - start:.8f} {rest}"]


def framing(found):
    """How well one moment reads, 0 to 1, from where `project` puts each aircraft: [(x, y, size)], x and y from
    -1 to 1, size a share of the picture's width. The smallest aircraft should be at least 3% of the width
    (smaller reads as a dot) and is better bigger, up to a tenth of it, where its detail shows; none at the
    very edge, and two should stand apart, not one over the other."""
    smallest = min(size for _x, _y, size in found)
    value = min(1, smallest / .03) * (.75 + .25 * min(1, smallest / .1))
    if any(max(abs(x), abs(y)) > .85 for x, y, _size in found):
        value *= .6
    if len(found) == 2:
        (ax, ay, a), (bx, by, b) = found
        if math.hypot(ax - bx, (ay - by) * 9 / 16) < a + b:  # x spans two units across the width
            value *= .4
    return value


class Desk:
    def __init__(self, root, viewer=None, media_root=None, videos=None):
        """`media_root` is the working folder (stills, preview clips, the page's copy of a render): the viewer
        writes there, so its path has no spaces. `videos` is where a finished render is kept under a name that
        says what it is: the one folder the pilot looks in."""
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.media_root = Path(media_root) if media_root else self.root / "media"
        self.videos = Path(videos) if videos else self.media_root / "videos"
        (self.media_root / "clips").mkdir(parents=True, exist_ok=True)
        for still in self.media_root.glob("*.bmp"):  # left by an earlier run: no page will ask for them again
            self._drop(still)
        self._tidy()
        self.viewer = viewer or Viewer()
        self.lock = threading.RLock()
        self.compiled = {}  # camera_key -> cam commands, so an unchanged shot never rereads its tracks
        self.paths = {}  # (start, end, step) -> aircraft tracks: every angle tried on one stretch reads them once
        self.flight = None  # the last stretch's flight data in full; state["flight"] only says what it holds
        self.applied, self.built = None, {}  # which plan the viewer holds, and what could not be built in it
        self.jumped = 0.0  # when the film last jumped, for the aircraft fading in after it
        self.quiet = 0.0  # when the viewer has finished the last render's sound and the next render may start
        self.pace = 1  # how long this desk waits for the viewer's own timing, as a share; tests have no viewer to wait for
        self.stills = deque()
        self.drawing = deque()  # data overlays still to draw: ffmpeg's work, the viewer is not needed for it
        self.drawer = None
        self.films_folder = None  # where the viewer keeps its films, as its `films` reply says
        self.data = {"connected": False, "films": [], "project": None, "planes": [],
                     "now": 0, "icons": False, "problems": {}, "nearby": None, "airfield": None, "flight": None,
                     "preview": None, "proposals": [], "job": None, "videos": str(self.videos), "renders": [],
                     "ffmpeg": bool(shutil.which("ffmpeg")), "overlays": None}

    @staticmethod
    def _drop(path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass  # still open in a browser on Windows: the next start takes it

    def _tidy(self):
        """The working folder keeps the page's copies of the last few renders only; the videos folder has them all."""
        for old in sorted(self.media_root.glob("*.mp4"), key=lambda p: p.stat().st_mtime)[:-6]:
            self._drop(old)

    def _keep(self, path, project, row, labels):
        """A finished render, copied to the videos folder under a name that tells it from the others: the film,
        where the shot starts, its name and its camera (the page sends each shot's camera as it calls it)."""
        clock = lambda seconds: f"{int(seconds // 60):02d}.{int(seconds % 60):02d}"
        film, first = Path(project["film"]).stem, row[0]
        if len(row) == 1:
            camera = labels.get(first["id"]) if isinstance(labels, dict) else None
            title = f"{film} {clock(first['start'])} {first['name']} - {camera if isinstance(camera, str) and camera else first['kind']}"
        else:
            title = f"{film} {clock(first['start'])}-{clock(row[-1]['end'])} cut of {len(row)} shots"
        title = re.sub(r":\s*", " ", title)  # "Kill: Somebody" reads better as "Kill Somebody" than "Kill- Somebody"
        title = re.sub(r'[\\/*?"<>|\x00-\x1f]+', "-", title).strip(" .")[:120] or "video"
        self.videos.mkdir(parents=True, exist_ok=True)
        target, n = self.videos / (title + ".mp4"), 2
        while target.exists():  # the same shot rendered again is kept beside the first
            target, n = self.videos / f"{title} ({n}).mp4", n + 1
        shutil.copyfile(path, target)
        return target

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
        if data["job"] and data["job"]["running"]:
            data["job"]["elapsed"] = round(time.monotonic() - data["job"]["clock"], 1)
        return data

    def _idle(self):
        if self.data["job"] and self.data["job"]["running"]:
            raise Conflict("Film Viewer is busy. The current operation must finish first.")

    def _revision(self, raw):
        project = self.data["project"]
        if project is None:
            raise ValueError("Open a film first")
        if type(raw.get("expected_revision")) is not int or raw["expected_revision"] != project["revision"]:
            raise Conflict("The plan changed. Reload it and merge your changes before saving.")
        return project

    def _persist(self, project):
        path = self.root / (hashlib.sha256(project["film"].encode()).hexdigest()[:20] + ".json")
        temp = path.with_suffix(".new")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(project, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)

    def _renders_file(self, film):
        return self.root / (hashlib.sha256(film.encode()).hexdigest()[:20] + ".renders.json")

    def _load_renders(self, film):
        """The finished videos of a film that are still on disk, with the shots in each and their overlays."""
        path = self._renders_file(film)
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        return [row for row in rows if Path(row["file"]).exists() or any(Path(o).exists() for o in row.get("overlays", []))]

    def _record_render(self, film, row):
        with self.lock:
            renders = self.data["renders"] + [row]
            path = self._renders_file(film)
            temp = path.with_suffix(".new")
            temp.write_text(json.dumps(renders, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)
            self.data["renders"] = renders

    def forget(self, raw):
        """Take shots out of the plan; with `files`, move their finished videos and overlays to the Recycle Bin.
        A video of several shots goes with any of them: the page lists every file before asking."""
        with self.lock:
            self._idle()
            old = self._revision(raw)
            ids = raw.get("shot_ids")
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids):
                raise ValueError("Choose the shots to remove")
            project = {**old, "shots": [s for s in old["shots"] if s["id"] not in ids], "revision": old["revision"] + 1}
            self._persist(project)
            self.data["project"] = project
            gone, kept = [], []
            for row in self.data["renders"]:
                (gone if raw.get("files") is True and set(row["shots"]) & set(ids) else kept).append(row)
            paths = [Path(f) for row in gone for f in [row["file"], *row.get("overlays", [])]]
            paths = [f for f in paths if f.suffix.lower() in (".mp4", ".mov") and f.exists()]
            if paths:
                to_recycle_bin(paths)
            if gone:
                path = self._renders_file(project["film"])
                path.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
                self.data["renders"] = kept
            return {"project": copy.deepcopy(project), "renders": copy.deepcopy(self.data["renders"]), "recycled": len(paths)}

    def delete_film(self, raw):
        """Move a replay from the viewer's films folder to the Recycle Bin. Not the open one: Film Viewer holds it.
        Its saved shots stay with the desk and its finished videos in the videos folder."""
        with self.lock:
            self._idle()
            row = next((f for f in self.data["films"] if f["path"] == raw.get("film")), None)
            if row is None or self.films_folder is None:
                raise ValueError("Choose a replay from the list")
            if self.data["project"] and self.data["project"]["film"] == row["path"]:
                raise ValueError("Open another replay first: Film Viewer holds the one that is open")
            folder = self.films_folder.resolve()
            path = (folder / row["name"]).resolve()
            if path.parent != folder or path.suffix.lower() != ".ahf":
                raise ValueError("Only a replay in the films folder can be deleted")
            if path.exists():
                to_recycle_bin([path])
            self.data["films"] = [f for f in self.data["films"] if f["path"] != row["path"]]
            return {"films": copy.deepcopy(self.data["films"])}

    def save(self, raw):
        with self.lock:
            self._idle()
            old = self._revision(raw)
            project = {**old, "shots": validate_plan(raw.get("shots"), old["length"]),
                       "revision": old["revision"] + 1}
            self._persist(project)
            self.data["project"] = project
            return copy.deepcopy(project)

    def progress(self, message):
        with self.lock:
            self.data["job"]["message"] = message

    def command(self, command):
        return self.viewer.command(command, self.progress)

    def _render(self, start, end, path, size, label="", blur=None, filters=""):
        """One video of a stretch, with the film's sound whole (see TAIL) and the aircraft solid from its first frame
        when ffmpeg is there to cut off the lead-in (see LEAD). `blur`: the frame rate to blend the passes of a `size`
        at FASTEST down to (see PASSES). `filters`: the looks to give it (see style)."""
        early = min(LEAD, start) if shutil.which("ffmpeg") else 0
        early = early if early >= .05 else 0
        with self.lock:  # the wait is part of what this clip takes
            if self.data["job"]["expect"]:
                self.data["job"]["expect"] = round(self.data["job"]["expect"] + max(0, self.quiet - time.monotonic()), 1)
        while time.monotonic() < self.quiet:
            self.progress(f"Film Viewer is finishing the last clip's sound: {math.ceil(self.quiet - time.monotonic())} s, so this one keeps its own")
            time.sleep(.25)
        self.progress(label or f"Rendering {end - start:.1f} s of film")
        if abs(self.data["now"] - (start - early)) > .5:
            # A render that began far from where the film stood came out with its first second silent, just after
            # opening a film (-49 dB, 5 of 5). A seek there and 2 s paused kept it whole (2 of 2); a seek alone, or
            # with a still drawn, did not. Paused, not playing: playing before a render thins its sound (see LEAD).
            self._seek(start - early)
            time.sleep(2 * self.pace)
        whole = path.with_name(path.stem + "-lead" + path.suffix) if early or blur or filters else path
        parts = [whole.with_name(f"{whole.stem}{n}{whole.suffix}") for n in range(PASSES)] if blur else [whole]
        for n, part in enumerate(parts):  # only the first pass's sound is kept: the others need not wait for quiet
            if n:
                self.progress(f"{label}: motion blur pass {n + 1} of {PASSES}")
            shift = n / FASTEST / PASSES
            self.command(f"video {start - early + shift:.8f} {end + shift:.8f} {part.as_posix()} {size}")
        self.quiet = time.monotonic() + (end - start + early + TAIL) * self.pace
        if blur:
            self.progress("Blending the motion blur with ffmpeg")
            blend(parts, path, early, blur, filters)
        elif early or filters:
            self.progress("Giving it its look with ffmpeg" if filters else "Cutting off the lead-in with ffmpeg")
            trim(whole, path, early, filters)

    def start(self, raw):
        action = raw.get("action")
        if action not in ACTIONS:
            raise ValueError("Unknown action")
        with self.lock:
            self._idle()
            if action != "connect" and not self.data["connected"]:
                raise ValueError("Connect to Film Viewer first")
            if action not in ("connect", "open"):
                self._revision(raw)
            elif action == "open" and self.data["project"]:
                self._revision(raw)
            self.data["job"] = {"id": uuid.uuid4().hex, "running": True, "action": action,
                                "message": "Connecting to Film Viewer" if action == "connect" else "Preparing " + action,
                                "clock": time.monotonic(), "elapsed": 0, "expect": None, "error": None}
            job = copy.deepcopy(self.data["job"])
            self.worker = threading.Thread(target=self._work, args=(copy.deepcopy(raw),), daemon=True)
            self.worker.start()
            return job

    def _work(self, raw):
        try:
            self._action(raw)
            self.progress("Ready")
        except Exception as error:
            with self.lock:
                self.data["job"]["error"] = str(error)
                self.data["job"]["message"] = str(error)
                if isinstance(error, (OSError, ConnectionError)):
                    self.data["connected"] = False
        finally:
            with self.lock:
                self.data["job"]["running"] = False

    def _seek(self, seconds, rough=False):
        backwards = seconds < self.data["now"]
        self.progress(("Rewinding" if backwards else "Seeking") + f" to {seconds:.2f}s — Film Viewer is rebuilding the scene")
        self.command(f"seek {seconds:.8f}" + (" rough" if rough else ""))
        self.jumped = time.monotonic()
        planes = plane_rows(self.command("planes"))
        with self.lock:
            self.data.update(now=seconds, planes=planes)

    def _preroll(self, seconds, rough=False):
        """Stand at `seconds` with the aircraft drawn solid (see LEAD)."""
        start = max(0.0, seconds - LEAD)
        if seconds - start < .3:  # nothing before it to play: the aircraft fade in as after any jump
            return self._seek(seconds, rough=rough)
        self._seek(start, rough=rough)
        self.progress(f"Playing up to {seconds:.2f}s so the aircraft are drawn solid")
        self.command("play")
        deadline = time.monotonic() + 3 * LEAD + 5
        try:
            while time.monotonic() < deadline:
                reply = self.command("wait 5")  # "waited, time <film seconds>"
                try:
                    if float(reply[0].rsplit(" ", 1)[1]) >= seconds - .3:
                        break
                except (IndexError, ValueError):
                    break
        finally:
            self.command("pause")
        self._seek(seconds)
        self.jumped = 0.0  # a short seek forward: nothing fades in

    def _settle(self):
        """Let the aircraft fade in after a jump, before a picture that will stay on screen for a while."""
        while time.monotonic() - self.jumped < SETTLE * self.pace:
            self.command("wait 10")

    def _open(self, film):
        if film not in {row["path"] for row in self.data["films"]}:
            raise ValueError("Choose a film from the viewer library")
        self.progress("Opening film and reading its moments")
        self.compiled, self.paths, self.applied, self.flight = {}, {}, None, None
        with self.lock:
            self.data.update(project=None, preview=None, planes=[], problems={}, nearby=None, airfield=None, flight=None)
        reply = self.command("open " + film)
        match = re.search(r" length ([\d.]+) s", reply[0])
        if not match or "no ground" in reply[0]:
            raise ValueError("Check the terrain in Film Viewer before editing this film")
        # The viewer tells a film's length rounded to 0.1 s, and `tracks` leaves out a moment past the real end
        # (measured: a film "384.5 s" long had a snapshot at 384.48 and none at 384.5), so a shot reaching the
        # told length could not be filmed. The desk's film ends a twentieth of a second sooner: inside the film.
        length = round(max(.1, float(match[1]) - .05), 2)
        events = []
        for line in self.command("events kill,land,guns,impact,hit,drop"):
            if line.startswith("event "):
                fields = shlex.split(line)
                events.append({"id": str(len(events)), "time": float(fields[1]), "kind": fields[2],
                               "text": " ".join(fields[3:]), "fields": fields[3:]})
        path = self.root / (hashlib.sha256(film.encode()).hexdigest()[:20] + ".json")
        if path.exists():
            saved = json.loads(path.read_text(encoding="utf-8"))
            # A shot saved up to the told length ends with the film.
            shots = validate_plan([{**shot, "end": min(shot["end"], length)} for shot in saved["shots"]], length)
        else:
            saved, shots = {}, []
        project = {"film": film, "length": length, "revision": saved.get("revision", 0) + 1,
                   "shots": shots, "events": events}
        self._persist(project)
        with self.lock:
            self.data.update(project=project, now=1, preview=None, proposals=[], renders=self._load_renders(film))
        self._seek(min(1, length))
        self._settle()
        self._frame(project["revision"])

    def _tracks(self, start, end, step=None):
        # Choose an exact divisor so the final snapshot lands at the end.
        step = step or (end - start) / max(1, math.ceil((end - start) * 2))
        key = (round(start, 3), round(end, 3), round(step, 6))
        if key not in self.paths:
            self.progress("Reading aircraft paths")
            # Read every 0.02 s and the snapshots wanted picked out. Read every 0.5 s, the recording pilot was 25 to
            # 51 ft off his own path (measured on a Me 163): 7 degrees seen from an observer 390 ft away, nearly the
            # whole picture on a 9-degree lens, and the camera shook. The first snapshots of a read are off as well.
            early = min(.1, start)
            fine = track_rows(self.command(f"tracks {start - early:.8f} {end:.8f} {FINE:.10f}"))
            grid = sorted(fine)
            picked = {}
            for k in range(round((end - start) / step) + 1):
                t = start + k * step
                at = bisect.bisect_left(grid, t)
                near = min(grid[max(0, at - 1):at + 1], key=lambda g: abs(g - t), default=None)
                if near is not None and abs(near - t) <= FINE / 2 + 1e-6:
                    picked[t] = fine[near]
            self.paths[key] = picked
            while len(self.paths) > 12:
                del self.paths[next(iter(self.paths))]
        return self.paths[key]

    def _around(self, raw, project):
        """Who is near the lead aircraft in a stretch, so a shot's opponent is whoever was there to be fought."""
        start, end = number(raw.get("start"), 0, project["length"], "start"), number(raw.get("end"), 0, project["length"], "end")
        hero = number(raw.get("hero"), 0, 1000000, "hero")
        if not .1 <= end - start <= 120:
            raise ValueError("Choose a stretch of 0.1 to 120 seconds")
        names = {plane["id"]: plane["name"] for plane in self.data["planes"]}
        aircraft = [{**row, "name": names.get(row["id"])} for row in nearby(self._tracks(start, end), int(hero))]
        with self.lock:
            self.data["nearby"] = {"start": start, "end": end, "hero": int(hero), "aircraft": aircraft}

    def _airfield(self, project):
        """Your take-offs and touchdowns in the whole film, from your flight path a second apart (see
        ground_moments): about 5 s for a 9 minute film, read ten minutes at a time."""
        length, lines = project["length"], []
        for start in range(0, math.ceil(length), 600):
            self.progress(f"Reading your flight path for take-offs and landings: {start / length:.0%}")
            lines += self.command(f"tracks {start:.2f} {min(length, start + 600):.2f} 1")
        with self.lock:
            self.data["airfield"] = {"film": project["film"], "moments": ground_moments(filmer_path(lines))}

    def _flight(self, raw, project):
        """What every aircraft did in a stretch, and the lead and his opponent to each other (flight_data.py), from
        the viewer's kept tracks with their speeds: `tracks ... keep` writes them to a file in its trackcache folder,
        documented for reading directly. The whole of it is at GET /api/flight; the state says what it holds."""
        start, end = number(raw.get("start"), 0, project["length"], "start"), number(raw.get("end"), 0, project["length"], "end")
        # Read at 0.1 s, the recording pilot's own track jitters (measured on a Me 163 at 618 ft/s: steps of 0.3 to 114 ft
        # a snapshot, stored speeds 356 to 889 ft/s); at 0.02 s it is smooth, 12.3 ft every snapshot. The first two
        # snapshots of a read are off as well, so the read starts a tenth of a second early and those are left out.
        step = number(raw.get("step", .02), .02, 1, "step")
        if not .1 <= end - start <= 600:
            raise ValueError("Choose a stretch of 0.1 to 600 seconds")
        ids = []
        for key in ("hero", "target"):
            value = raw.get(key)
            ids.append(None if value in (None, "") else int(number(value, 0, 1000000, key)))
        hero, target = ids
        flight = self._read_flight(project, start, end, step)
        data = flight["data"]
        for ident in ids:
            if ident is not None and str(ident) not in data["aircraft"]:
                raise ValueError(f"Aircraft {ident} is not in the film in that stretch")
        rows = flight["rows"]
        data.update(hero=hero, target=target, pair=pair(rows[hero], rows[target]) if hero is not None and target is not None else [])
        with self.lock:
            self.data["flight"] = {**{k: data[k] for k in ("film", "start", "end", "step", "hero", "target")},
                                   "aircraft": [{k: row[k] for k in ("id", "name", "radius", "quality", "update")} for row in data["aircraft"].values()]}

    def _read_flight(self, project, start, end, step=.02):
        """Every aircraft's flight in a stretch, kept as the last one read: {key, rows, data}."""
        key = (project["film"], round(start, 3), round(end, 3), round(step, 3))
        if not self.flight or self.flight["key"] != key:
            self.progress("Reading every aircraft's flight")
            reply = self.command(f"tracks {max(0, start - .1):.8f} {end:.8f} {step:.10f} keep speed on pos double")
            found = next((m[1] for line in reply if (m := re.search(r" file (.+\.ahftracks)$", line))), None)
            if not found:
                raise ValueError("Film Viewer did not say where it kept the tracks")
            kept = read_kept(found)
            kept["tracks"] = {ident: [row for row in rows if start - 1e-6 <= row[0] <= end + 1e-6] for ident, rows in kept["tracks"].items()}
            aircraft = {str(ident): {"id": ident, **kept["aircraft"].get(ident, {"name": "", "radius": 0}), **derive(rows, kept["step"])}
                        for ident, rows in kept["tracks"].items() if rows}
            self.flight = {"key": key, "rows": kept["tracks"], "data": {"film": project["film"], "start": start, "end": end,
                                                                         "step": kept["step"], "aircraft": aircraft}}
        return self.flight

    def _segment(self, project, shot):
        """What the data overlay shows through one shot: its lead aircraft's numbers and the pair's (overlay.py)."""
        flight = self._read_flight(project, shot["start"], shot["end"])
        aircraft, rows = flight["data"]["aircraft"], flight["rows"]
        hero, target = shot["hero"], shot.get("target")
        lead = aircraft.get(str(hero), {})
        return {"start": shot["start"], "end": shot["end"], "hero": lead.get("samples", []), "hero_name": lead.get("name") or "You",
                "target_name": None if target is None else (aircraft.get(str(target), {}).get("name") or "Opponent"),
                "pair": pair(rows[hero], rows[target]) if hero in rows and target in rows else None}

    def _overlays(self, video, segments, quality):
        """Queue the data overlays of a finished video, to be drawn beside it while the desk goes on: one for a 16:9
        timeline at the video's size, one for a 9:16 reel, at the video's frame rate and length. Drawing them in the
        render's own job held the viewer for 80 s after a 37 s render at 4K (measured), and the next camera waited."""
        _, height, fps = map(int, RENDERS[quality].split())
        made = [(layout, video.with_name(f"{video.stem} - overlay {layout}.mov"), tall)
                for layout, tall in (("16x9", height), ("9x16", 960 if quality == "draft" else 1920))]
        with self.lock:
            self.drawing.extend((layout, segments, path, tall, fps) for layout, path, tall in made)
            if self.data["overlays"] and self.data["overlays"]["running"]:  # the drawer looks at the queue again before it stops
                self.data["overlays"]["left"] = len(self.drawing) + 1
            else:
                self.data["overlays"] = {"running": True, "message": "Drawing data overlays", "left": len(self.drawing), "error": None}
                self.drawer = threading.Thread(target=self._draw, daemon=True)
                self.drawer.start()
        return [str(path) for _, path, _ in made]

    def _draw(self):
        """Draw queued overlays one at a time, until none is left."""
        while True:
            with self.lock:
                if not self.drawing:
                    self.data["overlays"].update(running=False, left=0, message="Data overlays ready" if not self.data["overlays"]["error"] else self.data["overlays"]["message"])
                    return
                layout, segments, path, tall, fps = self.drawing.popleft()
                self.data["overlays"].update(message=f"Drawing the {layout.replace('x', ':')} data overlay of {path.stem.rsplit(' - overlay', 1)[0]}",
                                             left=len(self.drawing) + 1)
            try:
                overlay.render(layout, segments, path, tall, fps)
            except Exception as error:  # one failed overlay must not stop the others, nor the desk
                with self.lock:
                    self.data["overlays"].update(error=str(error), message=f"{path.name}: {error}")

    def flight_data(self):
        """The last stretch's flight data in full, for GET /api/flight; None before one was read."""
        with self.lock:
            return copy.deepcopy(self.flight["data"]) if self.flight else None

    def _plan(self, project, raw):
        """The saved shots, with an unsaved draft (if one was sent) standing in for whatever it overlaps,
        so an angle can be watched before it is added to the cut. Of saved takes of one stretch, the first plays."""
        if not raw.get("shot"):
            return (passes(project["shots"]) or [[]])[0], ("plan", project["revision"]), None
        draft = validate_shot(raw["shot"], project["length"])
        clear = [s for s in project["shots"] if s["id"] != draft["id"] and (s["end"] <= draft["start"] or s["start"] >= draft["end"])]
        return sorted(clear + [draft], key=lambda s: s["start"]), ("draft", project["revision"], camera_key(draft)), draft

    def _apply(self, project, raw=None, only=None):
        """Send the viewer its cameras: the plan (see _plan), or `only` these shots for one video of a cut."""
        shots, token, draft = self._plan(project, raw or {})
        if only is not None:
            shots, token = only, ("cut", project["revision"], *(s["id"] for s in only))
        if self.applied != token:
            commands, problems, drawn = [], {}, 0.0
            for shot in shots:
                key = camera_key(shot)
                if key not in self.compiled:
                    try:
                        tracks = {} if shot["kind"] in RIDES else self._tracks(shot["start"], shot["end"])
                        self.compiled[key] = compile_shot(shot, tracks)
                    except ValueError as error:
                        # One shot that cannot be built must not stop the picture for the rest.
                        problems[shot["id"]] = str(error)
                        continue
                commands.extend(lead(self.compiled[key][0], max(drawn, shot["start"] - LEAD)) + self.compiled[key])
                drawn = max(drawn, shot["end"])
            while len(self.compiled) > 300:  # angles tried and dropped
                del self.compiled[next(iter(self.compiled))]
            self.progress("Applying camera plan")
            self.applied = None
            self.command("cams clear")
            for command in commands:
                self.command(command)
            self.command("cams on")
            self.command("cams save " + (self.root / "current.ahs").as_posix())
            self.applied, self.built = token, problems
        if only is not None:
            return
        if draft is None:
            with self.lock:
                self.data["problems"] = self.built
        elif draft["id"] in self.built:
            raise ValueError(self.built[draft["id"]])

    def _trails(self, project, row, width, height, fps, rgb):
        """An ASS script of the wingtip trails over one video of a cut (see trails.py), in the working folder. The
        video runs the row's shots end to end (the gaps between them skipped), from the first one's start."""
        first, last = row[0]["start"], row[-1]["end"]
        flight = self._read_flight(project, max(0.0, first - trails.STYLE["seconds"]), last)
        spans, offset = [], 0.0
        for shot in row:
            key = camera_key(shot)
            if key not in self.compiled:
                tracks = {} if shot["kind"] in RIDES else self._tracks(shot["start"], shot["end"])
                self.compiled[key] = compile_shot(shot, tracks)
            spans.append((offset, shot, self.compiled[key]))
            offset += shot["end"] - shot["start"]
        frames = []
        for k in range(round(offset * fps) + 1):
            if k % fps == 0:
                self.progress(f"Finding the camera for the trails: {k // fps} of {math.ceil(offset)} s")
            begin, shot, lines = next(span for span in reversed(spans) if span[0] <= k / fps + 1e-9)
            t = min(shot["end"], shot["start"] + k / fps - begin)
            frames.append((t, self._eye(lines, t, shot)))
        radii = {int(ident): plane["radius"] for ident, plane in flight["data"]["aircraft"].items()}
        ids = sorted({shot[key] for shot in row for key in ("hero", "target") if shot.get(key) is not None})
        path = self.media_root / (uuid.uuid4().hex + ".ass")
        path.write_text(trails.script(frames, flight["rows"], radii, ids, flight["data"]["step"], fps, width, height, rgb),
                        encoding="utf-8")
        return path

    def _eye(self, lines, t, shot):
        """Where the camera of a shot's compiled `cam` lines is at film time t (x, alt, z, yaw, pitch, roll, fov), by
        the viewer's own projection from the kept tracks; None for the pilot's view and the weapon ride, which it
        does not project. The projection takes still cameras only: a moving one is placed on its curve first."""
        if shot["kind"] in ("recorded", "weapon"):
            return None
        pieces = [line.split(maxsplit=3) for line in lines]  # cam, start, duration, the camera
        rest = pieces[0][3]
        if " to " in rest:
            points = [(float(p[1]), [float(v) for v in p[3].split()[1:8]]) for p in pieces]
            points.append((float(pieces[-1][1]) + float(pieces[-1][2]), [float(v) for v in pieces[-1][3].split(" to ")[1].split()[:7]]))
            held = re.search(r" at (\S+)", rest)
            camera = "fixed " + " ".join(f"{v:.8f}" for v in trails.between(points, t)) + (f" at {held[1]}" if held else "")
        else:
            camera = re.sub(r" (outside|curve)\b", "", rest).split(" name ")[0]
        camfile, found = self.root / "trailcam.txt", self.root / "trailcam-eye.txt"
        camfile.write_text(camera + "\n", encoding="ascii")
        # Ending at t keeps it inside the kept tracks (beyond them the viewer would read the film); its second step is t.
        self.command(f"project {t - .15:.8f} {t:.8f} .15 {camfile.as_posix()} {found.as_posix()} ids {shot['hero']}")
        eyes = [row.split() for row in found.read_text().splitlines() if row.startswith("eye ")]
        return [float(v) for v in eyes[-1][3:10]] if eyes else None

    def _cut(self, project, row):
        """The viewer set to film these shots in a row as one video, the gaps between them skipped."""
        self._apply(project, only=row)
        for shot in row:
            if shot["id"] in self.built:
                raise ValueError(f"{shot['name']}: {self.built[shot['id']]}")
        for a, b in zip(row, row[1:]):
            if b["start"] > a["end"] + .01:
                self.command(f"skip {a['end']:.8f} {b['start']:.8f}")
        self.applied = None  # The skips must not leak into the next look at one shot.

    def _glance(self, project, raw):
        """A still of the moment already on screen through a camera that has just changed. Only that camera is
        sent and the film is not moved, so the picture is back in about a tenth of a second (measured 0.08 s):
        fast enough to follow a drag on the picture."""
        draft, now = validate_shot(raw["shot"], project["length"]), self.data["now"]
        key = camera_key(draft)
        if key not in self.compiled:
            tracks = {} if draft["kind"] in RIDES else self._tracks(draft["start"], draft["end"])
            self.compiled[key] = compile_shot(draft, tracks)
            while len(self.compiled) > 300:
                del self.compiled[next(iter(self.compiled))]
        spans = [(command, *map(float, command.split()[1:3])) for command in self.compiled[key]]
        # A seek can leave the film a little past the moment asked for (measured: up to 0.08 s, and `time` only
        # tells tenths), so a moving camera's neighbouring pieces go too.
        lines = [command for command, start, duration in spans if start - .3 <= now <= start + duration + .3]
        self.applied = None  # the viewer now holds this one camera, not the plan
        self.command("cams clear")
        for line in lines or [spans[0][0]]:
            self.command(line)
        self.command("cams on")
        self._frame(project["revision"])

    def _frame(self, revision):
        path = self.media_root / (uuid.uuid4().hex + ".bmp")
        self.command("shot " + path.as_posix())
        with self.lock:
            # fresh: taken while the aircraft were still fading in after a jump; worth taking again once they are solid
            self.data["preview"] = {"url": "/media/" + path.name, "kind": "image", "time": self.data["now"],
                                    "revision": revision, "film": self.data["project"]["film"],
                                    "fresh": time.monotonic() - self.jumped < FADE}
        self.stills.append(path)
        while len(self.stills) > 3:  # A browser may still be fetching the last couple.
            try:
                self.stills.popleft().unlink(missing_ok=True)
            except OSError:
                pass  # Still open in a browser on Windows; it stays in the output folder.

    def _clip(self, raw, project):
        """The desk's moving picture. The socket has no stream, and the viewer renders a video in about the
        time it plays, so a stretch is rendered once and replayed from its file until its cameras change."""
        length = project["length"]
        start, end, row = self._stretch(raw, project)
        shots = row or self._plan(project, raw)[0]
        if raw.get("hero") is not None:  # who is near the lead in this stretch, for choosing the opponent
            self._around(raw, project)
        film = next((row for row in self.data["films"] if row["path"] == project["film"]), {})
        name = hashlib.sha256(json.dumps([
            project["film"], film.get("mb"), film.get("modified"), round(start, 2), round(end, 2), CLIP, CAMERAS,
            self.data["icons"], row is not None,
            [camera_key(s) for s in shots if s["end"] > start and s["start"] < end]]).encode()).hexdigest()[:32]
        folder = self.media_root / "clips"
        path = folder / (name + ".mp4")
        if " " in str(path):
            raise ValueError("Film Viewer video paths cannot contain spaces: move AH Quick Shots to a folder without spaces in its path")
        if path.exists():
            path.touch()
        else:
            with self.lock:
                self.data["job"]["expect"] = round(end - start + 1.5, 1)
            if row:
                self._cut(project, row)
            else:
                self._apply(project, raw)
            if raw.get("still") is not None and not row:  # something to look at while the clip renders
                self._seek(number(raw["still"], 0, length, "time"), rough=True)
                self._settle()
                self._frame(project["revision"])
            self._render(start, end, path, CLIP)  # the film is back where it was afterwards
            for old in sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime)[:-80]:
                old.unlink(missing_ok=True)
        with self.lock:
            self.data["preview"] = {"url": "/media/clips/" + path.name, "kind": "clip", "start": start, "end": end,
                                    "time": start, "revision": project["revision"], "film": project["film"]}

    @staticmethod
    def _stretch(raw, project):
        """What a preview shows: saved shots in a row (`shot_ids`, a sequence: as they will be rendered), or a
        stretch from `start` to `end` through the plan and an unsaved shot. (start, end, the row or None)"""
        length, row = project["length"], None
        if raw.get("shot_ids") is not None:
            if not isinstance(raw["shot_ids"], list):
                raise ValueError("shot_ids must be a list")
            row = sorted((s for s in project["shots"] if s["id"] in raw["shot_ids"]), key=lambda s: s["start"])
            if not row or any(b["start"] < a["end"] - .01 for a, b in zip(row, row[1:])):
                raise ValueError("Choose saved shots that follow one another")
            start, end = row[0]["start"], row[-1]["end"]
        else:
            start, end = number(raw.get("start"), 0, length, "start"), number(raw.get("end"), 0, length, "end")
        if not .1 <= end - start <= 120:
            raise ValueError("A preview lasts 0.1 to 120 seconds")
        return start, end, row

    def _live(self, raw, project):
        """The stretch played in Film Viewer's own window, through its cameras: seen at once, with its sound,
        nothing rendered. Playing just before a render thins the render's sound (see LEAD), so the next render
        waits as after a render of that length, and never less than 12 s (renders that far apart were whole)."""
        start, end, row = self._stretch(raw, project)
        if row:
            self._cut(project, row)
        else:
            self._apply(project, raw)
        self._seek(start)
        self.progress(f"Playing {end - start:.1f} s in Film Viewer's window")
        self.command("play")
        deadline, now = time.monotonic() + 2 * (end - start) + 5, start
        try:
            while time.monotonic() < deadline and now < end - .05:
                try:
                    now = float(self.command("wait 6")[0].rsplit(" ", 1)[1])  # "waited, time <film seconds>"
                except (IndexError, ValueError):
                    break
        finally:
            self.command("pause")
            self.quiet = max(self.quiet, time.monotonic() + max(12, end - start + TAIL) * self.pace)
            self.applied = None
            with self.lock:
                self.data["now"] = min(now, project["length"])

    def _suggest(self, raw, project):
        with self.lock:
            self.data["proposals"] = []  # Ratings of another interval must not pass for this one.
        shots = raw.get("shots")
        if not isinstance(shots, list) or not 1 <= len(shots) <= 64:
            raise ValueError("Send 1 to 64 candidate shots")
        shots = [validate_shot(shot, project["length"]) for shot in shots]
        start, end = shots[0]["start"], shots[0]["end"]
        if end - start < .5 or any((shot["start"], shot["end"]) != (start, end) for shot in shots):
            raise ValueError("Candidates must share one interval of at least half a second")
        samples = min(24, math.ceil((end - start) * 2))
        step = (end - start) / samples
        def index(seconds):
            return round((seconds - start) / step)
        tracks = {index(t): rows for t, rows in self._tracks(start, end, step).items()}
        if not tracks:
            raise ValueError("No aircraft paths returned")
        cameras, plans = [], []
        for shot in shots:
            try:
                probes = probe_cameras(shot, tracks, step)
            except ValueError:
                probes = []  # Rated as never framed.
            plans.append({key: len(cameras) + n for n, (key, _line) in enumerate(probes, 1)})
            cameras.extend(line for _key, line in probes)
        if not cameras:
            raise ValueError("A selected aircraft is missing during this shot. Shorten the interval or choose another aircraft.")
        camera_file, output = self.root / "candidates.txt", self.root / "projection.txt"
        camera_file.write_text("\n".join(cameras), encoding="ascii")
        ids = sorted({shot[key] for shot in shots for key in ("hero", "target") if shot[key] is not None})
        self.progress(f"Rating {len(shots)} camera angles over the whole shot")
        self.command(f"project {start:.8f} {end:.8f} {step:.10f} {camera_file.as_posix()} {output.as_posix()} ids {','.join(map(str, ids))} aspect 16/9")
        steps, seen = set(), {}
        for line in output.read_text().splitlines():
            row = line.split()
            if row and row[0] in ("eye", "on"):
                steps.add(index(float(row[1])))
                if row[0] == "on":
                    seen[int(row[2]), index(float(row[1])), int(row[3])] = tuple(map(float, row[4:7]))
        if not steps:
            raise ValueError("Film Viewer returned no framing for this interval")
        proposals = []
        for shot, plan in zip(shots, plans):
            # A camera riding the lead aircraft has it in the picture by construction; its centre projects off screen.
            need = [shot["target"]] if shot["kind"] in (*RIDES, "ambush") else [shot["hero"], shot["target"]]
            if shot["kind"] == "escort" and shot["watch"] != "both":  # a camera ship watching one aircraft
                need = [shot[shot["watch"]]]
            need = [ident for ident in need if ident is not None]
            sizes, quality = [], 0
            for i in steps:
                found = [seen.get((plan.get(None, plan.get(i)), i, ident)) for ident in need]
                if need and None not in found:
                    sizes.append(found[0][2])
                    quality += framing(found)
            proposals.append({"id": shot["id"], "score": round(len(sizes) / len(steps), 3) if need else None,
                              "size": round(sum(sizes) / len(sizes), 4) if sizes else 0,
                              "quality": round(quality / len(steps), 3)})
        with self.lock:
            self.data["proposals"] = proposals

    def _action(self, raw):
        action = raw["action"]
        if action == "connect":
            greeting = self.viewer.connect(self.progress)
            if int(greeting.split()[1]) < 5:
                raise ValueError("Update Film Viewer to 3.07 Patch 2 or newer")
            listing = self.command("films")
            films = film_rows(listing, os.path.getmtime)
            self.films_folder = next((Path(line[7:]) for line in listing if line.startswith("folder ")), None)
            self.command("icons " + ("on" if self.data["icons"] else "off"))
            with self.lock:
                self.data.update(connected=True, films=films, greeting=greeting)
            if self.data["project"]:
                self._open(self.data["project"]["film"])
            return
        if action == "open":
            return self._open(raw.get("film"))
        project = self.snapshot()["project"]
        if action == "suggest":
            return self._suggest(raw, project)
        if action == "airfield":
            return self._airfield(project)
        if action == "around":
            return self._around(raw, project)
        if action == "flight":
            return self._flight(raw, project)
        if action == "clip":
            return self._clip(raw, project)
        if action == "live":
            return self._live(raw, project)
        if action in ("seek", "preview", "icons"):
            # The names switch answers with a still of the moment asked for, like a seek: seen at once, no render.
            if action == "icons":
                self.command("icons " + ("on" if raw.get("on") else "off"))
                with self.lock:
                    self.data["icons"] = bool(raw.get("on"))
            shot, seconds = raw.get("shot"), raw.get("seconds")
            if (isinstance(shot, dict) and isinstance(seconds, (int, float)) and abs(seconds - self.data["now"]) < .02
                    and shot.get("start", seconds) <= seconds <= shot.get("end", seconds)):
                return self._glance(project, raw)
            self._apply(project, raw)
            selected = next((s for s in project["shots"] if s["id"] == raw.get("shot_id")), None)
            if action in ("seek", "preview") or raw.get("seconds") is not None:
                seconds = number(raw.get("seconds", selected["start"] if selected else 0), 0, project["length"], "time")
                if action != "seek" or abs(seconds - self.data["now"]) >= .02:  # asked for where it is, the film is not moved
                    self._preroll(seconds, rough=bool(raw.get("rough")))
            return self._frame(project["revision"])
        ids = raw.get("shot_ids")
        if ids is not None and not isinstance(ids, list):
            raise ValueError("shot_ids must be a list")
        shots = [s for s in project["shots"] if ids is None or s["id"] in ids]
        if not shots:
            raise ValueError("Select at least one saved shot")
        if " " in str(self.media_root):
            raise ValueError("Film Viewer video paths cannot contain spaces: move AH Quick Shots to a folder without spaces in its path")
        quality = raw.get("quality") if raw.get("quality") in RENDERS else "final"
        size = RENDERS[quality].replace(" ", "x", 1).replace(" ", " @ ")
        # The viewer holds one camera at a time: takes of one stretch come out as videos of their own, each the
        # same length, to lay over one another in an editor. Asked apart, every shot is a video of its own.
        rows = [[s] for s in sorted(shots, key=lambda s: s["start"])] if raw.get("apart") else passes(shots)
        segments = {}
        if raw.get("overlay"):
            if not shutil.which("ffmpeg"):
                raise ValueError("A data overlay needs ffmpeg: install it, then try again")
            # Read before the first render: touching the film between renders can thin the next one's sound (TAIL).
            segments = {s["id"]: self._segment(project, s) for s in shots}
        files = []
        if quality == "draft":  # measured: about real time; the larger sizes have not been timed
            with self.lock:
                self.data["job"]["expect"] = round(sum(s["end"] - s["start"] for s in shots) + 2 * len(rows), 1)
        width, height, fps = RENDERS[quality].split()
        blur = int(fps) if raw.get("blur") and shutil.which("ffmpeg") else None
        scripts = [None] * len(rows)
        if raw.get("trails"):
            if not shutil.which("ffmpeg"):
                raise ValueError("Trails need ffmpeg: install it, then try again")
            rgb = trails.colour(raw.get("trail_colour", "#ffffff"))
            # Worked out before the first render, as the overlay's numbers are: touching the film between renders can
            # thin the next one's sound (TAIL).
            scripts = [self._trails(project, row, int(width), int(height), int(fps), rgb) for row in rows]
        filters = style(int(width), int(height), bool(raw.get("look")), bool(raw.get("haze"))) if shutil.which("ffmpeg") else ""
        for n, row in enumerate(rows, 1):
            self._cut(project, row)
            path = self.media_root / (uuid.uuid4().hex + ".mp4")
            drawn = scripts[n - 1]
            try:
                self._render(row[0]["start"], row[-1]["end"], path, f"{width} {height} {FASTEST}" if blur else RENDERS[quality],
                             f"Rendering {len(row)} shot{'s' * (len(row) > 1)} at {size}" + (" with motion blur" if blur else "")
                             + (f", video {n} of {len(rows)}" if len(rows) > 1 else ""), blur,
                             ",".join(f for f in (trails.filters(drawn, int(height)) if drawn else "", filters) if f))
            finally:
                if drawn:
                    drawn.unlink(missing_ok=True)
            saved = self._keep(path, project, row, raw.get("labels"))
            files.append({"url": "/media/" + path.name, "file": str(saved), "shots": [s["id"] for s in row]})
            record = {"file": str(saved), "shots": [s["id"] for s in row]}
            if segments:
                files[-1]["overlays"] = record["overlays"] = self._overlays(saved, [segments[s["id"]] for s in row], quality)
            self._record_render(project["film"], record)
        self._tidy()
        with self.lock:
            self.data["preview"] = {"url": files[0]["url"], "kind": "video", "time": shots[0]["start"],
                                    "revision": project["revision"], "film": project["film"], "file": files[0]["file"],
                                    "files": files}
