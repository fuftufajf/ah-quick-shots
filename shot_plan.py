"""Editable shots -> native camera commands, using the viewer's own tracks."""
import math
import statistics
import uuid

from trails import SECONDS, STYLE, colour
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
# What a keyframe of the observer holds, with the limits each has on a shot: where the ship sits and its lens.
KEYED = (("orbit", -180, 180), ("distance", 20, 10000), ("height", -2000, 5000), ("fov", 6, 140))


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
    # A camera ship watching its hero can swing `sweep` degrees round it and close in to `closer` feet over the shot.
    # A camera on the airframe can be turned from where it looks: `turn` degrees to the left, `tilt` up.
    for key, low, high in (("sweep", -360, 360), ("closer", 20, 10000), ("turn", -180, 180), ("tilt", -89, 89)):
        if raw.get(key) is not None:
            shot[key] = number(raw[key], low, high, key)
    shot["watch"] = raw.get("watch", "both")
    if shot["watch"] not in WATCH:
        raise ValueError("A camera ship watches both aircraft, its hero or its target")
    two = shot["kind"] in ("pair", "ambush") or (shot["kind"] == "escort" and shot["watch"] != "hero")
    if shot["hero"] is None or shot["target"] == shot["hero"] or (two and shot["target"] is None):
        raise ValueError("Choose the lead aircraft and, for a shot of two, a different second aircraft")
    if kind == "escort":
        # The observer can be directed: `loose`, the seconds of the hero's path it flies the average of; `keys`,
        # framings set at moments of the shot, between which it glides, each with whom it watches from there
        # (`watch`; the shot's own when it does not say); `path` "own", when those moments pin it to the sky and it
        # flies its own line through them instead of the aircraft's.
        if raw.get("loose") is not None:
            shot["loose"] = number(raw["loose"], 1, 12, "looseness")
        keys = raw.get("keys") or []
        if not isinstance(keys, list) or len(keys) > 40 or not all(isinstance(key, dict) for key in keys):
            raise ValueError("A shot holds up to 40 keyframes")
        kept = {}
        for key in keys:
            t = round(number(key.get("t"), 0, length, "keyframe time"), 2)
            if shot["start"] - .001 <= t <= shot["end"] + .001:  # one outside the shot went with what was trimmed off
                kept[t] = {"t": t, **{name: number(key.get(name), low, high, name) for name, low, high in KEYED}}
                if key.get("watch") is not None:
                    if key["watch"] not in WATCH or (key["watch"] != "hero" and shot["target"] is None):
                        raise ValueError("A keyframe watches both aircraft, the hero or the target, and only an aircraft the shot has")
                    kept[t]["watch"] = key["watch"]
        if kept:
            shot["keys"] = [kept[t] for t in sorted(kept)]
            if raw.get("path") == "own":
                shot["path"] = "own"
        if raw.get("zoom") == "hold":  # the lens follows the aircraft it watches alone, to keep it the size it was framed
            shot["zoom"] = "hold"
    if raw.get("effects") is not None:
        shot["effects"] = validate_effects(raw["effects"])
    return shot


def validate_effects(raw):
    """What a shot is rendered with besides its camera, as the page sends it: `overlay` (the data overlay), `blur`,
    `look` (the film look and its haze, one switch) and `trails` ({lead, opponent, tail: "#rrggbb", tail None for
    none; seconds; exact}). {} is none of them."""
    if not isinstance(raw, dict):
        raise ValueError("A shot's effects are an object")
    effects = {name: True for name in ("overlay", "blur", "look") if raw.get(name) is True}
    if raw.get("trails"):
        asked = raw["trails"] if isinstance(raw["trails"], dict) else {}
        drawn = {"lead": asked.get("lead", "#e8f4ff"), "opponent": asked.get("opponent", "#ff9e6b"), "tail": asked.get("tail") or None}
        for key, value in drawn.items():
            if value is not None:
                colour(value)  # "#rrggbb", or refused
                drawn[key] = value.lower()  # so two shots of one colour are rendered together
        effects["trails"] = {**drawn, "seconds": number(asked.get("seconds", STYLE["seconds"]), *SECONDS, "trail length"),
                             "exact": asked.get("exact") is True}
    return effects


def _glide(times, values, t):
    """A value through keyframes at t: held before the first and after the last; between them on a cubic that
    passes each at the pace of its neighbours and is flat at the two ends, so a move starts and stops gently and
    does not halt at the keyframes on the way."""
    if t <= times[0] or len(times) == 1:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    i = max(k for k in range(len(times) - 1) if times[k] <= t)
    pace = [0.0 if k in (0, len(times) - 1) else (values[k + 1] - values[k - 1]) / (times[k + 1] - times[k - 1]) for k in (i, i + 1)]
    h = times[i + 1] - times[i]
    s = (t - times[i]) / h
    return ((2 * s**3 - 3 * s**2 + 1) * values[i] + (s**3 - 2 * s**2 + s) * h * pace[0]
            + (-2 * s**3 + 3 * s**2) * values[i + 1] + (s**3 - s**2) * h * pace[1])


def framed(shot, t):
    """The shot as its keyframes set it at film time t: where the ship sits and its lens. Round the aircraft it goes
    the short way; a cubic can pass its keyframes' values, so each stays within what a shot may hold."""
    keys = shot.get("keys")
    if not keys:
        return shot
    times, out = [key["t"] for key in keys], dict(shot)
    for name, low, high in KEYED:
        values = [key[name] for key in keys]
        if name == "orbit":
            for k in range(1, len(values)):
                values[k] = values[k - 1] + (values[k] - values[k - 1] + 180) % 360 - 180
            low, high = -math.inf, math.inf
        out[name] = min(high, max(low, _glide(times, values, t)))
    return out


def _watching(shot, t):
    """How much of the picture is both aircraft's, the hero's alone and the target's alone at film time t: three
    shares of one, in WATCH's order. A keyframe may say whom the ship watches from there, and the picture passes
    from one keyframe's aircraft to the next one's over the time between them, starting and ending gently. Set two
    keyframes close together for a quick turn of the head."""
    own = shot.get("watch", "both")
    marks = [(key["t"], key.get("watch", own)) for key in shot.get("keys", ())] or [(0, own)]
    i = max((k for k, (at, _who) in enumerate(marks) if at <= t), default=0)
    (a, was), (b, will) = marks[i], marks[min(i + 1, len(marks) - 1)]
    share = _ease(min(1, max(0, (t - a) / (b - a)))) if b > a else 0
    return tuple((1 - share) * (was == who) + share * (will == who) for who in WATCH)


def track_rows(lines):
    result = {}
    for line in lines:
        if line.startswith("track "):
            row = line.split()
            result.setdefault(float(row[1]), {})[int(row[2])] = tuple(map(float, row[3:9]))
    return result


def filmer_path(lines):
    """The recording pilot's aircraft through `tracks` lines: [(t, id, x, alt, z)]. It is the aircraft nearest the
    eye of the recorded cockpit view (measured: 3 to 5 ft from it); while the view is outside, the last one found."""
    tracks, path, filmer = track_rows(lines), [], None
    eyes = {float(row[1]): tuple(map(float, row[3:6])) for row in (line.split() for line in lines)
            if row[0] == "view" and len(row) > 5 and row[2] == "internal"}
    for t in sorted(tracks):
        if t in eyes and tracks[t]:
            ident, row = min(tracks[t].items(), key=lambda item: math.dist(item[1][:3], eyes[t]))
            if math.dist(row[:3], eyes[t]) < 15:
                filmer = ident
        if filmer in tracks[t]:
            path.append((t, filmer, *tracks[t][filmer][:3]))
    return path


def ground_moments(path):
    """Take-offs and touchdowns along the filmer's path [(t, id, x, alt, z)], about a second apart.
    A take-off starts from standing (under 8 ft/s): the roll, then the lift-off, the last moment within 1.5 ft of
    the ground before it is 8 ft up. Measured on a real film: parked to 8 s, rolling from 8.5 s, lift-off at 18.5 s.
    A touchdown ends in standing: back from the stop, the first moment the aircraft was within 2 ft of where it
    stopped, after being 20 ft higher in the minute before. `ground` is the aircraft's height on the ground there."""
    found, runs = [], [[]]
    for point in path:
        if runs[-1] and (point[1] != runs[-1][-1][1] or point[0] - runs[-1][-1][0] > 2.5):
            runs.append([])  # another sortie, or the film skipped
        runs[-1].append(point)
    for run in runs:
        still = [True] + [math.dist(b[2:], a[2:]) / (b[0] - a[0]) < 8 for a, b in zip(run, run[1:])]
        for i in range(1, len(run)):
            if still[i - 1] and not still[i]:
                ground = run[i - 1][3]
                for k in range(i, len(run)):
                    if still[k] or run[k][0] - run[i][0] > 120:
                        break
                    if run[k][3] > ground + 8:
                        lift = max(m for m in range(i - 1, k) if run[m][3] <= ground + 1.5)
                        found.append({"kind": "takeoff", "time": run[lift][0], "roll": run[i - 1][0], "ground": ground, "hero": run[i][1]})
                        break
            if not still[i - 1] and still[i]:
                ground, down = run[i][3], i
                while down > 0 and abs(run[down - 1][3] - ground) <= 2:
                    down -= 1
                if any(run[j][3] > ground + 20 and run[down][0] - run[j][0] <= 60 for j in range(down)):
                    found.append({"kind": "touchdown", "time": run[down][0], "stop": run[i][0], "ground": ground, "hero": run[i][1]})
    return found


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
# Radians: how far up or down from the hero the observer's station follows the line through both. Straight under or
# over the hero a camera looks straight up or down and the picture spins (a camera on that line itself whipped
# at over 100 degrees a second and lost the opponent each time he was 80 degrees above or below). At 0.9 the lens
# had to open to 92 degrees in a vertical fight to hold him; at 1.1 to 80, without one aircraft covering the
# other more often than a second or two in two minutes; steeper, it does.
STEEPEST = 1.1
FADE, LENS_BACK = 1.5, 3.0  # seconds: an aircraft that left the film lets go of the picture, then of the lens


def _ease(x):
    """0 to 1 starting and ending gently."""
    return x * x * (3 - 2 * x)


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
    time. It is heavy: its place follows the hero's path averaged over one to two seconds either side, the
    further off the heavier (`distance` / 200; the shot's `loose` says otherwise), so the hero moves, turns, comes
    near and falls away in its picture instead of being nailed to it. Close by (120 ft: a second) it has to hold
    tight; measured on a turning fight, a four-second line at that distance came within 20 ft of the hero and
    whipped round it. Heavier than two seconds it loses the other aircraft in a turning fight: over three fights
    (118 s, 37 s, 10 s) three seconds at 400 ft kept both in the picture 81, 88 and 100% of the time, two seconds
    97, 98 and 100%.
    Watching both aircraft it keeps station `distance` feet from that line and `orbit` degrees off the line
    through both, on the hero's side of it and following it up or down, so the other aircraft is beyond the
    hero and a little aside. Its lens opens by itself, from the shot's own up to WIDEST, to hold both: on a
    fixed lens a station that does not follow the hero's every turn loses the other aircraft (measured at
    400 ft over a 44 s fight: both in the picture 43% of the time on a fixed lens, 92% on this one).
    Watching the target alone it flies the same station and looks straight at it: a long lens from a steady seat.
    Watching the hero alone, `orbit` counts round the hero's own line of flight instead (0 behind, 90 off the
    right wing, 180 ahead): any side of it, as close as the lens likes, a second heavy.
    Its keyframes can say whom it watches from each on (_watching): between two that differ it changes seat, look
    and lens together, each by the share of the picture the one and the other then have.
    `step` is the seconds between tracks when their keys are not times."""
    times = sorted(tracks)
    last = len(times) - 1
    per = step or ((times[-1] - times[0]) / last if last else 1)
    when = [t if step is None else shot["start"] + t * step for t in times]  # film time of each track
    mix = [_watching(shot, t) if shot["target"] is not None else (0, 1, 0) for t in when]  # both's, hero's, target's
    alone = all(hero == 1 for _both, hero, _target in mix)
    weight = shot.get("loose") or (1 if alone else max(1, min(2, shot["distance"] / 200)))
    frames = [framed(shot, t) for t in when]
    reach = max(1, round(weight / per))
    lead = [tracks[t][shot["hero"]][:3] for t in times]
    path = _smooth(lead, reach)
    # The other aircraft without the small steps a distant one moves in.
    other = None if shot["target"] is None else _smooth([tracks[t][shot["target"]][:3] for t in times], max(1, round(.5 / per)))
    turns = [math.radians(frame["orbit"]) for frame in frames]
    # When the other aircraft leaves the film (shot down) the ship lets go of him gently. Held at his last place,
    # he stopped the picture dead: turning 27 degrees a second, then 0.6 (measured on a kill at 400 ft), and the
    # lens, open to hold him, snapped back from 31 to 9 degrees in two seconds. Now he flies on as he was going
    # for as long as the picture still leans toward him (FADE), and the lens comes back over LENS_BACK.
    gone = (seen or {}).get(shot["target"]) if other is not None else None
    after = [0.0 if gone is None or when <= gone else (when - gone) * (step or 1) for when in times]
    if any(after):
        g = after.index(next(a for a in after if a > 0)) - 1  # his last moment in the film
        drift = tuple(other[g][k] - other[max(0, g - 1)][k] for k in range(3))
        other = other[:g + 1] + [tuple(other[g][k] + drift[k] * min(i - g, FADE / per) for k in range(3)) for i in range(g + 1, len(times))]
    # Where the ship sits from the hero, a bearing (0 toward +z, a quarter turn toward +x) and a climb: `solo` round
    # the hero's own line of flight, `pair` off the line through both.
    solo = pair = None
    if any(hero for _both, hero, _target in mix):
        heading = _smooth([(lead[min(last, i + 1)][0] - lead[max(0, i - 1)][0], lead[min(last, i + 1)][2] - lead[max(0, i - 1)][2])
                           for i in range(len(times))], reach)
        behind, sweep, solo = 0, math.radians(shot.get("sweep", 0)), []
        for i, move in enumerate(heading):
            if math.hypot(*move) > 1e-6:  # standing still, it keeps the heading it had
                behind = math.atan2(-move[0], -move[1])
            solo.append((behind - turns[i] - sweep * (i / last if last else 0), 0))
    if not alone:
        # Where the line from the other aircraft through the hero points, turning the short way round from one
        # moment to the next: when the two pass each other the ship swings round in its own time.
        line = []
        for since, a, b in zip(after, lead, other):
            if line and since > FADE:
                line.append(line[-1])  # the other aircraft has left the film: the line stops turning
                continue
            angle = math.atan2(a[0] - b[0], a[2] - b[2])
            climb = math.atan2(a[1] - b[1], math.hypot(a[0] - b[0], a[2] - b[2]))
            line.append((line[-1][0] + (angle - line[-1][0] + math.pi) % (2 * math.pi) - math.pi if line else angle,
                         max(-STEEPEST, min(STEEPEST, climb))))  # not straight over or under the hero
        # Averaged, and then once more over a second either side, so the ship gathers and loses its pace. When the
        # two pass close by, that line swings right round in half a second (measured: 150 degrees, passing at 76 ft).
        # Averaged once, the ship's swing began and ended at once and the picture jerked: see "The observer's swing"
        # in docs/how-it-works.md for what nine fights showed of this and of three other ways.
        eased = _smooth(_smooth(line, max(1, round(max(1.5, weight) / per))), max(1, round(1 / per)))
        pair = [(angle + turns[i], climb) for i, (angle, climb) in enumerate(eased)]
    stations = solo if pair is None else pair
    if solo and pair:
        # Between a keyframe that watches the hero alone and one that does not it goes from one seat to the other
        # round the hero: the short way as the move begins, and that way to its end (through the hero is no way).
        stations, gap = [], 0
        for (bearing, climb), (round_hero, _level), (_both, hero, _target) in zip(pair, solo, mix):
            if not 0 < hero < 1:
                gap = 0
            gap += (round_hero - bearing - gap + math.pi) % (2 * math.pi) - math.pi
            stations.append((bearing + hero * gap, climb * (1 - hero)))
    places = []
    for i, (bearing, climb) in enumerate(stations):
        base = frames[i]["distance"]
        distance = base + (shot.get("closer", base) - base) * (i / last if last else 0)
        up = math.sin(climb) * math.cos(turns[i]) * distance
        flat = math.sqrt(distance ** 2 - up ** 2)
        places.append((path[i][0] + math.sin(bearing) * flat, path[i][1] + up + frames[i]["height"], path[i][2] + math.cos(bearing) * flat))
    if shot.get("path") == "own":
        # Its own path: each keyframe pins the ship to the sky where it then sat, and it flies a smooth line through
        # those places, standing at the first before it and at the last after. The aircraft come by, pass and leave.
        pins = [(key["t"], places[min(range(len(when)), key=lambda i: abs(when[i] - key["t"]))]) for key in shot["keys"]]
        places = [tuple(_glide([t for t, _place in pins], [place[axis] for _t, place in pins], t) for axis in range(3)) for t in when]
    fovs = [math.radians(frame["fov"]) for frame in frames]
    if shot.get("zoom") == "hold":
        # The lens keeps the aircraft it watches alone as big as the framed lens shows one from the framed distance:
        # from a ship that stands still (a path of its own, one keyframe) the camera follows by turning and zooming
        # only. Within what a lens may be: further off than a 6 degree lens reaches, the aircraft does grow small.
        for i, (pos, (both, hero, his)) in enumerate(zip(places, mix)):
            if both < 1:
                far = (hero * math.dist(pos, lead[i]) + (his and his * math.dist(pos, other[i]))) / (hero + his)
                held = 2 * math.atan(math.tan(fovs[i] / 2) * frames[i]["distance"] / max(1, far))
                fovs[i] = fovs[i] * both + min(math.radians(140), max(math.radians(6), held)) * (1 - both)
    lenses = [(fov,) for fov in fovs]
    if any(both for both, _hero, _target in mix):  # the lens moves as gently as the ship does
        raw = [_lens(pos, lead[i], other[i], fovs[i]) for i, pos in enumerate(places)]
        if any(after):
            left = raw[g]
            raw = [lens if not after[i] else fovs[i] + (left - fovs[i]) * _ease(1 - min(1, after[i] / LENS_BACK)) for i, lens in enumerate(raw)]
        lenses = _smooth([(fovs[i] * (1 - both) + lens * both,) for i, (lens, (both, _hero, _target)) in enumerate(zip(raw, mix))],
                         max(1, round(1.5 / per)))
    poses = []
    for i, pos in enumerate(places):
        wide = min(WIDEST, max(fovs[i], lenses[i][0]))  # smoothing runs on past either end
        both, _hero, his = mix[i]
        both *= 1 - min(1, after[i] / FADE)  # how much he still draws the picture once he has left the film
        looks = [(1 - both - his, _aim(pos, lead[i], 0, 0)[3:5])]  # the hero alone has the rest of it
        if both:
            looks.append((both, _hold(pos, lead[i], other[i], math.degrees(wide))))
        if his:
            looks.append((his, _aim(pos, other[i], 0, 0)[3:5]))
        # Each look's share of the way from the first, the short way round.
        (_all, (yaw, pitch)), *rest = [look for look in looks if look[0]]
        poses.append([*pos, yaw + sum(share * ((there[0] - yaw + math.pi) % (2 * math.pi) - math.pi) for share, there in rest),
                      pitch + sum(share * (there[1] - pitch) for share, there in rest), math.radians(shot["roll"]), wide])
    return poses


def _poses(shot, tracks, times, seen, step=None):
    """A moving camera at each track time: [(pose, the aircraft the viewer holds in the middle, or None)]."""
    if shot["kind"] == "pair":
        return pair_poses(shot, tracks, times, seen, step)
    # A camera ship watching one aircraft: held by the viewer between our points, at any lens. Where its keyframes
    # pass the picture from one aircraft to another, the points themselves say where it looks.
    held = []
    for t in times:
        _both, hero, target = _watching(shot, t if step is None else shot["start"] + t * step)
        held.append(shot["hero"] if hero == 1 else shot["target"] if target == 1 else None)
    return [(pose, who if who == then else None) for pose, who, then in zip(escort_poses(shot, tracks, step, seen), held, held[1:] + held[-1:])]


def _held(follow):
    return "" if follow is None else f" at {follow}"


def stamp(t):
    """A film time as the viewer is told it: to the microsecond, and no longer than it need be."""
    return f"{t:.6f}".rstrip("0").rstrip(".")


def pose_text(pose):
    return " ".join(f"{value:.8f}" for value in pose)


def _mount(shot):
    fov, roll = math.radians(shot["fov"]), math.radians(shot["roll"])
    left = -1 if shot["side"] == "left" else 1
    target = f" at {shot['target']}" if shot["target"] is not None else ""
    # The viewer's yaw turns to the left and its pitch up (the gear camera on the right looks in with +0.7).
    turn, tilt = math.radians(shot.get("turn", 0)), math.radians(shot.get("tilt", 0))
    if shot["kind"] == "cockpit":
        return f"mount {shot['hero']} 0 0 0 {turn:g} {tilt:g} 0 {fov:.8f}"
    if shot["kind"] == "follow":
        # Fastened to the airframe, so it stays behind (or beside) however the aircraft turns, loops or rolls.
        # Aimed a little above the aircraft: it sits low in the frame with the horizon over it.
        turn, d, up = math.radians(shot["orbit"]), shot["distance"], shot["height"]
        return (f"mount {shot['hero']} {math.sin(turn) * d:.2f} {up:g} {-math.cos(turn) * d:.2f} {turn:.4f} "
                f"{.1 - math.atan2(up, d):.4f} {roll:.8f} {fov:.8f}{target}")
    if shot["kind"] == "wing":
        return f"mount {shot['hero']} {24 * left} {shot['height']:g} -6 {turn:g} {tilt:g} {roll:.8f} {fov:.8f}{target}"
    right, up, ahead, yaw, pitch = MOUNTS[shot["kind"]]
    # Only over the shoulder does the camera turn to hold the other aircraft; the rest look at the airframe.
    return (f"mount {shot['hero']} {right * left:g} {up:g} {ahead:g} {yaw * left + turn:.4f} {pitch + tilt:g} {roll:.8f} {fov:.8f}"
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
        return [f"cam {stamp(start)} {duration:g} recorded"]  # the pilot's view as flown, head movements included
    if shot["kind"] == "weapon":
        # Rides the aircraft until its next bomb, rocket or torpedo leaves, then the weapon down to the blast.
        return [f"cam {stamp(start)} {duration:g} weapon {hero}"]
    if shot["kind"] == "cockpit":
        return [f"cam {stamp(start)} {duration:g} {_mount(shot)}"]
    if shot["kind"] in RIDES:
        return [f"cam {stamp(start)} {duration:g} {_mount(shot)} outside"]
    times = sorted(t for t in tracks if start - .011 <= t <= end + .011)
    if not times or abs(times[0] - start) > .03 or abs(times[-1] - end) > .03:
        raise ValueError("The viewer did not return the whole shot interval")
    seen = _last_seen(shot, {t: tracks[t] for t in times})
    tracks = _steady(shot, {t: tracks[t] for t in times})
    if shot["kind"] in ("fixed", "ambush"):
        pose = camera_pose(shot, tracks[times[len(times) // 2]])
        target = shot["target"] if shot["target"] is not None else hero
        return [f"cam {stamp(start)} {duration:g} fixed {pose_text(pose)} at {target}"]
    points = [(t, pose, follow) for t, (pose, follow) in zip(times, _poses(shot, tracks, times, seen))]
    # Unwrap yaw so a crossing of north does not spin the camera around.
    for i in range(1, len(points)):
        previous, current = points[i - 1][1], points[i][1]
        current[3] = previous[3] + (current[3] - previous[3] + math.pi) % (2 * math.pi) - math.pi
    # The viewer joins moves into one smooth curve when each begins exactly when the one before ends. Told with six
    # digits (`:g`) and its length with eight decimals, a move began up to 0.5 ms off the end of the one before, and
    # 5 ms in a film past 1,000 s: 2 ft of the camera's way, at every joint. Now to the microsecond, and its length
    # is the difference of the two times as they are told.
    stamps = [round(t, 6) for t, _pose, _follow in points]
    return [f"cam {stamp(a)} {b - a:.8f} fixed {pose_text(pa)} to {pose_text(pb)}{_held(follow)} curve"
            for a, b, (_t, pa, follow), (_then, pb, _next) in zip(stamps, stamps[1:], points, points[1:])]


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


def videos(shots, apart=False):
    """The videos a render of these shots in film order makes, a row of shots each: a shot each when asked `apart`;
    else the passes, each cut where the effects change, so every video is one stretch of film rendered one way."""
    if apart:
        return [[shot] for shot in shots]
    rows = []
    for row in passes(shots):
        rows.append(row[:1])
        for a, b in zip(row, row[1:]):
            if a.get("effects") == b.get("effects"):
                rows[-1].append(b)
            else:
                rows.append([b])
    return rows
