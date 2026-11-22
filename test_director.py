"""Run with python -m unittest test_director (no Film Viewer required)."""
import copy
import http.client
import json
import math
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import director_api
from director_api import listener, make_server, take_port, videos_folder, working_folder
import director_core
from director_core import Conflict, Desk, camera_key, framing
import finishing
import flight_data
import overlay
import trails
from shot_plan import (_glide, _last_seen, _seat, _steady, camera_pose, framed, compile_shot, escort_poses, filmer_path, ground_moments, landing, nearby,
                       passes, probe_cameras, ray, ray_eyes, validate_plan, validate_shot)
from viewer_socket import Viewer, film_rows, plane_rows

SHOT = {"id": "one", "name": "Pair", "start": 1, "end": 3, "kind": "pair",
        "hero": 19, "target": 10, "side": "right", "distance": 280, "height": 60, "fov": 60}
PROJECT = {"film": "films/test.ahf", "length": 20, "revision": 1, "shots": [], "events": []}


def turn_row(t, rate, bank):
    """A kept-tracks row of an aircraft at 300 ft/s and 500 ft, its heading turning `rate` rad/s to the left."""
    yaw = rate * t
    return (t, 0.0, 500.0, 0.0, yaw, 0.0, math.radians(bank), -300 * math.sin(yaw), 0.0, 300 * math.cos(yaw))


def write_kept(path, tracks, names):
    """A kept-tracks file as the viewer's help lays it out: speeds on, double positions, one entry per aircraft."""
    times = sorted({row[0] for rows in tracks.values() for row in rows})
    body = b""
    for t in times:
        rows = [(ident, row) for ident, rows in tracks.items() for row in rows if row[0] == t]
        body += struct.pack("<di", t, len(rows)) + b"".join(struct.pack("<idddffffff", ident, *row[1:]) for ident, row in rows)
    table = b"".join(struct.pack("<ii16sffff", ident, 0, names[ident].encode(), 20, 0, 0, 0) for ident in tracks)
    header = flight_data.HEADER.pack(b"AHTRACKS", 1, 3, 0, 0, 20.0, times[0], times[-1], .1, len(times), len(tracks),
                                     struct.calcsize("<idddffffff"), 0, flight_data.HEADER.size + len(body))
    path.write_bytes(header + body + table)


def fake_ffmpeg(calls, code=0, error=""):
    """Stands in for ffmpeg's process in finishing: notes its arguments and, unless it fails, writes the video."""
    class Process:
        returncode = code
        def __init__(self, args, **_options):
            calls.append(args)
            if not code:
                Path(args[-1]).write_bytes(b"cut")
        def communicate(self, timeout=None):
            return "", error
        def kill(self):
            pass
    return Process


class SlowViewer:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def command(self, command, progress):
        if command.startswith("seek "):
            self.entered.set()
            progress("waiting for viewer")
            self.release.wait(3)
            return ["at 1.0 in 2.0 s"]
        if command.startswith("planes"):
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder']
        if command.startswith("shot "):
            Path(command[5:]).write_bytes(b"test")
            return ["saved"]
        return []

    def commands(self, commands, progress):
        return [self.command(command, progress) for command in commands]


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.viewer = SlowViewer()
        self.desk = Desk(self.temp.name, self.viewer)
        no_ffmpeg = patch("director_core.shutil.which", return_value=None)  # the fakes write no real video
        no_ffmpeg.start()
        self.addCleanup(no_ffmpeg.stop)
        # The viewer stands where both of the test film's aircraft are (as `planes long` told the last seek).
        self.desk.data.update(project=copy.deepcopy(PROJECT), connected=True, now=10,
                              planes=[{"id": 19, "name": "Pilot"}, {"id": 10, "name": "Other"}])

    def tearDown(self):
        self.viewer.release.set()
        deadline = time.monotonic() + 3
        while self.desk.snapshot()["job"] and self.desk.snapshot()["job"]["running"] and time.monotonic() < deadline:
            time.sleep(.01)
        self.temp.cleanup()

    def test_background_seek_does_not_lock_state_and_rejects_duplicate_work(self):
        self.desk.start({"action": "seek", "seconds": 1, "expected_revision": 1})
        self.assertTrue(self.viewer.entered.wait(1))
        begin = time.monotonic()
        state = self.desk.snapshot()
        self.assertLess(time.monotonic() - begin, .1)
        self.assertTrue(state["job"]["running"])
        with self.assertRaises(Conflict):
            self.desk.start({"action": "seek", "seconds": 2, "expected_revision": 1})
        with self.assertRaises(Conflict):
            self.desk.save({"expected_revision": 1, "shots": []})

    def test_revision_conflict_and_restart_readback(self):
        saved = self.desk.save({"expected_revision": 1, "shots": [SHOT]})
        self.assertEqual(saved["revision"], 2)
        disk = json.loads(next(Path(self.temp.name).glob("*.json")).read_text())
        self.assertEqual(disk, saved)
        with self.assertRaises(Conflict):
            self.desk.save({"expected_revision": 1, "shots": []})
        with self.assertRaises(Conflict):
            self.desk.save({"expected_revision": 2.0, "shots": []})

    def test_failed_disk_write_keeps_previous_plan(self):
        self.desk.save({"expected_revision": 1, "shots": [SHOT]})
        before = self.desk.snapshot()["project"]
        with patch("director_core.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.desk.save({"expected_revision": 2, "shots": []})
        self.assertEqual(self.desk.snapshot()["project"], before)

    def test_plan_validation_boundaries(self):
        for changes in ({"start": float("nan")}, {"fov": 360}, {"hero": 1.5},
                        {"target": 19}, {"hero": None}, {"start": -1}, {"end": 40}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_shot({**SHOT, **changes}, 20)
        # Takes of one stretch through different cameras are allowed; they are dealt into passes without overlaps.
        takes = validate_plan([{**SHOT, "id": "later", "start": 5, "end": 7}, SHOT, {**SHOT, "id": "two"}], 20)
        self.assertEqual([[s["id"] for s in row] for row in passes(takes)], [["one", "later"], ["two"]])
        self.assertNotIn("notes", validate_shot({**SHOT, "notes": "kept by an older plan"}, 20))  # read, and dropped

    def test_true_world_camera_and_many_keyframes(self):
        shot = validate_shot(SHOT, 20)
        tracks = {t: {19: (t*100, 200, 0, 0, 0, 0), 10: (t*100, 200, 400, 0, 0, 0)} for t in [1, 1.5, 2, 2.5, 3]}
        commands = compile_shot(shot, tracks)
        self.assertEqual(len(commands), 4)
        self.assertTrue(all(" fixed " in c and c.endswith("curve") for c in commands))
        fixed = compile_shot({**shot, "kind": "fixed"}, tracks)
        self.assertEqual(len(fixed), 1)
        self.assertIn("at 10", fixed[0])
        # An ambush stands by the other aircraft's path instead (here 400 ft further along z) and watches it.
        ambush = compile_shot({**shot, "kind": "ambush", "orbit": 0}, tracks)
        self.assertEqual((len(ambush), ambush[0].endswith("at 10")), (1, True))
        self.assertAlmostEqual(float(ambush[0].split()[6]) - float(compile_shot({**shot, "kind": "fixed", "orbit": 0}, tracks)[0].split()[6]), 400)
        # A camera ship flies with the lead (heading +z; the other 600 ft ahead): 200 ft from it, 30 degrees off
        # the line through both, 30 ft up. Both fit its lens, so it looks between them: 30 degrees to the lead,
        # 7 to the other, 19 in the middle.
        ship = {**shot, "kind": "escort", "orbit": 30, "distance": 200, "height": 30, "fov": 50}
        flying = {t: {19: (0, 200, t * 400, 0, 0, 0), 10: (0, 200, t * 400 + 600, 0, 0, 0)} for t in [1, 1.5, 2, 2.5, 3]}
        x, alt, z, yaw = escort_poses(ship, flying)[2][:4]
        self.assertEqual((round(x), alt, round(z)), (-100, 230, 627))
        self.assertAlmostEqual(math.degrees(yaw), -18.7, places=0)
        # Asked for a narrower lens, it opens it as far as both need (23 degrees between them and room round
        # each) and shows the same picture. When even its widest could not hold both (the ship on the other
        # aircraft's side, the two some 150 degrees apart), it keeps the lens asked for and the lead in the middle.
        narrow = escort_poses({**ship, "fov": 20}, flying)[2]
        self.assertAlmostEqual(math.degrees(narrow[3]), -18.7, places=0)
        self.assertTrue(44 < math.degrees(narrow[6]) < 49)
        self.assertEqual(escort_poses(ship, flying)[2][6], math.radians(50))
        between = escort_poses({**ship, "orbit": 150, "fov": 36}, flying)[2]
        self.assertAlmostEqual(between[6], math.radians(36))
        self.assertLess(abs(between[3] - math.atan2(between[0], 800 - between[2])), math.radians(1.5))
        # The further off, the heavier: where the lead turns a sharp corner (at 3 s, 1200 ft along), a ship
        # 520 ft off swings much wider of its station than one 130 ft off, which cuts the corner only a little.
        bend = {t: {19: ((0, 200, t * 400, 0, 0, 0) if t <= 3 else ((t - 3) * 400, 200, 1200, 0, 0, 0)),
                    10: (0, 200, t * 400 + 3000, 0, 0, 0)} for t in [i / 2 for i in range(13)]}
        wide = lambda distance: math.dist(escort_poses({**ship, "orbit": 0, "distance": distance, "height": 0}, bend)[6][:3],
                                          (0, 200, 1200)) - distance
        self.assertTrue(0 < wide(130) < 200 and wide(520) > 1.5 * wide(130))  # a second heavy at 130 ft, two from 400 ft
        # It is heavy: the other aircraft crossing from one side to the other in half a second moves it little.
        crossing = {t: {19: rows[19], 10: ((-300 if t < 2 else 300), 200, t * 400 + 600, 0, 0, 0)} for t, rows in flying.items()}
        places = escort_poses(ship, crossing)
        self.assertLess(math.dist(places[1][:3], places[2][:3]) - 200, 60)  # 200 ft is the lead's own half second
        self.assertEqual(len(compile_shot(ship, flying)), 4)
        # The other aircraft 600 ft above as well: the ship drops below the lead, on the line through both.
        above = {t: {19: rows[19], 10: (0, 800, t * 400 + 600, 0, 0, 0)} for t, rows in flying.items()}
        self.assertLess(escort_poses({**ship, "height": 0}, above)[2][1], 200 - 100)
        # Watching one aircraft, the viewer holds it in the middle between our points, at any lens. Watching its
        # hero the ship circles the hero's own line of flight: 90 degrees is off the right wing, +x when heading +z.
        close = {**ship, "watch": "hero", "orbit": 90, "distance": 90, "height": 0, "fov": 12}
        self.assertEqual([round(v) for v in escort_poses(close, flying)[2][:3]], [90, 200, 800])
        self.assertTrue(all(" at 19 curve" in line for line in compile_shot(close, flying)))
        self.assertTrue(all(" at 10 curve" in line for line in compile_shot({**ship, "watch": "target"}, flying)))
        self.assertEqual(validate_shot({**SHOT, "kind": "escort", "watch": "hero", "target": None}, 20)["watch"], "hero")
        with self.assertRaises(ValueError):
            validate_shot({**SHOT, "kind": "escort", "target": None}, 20)
        # The camera for two aims between them, not at the point half way: on a long lens with the other aircraft
        # far off (3000 ft ahead here) the near one stays in the picture, 12.5 degrees being half this lens.
        far = {19: (0, 200, 0, 0, 0, 0), 10: (0, 200, 3000, 0, 0, 0)}
        pose = camera_pose({**shot, "orbit": 25, "distance": 300, "height": 0, "fov": 25}, far)
        self.assertLess(abs(math.degrees(pose[3]) - math.degrees(math.atan2(pose[0], -pose[2]))), 12.5)
        self.assertEqual(len(probe_cameras(ship, dict(enumerate(flying.values())), .5)), 5)
        # The near aircraft (19) shot down at 2 s while the other flies past where it fell: the camera stays where
        # it was, turns to the other over a second, and from then on the viewer holds the other for it. Left on
        # the line through both, it would be whipped round that place.
        passing = {t: {19: (0, 200, 0, 0, 0, 0), 10: (300 - 200 * t, 200, 400, 0, 0, 0)} for t in [0, .5, 1, 1.5, 2, 2.5, 3, 3.5, 4]}
        for t in (2.5, 3, 3.5, 4):
            passing[t].pop(19)
        fallen = compile_shot({**shot, "start": 0, "end": 4}, passing)
        places = [line.split()[4:7] for line in fallen]
        self.assertEqual(places[4:], [places[4]] * 4)
        self.assertNotEqual(places[3], places[4])
        self.assertEqual([" at 10 " in line for line in fallen], [False] * 6 + [True] * 2)
        # The far aircraft shot down instead: the camera flies on with the near one, the same way off it.
        for t in passing:
            passing[t] = {19: (t * 400, 200, 0, 0, 0, 0), **({10: (t * 400, 200, 900, 0, 0, 0)} if t <= 2 else {})}
        onward = [list(map(float, line.split()[4:7])) for line in compile_shot({**shot, "start": 0, "end": 4}, passing)]
        self.assertEqual([round(b[0] - a[0]) for a, b in zip(onward, onward[1:])], [200] * 7)
        self.assertEqual({round(place[2]) for place in onward}, {round(onward[0][2])})
        # Shot down mid-shot: the camera holds on where the aircraft was last seen.
        tracks[2.5].pop(10); tracks[3].pop(10)
        self.assertEqual(len(compile_shot(shot, tracks)), 4)
        for row in tracks.values():
            row.pop(10, None)
        with self.assertRaises(ValueError):
            compile_shot(shot, tracks)

    def run_action(self, raw):
        self.desk.start({**raw, "expected_revision": self.desk.snapshot()["project"]["revision"]})
        deadline = time.monotonic()+2
        while self.desk.snapshot()["job"]["running"] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIsNone(self.desk.snapshot()["job"]["error"])

    def test_a_click_on_the_ground_in_a_still_puts_a_tripod_there(self):
        # The way through a point of the picture: the middle along the camera, the right edge to its right, the top
        # up. A camera rolled 0.3 rad shows at (0.3074, -0.1088) what one not rolled shows at (0.2755, -0.2653), both
        # as the viewer's own `project` put an aircraft (22 Nov 2026).
        level = [0, 0, 0, 0, 0, 0, math.radians(90)]
        self.assertEqual([round(v, 6) + 0 for v in ray(level, 2, .5, .5)], [0, 0, 1])
        self.assertEqual([round(v, 6) + 0 for v in ray(level, 2, 1, .5)], [round(math.sqrt(.5), 6), 0, round(math.sqrt(.5), 6)])
        self.assertGreater(ray(level, 2, .5, 0)[1], 0)
        turned, screen = [0, 0, 0, 1.1, -.2, 0, 1.0], lambda x, y: ((x + 1) / 2, (1 - y) / 2)
        self.assertLess(math.dist(ray(turned, 16 / 9, *screen(.2755, -.2653)), ray([*turned[:5], .3, 1.0], 16 / 9, *screen(.3074, -.1088))), 3e-4)
        self.assertIsNone(landing(ray_eyes((0, 0, 0), (0, 1, 0)), {}))  # straight up: the sky
        # The viewer drew the still from 300 ft up, looking 0.3 rad down along +z, over flat ground 100 ft up.
        self.desk.media_root.mkdir(parents=True, exist_ok=True)
        (self.desk.media_root / "still.bmp").write_bytes(b"BM" + bytes(16) + struct.pack("<ii", 1600, -900) + bytes(8))
        self.desk.data["preview"] = {"url": "/media/still.bmp", "kind": "image", "time": 10, "revision": 1, "film": PROJECT["film"]}
        drawn = ["camera fixed 0 300 0 0 -0.3 0 fov 1.0 time 10.00 shot 8.00"]
        def native(command, progress):
            if command == "camera":
                return drawn
            if command.startswith("tracks "):  # the hero flying along +z 2,000 ft up, the other beside him
                return [f"track {t / 50:.2f} {ident} {x} 2000 {300 * t / 50:.2f} 0 0 0" for t in range(390, 651) for ident, x in ((19, 0), (10, 300))]
            if command.startswith("project "):
                parts = command.split()
                self.assertEqual((parts[1], parts[2], parts[-1]), ("10.000000", "10.100100", "check"))
                eyes = [list(map(float, line.split()[1:4])) for line in Path(parts[4]).read_text().splitlines()]
                Path(parts[5]).write_text("\n".join(f"ground 10.00 {i + 1} {alt - 100:.1f}" for i, (_x, alt, _z) in enumerate(eyes) if alt - 100 < 3))
            return []
        self.desk.viewer.command = native
        self.run_action({"action": "stand", "shot": {**SHOT, "start": 8, "end": 12}, "x": .5, "y": .5})
        stood = self.desk.snapshot()["stand"]
        self.assertEqual({k: stood["shot"][k] for k in ("kind", "watch", "path", "zoom", "hero", "target")},
                         {"kind": "escort", "watch": "hero", "path": "own", "zoom": "hold", "hero": 19, "target": 10})
        self.assertEqual(stood["shot"]["keys"][0]["t"], 10)
        # It stands 6 ft over where the middle of the picture meets the ground, all through the shot.
        tracks = self.desk._tracks(8, 12)
        places = {tuple(round(v, 1) for v in pose[:3]) for pose in escort_poses(stood["shot"], _steady(stood["shot"], tracks))}
        self.assertEqual(len(places), 1)
        self.assertLess(math.dist(next(iter(places)), (0, 106, 200 / math.tan(.3))), 3)
        self.assertAlmostEqual(stood["away"], math.hypot(200, 200 / math.tan(.3)), delta=3)
        # Clicked in the sky, or on a picture from the pilot's seat, nothing moves and the page is told why.
        for x, y, camera, said in ((.5, 0, drawn, "not the ground"), (.5, .5, ["camera recorded time 10.00"], "stands in the world")):
            drawn = camera
            self.desk.start({"action": "stand", "shot": {**SHOT, "start": 8, "end": 12}, "x": x, "y": y, "expected_revision": 1})
            while self.desk.snapshot()["job"]["running"]:
                time.sleep(.01)
            self.assertIn(said, self.desk.snapshot()["job"]["error"])
        self.assertEqual(self.desk.snapshot()["stand"]["stamp"], stood["stamp"])

    def test_a_tripod_trimmed_to_a_later_stretch_stands_where_it_stood(self):
        # The hero turning, so his averaged path, and the tripod's place with it, depends on where a stretch ends.
        def native(command, progress):
            if command.startswith("tracks "):
                return [f"track {t / 50:.2f} {ident} {2000 * math.cos(t / 50 * .3) + x:.2f} 2000 {2000 * math.sin(t / 50 * .3):.2f} 0 0 0"
                        for t in range(390, 901) for ident, x in ((19, 0), (10, 300))]
            return []
        self.desk.viewer.command = native
        self.desk.data["job"] = {"running": False, "message": "", "share": None, "clock": time.monotonic()}  # what reading reports to
        place = lambda shot: tuple(round(float(v), 3) for v in self.desk._camera(validate_shot(shot, 20))[0].split()[4:7])
        stood = {**SHOT, "kind": "escort", "watch": "hero", "path": "own", "zoom": "hold", "start": 8, "end": 14,
                 "keys": [{"t": 8.3, "orbit": 90, "distance": 400, "height": 30, "fov": 14, "watch": "hero"}]}
        later = {**stood, "id": "copy", "start": 11, "end": 14}
        # A copy cut to a stretch after its keyframe: with the stretch it was placed over it stands where it stood.
        self.assertEqual(place({**later, "keys": [{**stood["keys"][0], "span": [8, 14]}]}), place(stood))
        # Without it, as before: the keyframe went with what was trimmed off, and the camera flies with the hero.
        self.assertNotIn("keys", validate_shot(later, 20))
        with self.assertRaises(ValueError):  # a stretch that does not hold its keyframe
            validate_shot({**later, "keys": [{**stood["keys"][0], "span": [11, 14]}]}, 20)

    def test_angles_are_rated_over_the_whole_shot(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return [f"track {t} {ident} {x} 200 0 0 0 0"
                        for t in (1, 1.5, 2, 2.5, 3) for ident, x in ((19, 0), (10, 300))]
            if command.startswith("project "):
                parts = command.split()
                cameras = Path(parts[4]).read_text().splitlines()
                self.assertEqual(len(cameras), 6)  # five steps of the moving pair, one wing mount
                rows = []
                for step, t in enumerate(("1.00", "1.50", "2.00", "2.50", "3.00")):
                    for cam in range(1, 7):
                        rows.append(f"eye {t} {cam} 0 0 0 0 0 0 1")
                        if cam == 6 and step < 2:
                            continue  # the wing camera loses the opponent after the first second
                        rows.extend(f"on {t} {cam} {ident} {x} 0 .05" for ident, x in ((19, -.3), (10, .3)))
                # `check`: the pair's last camera is under a hill, the opponent is behind one from the wing at 2.5 s,
                # and the wing camera 0.1 ft over a runway is still a picture.
                rows += ["ground 3.00 5 -12.0", "blocked 2.50 6 10 terrain", "ground 2.00 6 0.1"]
                Path(parts[5]).write_text("\n".join(rows))
            return []
        self.desk.viewer.command = native
        self.run_action({"action": "suggest", "shots": [SHOT, {**SHOT, "id": "wing", "kind": "wing", "height": 3}]})
        self.assertEqual(self.desk.snapshot()["proposals"], [{"id": "one", "score": .8, "size": .05, "quality": .7},
                                                             {"id": "wing", "score": .4, "size": .05, "quality": .35}])
        self.assertTrue(next(c for c in commands if c.startswith("project ")).endswith(" aspect 16/9 check"))
        # A dot, an aircraft at the edge and one hiding the other all read worse than two clear silhouettes,
        # and the smaller of the two reads better bigger, up to a tenth of the picture's width.
        clear = framing([(-.3, 0, .2), (.3, 0, .05)])
        self.assertEqual(framing([(-.3, 0, .2), (.3, 0, .12)]), 1)
        for worse in ([(-.3, 0, .2), (.3, 0, .01)], [(-.3, 0, .2), (.95, 0, .05)], [(0, 0, .2), (.1, 0, .05)]):
            self.assertLess(framing(worse), clear)
        self.assertLess(clear, 1)
        self.assertEqual(len([c for c in commands if c.startswith("project ")]), 1)
        with self.assertRaises(ValueError):
            self.desk._suggest({"shots": [SHOT, {**SHOT, "id": "two", "end": 4}]}, self.desk.snapshot()["project"])

    def test_unchanged_shots_keep_their_cameras_and_selected_shots_render_as_one_video(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):  # every step asked for, as the viewer answers
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + i * step:.4f} {ident} {x} 200 0 0 0 0"
                        for i in range(round((end - start) / step) + 1) for ident, x in ((19, 0), (10, 300))]
            if command.startswith("time"):
                return ["time 4.0 length 20.0 paused 0"]
            if command.startswith("planes"):
                return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder']
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"test")
            return []
        self.desk.viewer.command = native
        plan = [SHOT, {**SHOT, "id": "two", "start": 5, "end": 7}, {**SHOT, "id": "three", "start": 9, "end": 11}]
        self.desk.save({"expected_revision": 1, "shots": plan})
        self.run_action({"action": "seek", "seconds": 2, "rough": True})
        # One seek: since Patch 3 it plays the 5 s before the moment, so the aircraft in the still are solid.
        at = commands.index("seek 2.00000000 rough")
        self.assertEqual(commands[at:at + 2], ["seek 2.00000000 rough", "planes long"])
        self.assertTrue(commands[at + 2].startswith("shot ") and "play" not in commands)
        self.assertEqual(len([c for c in commands if c.startswith("tracks ")]), 3)
        # Paths are read every 0.02 s from a tenth of a second early: read every 0.5 s, the recording pilot was off his path.
        self.assertIn("tracks 0.90000000 3.00000000 0.0200000000", commands)
        plan[0] = {**SHOT, "name": "Renamed"}
        self.desk.save({"expected_revision": 2, "shots": plan})
        self.run_action({"action": "seek", "seconds": 2})
        self.assertEqual(len([c for c in commands if c.startswith("tracks ")]), 3)
        del commands[:]
        self.run_action({"action": "export_cut", "shot_ids": ["one", "three"], "quality": "draft"})
        self.assertIn("skip 3.00000000 9.00000000", commands)
        self.assertTrue(commands[-1].startswith("video 1.00000000 11.00000000 ") and commands[-1].endswith(" 960 540 60"))  # twice the clip's 30: see Desk._render
        self.assertNotIn("play", commands)  # playing just before a render takes the engine and guns out of its sound
        self.assertFalse(any(c.startswith("seek ") for c in commands))  # no seek first: `video` plays the 5 s before its start itself
        self.run_action({"action": "icons", "on": True})
        self.assertIn("icons on", commands)
        self.assertTrue(self.desk.snapshot()["icons"])
        # Asked with a time, a switch answers with a still of that moment: seen at once, no render.
        self.run_action({"action": "icons", "on": False, "seconds": 6, "rough": True})
        self.assertEqual(commands[-5:-1], ["icons off", "time", "seek 6.00000000", "planes long"])  # 4 s ahead of the film: the plain seek
        # Asked for again where the film already is, a still is taken without another jump.
        self.run_action({"action": "seek", "seconds": .2, "rough": True})
        del commands[:]
        self.run_action({"action": "seek", "seconds": .2, "rough": True})
        self.assertFalse(any(c.startswith("seek ") for c in commands))
        self.assertEqual(self.desk.snapshot()["preview"]["kind"], "image")
        self.run_action({"action": "export_cut", "shot_ids": ["two"], "quality": "4k"})
        self.assertTrue(commands[-1].endswith(" 3840 2160 60"))  # a 30 fps video (unless 60 is asked for) is rendered at twice that
        # A second take of the first stretch cannot share the viewer's one camera: it gets a video of its own,
        # and until it is selected the first take is the one that plays.
        self.desk.save({"expected_revision": 3, "shots": plan + [{**SHOT, "id": "take", "kind": "cockpit"}]})
        del commands[:]
        self.run_action({"action": "export_cut", "shot_ids": ["one", "take", "three"], "quality": "draft", "labels": {"take": "Cock/pit"}})
        self.assertEqual([c.split()[1:3] for c in commands if c.startswith("video ")],
                         [["1.00000000", "11.00000000"], ["1.00000000", "3.00000000"]])
        files = self.desk.snapshot()["preview"]["files"]
        self.assertEqual([f["shots"] for f in files], [["one", "three"], ["take"]])
        # Each finished video is kept in the videos folder under a name that says what it is (the same cut
        # rendered again beside the first); the working folder holds the copy the page plays.
        self.assertEqual([Path(f["file"]).name for f in files], ["test 00.01-00.11 cut of 2 shots (2).mp4", "test 00.01 Pair - Cock-pit.mp4"])
        self.assertTrue(all(Path(f["file"]).parent == self.desk.videos and Path(f["file"]).exists()
                            and (self.desk.media_root / Path(f["url"]).name).exists() for f in files))
        self.assertEqual(len(list(self.desk.videos.glob("*.mp4"))), 4)
        # Apart, every shot is a video of its own, in time order, named as one shot.
        del commands[:]
        self.run_action({"action": "export_cut", "shot_ids": ["three", "one"], "quality": "draft", "apart": True})
        self.assertEqual([c.split()[1:3] for c in commands if c.startswith("video ")],
                         [["1.00000000", "3.00000000"], ["9.00000000", "11.00000000"]])
        self.assertNotIn("skip 3.00000000 9.00000000", commands)
        self.assertEqual([f["shots"] for f in self.desk.snapshot()["preview"]["files"]], [["one"], ["three"]])
        self.run_action({"action": "export_cut", "shot_ids": ["one"], "quality": "4k", "fps": 60})
        self.assertTrue(commands[-1].endswith(" 3840 2160 60"))
        del commands[:]
        self.run_action({"action": "seek", "seconds": 2})
        cams = [c for c in commands if c.startswith("cam ")]
        self.assertTrue(cams and not any(c.startswith("cam 1 2 mount") for c in cams))

    def test_socket_does_not_accept_partial_confirmation_or_injected_lines(self):
        viewer = Viewer()
        client, peer = socket.socketpair()
        viewer.sock, viewer.reader = client, client.makefile("r", encoding="utf-8")
        with self.assertRaises(ValueError):
            viewer.command("seek 1\nquit")
        peer.sendall(b"at 1.0\n")
        peer.shutdown(socket.SHUT_WR)
        with self.assertRaises(ConnectionError):
            viewer.command("seek 1")
        peer.close()

    def test_commands_sent_together_are_each_answered_and_an_error_leaves_no_reply_waiting(self):
        viewer = Viewer()
        client, peer = socket.socketpair()
        viewer.sock, viewer.reader = client, client.makefile("r", encoding="utf-8", newline="\n")
        peer.sendall(b"one\n.end\nerror: no such aircraft\n.end\nthree\n.end\nfour\n.end\n")
        with self.assertRaises(ValueError):
            viewer.commands(["a", "b", "c"])
        self.assertEqual(peer.recv(100), b"a\nb\nc\n")  # in one piece
        self.assertEqual(viewer.command("d"), ["four"])
        peer.close()
        viewer.close()

    def test_the_ratings_and_a_stretch_cut_shorter_pick_from_the_read_already_made(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            start, end, step = map(float, command.split()[1:4])
            return [f"track {start + i * step:.4f} 19 0 200 {300 * (start + i * step):.1f} 0 0 0" for i in range(round((end - start) / step) + 1)]
        self.desk.viewer.command = native
        self.desk.progress = lambda *told: None
        whole = self.desk._tracks(1, 3)
        self.assertEqual(len(self.desk._tracks(1, 3, .25)), 9)  # the ratings' moments
        short = self.desk._tracks(1, 2)
        self.assertEqual(commands, ["tracks 0.90000000 3.00000000 0.0200000000"])
        self.assertEqual(short, {t: row for t, row in whole.items() if t < 2.001})
        self.desk._tracks(1, 4)  # longer, or from another start: read
        self.desk._tracks(1.5, 3)
        self.assertEqual(len(commands), 3)

    def test_a_shot_that_cannot_be_built_does_not_block_the_desk(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):  # aircraft 10 never appears
                return [f"track {t} 19 0 200 0 0 0 0" for t in (1, 1.5, 2, 2.5, 3)]
            if command.startswith("time"):
                return ["time 4.0 length 20.0 paused 0"]
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [SHOT]})
        self.run_action({"action": "seek", "seconds": 2, "rough": True})
        self.assertIn("seek 2.00000000 rough", commands)
        self.assertIn("Aircraft 10 is not in the film", self.desk.snapshot()["problems"]["one"])
        self.desk.start({"action": "export_cut", "expected_revision": 2})
        deadline = time.monotonic()+2
        while self.desk.snapshot()["job"]["running"] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn("Aircraft 10", self.desk.snapshot()["job"]["error"])
        self.assertFalse(any(c.startswith("video ") for c in commands))

    def test_with_ffmpeg_a_30_fps_video_is_rendered_at_60_and_every_second_frame_kept(self):
        commands, cuts = [], []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"lead")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 5, "end": 7, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("finishing.subprocess.Popen", fake_ffmpeg(cuts)):
            self.run_action({"action": "export_cut", "quality": "draft"})
        video = next(c for c in commands if c.startswith("video "))
        self.assertTrue(video.startswith("video 5.00000000 7.00000000 ") and "-raw.mp4" in video and video.endswith(" 960 540 60"))
        self.assertEqual(cuts[0][cuts[0].index("-vf") + 1], "fps=30")
        self.assertFalse(Path(video.split()[3]).exists())  # the 60 fps video is gone
        self.assertEqual(Path(self.desk.snapshot()["preview"]["file"]).read_bytes(), b"cut")
        # The render counted its steps (the viewer's render, then ffmpeg's), followed ffmpeg through its
        # progress file, and its record says how long it took.
        job = self.desk.snapshot()["job"]
        self.assertEqual((job["step"], job["steps"]), (2, 2))
        self.assertEqual(cuts[0][cuts[0].index("-progress") + 1][-9:], ".progress")
        records = json.loads(next(self.desk.root.glob("*.renders.json")).read_text(encoding="utf-8"))
        self.assertGreaterEqual((records if isinstance(records, list) else records["renders"])[-1]["took"], 0)
        log = Path(self.temp.name) / "x.progress"
        self.assertIsNone(finishing._made(log))
        log.write_text("frame=1\nout_time_us=N/A\nprogress=continue\nframe=90\nout_time_us=1500000\nprogress=continue\n")
        self.assertEqual(finishing._made(log), 1.5)
        # ffmpeg failing is said, not hidden behind a video at twice its frame rate.
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("finishing.subprocess.Popen", fake_ffmpeg([], 1, "Unknown encoder 'libx264'")):
            self.desk.start({"action": "export_cut", "expected_revision": 2})
            self.desk.worker.join(3)
        self.assertIn("Unknown encoder", self.desk.snapshot()["job"]["error"])

    def test_cancel_stops_ffmpeg_at_once_and_the_viewer_after_its_pass(self):
        videos = []
        def native(command, progress):
            if command.startswith("video "):
                videos.append(command)
                Path(command.split()[3]).write_bytes(b"lead")
                if len(videos) == 2:
                    self.desk.stop()  # Cancel, pressed while the viewer renders the second pass
            return []
        started = threading.Event()
        class Endless:  # ffmpeg that would run on until it is killed
            returncode = None
            def __init__(self, args, **_options):
                Path(args[-1]).write_bytes(b"partial"); started.set()
            def communicate(self, timeout=None):
                if self.returncode is None and timeout is not None:
                    time.sleep(timeout)
                    raise subprocess.TimeoutExpired("ffmpeg", timeout)
                return "", ""
            def kill(self):
                self.returncode = -9
        self.desk.viewer.command = native
        seat = {**SHOT, "kind": "cockpit", "target": None}
        self.desk.save({"expected_revision": 1, "shots": [{**seat, "start": 5, "end": 7}, {**seat, "id": "two", "start": 9, "end": 10}]})
        # ffmpeg is stopped at once: the video it was making and the viewer's are gone, and nothing else is rendered.
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("finishing.subprocess.Popen", Endless):
            self.desk.start({"action": "export_cut", "quality": "draft", "apart": True, "expected_revision": 2})
            self.assertTrue(started.wait(2))
            self.assertEqual(self.desk.snapshot()["job"]["videos"], 2)
            self.desk.stop()
            self.desk.worker.join(3)
        job = self.desk.snapshot()["job"]
        self.assertEqual((job["error"], job["cancelling"], job["video"], len(videos)), ("Render cancelled", True, 1, 1))
        self.assertEqual(list(self.desk.media_root.glob("*.mp4")), [])
        self.assertFalse((self.desk.root / "errors.log").exists())  # asked for, not an error
        # The viewer cannot be stopped from the socket: it finishes its pass, and the next one is not begun. A
        # video finished before is kept.
        del videos[:]
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("finishing.subprocess.Popen", fake_ffmpeg([])), \
                patch("director_core.blend", lambda parts, target, *_a: Path(target).write_bytes(b"blurred")):
            self.desk.start({"action": "export_cut", "quality": "draft", "apart": True, "fps": 60, "effects": {"blur": True}, "expected_revision": 2})
            self.desk.worker.join(3)
        self.assertEqual(len(videos), 2)  # of the first shot's four passes
        self.assertEqual(self.desk.snapshot()["job"]["error"], "Render cancelled")
        self.assertEqual(list(self.desk.media_root.glob("*.mp4")), [])
        # A new job begins with no Cancel standing.
        self.run_action({"action": "export_cut", "quality": "draft", "shot_ids": ["two"]})

    def test_motion_blur_renders_four_passes_a_quarter_frame_apart_and_blends_them(self):
        commands, blends = [], []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"fast")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        def blend(parts, target, fps, filters=""):
            blends.append(([p.exists() for p in parts], fps, filters))
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 3, "end": 5, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.blend", blend):
            self.run_action({"action": "export_cut", "quality": "final", "fps": 60, "effects": {"blur": True}})
            self.run_action({"action": "export_cut", "quality": "4k", "effects": {"blur": True, "look": True}})
        videos = [c.split() for c in commands if c.startswith("video ")]
        self.assertEqual([(v[1], v[2], v[4:]) for v in videos[:4]], [(f"{3 + n / 480:.8f}", f"{5 + n / 480:.8f}", ["1920", "1080", "120"]) for n in range(4)])
        # At 30 fps, the default, sixteen frames a frame. At 4K they come as eight passes at 60: asked for 4K at
        # 120, the viewer crashed in its encoder.
        self.assertEqual([(v[1], v[2], v[4:]) for v in videos[4:]], [(f"{3 + n / 480:.8f}", f"{5 + n / 480:.8f}", ["3840", "2160", "60"]) for n in range(8)])
        self.assertEqual(blends, [([True] * 4, 60, ""), ([True] * 8, 30, finishing.style(3840, 2160, True, True))])

    def test_the_film_look_goes_on_in_ffmpeg(self):
        trims = []
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        def trim(source, target, filters="", fps=None):
            trims.append((filters, fps))
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 0, "end": 2, "kind": "cockpit"}]})
        commands = []
        self.desk.viewer.command = lambda command, progress: (commands.append(command), native(command, progress))[1]
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", trim):
            self.run_action({"action": "export_cut", "quality": "draft", "effects": {"look": True}})
            self.run_action({"action": "export_cut", "quality": "draft"})
            self.run_action({"action": "export_cut", "quality": "draft", "fps": 60})
        # The looks need ffmpeg (the film look brings its haze, laid first,
        # as in the grade). A video of 30 frames a second is rendered at 60, where Film Viewer does not draw the
        # recording pilot's aircraft a frame off, and ffmpeg keeps every second frame; one of 60 goes as it is.
        self.assertEqual(trims, [(finishing.haze(960, 540) + "," + finishing.look(540), 30), ("", 30)])
        self.assertEqual([c.split()[4:] for c in commands if c.startswith("video ")], [["960", "540", "60"]] * 3)
        # Without ffmpeg nothing can halve it: the video keeps the 60 it was rendered at.
        del commands[:]
        with patch("director_core.shutil.which", return_value=None), patch("director_core.trim", trim):
            self.run_action({"action": "export_cut", "quality": "draft"})
        self.assertEqual(([c.split()[4:] for c in commands if c.startswith("video ")], len(trims)), ([["960", "540", "60"]], 2))

    def test_each_shot_is_rendered_with_its_own_effects_and_alike_ones_together(self):
        renders, trims = [], []
        def native(command, progress):
            if command.startswith("video "):
                renders.append(tuple(map(float, command.split()[1:3])))
                Path(command.split()[3]).write_bytes(b"film")
            return []
        def trim(source, target, filters="", fps=None):
            trims.append(filters)
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = native
        seat = {**SHOT, "kind": "cockpit", "target": None}
        look = {"look": True}
        # One video asked for: the two shots that keep the film look are one stretch of it, the plain one after them
        # another; a shot that keeps nothing of its own has what the page sends.
        self.desk.save({"expected_revision": 1, "shots": [{**seat, "id": "a", "start": 2, "end": 4, "effects": look},
                                                          {**seat, "id": "b", "start": 4, "end": 6, "effects": {"look": True, "overlay": False}},
                                                          {**seat, "id": "c", "start": 6, "end": 8, "effects": {}}, {**seat, "id": "d", "start": 8, "end": 9}]})
        self.assertEqual(self.desk.snapshot()["project"]["shots"][1]["effects"], look)  # kept as the page sent it, falses left out
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", trim):
            self.run_action({"action": "export_cut", "quality": "draft", "effects": look})
        self.assertEqual(renders, [(2, 6), (6, 8), (8, 9)])
        styled = finishing.style(960, 540, True, True)
        self.assertEqual(trims, [styled, "", styled])
        self.assertEqual([(r["shots"], r["extras"]) for r in self.desk.snapshot()["renders"]],
                         [(["a", "b"], ["look", "haze"]), (["c"], []), (["d"], ["look", "haze"])])
        # Effects are not the camera: changing them leaves the shot's videos current.
        self.assertFalse(self.desk.snapshot()["versions"]["c"][0]["changed"])
        plan = self.desk.snapshot()["project"]
        self.desk.save({"expected_revision": plan["revision"], "shots": [{**s, "effects": {"blur": True}} for s in plan["shots"]]})
        self.assertFalse(self.desk.snapshot()["versions"]["c"][0]["changed"])
        for wrong in ({"trails": {"lead": "red"}}, {"trails": {"seconds": 500}}, "all"):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                validate_shot({**seat, "effects": wrong}, 20)
        self.assertEqual(validate_shot({**seat, "effects": {"trails": {"lead": "#ABCDEF", "tail": "#00FF00"}}}, 20)["effects"],
                         {"trails": {"lead": "#abcdef", "opponent": "#ff9e6b", "tail": "#00ff00", "seconds": 2.5, "exact": False}})

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_the_blend_keeps_the_film_s_length_at_its_own_frame_rate(self):
        parts, target = [Path(self.temp.name) / f"fast{n}.mp4" for n in range(4)], Path(self.temp.name) / "o.mp4"
        for part in parts:
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=120:d=1.5", "-f", "lavfi", "-i", "sine=d=1.5",
                            "-c:v", "libx264", "-c:a", "aac", str(part)], check=True)
        finishing.blend(parts, target, 60, finishing.style(160, 90, True, True))  # the looks fit in its graph
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames",
                                "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,60/1,90")
        self.assertTrue(probe[1].startswith("audio,"))
        self.assertFalse(any(p.exists() for p in parts))
        finishing.trim(target, parts[0], finishing.style(160, 90, True, True), 30)  # and with every second frame kept
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,nb_frames",
                                "-of", "csv=p=0", str(parts[0])], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,45")
        # At 30 fps the four passes are blended sixteen frames a frame.
        for part in parts:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=120:d=1.5", "-f", "lavfi", "-i", "sine=d=1.5",
                            "-c:v", "libx264", "-c:a", "aac", str(part)], check=True)
        finishing.blend(parts, target, 30)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames",
                                "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,30/1,45")
        # And so are eight passes at 60, as 4K is rendered.
        parts = [Path(self.temp.name) / f"slow{n}.mp4" for n in range(8)]
        for part in parts:
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=60:d=1.5", "-f", "lavfi", "-i", "sine=d=1.5",
                            "-c:v", "libx264", "-c:a", "aac", str(part)], check=True)
        finishing.blend(parts, target, 30)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames",
                                "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,30/1,45")

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_haze_lies_along_the_horizon_and_not_on_an_aircraft_in_the_sky(self):
        # Sky over ground; in the sky a small grey aircraft and a small olive one (coloured like ground).
        w, h = 320, 180
        scene = (f"color=c=0x4A6FA5:s={w}x{h // 2}[s];color=c=0x5A7A2A:s={w}x{h // 2}[g];[s][g]vstack,"
                 "drawbox=x=40:y=20:w=24:h=12:c=0x707070:t=fill,drawbox=x=240:y=20:w=24:h=12:c=0x556B2F:t=fill,format=gbrp")
        def picture(filters):
            raw = subprocess.run(["ffmpeg", "-v", "error", "-filter_complex", scene + ("," + filters if filters else ""),
                                  "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
            return [[raw[(y * w + x) * 3:(y * w + x) * 3 + 3] for x in range(w)] for y in range(h)]
        before, after = picture(""), picture(finishing.haze(w, h))
        moved = lambda x, y: max(abs(a - b) for a, b in zip(before[y][x], after[y][x]))
        self.assertGreater(moved(160, h // 2 - 2), 15)  # the sky just above the horizon
        self.assertGreater(moved(160, h // 2 + 2), 15)  # the far ground just below it
        for x, y in ((160, 3), (160, h - 3), (52, 26), (252, 26)):  # high sky, near ground, both aircraft
            self.assertLess(moved(x, y), 4, (x, y))

    def test_wingtips_and_the_camera_put_a_rolled_aircraft_where_the_eye_sees_it(self):
        # Level, heading +z, 18 ft to each tip; seen from 100 ft behind on a 1.2 rad lens: the tips either side of the
        # middle, the same distance off it. Rolled right a quarter turn, the right tip is straight below the middle.
        left, right = trails.wingtips((0, 0, 0), 0, 0, 0, 18)
        self.assertEqual((left, right), ((-18.0, 0.0, 0.0), (18.0, 0.0, 0.0)))
        cam = trails.camera((0, 0, -100, 0, 0, 0, 1.2))
        (lx, ly), (rx, ry) = (trails.to_screen(cam, p, 1920, 1080) for p in (left, right))
        self.assertAlmostEqual(lx + rx, 1920)
        self.assertAlmostEqual(rx - 960, 18 / 100 / math.tan(.6) * 960)
        self.assertEqual((round(ly), round(ry)), (540, 540))
        down = trails.wingtips((0, 0, 0), 0, 0, math.pi / 2, 18)[1]
        self.assertAlmostEqual(down[1], -18)
        self.assertIsNone(trails.to_screen(cam, (0, 0, -150), 1920, 1080))  # behind the camera
        # A camera rolled a quarter turn to the right puts the right of the world at the top of the picture.
        rolled = trails.to_screen(trails.camera((0, 0, -100, 0, 0, math.pi / 2, 1.2)), right, 1920, 1080)
        self.assertLess(rolled[1], 540)
        self.assertAlmostEqual(rolled[0], 960)

    def test_a_curving_camera_is_placed_as_the_viewer_moves_it(self):
        points = [(0.0, [0.0, 0.0]), (1.0, [10.0, 0.0]), (2.0, [20.0, 10.0]), (3.0, [30.0, 10.0])]
        self.assertEqual(trails.between(points, 1.0), [10.0, 0.0])
        self.assertEqual(trails.between(points, 3.0), [30.0, 10.0])
        self.assertAlmostEqual(trails.between(points, 1.5)[1], 5.0)  # halfway, the curve and the straight line agree
        self.assertLess(trails.between(points, 1.25)[1], 2.5)  # nearer the bend's start, the curve lags a straight line

    def test_trails_are_drawn_from_both_wingtips_fading_behind_and_not_without_a_camera(self):
        # An aircraft flying +z at 300 ft/s, seen from the side 400 ft off; three frames at 60 fps, the middle one blind.
        rows = [(t / 50, 0.0, 1000.0, 300 * t / 50, 0.0, 0.0, 0.0, 0.0, 0.0, 300.0) for t in range(0, 200)]
        eye = (-400.0, 1000.0, 300.0 * 3.5, -math.pi / 2, 0.0, 0.0, 1.2)  # yaw -pi/2 looks toward +x
        text = trails.script([(3.5, eye), (3.5 + 1 / 60, None), (3.5 + 2 / 60, eye)], {7: rows}, {7: 18}, {7: trails.colour("#ff8000")},
                             .02, 60, 1920, 1080)
        events = [line for line in text.splitlines() if line.startswith("Dialogue:")]
        firsts = [e for e in events if ",0:00:00.00,0:00:00.01," in e]
        style = trails.STYLE
        seen = sum(style["opacity"] * (1 - (b + .5) / style["bands"]) >= .01 for b in range(style["bands"]))
        per = len(firsts) // 2  # two wingtips: each step of brightness that is not too faint to see, then the last metre
        self.assertGreater(per, seen)
        # The last metre before the wingtip comes in from nothing: each piece towards the tip fainter, the last nearly gone.
        rise = [int(e.split("\\1a&H")[1][:2], 16) for e in firsts[seen:per]]
        self.assertEqual(rise, sorted(rise))
        self.assertGreater(rise[-1], 200)
        # The bright part is sized to the picture, not to the clock: where the aircraft takes twice as long to cross
        # the picture (a camera twice as far), the trail keeps its brightness twice as long. Half is gone after
        # ln 2 of that time.
        close, far = (trails._edges(style, bright)[0] for bright in (.2, .4))
        self.assertEqual(close, sorted(close))
        self.assertAlmostEqual(far[4] / close[4], 2, places=2)
        self.assertAlmostEqual(close[style["bands"] // 2] * style["seconds"], .2 * math.log(2), places=2)
        # At the lens a trail is not drawn at all: it would cut the frame in two.
        at_lens = (0.0, 1000.0, 300.0 * 3.5 - 5, 0.0, 0.0, 0.0, 1.2)  # on the flight path itself, looking along it
        behind = trails.script([(3.5, at_lens)], {7: rows}, {7: .1}, {7: (255, 255, 255)}, .02, 60, 1920, 1080)
        self.assertNotIn("Dialogue:", behind)
        # A trail is cut off a picture's width outside the picture: a point near the camera's own plane lands miles
        # off the screen, and libass draws an outline into a bitmap as large as the box round it.
        beside = (60.0, 1000.0, 300.0 * 3.5 - 200, 0.0, 0.0, 0.0, 1.2)  # just off the flight path, looking along it
        near = trails.script([(3.5, beside)], {7: rows}, {7: 18}, {7: (255, 255, 255)}, .02, 60, 1920, 1080)
        def across(lines):  # the x of every point drawn, in pixels
            return [int(v) / 4 for line in lines if line.startswith("Dialogue:")
                    for v in [t for t in line.split("}")[1].split() if t not in "ml"][0::2]]
        self.assertTrue(across(near.splitlines()) and all(-1920 - 10 <= x <= 2 * 1920 + 10 for x in across(near.splitlines())))
        # The trail comes in ahead of the tip (here in the middle of the picture, flying to one side), so it is
        # whole at the tip itself: it reaches a metre past it, 11 px at this distance, and far back the other way.
        reach = across(firsts)
        self.assertTrue(8 < min(960 - min(reach), max(reach) - 960) < 16, (min(reach), max(reach)))
        self.assertFalse([e for e in events if ",0:00:00.01,0:00:00.03," in e])  # no camera, no trails
        self.assertTrue(all("\\1c&H0080FF&" in e for e in events))
        alphas = [int(e.split("\\1a&H")[1][:2], 16) for e in firsts[:seen]]  # one wingtip's, from the wingtip back
        self.assertEqual(alphas, sorted(alphas))  # older, more transparent
        with self.assertRaises(ValueError):
            trails.colour("red")

    def test_each_side_has_its_colour_and_a_lasting_trail_keeps_the_fight_in_the_sky(self):
        shots = [{"hero": 23, "target": 30}, {"hero": 30, "target": None}, {"hero": 23, "target": 45}]
        self.assertEqual(trails.paint(shots, "you", "them"), {23: "you", 30: "them", 45: "them"})
        lasting = trails.style_for(60)
        self.assertEqual(trails.style_for(trails.STYLE["seconds"]), trails.STYLE)
        self.assertGreater((1 - .5) ** lasting["fade"], .75)  # halfway back a lasting trail is still three quarters as bright
        # A minute of trail a point every 0.04 s, the newest 1.5 s at every kept step, fixed in film time.
        rows = [(n / 50, 0.0, 0.0, float(n), 0.0, 0.0, 0.0, 0.0, 0.0, 50.0) for n in range(4000)]
        times, edge = trails._samples(rows, 70.0, .02, lasting)
        self.assertEqual((times[0], times[-1], edge), (10.0, 70.0, 68.5))
        self.assertLess(len(times), 58.5 / .04 + 76 + 3)
        self.assertAlmostEqual(times[101] - times[100], .04)
        # With a colour for it, a third trail leaves the tip of the fin: behind the aircraft's middle and above it.
        left, right, fin = trails.wingtips((0, 1000, 0), 0, 0, 0, 16, (16.2, 4.5))
        self.assertEqual([round(v, 3) for v in fin], [0, 1004.5, -16.2])
        flying = [(t / 50, 0.0, 1000.0, 300 * t / 50, 0.0, 0.0, 0.0, 0.0, 0.0, 300.0) for t in range(200)]
        eye = (-400.0, 1000.0, 300.0 * 3.5, -math.pi / 2, 0.0, 0.0, 1.2)
        text = trails.script([(3.5, eye)], {7: flying}, {7: 18}, {7: (255, 255, 255)}, .02, 60, 1920, 1080, tail=(0, 255, 0))
        self.assertIn("\\1c&H00FF00&", text)
        later, _ = trails._samples(rows, 70.0 + 1 / 60, .02, lasting)
        self.assertEqual(set(t for t in times if 12 < t < 68) - set(later), set())  # the old part stays where it was

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_the_trail_filters_lay_a_light_line_over_the_picture(self):
        rows = [(t / 50, 0.0, 1000.0, 300 * t / 50, 0.0, 0.0, 0.0, 0.0, 0.0, 300.0) for t in range(0, 200)]
        eye = (-400.0, 1000.0, 300.0 * 3.5, -math.pi / 2, 0.0, 0.0, 1.2)
        folder = Path(self.temp.name) / "O'Brien"  # an apostrophe would end the quotes round the path
        folder.mkdir()
        ass = folder / "t.ass"
        ass.write_text(trails.script([(3.5, eye)], {7: rows}, {7: 18}, {7: (255, 255, 255)}, .02, 30, 320, 180), encoding="utf-8")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x203040:s=320x180:d=0.03:r=30",
                              "-vf", trails.filters(ass, 180), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        brightest = max(sum(raw[i:i + 3]) for i in range(0, len(raw), 3))
        self.assertGreater(brightest, 0x20 + 0x30 + 0x40 + 200)  # a trail lit the dark blue

    def trail_viewer(self, projects):
        """A viewer for trails: kept tracks of two aircraft over 12 s, and `project` answering where the camera is."""
        path = Path(self.temp.name) / "fight.ahftracks"
        write_kept(path, {19: [turn_row(t / 10, 0, 0) for t in range(0, 121)],
                          10: [(t / 10, 0, 500, 1000 + 250 * t / 10, 0, 0, 0, 0, 0, 250) for t in range(0, 121)]}, {19: "Pilot", 10: "Other"})
        def native(command, progress):
            if command.startswith("tracks ") and " keep " in command:
                return [f"tracks kept file {path}"]
            if command.startswith("tracks "):
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + i * step:.4f} {ident} {x} 200 {300 * (start + i * step):.1f} 0 0 0"  # both fly on, toward +z
                        for i in range(round((end - start) / step) + 1) for ident, x in ((19, 0), (10, 300))]
            if command.startswith("project "):
                parts = command.split()
                begin, end, step = map(float, parts[1:4])
                cameras = Path(parts[4]).read_text().splitlines()
                projects.append((begin, end, step, cameras, " " in parts[4] + parts[5]))
                times = [begin + n * step for n in range(round((end - begin) / step) + 1)] if end - begin > 1e-9 else [begin]
                Path(parts[5]).write_text("".join(f"eye {t:.2f} {n} 0 500 -400 0 0 0 1.2\n"  # behind them, looking +z
                                                  for t in times for n in range(1, len(cameras) + 1)))
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        return native

    def test_trails_ask_the_viewer_where_the_camera_is_for_every_frame_at_once_and_leave_no_script_behind(self):
        projects, scripts = [], []
        def trim(source, target, filters="", fps=None):
            name = filters.split("subtitles=filename='")[1].split("'")[0].replace("\\:", ":")
            scripts.append(Path(name).read_text(encoding="utf-8"))
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = self.trail_viewer(projects)
        # A camera on the airframe from the film's very first moment, and a moving camera for two.
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "id": "seat", "start": 0, "end": 1, "kind": "cockpit", "target": None},
                                                          {**SHOT, "id": "two", "start": 5, "end": 6}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", trim):
            self.run_action({"action": "export_cut", "quality": "draft", "apart": True, "effects": {"trails": {"seconds": 2.5}}})
        seat, two = projects
        # A camera that stands on its airframe is asked once, for every frame of a second at 30 a second, both ends,
        # and never before the film's start. The step is rounded down, so the last one is not past the end.
        self.assertEqual([round(v, 4) for v in seat[:3]], [0, 1, .0333])
        self.assertLessEqual(30 * seat[2], 1)
        self.assertTrue(len(seat[3]) == 1 and seat[3][0].startswith("mount 19 ") and "outside" not in seat[3][0])
        # The moving camera is placed on its curve at every frame, and those asked together as still ones; the files
        # have no space in their path.
        self.assertEqual([round(v, 4) for v in two[:3]], [5, 6, .0333])
        self.assertEqual(len(two[3]), 31)
        self.assertTrue(all(row.startswith("fixed ") and " to " not in row and len(row.split()) == 8 for row in two[3]))
        self.assertNotEqual(two[3][0], two[3][15])
        self.assertFalse(any(row[4] for row in projects))
        # More than 60 frames of a moving camera are asked 60 at a time; a last frame off the others' steps, alone.
        del projects[:]
        moving = next(shot for shot in self.desk.snapshot()["project"]["shots"] if shot["id"] == "two")
        times, done = [5 + n / 100 for n in range(70)] + [5.999], []
        eyes = self.desk._eyes(self.desk._camera(moving), times, moving, done.append)
        self.assertEqual([len(row[3]) for row in projects], [60, 10, 1])
        self.assertEqual(done, [60, 70, 71])  # said after each asking: a long shot's progress moves, and Cancel is heard
        # The viewer projects a tenth of a second at least: the few last frames are begun whole steps earlier.
        self.assertTrue(all(end - begin > .1 for begin, end, *_rest in projects))
        self.assertEqual([round(v, 4) for v in projects[1][:3]], [5.58, 5.69, .01])
        self.assertEqual([round(v, 4) for v in projects[2][:3]], [5.799, 5.999, .1])
        self.assertTrue(len(eyes) == 71 and all(eye == [0, 500, -400, 0, 0, 0, 1.2] for eye in eyes))
        self.assertTrue(all("Dialogue:" in script for script in scripts) and len(scripts) == 2)
        self.assertEqual(list(self.desk.media_root.glob("*.ass")), [])

    def test_a_render_that_fails_leaves_no_trail_script_and_long_trails_are_refused(self):
        def trim(source, target, filters="", fps=None):
            raise ValueError("ffmpeg could not cut the lead-in off the video: no room")
        self.desk.viewer.command = self.trail_viewer([])
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 5, "end": 6}, {**SHOT, "id": "two", "start": 8, "end": 9}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", trim):
            self.desk.start({"action": "export_cut", "quality": "draft", "apart": True, "effects": {"trails": True}, "expected_revision": 2})
            self.desk.worker.join(5)
        self.assertIn("no room", self.desk.snapshot()["job"]["error"])
        self.assertEqual(list(self.desk.media_root.glob("*.ass")), [])  # the second video's script went too
        with self.assertRaises(ValueError):  # the script grows with the video: a long one is asked for shot by shot
            self.desk._trails(PROJECT, [{**SHOT, "start": 0, "end": 121}], 960, 540, 30, {}, trails.STYLE)

    def test_a_look_a_little_ahead_is_sought_plainly_and_any_other_roughly(self):
        # A rough seek reopens the film whatever the hop (2.4 s measured); the plain one plays on from where the film
        # stands (0.1 s for 0.3 s ahead, 1.1 s for 20 s) and reopens it only to go back.
        commands, film = [], {"at": 10.0}
        def native(command, progress):
            commands.append(command)
            if command == "time":
                return [f"time {film['at']:.1f} length 60.0 paused 1"]
            if command.startswith("seek "):
                film["at"] = float(command.split()[1])
            if command.startswith("planes"):
                return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder']
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return []
        self.desk.viewer.command = native
        self.desk.data["now"] = 10
        self.desk.data["project"]["length"] = 100
        sought = lambda: [c for c in commands if c.startswith("seek ")][-1]
        for seconds, command in ((10.3, "seek 10.30000000"), (30.2, "seek 30.20000000"),  # ahead by 0.3 s, by all but 20 s
                                 (51, "seek 51.00000000 rough"),  # further ahead than that
                                 (50.7, "seek 50.70000000 rough"),  # back
                                 (50.75, "seek 50.75000000 rough")):  # ahead by less than the tenth the viewer tells its time in
            self.run_action({"action": "seek", "seconds": seconds, "rough": True})
            self.assertEqual(sought(), command)
        # Moved in the viewer's own window, the film is where the viewer says, not where the desk left it.
        film["at"] = 20.0
        self.run_action({"action": "seek", "seconds": 21, "rough": True})
        self.assertEqual(sought(), "seek 21.00000000")
        self.run_action({"action": "seek", "seconds": 5})  # asked for exactly, it is not asked of the viewer at all
        self.assertEqual((sought(), commands.count("time")), ("seek 5.00000000", 6))

    def test_a_render_sends_each_camera_where_its_aircraft_are_in_the_film(self):
        # Film Viewer refuses `mount <id>` and `at <id>` for an aircraft not in the film where it stands: just opened,
        # at 1 s, the opponent who joined later was refused and the render failed (17 Nov 2026). Here he joins at 4 s.
        commands, viewer = [], {"now": 1.0}
        def native(command, progress):
            commands.append(command)
            here = {19} | ({10} if viewer["now"] >= 4 else set())
            if command.startswith("seek "):
                viewer["now"] = float(command.split()[1])
                return [f"at {viewer['now']} in 0.1 s"]
            if command.startswith("planes"):
                return [f'plane {ident} "P{ident}" 0 0 0 0 0 0' for ident in sorted(here)]
            if command.startswith("tracks "):
                start, end, step = map(float, command.split()[1:4])
                times = [start + i * step for i in range(round((end - start) / step) + 1)]
                return [f"track {t:.4f} {ident} 0 500 {300 * t:.1f} 0 0 0" for t in times for ident in (19, 10) if ident == 19 or t >= 4]
            named = {int(ident) for ident in command.split()[4:] if ident.isdigit()} & {19, 10}  # mount 19 ... at 10
            if command.startswith("cam ") and named - here:
                raise ValueError(f"at {min(named - here)}: no aircraft with that id in this film")
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        self.desk.data.update(now=1, planes=[{"id": 19, "name": "Pilot"}])
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "shoulder", "start": 3, "end": 6}]})
        self.run_action({"action": "export_cut", "quality": "draft"})
        # A seek to the first moment with both of them in the film (and a tenth more), then its camera. Three seconds
        # ahead of where the film stands it is the plain seek, quicker there than the rough one.
        first = next(i for i, c in enumerate(commands) if c.startswith("cam "))
        seek = commands[first - 2].split()
        self.assertEqual((seek[0], len(seek), commands[first - 1]), ("seek", 2, "planes long"))
        self.assertTrue(4 < float(seek[1]) <= 4.6)
        self.assertEqual(len([c for c in commands if c.startswith("video ")]), 1)
        # Standing where they both are, nothing is sought.
        del commands[:]
        self.desk.save({"expected_revision": 2, "shots": [{**SHOT, "kind": "shoulder", "start": 3, "end": 6, "fov": 40}]})
        self.run_action({"action": "export_cut", "quality": "draft"})
        self.assertFalse(any(c.startswith("seek ") for c in commands))
        self.assertEqual(len([c for c in commands if c.startswith("video ")]), 1)

    def test_a_full_disk_is_not_a_lost_viewer_and_the_error_is_written_down(self):
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "cockpit", "target": None}]})
        with patch("director_core.shutil.copyfile", side_effect=OSError("disk full")):
            self.desk.start({"action": "export_cut", "quality": "draft", "expected_revision": 2})
            self.desk.worker.join(5)
        state = self.desk.snapshot()
        self.assertEqual((state["job"]["error"], state["connected"]), ("disk full", True))
        log = (self.desk.root / "errors.log").read_text(encoding="utf-8")
        self.assertIn("Traceback", log)
        self.assertIn("OSError: disk full", log)

    def test_a_long_render_is_given_longer_to_answer(self):
        waited = []
        def native(command, progress):
            if command.startswith("video "):
                waited.append(self.desk.viewer.patience)
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        self.desk.data["project"]["length"] = 100
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 10, "end": 70, "kind": "cockpit", "target": None},
                                                          {**SHOT, "id": "two", "start": 80, "end": 82, "kind": "cockpit", "target": None}]})
        self.run_action({"action": "export_cut", "quality": "draft", "apart": True})
        self.assertEqual((waited, self.desk.viewer.patience), ([1200, 600], 600))  # twenty times a minute, then as before

    def test_a_file_windows_will_not_let_go_leaves_the_plan_as_it_was(self):
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "cockpit", "target": None}]})
        self.run_action({"action": "export_cut", "quality": "draft"})
        def refuse(paths):
            raise OSError("Windows did not move the files to the Recycle Bin")
        with patch("director_core.to_recycle_bin", refuse), self.assertRaises(OSError):
            self.desk.forget({"expected_revision": 2, "shot_ids": ["one"], "files": True})
        state = self.desk.snapshot()
        self.assertEqual(([s["id"] for s in state["project"]["shots"]], len(state["renders"]), state["project"]["revision"]), (["one"], 1, 2))

    def test_what_has_no_recycle_bin_is_not_deleted_for_good(self):
        import ctypes
        video = Path(self.temp.name) / "video.mp4"
        video.write_bytes(b"film")
        with patch.object(ctypes.windll.kernel32, "GetDriveTypeW", return_value=4):  # a network drive
            with self.assertRaises(OSError) as refused:
                director_core.to_recycle_bin([video])
        self.assertIn("no Recycle Bin", str(refused.exception))
        self.assertTrue(video.exists())

    def test_a_viewer_older_than_patch_3_is_refused(self):
        self.desk.viewer.connect = lambda progress: "ahfilmclaude 5 port 48221"
        self.desk.start({"action": "connect"})
        self.desk.worker.join(3)
        self.assertIn("3.07 Patch 3", self.desk.snapshot()["job"]["error"])
        self.assertFalse(self.desk.snapshot()["connected"])
        # No Film Viewer to connect to is a state, not an error: the simple desk asks again every few seconds.
        self.assertFalse((self.desk.root / "errors.log").exists())

    def test_a_line_the_viewer_quoted_badly_is_left_out(self):
        self.assertEqual([p["id"] for p in plane_rows(['plane 19 "Pil\\" 0 0 0 0 0 0', 'plane 10 "Other" 1 2 3 0 0 0', "plane"])], [10])
        # `planes long` says what each one flies; a pilot may be called "model".
        rows = plane_rows(['plane 86 "fufu2" 1 2 3 0 0 0 recorder model "Me 163B" country Rooks', 'plane 7 "model" 1 2 3 0 0 0 model "La-7" country Knights'])
        self.assertEqual([(p["name"], p["recorder"], p["model"]) for p in rows], [("fufu2", True, "Me 163B"), ("model", False, "La-7")])
        def native(command, progress):
            if command.startswith("open "):
                return ["opened films/test.ahf length 20.0 s in 0.1 s"]
            if command.startswith("events "):
                return ['event 3.5 land "Pil\\" "ditched"', 'event 4.5 kill "you" "Other" 1 19 10', "2 events"]
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        self.desk.data["films"] = [{"path": "films/test.ahf", "name": "test.ahf", "mb": 1, "modified": 0}]
        self.run_action({"action": "open", "film": "films/test.ahf"})
        self.assertEqual([e["kind"] for e in self.desk.snapshot()["project"]["events"]], ["kill"])

    def assembly_desk(self):
        """Two shots, each rendered as a video of its own (the first twice), and once together in one video."""
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        seat = {**SHOT, "kind": "cockpit", "target": None}
        self.desk.save({"expected_revision": 1, "shots": [seat, {**seat, "id": "two", "start": 5, "end": 8}]})
        self.run_action({"action": "export_cut", "quality": "draft", "apart": True})
        self.run_action({"action": "export_cut", "quality": "final", "shot_ids": ["one"], "effects": {"look": True}})
        self.run_action({"action": "export_cut", "quality": "draft"})
        return self.desk.snapshot()["versions"]

    def test_a_shot_knows_its_own_videos_and_whether_its_camera_changed_since(self):
        found = self.assembly_desk()
        # Newest first; the video of both shots together is nobody's clip.
        self.assertEqual({ident: [(v["quality"], v["extras"], v["seconds"], v["changed"]) for v in videos] for ident, videos in found.items()},
                         {"one": [("final", ["look", "haze"], 2, False), ("draft", [], 2, False)], "two": [("draft", [], 3, False)]})
        plan = self.desk.snapshot()["project"]
        self.desk.save({"expected_revision": plan["revision"], "shots": [{**plan["shots"][0], "fov": 40}, plan["shots"][1]]})
        changed = self.desk.snapshot()["versions"]
        self.assertEqual(([v["changed"] for v in changed["one"]], [v["changed"] for v in changed["two"]]), ([True, True], [False]))
        # A record made before videos had ids is given one when the film is read again, once.
        file = self.desk._file("films/test.ahf", ".renders")
        rows = json.loads(file.read_text(encoding="utf-8"))
        file.write_text(json.dumps([{"file": row["file"], "shots": row["shots"]} for row in rows]), encoding="utf-8")
        again = self.desk._load_renders("films/test.ahf")
        self.assertTrue(all(len(row["id"]) == 32 for row in again))
        self.assertEqual(self.desk._load_renders("films/test.ahf"), again)

    def test_the_page_plays_a_shot_s_video_and_brings_its_music(self):
        found = self.assembly_desk()
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        server.desk = self.desk
        threading.Thread(target=server.serve_forever, daemon=True).start()
        def call(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse(); result = response.status, response.read()
            connection.close(); return result
        try:
            self.assertEqual(call("GET", "/media/video/" + found["two"][0]["id"]), (200, b"film"))
            self.assertEqual(call("GET", "/media/video/" + "0" * 32)[0], 404)
            status, body = call("POST", "/api/music", b"notes", {"X-Name": "My%20song.MP3"})
            kept = json.loads(body)
            self.assertEqual((status, kept["name"], kept["file"][-4:]), (200, "My song.MP3", ".mp3"))
            self.assertEqual(call("GET", "/media/music/" + kept["file"]), (200, b"notes"))
            self.assertEqual(call("POST", "/api/music", b"MZ", {"X-Name": "song.exe"})[0], 400)
            self.assertEqual(call("GET", "/media/music/..%2Fone.json")[0], 404)
            self.assertEqual(call("GET", "/media/assembly")[0], 404)  # none made yet
            # A waveform of a video on record or of kept music; the fakes hold no sound, so theirs is empty.
            self.assertEqual(call("GET", "/api/wave/video/" + found["two"][0]["id"]), (200, b'{"rate": 50, "peaks": []}'))
            self.assertEqual(call("GET", "/api/wave/music/" + kept["file"])[0], 200)
            self.assertEqual([call("GET", "/api/wave/video/" + "0" * 32)[0], call("GET", "/api/wave/music/..%2Fone.mp3")[0]], [404, 404])
        finally:
            server.shutdown(); server.server_close()

    def test_places_on_the_airframe_are_kept_per_aircraft_type(self):
        place = {"model": "La-7", "name": "Flaps", "right": 9, "up": 6, "ahead": -14, "turn": 0, "tilt": -20, "fov": 75, "roll": 0}
        self.assertEqual(self.desk.save_mounts({"mounts": [place]})["mounts"], [place])
        self.assertEqual(Desk(self.temp.name, self.viewer).data["mounts"], [place])  # kept across a restart
        for wrong in ({"mounts": "all"}, {"mounts": [{**place, "right": 500}]}, {"mounts": [{**place, "name": None}]}, {"mounts": [place] * 201}):
            with self.subTest(wrong=str(wrong)[:60]), self.assertRaises(ValueError):
                self.desk.save_mounts(wrong)
        self.assertEqual(self.desk.snapshot()["mounts"], [place])

    def test_an_assembly_is_checked_kept_and_made_while_the_desk_goes_on(self):
        found = self.assembly_desk()
        song = self.desk.keep_music("song.mp3", b"notes")
        assembly = {"clips": [{"shot": "two", "video": None, "start": .5, "end": 0, "join": "dissolve", "sound": 1},
                              {"shot": "one", "video": found["one"][1]["id"], "start": 0, "end": .25, "join": "cut", "sound": .5}],
                    "off": ["nine"], "music": {**song, "from": 20, "level": .4, "in": 1, "out": 3}, "sound": .8}
        saved = self.desk.save_assembly({"assembly": assembly})["assembly"]
        self.assertEqual(saved, assembly)
        self.assertEqual(json.loads(self.desk._file("films/test.ahf", ".assembly").read_text(encoding="utf-8")), saved)
        # A video that is gone gives way to the newest; what is not a rendered shot or kept music is refused.
        self.assertIsNone(self.desk.save_assembly({"assembly": {"clips": [{"shot": "one", "video": "f" * 32}]}})["assembly"]["clips"][0]["video"])
        for wrong in ({"clips": [{"shot": "nine"}]}, {"clips": [], "music": {"file": "../one.mp3"}}, {"clips": "all"}, {"clips": [{"shot": "one", "start": -1}]}):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                self.desk.save_assembly({"assembly": wrong})
        made = []
        def assemble(clips, music, target, width, height, fps):
            made.append((clips, music, (width, height, fps)))
            Path(target).write_bytes(b"assembled")
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.assemble", assemble):
            with self.assertRaises(ValueError):  # trimmed down to nothing
                self.desk.assemble({"assembly": {"clips": [{"shot": "one", "start": 1.9}]}})
            self.desk.assemble({"assembly": assembly, "quality": "draft", "fps": 60})
            deadline = time.monotonic() + 3
            while self.desk.snapshot()["assembling"]["running"] and time.monotonic() < deadline:
                time.sleep(.01)
        state = self.desk.snapshot()["assembling"]
        self.assertEqual((state["error"], Path(state["file"]).name, Path(state["file"]).read_bytes()), (None, "test assembly.mp4", b"assembled"))
        clips, music, size = made[0]
        self.assertEqual([(c["file"], c["start"], c["seconds"], c["join"], round(c["sound"], 3)) for c in clips],
                         [(found["two"][0]["file"], .5, 2.5, "dissolve", .8), (found["one"][1]["file"], 0, 1.75, "cut", .4)])
        self.assertEqual((music["file"], music["from"], size), (self.desk.root / "music" / song["file"], 20, (960, 540, 60)))

    def test_a_waveform_is_the_loudest_sample_of_each_step(self):
        pcm = struct.pack("<8h", 0, 100, -32768, 5, 16384, -16384, 0, 0)
        self.assertEqual(finishing.loudest(pcm, 4), [255, 128])
        self.assertEqual(finishing.loudest(pcm[:7], 3), [255])  # an odd byte at the end is left out
        self.assertEqual(finishing.peaks(Path(self.temp.name) / "none.mp4"), [])

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_a_waveform_follows_the_sound(self):
        # Half a second of a loud tone, then half a second of silence: 25 loud values a second, then 25 quiet ones.
        path = Path(self.temp.name) / "tone.m4a"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=1,volume='if(lt(t,.5),1,0)':eval=frame", str(path)], check=True)
        found = self.desk.wave(path)
        self.assertAlmostEqual(len(found), 50, delta=2)
        self.assertTrue(all(v > 20 for v in found[2:23]) and all(v < 3 for v in found[28:48]), found)
        self.assertEqual(self.desk.wave(path), found)  # kept: worked out once

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_clips_are_joined_by_cuts_and_dissolves_with_music_under_them(self):
        folder = Path(self.temp.name)
        for n, (size, rate) in enumerate((("160x90", 30), ("320x180", 60), ("160x90", 30))):  # clips need not match
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc2=s={size}:r={rate}:d=2", "-f", "lavfi", "-i", "sine=d=2",
                            "-c:v", "libx264", "-c:a", "aac", str(folder / f"clip{n}.mp4")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=220:d=30", str(folder / "song.wav")], check=True)
        clips = [{"file": folder / "clip0.mp4", "start": 0, "seconds": 2, "join": "dissolve", "sound": 1},
                 {"file": folder / "clip1.mp4", "start": .5, "seconds": 1.5, "join": "cut", "sound": .5},
                 {"file": folder / "clip2.mp4", "start": 0, "seconds": .5, "join": "dissolve", "sound": 1},  # too short to dissolve
                 {"file": folder / "clip0.mp4", "start": 1, "seconds": 1, "join": "cut", "sound": 1}]
        music = {"file": folder / "song.wav", "from": 5, "level": .4, "in": 1, "out": 2}
        total = finishing.assemble(clips, music, folder / "out.mp4", 160, 90, 30)
        self.assertAlmostEqual(total, 2 + 1.5 - .3 + .5 + 1)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames,width",
                                "-of", "csv=p=0", str(folder / "out.mp4")], capture_output=True, text=True).stdout.split()
        kind, width, rate, frames = probe[0].split(",")
        self.assertEqual((kind, width, rate), ("video", "160", "30/1"))
        self.assertAlmostEqual(int(frames), total * 30, delta=2)
        self.assertTrue(probe[1].startswith("audio"))
        self.assertTrue(all(clip["file"].exists() for clip in clips))  # the clips stay: they are the pilot's videos
        finishing.assemble(clips[:1], None, folder / "one.mp4", 160, 90, 30)  # one clip, no music
        self.assertTrue((folder / "one.mp4").stat().st_size > 1000)

    def test_the_desk_learns_how_this_pc_compares_with_the_page_s_estimate(self):
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "cockpit", "target": None}]})
        seen = []
        keep = self.desk._keep
        self.desk._keep = lambda *args: (seen.append(self.desk.snapshot()["job"]["expect"]), keep(*args))[1]
        self.run_action({"action": "export_cut", "quality": "final", "expect": 40})
        # The page's estimate is the job's; this render took no time at all, so the pace moves halfway to a quarter.
        self.assertEqual((seen, self.desk.snapshot()["pace"]), ([40], {"final": .625}))
        self.run_action({"action": "export_cut", "quality": "final", "expect": 40})
        self.assertEqual(self.desk.snapshot()["pace"], {"final": round(.625 * .625, 3)})
        self.assertEqual(Desk(self.temp.name, self.viewer).data["pace"], self.desk.snapshot()["pace"])  # kept with the desk
        self.run_action({"action": "export_cut", "quality": "4k"})  # no estimate, nothing learned
        self.assertEqual(list(self.desk.snapshot()["pace"]), ["final"])

    def test_the_observer_glides_between_keyframes_and_can_fly_a_path_of_its_own(self):
        # Through keyframes a value is held before the first and after the last, passes each one, and does not halt
        # at one on the way (the same numbers as the page's own, in ui/src/keyframes.test.mjs).
        self.assertEqual([round(_glide([2, 4, 7], [0, 10, 4], t), 4) for t in (1, 2, 3, 4, 5.5, 7, 9)], [0, 0, 4.8, 10, 7.3, 4, 4])
        turning = {"keys": [{"t": 0, "orbit": 170, "distance": 100, "height": 0, "fov": 40}, {"t": 2, "orbit": -170, "distance": 300, "height": 50, "fov": 60}]}
        self.assertEqual([framed(turning, 1)[name] for name in ("orbit", "distance", "height", "fov")], [180, 200, 25, 50])  # the short way round
        # A hero in a steady turn with another aircraft beside it, 12 s of tracks half a second apart.
        def spot(t, off):
            return (2000 * math.cos(t * .15) + off, 3000 + off / 10, 2000 * math.sin(t * .15), t * .15, 0, .5)
        tracks = {t / 2: {19: spot(t / 2, 0), 10: spot(t / 2 + 1.5, 300)} for t in range(25)}
        at = sorted(tracks).index
        ship = {**SHOT, "kind": "escort", "start": 0, "end": 12, "orbit": 30, "distance": 400, "height": 50, "fov": 40, "loose": 3}
        wide = {"orbit": -120, "distance": 900, "height": 300, "fov": 70}
        keyed = validate_shot({**ship, "keys": [{"t": 9, **wide}, {"t": 3, **{name: ship[name] for name in wide}}, {"t": 40, **wide}]}, 60)
        self.assertEqual([key["t"] for key in keyed["keys"]], [3, 9])  # in order; the one outside the shot went with the trim
        self.assertNotIn("keys", validate_shot({**SHOT, "keys": keyed["keys"], "path": "own", "loose": 5}, 60))  # the observer's alone
        self.assertNotIn("path", validate_shot({**ship, "path": "own"}, 60))  # a path of its own needs a place to pin
        place = lambda poses, t: tuple(round(v, 6) for v in poses[at(t)][:3])
        plain, far, moved = (escort_poses(validate_shot(shot, 60), tracks) for shot in (ship, {**ship, **wide}, keyed))
        # Up to its first keyframe the ship sits as that one says, from its last on as the last says, between them on the way.
        self.assertEqual([place(moved, t) for t in (0, 3)], [place(plain, t) for t in (0, 3)])
        self.assertEqual([place(moved, t) for t in (9, 12)], [place(far, t) for t in (9, 12)])
        self.assertNotIn(place(moved, 6), (place(plain, 6), place(far, 6)))
        self.assertAlmostEqual(moved[at(12)][6], math.radians(70), places=2)  # the lens too
        # On a path of its own the keyframes pin it to the sky: it stands at the first pin until then, flies a line
        # of its own to the last and stays there, whatever the aircraft do. One pin is a camera standing still.
        own = escort_poses(validate_shot({**keyed, "path": "own"}, 60), tracks)
        self.assertEqual({place(own, t) for t in (0, 1.5, 3)}, {place(moved, 3)})
        self.assertEqual({place(own, t) for t in (9, 10.5, 12)}, {place(moved, 9)})
        self.assertGreater(math.dist(place(own, 6), place(moved, 6)), 50)
        still = escort_poses(validate_shot({**ship, "path": "own", "keys": [{"t": 6, **wide}]}, 60), tracks)
        self.assertEqual(len({place(still, t) for t in sorted(tracks)}), 1)
        self.assertTrue(all(" curve" in line for line in compile_shot(validate_shot({**keyed, "path": "own"}, 60), tracks)))
        # Looser, it flies the average of more of the hero's path: round a turn, a shorter way.
        flown = lambda seconds: (lambda poses: sum(math.dist(a[:3], b[:3]) for a, b in zip(poses, poses[1:])))(
            escort_poses(validate_shot({**ship, "loose": seconds}, 60), tracks))
        self.assertLess(flown(8), flown(1))

    def test_a_keyframe_says_whom_the_observer_watches_from_there(self):
        # The hero flies straight on, the other aircraft 800 ft ahead, to the right and above: 12 s, half a second apart.
        tracks = {t / 2: {19: (0, 1000, 150 * t, 0, 0, 0), 10: (300, 1100, 150 * t + 800, 0, 0, 0)} for t in range(25)}
        at = sorted(tracks).index
        ship = {**SHOT, "kind": "escort", "start": 0, "end": 12, "orbit": 30, "distance": 300, "height": 20, "fov": 20, "roll": 0}
        frame = {name: ship[name] for name in ("orbit", "distance", "height", "fov")}
        turned = validate_shot({**ship, "keys": [{"t": 3, **frame, "watch": "hero"}, {"t": 6, **frame, "watch": "target"}, {"t": 9, **frame}]}, 60)
        self.assertEqual([key.get("watch") for key in turned["keys"]], ["hero", "target", None])  # the last one: the shot's own, both
        for refused in ({**ship, "keys": [{"t": 3, **frame, "watch": "nobody"}]},
                        {**ship, "watch": "hero", "target": None, "keys": [{"t": 3, **frame, "watch": "both"}]}):
            with self.assertRaises(ValueError):
                validate_shot(refused, 60)
        poses = escort_poses(turned, tracks)
        hero, target, both = (escort_poses(validate_shot({**ship, "watch": who}, 60), tracks) for who in ("hero", "target", "both"))
        same = lambda t, whole: self.assertEqual([round(v, 4) for v in poses[at(t)][:5]], [round(v, 4) for v in whole[at(t)][:5]])
        # Up to its first keyframe it is the ship that watches the hero alone, at the second the one that watches
        # the target, from the third on the one that holds both: seat and look, and its lens open again.
        for t, whole in ((0, hero), (3, hero), (6, target), (9, both), (12, both)):
            same(t, whole)
        self.assertAlmostEqual(poses[at(12)][6], both[at(12)][6], places=4)
        # On the way from one to the next it is at neither seat, never nearer the hero than both seats are, and its
        # picture turns without a jerk.
        self.assertGreater(min(math.dist(poses[at(4.5)][:3], whole[at(4.5)][:3]) for whole in (hero, target)), 50)
        self.assertGreater(min(math.dist(pose[:3], tracks[t][19][:3]) for pose, t in zip(poses, sorted(tracks))), 295)
        rate = [math.degrees((b[3] - a[3] + math.pi) % (2 * math.pi) - math.pi) / .5 for a, b in zip(poses, poses[1:])]
        self.assertLess(max(abs(b - a) for a, b in zip(rate, rate[1:])), 25)  # degrees a second, from one half second to the next
        # The viewer holds an aircraft in the middle only where the ship watches it alone at both ends of a move.
        held = ["hero" if " at 19 curve" in line else "target" if " at 10 curve" in line else "" for line in compile_shot(turned, tracks)]
        self.assertEqual(held, ["hero"] * 6 + [""] * 18)
        # A tripod: the ship pinned where it is at 6 s, watching the hero alone, its lens keeping him the size the
        # framed lens shows from the framed distance. It follows by turning and zooming only.
        tripod = validate_shot({**ship, "watch": "hero", "orbit": 90, "path": "own", "zoom": "hold", "keys": [{"t": 6, **frame, "orbit": 90}]}, 60)
        stood = escort_poses(tripod, tracks)
        self.assertEqual(len({tuple(round(v, 6) for v in pose[:3]) for pose in stood}), 1)
        sizes = [math.tan(pose[6] / 2) * math.dist(pose[:3], tracks[t][19][:3]) for pose, t in zip(stood, sorted(tracks))
                 if pose[6] > math.radians(6.01)]
        self.assertGreater(len(sizes), 8)
        self.assertAlmostEqual(min(sizes), max(sizes), places=6)
        self.assertAlmostEqual(sizes[0], math.tan(math.radians(20) / 2) * 300, places=6)
        # At 6 s he is near; at the start 1,800 ft off, further than its longest lens (6 degrees) holds him that big.
        self.assertEqual([round(math.degrees(stood[at(t)][6]), 1) for t in (0, 6)], [6, 20])
        self.assertNotIn("zoom", validate_shot({**SHOT, "zoom": "hold"}, 60))  # the observer's alone

    def test_playing_in_the_viewer_is_paused_from_the_page_and_goes_on_from_the_playhead(self):
        commands, clock = [], [0.0]
        def native(command, progress):
            commands.append(command)
            if command.startswith("seek "):
                clock[0] = float(command.split()[1])
            if command.startswith("wait "):
                clock[0] += .1
                if clock[0] >= 2.45 and "seek 2.50000000" not in commands:
                    self.desk.halt.set()  # the page's Pause, while the stretch plays the first time
                return [f"waited, time {clock[0]:.2f}"]
            return []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "cockpit", "target": None, "start": 1, "end": 9}]})
        self.run_action({"action": "live", "start": 1, "end": 9, "shot_id": "one"})
        # Stopped where it was, with the viewer paused; the page sees the moment and can go on from it.
        self.assertEqual((commands[-1], round(self.desk.snapshot()["now"], 2)), ("pause", 2.5))
        commands.clear()
        self.run_action({"action": "live", "start": 1, "end": 9, "from": 2.5})
        self.assertEqual(([c for c in commands if c.startswith("seek ")][0], commands[-1], round(self.desk.snapshot()["now"], 1)), ("seek 2.50000000", "pause", 9))

    def test_take_offs_and_touchdowns_are_found_in_the_filmer_s_path(self):
        # Parked, a roll, a lift-off at 18 s, a flight, a landing at 60 s that stops at 64 s; then a second sortie
        # (a new id) that never leaves the ground.
        alt = lambda t: 2134.4 + (0 if t <= 18 else min(500, (t - 18) * 40) if t < 40 else max(0, (60 - t) * 25) - 3.5)
        x = lambda t: 0 if t <= 8 else sum(min(150, 20 * (u - 8)) if u < 60 else max(0, 180 - 45 * (u - 60)) for u in range(9, int(t) + 1))
        path = [(float(t), 88, x(t), alt(t), 0.0) for t in range(1, 70)] + [(float(t), 90, 0.0, 300.0, 0.0) for t in range(70, 80)]
        found = ground_moments(path)
        self.assertEqual([(m["kind"], m["time"], m["hero"]) for m in found], [("takeoff", 18.0, 88), ("touchdown", 60.0, 88)])
        self.assertEqual((found[0]["roll"], found[1]["stop"], found[1]["ground"]), (8.0, 64.0, 2130.9))  # roll: the last still moment
        # The filmer is whoever sits at the recorded cockpit's eye; while the view is outside, the last one found.
        lines = ["track 1 88 0 100 0 0 0 0", "track 1 7 50 100 0 0 0 0", "view 1 internal 1 103 0 0 0 0 rel 0 0 0 0 0 0",
                 "track 2 88 9 100 0 0 0 0", "track 2 7 2 100 0 0 0 0", "view 2 external rel 0 0 0 0 0 0"]
        self.assertEqual(filmer_path(lines), [(1.0, 88, 0.0, 100.0, 0.0), (2.0, 88, 9.0, 100.0, 0.0)])

    def test_a_camera_ship_can_swing_round_its_hero_and_close_in(self):
        tracks = {t: {19: (0, 100, 50 * t, 0, 0, 0)} for t in range(5)}
        plain = {**SHOT, "kind": "escort", "watch": "hero", "target": None, "orbit": 90, "distance": 200, "height": 10}
        swung = validate_shot({**plain, "sweep": 90, "closer": 100}, 20)
        self.assertEqual((swung["sweep"], swung["closer"]), (90, 100))
        self.assertNotIn("sweep", validate_shot(plain, 20))  # a shot without them is saved as before
        before, after = escort_poses(validate_shot(plain, 20), tracks), escort_poses(swung, tracks)
        self.assertEqual(before[0][:3], after[0][:3])  # it starts where the plain one stands
        gap = lambda pose, t: math.dist(pose[:3], (0, 100, 50 * t))
        self.assertAlmostEqual(gap(after[-1], 4), math.hypot(100, 10), delta=1)
        self.assertGreater(abs(after[-1][0] - before[-1][0]) + abs(after[-1][2] - before[-1][2]), 50)

    def test_airfield_is_found_in_the_whole_film_as_it_was_read_when_opened(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return ["track 1 88 0 100 0 0 0 0", "view 1 internal 0 103 0 0 0 0 rel 0 0 0 0 0 0"]
            return []
        self.desk.viewer.command = native
        self.desk.data["project"]["length"] = 1300
        with patch.object(self.desk, "progress"):
            self.desk._filmable([], 1300)  # as a film is opened
        self.assertEqual([c for c in commands if c.startswith("tracks")],
                         ["tracks 0.00 600.00 1", "tracks 600.00 1200.00 1", "tracks 1200.00 1300.00 1"])
        self.assertEqual(self.desk.own, [(1.0, 88, 0.0, 100.0, 0.0)])
        del commands[:]
        self.run_action({"action": "airfield"})
        self.assertEqual(commands, [])  # the film is not read a second time
        self.assertEqual(self.desk.snapshot()["airfield"], {"film": "films/test.ahf", "moments": []})

    def test_a_sequence_previews_as_the_cut_renders_it(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"clip")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        row = [{**SHOT, "id": "a", "start": 2, "end": 4, "kind": "cockpit"}, {**SHOT, "id": "b", "start": 9, "end": 11, "kind": "gear"}]
        self.desk.save({"expected_revision": 1, "shots": row + [{**SHOT, "id": "c", "start": 2, "end": 4, "kind": "nose"}]})
        self.run_action({"action": "clip", "shot_ids": ["b", "a"]})
        self.assertIn("skip 4.00000000 9.00000000", commands)
        self.assertFalse(any(c.startswith("cam ") and " 19 " in c and "3.2" in c for c in commands))  # not the other take
        self.assertTrue(next(c for c in commands if c.startswith("video ")).startswith("video 2.00000000 11.00000000 "))
        preview = self.desk.snapshot()["preview"]
        self.assertEqual((preview["kind"], preview["start"], preview["end"]), ("clip", 2, 11))
        self.desk.start({"action": "clip", "shot_ids": ["a", "c"], "expected_revision": 2}); self.desk.worker.join(2)
        self.assertIn("follow one another", self.desk.snapshot()["job"]["error"])

    def test_a_camera_on_the_airframe_turns_and_tilts(self):
        seat = validate_shot({**SHOT, "kind": "cockpit", "turn": 30, "tilt": -10}, 20)
        self.assertEqual(compile_shot(seat, {})[0], "cam 1 2 mount 19 0 0 0 0.523599 -0.174533 0 1.04719755")
        gear = compile_shot(validate_shot({**SHOT, "kind": "gear", "target": None, "turn": 30}, 20), {})[0]
        self.assertIn(" 1.2236 0.14 ", gear)  # its own 0.7 rad, and 30 degrees more to the left
        with self.assertRaises(ValueError):
            validate_shot({**SHOT, "kind": "cockpit", "tilt": 95}, 20)

    def test_live_plays_in_the_viewer_window(self):
        commands, clock = [], iter([3.0, 5.0, 7.0])
        def native(command, progress):
            commands.append(command)
            if command.startswith("wait "):
                return [f"waited, time {next(clock)}"]
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 2, "end": 6, "kind": "cockpit"}]})
        self.run_action({"action": "live", "start": 2, "end": 6})
        self.assertEqual([c for c in commands if c.split()[0] in ("seek", "play", "pause")], ["seek 2.00000000", "play", "pause"])
        self.assertEqual(len([c for c in commands if c.startswith("wait ")]), 3)  # until the film reached the end
        self.assertEqual(self.desk.snapshot()["now"], 7.0)

    def test_a_film_ends_inside_the_length_the_viewer_tells(self):
        # The viewer rounds a film's length to 0.1 s and has no aircraft paths past the real end: the desk's film
        # ends 0.05 s sooner, and a shot saved up to the told length ends with it.
        def native(command, progress):
            if command.startswith("open "):
                return ["opened films/test.ahf length 20.0 s in 0.1 s"]
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        self.desk.data["films"] = [{"path": "films/test.ahf", "name": "test.ahf", "mb": 1, "modified": 0}]
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 15, "end": 20}]})
        self.run_action({"action": "open", "film": "films/test.ahf"})
        project = self.desk.snapshot()["project"]
        self.assertEqual((project["length"], project["shots"][0]["end"]), (19.95, 19.95))

    def test_preview_clip_shows_an_unsaved_angle_and_is_rendered_once(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return [f"track {t} {ident} {x} 200 0 0 0 0"
                        for t in (1, 1.5, 2, 2.5, 3) for ident, x in ((19, 0), (10, 300))]
            if command.startswith(("shot ", "video ")):
                Path(command.split()[-4 if command.startswith("video ") else -1]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        saved = {**SHOT, "id": "kept", "start": 5, "end": 7, "kind": "cockpit"}
        self.desk.save({"expected_revision": 1, "shots": [saved, {**SHOT, "id": "old", "start": 2, "end": 4, "kind": "cockpit"}]})
        ask = {"action": "clip", "start": 1, "end": 3, "shot": SHOT, "still": 2.5}
        self.run_action(ask)
        # The unsaved draft replaces the saved shot it overlaps; the rest of the plan stays.
        cams = [c for c in commands if c.startswith("cam ")]
        self.assertEqual(len(cams), 5)
        self.assertFalse(any(c.startswith("cam 2 2 mount") for c in cams))
        self.assertLess(commands.index("seek 2.50000000 rough"), commands.index(next(c for c in commands if c.startswith("video "))))
        first = self.desk.snapshot()["preview"]
        self.assertEqual((first["kind"], first["start"], first["end"]), ("clip", 1, 3))
        self.assertTrue((self.desk.media_root / "clips" / Path(first["url"]).name).exists())
        self.assertEqual(self.desk.snapshot()["project"]["shots"][0]["id"], "old")  # nothing was saved
        del commands[:]
        self.run_action({**ask, "shot": {**SHOT, "name": "Renamed"}})
        self.assertEqual(commands, [])  # straight from its file
        self.assertEqual(self.desk.snapshot()["preview"]["url"], first["url"])
        self.run_action({**ask, "shot": {**SHOT, "side": "left"}})
        self.assertNotEqual(self.desk.snapshot()["preview"]["url"], first["url"])
        # Asked bare (it will be played in the viewer's own window): the cameras and a still, nothing rendered.
        del commands[:]
        self.run_action({**ask, "shot": {**SHOT, "side": "right", "distance": 77}, "bare": True})
        self.assertTrue(any(c.startswith("cam ") for c in commands))
        self.assertFalse(any(c.startswith("video ") for c in commands))
        self.assertEqual(self.desk.snapshot()["preview"]["kind"], "image")
        self.assertFalse(any(c.startswith("tracks ") for c in commands))  # another angle on the same stretch
        self.desk.start({**ask, "shot": {**SHOT, "target": 77}, "expected_revision": 2})
        deadline = time.monotonic()+2
        while self.desk.snapshot()["job"]["running"] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn("Aircraft 77", self.desk.snapshot()["job"]["error"])

    def test_cameras_on_the_airframe_and_on_the_bomb(self):
        base = validate_shot({**SHOT, "fov": 66}, 20)
        self.assertEqual(compile_shot({**base, "kind": "shoulder"}, {}), ["cam 1 2 mount 19 1.4 3.6 -2.5 0.0000 -0.07 0.00000000 1.15191731 at 10 outside"])
        self.assertEqual(compile_shot({**base, "kind": "gear", "side": "left"}, {}), ["cam 1 2 mount 19 -9 -6 -6 -0.7000 0.14 0.00000000 1.15191731 outside"])
        self.assertIn("mount 19 0 3.2 16 3.1416 -0.09", compile_shot({**base, "kind": "nose"}, {})[0])
        self.assertEqual(compile_shot({**base, "kind": "weapon"}, {}), ["cam 1 2 weapon 19"])
        self.assertEqual(compile_shot({**base, "kind": "wing", "height": 3}, {}), ["cam 1 2 mount 19 24 3 -6 0 0 0.00000000 1.15191731 at 10 outside"])
        self.assertEqual(probe_cameras({**base, "kind": "weapon"}, {}), [])
        self.assertEqual(probe_cameras({**base, "kind": "belly"}, {}), [(None, "mount 19 0 -4.5 -6 0.0000 0.09 0.00000000 1.15191731")])
        self.assertEqual(validate_shot({**SHOT, "kind": "weapon", "target": None}, 20)["kind"], "weapon")
        self.assertEqual(compile_shot({**base, "kind": "recorded"}, {}), ["cam 1 2 recorded"])
        # Anywhere on the airframe: where it sits and where it looks are the shot's own.
        free = validate_shot({**SHOT, "kind": "airframe", "target": None, "right": 9, "up": 6, "ahead": -14, "tilt": -20, "fov": 75}, 20)
        self.assertEqual(compile_shot(free, {}), ["cam 1 2 mount 19 9 6 -14 0.0000 -0.3491 0.00000000 1.30899694 outside"])
        with self.assertRaises(ValueError):
            validate_shot({**free, "ahead": -400}, 20)
        # Behind the tail and fastened to the airframe, so a loop or a roll cannot swing it round to the side.
        behind = {**base, "kind": "follow", "target": None, "orbit": 0, "distance": 80, "height": 16}
        self.assertEqual(compile_shot(behind, {}), ["cam 1 2 mount 19 0.00 16 -80.00 0.0000 -0.0974 0.00000000 1.15191731 outside"])
        self.assertIn("mount 19 80.00 16 -0.00 1.5708", compile_shot({**behind, "orbit": 90}, {})[0])
        # A shot saved before cameras could stand anywhere keeps its place: a side becomes degrees round the aircraft.
        self.assertEqual([validate_shot({**SHOT, **old}, 20)["orbit"] for old in ({}, {"side": "left"}, {"kind": "follow", "side": "rear"}, {"kind": "fixed", "side": "left"})], [25, -25, 0, -90])
        self.assertEqual(validate_shot({**SHOT, "kind": "follow", "side": "top"}, 20)["side"], "right")

    def test_a_camera_change_at_the_playhead_sends_one_camera_and_does_not_move_the_film(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return [f"track {9 + i / 2} {ident} {x} 200 0 0 0 0" for i in range(7) for ident, x in ((19, 0), (10, 300))]
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command.startswith("planes") else []
        self.desk.viewer.command = native
        draft = {**SHOT, "start": 9, "end": 12}
        self.run_action({"action": "seek", "seconds": 10, "rough": True, "shot": draft})  # the desk is at 10 s
        self.assertEqual([c.split()[0] for c in commands], ["tracks", "cams", "cam", "cam", "cams", "shot"])
        # Only the pieces of the moving camera around the playhead: a seek may have landed a little late.
        self.assertTrue(commands[2].startswith("cam 9.5 0.5") and commands[3].startswith("cam 10 0.5"))
        del commands[:]
        self.run_action({"action": "seek", "seconds": 10, "rough": True, "shot": {**draft, "orbit": 40}})
        self.assertEqual([c.split()[0] for c in commands], ["cams", "cam", "cam", "cams", "shot"])
        # Anywhere else the film is moved, with the whole plan in the viewer again.
        self.run_action({"action": "seek", "seconds": 11, "rough": True, "shot": draft})
        self.assertIn("seek 11.00000000", commands)  # a second ahead: the plain seek
        self.assertEqual(len([c for c in commands if c.startswith("cam ")]), 2 + 6)  # the whole plan: the shot's six pieces
        # Asked to wait, the desk answers with the finished state: one round trip per look.
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        server.desk = self.desk
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            connection.request("POST", "/api/action", json.dumps({"action": "seek", "seconds": 11, "shot": draft, "expected_revision": 1, "wait": True}))
            response = connection.getresponse()
            state = json.loads(response.read())
            connection.close()
            self.assertEqual((response.status, state["job"]["running"], state["preview"]["kind"]), (200, False, "image"))
            # The same state a poll gives: without its version the page took the desk for a release until the next poll.
            self.assertEqual((state["version"], state["stale"]), (director_api.VERSION, False))
        finally:
            server.shutdown(); server.server_close()

    def test_who_is_around_the_lead_aircraft(self):
        tracks = {1: {19: (0, 200, 0, 0, 0, 0), 10: (300, 200, 400, 0, 0, 0), 7: (0, 200, 9000, 0, 0, 0)},
                  2: {19: (100, 200, 0, 0, 0, 0), 10: (100, 200, 120, 0, 0, 0), 7: (0, 200, 9000, 0, 0, 0)},
                  3: {10: (0, 0, 0, 0, 0, 0)}}  # the lead is gone here: nothing to measure
        self.assertEqual(nearby(tracks, 19), [{"id": 10, "closest": 120, "typical": 310}, {"id": 7, "closest": 9000, "typical": 9000}])
        commands = []
        def native(command, progress):
            commands.append(command)
            return [f"track {t} {ident} {x} 200 {z} 0 0 0" for t, rows in tracks.items() for ident, (x, _alt, z, *_rest) in rows.items()] if command.startswith("tracks ") else []
        self.desk.viewer.command = native
        self.desk.data["planes"] = [{"id": 10, "name": "YORK"}]
        self.run_action({"action": "around", "start": 1, "end": 3, "hero": 19})
        self.run_action({"action": "around", "start": 1, "end": 3, "hero": 19})
        self.assertEqual(len(commands), 1)  # the stretch is read once, also for the cameras that follow
        found = self.desk.snapshot()["nearby"]
        self.assertEqual((found["hero"], found["aircraft"][0]), (19, {"id": 10, "closest": 120, "typical": 310, "name": "YORK", "model": None}))
        self.assertIsNone(found["aircraft"][1]["name"])

    def test_flight_data_of_a_level_turn_and_of_two_aircraft(self):
        # A right turn at 300 ft/s banked 60 degrees: 2 G, 10.6 degrees a second, a radius of 1,615 ft. Yaw grows to
        # the left, so a right turn makes it smaller; a positive roll is a right bank.
        rate = flight_data.G * math.tan(math.radians(60)) / 300
        turn = [turn_row(t / 10, -rate, 60) for t in range(40)]
        samples = flight_data.derive(turn, .1)["samples"][5:-5]
        for row in samples:
            self.assertAlmostEqual(row["g"], 2.0, delta=.02)
            self.assertAlmostEqual(row["turn"], math.degrees(rate), delta=.1)
            self.assertAlmostEqual(row["radius"], 300 / rate, delta=5)
            self.assertEqual((row["tas"], row["climb"]), (300, 0))
            self.assertAlmostEqual(row["power"], 0, delta=.5)
        left = [turn_row(t / 10, rate, -60) for t in range(40)]
        self.assertAlmostEqual(flight_data.derive(left, .1)["samples"][20]["g"], 2.0, delta=.02)
        # A far aircraft's speed is told every 2 s and held between: its numbers are marked coarse.
        held = [(*row[:7], *turn[int(row[0] // 2) * 20][7:]) for row in turn]
        self.assertEqual(flight_data.derive(held, .1)["quality"], "coarse")
        self.assertEqual(flight_data.derive(turn, .1)["quality"], "fine")
        # Two aircraft flying north (+z), the target 1,000 ft ahead and 50 ft/s slower; then coming head-on.
        hero = [(t, 0, 500, 300 * t, 0, 0, 0, 0, 0, 300) for t in (0, .1)]
        ahead = [(t, 0, 500, 1000 + 250 * t, 0, 0, 0, 0, 0, 250) for t in (0, .1)]
        first = flight_data.pair(hero, ahead)[0]
        self.assertEqual({k: first[k] for k in ("range", "closure", "off_nose", "off_tail", "crossing", "behind")},
                         {"range": 1000, "closure": 50, "off_nose": 0, "off_tail": 0, "crossing": 0, "behind": True})
        facing = [(t, 0, 500, 1000 - 250 * t, math.pi, 0, 0, 0, 0, -250) for t in (0, .1)]
        first = flight_data.pair(hero, facing)[0]
        self.assertEqual((first["closure"], first["off_tail"], first["behind"]), (550, 180, False))

    def test_kept_tracks_are_read_and_checked(self):
        path = Path(self.temp.name) / "fight.ahftracks"
        write_kept(path, {19: [turn_row(t / 10, 0, 0) for t in range(5)]}, {19: "Pilot"})
        kept = flight_data.read_kept(path)
        self.assertEqual((kept["step"], kept["aircraft"][19]["name"], len(kept["tracks"][19])), (.1, "Pilot", 5))
        self.assertEqual(kept["tracks"][19][2][9], 300)
        path.write_bytes(path.read_bytes()[:150])
        with self.assertRaises(ValueError):
            flight_data.read_kept(path)
        with self.assertRaises(ValueError):
            flight_data.read_kept(Path(self.temp.name) / "fight.bmp")

    def test_flight_of_a_stretch_is_read_once_and_served_whole(self):
        path = Path(self.temp.name) / "fight.ahftracks"
        write_kept(path, {19: [turn_row(t / 10, 0, 0) for t in range(9, 31)],
                          10: [(t / 10, 0, 500, 1000 + 250 * t / 10, 0, 0, 0, 0, 0, 250) for t in range(9, 31)]},
                   {19: "Pilot", 10: "Other"})
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return [f"tracks kept 1 to 3 step 0.1 speed double: 21 snapshots 0.0 MB in 0.1 s file {path}"]
            return []
        self.desk.viewer.command = native
        self.run_action({"action": "flight", "start": 1, "end": 3, "hero": 19, "target": 10})
        self.run_action({"action": "flight", "start": 1, "end": 3, "hero": 10, "target": 19})
        self.assertEqual(commands, ["tracks 0.90000000 3.00000000 0.0200000000 keep speed on pos double"])
        state = self.desk.snapshot()["flight"]
        self.assertEqual((state["hero"], sorted(row["name"] for row in state["aircraft"])), (10, ["Other", "Pilot"]))
        whole = self.desk.flight_data()
        self.assertEqual(len(whole["aircraft"]["19"]["samples"]), 21)
        self.assertEqual((whole["aircraft"]["19"]["samples"][0]["t"], whole["pair"][0]["range"]), (1, 1250))
        self.desk.start({"action": "flight", "start": 1, "end": 3, "hero": 7, "expected_revision": 1})
        self.desk.worker.join(2)
        self.assertIn("not in the film", self.desk.snapshot()["job"]["error"])

    def test_shots_are_removed_with_or_without_their_videos(self):
        def native(command, progress):
            if command.startswith("tracks "):
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + i * step:.4f} {ident} {x} 200 0 0 0 0" for i in range(round((end - start) / step) + 1)
                        for ident, x in ((19, 0), (10, 300))]
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"test")
            return []
        self.desk.viewer.command = native
        plan = [SHOT, {**SHOT, "id": "two", "start": 5, "end": 7}, {**SHOT, "id": "three", "start": 9, "end": 11}]
        self.desk.save({"expected_revision": 1, "shots": plan})
        self.run_action({"action": "export_cut", "shot_ids": ["one", "two"], "quality": "draft", "apart": True})
        self.run_action({"action": "export_cut", "shot_ids": ["three"], "quality": "draft"})
        # Each finished video is on record with its shots, beside the plan, and read again with the film.
        renders = self.desk.snapshot()["renders"]
        self.assertEqual([r["shots"] for r in renders], [["one"], ["two"], ["three"]])
        self.assertEqual(self.desk._load_renders("films/test.ahf"), renders)
        recycled = []
        with patch("director_core.to_recycle_bin", lambda paths: [recycled.extend(paths), *(p.unlink() for p in paths)]):
            kept = self.desk.forget({"expected_revision": 2, "shot_ids": ["two"], "files": False})
            self.assertEqual(([s["id"] for s in kept["project"]["shots"]], len(kept["renders"]), recycled), (["one", "three"], 3, []))
            Path(renders[0]["file"]).unlink()  # deleted by hand: nothing to move, nothing to fail on
            gone = self.desk.forget({"expected_revision": 3, "shot_ids": ["one", "three"], "files": True})
        self.assertEqual(([s["id"] for s in gone["project"]["shots"]], gone["recycled"]), ([], 1))
        self.assertEqual([p.name for p in recycled], [Path(renders[2]["file"]).name])
        self.assertEqual([r["shots"] for r in self.desk.snapshot()["renders"]], [["two"]])  # kept when its shot went alone
        with self.assertRaises(Conflict):
            self.desk.forget({"expected_revision": 3, "shot_ids": ["two"]})
        with self.assertRaises(ValueError):
            self.desk.forget({"expected_revision": 4, "shot_ids": []})

    def test_shots_keep_when_they_were_made_and_last_changed_and_their_place(self):
        with patch("director_core.time.time", return_value=1000):
            shots = self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "id": "two", "start": 5, "end": 7}, SHOT]})["shots"]
        self.assertEqual([(s["id"], s["created"], "modified" in s) for s in shots], [("one", 1000, False), ("two", 1000, False)])
        # A copy brings the first one's times along: it is a shot of its own, made now, last of its stretch.
        with patch("director_core.time.time", return_value=2000):
            second = self.desk.save({"expected_revision": 2, "shots": [*shots, {**shots[0], "id": "take", "height": 120}]})["shots"]
        self.assertEqual([(s["id"], s["created"]) for s in second], [("one", 1000), ("take", 2000), ("two", 1000)])
        # Changed and sent last (as Update shot does), the first keeps its place and is stamped; the others are not.
        with patch("director_core.time.time", return_value=3000):
            third = self.desk.save({"expected_revision": 3, "shots": [second[1], second[2], {**second[0], "height": 90}]})["shots"]
        self.assertEqual([(s["id"], s["created"], s.get("modified")) for s in third], [("one", 1000, 3000), ("take", 2000, None), ("two", 1000, None)])
        # The page's times are not taken, and they change no camera.
        forged = self.desk.save({"expected_revision": 4, "shots": [{**s, "created": 5, "modified": 6} for s in third]})["shots"]
        self.assertEqual(forged, third)
        self.assertEqual(camera_key(third[0]), camera_key({k: v for k, v in third[0].items() if k not in ("created", "modified")}))
        # One saved before the desk kept the times is older than a new take of its stretch.
        self.desk.data["project"]["shots"] = [{k: v for k, v in s.items() if k != "created"} for s in forged]
        later = self.desk.save({"expected_revision": 5, "shots": [{**forged[2], "id": "late"}, *self.desk.data["project"]["shots"]]})["shots"]
        self.assertEqual([s["id"] for s in later], ["one", "take", "two", "late"])
        # The effects the page gives a shot saved before shots kept them do not make it changed.
        effects = self.desk.save({"expected_revision": 6, "shots": [{**s, "effects": {"blur": True}} for s in later]})["shots"]
        self.assertEqual([s.get("modified") for s in effects], [s.get("modified") for s in later])

    def test_a_video_made_again_can_take_the_place_of_the_last(self):
        def native(command, progress):
            if command.startswith("tracks "):
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + i * step:.4f} {ident} {x} 200 0 0 0 0" for i in range(round((end - start) / step) + 1)
                        for ident, x in ((19, 0), (10, 300))]
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"test")
            return []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [SHOT]})
        self.run_action({"action": "export_cut", "shot_ids": ["one"], "quality": "draft"})
        self.run_action({"action": "export_cut", "shot_ids": ["one"], "quality": "draft"})  # not asked to: kept beside it
        beside = self.desk.snapshot()["renders"]
        self.assertEqual(len({r["file"] for r in beside}), 2)
        recycled = []
        with patch("director_core.to_recycle_bin", lambda paths: [recycled.extend(paths), *(p.unlink() for p in paths)]):
            self.run_action({"action": "export_cut", "shot_ids": ["one"], "quality": "final", "replace": True})
        after = self.desk.snapshot()["renders"]
        self.assertEqual([p.name for p in recycled], [Path(beside[1]["file"]).name])  # the last one only
        self.assertEqual([(r["id"], r["file"]) for r in after], [(r["id"], r["file"]) for r in beside])  # its id and its name
        self.assertEqual([r["quality"] for r in after], ["draft", "final"])
        self.assertEqual(self.desk._load_renders("films/test.ahf"), after)
        # Held by Windows (open in an editor): the old one stays, and the new one is kept beside it.
        def refuse(paths):
            raise OSError("in use")
        with patch("director_core.to_recycle_bin", refuse):
            self.run_action({"action": "export_cut", "shot_ids": ["one"], "quality": "draft", "replace": True})
        self.assertEqual(len(self.desk.snapshot()["renders"]), 3)
        self.assertEqual(self.desk.snapshot()["preview"]["files"][0]["stayed"], beside[1]["file"])

    def test_moments_whose_aircraft_are_not_in_the_film_are_left_out(self):
        # Aircraft 19 is in the film throughout, 10 only up to 5 s, 30 and 36 never.
        self.desk.viewer.command = lambda command, progress: [f"track {t}.0 {ident} 0 200 0 0 0 0" for t in range(11) for ident in (19, 10) if ident == 19 or t <= 5]
        self.desk.progress = lambda message: None  # no job is running here
        def event(time, kind, *fields):
            return {"id": "x", "time": time, "kind": kind, "fields": list(fields)}
        events = [event(3, "kill", "Pilot", "Other", "1", "19", "10"), event(9, "kill", "Pilot", "Other", "1", "19", "10"),
                  event(4, "kill", "LTown", "Trapshtr", "1", "36", "30"), event(4, "drop", "30", "bomb", "B100", "2"),
                  event(4, "drop", "19", "bomb", "B100", "-"), event(4, "land", "Pilot")]
        kept = self.desk._filmable(events, 10)
        self.assertEqual([(e["time"], e["kind"], e.get("alone", False)) for e in kept],
                         [(3, "kill", False), (9, "kill", True), (4, "drop", False), (4, "land", False)])
        self.desk.viewer.command = lambda command, progress: []  # no paths read: nothing is left out
        self.assertEqual(len(self.desk._filmable(events, 10)), 6)

    def test_a_replay_goes_to_the_recycle_bin_but_not_the_open_one(self):
        folder = Path(self.temp.name) / "films"
        folder.mkdir()
        for name in ("old.ahf", "test.ahf"):
            (folder / name).write_bytes(b"film")
        self.desk.films_folder = folder
        self.desk.data["films"] = [{"path": "films/old.ahf", "name": "old.ahf", "mb": 1.0, "modified": 0},
                                   {"path": "films/test.ahf", "name": "test.ahf", "mb": 1.0, "modified": 0}]
        moved = []
        with patch("director_core.to_recycle_bin", lambda paths: [moved.extend(paths), *(p.unlink() for p in paths)]):
            with self.assertRaises(ValueError):  # only what the viewer listed
                self.desk.delete_film({"film": "films/../../director_api.py"})
            left = self.desk.delete_film({"film": "films/old.ahf"})
            self.assertEqual(([p.name for p in moved], [f["name"] for f in left["films"]]), (["old.ahf"], ["test.ahf"]))
            # The open one, which Film Viewer holds, is marked instead; asked again, the mark is taken off.
            self.assertEqual(self.desk.delete_film({"film": "films/test.ahf"})["waiting"], ["films/test.ahf"])
            self.assertEqual(self.desk.delete_film({"film": "films/test.ahf"})["waiting"], [])
            self.desk.delete_film({"film": "films/test.ahf"})
            self.assertEqual(Desk(self.temp.name, self.viewer).data["waiting"], ["films/test.ahf"])  # kept with the desk
            self.desk._let_go("films/test.ahf")  # opened again: still held
            self.assertEqual((sorted(p.name for p in folder.iterdir()), self.desk.data["waiting"]), (["test.ahf"], ["films/test.ahf"]))
            # It goes when the viewer is given another replay, which becomes the newest of the recent ones.
            (folder / "next.ahf").write_bytes(b"film")
            self.desk.data["films"].append({"path": "films/next.ahf", "name": "next.ahf", "mb": 1.0, "modified": 0})
            self.desk._let_go("films/next.ahf")
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["next.ahf"])
        self.assertEqual((self.desk.data["waiting"], self.desk.data["recent"], [f["name"] for f in self.desk.data["films"]]),
                         ([], ["films/next.ahf", "films/test.ahf"], ["next.ahf"]))

    def test_a_replay_is_renamed_with_its_saved_shots_while_the_viewer_holds_another(self):
        folder = Path(self.temp.name) / "films"
        folder.mkdir()
        for name in ("other.ahf", "test.ahf"):
            (folder / name).write_bytes(b"film")
        commands = []
        def native(command, progress):
            commands.append(command)
            if command == "films":
                return [f"folder {folder}", *(f"film films/{p.name} 1.0 MB" for p in sorted(folder.iterdir())), "2 films"]
            if command.startswith("open "):
                return [f"opened {command[5:]} length 20.0 s in 0.1 s"]
            return []
        self.desk.viewer.command = native
        self.desk._library()
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "kind": "cockpit", "target": None}]})
        self.desk._note("recent", ["films/test.ahf"])
        for refused in ("", "a/b", "other", "x" * 101):  # no name, a path, one that is taken, too long
            self.desk.start({"action": "rename", "film": "films/test.ahf", "name": refused, "expected_revision": self.desk.snapshot()["project"]["revision"]})
            while self.desk.snapshot()["job"]["running"]:
                time.sleep(.01)
            self.assertIsNotNone(self.desk.snapshot()["job"]["error"], refused)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["other.ahf", "test.ahf"])
        del commands[:]
        self.run_action({"action": "rename", "film": "films/test.ahf", "name": " My kill.ahf "})
        state = self.desk.snapshot()
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["My kill.ahf", "other.ahf"])
        self.assertEqual([c for c in commands if c.startswith("open ")], ["open films/other.ahf", "open films/My kill.ahf"])
        self.assertEqual((state["project"]["film"], [s["kind"] for s in state["project"]["shots"]], state["recent"]),
                         ("films/My kill.ahf", ["cockpit"], ["films/My kill.ahf"]))
        self.assertFalse(self.desk._file("films/test.ahf").exists())
        # One that is not open is renamed where it lies: the viewer is not asked to open anything.
        del commands[:]
        self.run_action({"action": "rename", "film": "films/other.ahf", "name": "Dull"})
        self.assertEqual((sorted(p.name for p in folder.iterdir()), [c for c in commands if c.startswith("open ")]), (["Dull.ahf", "My kill.ahf"], []))

    def test_the_observer_lets_go_of_an_aircraft_that_leaves_the_film(self):
        # He crosses ahead of the hero and leaves the film at 6 s (shot down). Held at his last place, he pulled the
        # lens open to 90 degrees (or, past the widest, snapped it back to the shot's own) and stopped the turn dead.
        shot = {**SHOT, "start": 0, "end": 12, "kind": "escort", "distance": 400, "height": -80, "orbit": -20, "fov": 9, "roll": 0}
        tracks = {}
        for i in range(25):
            t = i / 2
            tracks[t] = {19: (0, 1000, 300 * t, 0, 0, 0)}
            if t <= 6:
                tracks[t][10] = (600 - 200 * t, 1050, 300 * t + 400, 0, 0, 0)
        poses = escort_poses(shot, _steady(shot, tracks), None, _last_seen(shot, tracks))
        rate = [math.degrees((b[3] - a[3] + math.pi) % (2 * math.pi) - math.pi) / .5 for a, b in zip(poses, poses[1:])]
        lens = [math.degrees(p[6]) for p in poses]
        self.assertLess(max(abs(b - a) for a, b in zip(rate, rate[1:])), 6)  # degrees a second, from one half second to the next
        self.assertLess(max(a - b for a, b in zip(lens, lens[1:])), 5)
        self.assertAlmostEqual(lens[-1], 9, delta=.1)  # back on the shot's own lens

    def test_the_observer_eases_into_and_out_of_its_swing_when_the_two_pass_close_by(self):
        # Head on, 80 ft apart as they pass at 6 s: the line through both swings half round in half a second, and the
        # ship swings round the hero after it. With that line averaged once its swing began and ended at once (its
        # speed changed by 121 ft/s from one half second to the next); it gathers and loses its pace instead (74).
        tracks = {i / 2: {19: (0, 1000, 150 * i, 0, 0, 0), 10: (80, 1000, 3600 - 150 * i, math.pi, 0, 0)} for i in range(25)}
        shot = validate_shot({**SHOT, "start": 0, "end": 12, "kind": "escort", "distance": 400, "height": 48, "orbit": -20, "fov": 28, "roll": 0}, 60)
        poses = escort_poses(shot, tracks)
        speed = [math.dist(a[:3], b[:3]) / .5 for a, b in zip(poses, poses[1:])]
        self.assertGreater(max(speed), 500)  # it does swing round: the hero flies 300 ft/s
        self.assertLess(max(abs(b - a) for a, b in zip(speed, speed[1:])), 90)

    def test_a_moving_camera_s_points_are_where_the_aircraft_are_then_and_its_moves_meet(self):
        # The hero flies 300 ft/s along z, read every 0.02 s. A shot of 2.23 s has ten points a second (the viewer
        # carries the camera's turn straight from point to point, so they are close together), 0.097 s apart, between
        # snapshots: taken from the nearer snapshot, a point was up to 3 ft from where the aircraft is at its moment.
        def native(command, progress):
            if command.startswith("tracks "):
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + n * step:.4f} 19 0 1000 {300 * (start + n * step):.3f} 0 0 0" for n in range(round((end - start) / step) + 1)]
            return []
        self.desk.viewer.command = native
        self.desk.data["job"] = {"running": False}
        paths = self.desk._tracks(1001, 1003.23)
        self.assertEqual(len(paths), 24)
        self.assertLess(max(abs(rows[19][2] - 300 * t) for t, rows in paths.items()), .01)
        # Past 1,000 s a film time told with six digits was 5 ms off: each move now begins when the one before ends.
        tracks = {t: {19: rows[19], 10: (300, 1000, rows[19][2] + 500, 0, 0, 0)} for t, rows in paths.items()}
        moves = [line.split() for line in compile_shot(validate_shot({**SHOT, "kind": "escort", "start": 1001, "end": 1003.23}, 2000), tracks)]
        self.assertEqual([moves[0][1], len(moves)], ["1001", 23])
        for one, two in zip(moves, moves[1:]):
            self.assertAlmostEqual(float(one[1]) + float(one[2]), float(two[1]), places=9)
            self.assertEqual(one[12:19], two[4:11])  # and where

    def test_an_observer_that_rides_is_a_camera_on_the_airframe_it_watches(self):
        # The hero in a banked climbing turn, half a second apart; the ship watches him alone and is asked to ride.
        def spot(t, off):
            return (2000 * math.cos(t * .15) + off, 3000 + 20 * t, 2000 * math.sin(t * .15), t * .15 + math.pi / 2, .1, -.9)
        tracks = {t / 2: {19: spot(t / 2, 0), 10: spot(t / 2 + 1.5, 300)} for t in range(13)}
        ship = {**SHOT, "kind": "escort", "watch": "hero", "start": 0, "end": 6, "orbit": 90, "distance": 200, "height": 20, "fov": 30, "roll": 0}
        world = compile_shot(validate_shot(ship, 60), tracks)
        riding = validate_shot({**ship, "ride": True}, 60)
        self.assertNotIn("ride", validate_shot({**SHOT, "ride": True}, 60))  # the observer's alone
        lines = compile_shot(riding, tracks)
        self.assertTrue(all(line.split()[3:5] == ["mount", "19"] and line.endswith(" at 19 outside curve") for line in lines))
        self.assertTrue(all(" fixed " in line and " at 19 curve" in line for line in world))
        # Each seat is the world camera's own place, told in the airframe's axes: put back into the world it is that place.
        for (t, plane), ride, fixed in zip(sorted(tracks.items()), lines, world):
            seat, wanted = [float(v) for v in ride.split()[5:8]], [float(v) for v in fixed.split()[4:7]]
            self.assertAlmostEqual(math.hypot(*seat), math.dist(wanted, plane[19][:3]), places=2)
            self.assertEqual(_seat([*plane[19][:3]], plane[19]), [0, 0, 0])
        yaw, pitch = .3, .2  # ahead of an airframe is ahead, above it is up, whatever its bank
        nose = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        self.assertEqual([round(v, 6) for v in _seat([100 * v for v in nose], (0, 0, 0, yaw, pitch, 1.2))], [0, 0, 100])
        self.assertEqual([round(v, 6) for v in _seat((0, 100, 0), (0, 0, 0, 0, 0, 0))], [0, 100, 0])
        self.assertEqual([round(v, 6) for v in _seat((100, 0, 0), (0, 0, 0, 0, 0, 0))], [100, 0, 0])  # looking toward +z, +x is to the right
        # A ship pinned to the sky stays a camera in the world, and so does one after its aircraft has left the film.
        pinned = validate_shot({**ship, "ride": True, "path": "own", "keys": [{"t": 3, "orbit": 90, "distance": 200, "height": 20, "fov": 30}]}, 60)
        self.assertTrue(all(" fixed " in line for line in compile_shot(pinned, tracks)))
        gone = {t: ({19: rows[19]} if t <= 3 else {}) | {10: rows[10]} for t, rows in tracks.items()}
        self.assertEqual(["mount" in line for line in compile_shot(riding, gone)], [True] * 6 + [False] * 6)

    def test_the_data_overlay_card(self):
        hero = [{"t": 10 + i / 50, "tas": 605.8, "alt": 1460.3, "climb": 35.2, "g": 7.76} for i in range(101)]
        near = [{"t": 10 + i / 50, "range": 355, "closure": 376.4} for i in range(51)]  # he leaves the film at 11 s
        seg = {"start": 10, "end": 12, "hero": hero, "hero_name": "fu{fu}2", "pair": near, "target_name": "AAdecoy"}
        colour, matte = overlay.script("16x9", [seg]), overlay.script("16x9", [seg], matte=True)
        self.assertIn("PlayResX: 1920", colour)
        self.assertIn("YCbCr Matrix: None", colour)  # or libass shifts the colours
        self.assertIn("FU(FU)2 · SPEED", colour)  # braces would be read as styling
        first = colour.split("0:00:00.00,0:00:00.10")[1:]
        for value in ("}413{", "}1,460{", "}+2,110{", "}7.8", "}355{", "}257{"):
            self.assertTrue(any(value in line for line in first), value)
        self.assertIn("0:00:01.90,0:00:02.00", colour)
        late = "".join(line for line in colour.splitlines() if "0:00:01.50," in line)
        self.assertIn("}–{", late)  # the opponent gone: his numbers are a dash
        # The transparency pass draws the same in greys: the panel 58% (148), text and numbers whole (255).
        self.assertIn("\\1c&H949494&", matte)
        self.assertNotIn("&H18100C&", matte)
        self.assertIn("\\1c&H18100C&", colour)
        self.assertEqual((overlay.number(-0.13, 1), overlay.number(2110, sign=True), overlay.number(0, sign=True)), ("−0.1", "+2,110", "0"))
        # The reel's card is at the top and half as large again; without an opponent it has no bottom row.
        reel = overlay.script("9x16", [{**seg, "pair": None, "target_name": None}])
        self.assertIn("PlayResY: 1920", reel)
        self.assertIn("\\pos(64.0,260.0)", reel)
        self.assertNotIn("CLOSING", reel)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_the_data_overlay_film_is_prores_4444_with_transparency(self):
        hero = [{"t": i / 10, "tas": 300, "alt": 500, "climb": 0, "g": 1} for i in range(6)]
        target = Path(self.temp.name) / "with space" / "o.mov"
        target.parent.mkdir()
        overlay.render("16x9", [{"start": 0, "end": .5, "hero": hero, "hero_name": "Pilot"}], target, 216, 10)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,profile,width,height,pix_fmt,nb_frames,color_space",
                                "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.strip()
        self.assertEqual(probe, "prores,4444,384,216,yuva444p12le,bt709,5")

    def test_a_render_can_bring_its_data_overlays(self):
        path = Path(self.temp.name) / "fight.ahftracks"
        write_kept(path, {19: [turn_row(t / 10, 0, 0) for t in range(0, 121)],
                          10: [(t / 10, 0, 500, 1000, 0, 0, 0, 0, 0, 0) for t in range(0, 121)]}, {19: "Pilot", 10: "Other"})
        commands, made = [], []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks ") and " keep " in command:
                return [f"tracks kept file {path}"]
            if command.startswith("tracks "):  # the cameras' own read of the stretch
                start, end, step = map(float, command.split()[1:4])
                return [f"track {start + i * step:.4f} {ident} {x} 200 0 0 0 0" for i in range(round((end - start) / step) + 1)
                        for ident, x in ((19, 0), (10, 300))]
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"test")
            return []
        drawing, go_on = threading.Event(), threading.Event()
        def draw(layout, segments, target, height, fps):
            drawing.set()
            go_on.wait(2)
            made.append((layout, [(s["start"], s["end"], s["hero_name"], s["target_name"], len(s["hero"])) for s in segments], Path(target).name, height, fps))
            Path(target).write_bytes(b"overlay")
            return target
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [SHOT, {**SHOT, "id": "two", "start": 5, "end": 7, "target": None, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", lambda a, b, s, h=None, fps=None: Path(b).write_bytes(b"cut")), \
                patch("director_core.overlay.render", draw):
            self.run_action({"action": "export_cut", "quality": "final", "effects": {"overlay": True}})
            # The overlays are drawn while the desk goes on: the render's job is over, the viewer free.
            self.assertTrue(drawing.wait(2))
            state = self.desk.snapshot()
            self.assertFalse(state["job"]["running"])
            self.assertEqual((state["overlays"]["running"], state["overlays"]["left"]), (True, 2))
            self.assertIn("16:9 data overlay of test 00.01-00.07 cut of 2 shots", state["overlays"]["message"])
            go_on.set()
            self.desk.drawer.join(2)
        self.assertEqual(self.desk.snapshot()["overlays"], {"running": False, "message": "Data overlays ready", "left": 0, "error": None})
        # The flights are read before the first render, and each video gets a 16:9 and a 9:16 overlay of its shots.
        self.assertLess(max(i for i, c in enumerate(commands) if " keep " in c), min(i for i, c in enumerate(commands) if c.startswith("video ")))
        both = [(1, 3, "Pilot", "Other", 21), (5, 7, "Pilot", None, 21)]
        self.assertEqual(made, [("16x9", both, "test 00.01-00.07 cut of 2 shots - overlay 16x9.mov", 1080, 30),
                                ("9x16", both, "test 00.01-00.07 cut of 2 shots - overlay 9x16.mov", 1920, 30)])
        self.assertEqual(len(self.desk.snapshot()["preview"]["files"][0]["overlays"]), 2)
        with patch("director_core.shutil.which", return_value=None):
            self.desk.start({"action": "export_cut", "effects": {"overlay": True}, "expected_revision": 2})
            self.desk.worker.join(2)
        self.assertIn("needs ffmpeg", self.desk.snapshot()["job"]["error"])

    def test_a_second_desk_cannot_take_the_port_of_a_running_one(self):
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        try:
            with self.assertRaises(OSError):
                make_server(server.server_port, Path(self.temp.name) / "second", self.desk.media_root)
        finally:
            server.server_close()

    def test_a_desk_of_another_copy_gives_up_the_port_and_this_copy_s_own_keeps_it(self):
        # The server has no window: one started from another folder, an older version since deleted, kept the port
        # and the page came from it, "404 Not Found". Now it is stopped; this copy's own, or a busy one, stays.
        first = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        port = first.server_port
        threading.Thread(target=first.serve_forever, daemon=True).start()
        def stop(pid, sig):
            first.shutdown(); first.server_close()
        try:
            self.assertIsNone(take_port(port))  # it is this copy's: its page is the one
            with patch("director_api.running_desk", return_value={"job": {"running": True}}), patch("director_api.os.kill") as kill:
                self.assertIsNone(take_port(port))  # another copy's, busy rendering: left alone
                kill.assert_not_called()
            with patch("director_api.running_desk", return_value={"job": None}), patch("director_api.listener", return_value=4242),                     patch("director_api.os.kill", side_effect=stop) as kill,                     patch("director_api.make_server", side_effect=lambda p: make_server(p, Path(self.temp.name) / "new", self.desk.media_root)):
                second = take_port(port)
            self.assertEqual((kill.call_args.args[0], second.server_port), (4242, port))
            second.server_close()
            with patch("director_api.running_desk", return_value=None), self.assertRaises(OSError):
                held = make_server(port, Path(self.temp.name) / "held", self.desk.media_root)
                try:
                    take_port(port)  # something else holds the port: it is said, not stopped
                finally:
                    held.server_close()
        finally:
            first.server_close()
        rows = ("\r\n  Proto  Adres lokalny          Obcy adres             Stan\r\n"
                f"  TCP    127.0.0.1:{port}         0.0.0.0:0              NAS\u0141UCHIWANIE   5150\r\n"
                f"  TCP    127.0.0.1:{port}         127.0.0.1:50000        USTANOWIONO     5150\r\n"
                f"  TCP    127.0.0.1:50000        127.0.0.1:{port}         USTANOWIONO     777\r\n").encode("cp852")
        with patch("director_api.subprocess.run", return_value=subprocess.CompletedProcess([], 0, rows, b"")):
            self.assertEqual((listener(port), listener(port + 1)), (5150, None))

    def test_the_page_s_script_is_javascript_whatever_the_windows_registry_says(self):
        # Some programs make .js plain text in the registry, where Python looks: the browser ran no script, blank page.
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        script = next((director_api.ROOT / "ui" / "dist" / "assets").glob("*.js")).name
        try:
            with patch("director_api.mimetypes.guess_type", return_value=("text/plain", None)):
                for path, kind in ((f"/assets/{script}", "text/javascript"), ("/", "text/html; charset=utf-8")):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    connection.request("GET", path)
                    response = connection.getresponse()
                    response.read()
                    connection.close()
                    self.assertEqual((response.status, response.getheader("Content-Type")), (200, kind))
        finally:
            server.shutdown(); server.server_close()

    def test_working_folder_has_no_space_when_the_account_name_does(self):
        with patch("director_api.tempfile.gettempdir", return_value="C:/Users/John Smith/AppData/Local/Temp"):
            self.assertEqual(working_folder("C:/ah-quick-shots/.quickshots"), Path("C:/ah-quick-shots/.quickshots/media"))
            with patch.dict("os.environ", {"PUBLIC": "C:/Users/Public"}):
                self.assertEqual(working_folder("C:/My Games/ah-quick-shots/.quickshots"), Path("C:/Users/Public/AHQuickShots"))
        with patch("director_api.tempfile.gettempdir", return_value="C:/Temp"):
            self.assertEqual(working_folder("C:/any where/.quickshots"), Path("C:/Temp/AHQuickShots"))

    def test_finished_videos_have_a_folder_of_their_own_and_the_working_folder_is_tidied(self):
        # The videos folder may have spaces: output.txt names it, else it is in the account's Videos folder.
        root = Path(self.temp.name) / "settings"
        root.mkdir()
        with patch("director_api.Path.home", return_value=Path(self.temp.name)):
            self.assertEqual(videos_folder(root), Path(self.temp.name) / "AH Quick Shots")
        (root / "output.txt").write_text("D:/My Videos/Aces High\n", encoding="utf-8")
        self.assertEqual(videos_folder(root), Path("D:/My Videos/Aces High"))
        # A start clears the stills an earlier run left and all but the last few renders; clips are a cache.
        work = Path(self.temp.name) / "work"
        (work / "clips").mkdir(parents=True)
        for name in ["a.bmp", "b.bmp", "clips/kept.mp4", *[f"{n}.mp4" for n in range(9)]]:
            (work / name).write_bytes(b"test")
        Desk(Path(self.temp.name) / "plans", self.viewer, work)
        self.assertEqual((len(list(work.glob("*.bmp"))), len(list(work.glob("*.mp4"))), (work / "clips/kept.mp4").exists()), (0, 6, True))

    def test_film_library_lists_newest_first(self):
        lines = ["folder C:\\ah\\films", "film films/old.ahf 1.0 MB", "film films/new one.ahf 2.5 MB", "2 films"]
        dates = {"C:\\ah\\films/old.ahf": 10, "C:\\ah\\films/new one.ahf": 20}
        self.assertEqual([row["name"] for row in film_rows(lines, dates.__getitem__)], ["new one.ahf", "old.ahf"])
        self.assertEqual([row["path"] for row in film_rows(lines)], ["films/old.ahf", "films/new one.ahf"])

    def test_socket_waiting_message_keeps_reading_until_end(self):
        viewer = Viewer()
        client, peer = socket.socketpair()
        viewer.sock, viewer.reader = client, client.makefile("r", encoding="utf-8")
        peer.sendall(b"waiting for the user: terrain?\nopened film\n.end\n")
        progress = []
        self.assertEqual(viewer.command("open films/a.ahf", progress.append)[-1], "opened film")
        self.assertIn("terrain?", progress[0])
        viewer.close(); peer.close()

    def test_http_stays_responsive_during_seek_and_has_local_security_gate(self):
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        server.desk = self.desk
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def call(method, path, data=None, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
            connection.request(method, path, json.dumps(data) if data is not None else None, headers or {})
            response = connection.getresponse(); result = response.status, response.read()
            connection.close(); return result
        try:
            status, _ = call("POST", "/api/action", {"action": "seek", "seconds": 1, "expected_revision": 1})
            self.assertEqual(status, 202); self.assertTrue(self.viewer.entered.wait(1))
            before = time.monotonic(); status, body = call("GET", "/api/state")
            self.assertLess(time.monotonic()-before, .2)
            self.assertTrue(json.loads(body)["job"]["running"])
            # The desk says when its code on disk is no longer the code it runs, and does not restart mid-operation.
            self.assertFalse(json.loads(body)["stale"])
            self.assertEqual(json.loads(body)["version"], "source")  # no VERSION file: not a package
            self.assertLessEqual({director_api.ROOT / name for name in ("trails.py", "finishing.py", "overlay.py")}, set(director_api.CODE))
            server.code = [None]
            self.assertTrue(json.loads(call("GET", "/api/state")[1])["stale"])
            status, body = call("POST", "/api/restart", {})
            self.assertEqual(status, 400); self.assertIn("Wait", json.loads(body)["message"])
            self.assertEqual(call("POST", "/api/project", {"expected_revision": 1, "shots": []})[0], 409)
            self.assertEqual(call("POST", "/api/action", {}, {"Origin": "https://example.com"})[0], 403)
            self.assertEqual(call("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
            self.assertEqual(call("GET", "/media/../../director_api.py")[0], 404)
            media = self.desk.root / "media" / ("a"*32+".mp4"); media.write_bytes(b"0123456789")
            self.assertEqual(call("GET", "/media/"+media.name, headers={"Range": "bytes=2-4"}), (206, b"234"))
            (media.parent / "clips" / media.name).write_bytes(b"clip")
            self.assertEqual(call("GET", "/media/clips/"+media.name), (200, b"clip"))
            self.assertEqual(call("GET", "/media/clips/../"+media.name)[0], 404)
        finally:
            self.viewer.release.set(); server.shutdown(); server.server_close()

    def test_an_idle_desk_restarts_with_the_code_on_disk(self):
        server = make_server(0, Path(self.temp.name) / "http", self.desk.media_root)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        started = threading.Event()
        try:
            with patch("director_api.restart", lambda s: started.set()):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                connection.request("POST", "/api/restart", "{}")
                self.assertEqual(connection.getresponse().status, 202)
                connection.close()
                self.assertTrue(started.wait(1))
        finally:
            server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
