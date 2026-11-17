"""The documented Film Viewer text socket. Only the desk worker uses it."""
import re
import socket


class Viewer:
    # Seconds the viewer may take to answer one command. A render answers only when it is done, so the desk gives a
    # long one more (Desk._render): cut off at ten minutes, a long cut was lost with its file half made.
    patience = 600

    def __init__(self):
        self.sock = self.reader = None

    def close(self):
        if self.reader:
            self.reader.close()
        if self.sock:
            self.sock.close()
        self.sock = self.reader = None

    def _reply(self, progress):
        lines, size = [], 0
        while True:
            raw = self.reader.readline(65537)
            if not raw:
                raise ConnectionError("Film Viewer disconnected before confirming the command")
            size += len(raw)
            if len(raw) > 65536 or size > 32 * 1024 * 1024:
                raise ConnectionError("Viewer reply is too large; use a shorter film range")
            line = raw.rstrip("\r\n")
            if line == ".end":
                break
            if line.startswith("waiting for the user:"):
                progress("Look at Film Viewer: " + line.partition(":")[2].strip())
            lines.append(line)
        errors = [line[7:] for line in lines if line.startswith("error:")]
        if errors:
            raise ValueError("; ".join(errors))
        return lines

    def connect(self, progress):
        self.close()
        for port in range(48221, 48230):
            try:
                self.sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                self.sock.settimeout(600)
                self.reader = self.sock.makefile("r", encoding="utf-8", newline="\n")
                greeting = self._reply(progress)
                if not greeting or not greeting[0].startswith("ahfilmclaude "):
                    raise ValueError("Unexpected Film Viewer greeting")
                return greeting[0]
            except ConnectionRefusedError:
                self.close()
            except Exception:
                self.close()
                raise
        raise ConnectionError("Start Film Viewer 3.07 Patch 3 or newer, then Connect")

    def command(self, command, progress=lambda message: None):
        return self.commands([command], progress)[0]

    def commands(self, commands, progress=lambda message: None):
        """Each command's reply. They are sent 60 at a time (the viewer lets 64 lines wait): it takes one command a
        frame when they come one by one, 3.0 s for a camera plan of 224 lines against 0.05 s sent together (measured
        17 Nov 2026). Every reply of those sent is read before one's error is raised, so none is left waiting."""
        if any("\n" in command or "\r" in command or len(command.encode("utf-8")) > 8191 for command in commands):
            raise ValueError("Invalid viewer command")
        if not self.sock:
            self.connect(progress)
        replies, refused = [], None
        try:
            self.sock.settimeout(self.patience)
            for at in range(0, len(commands), 60):
                self.sock.sendall("".join(command + "\n" for command in commands[at:at + 60]).encode("utf-8"))
                for _ in commands[at:at + 60]:
                    try:
                        replies.append(self._reply(progress))
                    except ValueError as error:  # the viewer's "error:", told once its reply was read whole
                        if isinstance(error, UnicodeError):  # not a reply at all: the connection is given up below
                            raise
                        refused = refused or error
                if refused:
                    break
        except (OSError, ConnectionError, UnicodeError):
            self.close()
            raise ConnectionError("Viewer did not confirm completion. Check it before reconnecting; the action was not retried.")
        if refused:
            raise refused
        return replies


def number(value, low, high, name):
    import math
    if isinstance(value, bool):
        raise ValueError(f"Invalid {name}")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid {name}") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} must be between {low:g} and {high:g}")
    return value


def safe_text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError("Text contains unsupported characters")
    return value


def plane_rows(lines):
    import shlex
    planes = []
    for line in lines:
        if line.startswith("plane "):
            try:
                row = shlex.split(line)
                tail = row[9:]  # `planes long`: [recorder] model "<aircraft>" country <country>
                planes.append({"id": int(row[1]), "name": row[2], "position": list(map(float, row[3:6])),
                               "recorder": tail[:1] == ["recorder"],
                               "model": tail[tail.index("model") + 1] if "model" in tail[:-1] else None})
            except (ValueError, IndexError):
                pass  # a line that cannot be read is one aircraft less in the list, not a seek that fails
    return planes


def film_rows(lines, modified=None):
    """The viewer's library, newest first when the films folder can be read for dates."""
    rows = [{"path": m[1], "name": m[1].removeprefix("films/"), "mb": float(m[2]), "modified": 0}
            for line in lines if (m := re.fullmatch(r"film (.+) ([\d.]+) MB", line))]
    folder = next((line[7:] for line in lines if line.startswith("folder ")), None)
    if folder and modified:
        for row in rows:
            try:
                row["modified"] = modified(folder + "/" + row["name"])
            except OSError:
                pass
        rows.sort(key=lambda row: -row["modified"])
    return rows
