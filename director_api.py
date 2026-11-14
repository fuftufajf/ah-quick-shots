"""Local HTTP surface for the page. No viewer I/O on request threads. The API: docs/how-it-works.md."""
import argparse
import json
import mimetypes
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

from director_core import Conflict, Desk

ROOT = Path(__file__).resolve().parent
HOST, PORT = "127.0.0.1", 8766
# A desk keeps running with the code it started with. When a new version is unzipped over it, the page (read
# from disk) is new and the desk is not: the page's newer actions came back "Unknown action". The desk notices.
CODE = [ROOT / name for name in ("director_api.py", "director_core.py", "flight_data.py", "shot_plan.py", "viewer_socket.py")] + [ROOT / "ui" / "dist" / "index.html"]


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
            return self.send_json(200, {**self.server.desk.snapshot(), "stale": code_stamp() != self.server.code})
        if relative == "/api/flight":
            data = self.server.desk.flight_data()
            return self.send_json(200, data) if data else self.send_json(404, {"message": "Read a stretch's flight first"})
        if relative.startswith("/media/"):
            if not re.fullmatch(r"/media/(clips/)?[a-f0-9]{32}\.(bmp|mp4)", relative):
                return self.send_error(404)
            path = self.server.desk.media_root / relative[7:]
        else:
            base = ROOT / "ui" / "dist"
            path = (base / (relative.lstrip("/") or "index.html")).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                return self.send_error(404)
        if not path.is_file():
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
        self.send_header("Content-Type", mimetypes.guess_type(path)[0] or "application/octet-stream")
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
        except (BrokenPipeError, ConnectionResetError):
            pass  # Seeking in the browser replaces its previous media request.

    def do_POST(self):
        if not self.allowed():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 512 * 1024:
                raise ValueError("Request must be 1 to 524288 bytes")
            raw = json.loads(self.rfile.read(length))
            if not isinstance(raw, dict):
                raise ValueError("Request must be an object")
            if self.path == "/api/project":
                return self.send_json(200, self.server.desk.save(raw))
            if self.path == "/api/forget":
                return self.send_json(200, self.server.desk.forget(raw))
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


def make_server(port=PORT, root=ROOT / ".quickshots", media_root=None, videos=None):
    server = Server((HOST, port), Handler)
    server.daemon_threads = True
    server.code = code_stamp()
    server.desk = Desk(root, media_root=media_root or working_folder(root), videos=videos or videos_folder(root))
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        import unittest
        suite = unittest.defaultTestLoader.discover(str(ROOT), pattern="test_director.py")
        if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():
            raise SystemExit(1)
        return
    print(f"AH Quick Shots: http://{HOST}:{PORT}", flush=True)
    with make_server() as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
