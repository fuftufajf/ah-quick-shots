"""Trails from the wingtips, drawn over a render: two smooth lines behind each aircraft of the shot, fading with
age, with a soft glow, in a colour of the user's choosing.

Where the viewer's camera stands at every frame comes from its own projection (`project`'s eye lines); where the
wingtips were, from the kept tracks. Checked against the viewer's own placing of the aircraft at 1080p: 0.7 px
(median) on a moving camera, 0.15 px on airframe cameras rolled up to 70 degrees. libass draws the lines through
ffmpeg; ffmpeg blurs a copy for the glow and lays the light over the picture (screen), which is far faster than
libass blurring each piece (42 s against 200 s for 7 s at 1080p60)."""
import math
import re

STYLE = {
    "seconds": 2.5,      # how far back a trail reaches
    "span": .88,         # the wingtip's offset as a share of the aircraft's visible radius (a Yak's radius 17.9 ft, its tips 15.8 ft out)
    "width": 1.8,        # the line at the wingtip, pixels of a 1080-high picture
    "tail": .25,         # its width at the far end, as a share
    "opacity": .75,      # at the wingtip
    "fade": 1.8,         # opacity falls as (1 - age / seconds) ** fade
    "glow": 7.0,         # the glow's blur, pixels of a 1080-high picture
    "glow_gain": 3.5,    # the blurred line brightened this many times
    "bands": 14,         # steps of opacity along a trail
    "smooth": 2,         # wingtip positions averaged over this many samples either side (not the newest)
}


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


def wingtips(pos, yaw, pitch, roll, half):
    """Left and right wingtip in the world. Yaw 0 looks toward +z and +pi/2 toward -x, pitch up and roll right are
    positive (as flight_data._axes)."""
    nose = (-math.sin(yaw) * math.cos(pitch), math.sin(pitch), math.cos(yaw) * math.cos(pitch))
    flat = (math.cos(yaw), 0.0, math.sin(yaw))
    level = _cross(nose, flat)
    lift = tuple(level[i] * math.cos(roll) + flat[i] * math.sin(roll) for i in range(3))
    right = _cross(lift, nose)
    return [tuple(pos[i] + side * half * right[i] for i in range(3)) for side in (-1, 1)]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


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
    (x, alt, z), ahead, right, up, k = cam
    d = (p[0] - x, p[1] - alt, p[2] - z)
    depth = sum(d[i] * ahead[i] for i in range(3))
    if depth < near:
        return None
    sx = sum(d[i] * right[i] for i in range(3)) / depth / k
    sy = sum(d[i] * up[i] for i in range(3)) / depth / k * w / h
    return ((sx + 1) / 2 * w, (1 - sy) / 2 * h)


def between(points, t):
    """A chained `curve` camera at t: [(time, pose)] through a uniform Catmull-Rom curve, as the viewer moves it
    (measured: within 0.05 ft halfway between points 0.5 s apart; a straight line was 1 to 4 ft off)."""
    i = 0
    while i < len(points) - 2 and points[i + 1][0] <= t:
        i += 1
    (a, p1), (b, p2) = points[i], points[i + 1]
    p0, p3 = points[max(0, i - 1)][1], points[min(len(points) - 1, i + 2)][1]
    f = min(1.0, max(0.0, (t - a) / (b - a)))
    return [.5 * (2 * p1[c] + (p2[c] - p0[c]) * f + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * f * f
                  + (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * f ** 3) for c in range(len(p1))]


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
    """Screen points (x, y, age) through a Catmull-Rom curve, `per` pieces between neighbours."""
    if len(points) < 3:
        return points
    out = []
    for i in range(len(points) - 1):
        p0, p1, p2, p3 = points[max(0, i - 1)], points[i], points[i + 1], points[min(len(points) - 1, i + 2)]
        for s in range(per):
            f = s / per
            out.append(tuple(.5 * (2 * p1[c] + (p2[c] - p0[c]) * f + (2 * p0[c] - 5 * p1[c] + 4 * p2[c] - p3[c]) * f * f
                                   + (3 * p1[c] - p0[c] - 3 * p2[c] + p3[c]) * f ** 3) for c in range(3)))
    out.append(points[-1])
    return out


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


def script(frames, tracks, radii, ids, step, fps, w, h, rgb=(255, 255, 255), style=STYLE):
    """An ASS script drawing the trails of aircraft `ids` on every frame. frames: [(film time, eye or None)], one
    per video frame from its first; tracks: {id: kept rows}; radii: {id: visible radius in feet}."""
    unit = h / 1080
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {w}", f"PlayResY: {h}", "WrapStyle: 2", "ScaledBorderAndShadow: yes",
             "YCbCr Matrix: None", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, "
             "StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
             "Style: T,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1", "",
             "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    fill = f"&H{rgb[2]:02X}{rgb[1]:02X}{rgb[0]:02X}&"
    for k, (t, eye) in enumerate(frames):
        # Frame k shows at k / fps: an event from floor(100 k / fps) to floor(100 (k + 1) / fps) centiseconds covers
        # it and no other frame.
        begin, end = math.floor(100 * k / fps), math.floor(100 * (k + 1) / fps)
        if eye is None or end <= begin:
            continue
        cam, span = camera(eye), f"{_clock(begin)},{_clock(end)}"
        for ident in ids:
            rows = tracks.get(ident)
            if not rows or not rows[0][0] <= t <= rows[-1][0]:
                continue
            first = max(rows[0][0], t - style["seconds"])
            n0, n1 = math.ceil((first - rows[0][0]) / step - 1e-9), math.floor((t - rows[0][0]) / step - 1e-9)
            times = [first] + [rows[0][0] + n * step for n in range(n0, n1 + 1) if first + 1e-6 < rows[0][0] + n * step < t - 1e-6] + [t]
            tips = ([], [])
            for s in times:
                pos, (yaw, pitch, roll) = at(rows, s, step)
                for side, p in enumerate(wingtips(pos, yaw, pitch, roll, radii.get(ident, 18) * style["span"])):
                    tips[side].append(p)
            for side in tips:
                runs, run = [], []
                for s, p in zip(times, _smooth(side, style["smooth"])):
                    q = to_screen(cam, p, w, h)
                    if q is None:  # behind the camera: the trail breaks there
                        runs.append(run)
                        run = []
                    else:
                        run.append((q[0], q[1], (t - s) / style["seconds"]))
                runs.append(run)
                for run in (r for r in runs if len(r) > 1):
                    lines += _bands(_catmull(run), span, fill, unit, style)
    return "\n".join(lines) + "\n"


def _bands(curve, span, fill, unit, style):
    """A trail as pieces of falling opacity, from the wingtip back; each reaches one point into the younger piece so
    they join."""
    out, bands = [], style["bands"]
    for band in range(bands):
        lo, hi = band / bands, (band + 1) / bands
        index = [i for i, c in enumerate(curve) if lo - 1e-9 <= c[2] <= hi + 1e-9]
        if not index:
            continue
        piece = curve[index[0]:min(len(curve), index[-1] + 2)]
        opacity = style["opacity"] * (1 - (lo + hi) / 2) ** style["fade"]
        if len(piece) < 2 or opacity < .01:
            continue
        widths = [unit * style["width"] * (1 - (1 - style["tail"]) * c[2]) for c in piece]
        alpha = f"&H{255 - round(255 * opacity):02X}&"
        out.append(f"Dialogue: 0,{span},T,,0,0,0,,{{\\an7\\pos(0,0)\\p3\\bord0\\shad0\\1c{fill}\\1a{alpha}}}"
                   + _ribbon([(c[0], c[1]) for c in piece], widths))
    return out


def filters(path, height, style=STYLE):
    """ffmpeg filters laying the trails of the ASS script at `path` over a video `height` pixels high, with their
    glow: one input, one output."""
    ass = path.as_posix().replace(":", r"\:")  # a filter graph reads a colon as the end of an option
    gain = f"min(255,val*{style['glow_gain']:g})"
    return (f"format=gbrp,split[tr0][tr1];[tr1]lutrgb=r=0:g=0:b=0,subtitles=filename='{ass}',split[tr2][tr3];"
            f"[tr3]gblur=sigma={style['glow'] * height / 1080:.1f},lutrgb=r='{gain}':g='{gain}':b='{gain}'[tr4];"
            f"[tr2][tr4]blend=all_mode=addition[tr5];[tr0][tr5]blend=all_mode=screen")
