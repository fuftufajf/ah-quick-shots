"""Builds the Windows package: a zip with a folder that runs on a PC with neither Python nor ffmpeg installed.

Inside are the app's own files as git has them (no saved shots, no tests, no page sources), the official embeddable
Python from python.org and ffmpeg from gyan.dev's essentials build of an FFmpeg release. Both are downloaded once into
build/downloads and must match the SHA-256 written here, so every build packs exactly the same two files.
GitHub builds it when a release is published (.github/workflows/package.yml); by hand: python dev/build_package.py.
The zip and its .sha256 land in build/."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
NAME = "AH-Quick-Shots-Windows.zip"
FOLDER = "ah-quick-shots"  # the folder the zip unpacks to: no spaces, the viewer takes none in a video path
PYTHON = ("https://www.python.org/ftp/python/3.15.2/python-3.15.2-embed-amd64.zip",
          "d012f3aa29cf07436dae8aea1fec0fd195c2612a0b059b1f915ae0afb9ad1f62")
FFMPEG = ("https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
          "60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba")


def wanted(path):
    """What a player needs: the app, its ready page, its font, the documents. Not dev/, the tests or ui/src."""
    if "/" not in path:
        return path.endswith(".py") and path != "test_director.py" or path in (
            "AH Quick Shots.bat", "LICENSE", "README.md", "THIRD-PARTY-NOTICES.md")
    return path.startswith(("ui/dist/", "fonts/", "docs/"))


def download(url, sha256):
    target = BUILD / "downloads" / url.rsplit("/", 1)[1]
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        print("downloading", url, flush=True)
        with urllib.request.urlopen(url) as answer, open(target.with_suffix(".part"), "wb") as out:
            shutil.copyfileobj(answer, out)
        target.with_suffix(".part").replace(target)
    found = hashlib.sha256(target.read_bytes()).hexdigest()
    if found != sha256:
        raise SystemExit(f"{target.name}: SHA-256 {found}, expected {sha256}")
    return target


def main():
    stage = BUILD / FOLDER
    shutil.rmtree(stage, ignore_errors=True)
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.splitlines()
    for path in filter(wanted, files):
        (stage / path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / path, stage / path)

    with zipfile.ZipFile(download(*PYTHON)) as embed:
        embed.extractall(stage / "python")
    # The embeddable Python reads its search path from this file only: add the app's folder, one level up.
    pth = next((stage / "python").glob("python3*._pth"))
    pth.write_text(pth.read_text(encoding="utf-8").rstrip("\n") + "\n..\n", encoding="utf-8")

    with zipfile.ZipFile(download(*FFMPEG)) as build:
        (stage / "ffmpeg").mkdir()
        for name in build.namelist():  # ffmpeg itself and its licence and build notes; the app needs no ffprobe or ffplay
            if name.endswith(("/bin/ffmpeg.exe", "/LICENSE", "/README.txt")):
                (stage / "ffmpeg" / name.rsplit("/", 1)[1]).write_bytes(build.read(name))

    # Started from elsewhere, as the desk's own restart does: the app must be found through the ._pth alone.
    subprocess.run([str(stage / "python" / "python.exe"), "-c", "import director_api, overlay"], cwd=BUILD, check=True)
    filters = subprocess.run([str(stage / "ffmpeg" / "ffmpeg.exe"), "-hide_banner", "-filters"], capture_output=True, text=True, check=True)
    assert " subtitles " in filters.stdout, "this ffmpeg cannot draw the data overlay (no libass)"

    package = BUILD / NAME
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in sorted(p for p in stage.rglob("*") if p.is_file()):
            out.write(path, path.relative_to(BUILD).as_posix())
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (BUILD / (NAME + ".sha256")).write_text(f"{digest} *{NAME}\n", encoding="ascii")
    print(f"{package}  {package.stat().st_size / 2**20:.1f} MB\nSHA-256 {digest}")


if __name__ == "__main__":
    main()
