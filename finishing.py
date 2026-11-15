"""What ffmpeg does to a video once the viewer has rendered it: the lead-in cut off, the passes of a blurred one
blended, the film look and the haze laid on. One encode does all of it."""
from pathlib import Path
import subprocess

# With motion blur a video is rendered PASSES times at the viewer's most, FASTEST frames a second ("fps 1 to 120", it
# answers to more), each pass a quarter of a frame later, and ffmpeg weaves them and makes each frame the mean of all
# the frames in its time, as a 360° shutter does: real blur from the viewer's own frames. Half of them (a film camera's
# 180°) left too little to see; two frames a frame at 120 showed a double image, not a streak; ffmpeg's motion
# interpolation took 130 times the film's length.
FASTEST = 120
PASSES = 4
# The film look: the colour of a DaVinci Resolve grade (Film Look Creator), fitted as a LUT to stills of the same
# footage before and after it, then bloom, a red halation round highlights, a vignette and grain. As faint as the grade
# (its colour mixed in at half, corners 10% darker, grain about 1/255) it could not be seen, so all are stronger: the
# colour whole, corners about 20% darker, grain you see. Sizes scale with the picture's height.
LOOK = Path(__file__).resolve().parent / "looks" / "film.cube"
# Haze along the horizon. The Resolve grade lays it through a sky mask by an AI depth map; the viewer tells nothing of
# its camera to work depth out from, so here the horizon is where ground meets sky. Both are told apart by colour at an
# eighth of the size (sky bluish, ground coloured and not bluish), patches of ground too small to be land (an aircraft)
# are opened away, both are blurred, and where both are present the picture goes towards this grey-blue, which the film
# look turns into the grade's mist. Dark things, a canopy frame, keep clear. Tried on stills: the far ground and the
# sky just above it misted much as in Resolve; an aircraft against the sky and clouds left as they were.
HAZE = "0x808F9F"


def graph_path(path):
    """A file's path as it goes between single quotes in a filter graph: a colon there would end the option, and an
    apostrophe (a Windows account named O'Brien) the quotes."""
    return Path(path).as_posix().replace(":", r"\:").replace("'", r"'\\\''")


def look(height):
    """ffmpeg filters giving a video `height` pixels high the film look (see LOOK): one input, one output."""
    return (f"lut3d=file='{graph_path(LOOK)}':interp=tetrahedral,format=gbrp,split=3[lk0][lk1][lk2];"
            f"[lk1]curves=all='0/0 0.6/0 1/1',gblur=sigma={height / 108:.1f}[lkb];"
            f"[lk2]curves=all='0/0 0.75/0 1/1',gblur=sigma={height / 270:.1f},colorchannelmixer=gg=.4:bb=.15[lkh];"
            f"[lk0][lkb]blend=all_mode=screen:all_opacity=.3[lkm];[lkm][lkh]blend=all_mode=screen:all_opacity=.35,"
            f"vignette=angle=.3,format=yuv420p,noise=c0s=6:c0f=t+u")


def _step(x, a, c):
    """A smooth step of expression `x` from `a` to `c`, in ffmpeg's expressions."""
    return f"(st(9,clip(({x}-{a:g})/{c - a:g},0,1));ld(9)*ld(9)*(3-2*ld(9)))"


def haze(width, height):
    """ffmpeg filters laying haze along the horizon of a `width` x `height` video (see HAZE): one input, one output."""
    r, g, b = "r(X,Y)/255", "g(X,Y)/255", "b(X,Y)/255"
    light, blue = f"(.2126*{r}+.7152*{g}+.0722*{b})", f"({b}-{r})"
    most = f"max(max({r},{g}),{b})"
    colour = f"(({most}-min(min({r},{g}),{b}))/max({most},.001))"
    sky = f"{_step(blue, .02, .1)}*{_step(light, .12, .2)}"
    ground = f"(1-{_step(blue, 0, .06)})*{_step(colour, .05, .12)}*{_step(light, .1, .18)}"
    grey = lambda e: f"geq=r='255*{e}':g='255*{e}':b='255*{e}'"
    both = "0.85*clip(6*val/maxval,0,1)*maxval"  # full haze where sky and ground are each about a sixth
    sigma = height / 8 * .08
    return (f"format=gbrp,split=4[hz0][hz1][hz2][hz3];[hz2]scale={width // 8}:{height // 8}:flags=area,split[hs][hg];"
            f"[hs]{grey(sky)},gblur=sigma={sigma:.1f}[hsb];"
            f"[hg]{grey(ground)},{'erosion,' * 4}{'dilation,' * 4}gblur=sigma={sigma:.1f}[hgb];"
            f"[hsb][hgb]blend=all_mode=multiply,lutrgb=r='{both}':g='{both}':b='{both}',scale={width}:{height}[hb];"
            f"[hz3]format=gray,lut=c0='maxval*{_step('val/maxval', .2, .35)}',format=gbrp[hl];"
            f"[hb][hl]blend=all_mode=multiply[hm];[hz1]drawbox=c={HAZE}:t=fill[hc];[hz0][hc][hm]maskedmerge")


def style(width, height, film=False, hazy=False):
    """The filters for the chosen looks, the haze first as in the grade; "" for none."""
    return ",".join(([haze(width, height)] if hazy else []) + ([look(height)] if film else []))


def trim(source, target, seconds, filters=""):
    """`source` without its first `seconds`, as `target`, through `filters` (see style). Re-encoded so the cut is
    frame-exact (a stream copy cuts at keyframes), close to the viewer's own quality (about 21 Mbit/s at 1080p60,
    AAC 192 kbit/s)."""
    encode(["-ss", f"{seconds:.6f}", "-i", str(source)], ["-vf", filters] if filters else [], [source], target,
           "cut the lead-in off the video")


def blend(sources, target, seconds, fps, filters=""):
    """The passes of a blurred video (see PASSES), as `target` at `fps` with the first pass's sound: as `trim`."""
    rate = FASTEST * len(sources)
    each = rate // fps  # woven frames in one frame's time: all of them make it
    weave = "".join(f"[{n}:v]setpts=PTS+{n}/{rate}/TB[w{n}];" for n in range(len(sources)))
    graph = (weave + "".join(f"[w{n}]" for n in range(len(sources))) + f"interleave=nb_inputs={len(sources)},"
             f"tmix=frames={each},select='eq(mod(n,{each}),{each - 1})',setpts=N/{fps}/TB"
             + (f",{filters}" if filters else "") + "[v]")
    encode([a for s in sources for a in ("-ss", f"{seconds:.6f}", "-i", str(s))],
           ["-filter_complex", graph, "-map", "[v]", "-map", "0:a?", "-r", str(fps)], sources, target, "blend the motion blur")


DISSOLVE = .3  # seconds two clips share when one dissolves into the next


def assemble(clips, music, target, width, height, fps):
    """Finished videos in a row as one video, with music under them: the assembly. clips: [{file, start, seconds,
    join, sound}], each playing `seconds` of its file from `start`, at `sound` times its own loudness, and joining
    the next with a "cut" or a "dissolve" (taken as a cut when either clip is too short to share DISSOLVE). music:
    None or {file, from, level, in, out}: played from `from`, faded in and out, and ended with the video. Returns
    the video's length."""
    parts, video, sound, total = [], "[v0]", "[a0]", clips[0]["seconds"]
    for n, clip in enumerate(clips):
        cut = f"start={clip['start']:.3f}:duration={clip['seconds']:.3f}"
        # Every clip to one size, frame rate and time base: the joins below take no others.
        parts.append(f"[{n}:v]trim={cut},setpts=PTS-STARTPTS,scale={width}:{height},setsar=1,format=yuv420p,fps={fps},settb=AVTB[v{n}]")
        parts.append(f"[{n}:a]atrim={cut},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={clip['sound']:.3f}[a{n}]")
        if n == 0:
            continue
        if clips[n - 1]["join"] == "dissolve" and min(clips[n - 1]["seconds"], clip["seconds"]) > 2 * DISSOLVE:
            parts.append(f"{video}[v{n}]xfade=transition=fade:duration={DISSOLVE}:offset={total - DISSOLVE:.3f},fps={fps},settb=AVTB[x{n}]")
            parts.append(f"{sound}[a{n}]acrossfade=d={DISSOLVE}[y{n}]")
            total += clip["seconds"] - DISSOLVE
        else:
            parts.append(f"{video}[v{n}]concat=n=2:v=1:a=0,fps={fps},settb=AVTB[x{n}]")
            parts.append(f"{sound}[a{n}]concat=n=2:v=0:a=1[y{n}]")
            total += clip["seconds"]
        video, sound = f"[x{n}]", f"[y{n}]"
    inputs = [a for clip in clips for a in ("-i", str(clip["file"]))]
    if music:
        inputs += ["-i", str(music["file"])]
        fades = (f",afade=t=in:st=0:d={music['in']:.3f}" if music["in"] > 0 else "") + (
            f",afade=t=out:st={max(0, total - music['out']):.3f}:d={music['out']:.3f}" if music["out"] > 0 else "")
        parts.append(f"[{len(clips)}:a]atrim=start={music['from']:.3f}:duration={total:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                     f"aformat=channel_layouts=stereo,volume={music['level']:.3f}{fades}[m]")
        # normalize=0: the film's sound stays as loud as it was; music that ends first leaves it alone.
        parts.append(f"{sound}[m]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[mix]")
        sound = "[mix]"
    encode(inputs, ["-filter_complex", ";".join(parts), "-map", video, "-map", sound, "-r", str(fps)], [], Path(target),
           "assemble the clips")
    return total


def encode(inputs, graph, sources, target, what):
    try:
        done = subprocess.run(
            ["ffmpeg", "-v", "error", "-y", *inputs, *graph, "-c:v", "libx264", "-crf", "16",
             "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(target)],
            capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        for source in sources:
            source.unlink(missing_ok=True)
    if done.returncode:
        target.unlink(missing_ok=True)
        raise ValueError(f"ffmpeg could not {what}: " + (done.stderr.strip().splitlines() or ["no reason given"])[-1])
