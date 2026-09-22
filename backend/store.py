from __future__ import annotations
import csv
import io
import json
import sqlite3
import threading
import time
import zlib
from pathlib import Path

from .patterns import KINDS, outcome, scan_windows
from .source import COMPETITION, START_SEASON
from .gold import rebuild_score_blueprints

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
-- a collector write burst (a whole matchday landing at once) can hold the database for longer
-- than five seconds on a small host; waiting is always better than failing a foreground read
PRAGMA busy_timeout=15000;
-- The write-ahead log is a real file next to the database and counts against the workspace budget.
-- Fold it back early and often instead of letting it grow to tens of megabytes between checkpoints.
PRAGMA wal_autocheckpoint=256;
PRAGMA journal_size_limit=4194304;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS seasons(id INTEGER PRIMARY KEY, discovered_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS teams(name TEXT PRIMARY KEY, first_seen REAL NOT NULL);
CREATE TABLE IF NOT EXISTS matchdays(
 season INTEGER NOT NULL REFERENCES seasons(id), day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 30),
 status TEXT NOT NULL DEFAULT 'pending', match_count INTEGER DEFAULT 0, final_count INTEGER DEFAULT 0,
 attempts INTEGER DEFAULT 0, error TEXT, last_attempt REAL, next_retry REAL DEFAULT 0,
 source_hash TEXT, PRIMARY KEY(season,day));
CREATE TABLE IF NOT EXISTS season_probe(
 season INTEGER PRIMARY KEY, valid INTEGER NOT NULL, published_days INTEGER NOT NULL DEFAULT 0,
 probed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS season_published(
 season INTEGER NOT NULL, day INTEGER NOT NULL, first_seen REAL NOT NULL, last_seen REAL NOT NULL,
 PRIMARY KEY(season,day));
CREATE TABLE IF NOT EXISTS matches(
 id INTEGER PRIMARY KEY, event_id TEXT, season INTEGER NOT NULL REFERENCES seasons(id),
 day INTEGER NOT NULL CHECK(day BETWEEN 1 AND 30), home TEXT NOT NULL, away TEXT NOT NULL,
 start_time REAL, status TEXT NOT NULL, source_status TEXT, phase TEXT,
 ht_home INTEGER, ht_away INTEGER, ft_home INTEGER, ft_away INTEGER,
 live_ht_home INTEGER, live_ht_away INTEGER, live_ft_home INTEGER, live_ft_away INTEGER,
 match_time TEXT, final_source_hash TEXT, last_source_hash TEXT,
 first_seen REAL NOT NULL, updated_at REAL NOT NULL, final_at REAL,
 UNIQUE(season,day,home,away));
CREATE INDEX IF NOT EXISTS match_season_day ON matches(season,day,status);
CREATE INDEX IF NOT EXISTS match_home ON matches(home,season,day);
CREATE INDEX IF NOT EXISTS match_away ON matches(away,season,day);
CREATE UNIQUE INDEX IF NOT EXISTS match_event ON matches(event_id) WHERE event_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS fingerprints(
 season INTEGER NOT NULL, team TEXT NOT NULL, kind TEXT NOT NULL, signature TEXT NOT NULL,
 cards TEXT NOT NULL, played INTEGER NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(kind,season,team));
CREATE INDEX IF NOT EXISTS fp_search ON fingerprints(kind,season,team,signature);
CREATE TABLE IF NOT EXISTS score_blueprints(
 season INTEGER NOT NULL, team TEXT NOT NULL, home_away TEXT NOT NULL, team_relative TEXT NOT NULL,
 cards TEXT NOT NULL, played INTEGER NOT NULL, updated_at REAL NOT NULL,
 PRIMARY KEY(season,team));
CREATE INDEX IF NOT EXISTS score_blueprint_team ON score_blueprints(team,season);
CREATE TABLE IF NOT EXISTS markets(
 event_id TEXT PRIMARY KEY, data TEXT NOT NULL, full INTEGER NOT NULL DEFAULT 0,
 source_hash TEXT, fetched_at REAL NOT NULL, meta TEXT);
CREATE TABLE IF NOT EXISTS receipts(
 hash TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL, body BLOB NOT NULL,
 first_seen REAL NOT NULL, last_seen REAL NOT NULL, source_date TEXT);
CREATE TABLE IF NOT EXISTS activity(
 id INTEGER PRIMARY KEY, at REAL NOT NULL, category TEXT NOT NULL, level TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS endpoints(route TEXT PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS comparisons(cache_key TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pins(id INTEGER PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS single_hit_rows(
 id INTEGER PRIMARY KEY, current_season INTEGER NOT NULL, current_team TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('all','same')), sequence_no INTEGER NOT NULL,
 historical_season INTEGER NOT NULL, historical_team TEXT NOT NULL, alignment_offset INTEGER NOT NULL,
 current_start INTEGER NOT NULL, current_end INTEGER NOT NULL,
 first_seen REAL NOT NULL, last_seen REAL NOT NULL, observation_count INTEGER NOT NULL DEFAULT 1,
 data TEXT NOT NULL, UNIQUE(current_season,current_team,scope,sequence_no));
CREATE INDEX IF NOT EXISTS single_hit_stream ON single_hit_rows(current_season,current_team,scope,sequence_no);
CREATE TABLE IF NOT EXISTS single_hit_observations(
 observation_key TEXT PRIMARY KEY, row_id INTEGER NOT NULL REFERENCES single_hit_rows(id),
 observed_at REAL NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS single_hit_evidence(
 row_id INTEGER NOT NULL REFERENCES single_hit_rows(id), source_hash TEXT NOT NULL,
 PRIMARY KEY(row_id,source_hash));
CREATE INDEX IF NOT EXISTS single_hit_evidence_hash ON single_hit_evidence(source_hash);
PRAGMA user_version=3;
"""


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.competition = COMPETITION
        self.start_season = START_SEASON
        self.version = int(self.get_meta("version", 0))
        self.fp_revision = int(self.get_meta("fp_revision", 0))
        self.memory_cache = {}
        self.last_prune = 0
        self.migrate_correct_scores()

    def migrate_correct_scores(self):
        """Populate new materialized views from the real existing archive once."""
        if self.get_meta("correct_score_blueprint_version") == 1:
            return
        with self.lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                seasons = self.all("SELECT DISTINCT season FROM matches WHERE status='final'")
                for row in seasons:
                    rebuild_score_blueprints(self, row["season"])
                self.set_meta("correct_score_blueprint_version", 1)
                if seasons:
                    self.bump(fingerprint=True)
                    self.log("gold", f"Correct-score blueprints indexed from {len(seasons)} stored seasons")
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def all(self, query, args=()):
        with self.lock:
            return [dict(r) for r in self.db.execute(query, args).fetchall()]

    def one(self, query, args=()):
        with self.lock:
            row = self.db.execute(query, args).fetchone()
            return dict(row) if row else None

    def get_meta(self, key, default=None):
        row = self.one("SELECT value FROM meta WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_meta(self, key, value):
        with self.lock:
            self.db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, dumps(value)))

    def bump(self, fingerprint=False):
        self.version += 1
        self.set_meta("version", self.version)
        if fingerprint:
            self.fp_revision += 1
            self.set_meta("fp_revision", self.fp_revision)
            self.memory_cache.clear()
            self.db.execute("DELETE FROM comparisons")

    def log(self, category, message, level="info"):
        with self.lock:
            previous = self.one("SELECT message,at FROM activity ORDER BY id DESC LIMIT 1")
            if previous and previous["message"] == message and time.time() - previous["at"] < 60:
                return
            self.db.execute("INSERT INTO activity(at,category,level,message) VALUES(?,?,?,?)", (time.time(), category, level, message))
            self.db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 200)")

    def receipt(self, digest, url, kind, raw, stamp, source_date):
        with self.lock:
            self.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?) ON CONFLICT(hash) DO UPDATE SET last_seen=excluded.last_seen",
                            (digest, url, kind, zlib.compress(raw.encode(), 6), stamp, stamp, source_date))
            if stamp - self.last_prune > 600:
                # Keep all result evidence. Bound high-frequency live evidence, but
                # never remove a receipt referenced by a stored match or market.
                self.db.execute("""DELETE FROM receipts WHERE kind IN ('live','upcoming')
                 AND hash NOT IN (SELECT hash FROM receipts WHERE kind IN ('live','upcoming') ORDER BY last_seen DESC LIMIT 2000)
                 AND hash NOT IN (SELECT final_source_hash FROM matches WHERE final_source_hash IS NOT NULL)
                 AND hash NOT IN (SELECT last_source_hash FROM matches WHERE last_source_hash IS NOT NULL)
                 AND hash NOT IN (SELECT source_hash FROM markets WHERE source_hash IS NOT NULL)
                 AND hash NOT IN (SELECT source_hash FROM single_hit_evidence)""")
                self.last_prune = stamp

    def endpoint(self, route, value):
        with self.lock:
            self.db.execute("INSERT INTO endpoints VALUES(?,?) ON CONFLICT(route) DO UPDATE SET data=excluded.data", (route, dumps(value)))

    def source_time(self, route):
        row = self.one("SELECT data FROM endpoints WHERE route=?", (route,))
        return json.loads(row["data"]).get("source_at") if row else None

    def discover(self, seasons):
        with self.lock:
            for season in sorted(set(int(s) for s in seasons if int(s) >= START_SEASON)):
                cur = self.db.execute("INSERT OR IGNORE INTO seasons VALUES(?,?)", (season, time.time()))
                if cur.rowcount:
                    self.db.executemany("INSERT OR IGNORE INTO matchdays(season,day) VALUES(?,?)", [(season, d) for d in range(1, 31)])
                    self.log("discovery", f"Season {season} discovered · 30 matchdays queued")
                    self.bump()

    def ingest(self, rows, digest, stamp):
        """Idempotent upserts; provisional observations never overwrite final scores."""
        if not rows:
            return False
        with self.lock:
            self.discover([r["season"] for r in rows])
            changed_seasons, rounds = set(), set()
            self.db.execute("BEGIN IMMEDIATE")
            try:
                for item in rows:
                    season, day = item["season"], item["day"]
                    rounds.add((season, day))
                    for name in (item["home"], item["away"]):
                        self.db.execute("INSERT OR IGNORE INTO teams VALUES(?,?)", (name, stamp))
                    key = (season, day, item["home"], item["away"])
                    old = self.one("SELECT * FROM matches WHERE season=? AND day=? AND home=? AND away=?", key)
                    is_final = item["status"] == "final"
                    ht, ft = item["ht"] or (None, None), item["ft"] or (None, None)
                    if is_final and (not old or old["status"] != "final" or (old["ht_home"], old["ht_away"], old["ft_home"], old["ft_away"]) != (*ht, *ft)):
                        changed_seasons.add(season)
                    if old and ((old["status"] == "final" and not is_final) or (old["status"] == "live" and item["status"] == "scheduled")):
                        if item["event_id"]:
                            self.db.execute("UPDATE matches SET event_id=COALESCE(event_id,?),start_time=COALESCE(start_time,?) WHERE id=?",
                                            (item["event_id"], item["start_time"], old["id"]))
                        continue
                    lht, lft = item["live_ht"] or (None, None), item["live_ft"] or (None, None)
                    # Final scores only change when a new validated final observation arrives.
                    values = (item["event_id"], season, day, item["home"], item["away"], item["start_time"],
                              item["status"], item["source_status"], item["phase"], *ht, *ft, *lht, *lft,
                              item["match_time"], digest if is_final else None, digest, stamp, stamp,
                              (old.get("final_at") if old else None) or (stamp if is_final else None))
                    self.db.execute("""INSERT INTO matches(event_id,season,day,home,away,start_time,status,source_status,phase,
                     ht_home,ht_away,ft_home,ft_away,live_ht_home,live_ht_away,live_ft_home,live_ft_away,
                     match_time,final_source_hash,last_source_hash,first_seen,updated_at,final_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                     ON CONFLICT(season,day,home,away) DO UPDATE SET
                     event_id=COALESCE(excluded.event_id,matches.event_id),start_time=COALESCE(excluded.start_time,matches.start_time),
                     status=excluded.status,source_status=excluded.source_status,phase=excluded.phase,
                     ht_home=excluded.ht_home,ht_away=excluded.ht_away,ft_home=excluded.ft_home,ft_away=excluded.ft_away,
                     live_ht_home=excluded.live_ht_home,live_ht_away=excluded.live_ht_away,
                     live_ft_home=excluded.live_ft_home,live_ft_away=excluded.live_ft_away,match_time=excluded.match_time,
                     final_source_hash=COALESCE(excluded.final_source_hash,matches.final_source_hash),
                     last_source_hash=excluded.last_source_hash,updated_at=excluded.updated_at,final_at=excluded.final_at""", values)
                for season, day in rounds:
                    before = self.one("SELECT status FROM matchdays WHERE season=? AND day=?", (season, day))
                    counts = self.one("SELECT COUNT(*) n,SUM(status='final') f FROM matches WHERE season=? AND day=?", (season, day))
                    teams = self.one("SELECT COUNT(DISTINCT name) n FROM (SELECT home name FROM matches WHERE season=? AND day=? UNION SELECT away name FROM matches WHERE season=? AND day=?)", (season, day, season, day))
                    state = "complete" if counts["n"] == 8 and counts["f"] == 8 and teams["n"] == 16 else "partial"
                    self.db.execute("UPDATE matchdays SET status=?,match_count=?,final_count=?,source_hash=?,error=NULL WHERE season=? AND day=?",
                                    (state, counts["n"], counts["f"] or 0, digest, season, day))
                    if state == "complete" and before and before["status"] != "complete":
                        self.log("results", f"S{season} · Matchday {day} closed · 8 HT/FT results verified")
                for season in changed_seasons:
                    self.rebuild_fingerprints(season)
                self.bump(fingerprint=bool(changed_seasons))
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise
            return bool(changed_seasons)

    def rebuild_fingerprints(self, season):
        matches = self.all("SELECT * FROM matches WHERE season=? AND status='final' ORDER BY day", (season,))
        teams = sorted({name for m in matches for name in (m["home"], m["away"])})
        for team in teams:
            relevant = {m["day"]: m for m in matches if team in (m["home"], m["away"])}
            for kind in KINDS:
                cards, sig = [], ""
                for day in range(1, 31):
                    m = relevant.get(day)
                    if m is None:
                        cards.append({"day": day, "code": None, "label": "—", "ft": None})
                        sig += "."
                        continue
                    home = m["home"] == team
                    code = outcome(kind, m["ft_home"], m["ft_away"], home)
                    cards.append({"day": day, "code": code, "label": KINDS[kind]["labels"][code],
                                  "ft": f'{m["ft_home"]}:{m["ft_away"]}', "ht": f'{m["ht_home"]}:{m["ht_away"]}',
                                  "opponent": m["away"] if home else m["home"], "venue": "H" if home else "A",
                                  "home": m["home"], "away": m["away"], "match_id": m["id"]})
                    sig += code
                self.db.execute("INSERT INTO fingerprints VALUES(?,?,?,?,?,?,?) ON CONFLICT(kind,season,team) DO UPDATE SET signature=excluded.signature,cards=excluded.cards,played=excluded.played,updated_at=excluded.updated_at",
                                (season, team, kind, sig, dumps(cards), len(relevant), time.time()))
        rebuild_score_blueprints(self, season, matches)

    def save_markets(self, event_id, markets, digest, stamp, full=False, meta=None):
        with self.lock:
            old = self.one("SELECT full FROM markets WHERE event_id=?", (event_id,))
            if old and old["full"] and not full:
                return
            self.db.execute("INSERT INTO markets VALUES(?,?,?,?,?,?) ON CONFLICT(event_id) DO UPDATE SET data=excluded.data,full=excluded.full,source_hash=excluded.source_hash,fetched_at=excluded.fetched_at,meta=excluded.meta",
                            (event_id, dumps(markets), int(full), digest, stamp, dumps(meta or {})))
            self.bump()

    def market(self, event_id):
        row = self.one("SELECT * FROM markets WHERE event_id=?", (event_id,))
        if row:
            row["markets"] = json.loads(row.pop("data"))
            row["meta"] = json.loads(row["meta"])
            row["fixture"] = self.one("SELECT * FROM matches WHERE event_id=?", (event_id,))
        return row

    def attempt(self, season, day, error=None):
        with self.lock:
            self.db.execute("UPDATE matchdays SET attempts=attempts+1,last_attempt=?,error=?,next_retry=? WHERE season=? AND day=?",
                            (time.time(), error, time.time() + (180 if error else 40), season, day))
            self.bump()

    def record_published(self, season, days):
        """Remember which matchdays the source says it has results for.

        This is the only trustworthy statement of what exists: asking for an unpublished matchday
        is clamped to the season's last published day and answered with that day's finals, so a
        request that returns rows proves nothing about the day that was asked for.
        """
        days = sorted({int(day) for day in days if 1 <= int(day) <= 30})
        if not days:
            return
        now = time.time()
        with self.lock:
            self.db.executemany(
                "INSERT INTO season_published(season,day,first_seen,last_seen) VALUES(?,?,?,?) "
                "ON CONFLICT(season,day) DO UPDATE SET last_seen=excluded.last_seen",
                [(int(season), day, now, now) for day in days])
            self.db.execute("INSERT OR IGNORE INTO seasons VALUES(?,?)", (int(season), now))

    def record_probe(self, season, valid, published_days=0):
        """Remember every id the scan has asked about, so no id is ever probed twice."""
        with self.lock:
            self.db.execute("INSERT INTO season_probe VALUES(?,?,?,?) ON CONFLICT(season) DO UPDATE SET "
                            "valid=excluded.valid,published_days=excluded.published_days,probed_at=excluded.probed_at",
                            (int(season), int(bool(valid)), int(published_days), time.time()))

    def has_probe(self, season):
        return self.one("SELECT 1 x FROM season_probe WHERE season=?", (int(season),)) is not None

    def probe_totals(self):
        row = self.one("SELECT COUNT(*) checked,SUM(valid) hits FROM season_probe")
        return (row["checked"] or 0, row["hits"] or 0)

    def published_days(self, season):
        return [row["day"] for row in self.all("SELECT day FROM season_published WHERE season=? ORDER BY day", (season,))]

    def published_coverage(self):
        """Per season: what the source published, what is stored, and what is still outstanding."""
        rows = self.all("""SELECT s.id season,
             (SELECT COUNT(*) FROM season_published p WHERE p.season=s.id) published,
             (SELECT COUNT(*) FROM matchdays m WHERE m.season=s.id AND m.status='complete') complete_days,
             (SELECT COUNT(*) FROM matchdays m WHERE m.season=s.id AND m.status='partial') partial_days,
             (SELECT COUNT(*) FROM matches x WHERE x.season=s.id AND x.status='final') final_matches,
             (SELECT MAX(last_seen) FROM season_published p WHERE p.season=s.id) published_at
           FROM seasons s ORDER BY s.id DESC""")
        for row in rows:
            outstanding = self.all("""SELECT COUNT(*) n FROM matchdays m WHERE m.season=? AND m.status!='complete'
                 AND (m.day <= ? OR EXISTS(SELECT 1 FROM season_published p WHERE p.season=m.season AND p.day=m.day))""",
                 (row["season"], row["published"] or 0))[0]["n"]
            row["outstanding_days"] = outstanding
            row["missing_days"] = max(0, (row["published"] or 0) - (row["complete_days"] or 0))
            row["complete"] = bool(row["published"]) and row["missing_days"] == 0 and not row["partial_days"]
        return rows

    def queue_state(self):
        """How much history work is outstanding, split by why it is outstanding."""
        unpublished = self.all("""SELECT m.season,m.day,m.status FROM matchdays m
             WHERE m.status!='complete' ORDER BY m.season DESC,m.day ASC""")
        published_now = self.all("SELECT season,day FROM season_published")
        known = {(row["season"], row["day"]) for row in published_now}
        ready = [row for row in unpublished if (row["season"], row["day"]) in known]
        awaiting = [row for row in unpublished if (row["season"], row["day"]) not in known]
        return {"outstanding": len(unpublished), "published_unfilled": len(ready),
                "awaiting_publication": len(awaiting),
                "next": ({"season": ready[0]["season"], "day": ready[0]["day"]} if ready else None),
                "seasons_tracked": self.one("SELECT COUNT(*) n FROM seasons")["n"],
                "days_published": len(known)}

    def prefix_length(self, season):
        completed = {r["day"] for r in self.all("SELECT day FROM matchdays WHERE season=? AND status='complete'", (season,))}
        day = 0
        while day + 1 in completed:
            day += 1
        return day

    def season_summary(self):
        current = self.get_meta("upcoming", {}).get("season")
        results = self.all("""SELECT s.id,s.discovered_at,COUNT(d.day) total_days,
          SUM(d.status='complete') complete_days,SUM(d.match_count) match_count,
          SUM(d.final_count) final_count,SUM(d.error IS NOT NULL) errors
          FROM seasons s LEFT JOIN matchdays d ON d.season=s.id GROUP BY s.id ORDER BY s.id DESC""")
        for row in results:
            row["current"] = row["id"] == current
            row["coverage"] = round((row["complete_days"] or 0) / 30 * 100, 1)
            row["days"] = self.all("SELECT day,status,match_count,final_count,error,attempts,last_attempt FROM matchdays WHERE season=? ORDER BY day", (row["id"],))
        # What the source says it published, per season, so a coverage table can tell "not played
        # yet" apart from "published on the website and missing here" — the reported fault.
        coverage = {entry["season"]: entry for entry in self.published_coverage()}
        for row in results:
            entry = coverage.get(row["id"]) or {}
            row["published_days"] = entry.get("published") or 0
            row["missing_days"] = entry.get("missing_days") or 0
            row["outstanding_days"] = entry.get("outstanding_days") or 0
            row["checked_at"] = entry.get("published_at")
            row["source_complete"] = bool(entry.get("complete"))
        return results

    def fingerprints(self, season, kind):
        rows = self.all("SELECT * FROM fingerprints WHERE season=? AND kind=? ORDER BY team", (season, kind))
        for r in rows:
            r["cards"] = json.loads(r["cards"])
        return {"season": season, "kind": kind, "definition": KINDS[kind], "teams": rows, "revision": self.fp_revision}

    def overview(self):
        began = time.perf_counter()
        with self.lock:
            upcoming = self.get_meta("upcoming", {})
            ongoing = self.get_meta("ongoing", {})
            fixture_ids = upcoming.get("event_ids", [])
            fixtures = []
            for eid in fixture_ids:
                match = self.one("SELECT * FROM matches WHERE event_id=?", (eid,))
                if not match:
                    continue
                market = self.market(eid)
                catalogue = market["markets"] if market else []
                # Keep the frequently refreshed overview light. The complete
                # locally stored catalogue is read on demand by the market drawer.
                match["markets"] = [m for m in catalogue if m["name"].upper() in
                                    {"1X2", "ODD/EVEN", "BOTH TEAMS TO SCORE", "DRAW NO BET", "TOTAL"}]
                match["markets_full"] = bool(market and market["full"])
                match["odds_at"] = market["fetched_at"] if market else None
                match["market_count"] = len(catalogue)
                fixtures.append(match)
            live_rows = []
            if ongoing.get("season"):
                live_rows = self.all("SELECT * FROM matches WHERE season=? AND day=? ORDER BY id", (ongoing["season"], ongoing["day"]))
            counts = self.one("SELECT COUNT(*) matches,SUM(status='final') finalized FROM matches")
            counts["teams"] = self.one("SELECT COUNT(*) n FROM teams")["n"]
            counts["fingerprints"] = self.one("SELECT COUNT(*) n FROM fingerprints")["n"]
            counts["score_blueprints"] = self.one("SELECT COUNT(*) n FROM score_blueprints")["n"]
            counts["seasons"] = self.one("SELECT COUNT(*) n FROM seasons")["n"]
            counts["complete_seasons"] = self.one("SELECT COUNT(*) n FROM (SELECT season FROM matchdays WHERE status='complete' GROUP BY season HAVING COUNT(*)=30)")["n"]
            counts["complete_days"] = self.one("SELECT COUNT(*) n FROM matchdays WHERE status='complete'")["n"]
            counts["failed_days"] = self.one("SELECT COUNT(*) n FROM matchdays WHERE error IS NOT NULL")["n"]
            counts["receipts"] = self.one("SELECT COUNT(*) n FROM receipts")["n"]
            current = upcoming.get("season") or ongoing.get("season")
            prefix = self.prefix_length(current) if current else 0
            last_upcoming = upcoming.get("fetched_at", 0)
            source_age = max(0, time.time() - last_upcoming, time.time() - (upcoming.get("source_at") or last_upcoming)) if last_upcoming else None
            live_age = max(0, time.time() - ongoing["fetched_at"], time.time() - (ongoing.get("source_at") or ongoing["fetched_at"])) if ongoing.get("fetched_at") else None
            out = {"version": self.version, "fp_revision": self.fp_revision, "server_time": time.time(),
                   "source": {"name": "Betika League", "api_name": "Betika Sakata League", "competition_id": 26,
                              "domain": "virtuals.betika.com", "start_season": START_SEASON, "timezone": "Africa/Nairobi",
                              "age_seconds": source_age, "live_age_seconds": live_age,
                              "stale": source_age is None or source_age > 35},
                   "counts": counts, "upcoming": {**upcoming, "fixtures": fixtures},
                   "ongoing": {**ongoing, "matches": live_rows}, "current_season": current,
                   "completed_prefix": prefix, "seasons": self.season_summary(),
                   "teams": [r["name"] for r in self.all("SELECT name FROM teams ORDER BY name")],
                   "activity": self.all("SELECT * FROM activity ORDER BY id DESC LIMIT 15"),
                   "backfill": self.get_meta("backfill", {}), "paused": self.get_meta("paused", False),
                   "endpoints": {r["route"]: json.loads(r["data"]) for r in self.all("SELECT * FROM endpoints")}}
            out["read_ms"] = round((time.perf_counter() - began) * 1000, 2)
            return out

    def compare(self, kind, minimum=100.0, scope="all"):
        """Historical similarity, never predictive confidence. All upcoming teams."""
        began = time.perf_counter()
        with self.lock:
            up = self.get_meta("upcoming", {})
            current = up.get("season")
            n = self.prefix_length(current) if current else 0
            n = min(n, max(0, up.get("day", 1) - 1))
            teams = up.get("teams", [])
            key = dumps([current, up.get("day"), n, kind, minimum, scope, teams])
            if key in self.memory_cache:
                return {**self.memory_cache[key], "cached": True, "compute_ms": round((time.perf_counter() - began) * 1000, 2)}
            disk = self.one("SELECT data FROM comparisons WHERE cache_key=? AND revision=?", (key, self.fp_revision))
            if disk:
                data = json.loads(disk["data"])
                self.memory_cache[key] = data
                return {**data, "cached": True, "compute_ms": round((time.perf_counter() - began) * 1000, 2)}
            historical = self.all("SELECT * FROM fingerprints WHERE kind=? AND season<? ORDER BY season DESC,team", (kind, current or 0))
            for row in historical:
                row["cards"] = json.loads(row["cards"])
            forming = {r["team"]: r for r in self.all("SELECT * FROM fingerprints WHERE kind=? AND season=?", (kind, current or 0))}
            total_windows, total_matches, exact_total, result = 0, 0, 0, []
            for team in teams:
                prefix_row = forming.get(team)
                prefix = prefix_row["signature"][:n] if prefix_row and n else ""
                current_cards = json.loads(prefix_row["cards"])[:n] if prefix_row and n else []
                candidates, scanned = [], 0
                if prefix and "." not in prefix:
                    for historical_row in historical:
                        if scope == "same" and historical_row["team"] != team:
                            continue
                        found, count = scan_windows(prefix, historical_row["signature"], minimum)
                        scanned += count
                        for alignment in found:
                            candidates.append({**alignment, "team": historical_row["team"], "season": historical_row["season"],
                                               "cards": historical_row["cards"]})
                candidates.sort(key=lambda c: (-c["similarity"], -c["season"], c["start"], c["team"]))
                exact = sum(c["exact"] for c in candidates)
                total_windows += scanned
                total_matches += len(candidates)
                exact_total += exact
                result.append({"team": team, "prefix": prefix, "cards": current_cards, "length": len(prefix),
                               "windows_scanned": scanned, "match_count": len(candidates), "exact_count": exact,
                               "candidates": candidates[:20]})
            data = {"kind": kind, "minimum": minimum, "scope": scope, "current_season": current,
                    "upcoming_day": up.get("day"), "length": n, "teams": result,
                    "windows_scanned": total_windows, "match_count": total_matches, "exact_count": exact_total,
                    "historical_blueprints": len(historical), "revision": self.fp_revision,
                    "computed_at": time.time(), "compute_ms": round((time.perf_counter() - began) * 1000, 2),
                    "cached": False, "notice": "Similarity describes past sequences, not the probability of any future outcome."}
            self.memory_cache[key] = data
            if len(self.memory_cache) > 24:
                self.memory_cache.pop(next(iter(self.memory_cache)))
            self.db.execute("INSERT OR REPLACE INTO comparisons VALUES(?,?,?,?)", (key, self.fp_revision, dumps(data), time.time()))
            self.db.execute("DELETE FROM comparisons WHERE cache_key NOT IN (SELECT cache_key FROM comparisons ORDER BY created_at DESC LIMIT 24)")
            return data

    def results(self, season=None, day=None, team=None):
        query, args = "SELECT * FROM matches WHERE 1=1", []
        if season is not None:
            query += " AND season=?"
            args.append(season)
        if day is not None:
            query += " AND day=?"
            args.append(day)
        if team:
            query += " AND (home=? OR away=?)"
            args += [team, team]
        return self.all(query + " ORDER BY season DESC,day ASC,id ASC", args)

    def export_csv(self, season=None):
        rows = self.results(season)
        fields = ["season", "day", "event_id", "home", "away", "status", "ht_home", "ht_away", "ft_home", "ft_away", "live_ht_home", "live_ht_away", "live_ft_home", "live_ft_away", "start_time", "updated_at", "final_source_hash"]
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Prevent spreadsheet formula injection from an upstream team name.
            for field in ("home", "away"):
                if row[field].startswith(("=", "+", "-", "@")):
                    row[field] = "'" + row[field]
            writer.writerow(row)
        return buf.getvalue()

    def backup(self, destination):
        with self.lock:
            target = sqlite3.connect(str(destination))
            try:
                self.db.backup(target)
            finally:
                target.close()

    def checkpoint(self, truncate=True):
        """Fold the write-ahead log back into the database and, optionally, truncate it.

        A long collector run grows `league.sqlite-wal` without bound until something checkpoints
        it, and that side file counts against a workspace budget just like the database does.
        The app calls this on a slow timer; `scripts/compact_databases.py` calls it offline.
        """
        mode = "TRUNCATE" if truncate else "PASSIVE"
        with self.lock:
            try:
                return self.db.execute(f"PRAGMA wal_checkpoint({mode})").fetchone()
            except sqlite3.Error:
                return None

    def close(self):
        self.checkpoint()
        with self.lock:
            self.db.close()
