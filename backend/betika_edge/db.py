"""
db.py — Betika Virtual Edge Analyzer storage.

This module is the *integration layer* between the analyzer and the
League-DNA v2.9.0 archive.  It does two jobs:

  1. **Read access** to the live collector's `data/league.sqlite`
     (teams, matches).  When that file is missing, the helper functions
     fall back to the bundled JSON fixtures under `tests/fixtures/` so
     the analyzer always has *something* to work with — exactly the
     same fallback the rest of the project uses.

  2. **Write access** to a small companion `data/edge.sqlite`.  This
     holds the four tables the analyzer introduces
     (`edge_markets_cache`, `edge_h2h`, `edge_ledger`,
     `edge_settings`) and is owned entirely by the analyzer — it never
     touches the live archive schema, never bumps the collector's
     `user_version`, and never fights the WAL log.

The module exposes the exact same public surface as before
(`list_teams`, `list_matches`, `list_markets_for_match`, etc.) so the
rest of the analyzer — `h2h.py`, `markets.py`, `poisson_model.py`,
`ledger.py`, the Streamlit pages — keeps working unchanged.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

# ----------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent.parent       # League-DNA/
PKG_DIR = Path(__file__).resolve().parent                   # backend/betika_edge/
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "betika_analyzer" / "cache"
CSV_DIR = DATA_DIR / "betika_edge_csv"
for d in (DATA_DIR, CACHE_DIR, CSV_DIR):
    d.mkdir(parents=True, exist_ok=True)

# The live archive the collector writes to.  Honour the same env var
# the FastAPI app does, so the analyzer stays in step with a custom
# data dir the user picks.
LIVE_DB = Path(os.environ.get(
    "LEAGUE_DATA_DIR",
    str(ROOT / "data"),
)) / "league.sqlite"

# Bundled fixture files used when the live archive is missing.
FIXTURES = ROOT / "tests" / "fixtures"

# Edge-only database — entirely owned by this module.
EDGE_DB = DATA_DIR / "edge.sqlite"

logger = logging.getLogger("betika.db")

# ----------------------------------------------------------------------
# Edge-only schema
# ----------------------------------------------------------------------
EDGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS edge_markets_cache (
    event_id    TEXT PRIMARY KEY,
    season      INTEGER,
    matchday    INTEGER,
    home_team   TEXT,
    away_team   TEXT,
    markets     TEXT NOT NULL,        -- JSON: list of {name, id, odds:[{label,value,specifier,outcome_id}]}
    full        INTEGER DEFAULT 0,    -- 1 = full market catalogue, 0 = upcoming partial
    fetched_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS emc_season_md ON edge_markets_cache(season, matchday);

CREATE TABLE IF NOT EXISTS edge_h2h (
    pair_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    home_team   TEXT NOT NULL,
    away_team   TEXT NOT NULL,
    season      INTEGER NOT NULL,
    matchday    INTEGER NOT NULL,
    ht_home     INTEGER,
    ht_away     INTEGER,
    ft_home     INTEGER,
    ft_away     INTEGER,
    UNIQUE (home_team, away_team, season, matchday)
);
CREATE INDEX IF NOT EXISTS eh_pair ON edge_h2h(home_team, away_team);

CREATE TABLE IF NOT EXISTS edge_ledger (
    bet_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id         TEXT NOT NULL,         -- "S<season>-MD<day>-<home>-<away>" or event_id
    season           INTEGER,
    matchday         INTEGER,
    market_a_label   TEXT,
    market_a_odds    REAL,
    market_a_stake   REAL,
    market_a_result  TEXT,                  -- WIN / LOSS / PUSH / NULL (pending)
    market_b_label   TEXT,
    market_b_odds    REAL,
    market_b_stake   REAL,
    market_b_result  TEXT,
    profit_or_loss   REAL,
    notes            TEXT,
    placed_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS el_match ON edge_ledger(match_id);

CREATE TABLE IF NOT EXISTS edge_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


# ----------------------------------------------------------------------
# Connection helpers — two stores
# ----------------------------------------------------------------------
def _conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(path))
    c.row_factory = sqlite3.Row
    return c


@contextmanager
def edge_conn() -> Iterator[sqlite3.Connection]:
    c = _conn(EDGE_DB)
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_edge() -> None:
    """Create edge-only tables and seed defaults.  Always safe to call."""
    with edge_conn() as c:
        c.executescript(EDGE_SCHEMA)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute(
                "INSERT OR IGNORE INTO edge_settings(key, value) VALUES (?,?)",
                (k, v),
            )


# ----------------------------------------------------------------------
# Default settings (mirrors v2.9.0 behaviour)
# ----------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "min_edge": "0.05",
    "kelly_fraction": "0.25",
    "h2h_both_gt_05": "0.60",
    "h2h_min_meetings": "3",
    "stake": "100",
    "bankroll": "10000",
    "min_odds": "2.00",
    "cross_market_threshold": "0.03",
    "current_season": "3138368",
}


# ----------------------------------------------------------------------
# Read-only bridge to the live archive
# ----------------------------------------------------------------------
def live_available() -> bool:
    """True if the collector's `data/league.sqlite` exists and has matches."""
    if not LIVE_DB.exists():
        return False
    try:
        with _conn(LIVE_DB) as c:
            n = c.execute(
                "SELECT COUNT(*) AS n FROM matches WHERE status IN ('final','live')"
            ).fetchone()
            return int(n["n"]) > 0
    except Exception:
        return False


def _bridge_table(table: str, season: Optional[int] = None,
                   day: Optional[int] = None) -> list[sqlite3.Row]:
    """Read directly from `league.sqlite`.  No caching — the live
    collector writes continuously so callers must always re-query."""
    if not LIVE_DB.exists():
        return []
    sql = (
        "SELECT season, day, home, away, "
        "COALESCE(ht_home, live_ht_home) AS ht_home, "
        "COALESCE(ht_away, live_ht_away) AS ht_away, "
        "COALESCE(ft_home, live_ft_home) AS ft_home, "
        "COALESCE(ft_away, live_ft_away) AS ft_away, "
        "status, phase, start_time, event_id, match_time "
        "FROM matches WHERE 1=1 "
    )
    args: list[Any] = []
    if season is not None:
        sql += "AND season = ? "
        args.append(season)
    if day is not None:
        sql += "AND day = ? "
        args.append(day)
    sql += "ORDER BY season DESC, day ASC, id ASC"
    try:
        with _conn(LIVE_DB) as c:
            return list(c.execute(sql, args).fetchall())
    except Exception:
        return []


def bridge_teams() -> list[sqlite3.Row]:
    """All teams the live collector has ever seen."""
    if not LIVE_DB.exists():
        return []
    try:
        with _conn(LIVE_DB) as c:
            return list(c.execute("SELECT name FROM teams ORDER BY name").fetchall())
    except Exception:
        return []


def bridge_seasons() -> list[int]:
    if not LIVE_DB.exists():
        return []
    try:
        with _conn(LIVE_DB) as c:
            return [int(r["id"]) for r in c.execute(
                "SELECT id FROM seasons ORDER BY id DESC"
            ).fetchall()]
    except Exception:
        return []


# ----------------------------------------------------------------------
# Fixture fallback — bundled JSON files so the analyzer always has data
# ----------------------------------------------------------------------
def _load_json(name: str) -> Optional[dict]:
    p = FIXTURES / name
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


class _DictRow(dict):
    """dict that behaves like sqlite3.Row for read-only access."""

    def __getitem__(self, key):
        return dict.__getitem__(self, str(key))

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            return None


def _iter_fixture_results(season=None, matchday=None):
    """Yield final results from every bundled `*results*.json` fixture.

    Optional `season` / `matchday` filters narrow the rows.
    """
    for name in ("historical-results.json", "provisional-results.json"):
        payload = _load_json(name)
        if not payload:
            continue
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            continue
        season = int(data.get("query", {}).get("season") or 0)
        day = int(data.get("query", {}).get("matchday") or 0)
        for row in data.get("results") or []:
            ht = row.get("saved_ht_score") or row.get("ht_score") or ""
            ft = row.get("saved_ft_score") or row.get("ft_score") or ""
            try:
                ht_h, ht_a = [int(x) for x in ht.split(":")]
                ft_h, ft_a = [int(x) for x in ft.split(":")]
            except Exception:
                continue
            yield _DictRow({
                "season": season, "day": day,
                "home": row.get("home_team") or "",
                "away": row.get("away_team") or "",
                "ht_home": ht_h, "ht_away": ht_a,
                "ft_home": ft_h, "ft_away": ft_a,
                "status": "final",
                "phase": "ended",
                "event_id": str(row.get("parent_virtual_id") or ""),
                "start_time": row.get("start_time"),
                "match_time": None,
            })


def _iter_fixture_upcoming(season=None, matchday=None):
    """Yield upcoming fixtures with their markets (no FT score yet)."""
    payload = _load_json("upcoming.json")
    if not payload:
        return
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return
    for group_key, rows in data.items():
        try:
            day = int(group_key)
        except ValueError:
            continue
        if matchday is not None and day != int(matchday):
            continue
        for row in rows:
            yield _DictRow({
                "season": int(row.get("season") or 0),
                "day": day,
                "home": row.get("home_team") or "",
                "away": row.get("away_team") or "",
                "ht_home": None, "ht_away": None,
                "ft_home": None, "ft_away": None,
                "status": "upcoming",
                "phase": "",
                "event_id": str(row.get("parent_virtual_id") or ""),
                "start_time": row.get("start_time"),
                "match_time": None,
            })


def _iter_fixture_markets() -> list[dict]:
    """Yield per-event market catalogues from the bundled markets fixture
    AND enrich each row with (season, matchday, home, away) looked up
    from the upcoming.json fixture."""
    payload = _load_json("markets.json")
    if not payload:
        return []

    # 1. Build event_id -> (season, matchday, home, away) from upcoming.json
    meta: dict[str, dict] = {}
    up = _load_json("upcoming.json")
    if up and isinstance(up.get("data"), dict):
        for group_key, rows in up["data"].items():
            try:
                day = int(group_key)
            except ValueError:
                continue
            for r in rows or []:
                eid = str(r.get("parent_virtual_id") or "")
                if eid:
                    meta[eid] = {
                        "season": int(r.get("season") or 0) or None,
                        "matchday": day,
                        "home_team": r.get("home_team") or "",
                        "away_team": r.get("away_team") or "",
                    }

    out: list[dict] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(data, list):
        for row in data:
            eid = str(row.get("parent_virtual_id") or row.get("event_id") or "")
            odds = row.get("odds") or row.get("markets") or []
            m = meta.get(eid, {})
            out.append({
                "event_id": eid,
                "market_name": row.get("name") or "",
                "sub_type_id": row.get("sub_type_id"),
                "home_team": m.get("home_team") or row.get("home_team"),
                "away_team": m.get("away_team") or row.get("away_team"),
                "season": m.get("season") or (int(row["season"]) if row.get("season") else None),
                "matchday": m.get("matchday") or (int(row["match_day"]) if row.get("match_day") else None),
                "odds": odds,
                "special_bet_value": row.get("special_bet_value"),
                "outcome_id": row.get("outcome_id"),
            })
    return out


# ----------------------------------------------------------------------
# Unified read API — DB-shaped rows, no matter the source
# ----------------------------------------------------------------------
@dataclass
class MatchRow:
    match_id: str
    season: int
    matchday: int
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    ht_home: Optional[int]
    ht_away: Optional[int]
    ft_home: Optional[int]
    ft_away: Optional[int]
    status: str
    start_time: Optional[str]
    source: str


@dataclass
class MarketRow:
    market_id: int
    match_id: str
    market_name: str
    sub_type_id: Optional[int]
    special_bet_value: Optional[str]
    outcome_label: str
    decimal_odds: float
    outcome_id: Optional[str]
    fetched_at: float


def _match_id(season: int, day: int, home: str, away: str) -> str:
    return f"S{season}-MD{day}-{home}-vs-{away}"


def list_teams() -> list[dict]:
    """All 16 BSL teams.  Priority: live archive, then fixtures, then defaults."""
    if live_available():
        return [{"team_id": i + 1, "name": r["name"]}
                for i, r in enumerate(bridge_teams())]
    # fall back to the union of teams in the fixtures
    seen: dict[str, int] = {}
    for src in (_iter_fixture_results(), _iter_fixture_upcoming()):
        for r in src:
            for name in (r["home"], r["away"]):
                if name and name not in seen:
                    seen[name] = len(seen) + 1
    if not seen:
        # last-resort defaults — the canonical 16
        for n in (
            "Bidii Co. BSL", "Gor BSL", "Batoto BSL", "Kanairo Stars",
            "Ba Zoo BSL", "Bandarini BSL", "Leopard Sakata", "Sakata Nzoia",
            "Walinzi BSL", "Sakata Mathare", "KCBB BSL", "Tuska Sakata",
            "P Rangers BSL", "Sakata Sharks", "Sakata Wazito", "Kach BSL",
        ):
            if n not in seen:
                seen[n] = len(seen) + 1
    return [{"team_id": tid, "name": name} for name, tid in seen.items()]


def get_team_id(name: str) -> Optional[int]:
    for t in list_teams():
        if t["name"] == name:
            return t["team_id"]
    return None


def list_matches(season: Optional[int] = None, matchday: Optional[int] = None) -> list[MatchRow]:
    """All matches visible to the analyzer.

    Sources, merged:
      1. The live archive (`data/league.sqlite`) if it exists.
      2. Bundled finalised fixtures from `tests/fixtures/*results*.json`.
      3. Bundled upcoming fixtures from `tests/fixtures/upcoming.json`.

    The function uses match_id as the dedupe key so the same fixture
    fetched from two sources never appears twice.

    Filters: when `season` is None, all seasons are kept; otherwise the
    filter is applied to every source.
    """
    out: list[MatchRow] = []
    seen_ids: set[str] = set()
    seen_pairs: set[tuple] = set()

    def _emit(m: MatchRow):
        # Dedupe on match_id first, then on (season, matchday, home, away).
        if m.match_id in seen_ids:
            return
        key = (m.season, m.matchday, m.home_team_id, m.away_team_id)
        if key in seen_pairs:
            return
        seen_ids.add(m.match_id)
        seen_pairs.add(key)
        out.append(m)

    sources: list = []
    if live_available():
        sources.append(_bridge_table("matches", season=season, day=matchday))
    sources.append(_iter_fixture_results(season=season, matchday=matchday))
    sources.append(_iter_fixture_upcoming(season=season, matchday=matchday))

    for src in sources:
        for r in src:
            try:
                if season is not None and int(r["season"]) != int(season):
                    continue
                if matchday is not None and int(r["day"]) != int(matchday):
                    continue
                home, away = r["home"], r["away"]
                if not home or not away:
                    continue
                home_id = get_team_id(home)
                away_id = get_team_id(away)
                if home_id is None or away_id is None:
                    continue
                _emit(MatchRow(
                    match_id=_match_id(int(r["season"]), int(r["day"]), home, away),
                    season=int(r["season"]),
                    matchday=int(r["day"]),
                    home_team_id=home_id,
                    away_team_id=away_id,
                    home_team=home,
                    away_team=away,
                    ht_home=r["ht_home"], ht_away=r["ht_away"],
                    ft_home=r["ft_home"], ft_away=r["ft_away"],
                    status=str(r["status"] or "upcoming"),
                    start_time=r["start_time"],
                    source="live" if live_available() else "fixture",
                ))
            except Exception:
                continue
    out.sort(key=lambda m: (m.season or 0, m.matchday or 0, m.home_team))
    return out


def _is_nonempty(src) -> bool:
    """Quick check that an iterator yields at least one row."""
    it = iter(src)
    try:
        first = next(it)
    except StopIteration:
        return False
    return True


# ----------------------------------------------------------------------
# Markets cache (edge_markets_cache)  — backed by edge.sqlite
# ----------------------------------------------------------------------
def cache_market(event_id: str, season: Optional[int], matchday: Optional[int],
                 home: str, away: str, markets: list[dict], full: int = 0) -> None:
    """Insert / merge the cached markets catalogue for a virtual event.

    `markets` follows the existing collector's shape::

        [
          {"name": "1X2", "sub_type_id": "1",
           "odds": [{"display":"1","value":"1.55","special_bet_value":None,
                     "outcome_id":"sr:match_winner:1"}, ...]},
          ...
        ]

    Subsequent calls with the same `event_id` merge into the existing
    catalogue rather than overwriting — the standalone
    `markets.json` fixture and the bundled `upcoming.json` row both
    carry markets for the same event, and we want the union.
    """
    init_edge()
    with edge_conn() as c:
        existing = c.execute(
            "SELECT season,matchday,home_team,away_team,markets,full FROM edge_markets_cache WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if existing:
            try:
                prior = json.loads(existing["markets"])
            except Exception:
                prior = []
            merged = _merge_markets(prior, markets)
            c.execute(
                "UPDATE edge_markets_cache SET season=?, matchday=?, home_team=?, away_team=?, "
                "markets=?, full=?, fetched_at=strftime('%s','now') WHERE event_id=?",
                (
                    season if season is not None else existing["season"],
                    matchday if matchday is not None else existing["matchday"],
                    home or existing["home_team"], away or existing["away_team"],
                    json.dumps(merged, ensure_ascii=False),
                    int(max(full or 0, existing["full"] or 0)),
                    event_id,
                ),
            )
        else:
            c.execute(
                "INSERT INTO edge_markets_cache(event_id,season,matchday,home_team,away_team,markets,full,fetched_at) "
                "VALUES(?,?,?,?,?,?,?,strftime('%s','now'))",
                (event_id, season, matchday, home, away,
                 json.dumps(markets, ensure_ascii=False), int(full)),
            )


def _merge_markets(prior: list[dict], extra: list[dict]) -> list[dict]:
    """Union-by-(market_name) — keep prior outcomes, append new ones
    from `extra` for market names `prior` doesn't yet have."""
    by_name = {m.get("name", ""): m for m in prior}
    for m in extra:
        name = m.get("name", "")
        if name in by_name:
            # append any odds not already present
            seen = {(o.get("display"), o.get("value")) for o in by_name[name].get("odds", [])}
            for o in m.get("odds", []):
                key = (o.get("display"), o.get("value"))
                if key not in seen:
                    by_name[name].setdefault("odds", []).append(o)
                    seen.add(key)
        else:
            by_name[name] = m
    return list(by_name.values())


def seed_markets_from_fixtures() -> int:
    """One-shot: copy every market that the bundled fixtures carry into the
    edge cache.

    Sources, in priority order:
      1. **`tests/fixtures/upcoming.json`** — each row has a nested
         `markets` array (1X2, Double chance, Total, …) per event.
      2. **`tests/fixtures/markets.json`** — a fuller 23-market catalogue
         for one event (Bandarini vs Walinzi, S3134579 MD10).
    """
    init_edge()
    n = 0

    # ----- 1. Upcoming rows carry inline markets ----------------------
    payload = _load_json("upcoming.json")
    if payload:
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            for group_key, rows in data.items():
                try:
                    day = int(group_key)
                except ValueError:
                    continue
                for row in rows or []:
                    eid = str(row.get("parent_virtual_id") or "")
                    if not eid:
                        continue
                    markets = []
                    for m in row.get("markets") or []:
                        odds = [
                            {
                                "display": o.get("display") or o.get("odd_key") or "",
                                "value": str(o.get("odd_value") or ""),
                                "special_bet_value": o.get("special_bet_value"),
                                "outcome_id": str(o.get("outcome_id") or ""),
                            }
                            for o in (m.get("odds") or [])
                            if (str(o.get("odd_value") or "")).replace(".", "").lstrip("-").isdigit()
                        ]
                        if odds:
                            markets.append({"name": m.get("name") or "",
                                            "sub_type_id": m.get("sub_type_id"),
                                            "odds": odds})
                    if markets:
                        season = int(row.get("season") or 0) or None
                        cache_market(eid, season, day,
                                     row.get("home_team") or "",
                                     row.get("away_team") or "",
                                     markets, full=1)
                        n += 1

    # ----- 2. Standalone markets.json catalogue -----------------------
    for m in _iter_fixture_markets():
        if not m["event_id"]:
            continue
        markets = [{
            "name": m["market_name"],
            "sub_type_id": str(m["sub_type_id"] or ""),
            "odds": [
                {
                    "display": o.get("display") or o.get("odd_key") or "",
                    "value": str(o.get("odd_value") or o.get("decimal_odds") or ""),
                    "special_bet_value": o.get("special_bet_value"),
                    "outcome_id": str(o.get("outcome_id") or ""),
                }
                for o in m["odds"]
                if str(o.get("odd_value") or o.get("decimal_odds") or "").replace(".", "").lstrip("-").isdigit()
            ],
        }]
        if markets and markets[0]["odds"]:
            cache_market(m["event_id"], m["season"], m["matchday"],
                         m["home_team"] or "", m["away_team"] or "",
                         markets, full=1)
            n += 1
    return n


def list_markets_for_match(match_id: str) -> list[MarketRow]:
    """Return all markets + outcomes for a synthetic match id.

    Match ids look like ``S3138368-MD2-Leopard Sakata-vs-Kach BSL``.
    """
    init_edge()
    parsed = _parse_match_id(match_id)
    if not parsed:
        return []
    season, matchday, home, away = parsed
    with edge_conn() as c:
        rows = list(c.execute(
            "SELECT * FROM edge_markets_cache "
            "WHERE season=? AND matchday=? AND home_team=? AND away_team=?",
            (season, matchday, home, away),
        ).fetchall())
    if not rows:
        return []
    out: list[MarketRow] = []
    fetched_at = float(rows[0]["fetched_at"] or 0)
    for row in rows:
        try:
            markets = json.loads(row["markets"])
        except Exception:
            continue
        for m in markets:
            for i, o in enumerate(m.get("odds") or []):
                try:
                    val = float(o.get("value") or o.get("decimal_odds"))
                except (TypeError, ValueError):
                    continue
                if val <= 0:
                    continue
                out.append(MarketRow(
                    market_id=int(f"{row['event_id']}{m.get('sub_type_id','')}{i}"[:12], 36) or 0,
                    match_id=match_id,
                    market_name=m.get("name") or "",
                    sub_type_id=int(m["sub_type_id"]) if str(m.get("sub_type_id", "")).isdigit() else None,
                    special_bet_value=o.get("special_bet_value"),
                    outcome_label=str(o.get("display") or o.get("odd_key") or ""),
                    decimal_odds=val,
                    outcome_id=str(o.get("outcome_id") or "") or None,
                    fetched_at=fetched_at,
                ))
    return out


def _parse_match_id(mid: str) -> Optional[tuple[int, int, str, str]]:
    """Reverse the encode done by `_match_id` — tolerant to team-name dashes.

    Format: ``S{season}-MD{day}-{home}-vs-{away}``
    """
    if not mid.startswith("S"):
        return None
    try:
        season_str, rest = mid[1:].split("-", 1)
        season = int(season_str)
        # find the "MD<int>-" prefix
        if not rest.startswith("MD"):
            return None
        md_str, rest = rest.split("-", 1)
        matchday = int(md_str[2:])
        # split at the LAST "-vs-"
        if "-vs-" in rest:
            home, away = rest.rsplit("-vs-", 1)
        else:
            home, away = rest.rsplit("-", 1)
        return season, matchday, home, away
    except Exception:
        return None


def build_match_id(season: int, matchday: int, home: str, away: str) -> str:
    return _match_id(season, matchday, home, away)


# ----------------------------------------------------------------------
# Edge H2H — populated by reading archive + fixtures
# ----------------------------------------------------------------------
def refresh_edge_h2h() -> int:
    """Rebuild edge_h2h from every available source (live + fixtures)."""
    init_edge()
    n = 0
    sources = []
    if live_available():
        sources.append(_bridge_table("matches"))
    sources.append(_iter_fixture_results())
    seen: set[tuple[str, str, int, int]] = set()
    with edge_conn() as c:
        for src in sources:
            for r in src:
                ht_h = r["ht_home"]; ht_a = r["ht_away"]
                ft_h = r["ft_home"]; ft_a = r["ft_away"]
                if ft_h is None or ft_a is None:
                    continue
                key = (r["home"], r["away"], int(r["season"]), int(r["day"]))
                if key in seen:
                    continue
                seen.add(key)
                c.execute(
                    "INSERT OR REPLACE INTO edge_h2h(home_team,away_team,season,matchday,ht_home,ht_away,ft_home,ft_away) "
                    "VALUES(?,?,?,?,?,?,?,?)", key + (ht_h, ht_a, ft_h, ft_a),
                )
                n += 1
    return n


def seed_demo_h2h(home: str, away: str, n_meetings: int = 5) -> int:
    """Seed *synthetic* H2H meetings so the two-market rule can be exercised
    on fixtures that don't yet have any real meetings in the archive.

    The synthesized meetings are clearly labelled ``season=0`` so they
    never collide with real seasons, and they appear in the H2H table
    alongside genuine data — readers of the H2H page see an explicit
    *synthetic* marker on every one.
    """
    init_edge()
    import random as _r
    rng = _r.Random(0x42424242)
    n = 0
    for md in range(-n_meetings, 0):
        ft_h = rng.randint(0, 3)
        ft_a = rng.randint(0, 2)
        ht_h = min(ft_h, rng.randint(0, 2))
        ht_a = min(ft_a, rng.randint(0, 1))
        with edge_conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO edge_h2h(home_team,away_team,season,matchday,ht_home,ht_away,ft_home,ft_away) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (home, away, 0, md, ht_h, ht_a, ft_h, ft_a),
            )
            n += 1
    return n


def list_h2h(home_team: str, away_team: str) -> list[sqlite3.Row]:
    init_edge()
    with edge_conn() as c:
        return list(c.execute(
            "SELECT * FROM edge_h2h WHERE home_team=? AND away_team=? ORDER BY season,matchday",
            (home_team, away_team),
        ).fetchall())


# ----------------------------------------------------------------------
# Settings (edge-only)
# ----------------------------------------------------------------------
def get_setting(key: str, default: Any = None) -> str:
    init_edge()
    with edge_conn() as c:
        row = c.execute("SELECT value FROM edge_settings WHERE key=?", (key,)).fetchone()
    if row:
        return str(row["value"])
    return str(default) if default is not None else ""


def set_setting(key: str, value: Any) -> None:
    init_edge()
    with edge_conn() as c:
        c.execute(
            "INSERT INTO edge_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def load_settings() -> dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    for k in DEFAULT_SETTINGS:
        out[k] = get_setting(k, DEFAULT_SETTINGS[k])
    return out


# ----------------------------------------------------------------------
# Ledger (edge-only)
# ----------------------------------------------------------------------
def insert_ledger_row(row: dict[str, Any]) -> int:
    init_edge()
    cols = [
        "match_id", "matchday", "season",
        "market_a_label", "market_a_odds", "market_a_stake", "market_a_result",
        "market_b_label", "market_b_odds", "market_b_stake", "market_b_result",
        "profit_or_loss", "notes",
    ]
    placeholders = ",".join("?" * len(cols))
    with edge_conn() as c:
        cur = c.execute(
            f"INSERT INTO edge_ledger({','.join(cols)}) VALUES ({placeholders})",
            [row.get(c) for c in cols],
        )
        return int(cur.lastrowid)


def update_ledger_result(bet_id: int, a_result: str, b_result: str, profit: float) -> None:
    init_edge()
    with edge_conn() as c:
        c.execute(
            "UPDATE edge_ledger SET market_a_result=?, market_b_result=?, profit_or_loss=? WHERE bet_id=?",
            (a_result, b_result, profit, bet_id),
        )


def list_ledger() -> list[sqlite3.Row]:
    init_edge()
    with edge_conn() as c:
        return list(c.execute("SELECT * FROM edge_ledger ORDER BY placed_at DESC, bet_id DESC").fetchall())


def query_one(sql: str, params: Iterable[Any] = ()):
    init_edge()
    with edge_conn() as c:
        row = c.execute(sql, tuple(params)).fetchone()
    return _DictRow(dict(row)) if row else None


def query_all(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    init_edge()
    with edge_conn() as c:
        return [_DictRow(dict(r)) for r in c.execute(sql, tuple(params)).fetchall()]


# ----------------------------------------------------------------------
# CSV export
# ----------------------------------------------------------------------
def export_csvs() -> dict[str, Path]:
    import csv
    tables = ("edge_markets_cache", "edge_h2h", "edge_ledger", "edge_settings")
    out: dict[str, Path] = {}
    with edge_conn() as c:
        for t in tables:
            rows = c.execute(f"SELECT * FROM {t}").fetchall()
            if not rows:
                continue
            path = CSV_DIR / f"{t}.csv"
            with path.open("w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(rows[0].keys())
                for r in rows:
                    w.writerow([r[k] for k in rows[0].keys()])
            out[t] = path
    if live_available():
        with _conn(LIVE_DB) as c:
            for t in ("teams", "matches"):
                rows = c.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    continue
                path = CSV_DIR / f"live_{t}.csv"
                with path.open("w", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    w.writerow(rows[0].keys())
                    for r in rows:
                        w.writerow([r[k] for k in rows[0].keys()])
                out[t] = path
    return out


# ----------------------------------------------------------------------
# Bootstrap helper — called by CLI / Streamlit on startup
# ----------------------------------------------------------------------
def bootstrap() -> dict[str, Any]:
    """One-shot warm-up: init edge DB, refresh H2H, seed fixture markets.

    Returns a small dict so callers can show "what got loaded" in the
    UI without having to re-query the database.
    """
    init_edge()
    h2h_n = refresh_edge_h2h()
    markets_n = seed_markets_from_fixtures()
    live = live_available()
    return {
        "live_archive": live,
        "edge_db": str(EDGE_DB),
        "live_db": str(LIVE_DB),
        "edge_h2h_rows": h2h_n,
        "edge_markets_cached": markets_n,
        "fixtures_dir": str(FIXTURES),
    }


# ----------------------------------------------------------------------
# CLI — verify the integration in isolation
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s | %(message)s")
    info = bootstrap()
    print(json.dumps(info, indent=2, default=str))
    print("teams:", len(list_teams()))
    print("sample matches:", len(list_matches(season=3138368, matchday=2)))
    print("settings:", load_settings())
