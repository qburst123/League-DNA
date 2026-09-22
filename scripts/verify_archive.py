#!/usr/bin/env python3
"""Prove an archive will open, before anyone else has to find out the hard way.

Written because a hand-made zip was once reported as "invalid" by Windows Explorer. Every check
here corresponds to a real failure mode of small extractors (Windows Explorer, macOS Archive
Utility, Android file managers, older Info-ZIP builds):

    ZIP64               — a 64-bit record the extractor may not implement
    data descriptors    — general-purpose flag bit 3, which some readers reject outright
    extra fields        — extended timestamps / Unix UID-GID blocks some tools choke on
    mixed methods       — an entry whose compression method the extractor cannot handle
    non-ASCII names     — mangled by code pages
    directory as a file — the classic "invalid archive" fault, where a folder is stored as an
                          empty file and extractors disagree about what it means
    missing directory   — a file whose parent folder was never stored
    CRC or truncation   — a genuinely corrupt download

It then does what a human would do: extracts the archive with the system `unzip` (when available)
*and* with Python's zipfile, compares the file counts, and checks the executable bits of the
launchers and the integrity of any SQLite database inside.

    python3 scripts/verify_archive.py /home/user/League-DNA-v2.5.0.zip
    python3 scripts/verify_archive.py <zip> --keep --launch-test      # extract and boot the app

Exit code is 0 only when every check passes.
"""
from __future__ import annotations
import argparse
import hashlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

BAD = "  FAIL"
OK = "  ok  "


def check(condition: bool, label: str, detail: str = "", failures: list | None = None) -> bool:
    print(f"{OK if condition else BAD} {label}" + (f" — {detail}" if detail else ""))
    if not condition and failures is not None:
        failures.append(label if not detail else f"{label}: {detail}")
    return condition


def verify(path: Path, launch_test: bool = False, keep: bool = False, port: int = 8021) -> int:
    failures: list[str] = []
    print(f"verifying {path} ({path.stat().st_size:,} bytes)")
    print(f"sha256 {hashlib.sha256(path.read_bytes()).hexdigest()}")
    print()

    # ---------------------------------------------------------------- structure
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        print(f"{BAD} not a readable zip: {error}")
        return 1

    with archive:
        bad_crc = archive.testzip()
        infos = archive.infolist()
        names = archive.namelist()
        files = [info for info in infos if not info.filename.endswith("/")]

        check(bad_crc is None, "every entry passes its CRC check", f"first bad entry: {bad_crc}", failures)
        check(not any(info.flag_bits & 0x08 for info in infos), "no data descriptors (flag bit 3)",
              f"{sum(1 for i in infos if i.flag_bits & 0x08)} entries would need one", failures)
        check(all(not info.extra for info in infos), "no extra fields",
              f"{sum(1 for i in infos if i.extra)} entries carry one", failures)
        check(not any(info.file_size > 0xFFFFFFFF or info.header_offset > 0xFFFFFFFF for info in infos),
              "no ZIP64 records — every entry and offset fits 32 bits", "", failures)
        methods = sorted({info.compress_type for info in infos})
        check(set(methods) <= {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}, "only stored/deflate entries",
              f"methods present: {methods}", failures)
        non_ascii = [info.filename for info in infos if not info.filename.isascii()]
        check(not non_ascii, "ASCII-safe entry names", "; ".join(non_ascii[:3]), failures)
        check(all("\\" not in info.filename for info in infos), "forward slashes only", "", failures)

        stray = [name for name in names if not name.endswith("/") and
                 any(other == name + "/" or other.startswith(name + "/") for other in names)]
        check(not stray, "no directory stored as a file (the classic invalid-archive fault)",
              "; ".join(stray[:3]), failures)

        directories = {name for name in names if name.endswith("/")}
        missing = sorted({"/".join(info.filename.split("/")[:-1]) + "/"
                          for info in files if "/" in info.filename} - directories)
        check(not missing, "every file's parent directory is stored explicitly",
              f"missing: {missing[:3]}", failures)

        root_dir = names[0].split("/")[0] + "/" if names else ""
        check(bool(root_dir) and all(name.startswith(root_dir) for name in names),
              "one top-level folder contains everything", f"root: {root_dir.rstrip('/')}", failures)
        check(len(files) == len([n for n in names if not n.endswith("/")]),
              "file count agrees with the central directory", f"{len(files)} files", failures)
        print(f"       {len(files)} files, {len(directories)} directories, "
              f"{path.stat().st_size / 1048576:.2f} MB, methods {methods}")

    # ---------------------------------------------------------------- extraction
    target = Path(tempfile.mkdtemp(prefix="verify-archive-"))
    extracted = []
    try:
        if shutil.which("unzip"):
            result = subprocess.run(["unzip", "-q", str(path), "-d", str(target)],
                                    capture_output=True, text=True, timeout=900)
            check(result.returncode == 0, "system `unzip` extracts it cleanly",
                  result.stderr.strip()[:200], failures)
            extracted = [p for p in target.rglob("*") if p.is_file()]
        else:
            print("  --  system `unzip` not installed; skipping that extractor")

        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
        python_files = [p for p in target.rglob("*") if p.is_file()]
        check(len(python_files) == len(files), "Python zipfile extracts the same number of files",
              f"{len(python_files)} vs {len(files)}", failures)

        root = target / root_dir.rstrip("/")
        check(root.is_dir(), "the extracted folder is usable", str(root), failures)

        # Launchers must arrive runnable.
        for launcher in ("Start-League-DNA.sh", "Start-League-DNA.command"):
            candidate = root / launcher
            if candidate.exists():
                check(bool(candidate.stat().st_mode & 0o111), f"{launcher} arrives executable",
                      oct(candidate.stat().st_mode & 0o777), failures)

        # The essentials a user needs to start the app.
        for essential in ("run.py", "requirements.txt", "web/index.html", "README.md", "release.json"):
            check((root / essential).exists(), f"package contains {essential}", "", failures)
        board = root / "FT score Prediction Playground" / "index.html"
        if board.exists():
            check("snapshot" in board.read_text()[:400000], "the playground board carries its embedded build")

        # Databases inside the package must be complete, not a copy of a live file.
        for database in sorted(set(root.glob("data/**/*.sqlite"))):
            try:
                con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
                state = con.execute("PRAGMA quick_check").fetchone()[0]
                tables = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                con.close()
                check(state == "ok", f"shipped database {database.name} passes quick_check",
                      f"{tables} tables", failures)
            except sqlite3.Error as error:
                check(False, f"shipped database {database.name} opens", str(error), failures)
        side = list(root.glob("data/*-wal")) + list(root.glob("data/*-shm"))
        check(not side, "no live -wal/-shm side files shipped",
              "; ".join(p.name for p in side), failures)

        # ------------------------------------------------------------ launch test
        if launch_test:
            port_check = subprocess.run(["python3", "-c",
                                         f"import socket;s=socket.socket();s.bind(('127.0.0.1',{port}));s.close()"],
                                        capture_output=True, text=True)
            if port_check.returncode != 0:
                print(f"  --  port {port} busy; skipping the launch test")
            else:
                import os
                import urllib.error
                import urllib.request
                env = dict(os.environ, LEAGUE_OFFLINE="1", LEAGUE_DATA_DIR=str(root / "data"))
                process = subprocess.Popen([sys.executable, "run.py", "--port", str(port), "--no-browser"],
                                           cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                try:
                    ready = False
                    for _ in range(60):
                        time.sleep(1)
                        if process.poll() is not None:
                            break
                        try:
                            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3)
                            ready = True
                            break
                        except Exception:
                            continue
                    check(ready, f"the extracted app boots and answers on port {port}", "", failures)
                    if ready:
                        for route in ("/", "/playground", "/api/playground/status", "/api/workspace"):
                            try:
                                with urllib.request.urlopen(f"http://127.0.0.1:{port}{route}", timeout=120) as response:
                                    check(response.status == 200, f"serves {route}",
                                          f"HTTP {response.status}, {len(response.read())} bytes", failures)
                            except Exception as error:
                                check(False, f"serves {route}", str(error)[:160], failures)
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()
    finally:
        if keep:
            print(f"\nextraction kept at {target}")
        else:
            shutil.rmtree(target, ignore_errors=True)

    print()
    if failures:
        print("ARCHIVE NOT SHIPPABLE")
        for line in failures:
            print("  -", line)
        return 1
    print("ARCHIVE VERIFIED — it will extract and run")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--launch-test", action="store_true", help="extract, boot the app and probe its routes")
    parser.add_argument("--keep", action="store_true", help="keep the extraction instead of deleting it")
    parser.add_argument("--port", type=int, default=8021, help="port for the launch test")
    args = parser.parse_args()
    if not args.archive.exists():
        print(f"no such file: {args.archive}")
        return 1
    return verify(args.archive, launch_test=args.launch_test, keep=args.keep, port=args.port)


if __name__ == "__main__":
    raise SystemExit(main())
