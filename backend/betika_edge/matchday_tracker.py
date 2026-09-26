"""
matchday_tracker.py — Build the MD1..30 board from the unified store.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from . import db
from .ledger import evaluate_match


TOTAL_MATCHDAYS = 30


@dataclass
class MatchdayRow:
    season: Optional[int]
    matchday: int
    match_label: str
    ht: str
    ft: str
    both_gt_05: str
    market_a_odds: Optional[float]
    market_b_odds: Optional[float]
    rule_met: bool
    bet_placed: bool
    market_a_result: str
    market_b_result: str
    pnl: Optional[float]
    reason: str
    fixture_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _format_score(home, away) -> str:
    if home is None or away is None:
        return ""
    return f"{int(home)}-{int(away)}"


def build_table(season: Optional[int] = None,
                matchdays: int = TOTAL_MATCHDAYS) -> list[MatchdayRow]:
    rows: list[MatchdayRow] = []
    matches = db.list_matches(season=season)
    by_md: dict[int, list] = {}
    for m in matches:
        if m.matchday is None:
            continue
        by_md.setdefault(int(m.matchday), []).append(m)

    ledger_rows = db.list_ledger()
    bet_by_match = {r["match_id"]: r for r in ledger_rows}

    for md in range(1, matchdays + 1):
        ms = by_md.get(md, [])
        if not ms:
            rows.append(MatchdayRow(
                season=season, matchday=md,
                match_label="(no fixture in archive)",
                ht="", ft="", both_gt_05="", market_a_odds=None,
                market_b_odds=None, rule_met=False, bet_placed=False,
                market_a_result="", market_b_result="", pnl=None,
                reason="NO BET — no fixture in archive for this matchday",
            ))
            continue

        for m in ms:
            mid = m.match_id
            label = f"{m.home_team} vs {m.away_team}"
            decision = evaluate_match(mid)
            bet_row = bet_by_match.get(mid)
            both_gt = ""
            if decision.played:
                both_gt = f"{decision.both_gt_05:.0%} ({decision.played})"
            pnl = None
            a_res = b_res = ""
            if bet_row is not None:
                pnl = bet_row["profit_or_loss"]
                a_res = bet_row["market_a_result"] or ""
                b_res = bet_row["market_b_result"] or ""

            rows.append(MatchdayRow(
                season=season, matchday=md,
                match_label=label,
                ht=_format_score(m.ht_home, m.ht_away),
                ft=_format_score(m.ft_home, m.ft_away),
                both_gt_05=both_gt,
                market_a_odds=decision.market_a_odds,
                market_b_odds=decision.market_b_odds,
                rule_met=decision.should_bet,
                bet_placed=bet_row is not None,
                market_a_result=a_res,
                market_b_result=b_res,
                pnl=pnl,
                reason=decision.reason if not decision.should_bet else "rule met",
                fixture_id=mid,
            ))
    return rows


def summary(rows: list[MatchdayRow]) -> dict:
    total = len(rows)
    with_bet = sum(1 for r in rows if r.bet_placed)
    wins = sum(1 for r in rows if r.bet_placed and (r.market_a_result == "WIN" or r.market_b_result == "WIN"))
    losses = sum(1 for r in rows if r.bet_placed and r.market_a_result == "LOSS" and r.market_b_result == "LOSS")
    no_bet = total - with_bet
    pnl = sum((r.pnl or 0.0) for r in rows)
    return {
        "total_matchdays": total,
        "with_bet": with_bet,
        "wins": wins,
        "losses": losses,
        "no_bet_days": no_bet,
        "total_pnl": pnl,
        "roi": (pnl / max(1.0, with_bet * float(db.get_setting("stake", "100")))) if with_bet else 0.0,
    }
