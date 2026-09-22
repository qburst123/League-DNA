"""Head-to-head matchup DNA: every meeting of a team pair, and the half it was decided in.

Three things live here, in order:

**1. The index.** `matchup_index` materialises every stored result as one row per *ordered* pair
(home, away) with the goals scored in each half and the half that out-scored the other. In this
league every ordered pair meets exactly once per season (16 teams, 30 matchdays, home and away), so
the table is the answer to "show me Walinzi BSL vs KCBB BSL across every season" without scanning
the match table. It is maintained incrementally and rebuilt from nothing when the schema is new.

**2. The prediction.** For an upcoming fixture the engines here answer one question only: of the
three possible outcomes, **which half produced more goals — the first, the second, or neither
(equal)?** The evidence is that pair's own recorded meetings, exactly as the fixture is drawn
(ordered pair, venue included), with the venue-free view available and used as a stated fallback
when the ordered sample is too thin to speak. Counts are Jeffreys-smoothed so a three-way split
with a small sample cannot claim certainty, and the league-wide base rate is always printed beside
the pair's own rate so the reader can see how unusual the pair is.

**3. The ledger.** The prediction is frozen: a sheet is written once, when the matchday is
announced, and never rewritten. Grading fills in the recorded HT/FT and the actual half. This is
the same discipline as `backend/ledger.py` — see `docs/playground.md` for why.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

HALVES = ("1H", "2H", "EQ")
HALF_LABEL = {"1H": "1st half", "2H": "2nd half", "EQ": "Equal"}
HALF_SHORT = {"1H": "1ST HALF", "2H": "2ND HALF", "EQ": "EQUAL"}
VENUES = ("ordered", "either")

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS matchup_index(
  match_id    INTEGER PRIMARY KEY,
  season      INTEGER NOT NULL,
  day         INTEGER NOT NULL,
  home        TEXT    NOT NULL,
  away        TEXT    NOT NULL,
  ht_home     INTEGER NOT NULL,
  ht_away     INTEGER NOT NULL,
  ft_home     INTEGER NOT NULL,
  ft_away     INTEGER NOT NULL,
  first_half  INTEGER NOT NULL,
  second_half INTEGER NOT NULL,
  half        TEXT    NOT NULL,
  updated_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS matchup_pair   ON matchup_index(home, away, season, day);
CREATE INDEX IF NOT EXISTS matchup_season ON matchup_index(season, day);
CREATE TABLE IF NOT EXISTS matchup_index_season(
  season INTEGER PRIMARY KEY, matches INTEGER NOT NULL, built_at REAL NOT NULL);
"""

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS half_sheets(
  season      INTEGER NOT NULL,
  day         INTEGER NOT NULL,
  source      TEXT    NOT NULL DEFAULT 'live',
  locked_at   REAL,
  kickoff     REAL,
  lead_seconds REAL,
  late        INTEGER NOT NULL DEFAULT 0,
  revision    INTEGER,
  fixtures    INTEGER,
  detail      TEXT,
  PRIMARY KEY(season, day)
);
CREATE TABLE IF NOT EXISTS half_picks(
  season      INTEGER NOT NULL,
  day         INTEGER NOT NULL,
  home        TEXT    NOT NULL,
  away        TEXT    NOT NULL,
  event_id    TEXT,
  pick        TEXT,
  probability REAL,
  counts      TEXT,
  sample      INTEGER,
  basis       TEXT,
  pair_rate   REAL,
  league_rate REAL,
  actual      TEXT,
  first_half  INTEGER,
  second_half INTEGER,
  ht          TEXT,
  ft          TEXT,
  verdict     TEXT,
  graded_at   REAL,
  PRIMARY KEY(season, day, home, away)
);
CREATE INDEX IF NOT EXISTS half_picks_open ON half_picks(season, day, actual);
"""


# ---------------------------------------------------------------------------- the half of a match
def split_halves(ht_home, ht_away, ft_home, ft_away):
    """Goals in each half, and which half out-scored the other.

    The first half is the recorded HT score. The second half is the FT score minus the HT score,
    which is the only definition that survives a match where a team scored in both halves.
    """
    ht_home = int(ht_home or 0)
    ht_away = int(ht_away or 0)
    ft_home = int(ft_home if ft_home is not None else ht_home)
    ft_away = int(ft_away if ft_away is not None else ht_away)
    first = ht_home + ht_away
    second = (ft_home - ht_home) + (ft_away - ht_away)
    half = "1H" if first > second else "2H" if second > first else "EQ"
    return first, second, half


def jeffreys(counts, prior=0.5):
    """Three-way probability from counts, smoothed so a thin sample cannot claim certainty."""
    total = sum(counts.get(key, 0) for key in HALVES)
    denominator = total + prior * len(HALVES)
    return {key: round((counts.get(key, 0) + prior) / denominator, 4) for key in HALVES}


def predict_half(counts, sample, *, basis="ordered", minimum_ordered=3,
                 pair_rates=None, league_rates=None, ordered_sample=None):
    """The pair's own record, read as a three-way call, with its confidence stated."""
    probabilities = jeffreys(counts)
    pick = max(HALVES, key=lambda key: (probabilities[key], -HALVES.index(key)))
    ordered = [{"half": key, "count": int(counts.get(key, 0)),
                "share": round(100.0 * counts.get(key, 0) / sample, 1) if sample else None,
                "probability": round(100.0 * probabilities[key], 2)} for key in HALVES]
    ordered.sort(key=lambda row: -row["probability"])
    runner_up = ordered[1]["probability"] if len(ordered) > 1 else None
    confidence = "thin" if sample < 6 else "fair" if sample < 15 else "strong"
    if basis == "either" and minimum_ordered:
        note = (f"this venue has only {ordered_sample} recorded meeting(s); the reading is the "
                f"{sample} meetings between these two teams at either venue")
    else:
        note = f"{sample} recorded meeting(s) of this exact fixture"
    return {"pick": pick, "label": HALF_SHORT[pick], "long_label": HALF_LABEL[pick],
            "probability": round(100.0 * probabilities[pick], 2), "margin": (
                round(100.0 * (probabilities[pick] - probabilities[ordered[1]["half"]]), 2) if runner_up else None),
            "counts": {key: int(counts.get(key, 0)) for key in HALVES}, "sample": int(sample),
            "basis": basis, "confidence": confidence, "note": note, "distribution": ordered,
            "pair_rates": pair_rates, "league_rates": league_rates}


# ---------------------------------------------------------------------------- the index
class MatchupIndex:
    """Every stored result as one row per ordered pair, with its halves worked out."""

    def __init__(self, store):
        self.store = store
        self.lock = threading.RLock()
        with self.store.lock:
            self.store.db.executescript(INDEX_SCHEMA)
        self._ensure()

    # ------------------------------------------------------------------ maintenance
    def revision(self):
        row = self.store.one("SELECT COUNT(*) matches,MAX(updated_at) refreshed FROM matchup_index")
        return f"{row['matches']}|{round(row['refreshed'] or 0, 1)}"

    def _ensure(self):
        """Rebuild only the seasons whose stored result count changed — cheap on every tick."""
        stored = {row["season"]: row["matches"] for row in
                  self.store.all("SELECT season,COUNT(*) matches FROM matches WHERE status='final' "
                                 "AND ht_home IS NOT NULL AND ft_home IS NOT NULL GROUP BY season")}
        built = {row["season"]: row["matches"] for row in
                 self.store.all("SELECT season,matches FROM matchup_index_season")}
        stale = [season for season, count in stored.items() if built.get(season) != count]
        for season in stale:
            self.rebuild(season)
        for season in list(built):
            if season not in stored:
                with self.store.lock:
                    self.store.db.execute("DELETE FROM matchup_index WHERE season=?", (season,))
                    self.store.db.execute("DELETE FROM matchup_index_season WHERE season=?", (season,))
        return len(stale)

    def rebuild(self, season):
        rows = self.store.all("""SELECT id,season,day,home,away,ht_home,ht_away,ft_home,ft_away
            FROM matches WHERE season=? AND status='final' AND ht_home IS NOT NULL AND ft_home IS NOT NULL
            ORDER BY day""", (season,))
        now = time.time()
        with self.store.lock:
            self.store.db.execute("BEGIN IMMEDIATE")
            try:
                self.store.db.execute("DELETE FROM matchup_index WHERE season=?", (season,))
                for row in rows:
                    first, second, half = split_halves(row["ht_home"], row["ht_away"], row["ft_home"], row["ft_away"])
                    self.store.db.execute(
                        "INSERT OR REPLACE INTO matchup_index VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["id"], row["season"], row["day"], row["home"], row["away"],
                         row["ht_home"], row["ht_away"], row["ft_home"], row["ft_away"],
                         first, second, half, now))
                self.store.db.execute(
                    "INSERT INTO matchup_index_season VALUES(?,?,?) ON CONFLICT(season) DO UPDATE SET "
                    "matches=excluded.matches,built_at=excluded.built_at", (season, len(rows), now))
                self.store.db.execute("COMMIT")
            except Exception:
                self.store.db.execute("ROLLBACK")
                raise
        return len(rows)

    def rebuild_all(self):
        seasons = [row["season"] for row in self.store.all("SELECT DISTINCT season FROM matches WHERE status='final'")]
        return {season: self.rebuild(season) for season in seasons}

    # ------------------------------------------------------------------ reads
    def pairs(self, query=None, limit=500):
        """The dropdown: every pair that has ever met, most meetings first."""
        clause, args = "", ()
        if query:
            like = f"%{query.strip()}%"
            clause = "WHERE home LIKE ? OR away LIKE ?"
            args = (like, like)
        rows = self.store.all(f"""
            SELECT home,away,COUNT(*) meetings,
                   SUM(half='1H') first_half,SUM(half='2H') second_half,SUM(half='EQ') equal_half,
                   MIN(season) first_season,MAX(season) last_season,
                   SUM(first_half) goals_first,SUM(second_half) goals_second
            FROM matchup_index {clause}
            GROUP BY home,away ORDER BY meetings DESC, home ASC, away ASC LIMIT ?""", (*args, int(limit)))
        for row in rows:
            row["label"] = f"{row['home']} vs {row['away']}"
            row["counts"] = {"1H": row.pop("first_half"), "2H": row.pop("second_half"), "EQ": row.pop("equal_half")}
        return rows

    def meetings(self, home, away, either=False):
        if either:
            rows = self.store.all("""SELECT * FROM matchup_index
                WHERE (home=? AND away=?) OR (home=? AND away=?)
                ORDER BY season DESC, day ASC""", (home, away, away, home))
        else:
            rows = self.store.all("""SELECT * FROM matchup_index WHERE home=? AND away=?
                ORDER BY season DESC, day ASC""", (home, away))
        return rows

    def league_rates(self):
        row = self.store.one("""SELECT COUNT(*) n,SUM(half='1H') f,SUM(half='2H') s,SUM(half='EQ') e,
                                       SUM(first_half) gf,SUM(second_half) gs FROM matchup_index""")
        n = row["n"] or 0
        return {"matches": n,
                "counts": {"1H": row["f"] or 0, "2H": row["s"] or 0, "EQ": row["e"] or 0},
                "rates": {key: round(100.0 * (row[alias] or 0) / n, 1) if n else None
                          for key, alias in (("1H", "f"), ("2H", "s"), ("EQ", "e"))},
                "mean_first_half_goals": round((row["gf"] or 0) / n, 2) if n else None,
                "mean_second_half_goals": round((row["gs"] or 0) / n, 2) if n else None}

    def analysis(self, home, away, either=False, minimum_ordered=3):
        """Everything the historical sub-workspace shows for one selected pair."""
        ordered = self.meetings(home, away, either=False)
        rows = ordered if either is False else self.meetings(home, away, either=True)
        counts = {key: sum(1 for row in rows if row["half"] == key) for key in HALVES}
        sample = len(rows)
        pair_rates = {key: round(100.0 * counts[key] / sample, 1) if sample else None for key in HALVES}
        basis = "ordered" if not either else "either"
        if not either and sample < minimum_ordered:
            # The exact fixture has barely been played; say so and widen the lens, labelled.
            wide = self.meetings(home, away, either=True)
            if len(wide) > sample:
                wide_counts = {key: sum(1 for row in wide if row["half"] == key) for key in HALVES}
                return {"home": home, "away": away, "basis": "either", "requested_basis": "ordered",
                        "note": f"only {sample} recorded meeting(s) of this exact fixture; widened to "
                                f"both venues ({len(wide)} meetings)",
                        "ordered_meetings": sample, "rows": wide, "counts": wide_counts,
                        "pair_rates": {key: round(100.0 * wide_counts[key] / len(wide), 1) for key in HALVES},
                        "prediction": predict_half(wide_counts, len(wide), basis="either", minimum_ordered=minimum_ordered,
                                                   ordered_sample=sample, pair_rates=None,
                                                   league_rates=self.league_rates()["rates"]),
                        "league": self.league_rates()}
        return {"home": home, "away": away, "basis": basis, "note": None, "ordered_meetings": len(ordered),
                "rows": rows, "counts": counts, "pair_rates": pair_rates,
                "prediction": predict_half(counts, sample, basis=basis, minimum_ordered=minimum_ordered,
                                           pair_rates=pair_rates, league_rates=self.league_rates()["rates"])
                if sample else None,
                "league": self.league_rates()}


# ---------------------------------------------------------------------------- the frozen ledger
class HalfLedger:
    """One sheet per matchday: locked when the matchday is announced, graded when it is played."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self.lock:
            self.db.executescript(LEDGER_SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.commit()

    # ------------------------------------------------------------------ writing
    def sheet(self, season, day):
        with self.lock:
            row = self.db.execute("SELECT * FROM half_sheets WHERE season=? AND day=?", (season, day)).fetchone()
            if not row:
                return None
            out = dict(row)
            out["picks"] = [dict(p) for p in self.db.execute(
                "SELECT * FROM half_picks WHERE season=? AND day=? ORDER BY home", (season, day)).fetchall()]
            return out

    def has_sheet(self, season, day):
        with self.lock:
            return self.db.execute("SELECT 1 FROM half_sheets WHERE season=? AND day=?",
                                   (season, day)).fetchone() is not None

    def lock_sheet(self, season, day, rows, *, source="live", locked_at=None, kickoff=None,
                   revision=None, detail=None):
        """Write the sheet once. A second call with the same (season, day) is a no-op by design."""
        with self.lock:
            if self.db.execute("SELECT 1 FROM half_sheets WHERE season=? AND day=?",
                               (season, day)).fetchone():
                return False
            locked_at = locked_at or time.time()
            lead = round(kickoff - locked_at, 1) if kickoff else None
            late = 1 if (lead is not None and lead < 0 and source == "live") else 0
            self.db.execute("BEGIN IMMEDIATE")
            try:
                self.db.execute("INSERT INTO half_sheets(season,day,source,locked_at,kickoff,lead_seconds,late,"
                                "revision,fixtures,detail) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (season, day, source, locked_at, kickoff, lead, late, revision,
                                 len(rows), json.dumps(detail or {})))
                for row in rows:
                    self.db.execute("""INSERT INTO half_picks(season,day,home,away,event_id,pick,probability,counts,
                        sample,basis,pair_rate,league_rate) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                                    (season, day, row["home"], row["away"], row.get("event_id"),
                                     row["pick"], row["probability"], json.dumps(row.get("counts") or {}),
                                     row.get("sample"), row.get("basis"),
                                     (row.get("pair_rates") or {}).get(row["pick"]),
                                     (row.get("league_rates") or {}).get(row["pick"])))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return True

    def grade(self, season, day, results):
        """Fill in only the result columns: HT/FT as recorded, the half that happened, the verdict."""
        updated = 0
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for row in results:
                    key = (season, day, row["home"], row["away"])
                    existing = self.db.execute("""SELECT pick,actual FROM half_picks
                        WHERE season=? AND day=? AND home=? AND away=?""", key).fetchone()
                    if not existing or existing["actual"] is not None or not row.get("half"):
                        continue
                    verdict = "hit" if existing["pick"] == row["half"] else "miss"
                    self.db.execute("""UPDATE half_picks SET actual=?,first_half=?,second_half=?,ht=?,ft=?,
                        verdict=?,graded_at=? WHERE season=? AND day=? AND home=? AND away=?""",
                                    (row["half"], row.get("first_half"), row.get("second_half"),
                                     row.get("ht"), row.get("ft"), verdict, time.time(), *key))
                    updated += 1
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
        return updated

    # ------------------------------------------------------------------ reads
    def ungraded_sheets(self, limit=64):
        with self.lock:
            rows = self.db.execute("""SELECT DISTINCT season,day FROM half_picks WHERE actual IS NULL
                ORDER BY season DESC, day DESC LIMIT ?""", (int(limit),)).fetchall()
        return [(row["season"], row["day"]) for row in rows]

    def pending_sheets(self, limit=12):
        with self.lock:
            rows = self.db.execute("""SELECT DISTINCT season,day FROM half_picks WHERE actual IS NULL
                ORDER BY season DESC, day ASC LIMIT ?""", (int(limit),)).fetchall()
        return [(row["season"], row["day"]) for row in rows]

    def seasons(self):
        with self.lock:
            rows = self.db.execute("""SELECT s.season,COUNT(DISTINCT s.day) sheets,COUNT(p.home) picks,
                SUM(p.actual IS NOT NULL) graded,SUM(p.verdict='hit') hits,SUM(p.verdict='miss') misses,
                MIN(s.locked_at) first_lock,MAX(s.locked_at) last_lock,
                SUM(s.source='live') live,SUM(s.source='backfill') backfilled
                FROM half_sheets s LEFT JOIN half_picks p ON p.season=s.season AND p.day=s.day
                GROUP BY s.season ORDER BY s.season DESC""").fetchall()
        out = []
        for row in rows:
            entry = dict(row)
            graded = entry["graded"] or 0
            entry["hit_rate"] = round(100.0 * (entry["hits"] or 0) / graded, 1) if graded else None
            out.append(entry)
        return out

    def stats(self, season=None):
        clause, args = ("WHERE season=?", (season,)) if season is not None else ("", ())
        with self.lock:
            row = self.db.execute(f"""SELECT COUNT(*) picks,SUM(actual IS NOT NULL) graded,
                SUM(verdict='hit') hits,SUM(verdict='miss') misses,
                MAX(CASE WHEN actual IS NOT NULL THEN day END) last_graded_day
                FROM half_picks {clause}""", args).fetchone()
            sheets = self.db.execute(f"SELECT COUNT(*) n FROM half_sheets {clause}", args).fetchone()["n"]
        out = dict(row)
        out["sheets"] = sheets
        graded = out.get("graded") or 0
        out["hit_rate"] = round(100.0 * (out.get("hits") or 0) / graded, 1) if graded else None
        return out

    def integrity(self, season=None):
        clause, args = ("WHERE season=?", (season,)) if season is not None else ("", ())
        with self.lock:
            live = self.db.execute(f"""SELECT COUNT(*) n,SUM(late) late,AVG(lead_seconds) mean_lead,
                MIN(lead_seconds) worst_lead,SUM(source='live') live_sheets,SUM(source='backfill') backfilled
                FROM half_sheets {clause}""", args).fetchone()
        live_n = live["live_sheets"] or 0
        lead = self.db.execute(f"""SELECT AVG(lead_seconds) mean,MIN(lead_seconds) worst,SUM(lead_seconds>=0) on_time
            FROM half_sheets {clause} {'AND' if clause else 'WHERE'} source='live'""", args).fetchone()
        return {"sheets": live["n"] or 0, "live": live_n, "backfilled": live["backfilled"] or 0,
                "late": live["late"] or 0, "mean_lead": round(lead["mean"], 1) if live_n and lead["mean"] is not None else None,
                "worst_lead": round(lead["worst"], 1) if live_n and lead["worst"] is not None else None,
                "on_time_share": round(100.0 * (lead["on_time"] or 0) / live_n, 1) if live_n else None}

    def version(self):
        with self.lock:
            row = self.db.execute("""SELECT COUNT(*) picks,MAX(graded_at) graded_at FROM half_picks""").fetchone()
            sheets = self.db.execute("SELECT COUNT(*) n,MAX(locked_at) locked FROM half_sheets").fetchone()
        return f"{sheets['n']}|{round(sheets['locked'] or 0, 1)}|{row['picks']}|{round(row['graded_at'] or 0, 1)}"

    def checkpoint(self):
        with self.lock:
            self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self):
        with self.lock:
            self.db.close()


# ---------------------------------------------------------------------------- the service
class MatchupService:
    """Keeps the half-time ledger in sync with the archive and composes the workspace payloads.

    Same division of labour as the playground service: `sync()` is the only writer and is cheap
    enough to run every few seconds, while `compose()` reads the frozen ledger and never touches the
    engines. Nothing the browser asks for can change a prediction that was already locked.
    """

    SYNC_SECONDS = 5

    def __init__(self, store, data_dir, enabled=True):
        self.store = store
        self.data_dir = Path(data_dir)
        self.index = MatchupIndex(store)
        self.ledger = HalfLedger(self.data_dir / "predictions.sqlite")
        self.enabled = enabled
        self.lock = threading.RLock()
        self.sync_state = {"at": None, "locked": [], "graded": 0, "backfilled": 0, "seconds": None,
                           "checks": 0, "error": None}

    # ------------------------------------------------------------------ the target
    def target(self):
        """The matchday to predict: what the source is publishing, or the newest ledger season."""
        upcoming = self.store.get_meta("upcoming", {}) or {}
        season, day = upcoming.get("season"), upcoming.get("day")
        if not season:
            latest = self.store.get_meta("latest_result", {}) or {}
            season, day = latest.get("season"), (latest.get("day") or 0) + 1
        if not season:
            row = self.store.one("SELECT MAX(id) id FROM seasons")
            season, day = (row or {}).get("id"), 1
        fixtures = self.store.all("""SELECT * FROM matches WHERE season=? AND day=?
            ORDER BY id""", (season, day)) if season and day else []
        if not fixtures:
            fixtures = self.store.all("""SELECT * FROM matches WHERE season=?
                ORDER BY day DESC, id LIMIT 8""", (season,)) if season else []
        kickoff = min([row["start_time"] for row in fixtures if row["start_time"]], default=None)
        return {"season": season, "day": day, "fixtures": fixtures, "kickoff": kickoff,
                "label": f"{season}/{day}" if season else None}

    def days_the_source_has(self, season):
        days = set(self.store.published_days(season))
        days |= {row["day"] for row in self.store.all(
            "SELECT DISTINCT day FROM matches WHERE season=? AND status='final'", (season,))}
        return sorted(days)

    # ------------------------------------------------------------------ prediction from history
    def prediction_for(self, home, away, before_season=None, minimum_ordered=3):
        """The three-way half call for one fixture, from that pair's recorded meetings.

        ``before_season`` makes the call honest for a historical matchday: only meetings recorded in
        earlier seasons may be used, which is the same information a live lock would have had.
        """
        rows = self.index.meetings(home, away, either=False)
        if before_season is not None:
            rows = [row for row in rows if row["season"] < before_season]
        sample = len(rows)
        counts = {key: sum(1 for row in rows if row["half"] == key) for key in HALVES}
        basis, ordered_sample = "ordered", sample
        if sample < minimum_ordered:
            wide = self.index.meetings(home, away, either=True)
            if before_season is not None:
                wide = [row for row in wide if row["season"] < before_season]
            if len(wide) > sample:
                counts = {key: sum(1 for row in wide if row["half"] == key) for key in HALVES}
                sample, basis = len(wide), "either"
        league = self.index.league_rates()["rates"]
        call = predict_half(counts, sample, basis=basis, minimum_ordered=minimum_ordered,
                            ordered_sample=ordered_sample, league_rates=league,
                            pair_rates={key: round(100.0 * counts[key] / sample, 1) if sample else None
                                        for key in HALVES})
        call["home"], call["away"] = home, away
        call["history"] = [{"season": row["season"], "day": row["day"],
                            "ht": f"{row['ht_home']}:{row['ht_away']}", "ft": f"{row['ft_home']}:{row['ft_away']}",
                            "first_half": row["first_half"], "second_half": row["second_half"],
                            "half": row["half"]} for row in rows]
        call["history_count"] = len(rows)
        return call

    def lock_matchday(self, season, day, fixtures=None, source="live", revision=None):
        """Write the matchday's sheet once. Returns True when this call created it."""
        if self.ledger.has_sheet(season, day):
            return False
        fixtures = fixtures if fixtures is not None else self.store.all(
            "SELECT * FROM matches WHERE season=? AND day=? ORDER BY id", (season, day))
        upcoming = self.store.get_meta("upcoming", {}) or {}
        rows, kickoffs = [], []
        for fixture in fixtures:
            call = self.prediction_for(fixture["home"], fixture["away"])
            rows.append({"home": fixture["home"], "away": fixture["away"],
                         "event_id": fixture["event_id"], "pick": call["pick"],
                         "probability": call["probability"], "counts": call["counts"],
                         "sample": call["sample"], "basis": call["basis"],
                         "pair_rates": call["pair_rates"], "league_rates": call["league_rates"]})
            if fixture["start_time"]:
                kickoffs.append(fixture["start_time"])
        kickoff = min(kickoffs, default=None)
        locked_at = time.time()
        if source == "backfill":
            created = self.ledger.lock_sheet(season, day, rows, source="backfill", locked_at=locked_at,
                                             kickoff=None, revision=revision,
                                             detail={"note": "reconstructed from meetings recorded before this season"})
            return created
        created = self.ledger.lock_sheet(season, day, rows, source="live", locked_at=locked_at,
                                         kickoff=kickoff, revision=revision,
                                         detail={"event_ids": [row["event_id"] for row in rows],
                                                 "source_day": upcoming.get("day")})
        return created

    def backfill_season(self, season, limit=30):
        """Give a season that predates the ledger the sheets it would have had, honestly labelled."""
        done = 0
        played = [row["day"] for row in self.store.all(
            "SELECT DISTINCT day FROM matches WHERE season=? AND status='final' ORDER BY day", (season,))]
        for day in played[:limit]:
            if self.ledger.has_sheet(season, day):
                continue
            fixtures = self.store.all("SELECT * FROM matches WHERE season=? AND day=? ORDER BY id", (season, day))
            rows = []
            for fixture in fixtures:
                call = self.prediction_for(fixture["home"], fixture["away"], before_season=season)
                rows.append({"home": fixture["home"], "away": fixture["away"], "event_id": fixture["event_id"],
                             "pick": call["pick"], "probability": call["probability"], "counts": call["counts"],
                             "sample": call["sample"], "basis": call["basis"],
                             "pair_rates": call["pair_rates"], "league_rates": call["league_rates"]})
            if rows:
                self.ledger.lock_sheet(season, day, rows, source="backfill", locked_at=time.time(),
                                       detail={"note": "reconstructed from meetings recorded before this season"})
                done += 1
        return done

    # ------------------------------------------------------------------ sync
    def sync(self, force=False):
        """Lock what is announced, grade what is played. The only writer."""
        if not self.enabled:
            return {"skipped": True}
        began = time.perf_counter()
        locked, graded, backfilled = [], 0, 0
        try:
            self.index._ensure()
            target = self.target()
            season, day = target["season"], target["day"]
            # 1. the matchday the source is publishing (and any later unplayed matchday it has opened)
            if season and day and not self.ledger.has_sheet(season, day):
                if self.lock_matchday(season, day, target["fixtures"]):
                    locked.append({"season": season, "day": day, "source": "live"})
            # 2. the ongoing season's played matchdays that predate the ledger get honest backfilled
            #    sheets, so the board can be read across MD1-MD30 rather than only from today forward.
            if season:
                missing = [d for d in self.days_the_source_has(season) if not self.ledger.has_sheet(season, d)]
                played_missing = [d for d in missing if self.store.one(
                    "SELECT COUNT(*) n FROM matches WHERE season=? AND day=? AND status='final'",
                    (season, d))["n"] == 8]
                for d in played_missing[:6]:
                    if self.lock_matchday(season, d, source="backfill"):
                        backfilled += 1
                        locked.append({"season": season, "day": d, "source": "backfill"})
            # 3. grade everything outstanding, in every season — after any sheet this tick created,
            #    so a sheet locked and a result already in the archive are closed in the same pass.
            for sheet_season, sheet_day in self.ledger.ungraded_sheets(limit=96):
                results = self.store.all("""SELECT * FROM matches WHERE season=? AND day=?
                    AND status='final' AND ht_home IS NOT NULL AND ft_home IS NOT NULL""",
                    (sheet_season, sheet_day))
                if not results:
                    continue
                graded += self.ledger.grade(sheet_season, sheet_day, [{
                    "home": row["home"], "away": row["away"],
                    "half": split_halves(row["ht_home"], row["ht_away"], row["ft_home"], row["ft_away"])[2],
                    "first_half": split_halves(row["ht_home"], row["ht_away"], row["ft_home"], row["ft_away"])[0],
                    "second_half": split_halves(row["ht_home"], row["ht_away"], row["ft_home"], row["ft_away"])[1],
                    "ht": f"{row['ht_home']}:{row['ht_away']}", "ft": f"{row['ft_home']}:{row['ft_away']}"}
                    for row in results])
            self.sync_state = {"at": time.time(), "locked": locked, "graded": graded, "backfilled": backfilled,
                               "seconds": round(time.perf_counter() - began, 2),
                               "checks": (self.sync_state.get("checks") or 0) + 1, "error": None}
        except Exception as exc:                       # noqa: BLE001 — a failed sync must not kill the loop
            self.sync_state = {**self.sync_state, "at": time.time(), "error": str(exc)[:240]}
            self.store.log("matchups", f"half-time ledger sync failed: {str(exc)[:200]}", "warning")
        return self.sync_state

    # ------------------------------------------------------------------ compose
    def board(self, season=None):
        """Recorded HT/FT and the frozen half call for every team, matchday by matchday."""
        season = int(season) if season else (self.target()["season"])
        rows = self.store.all("""SELECT * FROM matchup_index WHERE season=? ORDER BY day, home""", (season,))
        teams = sorted({name for row in rows for name in (row["home"], row["away"])})
        picks, sheets, by_team = {}, {}, {}
        with self.ledger.lock:
            for pick in self.ledger.db.execute("SELECT * FROM half_picks WHERE season=? ORDER BY day",
                                               (season,)).fetchall():
                picks[(pick["day"], pick["home"], pick["away"])] = dict(pick)
                by_team[(pick["day"], pick["home"])] = dict(pick)
                by_team[(pick["day"], pick["away"])] = dict(pick)
            for row in self.ledger.db.execute("SELECT * FROM half_sheets WHERE season=?",
                                              (season,)).fetchall():
                sheets[row["day"]] = dict(row)
        days = sorted({row["day"] for row in rows} | set(sheets))
        board = []
        for team in teams:
            results, calls = [], []
            for day in days:
                fixture = next((row for row in rows if row["day"] == day and team in (row["home"], row["away"])), None)
                if not fixture:
                    # The matchday to play has a frozen card but no recorded score yet. It still
                    # belongs on the board, directly under the last played matchday — the whole point
                    # of the workspace is to see the call before the match is played.
                    pending = by_team.get((day, team))
                    if pending:
                        calls.append({"day": day,
                                      "opponent": pending["away"] if pending["home"] == team else pending["home"],
                                      "home": pending["home"], "away": pending["away"],
                                      "pick": pending["pick"], "label": HALF_SHORT[pending["pick"]],
                                      "probability": pending["probability"], "sample": pending["sample"],
                                      "basis": pending["basis"], "actual": pending["actual"],
                                      "actual_label": HALF_SHORT.get(pending["actual"]),
                                      "verdict": pending.get("verdict") or "pending",
                                      "first_half": pending["first_half"], "second_half": pending["second_half"],
                                      "ht": pending["ht"], "ft": pending["ft"],
                                      "source": (sheets.get(day) or {}).get("source"),
                                      "locked_at": (sheets.get(day) or {}).get("locked_at"),
                                      "lead_seconds": (sheets.get(day) or {}).get("lead_seconds")})
                    continue
                record = {"day": day, "home": fixture["home"], "away": fixture["away"],
                          "venue": "H" if fixture["home"] == team else "A",
                          "opponent": fixture["away"] if fixture["home"] == team else fixture["home"],
                          "ht": f"{fixture['ht_home']}:{fixture['ht_away']}",
                          "ft": f"{fixture['ft_home']}:{fixture['ft_away']}",
                          "first_half": fixture["first_half"], "second_half": fixture["second_half"],
                          "half": fixture["half"]}
                results.append(record)
                pick = picks.get((day, fixture["home"], fixture["away"]))
                if pick:
                    calls.append({"day": day, "opponent": record["opponent"],
                                  "home": fixture["home"], "away": fixture["away"],
                                  "pick": pick["pick"], "label": HALF_SHORT[pick["pick"]],
                                  "probability": pick["probability"], "sample": pick["sample"],
                                  "basis": pick["basis"], "actual": pick["actual"],
                                  "actual_label": HALF_SHORT.get(pick["actual"]),
                                  "verdict": pick.get("verdict") or "pending", "first_half": pick["first_half"],
                                  "second_half": pick["second_half"], "ht": pick["ht"], "ft": pick["ft"],
                                  "source": (sheets.get(day) or {}).get("source"),
                                  "locked_at": (sheets.get(day) or {}).get("locked_at"),
                                  "lead_seconds": (sheets.get(day) or {}).get("lead_seconds")})
            board.append({"team": team, "results": results, "calls": calls})
        played = [row for row in rows]
        graded = [pick for pick in picks.values() if pick["actual"]]
        return {"season": season, "days": days, "teams": board,
                "results": {f"{row['season']}|{row['day']}|{row['home']}|{row['away']}": row for row in played},
                "summary": {"teams": len(teams), "matchdays": len(days), "matches": len(rows),
                            "picks": len(picks), "graded": len(graded), "pending": len(picks) - len(graded),
                            "hits": sum(1 for pick in graded if pick["verdict"] == "hit"),
                            "hit_rate": round(100.0 * sum(1 for pick in graded if pick["verdict"] == "hit") / len(graded), 1)
                            if graded else None},
                "league": self.index.league_rates()}

    @staticmethod
    def _half_counts(value):
        """Ledger picks store the counts as JSON text; the board always publishes them as an object."""
        if isinstance(value, dict):
            return {key: int(value.get(key, 0) or 0) for key in HALVES}
        if isinstance(value, str) and value.strip():
            try:
                return MatchupService._half_counts(json.loads(value))
            except (TypeError, ValueError):
                return {}
        return {}

    def upcoming(self):
        """The matchday to play, with the frozen card that sits directly below it on the board."""
        target = self.target()
        season, day = target["season"], target["day"]
        sheet = self.ledger.sheet(season, day) if season and day else None
        fixtures = []
        for fixture in target["fixtures"]:
            pick = None
            if sheet:
                pick = next((row for row in sheet["picks"]
                             if row["home"] == fixture["home"] and row["away"] == fixture["away"]), None)
            if not pick:
                call = self.prediction_for(fixture["home"], fixture["away"])
                pick = {"pick": call["pick"], "probability": call["probability"], "counts": self._half_counts(call["counts"]),
                        "sample": call["sample"], "basis": call["basis"], "verdict": None, "actual": None}
            fixtures.append({"home": fixture["home"], "away": fixture["away"],
                             "event_id": fixture["event_id"], "kickoff": fixture["start_time"],
                             "status": fixture["status"], "pick": pick["pick"], "label": HALF_SHORT[pick["pick"]],
                             "probability": pick["probability"], "counts": self._half_counts(pick["counts"]),
                             "sample": pick["sample"], "basis": pick["basis"],
                             "verdict": pick.get("verdict"), "actual": pick.get("actual"),
                             "actual_label": HALF_SHORT.get(pick.get("actual")),
                             "frozen": bool(sheet),
                             "history": self.prediction_for(fixture["home"], fixture["away"])["history"][:6]})
        return {"season": season, "day": day, "label": target["label"], "kickoff": target["kickoff"],
                "fixtures": fixtures, "locked": bool(sheet),
                "locked_at": (sheet or {}).get("locked_at"), "lead_seconds": (sheet or {}).get("lead_seconds"),
                "source": (sheet or {}).get("source"), "late": bool((sheet or {}).get("late")),
                "starts_in": round(target["kickoff"] - time.time(), 1) if target["kickoff"] else None}

    def payload(self, season=None):
        target = self.target()
        season = int(season) if season else target["season"]
        board = self.board(season)
        return {"meta": {"version": "1.0.0", "generated_at": time.time(), "season": season,
                         "board_key": self.board_key(season), "protocol": (
                             "A matchday's half call is locked when the matchday is announced and never "
                             "rewritten; grading fills the recorded HT/FT and the half that happened")},
                "season": season, "board": board, "upcoming": self.upcoming(),
                "ledger": {"stats": self.ledger.stats(), "season_stats": self.ledger.stats(season),
                           "seasons": self.ledger.seasons(), "integrity": self.ledger.integrity(),
                           "season_integrity": self.ledger.integrity(season), "version": self.ledger.version(),
                           "sync": {key: value for key, value in self.sync_state.items() if key != "error"},
                           "sync_error": self.sync_state.get("error")},
                "index": {"revision": self.index.revision(), "pairs": self.store.one(
                    "SELECT COUNT(*) n FROM (SELECT DISTINCT home,away FROM matchup_index)")["n"],
                    "matches": self.store.one("SELECT COUNT(*) n FROM matchup_index")["n"]},
                "league": board["league"]}

    def board_key(self, season=None):
        """Everything that should make a reader re-render: new sheets, new gradings, new results."""
        target = self.target()
        return f"{self.ledger.version()}|{self.index.revision()}|{season or target['season']}|{target['day']}"

    def tick(self):
        target = self.target()
        season, day = target["season"], target["day"]
        sheet = self.ledger.sheet(season, day) if season and day else None
        stats = self.ledger.stats()
        return {"board_key": self.board_key(), "season": season, "target": {
                    "label": target["label"], "day": day, "kickoff": target["kickoff"],
                    "starts_in": round(target["kickoff"] - time.time(), 1) if target["kickoff"] else None,
                    "locked_at": (sheet or {}).get("locked_at"), "lead_seconds": (sheet or {}).get("lead_seconds"),
                    "source": (sheet or {}).get("source"), "late": bool((sheet or {}).get("late")),
                    "frozen": bool(sheet)},
                "graded": {"picks": stats["graded"], "hits": stats["hits"], "hit_rate": stats["hit_rate"]},
                "sync": {key: value for key, value in self.sync_state.items() if key != "error"},
                "sync_error": self.sync_state.get("error")}

    def summary(self):
        stats = self.ledger.stats()
        return {"ready": True, "pairs": self.store.one(
            "SELECT COUNT(*) n FROM (SELECT DISTINCT home,away FROM matchup_index)")["n"],
            "matches": self.store.one("SELECT COUNT(*) n FROM matchup_index")["n"],
            "picks": stats["picks"], "graded": stats["graded"], "hits": stats["hits"],
            "hit_rate": stats["hit_rate"], "ledger": self.ledger.version(),
            "sync": {key: value for key, value in self.sync_state.items() if key != "error"}}
