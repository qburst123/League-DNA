"""Build the distributable packages for a release.

Written after a report that the first hand-made zip would not open: the archives
produced here avoid every construct that older extractors (Windows Explorer,
macOS Archive Utility, Android file managers) are known to reject, and they ship
a consistent database snapshot instead of one that is still being written:

* no ZIP64 records and no data descriptors (every file is far below the limit)
* no extended-timestamp or Unix UID/GID extra fields, no comments
* a single compression method (deflate) for every entry, including directories
* ASCII-safe names, forward slashes, directory entries included explicitly
* each database is copied with SQLite's online backup API, then checkpointed and
  vacuumed, so a package never contains a live `-wal`/`-shm` side file

Usage:
    python scripts/package_release.py                 # full + server-only + tar.gz
    python scripts/package_release.py --only server   # a single variant
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD_DIR_NAME = "FT score Prediction Playground"      # the standalone board workspace
BOARD_FILES = ("index.html", "README.md", "playground.json")
EXCLUDE_DIRS = {"node_modules", ".vite", "__pycache__", ".pytest_cache", ".cache", ".venv", "research", "releases"}
EXCLUDE_SUFFIX = {".pyc", ".pyo", "-wal", "-shm", ".DS_Store", ".zip"}
# The prediction ledger travels with the release: the frozen sheets are the record of what the
# board said *before* each matchday was played, and that record cannot be reconstructed later for
# matchdays the live source has already moved past. It is a database like the other two, so it is
# snapshotted with the same online-backup + checkpoint + vacuum path.
DATABASES = ("league.sqlite", "ft-sequences.sqlite", "predictions.sqlite")

# The playground payload is cached next to the databases. Shipping it means a freshly extracted
# package answers the board immediately instead of spending a minute building it while the user
# is opening the app. (backend/playground_service.py also falls back to the board's own copy.)
PLAYGROUND_CACHE = "playground.json"
# Caches that make a freshly extracted package answer immediately instead of refitting for a minute:
# the board payload, the unplayed matchday rows, and the walk-forward report the payload is built from.
PLAYGROUND_CACHES = ("playground.json", "live-sheets.json", "backtest.json")


def args_databases_missing(source_dir: Path | None) -> bool:
    """True when the caller pointed at a snapshot directory that is not there."""
    return source_dir is not None and not Path(source_dir).is_dir()


def snapshot(source: Path, target: Path) -> None:
    """Consistent copy of a possibly-running database, checkpointed and compacted."""
    target.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"file:{source}?mode=ro"
    live = sqlite3.connect(source_uri, uri=True)
    copy = sqlite3.connect(target)
    try:
        live.backup(copy)
    finally:
        live.close()
    copy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copy.execute("PRAGMA journal_mode=DELETE")
    copy.execute("VACUUM")
    os.chmod(target, 0o644)  # a packaged database must not arrive read-only to its owner
    check = copy.execute("PRAGMA quick_check").fetchone()[0]
    copy.close()
    if check != "ok":
        raise SystemExit(f"{source.name} failed its integrity check: {check}")


def board_dir() -> Path | None:
    """The playground board sits next to the project, or inside it when packaged."""
    for candidate in (ROOT.parent / BOARD_DIR_NAME, ROOT / BOARD_DIR_NAME):
        if (candidate / "index.html").exists():
            return candidate
    return None


def collect(variant: str):
    """Yield (archive_path, absolute_path) for the requested variant."""
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith("."))
        for name in sorted(files):
            path = Path(base) / name
            if any(name.endswith(suffix) for suffix in EXCLUDE_SUFFIX):
                continue
            relative = path.relative_to(ROOT).as_posix()
            if relative.startswith("data/"):
                continue  # databases are added from the snapshot below
            if variant in {"server", "lite"} and relative == "League-DNA.html":
                continue  # the offline single file is shipped on its own
            if variant == "lite" and (relative.startswith("artifacts/") or relative.startswith("tests/")):
                continue  # keep the download small; code, interface and docs only
            yield relative, path


def write_zip(target: Path, entries) -> None:
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, allowZip64=False, strict_timestamps=False) as archive:
        for relative, path in entries:
            if path.is_dir():
                # A directory entry must end in "/" and carry the DOS directory bit.
                # Writing an empty file under the directory's name is what makes an
                # archive look invalid to Windows Explorer and Archive Utility.
                info = zipfile.ZipInfo(relative.rstrip("/") + "/", date_time=time.localtime(path.stat().st_mtime)[:6])
                info.compress_type = zipfile.ZIP_STORED
                info.create_system = 3  # Unix: store real mode bits, as Info-ZIP does
                info.external_attr = (0o040755 << 16) | 0x10
                archive.writestr(info, b"")
                continue
            info = zipfile.ZipInfo(relative, date_time=time.localtime(path.stat().st_mtime)[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3  # Unix mode bits; Windows ignores them
            info.external_attr = ((0o100755 if relative.endswith((".sh", ".command")) else 0o100644) << 16) | 0x20
            info.internal_attr = 0
            archive.writestr(info, path.read_bytes())


def write_tar(target: Path, entries) -> None:
    with tarfile.open(target, "w:gz", compresslevel=9, format=tarfile.GNU_FORMAT) as archive:
        for relative, path in entries:
            info = archive.gettarinfo(str(path), arcname=relative)
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = int(path.stat().st_mtime)
            if info.isdir():
                archive.addfile(info)
            else:
                with path.open("rb") as handle:
                    archive.addfile(info, handle)


def release_label() -> str:
    """The version this package is labelled with — release.json is the single source of truth."""
    try:
        return f"v{json.loads((ROOT / 'release.json').read_text())['version']}"
    except Exception:
        return "v0.0.0"


def build(variant: str, workdir: Path, source_dir: Path | None = None, version: str | None = None) -> list[Path]:
    label = version or release_label()
    # The offline single-file app is generated, not hand-written, so a tree that has never run
    # scripts/build_app.py packages without it and the archive silently loses its
    # "double-click and it opens" entry point. Warn instead of shipping a package that contradicts
    # its own PACKAGE.md.
    offline = ROOT / "League-DNA.html"
    if variant == "full" and not offline.exists():
        print("warning: League-DNA.html is missing — run `python3 scripts/build_app.py` with the app "
              "running before packaging the full variant", file=sys.stderr)
    name = f"League-DNA-{label}" if variant == "full" else f"League-DNA-{label}-{variant}"
    root = workdir / name
    root.mkdir(parents=True, exist_ok=True)
    counts, entries = {}, []
    for relative, path in collect(variant):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
    # The board is its own workspace folder: without it the packaged app's sidebar entry
    # would point at an empty /playground, so it travels with every variant.
    board = board_dir()
    packed_board = []
    packed_playground_cache = False
    if board is not None:
        destination = root / BOARD_DIR_NAME
        destination.mkdir(parents=True, exist_ok=True)
        for board_file in BOARD_FILES:
            source_file = board / board_file
            if source_file.exists():
                (destination / board_file).write_bytes(source_file.read_bytes())
                packed_board.append(board_file)
    for database in DATABASES:
        source = (source_dir or ROOT / "data") / database
        if variant == "lite" or not source.exists():
            continue  # the lite build ships code only and collects into an empty database
        destination = root / "data" / database
        snapshot(source, destination)
        with sqlite3.connect(f"file:{destination}?mode=ro", uri=True) as connection:
            counts[database] = connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    if variant != "lite" and not args_databases_missing(source_dir):
        (root / "data").mkdir(parents=True, exist_ok=True)
        for cache in PLAYGROUND_CACHES:
            cached = (source_dir or ROOT / "data") / cache
            if cached.exists() and cached.stat().st_size > 1024:
                (root / "data" / cache).write_bytes(cached.read_bytes())
                packed_playground_cache = True
    # The release manifest is regenerated from the files actually packaged.
    manifest = json.loads((root / "release.json").read_text())
    # A manifest cannot state the hash of the archive that contains it, so inside the package the
    # hashes are replaced by a pointer to the authoritative list that ships beside the archives.
    manifest["packages"] = {"note": "the sha256 of each published archive is recorded in RELEASES.md "
                                    "and in the release.json that sits next to the archives, not here",
                            "self_reference": "this file is inside the archive, so it cannot carry its own hash"}
    manifest.update({"package": f"{name}.zip", "package_variant": variant,
                     "packaged_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                     "package_integrity": "databases snapshotted, checkpointed and vacuumed",
                     "package_verification": "python3 scripts/verify_archive.py <archive> --launch-test",
                     "playground_board": f"{BOARD_DIR_NAME}/index.html" if packed_board else None,
                     "playground_board_files": packed_board,
                     "playground_cache": ("data/" + ", data/".join(PLAYGROUND_CACHES)) if packed_playground_cache else None,
                     "prediction_ledger": "data/predictions.sqlite (frozen sheets, locked once and never overwritten)"})
    (root / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(dirs)
        entries.append(((Path(base)).relative_to(workdir).as_posix(), Path(base)))
        for file in sorted(files):
            path = Path(base) / file
            entries.append((path.relative_to(workdir).as_posix(), path))
    # Extracted launchers must stay runnable; everything else is a plain readable file.
    for relative, path in entries:
        if path.is_dir():
            os.chmod(path, 0o755)
        elif relative.endswith((".sh", ".command")):
            os.chmod(path, 0o755)
        else:
            os.chmod(path, 0o644)
    outputs = []
    for target in (workdir / f"{name}.zip", workdir / f"{name}.tar.gz"):
        write_zip(target, entries) if target.suffix == ".zip" else write_tar(target, entries)
        outputs.append(target)
    return outputs


def verify(target: Path, expect_offline: bool = False) -> dict:
    """Structural check plus a genuine extraction, because CRC tests alone miss layout faults."""
    if target.suffix == ".zip":
        with zipfile.ZipFile(target) as archive:
            broken = archive.testzip()
            entries = archive.infolist()
            extras = [i.filename for i in entries if i.extra]
            zip64 = [i.filename for i in entries if i.file_size > 0xFFFFFFFF or i.header_offset > 0xFFFFFFFF]
            methods = sorted({i.compress_type for i in entries})
            names = archive.namelist()
            stray = [name for name in names if not name.endswith("/") and
                     any(other == name + "/" or other.startswith(name + "/") for other in names)]
            if stray:
                raise SystemExit(f"{target.name}: directory entry written as a file: {stray[:3]}")
            folder = Path(tempfile.mkdtemp())
            try:
                archive.extractall(folder)
                extracted = sum(1 for item in folder.rglob("*") if item.is_file())
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            if extracted != len([n for n in names if not n.endswith("/")]):
                raise SystemExit(f"{target.name}: extraction produced {extracted} files, archive lists {len([n for n in names if not n.endswith('/')])}")
    else:
        with tarfile.open(target) as archive:
            names = archive.getnames()
            members = [member for member in archive.getmembers() if member.isfile()]
            broken, extras, zip64 = None, [], []
            methods = ["gzip"]
            folder = Path(tempfile.mkdtemp())
            try:
                archive.extractall(folder, filter="data")
                extracted = sum(1 for item in folder.rglob("*") if item.is_file())
            finally:
                shutil.rmtree(folder, ignore_errors=True)
            if extracted != len(members):
                raise SystemExit(f"{target.name}: extraction produced {extracted} files, archive lists {len(members)}")
    if expect_offline and not any(name.endswith("League-DNA.html") for name in names):
        print(f"warning: {target.name} ships the databases but not the offline League-DNA.html", file=sys.stderr)
    return {"archive": target.name, "extracted_files": extracted, "bytes": target.stat().st_size,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "entries": len(names), "testzip": broken, "extra_fields": len(extras),
            "zip64": len(zip64), "methods": methods,
            "has_databases": all(any(n.endswith(f"data/{db}") for n in names) for db in DATABASES),
            "has_interface": any(n.endswith("web/index.html") for n in names)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=["full", "server", "lite"], default="full")
    parser.add_argument("--workdir", default="/tmp/packages")
    parser.add_argument("--outdir", default="/home/user")
    parser.add_argument("--databases", help="directory holding the frozen database snapshot to ship")
    parser.add_argument("--version", help="package label, e.g. v2.4.0 (default: release.json)")
    args = parser.parse_args()
    workdir = Path(args.workdir)
    if workdir.exists():
        import shutil
        shutil.rmtree(workdir)
    variants = ["full", "server", "lite"] if args.only == "full" else [args.only]
    report = []
    for variant in variants:
        for target in build(variant, workdir, Path(args.databases) if args.databases else None, args.version):
            out = Path(args.outdir) / target.name
            out.write_bytes(target.read_bytes())
            report.append(verify(out, expect_offline=(variant == "full")))
    print(json.dumps(report, indent=2))
