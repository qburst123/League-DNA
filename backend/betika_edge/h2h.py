"""
h2h.py — Head-to-head analysis (string team names).

For every directional pair (home_team_name, away_team_name) we look
at all stored meetings and compute played/wins/draws/losses, goal
averages, Over/Under hit rates, BTTS hit rate and the
both-teams-over-0.5 rate used by the two-market ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from . import db


# ----------------------------------------------------------------------
# Dataclass
# ----------------------------------------------------------------------
@dataclass
class PairStats:
    home_team_id: int
    home_team_name: str
    away_team_id: int
    away_team_name: str

    played: int = 0
    home_wins: int = 0
    draws: int = 0
    away_wins: int = 0

    avg_ft_home: float = 0.0
    avg_ft_away: float = 0.0
    avg_ft_total: float = 0.0
    avg_ht_home: float = 0.0
    avg_ht_away: float = 0.0
    avg_ht_total: float = 0.0

    over_05: float = 0.0
    over_15: float = 0.0
    over_25: float = 0.0
    btts_yes: float = 0.0
    both_gt_05: float = 0.0

    meetings: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if b else 0.0


# ----------------------------------------------------------------------
# Pair statistics
# ----------------------------------------------------------------------
def pair_stats(home_name: str, away_name: str) -> PairStats:
    """Compute PairStats for the directional pair (home vs away) by name."""
    home_id = db.get_team_id(home_name) or 0
    away_id = db.get_team_id(away_name) or 0
    rows = db.list_h2h(home_name, away_name)
    stats = PairStats(
        home_team_id=home_id, home_team_name=home_name,
        away_team_id=away_id, away_team_name=away_name,
    )

    if not rows:
        return stats

    n = len(rows)
    ft_home = [int(r["ft_home"]) for r in rows]
    ft_away = [int(r["ft_away"]) for r in rows]
    ht_home = [int(r["ht_home"] or 0) for r in rows]
    ht_away = [int(r["ht_away"] or 0) for r in rows]

    stats.played = n
    stats.home_wins = sum(1 for h, a in zip(ft_home, ft_away) if h > a)
    stats.away_wins = sum(1 for h, a in zip(ft_home, ft_away) if a > h)
    stats.draws = n - stats.home_wins - stats.away_wins

    stats.avg_ft_home = _safe_div(sum(ft_home), n)
    stats.avg_ft_away = _safe_div(sum(ft_away), n)
    stats.avg_ft_total = stats.avg_ft_home + stats.avg_ft_away
    stats.avg_ht_home = _safe_div(sum(ht_home), n)
    stats.avg_ht_away = _safe_div(sum(ht_away), n)
    stats.avg_ht_total = stats.avg_ht_home + stats.avg_ht_away

    stats.over_05 = _safe_div(sum(1 for h, a in zip(ft_home, ft_away) if h + a > 0.5), n)
    stats.over_15 = _safe_div(sum(1 for h, a in zip(ft_home, ft_away) if h + a > 1.5), n)
    stats.over_25 = _safe_div(sum(1 for h, a in zip(ft_home, ft_away) if h + a > 2.5), n)
    stats.btts_yes = _safe_div(sum(1 for h, a in zip(ft_home, ft_away) if h > 0 and a > 0), n)
    stats.both_gt_05 = _safe_div(
        sum(1 for h, a in zip(ft_home, ft_away) if h > 0.5 and a > 0.5), n
    )

    for r in rows:
        stats.meetings.append({
            "season": r["season"],
            "matchday": r["matchday"],
            "ht_home": r["ht_home"], "ht_away": r["ht_away"],
            "ft_home": r["ft_home"], "ft_away": r["ft_away"],
            "ft_total": int(r["ft_home"]) + int(r["ft_away"]),
            "synthetic": int(r["season"]) == 0,
        })
    return stats


def meets_threshold(stats: PairStats, threshold: float,
                    min_meetings: int) -> tuple[bool, str]:
    if stats.played < min_meetings:
        return False, f"only {stats.played} meetings (need ≥{min_meetings})"
    if stats.both_gt_05 + 1e-9 < threshold:
        return False, f"both_gt_05={stats.both_gt_05:.0%} < threshold={threshold:.0%}"
    return True, "rule met"


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
if __name__ == "__main__":
    db.init_edge()
    teams = db.list_teams()
    print(f"loaded {len(teams)} teams")
    if len(teams) >= 2:
        s = pair_stats(teams[0]["name"], teams[1]["name"])
        print(s)
