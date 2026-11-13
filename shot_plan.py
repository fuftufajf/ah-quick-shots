"""Editable shots -> native camera commands, using the viewer's own tracks."""
import math
import statistics
import uuid

from viewer_socket import number, safe_text

# Cameras on the lead aircraft, from the viewer's `help director` table: right, up, ahead (feet from the
# pilot's eye), yaw, pitch (radians), for the right-hand side.
MOUNTS = {"nose": (0, 3.2, 16, math.pi, -.09), "shoulder": (1.4, 3.6, -2.5, 0, -.07),
          "belly": (0, -4.5, -6, 0, .09), "gear": (9, -6, -6, .7, .14)}
# pair, follow, fixed, ambush and escort stand anywhere around an aircraft: `orbit` degrees round it, `distance`
# and `height` feet. fixed stands still by the hero's path; ambush stands still by the target's, watching the
# target; escort is a camera ship flying its own line beside the two of them.
KINDS = ("pair", "follow", "fixed", "ambush", "escort", "wing", "cockpit", "weapon", "recorded", *MOUNTS)
WATCH = ("both", "hero", "target")  # whom a camera ship keeps in its picture
# Where a shot saved before `orbit` existed stood. `side` now only picks the wing, shoulder or gear leg.
SIDES = {"right": 90, "left": -90, "rear": 0, "front": 180, "top": 0}
# The camera travels with the lead aircraft (or is the pilot's own view): no tracks needed.
RIDES = ("follow", "wing", "cockpit", "weapon", "recorded", *MOUNTS)


def validate_shot(raw, length):
    if not isinstance(raw, dict):
        raise ValueError("A shot must be an object")
    kind, side = raw.get("kind", "pair"), raw.get("side", "right")
    if kind not in KINDS or side not in SIDES:
        raise ValueError("Choose a supported camera and side")
    shot = {"id": safe_text(raw.get("id") or uuid.uuid4().hex, 64),
            "name": safe_text(raw.get("name", "Untitled shot")),
            "kind": kind, "side": side if side in ("left", "right") else "right"}
    orbit = (-25 if side == "left" else 25) if kind == "pair" else SIDES[side]
    for key, default, low, high in [("start", 0, 0, length), ("end", 6, 0, length), ("orbit", orbit, -180, 180),
                                   ("distance", 280, 20, 10000), ("height", 60, -2000, 5000),
                                   ("fov", 60, 6, 140), ("roll", 0, -180, 180)]:
        shot[key] = number(raw.get(key, default), low, high, key)
    if shot["end"] - shot["start"] < .1 or shot["end"] - shot["start"] > 120:
        raise ValueError("A shot must last 0.1 to 120 seconds")
    for key in ("hero", "target"):
        value = raw.get(key)
        parsed = None if value in (None, "") else number(value, 0, 1000000, key)
        if parsed is not None and not parsed.is_integer():
            raise ValueError("Aircraft ids must be whole numbers")
        shot[key] = None if parsed is None else int(parsed)
    shot["watch"] = raw.get("watch", "both")
    if shot["watch"] not in WATCH:
        raise ValueError("A camera ship watches both aircraft, its hero or its target")
    two = shot["kind"] in ("pair", "ambush") or (shot["kind"] == "escort" and shot["watch"] != "hero")
    if shot["hero"] is None or shot["target"] == shot["hero"] or (two and shot["target"] is None):
        raise ValueError("Choose the lead aircraft and, for a shot of two, a different second aircraft")
    return shot


def track_rows(lines):
    result = {}
    for line in lines:
        if line.startswith("track "):
            row = line.split()
            result.setdefault(float(row[1]), {})[int(row[2])] = tuple(map(float, row[3:9]))
    return result


def nearby(tracks, hero, limit=8):
    """Who shares the sky with the lead aircraft in this stretch, nearest first: [{id, closest, typical}] in feet.
    A kill message or a stray hit names an aircraft; this says who was actually there."""
    gaps = {}
    for rows in tracks.values():
        if hero in rows:
            for ident, row in rows.items():
                if ident != hero:
                    gaps.setdefault(ident, []).append(math.dist(row[:3], rows[hero][:3]))
    found = [{"id": ident, "closest": round(min(gap)), "typical": round(statistics.median(gap))} for ident, gap in gaps.items()]
    return sorted(found, key=lambda row: row["closest"])[:limit]


def _aim(pos, target, fov, roll):
    dx, dy, dz = (target[i] - pos[i] for i in range(3))
    values = (*pos, math.atan2(-dx, dz), math.atan2(dy, math.hypot(dx, dz)), math.radians(roll), math.radians(fov))
    return list(values)


def _hold(pos, near, far, fov):
    """Where a camera at `pos` looks to show two aircraft, as yaw and pitch: half way from the near one toward
    the far one, but no further than keeps the whole near aircraft (25 ft from nose to wingtip) inside the
    picture. Aimed at the point half way between them instead, a long lens loses the near aircraft as soon as
    the far one is far. When even that turn cannot bring the far one in, the near one goes back to the middle."""
    across = math.radians(fov) / 2
    down = math.atan(math.tan(across) * 9 / 16)
    (yaw, pitch), (there_yaw, there_pitch) = (_aim(pos, there, 0, 0)[3:5] for there in (near, far))
    size = math.atan(25 / max(25, math.dist(pos, near[:3])))
    swing = (there_yaw - yaw + math.pi) % (2 * math.pi) - math.pi
    rise = there_pitch - pitch
    reach_yaw, reach_pitch = max(0, across - size) * .85, max(0, down - size) * .7
    lost = max(abs(swing) - reach_yaw - .9 * across, abs(rise) - reach_pitch - .9 * down)
    hold = max(0, 1 - max(0, lost) / .35)  # fades over 20 degrees, so the turn back is not a jerk
    return (yaw + hold * max(-reach_yaw, min(reach_yaw, swing / 2)),
            pitch + hold * max(-reach_pitch, min(reach_pitch, rise / 2)))


def _line(hero, target):
    """The way from the target through the hero, as a unit vector."""
    dx, dy, dz = (hero[i] - target[i] for i in range(3))
    norm = math.sqrt(dx * dx + dy * dy + dz * dz) or 1
    return dx / norm, dy / norm, dz / norm


def _beyond(shot, hero, line):
    """Where the camera for two stands: beyond the hero on the line through both aircraft, swung off it to
    separate the silhouettes."""
    ux, uy, uz = line
    horizontal = math.hypot(ux, uz) or 1
    turn = math.radians(shot["orbit"])
    along, across = math.cos(turn) * shot["distance"], math.sin(turn) * shot["distance"]  # across: to the right of that line
    return (hero[0] + ux * along + uz / horizontal * across, hero[1] + uy * along + shot["height"],
            hero[2] + uz * along - ux / horizontal * across)


def _last_seen(shot, tracks):
    """The last track time at which each of the shot's aircraft is still in the film."""
    return {ident: max((key for key in tracks if ident in tracks[key]), default=None)
            for ident in (shot["hero"], shot["target"]) if ident is not None}


def pair_poses(shot, tracks, times, seen, step=None):
    """The camera for two at each track time, each with the aircraft the viewer should hold in the middle from
    then on (None while the camera frames both itself).
    When one of the two leaves the film (shot down, landed) the line through both stops turning. Left to follow
    the place where that aircraft was last seen, it spins round as the other flies past that place and whips
    the camera with it (measured: 135 degrees in 2.5 s). If the far aircraft left, the camera flies on with the
    near one as it was. If the near one left, the camera stays where it is and turns, over a second, to the
    other: the viewer then holds that one, so it is watched flying past and away without a jerk."""
    poses, line = [], None
    for t in times:
        hero, target = tracks[t][shot["hero"]], tracks[t][shot["target"]]
        if line is None or (t <= seen[shot["hero"]] and t <= seen[shot["target"]]):
            line = _line(hero, target)
        pos = _beyond(shot, hero, line)
        yaw, pitch = _hold(pos, hero, target, shot["fov"])
        follow, since = None, (t - seen[shot["hero"]]) * (step or 1)
        if since > 0:
            there_yaw, there_pitch = _aim(pos, target, 0, 0)[3:5]
            share = min(1, since)
            yaw += share * ((there_yaw - yaw + math.pi) % (2 * math.pi) - math.pi)
            pitch += share * (there_pitch - pitch)
            follow = shot["target"] if share == 1 else None
        poses.append(([*pos, yaw, pitch, math.radians(shot["roll"]), math.radians(shot["fov"])], follow))
    return poses


def camera_pose(shot, rows):
    hero = rows.get(shot["hero"])
    target = rows.get(shot["target"])
    if hero is None or (shot["kind"] in ("pair", "ambush") and target is None):
        raise ValueError("A selected aircraft is missing during this shot. Shorten the interval or choose another aircraft.")
    x, y, z, yaw = (target if shot["kind"] == "ambush" else hero)[:4]
    d, height = shot["distance"], shot["height"]
    turn = math.radians(shot["orbit"])
    if shot["kind"] == "pair":
        pos = _beyond(shot, hero, _line(hero, target))
        return [*pos, *_hold(pos, hero, target, shot["fov"]), math.radians(shot["roll"]), math.radians(shot["fov"])]
    # Round the aircraft's own heading: 0 behind it, 90 off its right wing, 180 ahead.
    right, ahead = math.sin(turn) * d, -math.cos(turn) * d
    pos = (x + math.cos(yaw) * right - math.sin(yaw) * ahead, y + height,
           z + math.sin(yaw) * right + math.cos(yaw) * ahead)
    return _aim(pos, (target or hero)[:3], shot["fov"], shot["roll"])


def _smooth(points, reach):
    """Each point averaged with its neighbours up to `reach` samples either side. Past an end the points carry
    on as mirrored through it, so a steady climb, dive or turn is not flattened there."""
    last = len(points) - 1

    def at(j, axis):
        if j < 0:
            return 2 * points[0][axis] - points[min(last, -j)][axis]
        if j > last:
            return 2 * points[last][axis] - points[max(0, 2 * last - j)][axis]
        return points[j][axis]
    return [tuple(sum(at(i + k, axis) for k in range(-reach, reach + 1)) / (2 * reach + 1) for axis in range(len(points[0])))
            for i in range(len(points))]


WIDEST = math.radians(100)  # a wider lens bends the edges of the picture


def _lens(pos, a, b, base):
    """The lens, in radians, that shows two aircraft from `pos` with room round each: the shot's own when that
    is wide enough, and also when even the widest could not hold both (then one of them is let go)."""
    (yaw, pitch), (there_yaw, there_pitch) = (_aim(pos, there, 0, 0)[3:5] for there in (a, b))
    room = sum(math.atan(40 / max(40, math.dist(pos, there[:3]))) for there in (a, b))
    across = (abs((there_yaw - yaw + math.pi) % (2 * math.pi) - math.pi) + room) / .8
    down = (abs(there_pitch - pitch) + room) / .8
    need = max(across, 2 * math.atan(math.tan(min(1.5, down / 2)) * 16 / 9))  # the picture is 16:9
    return base if need > WIDEST else max(base, need)


def escort_poses(shot, tracks, step=None, seen=None):
    """The observer: a third aircraft flying with the hero on a smooth line of its own, one pose per track
    time. It is heavy: its place follows the hero's path averaged over one to four seconds either side, the
    further off the heavier (`distance` / 130), so the hero moves, turns, comes near and falls away in its
    picture instead of being nailed to it. Close by (120 ft: a second) it has to hold tight; measured on a
    turning fight, a four-second line at that distance came within 20 ft of the hero and whipped round it.
    Watching both aircraft it keeps station `distance` feet from that line and `orbit` degrees off the line
    through both, on the hero's side of it and following it up or down, so the other aircraft is beyond the
    hero and a little aside. Its lens opens by itself, from the shot's own up to WIDEST, to hold both: on a
    fixed lens a station that does not follow the hero's every turn loses the other aircraft (measured at
    400 ft over a 44 s fight: both in the picture 43% of the time on a fixed lens, 92% on this one).
    Watching the target alone it flies the same station and looks straight at it: a long lens from a steady seat.
    Watching the hero alone, `orbit` counts round the hero's own line of flight instead (0 behind, 90 off the
    right wing, 180 ahead): any side of it, as close as the lens likes, a second heavy.
    `step` is the seconds between tracks when their keys are not times."""
    times = sorted(tracks)
    last = len(times) - 1
    per = step or ((times[-1] - times[0]) / last if last else 1)
    watch = shot.get("watch", "both")
    weight = 1 if watch == "hero" or shot["target"] is None else max(1, min(4, shot["distance"] / 130))
    reach = max(1, round(weight / per))
    lead = [tracks[t][shot["hero"]][:3] for t in times]
    path = _smooth(lead, reach)
    # The other aircraft without the small steps a distant one moves in.
    other = None if shot["target"] is None else _smooth([tracks[t][shot["target"]][:3] for t in times], max(1, round(.5 / per)))
    turn = math.radians(shot["orbit"])
    stations = []  # where the ship sits from the hero: a bearing (0 toward +z, a quarter turn toward +x) and a climb
    if watch == "hero" or other is None:
        heading = _smooth([(lead[min(last, i + 1)][0] - lead[max(0, i - 1)][0], lead[min(last, i + 1)][2] - lead[max(0, i - 1)][2])
                           for i in range(len(times))], reach)
        behind = 0
        for move in heading:
            if math.hypot(*move) > 1e-6:  # standing still, it keeps the heading it had
                behind = math.atan2(-move[0], -move[1])
            stations.append((behind - turn, 0))
    else:
        # Where the line from the other aircraft through the hero points, turning the short way round from one
        # moment to the next: when the two pass each other the ship swings round in its own time.
        line, gone = [], (seen or {}).get(shot["target"])
        for when, a, b in zip(times, lead, other):
            if line and gone is not None and when > gone:
                line.append(line[-1])  # the other aircraft has left the film: the line stops turning
                continue
            angle = math.atan2(a[0] - b[0], a[2] - b[2])
            climb = math.atan2(a[1] - b[1], math.hypot(a[0] - b[0], a[2] - b[2]))
            line.append((line[-1][0] + (angle - line[-1][0] + math.pi) % (2 * math.pi) - math.pi if line else angle,
                         max(-.9, min(.9, climb))))  # not straight over or under the hero
        stations = [(angle + turn, climb) for angle, climb in _smooth(line, max(1, round(max(1.5, weight) / per)))]
    places = []
    for i, (bearing, climb) in enumerate(stations):
        up = math.sin(climb) * math.cos(turn) * shot["distance"]
        flat = math.sqrt(shot["distance"] ** 2 - up ** 2)
        places.append((path[i][0] + math.sin(bearing) * flat, path[i][1] + up + shot["height"], path[i][2] + math.cos(bearing) * flat))
    fov, both = math.radians(shot["fov"]), watch == "both" and other is not None
    lenses = [(fov,)] * len(places)
    if both:  # the lens moves as gently as the ship does
        lenses = _smooth([(_lens(pos, lead[i], other[i], fov),) for i, pos in enumerate(places)], max(1, round(1.5 / per)))
    poses = []
    for i, pos in enumerate(places):
        wide = min(WIDEST, max(fov, lenses[i][0]))  # smoothing runs on past either end
        if both:
            look = _hold(pos, lead[i], other[i], math.degrees(wide))
        else:
            look = _aim(pos, other[i] if watch == "target" else lead[i], 0, 0)[3:5]
        poses.append([*pos, *look, math.radians(shot["roll"]), wide])
    return poses


def _poses(shot, tracks, times, seen, step=None):
    """A moving camera at each track time: [(pose, the aircraft the viewer holds in the middle, or None)]."""
    if shot["kind"] == "pair":
        return pair_poses(shot, tracks, times, seen, step)
    # A camera ship watching one aircraft: held by the viewer between our points, at any lens.
    watched = None if shot.get("watch", "both") == "both" else shot["hero"] if shot["watch"] == "hero" else shot["target"]
    return [(pose, watched) for pose in escort_poses(shot, tracks, step, seen)]


def _held(follow):
    return "" if follow is None else f" at {follow}"


def pose_text(pose):
    return " ".join(f"{value:.8f}" for value in pose)


def _mount(shot):
    fov, roll = math.radians(shot["fov"]), math.radians(shot["roll"])
    left = -1 if shot["side"] == "left" else 1
    target = f" at {shot['target']}" if shot["target"] is not None else ""
    if shot["kind"] == "cockpit":
        return f"mount {shot['hero']} 0 0 0 0 0 0 {fov:.8f}"
    if shot["kind"] == "follow":
        # Fastened to the airframe, so it stays behind (or beside) however the aircraft turns, loops or rolls.
        # Aimed a little above the aircraft: it sits low in the frame with the horizon over it.
        turn, d, up = math.radians(shot["orbit"]), shot["distance"], shot["height"]
        return (f"mount {shot['hero']} {math.sin(turn) * d:.2f} {up:g} {-math.cos(turn) * d:.2f} {turn:.4f} "
                f"{.1 - math.atan2(up, d):.4f} {roll:.8f} {fov:.8f}{target}")
    if shot["kind"] == "wing":
        return f"mount {shot['hero']} {24 * left} {shot['height']:g} -6 0 0 {roll:.8f} {fov:.8f}{target}"
    right, up, ahead, yaw, pitch = MOUNTS[shot["kind"]]
    # Only over the shoulder does the camera turn to hold the other aircraft; the rest look at the airframe.
    return (f"mount {shot['hero']} {right * left:g} {up:g} {ahead:g} {yaw * left:.4f} {pitch:g} {roll:.8f} {fov:.8f}"
            + (target if shot["kind"] == "shoulder" else ""))


def _steady(shot, tracks):
    """An aircraft that leaves the film mid-shot (shot down, landed) keeps its last known place,
    so the camera holds on where it went down instead of the whole shot failing."""
    keys = sorted(tracks)
    filled = {key: dict(tracks[key]) for key in keys}
    for ident in (shot["hero"], shot["target"]):
        if ident is None:
            continue
        seen = [key for key in keys if ident in tracks[key]]
        if not seen:
            raise ValueError(f"Aircraft {ident} is not in the film during this shot. Move the shot or choose another aircraft.")
        for key in keys:
            if ident not in filled[key]:
                filled[key][ident] = tracks[max((k for k in seen if k < key), default=seen[0])][ident]
    return filled


def probe_cameras(shot, tracks, step=None):
    """One candidate as camera lines for the viewer's `project`: [(track key, line)]; key None covers the whole
    shot. `step` is the seconds between tracks when their keys are not times."""
    if shot["kind"] in ("weapon", "recorded"):
        return []  # a bomb's ride and the pilot's own view: the projection knows nothing about either
    if shot["kind"] in RIDES:
        return [(None, _mount(shot))]
    seen = _last_seen(shot, tracks)
    tracks = _steady(shot, tracks)
    keys = sorted(tracks)
    if shot["kind"] in ("fixed", "ambush"):
        target = shot["target"] if shot["target"] is not None else shot["hero"]
        return [(None, f"fixed {pose_text(camera_pose(shot, tracks[keys[len(keys) // 2]]))} at {target}")]
    return [(key, "fixed " + pose_text(pose) + _held(follow)) for key, (pose, follow) in zip(keys, _poses(shot, tracks, keys, seen, step))]


def compile_shot(shot, tracks):
    start, end = shot["start"], shot["end"]
    duration, hero = end - start, shot["hero"]
    if shot["kind"] == "recorded":
        return [f"cam {start:g} {duration:g} recorded"]  # the pilot's view as flown, head movements included
    if shot["kind"] == "weapon":
        # Rides the aircraft until its next bomb, rocket or torpedo leaves, then the weapon down to the blast.
        return [f"cam {start:g} {duration:g} weapon {hero}"]
    if shot["kind"] == "cockpit":
        return [f"cam {start:g} {duration:g} {_mount(shot)}"]
    if shot["kind"] in RIDES:
        return [f"cam {start:g} {duration:g} {_mount(shot)} outside"]
    times = sorted(t for t in tracks if start - .011 <= t <= end + .011)
    if not times or abs(times[0] - start) > .03 or abs(times[-1] - end) > .03:
        raise ValueError("The viewer did not return the whole shot interval")
    seen = _last_seen(shot, {t: tracks[t] for t in times})
    tracks = _steady(shot, {t: tracks[t] for t in times})
    if shot["kind"] in ("fixed", "ambush"):
        pose = camera_pose(shot, tracks[times[len(times) // 2]])
        target = shot["target"] if shot["target"] is not None else hero
        return [f"cam {start:g} {duration:g} fixed {pose_text(pose)} at {target}"]
    points = [(t, pose, follow) for t, (pose, follow) in zip(times, _poses(shot, tracks, times, seen))]
    # Unwrap yaw so a crossing of north does not spin the camera around.
    for i in range(1, len(points)):
        previous, current = points[i - 1][1], points[i][1]
        current[3] = previous[3] + (current[3] - previous[3] + math.pi) % (2 * math.pi) - math.pi
    return [f"cam {a:g} {b-a:.8f} fixed {pose_text(pa)} to {pose_text(pb)}{_held(follow)} curve"
            for (a, pa, follow), (b, pb, _next) in zip(points, points[1:])]


def validate_plan(shots, length):
    if not isinstance(shots, list) or len(shots) > 200:
        raise ValueError("A plan can contain up to 200 shots")
    shots = sorted((validate_shot(s, length) for s in shots), key=lambda s: s["start"])
    if len({s["id"] for s in shots}) != len(shots):
        raise ValueError("Shot ids must be unique")
    return shots  # they may overlap: takes of one stretch through different cameras


def passes(shots):
    """Shots in film order dealt into passes in which none overlap. The viewer holds one camera at a time, so
    takes of the same stretch through different cameras are played, and rendered, in passes of their own."""
    rows = []
    for shot in shots:
        row = next((row for row in rows if row[-1]["end"] <= shot["start"] + .001), None)
        if row:
            row.append(shot)
        else:
            rows.append([shot])
    return rows
