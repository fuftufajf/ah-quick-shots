"""Render a trial shot through the live desk with any render options, then take it out of the plan again.

    python dev/try_render.py <out.mp4> '<shot fields as JSON>' '<export_cut options as JSON>'
    python dev/try_render.py wide.mp4 '{"kind": "fixed", "start": 209, "end": 215, "distance": 2500, "height": 800, "orbit": 90, "fov": 70}' '{"trails": {"seconds": 60}}'

The shot is the plan's first one with the given fields changed, saved as "trails trial", rendered at final quality
with the options, copied to <out.mp4>, and removed from the plan again with its video moved to the Recycle Bin.
It uses the pilot's own desk (Film Viewer running, a film open): the render shows on their page, and it must not
run while they are working.
"""
import json
import shutil
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8766"


def call(path, body=None):
    request = urllib.request.Request(BASE + path, json.dumps(body).encode() if body is not None else None, {"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as reply:
        return json.load(reply)


def act(body):
    """One desk action, waited out; the state after it."""
    call("/api/action", {**body, "expected_revision": call("/api/state")["project"]["revision"]})
    while True:
        time.sleep(.5)
        state = call("/api/state")
        if not state["job"]["running"]:
            if state["job"]["error"]:
                raise SystemExit(f"{body['action']}: {state['job']['error']}")
            return state


def main():
    out, fields, options = sys.argv[1], json.loads(sys.argv[2]), json.loads(sys.argv[3])
    project = call("/api/state")["project"]
    trial = {**project["shots"][0], "id": "trial", "name": "trails trial", **fields}
    call("/api/project", {"expected_revision": project["revision"], "shots": project["shots"] + [trial]})
    try:
        began = time.time()
        state = act({"action": "export_cut", "shot_ids": ["trial"], "quality": "final", **options})
        shutil.copy(state["preview"]["file"], out)
        print(f"rendered in {time.time() - began:.1f} s to {out}")
    finally:
        project = call("/api/state")["project"]
        gone = call("/api/forget", {"expected_revision": project["revision"], "shot_ids": ["trial"], "files": True})
        print("trial taken out; the plan is", [s["name"] for s in gone["project"]["shots"]])


if __name__ == "__main__":
    main()
