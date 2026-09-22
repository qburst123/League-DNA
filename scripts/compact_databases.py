#!/usr/bin/env python3
"""Compact the League-DNA databases and report exactly what the workspace carries.

Two problems this solves, both observed in practice:

* a long collector run grows `league.sqlite-wal` until something checkpoints it — the write-ahead
  log is a real file, and it counts against any backup, zip or workspace quota;
* deleting rows (trail rebuilds, sequence re-indexing) leaves free pages inside the database file
  that only VACUUM returns to the filesystem.

Safe by default: it refuses to touch a database another process is writing to, verifies each file
with `PRAGMA quick_check` **before and after**, and never rebuilds or rewrites data — a checkpoint
and a vacuum only move bytes that are already committed.

    python3 scripts/compact_databases.py            # checkpoint + vacuum + verify, print the report
    python3 scripts/compact_databases.py --dry-run  # report the sizes and free pages only
    python3 scripts/compact_databases.py --checkpoint-only

Run it with the app stopped for the full reclaim; `--checkpoint-only` is safe while the app runs.
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DATABASES = ("league.sqlite", "ft-sequences.sqlite")
SIDE_SUFFIXES = ("-wal", "-shm")


def side_size(database: Path) -> int:
    return sum((database.parent / (database.name + suffix)).stat().st_size
               for suffix in SIDE_SUFFIXES
               if (database.parent / (database.name + suffix)).exists())


def report(database: Path) -> dict:
    size = database.stat().st_size
    side = side_size(database)
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        check = con.execute("PRAGMA quick_check").fetchone()[0]
        page_size = con.execute("PRAGMA page_size").fetchone()[0]
        free_pages = con.execute("PRAGMA freelist_count").fetchone()[0]
        page_count = con.execute("PRAGMA page_count").fetchone()[0]
    finally:
        con.close()
    return {"database": database.name, "bytes": size, "side_bytes": side, "total_bytes": size + side,
            "quick_check": check, "free_bytes": free_pages * page_size,
            "compactable_percent": round(100.0 * free_pages / max(1, page_count), 1)}


def compact(database: Path, checkpoint_only: bool) -> dict:
    before = report(database)
    if before["quick_check"] != "ok":
        raise SystemExit(f"{database.name}: quick_check says {before['quick_check']!r} — "
                         f"repair it with scripts/salvage_sqlite.py before compacting")
    try:
        con = sqlite3.connect(str(database), timeout=3.0)          # a live writer keeps it locked
    except sqlite3.OperationalError as error:
        raise SystemExit(f"{database.name}: cannot open for compaction ({error}); stop the app first")
    try:
        con.execute("PRAGMA busy_timeout=3000")
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError as error:
            raise SystemExit(f"{database.name}: checkpoint refused ({error}) — is the app running? "
                             f"Use --checkpoint-only while it is.")
        if not checkpoint_only:
            try:
                con.execute("VACUUM")
            except sqlite3.OperationalError as error:
                raise SystemExit(f"{database.name}: VACUUM refused ({error}) — is the app running?")
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        check = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    after = report(database)
    after["quick_check_after"] = check
    after["reclaimed_bytes"] = max(0, before["total_bytes"] - after["total_bytes"])
    return {"before": before, "after": after}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="report sizes and free pages only")
    parser.add_argument("--checkpoint-only", action="store_true", help="truncate the WAL, no VACUUM")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args()

    results, total = [], 0
    for name in DATABASES:
        database = args.data / name
        if not database.exists():
            print(f"{name}: not present, skipped")
            continue
        if args.dry_run:
            info = report(database)
            print(f"{name}: {info['bytes'] / 1048576:.1f} MB + {info['side_bytes'] / 1048576:.1f} MB WAL"
                  f" · free pages {info['free_bytes'] / 1048576:.1f} MB ({info['compactable_percent']}%)"
                  f" · quick_check {info['quick_check']}")
            continue
        outcome = compact(database, args.checkpoint_only)
        total += outcome["after"]["reclaimed_bytes"]
        results.append(outcome)
        print(f"{name}: {outcome['before']['total_bytes'] / 1048576:.1f} MB → "
              f"{outcome['after']['total_bytes'] / 1048576:.1f} MB "
              f"(reclaimed {outcome['after']['reclaimed_bytes'] / 1048576:.1f} MB, "
              f"quick_check {outcome['after']['quick_check_after']})")
    if results:
        print(f"total reclaimed: {total / 1048576:.1f} MB")
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
