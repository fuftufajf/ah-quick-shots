"""Look at cameras on the live desk without a browser. The desk and Film Viewer must be running, with a film open.

    python dev/try_camera.py <out folder> <start> <end> <you> <opponent or -> '<camera as JSON>' ['<camera>' ...]
    python dev/try_camera.py out 80 87.5 15 33 '{"kind": "pair", "orbit": 5, "distance": 700, "height": 20, "fov": 12}'

A camera is the fields of a shot that differ from a plain one. For each, the stretch is rendered as an unsaved
draft through the desk's own API (nothing is saved to the plan) and this prints: how long it took, how loud each
second of the clip is and, for a camera that can be measured, where the viewer's projection puts both aircraft
at each step: x and y from -1 to 1, then the share of the picture's width. The clip and one frame a second,
tiled, are left in the out folder as cameraN.mp4 and cameraN.png.

Reading the loudness: the pilot's view, or a camera on the airframe, peaks above -24 dB when the clip has its
sound and stays under when it lost it (see the film's sound in docs/how-it-works.md). A camera standing in
the world hears the aircraft from where it stands and is quieter anyway: compare it with itself.

It needs ffmpeg on the path, and it uses the pilot's own desk: what it renders shows on their page, and it must
not run while they are working. To look without touching anything, see "Checks" in docs/how-it-works.md.
"""
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8766"
ROOT = Path(__file__).resolve().parent.parent / ".quickshots"


def call(path, body=None):
    request = urllib.request.Request(BASE + path, json.dumps(body).encode() if body else None, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def act(body):
    """One desk action, waited out; the state after it."""
    call("/api/action", {**body, "expected_revision": call("/api/state")["project"]["revision"]})
    while True:
        state = call("/api/state")
        if not state["job"]["running"]:
            if state["job"]["error"]:
                raise SystemExit(f"{body['action']}: {state['job']['error']}")
            return state
        time.sleep(.3)


def loudness(path):
    """Each second's level in the band of a voice on the radio, in dB."""
    chain = "aresample=8000,highpass=f=300,lowpass=f=3000,aformat=sample_fmts=s16:channel_layouts=mono"
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-af", chain, "-f", "s16le", "-"], capture_output=True, check=True).stdout
    samples = struct.unpack(f"<{len(raw) // 2}h", raw[:len(raw) // 2 * 2])
    return [round(20 * math.log10(max(math.sqrt(sum(s * s for s in samples[i:i + 8000]) / 8000), 1) / 32768))
            for i in range(0, len(samples) - 7999, 8000)]


def framing(you, them):
    """What `project` found for the last candidate measured: {time: {aircraft: where}}, the camera of each step only."""
    rows, order = {}, []
    for line in (ROOT / "projection.txt").read_text().splitlines():
        kind, when, camera, *rest = line.split()
        if kind == "eye":
            order.append(when) if when not in order else None
        elif int(camera) in (1, order.index(when) + 1):  # one camera for the whole shot, or one per step
            rows.setdefault(when, {})[int(rest[0])] = " ".join(rest[1:]) if kind == "on" else kind
    return {when: {ident: rows.get(when, {}).get(ident, "not in the film") for ident in (you, them) if ident is not None} for when in order}


def main():
    out, start, end, you = Path(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3]), int(sys.argv[4])
    them = None if sys.argv[5] == "-" else int(sys.argv[5])
    out.mkdir(parents=True, exist_ok=True)
    for number, text in enumerate(sys.argv[6:], 1):
        shot = {"id": "try", "name": "try", "start": start, "end": end, "kind": "pair", "hero": you, "target": them, "side": "right",
                "orbit": 30, "distance": 120, "height": 20, "fov": 65, "roll": 0, "watch": "both", **json.loads(text)}
        began = time.time()
        state = act({"action": "clip", "start": start, "end": end, "shot": shot, "hero": you})
        took = time.time() - began
        clip = out / f"camera{number}.mp4"
        with urllib.request.urlopen(BASE + state["preview"]["url"], timeout=60) as reply:
            clip.write_bytes(reply.read())
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-vf", "fps=1,scale=480:-1,tile=4x4", "-frames:v", "1",
                        str(clip.with_suffix(".png"))], check=True)
        levels = loudness(clip)
        print(f"camera {number}: {text}\n  clip in {took:.1f} s, sound peaks at {max(levels)} dB {levels}")
        if shot["kind"] not in ("recorded", "weapon"):
            state = act({"action": "suggest", "shots": [shot]})
            print("  rating", {key: value for key, value in state["proposals"][0].items() if key != "id"})
            for when, found in framing(you, them).items():
                print("   ", when, found)


if __name__ == "__main__":
    main()
