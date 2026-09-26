"""
value_bets.py — Convenience wrapper for the two-market view + EV list.
"""

from __future__ import annotations

from typing import Iterable

from .markets import no_vig_table, ValueBet, value_bets as _value_bets
from .poisson_model import predict_fixture


AWAY_EXACT_1_LABELS = ("1", "AWAY 1", "AWAY EXACT GOALS 1", "AWAY_EXACT_GOALS 1")
AWAY_OVER_15_LABELS = ("OVER 1.5", "AWAY OVER 1.5", "AWAY_TOTAL OVER 1.5",
                       "OVER 1.5 GOALS", "AWAY TEAM OVER 1.5")


def _find_outcome(rows, candidates: Iterable[str]):
    for r in rows:
        if r.outcome_label.strip().upper() in {c.upper() for c in candidates}:
            return r
    for r in rows:
        if any(c.upper() in r.outcome_label.upper() for c in candidates):
            return r
    return None


def two_market_view(match_id: str, fair_away_exact: dict,
                    fair_away_over15: float,
                    away_team_name: Optional[str] = None) -> dict:
    table = no_vig_table(match_id)
    away_exact_keys = ("AWAY_EXACT_GOALS", "EXACT_GOALS_AWAY",
                       "AWAY TEAM EXACT GOALS", "AWAY GOALS EXACT")
    away_total_keys = ("AWAY_TOTAL", "AWAY_TOTAL_GOALS", "AWAY TEAM TOTAL",
                       "AWAY TOTAL", "AWAY OVER/UNDER")
    a_rows = next((table.markets[k] for k in away_exact_keys if k in table.markets), [])
    b_rows = next((table.markets[k] for k in away_total_keys if k in table.markets), [])
    a = _find_outcome(a_rows, AWAY_EXACT_1_LABELS)
    b = _find_outcome(b_rows, AWAY_OVER_15_LABELS)

    # Fall back to per-team markets in the bundled-fixture format
    if (a is None or b is None) and away_team_name:
        up_name = away_team_name.upper()
        if a is None:
            for market_name, market_rows in table.markets.items():
                up = market_name.upper()
                if "EXACT GOALS" in up and up_name in up:
                    a = _find_outcome(market_rows, AWAY_EXACT_1_LABELS)
                    if a:
                        break
        if b is None:
            for market_name, market_rows in table.markets.items():
                up = market_name.upper()
                if "TOTAL" in up and up_name in up and "EXACT" not in up:
                    b = _find_outcome(market_rows, AWAY_OVER_15_LABELS)
                    if b:
                        break

    def _wrap(row, fair):
        if row is None:
            return None
        return {
            "label": row.outcome_label,
            "decimal_odds": row.decimal_odds,
            "raw_implied": row.raw_implied,
            "no_vig": row.no_vig,
            "fair": float(fair),
            "ev": float(fair) * float(row.decimal_odds) - 1.0,
        }

    return {
        "market_a": _wrap(a, fair_away_exact.get("1", 0.0)),
        "market_b": _wrap(b, fair_away_over15),
    }


def value_bets_with_model(match_id: str, home_id: int, away_id: int,
                          fit, min_edge: float = 0.05,
                          kelly_fraction: float = 0.25) -> list[ValueBet]:
    pred = predict_fixture(fit, home_id, away_id)
    table = no_vig_table(match_id)
    fair: dict[str, float] = {}
    if "1X2" in table.markets:
        fair["1"] = pred.p_home; fair["X"] = pred.p_draw; fair["2"] = pred.p_away
    if "TOTAL" in table.markets or "TOTAL GOALS" in table.markets:
        fair["OVER 0.5"] = pred.p_over_05
        fair["OVER 1.5"] = pred.p_over_15
        fair["OVER 2.5"] = pred.p_over_25
        fair["OVER 3.5"] = pred.p_over_35
        fair["UNDER 2.5"] = pred.p_under_25
    if "BTTS" in table.markets:
        fair["YES"] = pred.p_btts_yes; fair["NO"] = pred.p_btts_no
    return _value_bets(match_id, min_edge=min_edge,
                       kelly_fraction=kelly_fraction, fair_source=fair)
