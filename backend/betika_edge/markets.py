"""
markets.py — Market normalisation (raw / no-vig / fair probabilities).

`db.list_markets_for_match(match_id)` returns `MarketRow` dataclasses
with the same fields as before, so this layer keeps working unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import db


# ----------------------------------------------------------------------
# Dataclasses
# ----------------------------------------------------------------------
@dataclass
class MarketRow:
    match_id: str
    market_id: int
    market_name: str
    outcome_label: str
    decimal_odds: float
    raw_implied: float
    overround: float
    no_vig: float
    margin: float
    sub_type_id: Optional[int] = None
    special_bet_value: Optional[str] = None

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class NoVigTable:
    match_id: str
    markets: dict[str, list[MarketRow]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {m: [r.to_dict() for r in rows] for m, rows in self.markets.items()}


# ----------------------------------------------------------------------
# Core calculation
# ----------------------------------------------------------------------
def no_vig_table(match_id: str) -> NoVigTable:
    rows = db.list_markets_for_match(match_id)
    table = NoVigTable(match_id=match_id)
    grouped: dict[str, list] = {}
    for r in rows:
        grouped.setdefault(r.market_name, []).append(r)

    for market_name, items in grouped.items():
        raw = [1.0 / float(r.decimal_odds) for r in items if r.decimal_odds]
        overround = float(sum(raw)) if raw else 1.0
        out_rows: list[MarketRow] = []
        for r, p in zip(items, raw):
            nv = p / overround if overround > 0 else p
            out_rows.append(MarketRow(
                match_id=match_id,
                market_id=int(r.market_id or 0),
                market_name=market_name,
                outcome_label=str(r.outcome_label),
                decimal_odds=float(r.decimal_odds),
                raw_implied=float(p),
                overround=float(overround),
                no_vig=float(nv),
                margin=float(p - nv),
                sub_type_id=r.sub_type_id,
                special_bet_value=r.special_bet_value,
            ))
        table.markets[market_name] = out_rows
    return table


# ----------------------------------------------------------------------
# Cross-market consistency
# ----------------------------------------------------------------------
def cross_market_flags(table: NoVigTable, threshold: float = 0.03) -> list[dict]:
    flags: list[dict] = []

    def _one(market_name: str, label: str) -> Optional[float]:
        rows = table.markets.get(market_name, [])
        for r in rows:
            if r.outcome_label.strip().lower() == label.strip().lower():
                return r.no_vig
        return None

    h1 = _one("1X2", "1")
    a1 = _one("1X2", "2")
    h_dnb = _one("DRAW_NO_BET", "1")
    a_dnb = _one("DRAW_NO_BET", "2")
    if (h1 is not None and a1 is not None and (h1 + a1) > 0
            and h_dnb is not None and a_dnb is not None):
        share_1x2 = h1 / (h1 + a1)
        share_dnb = h_dnb / (h_dnb + a_dnb) if (h_dnb + a_dnb) > 0 else 0
        diff = abs(share_1x2 - share_dnb)
        if diff > threshold:
            flags.append({
                "kind": "1X2 vs DNB",
                "detail": f"1X2 home share={share_1x2:.1%}  DNB home share={share_dnb:.1%}  diff={diff:.1%}",
                "diff": diff,
            })

    o25 = _one("TOTAL", "OVER 2.5")
    exact3plus = None
    for k, rows in table.markets.items():
        if k.upper().startswith("EXACT_GOALS") or k.upper().startswith("TOTAL_GOALS"):
            for r in rows:
                if r.outcome_label in {"3", "4", "5", "6", "3+", "4+", "5+", "6+"}:
                    exact3plus = (exact3plus or 0.0) + r.no_vig
    if o25 is not None and exact3plus is not None:
        diff = abs(o25 - exact3plus)
        if diff > threshold:
            flags.append({
                "kind": "TOTAL O2.5 vs ExactGoals ≥3",
                "detail": f"O2.5={o25:.1%}  ExactGoals≥3={exact3plus:.1%}  diff={diff:.1%}",
                "diff": diff,
            })

    btts = _one("BTTS", "YES")
    cs_11plus = None
    for k, rows in table.markets.items():
        if k.upper().startswith("CORRECT_SCORE"):
            for r in rows:
                try:
                    a_str, b_str = r.outcome_label.replace(":", "-").split("-")
                    a, b = int(a_str), int(b_str)
                    if a >= 1 and b >= 1:
                        cs_11plus = (cs_11plus or 0.0) + r.no_vig
                except Exception:
                    pass
    if btts is not None and cs_11plus is not None:
        diff = abs(btts - cs_11plus)
        if diff > threshold:
            flags.append({
                "kind": "BTTS YES vs CorrectScore ≥1-1",
                "detail": f"BTTS={btts:.1%}  CS≥1-1={cs_11plus:.1%}  diff={diff:.1%}",
                "diff": diff,
            })
    return sorted(flags, key=lambda f: -f["diff"])


# ----------------------------------------------------------------------
# Value-bet detection
# ----------------------------------------------------------------------
@dataclass
class ValueBet:
    match_id: str
    market_name: str
    outcome_label: str
    decimal_odds: float
    no_vig_prob: float
    fair_odds: float
    expected_value: float
    edge: float
    kelly_stake_fraction: float

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def value_bets(match_id: str, min_edge: float = 0.05,
               kelly_fraction: float = 0.25,
               fair_source: Optional[dict[str, float]] = None) -> list[ValueBet]:
    table = no_vig_table(match_id)
    bets: list[ValueBet] = []
    for market_name, rows in table.markets.items():
        probs = [r.no_vig for r in rows]
        if fair_source:
            for i, r in enumerate(rows):
                if r.outcome_label in fair_source:
                    probs[i] = fair_source[r.outcome_label]
            total = sum(probs)
            if total > 0:
                probs = [p / total for p in probs]
        for r, p in zip(rows, probs):
            ev = p * r.decimal_odds - 1.0
            if ev >= min_edge:
                fair_odds = 1.0 / p if p > 0 else 999.0
                b = r.decimal_odds - 1.0
                q = 1.0 - p
                full_kelly = ((b * p) - q) / b if b > 0 else 0.0
                stake_frac = max(0.0, full_kelly * kelly_fraction)
                bets.append(ValueBet(
                    match_id=match_id,
                    market_name=market_name,
                    outcome_label=r.outcome_label,
                    decimal_odds=r.decimal_odds,
                    no_vig_prob=float(p),
                    fair_odds=float(fair_odds),
                    expected_value=float(ev),
                    edge=float(ev),
                    kelly_stake_fraction=float(stake_frac),
                ))
    return sorted(bets, key=lambda b: -b.edge)
