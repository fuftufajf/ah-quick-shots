"""Local HTTP surface for the page. No viewer I/O on request threads. The API: docs/how-it-works.md."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit
import urllib.request
import webbrowser

from director_core import MUSIC, Conflict, Desk

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8766
# A desk keeps running with the code it started with. When a new version is unzipped over it, the page (read
# from disk) is new and the desk is not: the page's newer actions came back "Unknown action". The desk notices.
# Every module beside this one, not a list kept by hand: a list had missed the overlay and the trails.
CODE = sorted(path for path in ROOT.glob("*.py") if path.name != "test_director.py") + [
    ROOT / "ui" / "dist" / "index.html", ROOT / "looks" / "film.cube"]
# Which release this copy is: the Windows package says so in VERSION; a copy of the source is its own.
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip() if (ROOT / "VERSION").exists() else "source"


# Python takes file types from the Windows registry, where some programs make .js plain text: the browser then runs
# no script and the page stays blank. The page's own files have their types fixed here.
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".mp4": "video/mp4", ".bmp": "image/bmp",
         ".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".flac": "audio/flac", ".ogg": "audio/ogg"}


def code_stamp():
    return [path.stat().st_mtime_ns if path.exists() else None for path in CODE]


def restart(server):
    """Start this desk again with the code now on disk: the port is let go first, so the new one can take it."""
    server.shutdown()
    server.server_close()
    subprocess.Popen([sys.executable, str(ROOT / "director_api.py")], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    os._exit(0)


class Handler(BaseHTTPRequestHandler):
    def allowed(self):
        authority = self.headers.get("Host", "")
        if authority not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            self.send_error(403, "Local host required")
            return False
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"):
            self.send_error(403, "Same origin required")
            return False
        return True

    def send_json(self, status, value):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.allowed():
            return
        relative = unquote(urlsplit(self.path).path)
        if relative == "/api/state":
            return self.send_json(200, {**self.server.desk.snapshot(), "stale": code_stamp() != self.server.code, "folder": str(ROOT),
                                        "version": VERSION})
        if relative == "/api/flight":
            data = self.server.desk.flight_data()
            return self.send_json(200, data) if data else self.send_json(404, {"message": "Read a stretch's flight first"})
        desk = self.server.desk
        if video := re.fullmatch(r"/media/video/([a-f0-9]{32})", relative):  # a finished video on record: an assembly's clip
            path = desk.video(video[1])
        elif re.fullmatch(rf"/media/music/[a-f0-9]{{32}}\.({MUSIC})", relative):
            path = desk.root / relative[7:]
        elif relative == "/media/assembly":  # the assembly made last
            made = desk.data["assembling"]
            path = Path(made["file"]) if made and made["file"] else None
        elif relative.startswith("/media/"):
            if not re.fullmatch(r"/media/(clips/)?[a-f0-9]{32}\.(bmp|mp4)", relative):
                return self.send_error(404)
            path = desk.media_root / relative[7:]
        else:
            base = ROOT / "ui" / "dist"
            path = (base / (relative.lstrip("/") or "index.html")).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                return self.send_error(404)
        if path is None or not path.is_file():
            return self.send_error(404)
        size, start, end = path.stat().st_size, 0, path.stat().st_size - 1
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d+)-(\d*)", requested)
            if not match:
                return self.send_error(416)
            start = int(match[1])
            end = min(end, int(match[2]) if match[2] else end)
            if start > end:
                return self.send_error(416)
        self.send_response(206 if requested else 200)
        self.send_header("Content-Type", TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if requested:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with path.open("rb") as source:
                source.seek(start)
                remaining = end-start+1
                while remaining:
                    chunk = source.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except ConnectionError:
            pass  # Seeking in the browser replaces its previous media request; a download can be given up.

    def do_POST(self):
        if not self.allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if self.path == "/api/music":  # the file itself, its name in a header: a page cannot tell the desk a path
                if not 0 < length <= 100 * 2 ** 20:
                    raise ValueError("A music file can be up to 100 MB")
                return self.send_json(200, self.server.desk.keep_music(unquote(self.headers.get("X-Name", "")), self.rfile.read(length)))
            if not 0 < length <= 512 * 1024:
                raise ValueError("Request must be 1 to 524288 bytes")
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ValueError("Request must be an object")
            if self.path == "/api/project":
                return self.send_json(200, self.server.desk.save(raw))
            if self.path == "/api/forget":
                return self.send_json(200, self.server.desk.forget(raw))
            if self.path == "/api/delete-film":
                return self.send_json(200, self.server.desk.delete_film(raw))
            if self.path == "/api/assembly":
                return self.send_json(200, self.server.desk.save_assembly(raw))
            if self.path == "/api/assemble":
                return self.send_json(202, self.server.desk.assemble(raw))
            if self.path == "/api/action":
                job = self.server.desk.start(raw)
                if raw.get("wait") is True:  # a quick look: the finished state in one round trip, no poll
                    self.server.desk.worker.join(5)
                    return self.send_json(200, self.server.desk.snapshot())
                return self.send_json(202, job)
            if self.path == "/api/restart":
                state = self.server.desk.snapshot()
                if state["job"] and state["job"]["running"]:
                    raise ValueError("Wait for the operation in progress to finish, then restart")
                if state["overlays"] and state["overlays"]["running"]:
                    raise ValueError("Wait for the data overlays to be drawn, then restart")
                if state["assembling"] and state["assembling"]["running"]:
                    raise ValueError("Wait for the assembly to be made, then restart")
                self.send_json(202, {"restarting": True})
                threading.Thread(target=restart, args=(self.server,), daemon=True).start()
                return
            if self.path == "/api/folder":  # the videos folder, shown in Explorer: a page cannot open a folder itself
                folder = self.server.desk.videos
                folder.mkdir(parents=True, exist_ok=True)
                os.startfile(folder)
                return self.send_json(200, {"folder": str(folder)})
            self.send_json(404, {"message": "Not found"})
        except Conflict as error:
            self.send_json(409, {"message": str(error)})
        except (ValueError, TypeError, KeyError) as error:
            self.send_json(400, {"message": str(error)})
        except OSError as error:
            self.send_json(503, {"message": str(error)})

    def log_message(self, *_args):
        pass


def working_folder(root):
    """Where the viewer writes stills, preview clips and renders. Film Viewer takes no spaces in a video path,
    and the temp folder has one when the Windows account name does: then the first place without."""
    places = [Path(tempfile.gettempdir()) / "AHQuickShots", Path(root) / "media",
              Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "AHQuickShots"]
    return next((place for place in places if " " not in str(place)), places[0])


def videos_folder(root):
    """Where finished videos are kept: the folder named in output.txt, else "AH Quick Shots" in the account's
    Videos folder. The desk copies a finished render there, so this path may have spaces."""
    setting = Path(root) / "output.txt"
    if setting.exists() and setting.read_text(encoding="utf-8").strip():
        return Path(setting.read_text(encoding="utf-8").strip())
    videos = Path.home() / "Videos"
    return (videos if videos.is_dir() else Path.home()) / "AH Quick Shots"


class Server(ThreadingHTTPServer):
    # Windows lets servers that allow reuse share a port: a second start would split the page and the viewer
    # between two desks. Without it the second start fails and the page opens the desk that is running.
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        self.desk._log()  # a request that failed in a way nobody foresaw: the desk has no window to print it in


def make_server(port=PORT, root=ROOT / ".quickshots", media_root=None, videos=None):
    server = Server((HOST, port), Handler)
    server.daemon_threads = True
    server.code = code_stamp()
    server.desk = Desk(root, media_root=media_root or working_folder(root), videos=videos or videos_folder(root))
    return server


def running_desk(port=PORT):
    """The state of the desk answering on the port, or None when nothing, or something else, answers there."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/state", timeout=5) as reply:
            state = json.load(reply)
    except (OSError, ValueError):
        return None
    return state if isinstance(state, dict) and "job" in state else None


def listener(port=PORT):
    """The process that listens on the port, from netstat (its state column is in the system's language)."""
    rows = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.decode("ascii", "replace")
    for cells in (row.split() for row in rows.splitlines()):
        if len(cells) == 5 and cells[1] == f"{HOST}:{port}" and cells[2] == "0.0.0.0:0" and cells[4].isdigit():
            return int(cells[4])
    return None


def take_port(port=PORT):
    """A server for this desk, or None when the desk on the port is this copy's or busy: then its page is the one.
    The desk of another copy is stopped first. The server has no window, so a desk started earlier from another
    folder, often an older version since deleted, kept the port: the new one could not start and the page came from
    the old one, blank or "404 Not Found"."""
    try:
        return make_server(port)
    except OSError:
        state = running_desk(port)
        if state is None:
            raise
    if state.get("folder") == str(ROOT) or (state["job"] or {}).get("running"):
        return None
    pid = listener(port)
    if pid is None:
        raise OSError(f"cannot tell which program holds port {port}")
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        time.sleep(.1)
        try:
            return make_server(port)
        except OSError:
            pass
    raise OSError(f"the desk of another copy did not let go of port {port}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--page", action="store_true", help="open the page once the desk listens")
    args = parser.parse_args()
    if args.check:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_director.py")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        return
    try:
        server = take_port()
    except OSError as error:  # the server has no window: say it in one
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, f"AH Quick Shots could not start: {error}.\n\nAnother program may use port "
                                         f"{PORT}. Close it, or restart Windows, and start AH Quick Shots again.", "AH Quick Shots", 0x10)
        raise SystemExit(1)
    if args.page:
        webbrowser.open(f"http://{HOST}:{PORT}/")
    if server is None:
        return
    print(f"AH Quick Shots: http://{HOST}:{PORT}", flush=True)
    with server:
        server.serve_forever()


if __name__ == "__main__":
    main()
