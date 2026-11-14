"""A data overlay to lay over a render in an editor: the lead aircraft's speed, height, climb and G, and the range
and closure to his opponent, in a card, as a transparent ProRes 4444 film. libass draws it through ffmpeg. One
film fits a 16:9 timeline (the card in the lower left corner), one a 9:16 reel (the card at the top, clear of the
apps' own bars, half as large again to be read on a phone).

ffmpeg's subtitles filter squares the opacity of what it draws onto a transparent picture (measured: a panel
meant 58% opaque came out 34%), so the picture and its transparency are drawn apart, each fully opaque: once in
colour, once in greys that are the opacity, and joined with alphamerge."""
from pathlib import Path
import subprocess
import tempfile

MPH = 3600 / 5280
STEP = .1  # the numbers change ten times a second: faster cannot be read
LAYOUTS = {"16x9": {"size": (1920, 1080), "scale": 1.0, "left": 48, "bottom": 48},
           "9x16": {"size": (1080, 1920), "scale": 1.5, "left": 64, "top": 260}}
# The card, in units of a 1080-high picture: one outer margin, 24 inside, Source Sans Pro, digits of one width.
WIDTH, PAD, HEIGHT, THEM = 452, 24, 213, 62  # THEM: the opponent's row, below a line, when there is an opponent
PANEL, EDGE, LABEL, WHITE = (12, 16, 24), (255, 255, 255), (201, 206, 214), (255, 255, 255)
PANEL_OPACITY, EDGE_OPACITY = .58, .30
SEMIBOLD, REGULAR = "Source Sans Pro SemiBold", "Source Sans Pro"
DESCENT = .273  # below the baseline, as a share of the CSS size: libass sets a line's bottom there (font metrics)
ASS_SIZE = 1.257  # libass sizes a font by ascent plus descent; Source Sans Pro's are 1.257 of its em


def number(value, digits=0, sign=False):
    """1,460 / +2,110 / −45 / 7.8: thousands commas, a true minus sign (as wide as a digit), a plus when asked."""
    text = f"{abs(value):,.{digits}f}"
    if round(value, digits) < 0:
        return "−" + text
    return ("+" if sign and round(value, digits) > 0 else "") + text


def safe(text):
    """A pilot's name as plain text: braces and backslashes would be read as styling."""
    return str(text).replace("{", "(").replace("}", ")").replace("\\", "/")


def clock(seconds):
    cs = max(0, round(seconds * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def colour(rgb, opacity, matte):
    if matte:  # the transparency pass: grey is opacity
        rgb = (round(255 * opacity),) * 3
    return f"&H{rgb[2]:02X}{rgb[1]:02X}{rgb[0]:02X}&"


def over(top, opacity, under, under_opacity):
    """One translucent colour over another, as one colour and one opacity."""
    total = opacity + (1 - opacity) * under_opacity
    return tuple(round((opacity * t + (1 - opacity) * under_opacity * u) / total) for t, u in zip(top, under)), total


def text(x, baseline, parts, matte, right=False):
    """One line of text in pieces of (string, CSS size, font, colour), sharing a baseline, as libass override tags.
    Labels, in capitals, are spaced out by 0.08 of their size."""
    tallest = max(size for _, size, _, _ in parts)
    body = "".join(f"{{\\fn{font}\\fs{size * ASS_SIZE:.1f}\\fsp{size * .08 if string.strip().isupper() else 0:.2f}"
                   f"\\c{colour(rgb, 1, matte)}}}{string}" for string, size, font, rgb in parts)
    return f"{{\\an{3 if right else 1}\\pos({x:.1f},{baseline + DESCENT * tallest:.1f})\\bord0\\shad0}}{body}"


def box(x, y, width, height, radius, fill, opacity, matte, edge=None):
    r, k = radius, radius * .45  # k: the Bézier handle that makes a quarter circle
    path = (f"m {r} 0 l {width - r} 0 b {width - k} 0 {width} {k} {width} {r} l {width} {height - r} "
            f"b {width} {height - k} {width - k} {height} {width - r} {height} l {r} {height} b {k} {height} 0 {height - k} 0 {height - r} "
            f"l 0 {r} b 0 {k} {k} 0 {r} 0")
    outline = f"\\bord{edge[2]}\\3c{colour(edge[0], edge[1], matte)}\\3a&H00&" if edge else "\\bord0"
    return f"{{\\an7\\pos({x:.1f},{y:.1f})\\shad0{outline}\\1c{colour(fill, opacity, matte)}\\1a&H00&\\p1}}{path}{{\\p0}}"


def card(layout, hero_name, target_name, matte):
    """The card's fixed parts and where its numbers go: (static lines, {slot: (x, baseline, right)}, scale)."""
    spec = LAYOUTS[layout]
    s = spec["scale"]
    height = HEIGHT + (THEM if target_name is not None else 0)
    left = spec["left"]
    top = spec["top"] if "top" in spec else spec["size"][1] - spec["bottom"] - height * s
    x = left + PAD * s
    lines = [box(left, top, WIDTH * s, height * s, 4 * s, PANEL, PANEL_OPACITY, matte, (EDGE, EDGE_OPACITY, 1))]
    label = lambda string: (string.upper(), 15 * s, SEMIBOLD, LABEL)
    row = top + 150 * s  # baseline of the row labels
    lines += [text(x, top + 34 * s, [label(f"{safe(hero_name)} · Speed")], matte),
              text(x, row, [label("Alt")], matte), text(x + 140 * s, row, [label("Climb")], matte),
              text(x + 321 * s, row, [label("G")], matte)]
    slots = {"speed": (x, top + 107 * s), "alt": (x, top + 187 * s), "climb": (x + 140 * s, top + 187 * s),
             "g": (x + 321 * s, top + 187 * s)}
    if target_name is not None:
        rule, opacity = over(EDGE, EDGE_OPACITY, PANEL, PANEL_OPACITY)
        lines.append(box(x, top + 211 * s, (WIDTH - 2 * PAD) * s, max(1, s), 0, rule, opacity, matte))
        slots.update(range=(x, top + 248 * s), closing=(left + (WIDTH - PAD) * s, top + 248 * s))
    return lines, slots, s


def numbers(slots, s, hero, pair, target_name, matte):
    """The card's numbers at one moment: hero and pair are samples (flight_data), pair None when he is gone."""
    big = lambda value, unit, size: [(value, size * s, SEMIBOLD, WHITE), (" " + unit, size * .5 * s, REGULAR, LABEL)]
    label = lambda string: (string.upper() + " ", 15 * s, SEMIBOLD, LABEL)
    out = [text(*slots["speed"], [(number(hero["tas"] * MPH), 72 * s, SEMIBOLD, WHITE), (" mph", 32 * s, REGULAR, LABEL)], matte),
           text(*slots["alt"], big(number(hero["alt"]), "ft", 32), matte),
           text(*slots["climb"], big(number(round(hero["climb"] * 6) * 10, sign=True), "ft/min", 32), matte),
           text(*slots["g"], [(number(hero["g"], 1), 32 * s, SEMIBOLD, WHITE)], matte)]
    if "range" in slots:
        dash = "–"
        rng = number(pair["range"]) if pair else dash
        closing = number(pair["closure"] * MPH) if pair else dash
        out += [text(*slots["range"], [label(safe(target_name)), (rng, 22 * s, SEMIBOLD, WHITE), (" ft", 16 * s, REGULAR, LABEL)], matte),
                text(*slots["closing"], [label("Closing"), (closing, 22 * s, SEMIBOLD, WHITE), (" mph", 16 * s, REGULAR, LABEL)], matte, right=True)]
    return out


def script(layout, segments, matte=False):
    """An .ass script for the whole film. segments: the shots in the order the video plays them, each
    {start, end, hero: samples, hero_name, pair: samples or None, target_name or None}; samples every 0.02 s or so."""
    width, height = LAYOUTS[layout]["size"]
    events, offset = [], 0.0
    for seg in segments:
        length = seg["end"] - seg["start"]
        static, slots, s = card(layout, seg["hero_name"], seg.get("target_name"), matte)
        events += [(0, offset, offset + length, line) for line in static]
        n = max(1, round(length / STEP))
        for k in range(n):
            t = seg["start"] + k * STEP
            hero = nearest(seg["hero"], t)
            if hero is None:
                continue
            pair = nearest(seg.get("pair"), t)  # None once the opponent has left the film: his numbers go blank
            start, end = offset + k * STEP, offset + min(length, (k + 1) * STEP)
            events += [(1, start, end, line) for line in numbers(slots, s, hero, pair, seg.get("target_name"), matte)]
        offset += length
    # YCbCr Matrix None: colours as written. Without it libass shifts them for old players (measured: the panel's
    # 12,16,24 came out 26,30,37 and the 58% grey of the transparency 56%).
    head = ["[Script Info]", "ScriptType: v4.00+", "YCbCr Matrix: None", f"PlayResX: {width}", f"PlayResY: {height}", "WrapStyle: 2",
            "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Card,{SEMIBOLD},20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
            "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    return "\n".join(head + [f"Dialogue: {layer},{clock(a)},{clock(b)},Card,,0,0,0,,{line}" for layer, a, b, line in events]) + "\n"


def nearest(samples, t):
    """The sample nearest film time t, or None when the aircraft is not in the film within 0.1 s of it."""
    if not samples:
        return None
    lo, hi = 0, len(samples) - 1
    while lo < hi:  # samples are in time order
        mid = (lo + hi) // 2
        if samples[mid]["t"] < t:
            lo = mid + 1
        else:
            hi = mid
    best = min((samples[i] for i in (lo - 1, lo) if 0 <= i < len(samples)), key=lambda row: abs(row["t"] - t))
    return best if abs(best["t"] - t) <= .1 else None


def render(layout, segments, target, height, fps):
    """The overlay as a ProRes 4444 film with transparency, `height` pixels high (the render's height for 16:9),
    at the render's frame rate and length. Needs ffmpeg on the PATH."""
    width = round(height * LAYOUTS[layout]["size"][0] / LAYOUTS[layout]["size"][1] / 2) * 2
    length = sum(seg["end"] - seg["start"] for seg in segments)
    target = Path(target).resolve()  # ffmpeg runs in a folder of its own
    with tempfile.TemporaryDirectory() as folder:
        # Bare file names: inside a filter graph a Windows path's colon and backslashes would need escaping.
        Path(folder, "colour.ass").write_text(script(layout, segments), encoding="utf-8")
        Path(folder, "matte.ass").write_text(script(layout, segments, matte=True), encoding="utf-8")
        # Colour as the overlays already laid over films in Resolve on this desk's PC: BT.709, video range, tagged,
        # alpha Straight on import. 8-bit alpha is plenty for text edges and halves the file (16-bit: 51 MB, 8-bit:
        # 29 MB for 5 s at 1080p60).
        graph = ("[0]format=gbrp,split[a][b];[a]subtitles=colour.ass[c];[b]subtitles=matte.ass,extractplanes=r[m];"
                 "[c][m]alphamerge,scale=out_color_matrix=bt709:out_range=tv:flags=accurate_rnd+full_chroma_int,format=yuva444p10le,"
                 "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv[o]")
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r={fps}:d={length:.3f}",
             "-filter_complex", graph, "-map", "[o]", "-c:v", "prores_ks", "-profile:v", "4444", "-alpha_bits", "8",
             "-vendor", "apl0", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
             str(target)],
            cwd=folder, capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if done.returncode:
        raise ValueError("ffmpeg could not make the overlay: " + done.stderr.strip()[-300:])
    return target
