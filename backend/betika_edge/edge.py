"""FastAPI-side wrapper around the Betika Virtual Edge Analyzer.

Same architecture as `backend.playground_service` / `backend.matchup.Ma
tchupService`: a thin, thread-safe facade that owns the workspace's
runtime state, exposes the same JSON shapes from the Streamlit pages,
and rebuilds cheap composite views on demand.  Heavy model fits
(Poisson, EV) are cached.

The underlying modules are the ones in this package: ``db``, ``h2h``,
``markets``, ``poisson_model``, ``value_bets``, ``ledger``,
``matchday_tracker``.  All writer-level state lives in
``data/edge.sqlite``; reads pass through the live collector's
``data/league.sqlite`` via the bridge in ``db``.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Optional

from . import db, h2h as h2h_mod, markets, matchday_tracker, ledger, poisson_model, value_bets
from .models import predict_for_match, all_team_profiles


log = logging.getLogger("betika.edge")

# ----------------------------------------------------------------------
# Shared state
# ----------------------------------------------------------------------
_lock = threading.RLock()
_status: dict[str, Any] = {}


def _now() -> float:
    return time.time()


# ----------------------------------------------------------------------
# Bootstrap
# ----------------------------------------------------------------------
def bootstrap(force: bool = False) -> dict[str, Any]:
    """Idempotent — reads both the live archive and the bundled fixtures,
    refreshes the H2H index, seeds the markets cache.  Cheap; called on
    app startup."""
    global _status
    with _lock:
        if _status and not force:
            return _status
        try:
            info = db.bootstrap()
        except Exception as e:
            log.warning("bootstrap failed: %s", e)
            info = {"live_archive": False, "edge_h2h_rows": 0, "edge_markets_cached": 0}
        _status = {**info, "bootstrapped_at": _now()}
        return _status


def status() -> dict[str, Any]:
    bootstrap()
    with _lock:
        s = dict(_status)
    # live snapshot — cheap to compute on every call
    s["edge_db"] = str(db.EDGE_DB)
    s["live_db"] = str(db.LIVE_DB)
    s["fixtures_dir"] = str(db.FIXTURES)
    s["teams_count"] = len(db.list_teams())
    s["matches_count"] = len(db.list_matches())
    # Surface collector health so the UI can tell the user whether
    # the data pipeline is healthy or stuck (e.g. firewall blocking
    # virtuals.betika.com). We read the main collector's live DB.
    try:
        from backend.store import Store  # type: ignore
        store = Store(Path(db.LIVE_DB))
        summary = store.overview()
        source = summary.get("source", {}) or {}
        endpoints = summary.get("endpoints", {}) or {}
        s["collector"] = {
            "source": source.get("name", "Betika public website feeds"),
            "domain": source.get("domain", "virtuals.betika.com"),
            "competition_id": source.get("competition_id", 26),
            "max_requests_per_second": 1,
            "read_only": True,
            "stale": bool(source.get("stale")),
            "source_age_seconds": source.get("age_seconds"),
            "live_age_seconds": source.get("live_age_seconds"),
            "current_season": summary.get("current_season"),
            "complete_seasons": summary.get("counts", {}).get("complete_seasons", 0),
            "complete_days": summary.get("counts", {}).get("complete_days", 0),
            "live_matches": summary.get("counts", {}).get("matches", 0),
            "live_finalized": summary.get("counts", {}).get("finalized", 0),
            "receipts_cached": summary.get("counts", {}).get("receipts", 0),
            "paused": bool(summary.get("paused", False)),
        }
        last_errors = []
        for route, info in endpoints.items():
            if isinstance(info, dict) and info.get("ok") is False and info.get("error"):
                last_errors.append({"route": route, "error": str(info.get("error"))[:200],
                                    "at": info.get("at")})
        s["endpoints"] = endpoints
        s["last_endpoint_errors"] = last_errors[:6]
        s["seasons_tracked"] = len(summary.get("seasons", []) or [])
        s["activity"] = (summary.get("activity") or [])[:8]
    except Exception as exc:
        s["collector"] = {"error": f"collector probe failed: {exc}"}
    return s


def refresh() -> dict[str, Any]:
    """Force-rebuild H2H + seed markets from bundled fixtures."""
    bootstrap()
    with _lock:
        h2h_n = db.refresh_edge_h2h()
        markets_n = db.seed_markets_from_fixtures()
        _status.update({"edge_h2h_rows": h2h_n,
                        "edge_markets_cached": markets_n,
                        "bootstrapped_at": _now()})
    return status()


# ----------------------------------------------------------------------
# Teams
# ----------------------------------------------------------------------
def teams() -> list[dict]:
    bootstrap()
    return [{"id": t["team_id"], "name": t["name"]} for t in db.list_teams()]


def team_profiles() -> list[dict]:
    bootstrap()
    return all_team_profiles()


# ----------------------------------------------------------------------
# Matches
# ----------------------------------------------------------------------
def matches(season: Optional[int] = None, matchday: Optional[int] = None) -> list[dict]:
    bootstrap()
    out = []
    for m in db.list_matches(season=season, matchday=matchday):
        out.append({
            "match_id": m.match_id,
            "season": m.season, "matchday": m.matchday,
            "home_team_id": m.home_team_id, "away_team_id": m.away_team_id,
            "home": m.home_team, "away": m.away_team,
            "ht_home": m.ht_home, "ht_away": m.ht_away,
            "ft_home": m.ft_home, "ft_away": m.ft_away,
            "status": m.status,
            "start_time": m.start_time,
            "source": m.source,
        })
    return out


def _safe_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------
# Markets / no-vig / value
# ----------------------------------------------------------------------
def markets_for(match_id: str) -> list[dict]:
    """All market rows for a single fixture, no-vig computed.

    Accepts either a synthetic `match_id` (`S<season>-MD<day>-<home>-vs-<away>`)
    or a raw `event_id` (digit string).  The fixture metadata is looked up
    from `edge_markets_cache` first, then from the live store + bundled
    fixtures if needed, and finally falls through with the caller's value.
    """
    bootstrap()
    if not match_id:
        return []
    if match_id.isdigit():
        # raw event_id — look up the canonical match_id
        with db.edge_conn() as c:
            row = c.execute(
                "SELECT season,matchday,home_team,away_team FROM edge_markets_cache WHERE event_id=?",
                (match_id,),
            ).fetchone()
        if row and row["season"] is not None and row["matchday"] is not None:
            match_id = db.build_match_id(
                int(row["season"]), int(row["matchday"]),
                row["home_team"] or "", row["away_team"] or "",
            )
    table = markets.no_vig_table(match_id)
    out = []
    for market_name, items in table.markets.items():
        for r in items:
            out.append({
                "market": market_name,
                "outcome": r.outcome_label,
                "decimal_odds": r.decimal_odds,
                "raw_implied": r.raw_implied,
                "overround": r.overround,
                "no_vig": r.no_vig,
                "margin": r.margin,
            })
    return out


def no_vig_table_for(match_id: str) -> dict:
    bootstrap()
    table = markets.no_vig_table(match_id)
    return table.to_dict()


def cross_flags(match_id: str, threshold: float = 0.03) -> list[dict]:
    bootstrap()
    table = markets.no_vig_table(match_id)
    return markets.cross_market_flags(table, threshold=threshold)


# ----------------------------------------------------------------------
# H2H
# ----------------------------------------------------------------------
def h2h(home: str, away: str) -> dict:
    bootstrap()
    stats = h2h_mod.pair_stats(home, away)
    d = stats.to_dict()
    d["rule"] = {
        "min_odds": float(db.get_setting("min_odds", "2.00")),
        "h2h_both_gt_05": float(db.get_setting("h2h_both_gt_05", "0.60")),
        "h2h_min_meetings": int(db.get_setting("h2h_min_meetings", "3")),
    }
    return d


def pair_stats(home: str, away: str) -> dict:
    return h2h(home, away)


# ----------------------------------------------------------------------
# Poisson fit / predictions / value bets
# ----------------------------------------------------------------------
def predict(home: str, away: str) -> dict:
    bootstrap()
    home_id = db.get_team_id(home)
    away_id = db.get_team_id(away)
    if home_id is None or away_id is None:
        return {"error": "unknown team"}
    fit, pred = predict_for_match(home_id, away_id)
    return {
        "home": pred.home_team, "away": pred.away_team,
        "lambda_home": pred.lam_home, "lambda_away": pred.lam_away,
        "p_home": pred.p_home, "p_draw": pred.p_draw, "p_away": pred.p_away,
        "p_over_05": pred.p_over_05, "p_over_15": pred.p_over_15,
        "p_over_25": pred.p_over_25, "p_over_35": pred.p_over_35,
        "p_under_25": pred.p_under_25, "p_btts_yes": pred.p_btts_yes,
        "p_btts_no": pred.p_btts_no,
        "expected_total": pred.expected_total,
        "expected_home": pred.expected_home, "expected_away": pred.expected_away,
        "most_likely": [[list(k), v] for k, v in pred.most_likely],
        "exact_goals": pred.exact_goals,
        "home_exact_goals": pred.home_exact_goals,
        "away_exact_goals": pred.away_exact_goals,
        "sample_size": pred.sample_size,
        "fit": {
            "home_advantage": fit.home_advantage,
            "league_avg_home": fit.league_avg_home,
            "league_avg_away": fit.league_avg_away,
            "matches": fit.n_matches,
            "team_strengths": [
                {"team": ts.name, "attack": round(ts.attack, 3), "defence": round(ts.defence, 3),
                 "matches": ts.matches, "goals_for": ts.goals_for, "goals_against": ts.goals_against}
                for ts in fit.teams.values()
            ],
        },
    }


def value_bets_for(match_id: str, min_edge: Optional[float] = None,
                   kelly: Optional[float] = None) -> list[dict]:
    bootstrap()
    if min_edge is None:
        min_edge = float(db.get_setting("min_edge", "0.05"))
    if kelly is None:
        kelly = float(db.get_setting("kelly_fraction", "0.25"))
    # find home/away
    m = next((x for x in db.list_matches() if x.match_id == match_id), None)
    if m is None:
        return []
    fit = poisson_model.fit_league()
    bets = value_bets.value_bets_with_model(
        match_id, m.home_team_id, m.away_team_id, fit,
        min_edge=min_edge, kelly_fraction=kelly,
    )
    return [b.to_dict() for b in bets]


def two_market_view(match_id: str) -> dict:
    bootstrap()
    m = next((x for x in db.list_matches() if x.match_id == match_id), None)
    if m is None:
        return {"market_a": None, "market_b": None}
    fit = poisson_model.fit_league()
    pred = poisson_model.predict_fixture(fit, m.home_team_id, m.away_team_id)
    v = value_bets.two_market_view(
        match_id, pred.away_exact_goals, pred.p_over_15, away_team_name=m.away_team,
    )
    return v


# ----------------------------------------------------------------------
# Matchday tracker
# ----------------------------------------------------------------------
def matchday(season: int) -> dict:
    bootstrap()
    rows = matchday_tracker.build_table(season=season)
    s = matchday_tracker.summary(rows)
    return {
        "season": season,
        "rows": [r.to_dict() for r in rows],
        "summary": s,
    }


# ----------------------------------------------------------------------
# Ledger
# ----------------------------------------------------------------------
def ledger_rows() -> list[dict]:
    bootstrap()
    return [dict(r) for r in db.list_ledger()]


def ledger_summary() -> dict:
    bootstrap()
    s = ledger.summarize()
    return s.to_dict()


def place_bet(match_id: str, stake: Optional[float] = None) -> dict:
    bootstrap()
    try:
        bet_id = ledger.place_bet(match_id, stake=stake)
    except ValueError as e:
        msg = str(e)
        return {"ok": False, "error": msg.removeprefix("NO BET — reason: ") if msg.startswith("NO BET") else msg}
    return {"ok": True, "bet_id": bet_id}


def settle_pending() -> dict:
    bootstrap()
    n = ledger.settle_all_unsettled()
    return {"settled": n}


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------
def settings_get() -> dict[str, str]:
    bootstrap()
    return db.load_settings()


def settings_set(payload: dict[str, str]) -> dict:
    bootstrap()
    for k, v in payload.items():
        db.set_setting(k, v)
    return {"ok": True, "settings": settings_get()}


# ----------------------------------------------------------------------
# Demo helpers
# ----------------------------------------------------------------------
def seed_demo_h2h(home: str, away: str, n: int = 5) -> dict:
    bootstrap()
    added = db.seed_demo_h2h(home, away, n_meetings=int(n))
    return {"ok": True, "added": added}


# ----------------------------------------------------------------------
# Cache management
# ----------------------------------------------------------------------
def clear_cache() -> dict:
    bootstrap()
    with db.edge_conn() as c:
        c.execute("DELETE FROM edge_ledger")
        c.execute("DELETE FROM edge_h2h")
        c.execute("DELETE FROM edge_markets_cache")
    return {"ok": True}
