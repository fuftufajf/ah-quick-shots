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
import threading
import time
import traceback
import uuid

import finishing
from finishing import FASTEST, PASSES, Cancelled, assemble, blend, peaks, style, trim
from flight_data import derive, pair, read_kept
import overlay
import trails
from shot_plan import (RIDES, compile_shot, filmer_path, ground_moments, nearby, passes, probe_cameras, stamp, track_rows, validate_effects,
                       validate_plan, validate_shot, videos as video_rows)
from viewer_socket import Viewer, film_rows, number, plane_rows, safe_text

ACTIONS = ("connect", "open", "rename", "seek", "preview", "clip", "live", "around", "airfield", "flight", "icons", "suggest", "export_cut")
RENDERS = {"draft": "960 540", "final": "1920 1080", "4k": "3840 2160"}
# Frames a second of a finished video: 30 unless 60 is asked for (the operator's choice: more like film, and with
# fewer frames for ffmpeg and the trails to work on).
RATES = (30, 60)


def rate(raw):
    return 60 if raw.get("fps") == 60 else 30
# The viewer renders in about real time whatever the size (measured: 5 s of film in 5 s at 480x270 and at 960x540).
CLIP = "960 540 30"
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
# The points a second a moving camera is given. The viewer carries the camera's turn straight from one point to the
# next: with a point every half second the picture's pace was the same for half a second and changed at once at each
# point (measured in a finished video, frame by frame: 5.5, then 7.0, then 9.1 px a frame), so the frame caught up
# with the aircraft in steps. Ten a second make the steps a fifth as large and three frames apart. Telling the viewer
# 46 camera lines took under 0.2 s.
POINTS = 10
CAMERAS = 12  # raise when a camera's maths or the way a clip is rendered change: a kept clip made the old way must not pass for a new one


def to_recycle_bin(paths):
    """Move files to the Windows Recycle Bin, from where a deletion on the page can be undone."""
    import ctypes
    from ctypes import wintypes

    class Operation(ctypes.Structure):  # SHFILEOPSTRUCTW
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p), ("pTo", ctypes.c_void_p),
                    ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", ctypes.c_void_p),
                    ("lpszProgressTitle", ctypes.c_void_p)]
    paths = [Path(p).resolve() for p in paths]
    for path in paths:
        # What lies on a drive with no Recycle Bin (a network share, a USB stick) Windows deletes for good, and with
        # the flags below without asking: the page promises a deletion that can be undone, so such a file stays.
        # shortcut: a fixed drive whose bin is switched off or too small is not caught, ask SHQueryRecycleBinW if it matters
        if ctypes.windll.kernel32.GetDriveTypeW(path.anchor) != 3:  # DRIVE_FIXED
            raise OSError(f"{path.anchor} has no Recycle Bin: delete {path.name} in its folder yourself")
    names = ctypes.create_unicode_buffer("".join(str(p) + "\0" for p in paths) + "\0")
    # FO_DELETE with FOF_ALLOWUNDO (to the bin), no dialog, no confirmation, no error box
    operation = Operation(None, 3, ctypes.cast(names, ctypes.c_void_p), None, 0x40 | 0x4 | 0x10 | 0x400, False, None, None)
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation)) or operation.fAnyOperationsAborted:
        raise OSError("Windows did not move the files to the Recycle Bin")


class Conflict(ValueError):
    pass


def camera_key(shot):
    """What a shot films. Its effects are not in it: changing them re-renders no preview and changes no video's camera."""
    return json.dumps({k: v for k, v in shot.items() if k not in ("id", "name", "effects")}, sort_keys=True)


MUSIC = "mp3|wav|m4a|aac|flac|ogg"  # what the assembly takes as music


def versions(project, renders):
    """{shot id: the finished videos of that shot alone, newest first}: what the assembly lays in a row. A video of
    several shots is not among them. `changed`: the shot's camera or stretch is no longer what the video shows; a
    video recorded before the desk kept that is taken as current."""
    now = {s["id"]: (camera_key(s), s["end"] - s["start"]) for s in (project or {}).get("shots", [])}
    found = {}
    for row in renders:
        ident = row["shots"][0]
        if len(row["shots"]) == 1 and ident in now and "id" in row and row["file"].lower().endswith(".mp4"):
            key, length = now[ident]
            found.setdefault(ident, []).insert(0, {
                "id": row["id"], "file": row["file"], "made": row.get("made"), "quality": row.get("quality"), "fps": row.get("fps"),
                "extras": row.get("extras", []), "seconds": row.get("seconds", round(length, 3)),
                "changed": row.get("takes", {}).get(ident, key) != key})
    return found


def lead(command, start):
    """The shot's first camera, standing from `start` to the shot's start, for drawing the aircraft before it
    (see LEAD): a moving camera holds its first place. The pilot's view and the weapon ride need none."""
    _cam, at, _duration, rest = command.split(" ", 3)
    if float(at) - start < .05 or rest.startswith(("recorded", "weapon")):
        return []
    if rest.startswith("fixed ") and " to " in rest:
        rest = rest.split(" to ", 1)[0]
    return [f"cam {stamp(start)} {float(at) - float(stamp(start)):.8f} {rest}"]


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
        for left in (*self.media_root.glob("*.bmp"), *self.media_root.glob("*.ass")):
            self._drop(left)  # stills and trail scripts of an earlier run: no page will ask for them again
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
        self.halt = threading.Event()  # the page asks the playing in Film Viewer's window to stop where it is
        self.waves = {}  # (file, its time) -> its waveform, see wave
        self.films_folder = None  # where the viewer keeps its films, as its `films` reply says
        kept = lambda name: json.loads((self.root / name).read_text(encoding="utf-8")) if (self.root / name).exists() else []
        # `recent`: the replays opened last, newest first; `waiting`: those to go to the Recycle Bin once the viewer,
        # which holds the open one, has been given another.
        self.data = {"connected": False, "films": [], "project": None, "planes": [], "recent": kept("recent.json"), "waiting": kept("waiting.json"),
                     "now": 0, "icons": True, "problems": {}, "nearby": None, "airfield": None, "flight": None,
                     "preview": None, "proposals": [], "job": None, "videos": str(self.videos), "renders": [],
                     "ffmpeg": bool(shutil.which("ffmpeg")), "overlays": None, "assembly": None, "assembling": None,
                     "pace": json.loads((self.root / "pace.json").read_text(encoding="utf-8")) if (self.root / "pace.json").exists() else {}}

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
        target = self._free(title)
        shutil.copyfile(path, target)
        return target

    def _free(self, title):
        """A file in the videos folder under this title that is not there yet: the same thing made again is kept
        beside the first."""
        title = re.sub(r":\s*", " ", title)  # "Kill: Somebody" reads better as "Kill Somebody" than "Kill- Somebody"
        title = re.sub(r'[\\/*?"<>|\x00-\x1f]+', "-", title).strip(" .")[:120] or "video"
        self.videos.mkdir(parents=True, exist_ok=True)
        target, n = self.videos / (title + ".mp4"), 2
        while target.exists():
            target, n = self.videos / f"{title} ({n}).mp4", n + 1
        return target

    def snapshot(self):
        with self.lock:
            data = copy.deepcopy(self.data)
        if data["job"] and data["job"]["running"]:
            data["job"]["elapsed"] = round(time.monotonic() - data["job"]["clock"], 1)
        data["versions"] = versions(data["project"], data["renders"])
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

    def _file(self, film, kind=""):
        """Where the desk keeps a film's plan, or with ".renders" the record of its finished videos."""
        return self.root / (hashlib.sha256(film.encode()).hexdigest()[:20] + kind + ".json")

    @staticmethod
    def _write(path, value):
        """JSON written whole or not at all: beside the file first, then put in its place."""
        temp = path.with_suffix(".new")
        with temp.open("w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)

    def _persist(self, project):
        self._write(self._file(project["film"]), project)

    def _load_renders(self, film):
        """The finished videos of a film that are still on disk, with the shots in each and their overlays."""
        path = self._file(film, ".renders")
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        if any("id" not in row for row in rows):  # recorded before a video had an id: given one now, and kept
            for row in rows:
                row.setdefault("id", uuid.uuid4().hex)
            self._write(path, rows)
        return [row for row in rows if Path(row["file"]).exists() or any(Path(o).exists() for o in row.get("overlays", []))]

    def _record_render(self, film, row):
        with self.lock:
            renders = self.data["renders"] + [row]
            self._write(self._file(film, ".renders"), renders)
            self.data["renders"] = renders

    def _paced(self, quality, ratio):
        """This PC against the one the page's estimates were measured on. A render that took `ratio` times its
        estimate moves that quality's pace halfway there, and the page multiplies its next estimates by it: after a
        few renders they are this PC's own.
        shortcut: one number a quality, not one an effect or a frame rate; time them apart if the estimates stay off"""
        with self.lock:
            pace = {**self.data["pace"], quality: round(self.data["pace"].get(quality, 1) * (1 + (min(4, max(.25, ratio)) - 1) / 2), 3)}
            self._write(self.root / "pace.json", pace)
            self.data["pace"] = pace

    def _log(self):
        """The error being handled, with where it happened, added to errors.log beside the saved shots: the desk has
        no window to show it in, and this is the file to send when something goes wrong.
        shortcut: the file only grows, by a few lines an error; cut it down if a desk ever fails in a loop"""
        try:
            with (self.root / "errors.log").open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S\n") + traceback.format_exc() + "\n")
        except OSError:
            pass

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
            gone, kept = [], []
            for row in self.data["renders"]:
                (gone if raw.get("files") is True and set(row["shots"]) & set(ids) else kept).append(row)
            paths = [Path(f) for row in gone for f in [row["file"], *row.get("overlays", [])]]
            paths = [f for f in paths if f.suffix.lower() in (".mp4", ".mov") and f.exists()]
            if paths:  # first: a file Windows will not let go (open in an editor) leaves the plan as it was
                to_recycle_bin(paths)
            self._persist(project)
            self.data["project"] = project
            if gone:
                self._write(self._file(project["film"], ".renders"), kept)
                self.data["renders"] = kept
            return {"project": copy.deepcopy(project), "renders": copy.deepcopy(self.data["renders"]), "recycled": len(paths)}

    def _note(self, name, paths):
        """The recent or the waiting replays, kept with the desk."""
        with self.lock:
            self._write(self.root / (name + ".json"), paths)
            self.data[name] = paths

    def _replay(self, film):
        """A replay of the viewer's list and its file in the films folder: nothing else is deleted or renamed."""
        row = next((f for f in self.data["films"] if f["path"] == film), None)
        if row is None or self.films_folder is None:
            raise ValueError("Choose a replay from the list")
        folder = self.films_folder.resolve()
        path = (folder / row["name"]).resolve()
        if path.parent != folder or path.suffix.lower() != ".ahf":
            raise ValueError("Only a replay in the films folder can be deleted or renamed")
        return row, path

    def delete_film(self, raw):
        """Move a replay from the viewer's films folder to the Recycle Bin. Film Viewer holds the open one (Windows
        refuses to delete or rename it: checked), so that one is marked instead and goes when another replay is
        opened (_let_go); asked again, the mark is taken off. Its saved shots stay with the desk and its finished
        videos in the videos folder."""
        with self.lock:
            self._idle()
            row, path = self._replay(raw.get("film"))
            waiting = [f for f in self.data["waiting"] if f != row["path"]]
            if self.data["project"] and self.data["project"]["film"] == row["path"]:
                self._note("waiting", waiting if row["path"] in self.data["waiting"] else [*waiting, row["path"]])
            else:
                if path.exists():
                    to_recycle_bin([path])
                self.data["films"] = [f for f in self.data["films"] if f["path"] != row["path"]]
                self._note("waiting", waiting)
            return {"films": copy.deepcopy(self.data["films"]), "waiting": list(self.data["waiting"])}

    def _let_go(self, film):
        """The viewer now holds `film`: it is the newest of the recent ones, and the replays marked while the viewer
        held them go to the Recycle Bin. One that Windows still keeps stays marked for the next time."""
        with self.lock:
            left = []
            for marked in self.data["waiting"]:
                try:
                    row, path = self._replay(marked)
                    if marked == film:
                        left.append(marked)
                        continue
                    if path.exists():
                        to_recycle_bin([path])
                    self.data["films"] = [f for f in self.data["films"] if f["path"] != marked]
                except ValueError:
                    pass  # no longer in the films folder: there is nothing to delete
                except OSError:
                    left.append(marked)
            if left != self.data["waiting"]:
                self._note("waiting", left)
            self._note("recent", [film, *(f for f in self.data["recent"] if f != film)][:8])

    def _library(self):
        listing = self.command("films")
        self.films_folder = next((Path(line[7:]) for line in listing if line.startswith("folder ")), None)
        with self.lock:
            self.data["films"] = film_rows(listing, os.path.getmtime)

    def _rename(self, raw):
        """A replay under another name; its saved shots, the record of its videos and its assembly go with it.
        Film Viewer holds the open one and has no command to let go of a film, only to open another: so it is given
        another replay to hold, and this one again under its new name."""
        film = raw.get("film")
        row, path = self._replay(film)
        name = raw["name"].strip().removesuffix(".ahf").strip(" .") if isinstance(raw.get("name"), str) else ""
        if not name or len(name) > 100 or re.search(r'[\\/:*?"<>|\x00-\x1f]', name):
            raise ValueError('A replay\'s name is 1 to 100 characters, without \\ / : * ? " < > |')
        target, new = path.with_name(name + ".ahf"), f"films/{name}.ahf"
        if target.exists() and not target.samefile(path):
            raise ValueError("There is a replay of that name already")
        held = bool(self.data["project"]) and self.data["project"]["film"] == film
        if held:
            other = next((f["path"] for f in self.data["films"] if f["path"] != film and f["path"] not in self.data["waiting"]), None)
            if other is None:
                raise ValueError("Film Viewer lets go of the open replay only for another one, and there is no other in the films folder")
            self.progress("Renaming the replay: Film Viewer holds another one meanwhile")
            self.command("open " + other)
        try:
            path.rename(target)
        except OSError:
            if held:
                self._open(film)
            raise ValueError("Windows did not let the replay be renamed") from None
        for kind in ("", ".renders", ".assembly"):
            if self._file(film, kind).exists():
                os.replace(self._file(film, kind), self._file(new, kind))
        for kept in ("recent", "waiting"):
            self._note(kept, [new if f == film else f for f in self.data[kept]])
        self._library()
        if held:
            self._open(new)

    def video(self, ident):
        """The file of a finished video on record, by its id: the page plays the assembly's clips from it."""
        with self.lock:
            row = next((r for r in self.data["renders"] if r.get("id") == ident), None)
        return Path(row["file"]) if row else None

    def wave(self, path):
        """The waveform of a finished video's or a piece of music's sound (finishing.peaks), worked out once a file and
        kept while the desk runs: 50 bytes a second of sound."""
        key = (str(path), path.stat().st_mtime_ns)
        with self.lock:
            found = self.waves.get(key)
        if found is None:
            found = bytes(peaks(path))
            with self.lock:
                self.waves[key] = found
        return list(found)

    def keep_music(self, name, body):
        """Music for the assembly, kept with the desk under a name made of its content: {file, name}.
        shortcut: music is never removed from .quickshots/music, clear it by hand if it ever matters"""
        suffix = Path(name).suffix.lower()
        if not re.fullmatch(rf"\.({MUSIC})", suffix) or not body:
            raise ValueError("Music is an mp3, wav, m4a, aac, flac or ogg file")
        folder = self.root / "music"
        folder.mkdir(exist_ok=True)
        file = hashlib.sha256(body).hexdigest()[:32] + suffix
        (folder / file).write_bytes(body)
        return {"file": file, "name": Path(name).name[:200]}

    def _assembly(self, raw):
        """The assembly the page sends, checked: {clips, off, music, sound}. A clip is a finished video of one shot
        (`video`: its id, None for the newest) less `start` seconds at its beginning and `end` at its end; `off` are
        the shots kept off the timeline; `sound` is the film's loudness, `music` a file kept by keep_music."""
        project, raw = self.data["project"], raw.get("assembly")
        if project is None:
            raise ValueError("Open a film first")
        if not isinstance(raw, dict) or not isinstance(raw.get("clips"), list) or len(raw["clips"]) > 500:
            raise ValueError("An assembly holds up to 500 clips")
        found, clips = versions(project, self.data["renders"]), []
        for clip in raw["clips"]:
            if not isinstance(clip, dict) or not isinstance(clip.get("shot"), str) or clip["shot"] not in found:
                raise ValueError("A clip is a rendered shot of this film")
            mine = {video["id"] for video in found[clip["shot"]]}
            clips.append({"shot": clip["shot"], "video": clip.get("video") if clip.get("video") in mine else None,
                          "start": number(clip.get("start", 0), 0, 120, "start"), "end": number(clip.get("end", 0), 0, 120, "end"),
                          "join": "dissolve" if clip.get("join") == "dissolve" else "cut",
                          "sound": number(clip.get("sound", 1), 0, 2, "clip sound")})
        music = raw.get("music")
        if music is not None:
            if (not isinstance(music, dict) or not re.fullmatch(rf"[a-f0-9]{{32}}\.({MUSIC})", str(music.get("file")))
                    or not (self.root / "music" / music["file"]).is_file()):
                raise ValueError("Choose the music again")
            music = {"file": music["file"], "name": safe_text(music.get("name", "music")),
                     "from": number(music.get("from", 0), 0, 36000, "music start"), "level": number(music.get("level", .45), 0, 2, "music level"),
                     "in": number(music.get("in", 1), 0, 30, "fade in"), "out": number(music.get("out", 3), 0, 30, "fade out")}
        off = raw.get("off") if isinstance(raw.get("off"), list) else []
        return {"clips": clips, "off": [ident for ident in off if isinstance(ident, str)][:500], "music": music,
                "sound": number(raw.get("sound", 1), 0, 2, "film sound")}

    def save_assembly(self, raw):
        with self.lock:
            assembly = self._assembly(raw)
            self._write(self._file(self.data["project"]["film"], ".assembly"), assembly)
            self.data["assembly"] = assembly
            return {"assembly": copy.deepcopy(assembly)}

    def assemble(self, raw):
        """The assembly as one video in the videos folder. ffmpeg makes it while the desk goes on: the viewer has no
        part in it."""
        with self.lock:
            if self.data["assembling"] and self.data["assembling"]["running"]:
                raise Conflict("The last assembly is still being made")
            if not shutil.which("ffmpeg"):
                raise ValueError("An assembly needs ffmpeg: install it, then try again")
            assembly, project = self.save_assembly(raw)["assembly"], self.data["project"]
            if not assembly["clips"]:
                raise ValueError("Put at least one clip on the timeline")
            found, clips = versions(project, self.data["renders"]), []
            for clip in assembly["clips"]:
                video = next((v for v in found[clip["shot"]] if v["id"] == clip["video"]), found[clip["shot"]][0])
                seconds = video["seconds"] - clip["start"] - clip["end"]
                if seconds < .2:
                    raise ValueError("A clip is trimmed down to nothing: leave it a fifth of a second at least")
                clips.append({"file": video["file"], "start": clip["start"], "seconds": seconds, "join": clip["join"],
                              "sound": clip["sound"] * assembly["sound"]})
            music = assembly["music"] and {**assembly["music"], "file": self.root / "music" / assembly["music"]["file"]}
            quality = raw.get("quality") if raw.get("quality") in RENDERS else "final"
            target = self._free(f"{Path(project['film']).stem} assembly")
            self.data["assembling"] = {"running": True, "message": f"Assembling {len(clips)} clip{'s' * (len(clips) > 1)} with ffmpeg",
                                       "error": None, "file": None}
            threading.Thread(target=self._assemble, args=(clips, music, target, quality, rate(raw)), daemon=True).start()
            return copy.deepcopy(self.data["assembling"])

    def _assemble(self, clips, music, target, quality, fps):
        try:
            assemble(clips, music, target, *map(int, RENDERS[quality].split()), fps)
            done = {"running": False, "message": "Assembly ready", "error": None, "file": str(target)}
        except Exception as error:  # the desk goes on whatever ffmpeg made of it
            self._log()
            done = {"running": False, "message": str(error), "error": str(error), "file": None}
        with self.lock:
            self.data["assembling"] = done

    def save(self, raw):
        with self.lock:
            self._idle()
            old = self._revision(raw)
            project = {**old, "shots": validate_plan(raw.get("shots"), old["length"]),
                       "revision": old["revision"] + 1}
            self._persist(project)
            self.data["project"] = project
            return copy.deepcopy(project)

    def progress(self, message, share=None):
        """What the job is doing now, and how much of that step is done (0 to 1) when it is known."""
        with self.lock:
            self.data["job"].update(message=message, share=share)

    def _stage(self, message):
        """The next step of a job that counts its steps (a render: `steps` says how many there are)."""
        with self.lock:
            self.data["job"]["step"] += 1
        self.progress(message)

    def _encode(self, message, seconds, work):
        """A step done by ffmpeg, followed as it goes and stopped at once by Cancel: `work` makes `seconds` of video."""
        self._go_on()
        self._stage(message)
        finishing.local.watch, finishing.local.halt = (max(.1, seconds), lambda share: self.progress(message, share)), self.halt
        try:
            work()
        finally:
            finishing.local.watch = finishing.local.halt = None

    def stop(self):
        """The page's Pause, Stop or Cancel (POST /api/halt): a stretch playing in the viewer's window stops where it
        is; a render stops at once in ffmpeg, and after the pass the viewer is rendering, which the socket cannot stop."""
        self.halt.set()
        with self.lock:
            if self.data["job"] and self.data["job"]["running"] and self.data["job"]["action"] in ("export_cut", "clip"):
                self.data["job"]["cancelling"] = True

    def _go_on(self):
        if self.halt.is_set():
            raise Cancelled("Cancelled")

    def command(self, command):
        return self.viewer.command(command, self.progress)

    def _render(self, start, end, path, size, label="", blur=None, filters=""):
        """One video of a stretch, with the film's sound whole (see TAIL) and the aircraft solid from its first frame
        when ffmpeg is there to cut off the lead-in (see LEAD). `blur`: the frame rate to blend the passes of a `size`
        at FASTEST down to (see PASSES). `filters`: the looks to give it (see style). Cancel (see stop) is heard before
        each pass of the viewer and stops ffmpeg at once; what was rendered of the video is thrown away."""
        early = min(LEAD, start) if shutil.which("ffmpeg") else 0
        early = early if early >= .05 else 0
        with self.lock:  # the wait is part of what this clip takes
            if self.data["job"]["expect"]:
                self.data["job"]["expect"] = round(self.data["job"]["expect"] + max(0, self.quiet - time.monotonic()), 1)
        while time.monotonic() < self.quiet:
            self._go_on()
            self.progress(f"Film Viewer is finishing the last clip's sound: {math.ceil(self.quiet - time.monotonic())} s, so this one keeps its own")
            time.sleep(.25)
        self._go_on()
        if abs(self.data["now"] - (start - early)) > .5:
            # A render that began far from where the film stood came out with its first second silent, just after
            # opening a film (-49 dB, 5 of 5). A seek there and 2 s paused kept it whole (2 of 2); a seek alone, or
            # with a still drawn, did not. Paused, not playing: playing before a render thins its sound (see LEAD).
            self._seek(start - early)
            time.sleep(2 * self.pace)
        self._stage(label or f"Rendering {end - start:.1f} s of film")  # after the seek, whose message it replaces
        whole = path.with_name(path.stem + "-lead" + path.suffix) if early or blur or filters else path
        parts = [whole.with_name(f"{whole.stem}{n}{whole.suffix}") for n in range(PASSES)] if blur else [whole]
        # The viewer answers a render only when it is done (measured: 1.1 to 1.7 times the stretch at 1080p, 4K not
        # timed), so it may take twenty times the stretch before the desk gives it up.
        self.viewer.patience = max(Viewer.patience, 20 * (end - start + early))
        try:
            for n, part in enumerate(parts):  # only the first pass's sound is kept: the others need not wait for quiet
                if n:
                    self._go_on()
                    self._stage(f"{label}: motion blur pass {n + 1} of {len(parts)}")
                shift = n / FASTEST / len(parts)
                self.command(f"video {start - early + shift:.8f} {end + shift:.8f} {part.as_posix()} {size}")
                self.quiet = time.monotonic() + (end - start + early + TAIL) * self.pace
            self._go_on()
        except Cancelled:
            for part in parts:
                self._drop(part)
            raise
        finally:
            self.viewer.patience = Viewer.patience
        if blur:
            self._encode("Blending the motion blur with ffmpeg" + (", with its effects" if filters else ""), end - start,
                         lambda: blend(parts, path, early, blur, filters))
        elif early or filters:
            self._encode("Laying the effects on with ffmpeg" if filters else "Cutting off the lead-in with ffmpeg", end - start,
                         lambda: trim(whole, path, early, filters))

    def start(self, raw):
        action = raw.get("action")
        if action not in ACTIONS:
            raise ValueError("Unknown action")
        with self.lock:
            self._idle()
            if action != "connect" and not self.data["connected"]:
                raise ValueError("Connect to Film Viewer first")
            if action not in ("connect", "open", "rename"):
                self._revision(raw)
            elif action != "connect" and self.data["project"]:
                self._revision(raw)
            self.halt.clear()  # a Pause or Cancel meant for the job before
            # `video` of `videos`: which video of a render is being made; `cancelling`: Cancel was pressed
            self.data["job"] = {"id": uuid.uuid4().hex, "running": True, "action": action,
                                "message": "Connecting to Film Viewer" if action == "connect" else "Preparing " + action,
                                "clock": time.monotonic(), "elapsed": 0, "expect": None, "error": None,
                                "step": 0, "steps": None, "share": None, "video": None, "videos": None, "cancelling": False}
            job = copy.deepcopy(self.data["job"])
            self.worker = threading.Thread(target=self._work, args=(copy.deepcopy(raw),), daemon=True)
            self.worker.start()
            return job

    def _work(self, raw):
        try:
            self._action(raw)
            self.progress("Ready")
        except Exception as error:
            if not isinstance(error, Cancelled):  # asked for: nothing went wrong
                self._log()
            with self.lock:
                self.data["job"]["error"] = str(error)
                self.data["job"]["message"] = str(error)
                if isinstance(error, ConnectionError):  # the socket's own errors only: a full disk is not a lost viewer
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
            self.data.update(project=None, preview=None, planes=[], problems={}, nearby=None, airfield=None, flight=None, assembly=None)
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
                try:
                    fields = shlex.split(line)
                    events.append({"id": str(len(events)), "time": float(fields[1]), "kind": fields[2],
                                   "text": " ".join(fields[3:]), "fields": fields[3:]})
                except (ValueError, IndexError):
                    pass  # a line that cannot be read is one moment less, not a film that will not open
        events = self._filmable(events, length)
        path = self._file(film)
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
            assembly = self._file(film, ".assembly")
            self.data.update(project=project, now=1, preview=None, proposals=[], renders=self._load_renders(film),
                             assembly=json.loads(assembly.read_text(encoding="utf-8")) if assembly.exists() else None)
        self._let_go(film)
        self._seek(min(1, length))
        self._settle()
        self._frame(project["revision"])

    def _filmable(self, events, length):
        """The moments that can be filmed. A film tells of kills and bombs all over the arena, but holds only the
        aircraft near its recorder: a moment whose own aircraft is not in the film then (a kill's killer, a bomb's
        bomber) could only answer "not in the film during this shot", so it is left out. One whose other aircraft
        is missing (the victim) stays, marked `alone`: a shot of it opens on the one aircraft there is. Who is in
        the film comes from every aircraft's path a second apart (about 5 s for a 9 minute film); with no paths
        read at all, nothing is left out."""
        seen = {}  # aircraft id: the seconds at which it is in the film
        for start in range(0, math.ceil(length), 600):
            self.progress(f"Finding who is in the film: {start / length:.0%}")
            for t, row in track_rows(self.command(f"tracks {start:.2f} {min(length, start + 600):.2f} 1")).items():
                for ident in row:
                    seen.setdefault(ident, []).append(t)
        if not seen:
            return events
        def there(value, t):
            return value.isdigit() and any(abs(s - t) <= 2 for s in seen.get(int(value), ()))
        kept = []
        for event in events:
            fields = event["fields"]
            lead, other = {"kill": (3, 4), "impact": (0, 1), "hit": (0, None), "drop": (0, None)}.get(event["kind"], (None, None))
            if lead is not None and len(fields) > lead and not there(fields[lead], event["time"]):
                continue
            if other is not None and len(fields) > other and fields[other].isdigit() and not there(fields[other], event["time"]):
                event = {**event, "alone": True}
            kept.append(event)
        return kept

    def _tracks(self, start, end, step=None):
        # Choose an exact divisor so the final snapshot lands at the end.
        step = step or (end - start) / max(1, math.ceil((end - start) * POINTS))
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
            # Each aircraft where it is at the moment asked for, between the snapshots either side of it. The nearer
            # snapshot alone is up to 0.01 s off, 3 ft of a 300 ft/s flight and different at every point (a shot of
            # 22.3 s has its points 0.4956 s apart, the snapshots 0.02 s): the aircraft slid a yard back and forth
            # in the picture of a camera flying with it.
            for k in range(round((end - start) / step) + 1) if grid else ():
                t = start + k * step
                at = bisect.bisect_left(grid, t)
                a, b = grid[max(0, at - 1)], grid[min(len(grid) - 1, at)]
                near = a if t - a <= b - t else b
                if abs(near - t) <= FINE / 2 + 1e-6:
                    share = (t - a) / (b - a) if b > a else 0
                    picked[t] = {ident: (*(p + (q - p) * share for p, q in zip(fine[a][ident][:3], fine[b][ident][:3])), *row[3:])
                                 if ident in fine[a] and ident in fine[b] else row for ident, row in fine[near].items()}
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

    def _read_flight(self, project, start, end, step=.02, numbers=True):
        """Every aircraft's flight in a stretch, kept as the last one read: {key, rows, data}. Without `numbers` only
        the tracks, names and sizes are read: the trails need no speeds or G, and working them out for every aircraft
        of a long stretch took seconds and memory for nothing."""
        key = (project["film"], round(start, 3), round(end, 3), round(step, 3), numbers)
        if not self.flight or self.flight["key"] != key:
            self.progress("Reading every aircraft's flight")
            reply = self.command(f"tracks {max(0, start - .1):.8f} {end:.8f} {step:.10f} keep speed on pos double")
            found = next((m[1] for line in reply if (m := re.search(r" file (.+\.ahftracks)$", line))), None)
            if not found:
                raise ValueError("Film Viewer did not say where it kept the tracks")
            kept = read_kept(found)
            kept["tracks"] = {ident: [row for row in rows if start - 1e-6 <= row[0] <= end + 1e-6] for ident, rows in kept["tracks"].items()}
            aircraft = {str(ident): {"id": ident, **kept["aircraft"].get(ident, {"name": "", "radius": 0}),
                                     **(derive(rows, kept["step"]) if numbers else {})}
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

    def _overlays(self, video, segments, quality, fps):
        """Queue the data overlays of a finished video, to be drawn beside it while the desk goes on: one for a 16:9
        timeline at the video's size, one for a 9:16 reel, at the video's frame rate and length. Drawing them in the
        render's own job held the viewer for 80 s after a 37 s render at 4K (measured), and the next camera waited."""
        height = int(RENDERS[quality].split()[1])
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

    def _camera(self, shot):
        """A shot's `cam` commands, compiled once: an unchanged shot never rereads its tracks."""
        key = camera_key(shot)
        if key not in self.compiled:
            tracks = {} if shot["kind"] in RIDES else self._tracks(shot["start"], shot["end"])
            self.compiled[key] = compile_shot(shot, tracks)
            while len(self.compiled) > 300:  # angles tried and dropped
                del self.compiled[next(iter(self.compiled))]
        return self.compiled[key]

    def _apply(self, project, raw=None, only=None):
        """Send the viewer its cameras: the plan (see _plan), or `only` these shots for one video of a cut."""
        shots, token, draft = self._plan(project, raw or {})
        if only is not None:
            shots, token = only, ("cut", project["revision"], *(s["id"] for s in only))
        if self.applied != token:
            commands, problems, drawn = [], {}, 0.0
            for shot in shots:
                try:
                    lines = self._camera(shot)
                except ValueError as error:
                    # One shot that cannot be built must not stop the picture for the rest.
                    problems[shot["id"]] = str(error)
                    continue
                commands.extend(lead(lines[0], max(drawn, shot["start"] - LEAD)) + lines)
                drawn = max(drawn, shot["end"])
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

    def _trails(self, project, row, width, height, fps, colours, style, tail=None):
        """An ASS script of the wingtip trails over one video of a cut (see trails.py), in the working folder. The
        video runs the row's shots end to end (the gaps between them skipped), from the first one's start."""
        first, last = row[0]["start"], row[-1]["end"]
        length = sum(shot["end"] - shot["start"] for shot in row)
        # shortcut: the script is 1.5 to 3.4 MB a second of video (measured) and ffmpeg has not been tried on one of
        # hundreds of MB, and the tracks are read from the first shot to the last, gaps included; lift once tried live
        if length > 120 or last - first + style["seconds"] > 600:
            raise ValueError("Trails go on one video of up to 120 seconds, its shots within ten minutes of film: "
                             "render these shots as A file per shot")
        flight = self._read_flight(project, max(0.0, first - style["seconds"]), last, numbers=False)
        spans, offset = [], 0.0
        for shot in row:
            spans.append((offset, shot, self._camera(shot)))
            offset += shot["end"] - shot["start"]
        frames = []
        self._stage("Finding the camera for the trails")
        for k in range(round(offset * fps) + 1):
            if k % fps == 0:
                self._go_on()
                self.progress(f"Finding the camera for the trails: {k // fps} of {math.ceil(offset)} s", k / fps / max(.1, offset))
            begin, shot, lines = next(span for span in reversed(spans) if span[0] <= k / fps + 1e-9)
            t = min(shot["end"], shot["start"] + k / fps - begin)
            frames.append((t, self._eye(lines, t, shot)))
        radii = {int(ident): plane["radius"] for ident, plane in flight["data"]["aircraft"].items()}
        path = self.media_root / (uuid.uuid4().hex + ".ass")
        with path.open("w", encoding="utf-8") as script:  # line by line: the whole of it need not be held in memory
            script.writelines(line + "\n" for line in trails.lines(frames, flight["rows"], radii, colours, flight["data"]["step"],
                                                                 fps, width, height, style, tail))
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
            # Whom the viewer holds in the middle can change within a shot (an aircraft that leaves the film lets go).
            piece = next((p for p in reversed(pieces) if float(p[1]) <= t + 1e-9), pieces[0])
            held = re.search(r" at (\S+)", piece[3])
            camera = "fixed " + " ".join(f"{v:.8f}" for v in trails.between(points, t)) + (f" at {held[1]}" if held else "")
        else:
            camera = re.sub(r" (outside|curve)\b", "", rest).split(" name ")[0]
        # In the working folder: its path has no spaces, which the viewer promises to take only for a few commands.
        camfile, found = self.media_root / "trailcam.txt", self.media_root / "trailcam-eye.txt"
        camfile.write_text(camera + "\n", encoding="ascii")
        # Ending at t keeps it inside the kept tracks (beyond them the viewer would read the film); its last step is t.
        # A shot at the film's very start has less than .15 s before it, or nothing: then one step, at t.
        begin = max(0.0, t - .15)
        begin = begin if t - begin >= .01 else t
        self.command(f"project {begin:.8f} {t:.8f} {t - begin or .15:.8f} {camfile.as_posix()} {found.as_posix()} ids {shot['hero']}")
        eyes = [row.split() for row in found.read_text().splitlines() if row.startswith("eye ")]
        return [float(v) for v in min(eyes, key=lambda row: abs(float(row[1]) - t))[3:10]] if eyes else None

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
        spans = [(command, *map(float, command.split()[1:3])) for command in self._camera(draft)]
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
        if raw.get("bare") and not row:  # to be played in the viewer's own window: its cameras and a still, no render
            self._apply(project, raw)
            self._preroll(number(raw.get("still", start), 0, length, "time"), rough=True)
            return self._frame(project["revision"])
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
        # From the playhead when the page says where it is (`from`): a pause is then gone on from, not begun again.
        begin = start if raw.get("from") is None else min(max(start, number(raw["from"], 0, project["length"], "from")), end - .3)
        self._seek(begin)
        self.progress(f"Playing {end - begin:.1f} s in Film Viewer's window")
        self.command("play")
        deadline, now = time.monotonic() + 2 * (end - begin) + 5, begin
        try:
            # Between two waits of six frames the page can stop it (Pause, Stop: POST /api/halt), and sees where it is.
            while time.monotonic() < deadline and now < end - .05 and not self.halt.is_set():
                try:
                    now = float(self.command("wait 6")[0].rsplit(" ", 1)[1])  # "waited, time <film seconds>"
                except (IndexError, ValueError):
                    break
                with self.lock:
                    self.data["now"] = min(now, project["length"])
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
        # In the working folder: its path has no spaces, which the viewer promises to take only for a few commands.
        camera_file, output = self.media_root / "candidates.txt", self.media_root / "projection.txt"
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

    def _export(self, raw, project):
        """The chosen saved shots as videos in the videos folder, each with the effects its shot keeps (see
        validate_effects): a data overlay, motion blur, the film look and its haze, trails. A shot saved before shots
        kept their effects has those the page sends as `effects`. Shots of other effects are videos of their own."""
        ids = raw.get("shot_ids")
        if ids is not None and not isinstance(ids, list):
            raise ValueError("shot_ids must be a list")
        fallback = validate_effects(raw.get("effects") or {})
        shots = [{**s, "effects": s.get("effects", fallback)} for s in project["shots"] if ids is None or s["id"] in ids]
        if not shots:
            raise ValueError("Select at least one saved shot")
        if " " in str(self.media_root):
            raise ValueError("Film Viewer video paths cannot contain spaces: move AH Quick Shots to a folder without spaces in its path")
        ffmpeg = bool(shutil.which("ffmpeg"))
        for name, what in (("overlay", "A data overlay needs"), ("trails", "Trails need")):
            if not ffmpeg and any(s["effects"].get(name) for s in shots):
                raise ValueError(f"{what} ffmpeg: install it, then try again")
        quality = raw.get("quality") if raw.get("quality") in RENDERS else "final"
        (width, height), fps = map(int, RENDERS[quality].split()), rate(raw)
        size = f"{width}x{height} @ {fps}"
        # The viewer holds one camera at a time: takes of one stretch come out as videos of their own, each the
        # same length, to lay over one another in an editor. Asked apart, every shot is a video of its own.
        rows = video_rows(sorted(shots, key=lambda s: s["start"]), bool(raw.get("apart")))
        # Read before the first render: touching the film between renders can thin the next one's sound (TAIL).
        segments = {s["id"]: self._segment(project, s) for s in shots if s["effects"].get("overlay")}
        files = []
        # How long it should take: the page says (it shows what each effect costs before the render is asked for:
        # renderCost in desk.js). Without that, only a draft is known: about real time.
        expect = number(raw["expect"], 1, 86400, "expect") if raw.get("expect") is not None else (
            sum(s["end"] - s["start"] for s in shots) + 2 * len(rows) if quality == "draft" else None)
        # What each video is made with, all its shots alike: (motion blur's frame rate or None, the looks' filters,
        # the trails asked for or None).
        made = [(fps if ffmpeg and e.get("blur") else None, style(width, height, bool(e.get("look")), bool(e.get("look"))) if ffmpeg else "",
                 e.get("trails")) for e in (row[0]["effects"] for row in rows)]
        # The steps the page counts through: for each video the viewer's render (each pass of motion blur) and what
        # ffmpeg then does to it, and before them the trails' cameras.
        with self.lock:
            self.data["job"].update(expect=expect and round(expect, 1), video=1, videos=len(rows), steps=sum(
                (PASSES if blur else 1) + (ffmpeg and bool(blur or filters or drawn or row[0]["start"] >= .05)) + bool(drawn)
                for row, (blur, filters, drawn) in zip(rows, made)))
        mark = self.data["job"]["clock"]
        scripts = []
        try:
            # Worked out before the first render, as the overlay's numbers are: touching the film between renders
            # can thin the next one's sound (TAIL).
            for n, (row, (_blur, _filters, asked)) in enumerate(zip(rows, made), 1):
                if not asked:
                    scripts.append(None)
                    continue
                with self.lock:
                    self.data["job"]["video"] = n
                drawing = trails.style_for(asked["seconds"])
                if asked["exact"]:  # the flight path as it was flown, to be read: nothing averaged
                    drawing["smooth"] = 0
                scripts.append(self._trails(project, row, width, height, fps, trails.paint(row, trails.colour(asked["lead"]), trails.colour(asked["opponent"])),
                                            drawing, asked["tail"] and trails.colour(asked["tail"])))
            for n, (row, (blur, filters, _asked), script) in enumerate(zip(rows, made, scripts), 1):
                self._go_on()
                with self.lock:
                    self.data["job"]["video"] = n
                self._cut(project, row)
                path = self.media_root / (uuid.uuid4().hex + ".mp4")
                drawn = trails.filters(script, height) if script else ""
                self._render(row[0]["start"], row[-1]["end"], path, f"{width} {height} {FASTEST if blur else fps}",
                             f"Rendering {len(row)} shot{'s' * (len(row) > 1)} at {size}" + (" with motion blur" if blur else "")
                             + (f", video {n} of {len(rows)}" if len(rows) > 1 else ""), blur,
                             ",".join(f for f in (drawn, filters) if f))
                saved = self._keep(path, project, row, raw.get("labels"))
                files.append({"url": "/media/" + path.name, "file": str(saved), "shots": [s["id"] for s in row]})
                # What the assembly needs to know of a video: which it is, how it was made and what cameras it shows.
                e = row[0]["effects"]
                record = {"id": uuid.uuid4().hex, "file": str(saved), "shots": [s["id"] for s in row], "made": round(time.time()),
                          "quality": quality, "fps": fps, "extras": [name for name, on in (("blur", e.get("blur")), ("look", e.get("look")),
                                                                               ("haze", e.get("look")), ("trails", e.get("trails"))) if on],
                          "seconds": round(sum(s["end"] - s["start"] for s in row), 3), "takes": {s["id"]: camera_key(s) for s in row},
                          # How long it took to make, in seconds: the first video's holds what was prepared for them all.
                          "took": round(time.monotonic() - mark)}
                mark = time.monotonic()
                if e.get("overlay"):
                    files[-1]["overlays"] = record["overlays"] = self._overlays(saved, [segments[s["id"]] for s in row], quality, fps)
                self._record_render(project["film"], record)
        except Cancelled:
            raise Cancelled(f"Cancelled: {len(files)} of {len(rows)} videos were finished and kept" if files else "Render cancelled") from None
        finally:
            for script in scripts:  # those of videos never reached too: a render that fails leaves none behind
                if script:
                    script.unlink(missing_ok=True)
        if raw.get("expect") is not None:
            self._paced(quality, (time.monotonic() - self.data["job"]["clock"]) / expect)
        self._tidy()
        with self.lock:
            self.data["preview"] = {"url": files[0]["url"], "kind": "video", "time": shots[0]["start"],
                                    "revision": project["revision"], "film": project["film"], "file": files[0]["file"],
                                    "files": files}

    def _action(self, raw):
        action = raw["action"]
        if action == "connect":
            greeting = self.viewer.connect(self.progress)
            if int(greeting.split()[1]) < 5:
                raise ValueError("Update Film Viewer to 3.07 Patch 2 or newer")
            self._library()
            self.command("icons " + ("on" if self.data["icons"] else "off"))
            with self.lock:
                self.data["connected"] = True
            if self.data["project"]:
                self._open(self.data["project"]["film"])
            return
        if action == "open":
            return self._open(raw.get("film"))
        if action == "rename":
            return self._rename(raw)
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
        return self._export(raw, project)
