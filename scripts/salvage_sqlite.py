"""Salvage a partially corrupt SQLite archive into a clean database.

`sqlite3 .recover` aborts when the corruption blocks its initial scan, so this
script works one table at a time: it copies every readable table, and for
damaged tables it bisects rowid ranges down to single rows, keeping every row
SQLite can still return. Nothing is invented; rows that cannot be read are
reported by count so the loss is visible.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.store import Store  # noqa: E402  (fresh, known-good schema)


def tables(connection):
    return [row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def columns(connection, table):
    return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]


def salvage_range(source, target, table, names, low, high, report, depth=0):
    """Copy [low, high) by rowid, halving the window whenever SQLite refuses."""
    placeholders = ",".join("?" for _ in names)
    quoted = ",".join(f'"{name}"' for name in names)
    try:
        rows = source.execute(
            f'SELECT rowid,{quoted} FROM "{table}" WHERE rowid>=? AND rowid<? ORDER BY rowid', (low, high)).fetchall()
    except sqlite3.DatabaseError:
        if high - low <= 1:
            report[table]["unreadable_rows"] += 1
            return
        middle = (low + high) // 2
        salvage_range(source, target, table, names, low, middle, report, depth + 1)
        salvage_range(source, target, table, names, middle, high, report, depth + 1)
        return
    if rows:
        target.executemany(f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})',
                           [tuple(row[1:]) for row in rows])
        report[table]["rows"] += len(rows)
    report[table]["windows"] += 1


def main(corrupt: Path, clean: Path):
    if clean.exists():
        clean.unlink()
    template = clean.with_suffix(".schema.sqlite")
    if template.exists():
        template.unlink()
    Store(template).close()  # writes the application schema
    # Reuse the schema the application itself wrote, so indexes and defaults match.
    template_connection = sqlite3.connect(template)
    schema_sql = [line for line in template_connection.iterdump() if not line.startswith("INSERT")]
    template_connection.close()
    target = sqlite3.connect(clean)
    target.executescript("\n".join(schema_sql))
    template.unlink()

    source = sqlite3.connect(f"file:{corrupt}?mode=ro", uri=True)
    report, recovered = {}, {}
    for table in tables(source):
        names = columns(source, table)
        if not names:
            continue
        report[table] = {"rows": 0, "unreadable_rows": 0, "windows": 0}
        try:
            total = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        except sqlite3.DatabaseError:
            total = None
        try:  # fast path: the whole table reads in one pass
            quoted = ",".join(f'"{name}"' for name in names)
            rows = source.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
        except sqlite3.DatabaseError:
            rows = None
        if rows is not None:
            if rows:
                placeholders = ",".join("?" for _ in names)
                quoted = ",".join(f'"{name}"' for name in names)
                target.executemany(f'INSERT OR REPLACE INTO "{table}" ({quoted}) VALUES ({placeholders})', rows)
            report[table]["rows"] = len(rows)
            report[table]["fast"] = True
            recovered[table] = (len(rows), total)
            continue
        bounds = source.execute(f'SELECT MIN(rowid),MAX(rowid) FROM "{table}"').fetchone()
        if bounds and bounds[0] is not None:
            salvage_range(source, target, table, names, bounds[0], bounds[1] + 1, report)
        recovered[table] = (report[table]["rows"], total)
    target.commit()
    for table in tables(source):
        try:  # keep the declared rows for context, not the recovered count
            target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
        except sqlite3.DatabaseError as error:
            print("target table unreadable", table, error)
    target.execute("VACUUM")
    check = target.execute("PRAGMA quick_check").fetchone()[0]
    target.close()
    source.close()
    print(json.dumps({"clean": str(clean), "quick_check": check, "tables": recovered}, indent=2))
    return check == "ok"


if __name__ == "__main__":
    source_file = Path(sys.argv[1])
    target_file = Path(sys.argv[2])
    raise SystemExit(0 if main(source_file, target_file) else 1)
