"""What each aircraft did, from the viewer's kept tracks (`tracks ... keep`, its layout in the viewer's `help`),
and what two of them did to each other. Feet, seconds and degrees; speeds in feet per second."""
import math
from pathlib import Path
import statistics
import struct

G = 32.174  # ft/s²
HEADER = struct.Struct("<8siiqqddddiiiiq")  # 88 bytes, as the viewer's help lays it out
TABLE = struct.Struct("<ii16sffff")  # 40 bytes per aircraft entry


def read_kept(path):
    """A kept-tracks file: {start, end, step, aircraft: {id: {name, radius}}, tracks: {id: [(t, x, alt, z, yaw,
    pitch, roll, vx, valt, vz)]}}. Every count and offset is checked against the file before it is used."""
    path = Path(path)
    if path.suffix != ".ahftracks":
        raise ValueError("Not a kept-tracks file")
    data = path.read_bytes()
    try:
        magic, version, flags, _size, _written, _length, start, end, step, snapshots, entries, record, _, table = HEADER.unpack_from(data)
        if magic != b"AHTRACKS" or version != 1 or not flags & 1:
            raise ValueError("Kept tracks need version 1 with speed on")
        layout = "<i" + ("ddd" if flags & 2 else "fff") + "ffffff"
        if struct.calcsize(layout) != record or not 0 <= snapshots <= 10_000_000 or not 0 <= entries <= 100_000:
            raise ValueError("Kept tracks file has an unexpected layout")
        if not HEADER.size <= table <= len(data) - entries * TABLE.size:
            raise ValueError("Kept tracks file is cut short")
        tracks, offset = {}, HEADER.size
        for _ in range(snapshots):
            t, count = struct.unpack_from("<di", data, offset)
            offset += 12
            if count < 0 or offset + count * record > table:
                raise ValueError("Kept tracks file is cut short")
            for row in struct.iter_unpack(layout, data[offset:offset + count * record]):
                tracks.setdefault(row[0], []).append((t, *row[1:]))
            offset += count * record
        aircraft = {}
        for n in range(entries):
            ident, first, name, radius, *_eye = TABLE.unpack_from(data, table + n * TABLE.size)
            # shortcut: an id the film gave to another pilot within the stretch keeps the first name; split by `first` if that matters
            aircraft.setdefault(ident, {"name": name.split(b"\0")[0].decode("utf-8", "replace"), "radius": round(radius, 1)})
    except struct.error:
        raise ValueError("Kept tracks file is cut short") from None
    return {"start": start, "end": end, "step": step, "aircraft": aircraft, "tracks": tracks}


def _axes(yaw, pitch, roll):
    """Nose and lift directions in the world. Yaw 0 looks toward +z, +pi/2 toward -x; pitch up and roll right are
    positive (roll measured on a kept track: the lift leaned the way of a positive roll in 186 of 186 hard turns)."""
    nose = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    right = (math.cos(yaw), 0.0, math.sin(yaw))
    level = _cross(nose, right)
    lift = tuple(level[i] * math.cos(roll) + right[i] * math.sin(roll) for i in range(3))
    return nose, lift


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _angle(a, b):
    size = math.sqrt(_dot(a, a) * _dot(b, b))
    return math.degrees(math.acos(max(-1.0, min(1.0, _dot(a, b) / size)))) if size else 0.0


def update_interval(rows):
    """How often the film really told this aircraft's speed. The recording pilot's changes every snapshot. Another
    aircraft's was held, then changed over a few snapshots, then held again (measured: news every 2 s, each
    blended in over 0.3-0.4 s). What counts is how long a moment of the stretch typically sits in one held value:
    the gaps between changes, weighted by their length."""
    changes = [b[0] for a, b in zip(rows, rows[1:]) if a[7:10] != b[7:10]]
    if len(changes) < 2:
        return rows[-1][0] - rows[0][0] if len(rows) > 1 else 0.0
    gaps = sorted(b - a for a, b in zip(changes, changes[1:]))
    total, held = sum(gaps), 0.0
    for gap in gaps:
        held += gap
        if held >= total / 2:
            return gap
    return gaps[-1]


def derive(rows, step):
    """Per snapshot: true airspeed, climb, flight-path angle, load factor, turn rate and radius, specific energy
    and its rate. Accelerations come from the velocity averaged over a window: half a second, or two of the
    aircraft's own updates when the film told its speed more coarsely. Measured on a kept track: a far aircraft's
    load factor jumped to 12-22 G raw and still reached 5-9 G over half a second, so it is marked `coarse`."""
    every = update_interval(rows)
    window = max(.5, 2 * every)
    half = max(1, round(window / step / 2))
    out = []
    for i, row in enumerate(rows):
        t, x, alt, z, yaw, pitch, roll, *v = row
        speed = math.sqrt(_dot(v, v))
        before = rows[max(0, i - half):i + 1]
        after = rows[i:i + half + 1]
        span = statistics.fmean(r[0] for r in after) - statistics.fmean(r[0] for r in before)
        if span > 0:
            accel = tuple((statistics.fmean(r[7 + k] for r in after) - statistics.fmean(r[7 + k] for r in before)) / span for k in range(3))
        else:
            accel = (0.0, 0.0, 0.0)
        nose, lift = _axes(yaw, pitch, roll)
        carried = (accel[0], accel[1] + G, accel[2])  # what the wings hold up: the acceleration less gravity's
        along = _dot(accel, v) / speed if speed else 0.0
        across = math.sqrt(max(0.0, _dot(accel, accel) - along * along))
        out.append({"t": round(t, 3), "x": round(x, 1), "alt": round(alt, 1), "z": round(z, 1),
                    "yaw": round(math.degrees(yaw), 1), "pitch": round(math.degrees(pitch), 1), "roll": round(math.degrees(roll), 1),
                    "tas": round(speed, 1), "climb": round(v[1], 1),
                    "path": round(math.degrees(math.asin(max(-1, min(1, v[1] / speed)))) if speed else 0.0, 1),
                    "g": round(_dot(carried, lift) / G, 2),
                    "turn": round(math.degrees(across / speed), 1) if speed else 0.0,
                    "radius": round(min(99999, speed * speed / across)) if across > .5 else None,
                    "energy": round(alt + speed * speed / (2 * G)),
                    "power": round(v[1] + speed * along / G, 1)})
    return {"quality": "fine" if every <= .25 else "coarse", "update": round(every, 2), "window": round(window, 2), "samples": out}


def pair(hero, target):
    """At every snapshot both aircraft share: range, closure (positive while closing), the target's angle off the
    hero's nose, the hero's angle off the target's tail (0 dead astern), the angle between their noses, and
    whether the hero is behind the target's wing line. hero and target are rows as read_kept gives them."""
    there = {row[0]: row for row in target}
    out = []
    for a in hero:
        b = there.get(a[0])
        if b is None:
            continue
        sight = (b[1] - a[1], b[2] - a[2], b[3] - a[3])
        distance = math.sqrt(_dot(sight, sight))
        if not distance:
            continue
        drift = (b[7] - a[7], b[8] - a[8], b[9] - a[9])
        nose_a, nose_b = _axes(*a[4:7])[0], _axes(*b[4:7])[0]
        off_tail = _angle(nose_b, sight)
        out.append({"t": round(a[0], 3), "range": round(distance), "closure": round(-_dot(sight, drift) / distance, 1),
                    "above": round(b[2] - a[2]), "off_nose": round(_angle(nose_a, sight), 1),
                    "off_tail": round(off_tail, 1), "crossing": round(_angle(nose_a, nose_b), 1), "behind": off_tail < 90})
    return out
