"""Shared editing desk. One worker owns the viewer; HTTP only reads snapshots."""
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
import uuid

from shot_plan import RIDES, compile_shot, nearby, passes, probe_cameras, track_rows, validate_plan, validate_shot
from viewer_socket import Viewer, film_rows, number, plane_rows

ACTIONS = ("connect", "open", "seek", "preview", "clip", "around", "icons", "suggest", "export_cut")
RENDERS = {"draft": "960 540 30", "final": "1920 1080 60", "4k": "3840 2160 60"}
# The viewer renders in about real time whatever the size (measured: 5 s of film in 5 s at 480x270 and at 960x540).
CLIP = "960 540 30"
# After a jump in the film the viewer fades the aircraft in over about two seconds of real time, however many frames
# it draws (measured: nearly invisible at once, solid after 1.7 s). A still taken sooner shows them see-through.
FADE = 2.0
SETTLE = 1.0  # what a picture that stays on screen for a while waits of that: mostly solid, without doubling the wait
# For about as long as a rendered stretch lasts after its render ends, the viewer is still finishing that stretch's
# sound. A render started in that time can be heard from a point standing still in the sky instead of from the
# aircraft: no engine, guns or pilots' voices. Measured on an 11.5 s stretch: with a seek, a still or a read of
# the tracks 2, 5 or 8 s after a render, the next render is thin, and so is one started at once after a render
# that followed a seek; thin renders keep coming until the viewer has rested. Every render started 12 s or more
# after the one before was full, whatever happened in between (6 of 6). After a 4 s stretch, 3 s were enough.
TAIL = 1.5  # seconds added to the last rendered stretch's length before the next render may start
CAMERAS = 6  # raise when a camera's maths or the way a clip is rendered change: a kept clip made the old way must not pass for a new one


class Conflict(ValueError):
    pass


def camera_key(shot):
    return json.dumps({k: v for k, v in shot.items() if k not in ("id", "name")}, sort_keys=True)


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
        self.applied, self.built = None, {}  # which plan the viewer holds, and what could not be built in it
        self.jumped = 0.0  # when the film last jumped, for the aircraft fading in after it
        self.quiet = 0.0  # when the viewer has finished the last render's sound and the next render may start
        self.pace = 1  # how long this desk waits for the viewer's own timing, as a share; tests have no viewer to wait for
        self.stills = deque()
        self.data = {"connected": False, "films": [], "project": None, "planes": [],
                     "now": 0, "icons": False, "problems": {}, "nearby": None,
                     "preview": None, "proposals": [], "job": None, "videos": str(self.videos)}

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

    def _render(self, start, end, path, size, label=""):
        """One video of a stretch, with the film's sound whole (see TAIL)."""
        with self.lock:  # the wait is part of what this clip takes
            if self.data["job"]["expect"]:
                self.data["job"]["expect"] = round(self.data["job"]["expect"] + max(0, self.quiet - time.monotonic()), 1)
        while time.monotonic() < self.quiet:
            self.progress(f"Film Viewer is finishing the last clip's sound: {math.ceil(self.quiet - time.monotonic())} s, so this one keeps its own")
            time.sleep(.25)
        self.progress(label or f"Rendering {end - start:.1f} s of film")
        self.command(f"video {start:.8f} {end:.8f} {path.as_posix()} {size}")
        self.quiet = time.monotonic() + (end - start + TAIL) * self.pace

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

    def _settle(self):
        """Let the aircraft fade in after a jump, before a picture that will stay on screen for a while."""
        while time.monotonic() - self.jumped < SETTLE * self.pace:
            self.command("wait 10")

    def _open(self, film):
        if film not in {row["path"] for row in self.data["films"]}:
            raise ValueError("Choose a film from the viewer library")
        self.progress("Opening film and reading its moments")
        self.compiled, self.paths, self.applied = {}, {}, None
        with self.lock:
            self.data.update(project=None, preview=None, planes=[], problems={}, nearby=None)
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
            self.data.update(project=project, now=1, preview=None, proposals=[])
        self._seek(min(1, length))
        self._settle()
        self._frame(project["revision"])

    def _tracks(self, start, end, step=None):
        # Choose an exact divisor so the final snapshot lands at the end.
        step = step or (end - start) / max(1, math.ceil((end - start) * 2))
        key = (round(start, 3), round(end, 3), round(step, 6))
        if key not in self.paths:
            self.progress("Reading aircraft paths")
            self.paths[key] = track_rows(self.command(f"tracks {start:.8f} {end:.8f} {step:.10f}"))
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
            commands, problems = [], {}
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
                commands.extend(self.compiled[key])
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
        start, end = number(raw.get("start"), 0, length, "start"), number(raw.get("end"), 0, length, "end")
        if not .1 <= end - start <= 120:
            raise ValueError("A preview lasts 0.1 to 120 seconds")
        shots = self._plan(project, raw)[0]
        if raw.get("hero") is not None:  # who is near the lead in this stretch, for choosing the opponent
            self._around(raw, project)
        film = next((row for row in self.data["films"] if row["path"] == project["film"]), {})
        name = hashlib.sha256(json.dumps([
            project["film"], film.get("mb"), film.get("modified"), round(start, 2), round(end, 2), CLIP, CAMERAS,
            self.data["icons"],
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
            self._apply(project, raw)
            if raw.get("still") is not None:  # something to look at while the clip renders
                self._seek(number(raw["still"], 0, length, "time"), rough=True)
                self._settle()
                self._frame(project["revision"])
            self._render(start, end, path, CLIP)  # the film is back where it was afterwards
            for old in sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime)[:-80]:
                old.unlink(missing_ok=True)
        with self.lock:
            self.data["preview"] = {"url": "/media/clips/" + path.name, "kind": "clip", "start": start, "end": end,
                                    "time": start, "revision": project["revision"], "film": project["film"]}

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
            films = film_rows(self.command("films"), os.path.getmtime)
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
        if action == "around":
            return self._around(raw, project)
        if action == "clip":
            return self._clip(raw, project)
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
                    self._seek(seconds, rough=bool(raw.get("rough")))
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
        # same length, to lay over one another in an editor.
        rows, files = passes(shots), []
        if quality == "draft":  # measured: about real time; the larger sizes have not been timed
            with self.lock:
                self.data["job"]["expect"] = round(sum(s["end"] - s["start"] for s in shots) + 2 * len(rows), 1)
        for n, row in enumerate(rows, 1):
            self._apply(project, only=row)
            for shot in row:
                if shot["id"] in self.built:
                    raise ValueError(f"{shot['name']}: {self.built[shot['id']]}")
            for a, b in zip(row, row[1:]):
                if b["start"] > a["end"] + .01:
                    self.command(f"skip {a['end']:.8f} {b['start']:.8f}")
            self.applied = None  # The export-only skips must not leak into previews.
            path = self.media_root / (uuid.uuid4().hex + ".mp4")
            self._render(row[0]["start"], row[-1]["end"], path, RENDERS[quality],
                         f"Rendering {len(row)} shot{'s' * (len(row) > 1)} at {size}" + (f", video {n} of {len(rows)}" if len(rows) > 1 else ""))
            saved = self._keep(path, project, row, raw.get("labels"))
            files.append({"url": "/media/" + path.name, "file": str(saved), "shots": [s["id"] for s in row]})
        self._tidy()
        with self.lock:
            self.data["preview"] = {"url": files[0]["url"], "kind": "video", "time": shots[0]["start"],
                                    "revision": project["revision"], "film": project["film"], "file": files[0]["file"],
                                    "files": files}
