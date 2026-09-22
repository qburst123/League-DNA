"""The frozen prediction ledger — what the playground predicted, kept exactly as it was made.

The walk-forward backtest is a *measurement*: it re-runs every time the archive grows, so its
numbers for an already played matchday move as the window slides and the fitted weights change.
A prediction sheet is not a measurement — it is a commitment. This ledger is where a sheet is
written once, when the matchday is still to play, and never rewritten afterwards.

Rules this module enforces:

* a sheet for a (season, day) is written **once** (`lock_sheet` never overwrites a prediction);
* grading only ever fills in the recorded result and the verdict — the pick, its probability and
  the per-engine evidence stay byte-identical to what was stored;
* a sheet locked after its first kick-off is marked `late`, so "we knew this before kick-off" is a
  claim the ledger can prove instead of a claim the UI makes;
* every write is small (a few hundred bytes per pick), so a season of thirty matchdays costs a few
  hundred kilobytes and the whole ledger stays cheap to copy and to ship.

The ledger is its own SQLite file (`data/predictions.sqlite`). It is never merged into
`league.sqlite`: the archive is evidence collected from the source, the ledger is what this app
said at the time, and the two must be able to move independently.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS sheets(
    season      INTEGER NOT NULL,
    day         INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'live',
    locked_at   REAL,
    kickoff     REAL,
    lead_seconds REAL,
    late        INTEGER NOT NULL DEFAULT 0,
    revision    INTEGER,
    fixtures    INTEGER,
    weights     TEXT,
    hit_weights TEXT,
    detail      TEXT,
    PRIMARY KEY(season, day)
);
CREATE TABLE IF NOT EXISTS picks(
    season      INTEGER NOT NULL,
    day         INTEGER NOT NULL,
    team        TEXT    NOT NULL,
    opponent    TEXT,
    venue       TEXT,
    event_id    TEXT,
    pick        TEXT,
    probability REAL,
    top3        TEXT,
    engines     TEXT,
    pick_logloss TEXT,
    probability_logloss REAL,
    agrees      INTEGER,
    engines_n   INTEGER,
    actual      TEXT,
    verdict     TEXT,
    direction   TEXT,
    goals_for   INTEGER,
    goals_against INTEGER,
    graded_at   REAL,
    PRIMARY KEY(season, day, team)
);
CREATE INDEX IF NOT EXISTS picks_by_season_team ON picks(season, team);
CREATE INDEX IF NOT EXISTS picks_by_season ON picks(season, day);
"""


def parse_symbol(symbol):
    """'2:1' -> (2, 1); anything unparseable -> (None, None)."""
    if not symbol or not isinstance(symbol, str) or ":" not in symbol:
        return None, None
    left, _, right = symbol.partition(":")
    try:
        return int(left), int(right)
    except ValueError:
        return None, None


def direction_of(symbol):
    for_goals, against_goals = parse_symbol(symbol)
    if for_goals is None:
        return None
    if for_goals > against_goals:
        return "win"
    if for_goals < against_goals:
        return "loss"
    return "draw"


def verdict_of(pick, actual):
    """'hit' exact, 'direction' right side of the result, 'miss' otherwise, None when unknown."""
    if not pick or not actual:
        return None, None
    if pick == actual:
        return "hit", direction_of(pick)
    pick_direction, actual_direction = direction_of(pick), direction_of(actual)
    if pick_direction is None or actual_direction is None:
        return None, None
    return ("direction" if pick_direction == actual_direction else "miss"), actual_direction


class PredictionLedger:
    """Append-only store of prediction sheets and their graded results."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.commit()

    # ------------------------------------------------------------------ writing
    def sheet(self, season, day):
        with self.lock:
            row = self.db.execute("SELECT * FROM sheets WHERE season=? AND day=?", (season, day)).fetchone()
        return dict(row) if row else None

    def lock_sheet(self, season, day, picks, *, source="live", locked_at=None, kickoff=None,
                   revision=None, weights=None, hit_weights=None, detail=None):
        """Write a sheet exactly once. Returns True when it was written, False when it already existed.

        The pick columns of an existing sheet are never touched: this is the promise the whole
        workspace rests on — a matchday that has been played keeps the forecast it was given.
        """
        with self.lock:
            if self.db.execute("SELECT 1 FROM sheets WHERE season=? AND day=?", (season, day)).fetchone():
                return False
            stamp = time.time() if locked_at is None else float(locked_at)
            lead = None
            # `late` is a statement about a live lock only: a backfilled sheet was never a live
            # forecast, and `source` already says so, so it must not look like a missed deadline
            if kickoff and source == "live":
                lead = round(float(kickoff) - stamp, 1)
            late = 1 if (lead is not None and lead < 0) else 0
            self.db.execute(
                "INSERT INTO sheets(season, day, source, locked_at, kickoff, lead_seconds, late, revision,"
                " fixtures, weights, hit_weights, detail) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (season, day, source, stamp, kickoff, lead, late, revision, len(picks),
                 json.dumps(weights or {}), json.dumps(hit_weights or {}),
                 json.dumps(detail or {}, default=str)))
            for pick in picks:
                engines = pick.get("engines") or {}
                agrees = sum(1 for value in engines.values() if value.get("score") == pick.get("pick"))
                self.db.execute(
                    "INSERT INTO picks(season, day, team, opponent, venue, event_id, pick, probability,"
                    " top3, engines, pick_logloss, probability_logloss, agrees, engines_n, actual)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (season, day, pick.get("team"), pick.get("opponent"), pick.get("venue"), pick.get("event_id"),
                     pick.get("pick"), pick.get("probability"), json.dumps(pick.get("top3") or []),
                     json.dumps(engines), pick.get("pick_logloss"), pick.get("probability_logloss"),
                     pick.get("agrees", agrees if engines else None),
                     pick.get("engines_n", len(engines) or None), pick.get("actual")))
            self.db.commit()
        return True

    def grade(self, season, day, results):
        """Fill recorded results and verdicts. `results` maps team -> ('h:a' or None).

        Only the grading columns are written: the forecast itself is left untouched, so re-running
        the walk-forward later can never rewrite what the board said at kick-off.
        """
        graded = 0
        with self.lock:
            rows = self.db.execute("SELECT team, pick, pick_logloss FROM picks WHERE season=? AND day=?",
                                   (season, day)).fetchall()
            now = time.time()
            for row in rows:
                actual = results.get(row["team"])
                if not actual:
                    continue
                verdict, direction = verdict_of(row["pick"], actual)
                for_goals, against_goals = parse_symbol(actual)
                self.db.execute(
                    "UPDATE picks SET actual=?, verdict=?, direction=?, goals_for=?, goals_against=?, graded_at=?"
                    " WHERE season=? AND day=? AND team=?",
                    (actual, verdict, direction, for_goals, against_goals, now, season, day, row["team"]))
                graded += 1
            self.db.commit()
        return graded

    # ------------------------------------------------------------------ reading
    def seasons(self):
        with self.lock:
            picks = {row["season"]: dict(row) for row in self.db.execute(
                "SELECT season, COUNT(DISTINCT day) AS days, MIN(day) AS first_day, MAX(day) AS last_day,"
                " SUM(CASE WHEN verdict IS NOT NULL THEN 1 ELSE 0 END) AS graded,"
                " SUM(CASE WHEN verdict='hit' THEN 1 ELSE 0 END) AS hits,"
                " SUM(CASE WHEN verdict='direction' THEN 1 ELSE 0 END) AS direction,"
                " SUM(CASE WHEN verdict='miss' THEN 1 ELSE 0 END) AS misses"
                " FROM picks GROUP BY season").fetchall()}
            sheets = {row["season"]: dict(row) for row in self.db.execute(
                "SELECT season, COUNT(*) AS sheets,"
                " SUM(CASE WHEN source='live' THEN 1 ELSE 0 END) AS live,"
                " SUM(CASE WHEN source='backfill' THEN 1 ELSE 0 END) AS backfilled,"
                " MAX(late) AS any_late, MIN(locked_at) AS first_lock, MAX(locked_at) AS last_lock"
                " FROM sheets GROUP BY season").fetchall()}
        out = []
        for season in sorted(set(picks) | set(sheets), reverse=True):
            entry = dict(picks.get(season) or {})
            entry.update(sheets.get(season) or {})
            entry["season"] = season
            graded = entry.get("graded") or 0
            entry["hit_rate"] = round(100.0 * (entry.get("hits") or 0) / graded, 1) if graded else None
            out.append(entry)
        return out

    def days(self, season):
        """Per-matchday summary of one season's sheets, oldest first."""
        with self.lock:
            rows = self.db.execute(
                "SELECT day, source, locked_at, kickoff, lead_seconds, late, revision, fixtures,"
                " SUM(CASE WHEN actual IS NOT NULL THEN 1 ELSE 0 END) AS graded,"
                " SUM(CASE WHEN verdict='hit' THEN 1 ELSE 0 END) AS hits,"
                " SUM(CASE WHEN verdict='direction' THEN 1 ELSE 0 END) AS direction,"
                " SUM(CASE WHEN verdict='miss' THEN 1 ELSE 0 END) AS misses"
                " FROM picks JOIN sheets USING(season, day) WHERE season=?"
                " GROUP BY day ORDER BY day", (season,)).fetchall()
        return [dict(row) for row in rows]

    def sheet_rows(self, season, day):
        with self.lock:
            sheet = self.db.execute("SELECT * FROM sheets WHERE season=? AND day=?", (season, day)).fetchone()
            picks = self.db.execute("SELECT * FROM picks WHERE season=? AND day=? ORDER BY team",
                                    (season, day)).fetchall()
        if not sheet:
            return None
        out = dict(sheet)
        try:
            out["weights"] = json.loads(out.get("weights") or "{}")
        except ValueError:
            out["weights"] = {}
        try:
            out["hit_weights"] = json.loads(out.get("hit_weights") or "{}")
        except ValueError:
            out["hit_weights"] = {}
        out["picks"] = [self._pick(row) for row in picks]
        return out

    def season_rows(self, season):
        """Every graded or pending pick of a season, in matchday order — the season's sheets."""
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM picks WHERE season=? ORDER BY day, team", (season,)).fetchall()
        return [self._pick(row) for row in rows]

    def team_rows(self, season, team):
        with self.lock:
            rows = self.db.execute(
                "SELECT * FROM picks WHERE season=? AND team=? ORDER BY day", (season, team)).fetchall()
        return [self._pick(row) for row in rows]

    def board(self, season, days=None):
        """The frozen board: recorded results plus the picks, exactly as they were locked."""
        rows = self.season_rows(season)
        if days is not None:
            keep = set(int(day) for day in days)
            rows = [row for row in rows if row["day"] in keep]
        return rows

    @staticmethod
    def _pick(row):
        entry = dict(row)
        for key in ("top3", "engines"):
            try:
                entry[key] = json.loads(entry.get(key) or ("[]" if key == "top3" else "{}"))
            except ValueError:
                entry[key] = [] if key == "top3" else {}
        return entry

    # ------------------------------------------------------------------ bookkeeping
    def ungraded_sheets(self, limit=64):
        """The sheets whose results are still outstanding, newest season first.

        Grading must not depend on what the source is publishing right now: a season can end, the
        source can move on, and the results that arrived last still have to be written into the
        sheets that predicted them.
        """
        with self.lock:
            rows = self.db.execute(
                "SELECT p.season, p.day FROM picks p WHERE p.actual IS NULL"
                " GROUP BY p.season, p.day ORDER BY p.season DESC, p.day DESC LIMIT ?",
                (int(limit),)).fetchall()
        return [(row["season"], row["day"]) for row in rows]

    def pending_sheets(self, limit=12):
        """Sheets whose matchday has not been graded yet, newest season first."""
        with self.lock:
            rows = self.db.execute(
                "SELECT s.season, s.day FROM sheets s WHERE EXISTS ("
                "  SELECT 1 FROM picks p WHERE p.season=s.season AND p.day=s.day AND p.actual IS NULL"
                ") ORDER BY s.season DESC, s.day ASC LIMIT ?", (int(limit),)).fetchall()
        return [(row["season"], row["day"]) for row in rows]

    def covered_days(self, season):
        with self.lock:
            rows = self.db.execute("SELECT day FROM sheets WHERE season=?", (season,)).fetchall()
        return {row["day"] for row in rows}

    def integrity(self, season=None):
        """How much of the ledger was locked before the first kick-off — the honesty figure."""
        clause, params = ("WHERE season=?", (season,)) if season is not None else ("", ())
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS sheets,"
                " SUM(CASE WHEN late=0 AND source='live' THEN 1 ELSE 0 END) AS on_time,"
                " SUM(CASE WHEN source='live' THEN 1 ELSE 0 END) AS live,"
                " SUM(CASE WHEN source='backfill' THEN 1 ELSE 0 END) AS backfilled,"
                " AVG(CASE WHEN source='live' THEN lead_seconds END) AS mean_lead,"
                " MIN(CASE WHEN source='live' THEN lead_seconds END) AS worst_lead"
                f" FROM sheets {clause}", params).fetchone()
        out = dict(row) if row else {}
        live = out.get("live") or 0
        out["on_time_share"] = round(100.0 * (out.get("on_time") or 0) / live, 1) if live else None
        out["live_share"] = round(100.0 * live / out["sheets"], 1) if out.get("sheets") else None
        out["mean_lead"] = round(out["mean_lead"], 1) if out.get("mean_lead") is not None else None
        return out

    def stats(self, season=None):
        clause, params = ("WHERE season=?", (season,)) if season is not None else ("", ())
        with self.lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS picks,"
                " SUM(CASE WHEN actual IS NOT NULL THEN 1 ELSE 0 END) AS graded,"
                " SUM(CASE WHEN verdict='hit' THEN 1 ELSE 0 END) AS hits,"
                " SUM(CASE WHEN verdict='direction' THEN 1 ELSE 0 END) AS direction,"
                " SUM(CASE WHEN verdict='miss' THEN 1 ELSE 0 END) AS misses,"
                " MAX(CASE WHEN actual IS NOT NULL THEN day END) AS last_graded_day"
                f" FROM picks {clause}", params).fetchone()
        out = dict(row) if row else {}
        graded = out.get("graded") or 0
        out["hit_rate"] = round(100.0 * (out.get("hits") or 0) / graded, 1) if graded else None
        out["direction_rate"] = round(100.0 * (out.get("direction") or 0) / graded, 1) if graded else None
        return out

    def team_stats(self, season):
        with self.lock:
            rows = self.db.execute(
                "SELECT team, COUNT(*) AS picks,"
                " SUM(CASE WHEN actual IS NOT NULL THEN 1 ELSE 0 END) AS graded,"
                " SUM(CASE WHEN verdict='hit' THEN 1 ELSE 0 END) AS hits,"
                " SUM(CASE WHEN verdict='direction' THEN 1 ELSE 0 END) AS direction,"
                " SUM(CASE WHEN verdict='miss' THEN 1 ELSE 0 END) AS misses,"
                " AVG(probability) AS mean_probability"
                " FROM picks WHERE season=? GROUP BY team ORDER BY team", (season,)).fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            graded = entry.get("graded") or 0
            entry["hit_rate"] = round(100.0 * (entry.get("hits") or 0) / graded, 1) if graded else None
            out.append(entry)
        return out

    def version(self):
        """A cheap signature of the ledger's contents, for the ETag and the tick endpoint."""
        with self.lock:
            row = self.db.execute(
                "SELECT (SELECT COUNT(*) FROM sheets) AS sheets,"
                " (SELECT MAX(locked_at) FROM sheets) AS locked,"
                " (SELECT MAX(graded_at) FROM picks) AS graded").fetchone()
        return f"{row['sheets']}|{row['locked'] or 0:.3f}|{row['graded'] or 0:.3f}"

    def checkpoint(self):
        with self.lock:
            try:
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                self.db.commit()
            except sqlite3.Error:
                pass

    def close(self):
        with self.lock:
            try:
                self.db.commit()
            finally:
                self.db.close()
