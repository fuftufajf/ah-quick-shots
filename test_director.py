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
from director_core import Conflict, Desk, framing, lead
import flight_data
import overlay
import trails
from shot_plan import _last_seen, _steady, camera_pose, compile_shot, escort_poses, filmer_path, ground_moments, nearby, passes, probe_cameras, validate_plan, validate_shot
from viewer_socket import Viewer, film_rows

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


class SlowViewer:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()

    def command(self, command, progress):
        if command.startswith("seek "):
            self.entered.set()
            progress("waiting for viewer")
            self.release.wait(3)
            return ["at 1.0 in 2.0 s"]
        if command == "planes":
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder']
        if command.startswith("shot "):
            Path(command[5:]).write_bytes(b"test")
            return ["saved"]
        return []


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.viewer = SlowViewer()
        self.desk = Desk(self.temp.name, self.viewer)
        self.desk.pace = 0  # no waiting for a viewer that is not there
        no_ffmpeg = patch("director_core.shutil.which", return_value=None)  # the fakes write no real video
        no_ffmpeg.start()
        self.addCleanup(no_ffmpeg.stop)
        self.desk.data.update(project=copy.deepcopy(PROJECT), connected=True, now=10)

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
        self.assertTrue(0 < wide(130) < 200 and wide(520) > 2 * wide(130))
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
                Path(parts[5]).write_text("\n".join(rows))
            return []
        self.desk.viewer.command = native
        self.run_action({"action": "suggest", "shots": [SHOT, {**SHOT, "id": "wing", "kind": "wing", "height": 3}]})
        self.assertEqual(self.desk.snapshot()["proposals"], [{"id": "one", "score": 1.0, "size": .05, "quality": .875},
                                                             {"id": "wing", "score": .6, "size": .05, "quality": .525}])
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
            if command == "planes":
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
        # The two seconds before the still play through its camera, so the aircraft in it are drawn solid.
        at = commands.index("seek 0.00000000 rough")
        self.assertEqual(commands[at:at + 6], ["seek 0.00000000 rough", "planes", "play", "wait 5", "pause", "seek 2.00000000"])
        self.assertFalse(self.desk.snapshot()["preview"]["fresh"])
        self.assertIn("cam 0 1.00000000 fixed -253.76618037 260.00000000 118.33311329 -1.89422331 -0.15832816 0.00000000 1.04719755", commands)
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
        self.assertTrue(commands[-1].startswith("video 1.00000000 11.00000000 ") and commands[-1].endswith(" 960 540 30"))
        self.assertNotIn("play", commands)  # playing just before a render takes the engine and guns out of its sound
        self.assertEqual(commands[-3:-1], ["seek 1.00000000", "planes"])  # from far off: a seek first, or its first second is silent
        self.run_action({"action": "icons", "on": True})
        self.assertIn("icons on", commands)
        self.assertTrue(self.desk.snapshot()["icons"])
        # Asked with a time, a switch answers with a still of that moment: seen at once, no render.
        self.run_action({"action": "icons", "on": False, "seconds": 6, "rough": True})
        self.assertEqual(commands[-9:-6], ["icons off", "seek 4.00000000 rough", "planes"])
        # A still too near the start to play up to it is taken while the aircraft are still fading in: the still
        # says so, and asked for again where the film already is, it is taken again without another jump.
        self.run_action({"action": "seek", "seconds": .2, "rough": True})
        self.assertTrue(self.desk.snapshot()["preview"]["fresh"])
        del commands[:]
        self.desk.jumped -= 5
        self.run_action({"action": "seek", "seconds": .2, "rough": True})
        self.assertFalse(any(c.startswith("seek ") for c in commands) or self.desk.snapshot()["preview"]["fresh"])
        self.assertEqual(self.desk.snapshot()["preview"]["kind"], "image")
        self.run_action({"action": "export_cut", "shot_ids": ["two"], "quality": "4k"})
        self.assertTrue(commands[-1].endswith(" 3840 2160 60"))
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
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [SHOT]})
        self.run_action({"action": "seek", "seconds": 2, "rough": True})
        self.assertIn("seek 2.00000000", commands)
        self.assertIn("Aircraft 10 is not in the film", self.desk.snapshot()["problems"]["one"])
        self.desk.start({"action": "export_cut", "expected_revision": 2})
        deadline = time.monotonic()+2
        while self.desk.snapshot()["job"]["running"] and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertIn("Aircraft 10", self.desk.snapshot()["job"]["error"])
        self.assertFalse(any(c.startswith("video ") for c in commands))

    def test_with_ffmpeg_a_video_starts_two_seconds_early_and_they_are_cut_off(self):
        commands, cuts = [], []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"lead")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        def ffmpeg(args, **_options):
            cuts.append(args)
            Path(args[-1]).write_bytes(b"cut")
            return subprocess.CompletedProcess(args, 0, "", "")
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 5, "end": 7, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.subprocess.run", ffmpeg):
            self.run_action({"action": "export_cut", "quality": "draft"})
        video = next(c for c in commands if c.startswith("video "))
        self.assertTrue(video.startswith("video 3.00000000 7.00000000 ") and "-lead.mp4" in video)
        self.assertEqual(cuts[0][cuts[0].index("-ss") + 1], "2.000000")
        self.assertFalse(Path(video.split()[3]).exists())  # the uncut video is gone
        self.assertEqual(Path(self.desk.snapshot()["preview"]["file"]).read_bytes(), b"cut")
        # ffmpeg failing is said, not hidden behind a video two seconds too long.
        failed = lambda args, **_options: subprocess.CompletedProcess(args, 1, "", "Unknown encoder 'libx264'")
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.subprocess.run", failed):
            self.desk.start({"action": "export_cut", "expected_revision": 2})
            self.desk.worker.join(3)
        self.assertIn("Unknown encoder", self.desk.snapshot()["job"]["error"])

    def test_motion_blur_renders_four_passes_a_quarter_frame_apart_and_blends_them(self):
        commands, blends = [], []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"fast")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        def blend(parts, target, seconds, fps, filters=""):
            blends.append(([p.exists() for p in parts], seconds, fps, filters))
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 3, "end": 5, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.blend", blend):
            self.run_action({"action": "export_cut", "quality": "final", "blur": True})
            self.run_action({"action": "export_cut", "quality": "4k", "blur": True, "look": True})
        videos = [c.split() for c in commands if c.startswith("video ")]
        self.assertEqual([(v[1], v[2], v[4:]) for v in videos[:4]], [(f"{1 + n / 480:.8f}", f"{5 + n / 480:.8f}", ["1920", "1080", "120"]) for n in range(4)])
        self.assertEqual(blends, [([True] * 4, 2, 60, ""), ([True] * 4, 2, 60, director_core.style(3840, 2160, film=True))])

    def test_the_film_look_goes_on_in_ffmpeg_even_with_no_lead_in_to_cut(self):
        trims = []
        def native(command, progress):
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"film")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        def trim(source, target, seconds, filters=""):
            trims.append((seconds, filters))
            Path(target).write_bytes(b"cut")
        self.desk.viewer.command = native
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 0, "end": 2, "kind": "cockpit"}]})
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", trim):
            self.run_action({"action": "export_cut", "quality": "draft", "look": True})
            self.run_action({"action": "export_cut", "quality": "draft", "haze": True, "look": True})
            self.run_action({"action": "export_cut", "quality": "draft"})
        # At the film's start there is no lead-in: only the looks need ffmpeg. The haze goes first, as in the grade.
        self.assertEqual(trims, [(0, director_core.look(540)), (0, director_core.haze(960, 540) + "," + director_core.look(540))])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
    def test_the_blend_keeps_the_film_s_length_at_its_own_frame_rate(self):
        parts, target = [Path(self.temp.name) / f"fast{n}.mp4" for n in range(4)], Path(self.temp.name) / "o.mp4"
        for part in parts:
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=120:d=1.5", "-f", "lavfi", "-i", "sine=d=1.5",
                            "-c:v", "libx264", "-c:a", "aac", str(part)], check=True)
        director_core.blend(parts, target, .5, 60, director_core.style(160, 90, True, True))  # the looks fit in its graph
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,r_frame_rate,nb_frames",
                                "-of", "csv=p=0", str(target)], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,60/1,60")
        self.assertTrue(probe[1].startswith("audio,"))
        self.assertFalse(any(p.exists() for p in parts))
        director_core.trim(target, parts[0], .5, director_core.style(160, 90, True, True))  # and as the lead-in is cut
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,nb_frames",
                                "-of", "csv=p=0", str(parts[0])], capture_output=True, text=True).stdout.split()
        self.assertEqual(probe[0], "video,30")

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
        before, after = picture(""), picture(director_core.haze(w, h))
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
        seen = sum(style["opacity"] * (1 - (b + .5) / style["bands"]) ** style["fade"] >= .01 for b in range(style["bands"]))
        self.assertEqual(len(firsts), 2 * seen)  # two wingtips, each band that is not too faint to see
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
        # A minute of trail through about 300 points, the newest 1.5 s at every kept step, fixed in film time.
        rows = [(n / 50, 0.0, 0.0, float(n), 0.0, 0.0, 0.0, 0.0, 0.0, 50.0) for n in range(4000)]
        times, edge = trails._samples(rows, 70.0, .02, lasting)
        self.assertEqual((times[0], times[-1], edge), (10.0, 70.0, 68.5))
        self.assertLess(len(times), 300 + 76 + 3)
        later, _ = trails._samples(rows, 70.0 + 1 / 60, .02, lasting)
        self.assertEqual(set(t for t in times if 12 < t < 68) - set(later), set())  # the old part stays where it was

    @unittest.skipUnless(shutil.which("ffmpeg"), "needs ffmpeg")
    def test_the_trail_filters_lay_a_light_line_over_the_picture(self):
        rows = [(t / 50, 0.0, 1000.0, 300 * t / 50, 0.0, 0.0, 0.0, 0.0, 0.0, 300.0) for t in range(0, 200)]
        eye = (-400.0, 1000.0, 300.0 * 3.5, -math.pi / 2, 0.0, 0.0, 1.2)
        ass = Path(self.temp.name) / "t.ass"
        ass.write_text(trails.script([(3.5, eye)], {7: rows}, {7: 18}, {7: (255, 255, 255)}, .02, 30, 320, 180), encoding="utf-8")
        raw = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x203040:s=320x180:d=0.03:r=30",
                              "-vf", trails.filters(ass, 180), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        brightest = max(sum(raw[i:i + 3]) for i in range(0, len(raw), 3))
        self.assertGreater(brightest, 0x20 + 0x30 + 0x40 + 200)  # a trail lit the dark blue

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

    def test_airfield_reads_the_whole_film_and_keeps_what_it_found(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("tracks "):
                return ["track 1 88 0 100 0 0 0 0", "view 1 internal 0 103 0 0 0 0 rel 0 0 0 0 0 0"]
            return []
        self.desk.viewer.command = native
        self.desk.data["project"]["length"] = 1300
        self.run_action({"action": "airfield"})
        self.assertEqual([c for c in commands if c.startswith("tracks")],
                         ["tracks 0.00 600.00 1", "tracks 600.00 1200.00 1", "tracks 1200.00 1300.00 1"])
        self.assertEqual(self.desk.snapshot()["airfield"], {"film": "films/test.ahf", "moments": []})

    def test_a_sequence_previews_as_the_cut_renders_it(self):
        commands = []
        def native(command, progress):
            commands.append(command)
            if command.startswith("video "):
                Path(command.split()[3]).write_bytes(b"clip")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
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

    def test_live_plays_in_the_viewer_window_and_the_next_render_waits(self):
        commands, clock = [], iter([3.0, 5.0, 7.0])
        def native(command, progress):
            commands.append(command)
            if command.startswith("wait "):
                return [f"waited, time {next(clock)}"]
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        self.desk.viewer.command = native
        self.desk.pace = 1
        self.desk.save({"expected_revision": 1, "shots": [{**SHOT, "start": 2, "end": 6, "kind": "cockpit"}]})
        before = time.monotonic()
        self.run_action({"action": "live", "start": 2, "end": 6})
        self.assertEqual([c for c in commands if c.split()[0] in ("seek", "play", "pause")], ["seek 2.00000000", "play", "pause"])
        self.assertEqual(len([c for c in commands if c.startswith("wait ")]), 3)  # until the film reached the end
        self.assertGreaterEqual(self.desk.quiet - before, 12)
        self.assertEqual(self.desk.snapshot()["now"], 7.0)

    def test_a_shot_s_first_camera_stands_in_the_seconds_before_it(self):
        self.assertEqual(lead("cam 5 2 fixed 1 2 3 4 5 6 7 to 8 9 10 11 12 13 14 curve", 3), ["cam 3 2.00000000 fixed 1 2 3 4 5 6 7"])
        self.assertEqual(lead("cam 5 2 mount 19 0 0 0 0 0 0 1 outside", 4.5), ["cam 4.5 0.50000000 mount 19 0 0 0 0 0 0 1 outside"])
        self.assertEqual(lead("cam 5 2 recorded", 3), [])
        self.assertEqual(lead("cam 5 2 mount 19 0 0 0 0 0 0 1", 4.98), [])

    def test_a_film_ends_inside_the_length_the_viewer_tells(self):
        # The viewer rounds a film's length to 0.1 s and has no aircraft paths past the real end: the desk's film
        # ends 0.05 s sooner, and a shot saved up to the told length ends with it.
        def native(command, progress):
            if command.startswith("open "):
                return ["opened films/test.ahf length 20.0 s in 0.1 s"]
            if command.startswith("shot "):
                Path(command[5:]).write_bytes(b"test")
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
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
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
        self.desk.viewer.command = native
        saved = {**SHOT, "id": "kept", "start": 5, "end": 7, "kind": "cockpit"}
        self.desk.save({"expected_revision": 1, "shots": [saved, {**SHOT, "id": "old", "start": 2, "end": 4, "kind": "cockpit"}]})
        ask = {"action": "clip", "start": 1, "end": 3, "shot": SHOT, "still": 2.5}
        self.run_action(ask)
        # The unsaved draft replaces the saved shot it overlaps; the rest of the plan stays.
        cams = [c for c in commands if c.startswith("cam ")]
        self.assertEqual(len(cams), 7)  # with each shot's camera standing in the two seconds before it
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
        self.assertFalse(any(c.startswith("tracks ") for c in commands))  # another angle on the same stretch
        # The viewer is still finishing a rendered stretch's sound for about as long as the stretch lasts: the next
        # render waits that out, or it would lose the engine, the guns and the pilots' voices.
        self.desk.pace = .1
        self.run_action({**ask, "shot": {**SHOT, "orbit": 35}})
        waiting = time.monotonic()
        self.run_action({**ask, "shot": {**SHOT, "orbit": 40}})
        self.assertGreater(time.monotonic() - waiting, .3)  # the 2 s stretch and a second and a half, at this pace
        self.desk.pace = 0
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
            return ['plane 19 "Pilot" 0 0 0 0 0 0 recorder'] if command == "planes" else []
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
        self.assertIn("seek 9.00000000 rough", commands)
        self.assertIn("seek 11.00000000", commands)
        self.assertEqual(len([c for c in commands if c.startswith("cam ")]), 2 + 1 + 6)  # the whole plan: a camera before the shot and its six pieces
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
        self.assertEqual((found["hero"], found["aircraft"][0]), (19, {"id": 10, "closest": 120, "typical": 310, "name": "YORK"}))
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
            with self.assertRaises(ValueError):  # the open one: Film Viewer holds it
                self.desk.delete_film({"film": "films/test.ahf"})
            with self.assertRaises(ValueError):  # only what the viewer listed
                self.desk.delete_film({"film": "films/../../director_api.py"})
            left = self.desk.delete_film({"film": "films/old.ahf"})
        self.assertEqual(([p.name for p in moved], [f["name"] for f in left["films"]]), (["old.ahf"], ["test.ahf"]))
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ["test.ahf"])

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
        with patch("director_core.shutil.which", return_value="ffmpeg"), patch("director_core.trim", lambda a, b, s, h=None: Path(b).write_bytes(b"cut")), \
                patch("director_core.overlay.render", draw):
            self.run_action({"action": "export_cut", "quality": "final", "overlay": True})
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
        self.assertEqual(made, [("16x9", both, "test 00.01-00.07 cut of 2 shots - overlay 16x9.mov", 1080, 60),
                                ("9x16", both, "test 00.01-00.07 cut of 2 shots - overlay 9x16.mov", 1920, 60)])
        self.assertEqual(len(self.desk.snapshot()["preview"]["files"][0]["overlays"]), 2)
        with patch("director_core.shutil.which", return_value=None):
            self.desk.start({"action": "export_cut", "overlay": True, "expected_revision": 2})
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
