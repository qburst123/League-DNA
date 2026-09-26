"""
ledger.py — Two-market side-by-side paper-betting ledger.

Same strategy as before:

    Market A — Away team EXACT GOALS = 1
    Market B — Away team TOTAL goals OVER 1.5

Entry rule (enforced by code):

    * BOTH markets' decimal odds must be ≥ `min_odds` (default 2.00).
    * H2H "both teams >0.5 in ≥ threshold of meetings" must hold,
      with at least `min_meetings` recorded meetings.

Settlement (auto, after FT result is recorded):

    A wins only if away_goals == 1
    B wins if away_goals >= 2
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import db
from .h2h import pair_stats, meets_threshold
from .markets import no_vig_table


# ----------------------------------------------------------------------
# Resolution helpers
# ----------------------------------------------------------------------
def _find_outcome(rows, candidates):
    for r in rows:
        if r.outcome_label.strip().upper() in {c.upper() for c in candidates}:
            return r
    for r in rows:
        if any(c.upper() in r.outcome_label.upper() for c in candidates):
            return r
    return None


def _unwrap(wrapped):
    """Normalise either a raw `MarketRow` or a `(row, market_name)` pair."""
    if wrapped is None:
        return None
    if isinstance(wrapped, tuple) and len(wrapped) == 2:
        row, market_name = wrapped
        return {"odds": row.decimal_odds, "label": row.outcome_label,
                "market": market_name}
    if hasattr(wrapped, "decimal_odds"):
        return {"odds": wrapped.decimal_odds, "label": wrapped.outcome_label,
                "market": None}
    return None


def get_two_market_odds(match_id: str, away_team_name: Optional[str] = None) -> dict:
    """Find Market A and Market B for the given fixture.

    Tries:
      * the schema the brief asks for — ``AWAY_EXACT_GOALS``, ``AWAY_TOTAL``.
      * the per-team format the bundled fixture uses —
        ``"<TEAM> EXACT GOALS"`` / ``"<TEAM> TOTAL"`` — when ``away_team_name``
        is supplied.
    """
    table = no_vig_table(match_id)

    a_raw = None
    a_keys = ("AWAY_EXACT_GOALS", "EXACT_GOALS_AWAY",
              "AWAY TEAM EXACT GOALS", "AWAY GOALS EXACT")
    a_rows = next((table.markets[k] for k in a_keys if k in table.markets), [])
    a_raw = _find_outcome(a_rows, ["1"])
    if a_raw is None and away_team_name:
        up_name = away_team_name.upper()
        for market_name, market_rows in table.markets.items():
            up = market_name.upper()
            if "EXACT GOALS" in up and up_name in up:
                hit = _find_outcome(market_rows, ["1"])
                if hit:
                    a_raw = (hit, market_name); break

    b_raw = None
    b_keys = ("AWAY_TOTAL", "AWAY_TOTAL_GOALS", "AWAY TEAM TOTAL",
              "AWAY TOTAL", "AWAY OVER/UNDER")
    b_rows = next((table.markets[k] for k in b_keys if k in table.markets), [])
    b_raw = _find_outcome(b_rows, ["OVER 1.5"])
    if b_raw is None and away_team_name:
        up_name = away_team_name.upper()
        for market_name, market_rows in table.markets.items():
            up = market_name.upper()
            if "TOTAL" in up and up_name in up and "EXACT" not in up:
                hit = _find_outcome(market_rows, ["OVER 1.5"])
                if hit:
                    b_raw = (hit, market_name); break

    a = _unwrap(a_raw); b = _unwrap(b_raw)
    return {
        "market_a_odds": a["odds"] if a else None,
        "market_b_odds": b["odds"] if b else None,
        "market_a_label": a["label"] if a else None,
        "market_b_label": b["label"] if b else None,
        "market_a_market": a["market"] if a else None,
        "market_b_market": b["market"] if b else None,
    }


# ----------------------------------------------------------------------
# Decision engine
# ----------------------------------------------------------------------
@dataclass
class BetDecision:
    should_bet: bool
    reason: str
    market_a_odds: Optional[float] = None
    market_b_odds: Optional[float] = None
    market_a_label: Optional[str] = None
    market_b_label: Optional[str] = None
    both_gt_05: float = 0.0
    played: int = 0
    threshold: float = 0.0
    min_meetings: int = 0


def _load_match(match_id: str):
    for m in db.list_matches():
        if m.match_id == match_id:
            return m
    return None


def _away(match_id: str) -> Optional[str]:
    """Convenience: the away team name for a fixture id, or None."""
    m = _load_match(match_id)
    return m.away_team if m else None


def evaluate_match(match_id: str, settings: Optional[dict] = None) -> BetDecision:
    settings = settings or {
        "min_odds": float(db.get_setting("min_odds", "2.00")),
        "h2h_both_gt_05": float(db.get_setting("h2h_both_gt_05", "0.60")),
        "h2h_min_meetings": int(db.get_setting("h2h_min_meetings", "3")),
    }

    m = _load_match(match_id)
    odds = get_two_market_odds(match_id, away_team_name=(m.away_team if m else None))
    a, b = odds["market_a_odds"], odds["market_b_odds"]
    if a is None or b is None:
        return BetDecision(
            should_bet=False,
            reason="one of the two required markets is not stored",
            market_a_odds=a, market_b_odds=b,
            market_a_label=odds["market_a_label"], market_b_label=odds["market_b_label"],
        )

    if a < settings["min_odds"] or b < settings["min_odds"]:
        return BetDecision(
            should_bet=False,
            reason=f"both odds must be ≥ {settings['min_odds']:.2f} (got A={a:.2f}, B={b:.2f})",
            market_a_odds=a, market_b_odds=b,
            market_a_label=odds["market_a_label"], market_b_label=odds["market_b_label"],
        )

    m = _load_match(match_id)
    if m is None:
        return BetDecision(
            should_bet=False,
            reason="match not found in archive",
            market_a_odds=a, market_b_odds=b,
            market_a_label=odds["market_a_label"], market_b_label=odds["market_b_label"],
        )

    stats = pair_stats(m.home_team, m.away_team)
    ok, why = meets_threshold(stats, settings["h2h_both_gt_05"], settings["h2h_min_meetings"])
    if not ok:
        return BetDecision(
            should_bet=False, reason=why,
            market_a_odds=a, market_b_odds=b,
            market_a_label=odds["market_a_label"], market_b_label=odds["market_b_label"],
            both_gt_05=stats.both_gt_05,
            played=stats.played,
            threshold=settings["h2h_both_gt_05"],
            min_meetings=settings["h2h_min_meetings"],
        )

    return BetDecision(
        should_bet=True, reason="rule met",
        market_a_odds=a, market_b_odds=b,
        market_a_label=odds["market_a_label"], market_b_label=odds["market_b_label"],
        both_gt_05=stats.both_gt_05,
        played=stats.played,
        threshold=settings["h2h_both_gt_05"],
        min_meetings=settings["h2h_min_meetings"],
    )


def place_bet(match_id: str, stake: Optional[float] = None,
              notes: str = "") -> int:
    stake = stake if stake is not None else float(db.get_setting("stake", "100"))
    decision = evaluate_match(match_id)
    if not decision.should_bet:
        raise ValueError(f"NO BET — reason: {decision.reason}")
    m = _load_match(match_id)
    row = {
        "match_id": match_id,
        "matchday": m.matchday if m else None,
        "season": m.season if m else None,
        "market_a_label": decision.market_a_label or "Away Exact Goals = 1",
        "market_a_odds": decision.market_a_odds,
        "market_a_stake": stake,
        "market_a_result": None,
        "market_b_label": decision.market_b_label or "Away Total OVER 1.5",
        "market_b_odds": decision.market_b_odds,
        "market_b_stake": stake,
        "market_b_result": None,
        "profit_or_loss": 0.0,
        "notes": notes or f"both_gt_05={decision.both_gt_05:.0%} played={decision.played}",
    }
    bet_id = db.insert_ledger_row(row)
    if m and m.ft_away is not None:
        settle_bet(bet_id, int(m.ft_away))
    return bet_id


# ----------------------------------------------------------------------
# Settlement
# ----------------------------------------------------------------------
def settle_bet(bet_id: int, away_goals: int) -> None:
    row = db.query_one("SELECT * FROM edge_ledger WHERE bet_id = ?", (bet_id,))
    if row is None:
        return
    a_odds = float(row["market_a_odds"])
    b_odds = float(row["market_b_odds"])
    a_stake = float(row["market_a_stake"])
    b_stake = float(row["market_b_stake"])

    a_result = "WIN" if away_goals == 1 else "LOSS"
    b_result = "WIN" if away_goals >= 2 else "LOSS"

    a_pnl = (a_odds - 1.0) * a_stake if a_result == "WIN" else -a_stake
    b_pnl = (b_odds - 1.0) * b_stake if b_result == "WIN" else -b_stake
    db.update_ledger_result(bet_id, a_result, b_result, a_pnl + b_pnl)


def settle_all_unsettled() -> int:
    rows = db.list_ledger()
    n = 0
    for r in rows:
        if r["market_a_result"] is not None:
            continue
        m = _load_match(r["match_id"])
        if m is None or m.ft_away is None:
            continue
        settle_bet(r["bet_id"], int(m.ft_away))
        n += 1
    return n


# ----------------------------------------------------------------------
# Performance analytics
# ----------------------------------------------------------------------
@dataclass
class LedgerSummary:
    n_bets: int
    n_a_wins: int
    n_a_losses: int
    n_b_wins: int
    n_b_losses: int
    pnl_a: float
    pnl_b: float
    pnl_total: float
    strike_a: float
    strike_b: float
    roi_a: float
    roi_b: float
    roi_total: float
    max_drawdown: float
    longest_win_streak: int
    longest_loss_streak: int
    bankroll_start: float
    bankroll_a: float
    bankroll_b: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def summarize(starting_bankroll: Optional[float] = None) -> LedgerSummary:
    rows = db.list_ledger()
    starting_bankroll = starting_bankroll if starting_bankroll is not None else float(db.get_setting("bankroll", "10000"))
    settled = [r for r in rows if r["market_a_result"] is not None]

    n = len(settled)
    n_a_wins = sum(1 for r in settled if r["market_a_result"] == "WIN")
    n_a_losses = sum(1 for r in settled if r["market_a_result"] == "LOSS")
    n_b_wins = sum(1 for r in settled if r["market_b_result"] == "WIN")
    n_b_losses = sum(1 for r in settled if r["market_b_result"] == "LOSS")

    pnl_a = sum(
        ((float(r["market_a_odds"]) - 1.0) * float(r["market_a_stake"])) if r["market_a_result"] == "WIN" else -float(r["market_a_stake"])
        for r in settled
    )
    pnl_b = sum(
        ((float(r["market_b_odds"]) - 1.0) * float(r["market_b_stake"])) if r["market_b_result"] == "WIN" else -float(r["market_b_stake"])
        for r in settled
    )
    pnl_total = pnl_a + pnl_b

    total_staked_a = sum(float(r["market_a_stake"]) for r in settled) or 1.0
    total_staked_b = sum(float(r["market_b_stake"]) for r in settled) or 1.0
    total_staked = total_staked_a + total_staked_b

    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    win_streak = 0
    loss_streak = 0
    longest_win = 0
    longest_loss = 0
    for r in settled:
        won_any = (r["market_a_result"] == "WIN") or (r["market_b_result"] == "WIN")
        lost_any = (r["market_a_result"] == "LOSS") and (r["market_b_result"] == "LOSS")
        if won_any and not lost_any:
            win_streak += 1; loss_streak = 0
        elif lost_any:
            loss_streak += 1; win_streak = 0
        longest_win = max(longest_win, win_streak)
        longest_loss = max(longest_loss, loss_streak)
        a_pnl = ((float(r["market_a_odds"]) - 1.0) * float(r["market_a_stake"])) if r["market_a_result"] == "WIN" else -float(r["market_a_stake"])
        b_pnl = ((float(r["market_b_odds"]) - 1.0) * float(r["market_b_stake"])) if r["market_b_result"] == "WIN" else -float(r["market_b_stake"])
        cumulative += a_pnl + b_pnl
        peak = max(peak, cumulative)
        max_dd = max(max_dd, peak - cumulative)

    return LedgerSummary(
        n_bets=n,
        n_a_wins=n_a_wins, n_a_losses=n_a_losses,
        n_b_wins=n_b_wins, n_b_losses=n_b_losses,
        pnl_a=pnl_a, pnl_b=pnl_b, pnl_total=pnl_total,
        strike_a=(n_a_wins / n) if n else 0.0,
        strike_b=(n_b_wins / n) if n else 0.0,
        roi_a=(pnl_a / total_staked_a) if total_staked_a else 0.0,
        roi_b=(pnl_b / total_staked_b) if total_staked_b else 0.0,
        roi_total=(pnl_total / total_staked) if total_staked else 0.0,
        max_drawdown=float(max_dd),
        longest_win_streak=int(longest_win),
        longest_loss_streak=int(longest_loss),
        bankroll_start=float(starting_bankroll),
        bankroll_a=float(starting_bankroll + pnl_a),
        bankroll_b=float(starting_bankroll + pnl_b),
    )


if __name__ == "__main__":
    s = summarize()
    print(s)
