"""Trails from the wingtips, drawn over a render: two soft lines of vapour behind each aircraft of the shot, one
colour for the lead aircraft and another for his opponent. Short, they follow the aircraft; lasting (up to a
minute), they stay in the sky as the ribbons of the fight, to be looked at afterwards.

A trail has a bright part and a faint remainder. The bright part is sized to the picture, not to the clock: it lasts
as long as the aircraft takes to cross a share of the picture's width (`reach`), so it reads the same from a camera
120 ft off the wing and from one half a mile away. Timed alone (2.5 s of trail), a close camera saw only its newest
quarter of a second, as bright at the picture's edge as at the wingtip: two wires across the frame (seen on a
render of film13). The remainder stays as far back as the trail reaches and fades to nothing there; a lasting trail
keeps more of it. The trail is as wide as vapour is, in feet: thin at the wingtip, spreading as it ages, thicker
near the camera and never thinner than a pixel; close to the lens it comes in gently instead of cutting the frame.

Where the viewer's camera stands at every frame comes from its own projection (`project`'s eye lines); where the
wingtips were, from the kept tracks. Checked against the viewer's own placing of the aircraft at 1080p: 0.7 px
(median) on a moving camera, 0.15 px on airframe cameras rolled up to 70 degrees. libass draws the lines through
ffmpeg; ffmpeg blurs a copy for the glow and lays the light over the picture (screen), which is far faster than
libass blurring each piece (42 s against 200 s for 7 s at 1080p60)."""
import math
import re

from finishing import graph_path
from flight_data import _axes, _cross

STYLE = {
    "seconds": 2.5,      # how far back a trail reaches
    "span": .88,         # the wingtip's offset as a share of the aircraft's visible radius (a Yak's radius 17.9 ft, its tips 15.8 ft out)
    "opacity": .75,      # at the wingtip
    "rise": 3.3,         # feet ahead of the wingtip over which the trail comes in from nothing: it leaves any wing gently, wherever the tip really is
    "reach": .3,         # the bright part lasts as long as the aircraft takes to cross this share of the picture's width
    "remain": .07,       # the remainder's brightness, as a share of the wingtip's
    "fade": 1.8,         # the remainder falls as (1 - age / seconds) ** fade
    "core": .12,         # the trail's width at the wingtip, feet
    "spread": 1.5,       # feet it widens by in a second
    "thinnest": 1.0,     # what that comes to on the screen at least, pixels of a 1080-high picture
    "thickest": 6.0,     # and at most
    "close": (15, 80),   # feet from the camera: nearer than the first a trail is not drawn; up to the second it comes in gently
    "soft": .8,          # the lines' own blur, pixels of a 1080-high picture: vapour has no hard edge
    "glow": 5.0,         # the glow's blur, pixels of a 1080-high picture
    "glow_gain": 1.4,    # the blurred line brightened this many times
    "bands": 18,         # steps of brightness along a trail, each as large as the next: none stands out as a dash
    "smooth": 2,         # wingtip positions averaged over this many samples either side (not the newest)
    "fine": 1.5,         # the newest seconds sampled at every kept step
    "coarse": .04,       # before them, a sample this many seconds apart (up to `most` of them): every move of the stick stays in the ribbon
    "most": 3000,        # a two-minute trail is 3000 samples at that
    "tail": (.9, .25),   # the fin's tip, behind and above the aircraft's middle, as shares of its visible radius: the viewer tells no other size
    "thin": 1.5,         # screen points nearer than this many pixels to the last one kept are dropped
}
SECONDS = (1.0, 120.0)  # the lengths a page may ask for


def style_for(seconds):
    """STYLE for a trail `seconds` long. A lasting one keeps more of itself after its bright part and fades more
    slowly, so the fight's ribbons stay to be read: at a minute the remainder is half as bright as the wingtip, and
    halfway back still three quarters of that."""
    keep = min(1.0, STYLE["seconds"] / seconds) ** .5
    return {**STYLE, "seconds": seconds, "fade": STYLE["fade"] * keep, "remain": STYLE["remain"] + (.6 - STYLE["remain"]) * (1 - keep)}


def paint(shots, lead, opponent):
    """{aircraft id: colour}: each shot's lead aircraft in `lead`, its opponent in `opponent` (the first shot to name
    an aircraft decides)."""
    colours = {}
    for shot in shots:
        for key, rgb in (("hero", lead), ("target", opponent)):
            if shot.get(key) is not None:
                colours.setdefault(shot[key], rgb)
    return colours


def colour(text):
    """A colour from the page, "#rrggbb", as (r, g, b)."""
    if not isinstance(text, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        raise ValueError("A trail colour is #rrggbb")
    return tuple(int(text[i:i + 2], 16) for i in (1, 3, 5))


def at(rows, t, step):
    """Position and attitude at t from kept-track rows (t, x, alt, z, yaw, pitch, roll, vx, valt, vz): positions by
    cubic Hermite with the velocities, angles linearly with yaw and roll wrapping, as the viewer's help says."""
    i = max(0, min(len(rows) - 2, int((t - rows[0][0]) / step)))
    while i > 0 and rows[i][0] > t:
        i -= 1
    while i < len(rows) - 2 and rows[i + 1][0] <= t:
        i += 1
    a, b = rows[i], rows[i + 1]
    h = b[0] - a[0]
    s = min(1.0, max(0.0, (t - a[0]) / h))
    h00, h10, h01, h11 = 2 * s**3 - 3 * s**2 + 1, s**3 - 2 * s**2 + s, -2 * s**3 + 3 * s**2, s**3 - s**2
    pos = [h00 * a[1 + j] + h10 * h * a[7 + j] + h01 * b[1 + j] + h11 * h * b[7 + j] for j in range(3)]
    angles = []
    for j in (4, 5, 6):
        d = b[j] - a[j]
        if j != 5:
            d = (d + math.pi) % (2 * math.pi) - math.pi
        angles.append(a[j] + d * s)
    return pos, angles


def wingtips(pos, yaw, pitch, roll, half, tail=None):
    """Left and right wingtip in the world, by the same axes as the flight data (flight_data._axes); with `tail`
    (feet behind the aircraft's middle, feet above it) the tip of the fin as a third."""
    nose, lift = _axes(yaw, pitch, roll)
    right = _cross(lift, nose)
    tips = [tuple(pos[i] + side * half * right[i] for i in range(3)) for side in (-1, 1)]
    return tips + [tuple(pos[i] - tail[0] * nose[i] + tail[1] * lift[i] for i in range(3))] if tail else tips


def camera(eye):
    """A camera from an eye line's x, alt, z, yaw, pitch, roll, fov (the horizontal field of view)."""
    x, alt, z, yaw, pitch, roll, fov = eye
    ahead = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    right = (math.cos(yaw), 0.0, math.sin(yaw))
    up = _cross(ahead, right)
    c, s = math.cos(roll), math.sin(roll)
    return ((x, alt, z), ahead, tuple(right[i] * c - up[i] * s for i in range(3)), tuple(up[i] * c + right[i] * s for i in range(3)),
            math.tan(fov / 2))


def to_screen(cam, p, w, h, near=10.0):
    """Pixels of a w x h picture, or None for a point behind the camera or nearer than `near` feet in front."""
    seen = _seen(cam, p, w, h, near)
    return seen and seen[:2]


def _seen(cam, p, w, h, near):
    """As to_screen, with how far in front of the camera the point is: (x, y, feet)."""
    (x, alt, z), ahead, right, up, k = cam
    d = (p[0] - x, p[1] - alt, p[2] - z)
    depth = sum(d[i] * ahead[i] for i in range(3))
    if depth < near:
        return None
    sx = sum(d[i] * right[i] for i in range(3)) / depth / k
    sy = sum(d[i] * up[i] for i in range(3)) / depth / k * w / h
    return ((sx + 1) / 2 * w, (1 - sy) / 2 * h, depth)


def _curve(p0, p1, p2, p3, f):
    """The point a share `f` of the way from p1 to p2 on the uniform Catmull-Rom curve through the four."""
    return [.5 * (2 * p1[c] + (p2[c] - p0[c]) * f + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * f * f
                  + (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * f ** 3) for c in range(len(p1))]


def between(points, t):
    """A chained `curve` camera at t: [(time, pose)] through a uniform Catmull-Rom curve, as the viewer moves it
    (measured: within 0.05 ft halfway between points 0.5 s apart; a straight line was 1 to 4 ft off)."""
    i = 0
    while i < len(points) - 2 and points[i + 1][0] <= t:
        i += 1
    (a, p1), (b, p2) = points[i], points[i + 1]
    p0, p3 = points[max(0, i - 1)][1], points[min(len(points) - 1, i + 2)][1]
    return _curve(p0, p1, p2, p3, min(1.0, max(0.0, (t - a) / (b - a))))


def _smooth(points, reach):
    out = []
    for i in range(len(points)):
        if i == len(points) - 1:  # the wingtip itself stays where it is
            out.append(points[i])
            continue
        lo, hi = max(0, i - reach), min(len(points) - 1, i + reach)
        out.append(tuple(sum(points[j][c] for j in range(lo, hi + 1)) / (hi - lo + 1) for c in range(3)))
    return out


def _catmull(points, per=3):
    """Screen points (x, y, age, feet off) through a Catmull-Rom curve, `per` pieces between neighbours."""
    if len(points) < 3:
        return points
    out = []
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = points[max(0, i - 1)], points[i], points[i + 1], points[min(len(points) - 1, i + 2)]
        for s in range(per):
            out.append(tuple(_curve(p0, p1, p2, p3, s / per)))
    out.append(points[-1])
    return out


def _thin(points, gap):
    """Screen points without those nearer than `gap` pixels to the last one kept; the newest is always kept."""
    kept = [points[0]]
    for p in points[1:-1]:
        if math.hypot(p[0] - kept[-1][0], p[1] - kept[-1][1]) >= gap:
            kept.append(p)
    return kept + [points[-1]] if len(points) > 1 else kept


def _ribbon(points, widths):
    """A closed outline round a polyline of (x, y), `widths[i]` wide at each point."""
    left, right = [], []
    for i, (x, y) in enumerate(points):
        a, b = points[max(0, i - 1)], points[min(len(points) - 1, i + 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        n = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / n * widths[i] / 2, dx / n * widths[i] / 2
        left.append((x + nx, y + ny))
        right.append((x - nx, y - ny))
    outline = [(round(x * 4), round(y * 4)) for x, y in left + right[::-1]]  # \p3: a quarter of a pixel
    return f"m {outline[0][0]} {outline[0][1]} l " + " ".join(f"{x} {y}" for x, y in outline[1:])


def _clock(cs):
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _samples(rows, t, step, style):
    """The times a trail is drawn through at film time t, oldest first: the kept steps of the newest seconds, and
    before them a coarser grid fixed in film time (so the old part does not shimmer from frame to frame)."""
    first = max(rows[0][0], t - style["seconds"])
    coarse = max(step, round(max(style["coarse"], (style["seconds"] - style["fine"]) / style["most"]) / step) * step)
    edge = max(first, t - style["fine"])
    times = [first]
    for grid, lo, hi in ((coarse, first, edge), (step, edge, t)):
        n = math.ceil((lo - rows[0][0]) / grid - 1e-9)
        while rows[0][0] + n * grid < hi - 1e-6:
            s = rows[0][0] + n * grid
            if s > times[-1] + 1e-6:
                times.append(s)
            n += 1
    return times + [t], edge


def script(*args, **options):
    """The whole ASS script of `lines` as one text."""
    return "\n".join(lines(*args, **options)) + "\n"


def lines(frames, tracks, radii, colours, step, fps, w, h, style=STYLE, tail=None):
    """The lines of an ASS script drawing the trails of the aircraft in `colours` ({id: (r, g, b)}) on every frame,
    one after another, so a long script can go to its file without being held whole (measured: 1.5 to 3.4 MB a second
    of video). frames: [(film time, eye or None)], one per video frame from its first; tracks: {id: kept rows};
    radii: {id: visible radius in feet}; tail: the colour (r, g, b) of a third trail from the tip of each fin."""
    unit = h / 1080
    yield from ("[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
                "YCbCr Matrix: None", "", "[V4+ Styles]",
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
                "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
                "Style: T,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1", "",
                "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text")
    tips = {}  # (id, film time): the two wingtips, worked out once for every frame that draws through them
    for k, (t, eye) in enumerate(frames):
        # Frame k shows at k / fps: an event from floor(100 k / fps) to floor(100 (k + 1) / fps) centiseconds covers
        # it and no other frame.
        begin, end = math.floor(100 * k / fps), math.floor(100 * (k + 1) / fps)
        if eye is None or end <= begin:
            continue
        cam, span = camera(eye), f"{_clock(begin)},{_clock(end)}"
        for ident, rgb in colours.items():
            rows = tracks.get(ident)
            if not rows or not rows[0][0] <= t <= rows[-1][0]:
                continue
            fills = [f"&H{c[2]:02X}{c[1]:02X}{c[0]:02X}&" for c in ((rgb, rgb, tail) if tail else (rgb, rgb))]
            # How long the bright part lasts: the time this aircraft takes to cross `reach` of the picture's width,
            # where it is now (see the module's own words).
            here, before = at(rows, t, step)[0], at(rows, max(rows[0][0], t - .1), step)[0]
            wide = 2 * max(style["close"][0], math.dist(cam[0], here)) * cam[4]
            bright = min(style["seconds"], max(.08, style["reach"] * wide / max(50.0, math.dist(here, before) / .1)))
            times, edge = _samples(rows, t, step, style)
            for s in times:
                if (ident, s) not in tips:
                    pos, (yaw, pitch, roll) = at(rows, s, step)
                    size = radii.get(ident, 18)
                    tips[ident, s] = wingtips(pos, yaw, pitch, roll, size * style["span"], tail and tuple(size * share for share in style["tail"]))
            fine = next(i for i, s in enumerate(times) if s >= edge - 1e-6)
            for side, fill in enumerate(fills):
                points = [tips[ident, s][side] for s in times]
                points = points[:fine] + _smooth(points[fine:], style["smooth"])  # only the steps fine enough to jitter
                stamps = list(times)
                # The trail comes in over `rise` feet, and those lie ahead of the tip, the way it is going: it is whole
                # at the tip itself and its faint beginning is under the wing. Coming in behind the tip, it began a
                # metre behind the wing: seen from above in a fast roll, a trail torn off its wingtip.
                gap = math.dist(points[-1], points[-2]) if len(points) > 1 else 0
                if gap > 0:
                    points += [tuple(points[-1][c] + (points[-1][c] - points[-2][c]) / gap * style["rise"] * j / 4 for c in range(3)) for j in (1, 2, 3, 4)]
                    stamps += [t] * 4
                feet = [0.0]  # along the trail from the wingtip, newest first
                for a, b in zip(points[:0:-1], points[-2::-1]):
                    feet.append(feet[-1] + math.dist(a, b))
                runs, run = [], []
                for s, p, back in zip(stamps, points, feet[::-1]):
                    q = _seen(cam, p, w, h, style["close"][0])
                    # Behind the camera or at the lens the trail breaks; so it does a picture's width outside the
                    # picture, where nothing of it is seen. A point near the camera's own plane lands miles off the
                    # screen, and libass draws each outline into a bitmap as large as the box round it (measured,
                    # two aircraft circling with 30 s trails at 1080p60: 34 s of ffmpeg a second of film with those
                    # points, 1.2 s without, the picture the same to 5 parts in 255).
                    if q is None or not (-w <= q[0] <= 2 * w and -h <= q[1] <= 2 * h):
                        runs.append(run)
                        run = []
                    else:
                        run.append((*q[:2], (t - s) / style["seconds"], q[2], back))
                runs.append(run)
                for run in (r for r in runs if len(r) > 1):
                    yield from _bands(_thin(_catmull(run), style["thin"] * unit), span, fill, unit, style, bright, w / 2 / cam[4])


def _edges(style, bright):
    """The ages (shares of the trail's length) at which a trail has lost one more step of its brightness, from the
    wingtip (0) to its end (1), and the brightness of each piece between them as a share of the wingtip's."""
    def shine(age):
        return max(math.exp(-age * style["seconds"] / bright), style["remain"] * (1 - age) ** style["fade"])
    edges, bands = [0.0], style["bands"]
    for n in range(1, bands):
        lo, hi = edges[-1], 1.0
        for _ in range(18):  # shine only falls: halve the stretch until the step is found
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if shine(mid) > 1 - n / bands else (lo, mid)
        edges.append(hi)
    edges.append(1.0)
    return edges, [1 - (n + .5) / bands for n in range(bands)]


def _bands(curve, span, fill, unit, style, bright, scale):
    """A trail as pieces of falling opacity, from the wingtip back; each reaches one point into the younger piece so
    they join. `bright`: how many seconds the bright part lasts; `scale`: pixels a foot is, one foot from the camera."""
    out, (lens, clear) = [], style["close"]
    edges, levels = _edges(style, bright)
    # The last `rise` feet before the wingtip (the curve's end) are drawn point to point, each piece fainter than
    # the one behind it; the rest in its bands of age.
    head = next((i for i, c in enumerate(curve) if c[4] < style["rise"]), len(curve) - 1)
    body = curve[:max(1, head)]
    pieces = []
    for lo, hi, level in zip(edges, edges[1:], levels):
        index = [i for i, c in enumerate(body) if lo - 1e-9 <= c[2] <= hi + 1e-9]
        if index:
            pieces.append((body[index[0]:min(len(body), index[-1] + 2)], level))
    for a, b in zip(curve[max(0, head - 1):], curve[max(0, head - 1) + 1:]):
        pieces.append(([a, b], levels[0] * min(1.0, (a[4] + b[4]) / 2 / style["rise"])))
    for piece, level in pieces:
        off = sum(c[3] for c in piece) / len(piece)
        opacity = style["opacity"] * level * min(1.0, max(0.0, (off - lens) / (clear - lens)))
        if len(piece) < 2 or opacity < .01:
            continue
        # In feet, so thicker near the camera; never under a real pixel, or a draft's trail would be a third of one.
        widths = [max(1.0, unit * min(style["thickest"], max(style["thinnest"], (style["core"] + style["spread"] * c[2] * style["seconds"]) * scale / c[3] / unit)))
                  for c in piece]
        alpha = f"&H{255 - round(255 * opacity):02X}&"
        out.append(f"Dialogue: 0,{span},T,,0,0,0,,{{\\an7\\pos(0,0)\\p3\\bord0\\shad0\\1c{fill}\\1a{alpha}}}"
                   + _ribbon([(c[0], c[1]) for c in piece], widths))
    return out


def filters(path, height, style=STYLE):
    """ffmpeg filters laying the trails of the ASS script at `path` over a video `height` pixels high, with their
    glow: one input, one output."""
    gain = f"min(255,val*{style['glow_gain']:g})"
    soft = f",gblur=sigma={style['soft'] * height / 1080:.2f}" if style["soft"] else ""
    return (f"format=gbrp,split[tr0][tr1];[tr1]lutrgb=r=0:g=0:b=0,subtitles=filename='{graph_path(path)}'{soft},split[tr2][tr3];"
            f"[tr3]gblur=sigma={style['glow'] * height / 1080:.1f},lutrgb=r='{gain}':g='{gain}':b='{gain}'[tr4];"
            f"[tr2][tr4]blend=all_mode=addition[tr5];[tr0][tr5]blend=all_mode=screen")
