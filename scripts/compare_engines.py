#!/usr/bin/env python3
"""Measure a candidate engine against the same archive snapshot, with and without it.

The point of an ensemble is that its members disagree usefully, and the only honest test of a new
engine is a walk-forward run on identical data with and without it. This script runs both, prints
the headline numbers and the fitted weights, and leaves the decision to the numbers: an engine that
does not improve the mixture does not ship.

    python3 scripts/compare_engines.py                      # the current candidate (h2h)
    python3 scripts/compare_engines.py --candidate h2h --window 800

Measured on 2026-09-18 (revision 20900-ish, 640 evaluated rows): adding `h2h` moved the competition
mixture from 17.2% to 15.9% exact hits and the log-loss blend from 2.748 to 2.755 — worse on both —
so `h2h` stays a candidate and is not in `MODEL_CARDS`.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import playground as pg            # noqa: E402
from backend.store import Store                 # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data"


def evaluate(store, history, season, window, models):
    original = pg.BACKTEST_MODELS
    pg.BACKTEST_MODELS = models
    try:
        return pg.backtest(store, history, window=window, season=season)
    finally:
        pg.BACKTEST_MODELS = original


def summarise(label, report):
    blends = report.get("models") or {}
    hit = (blends.get("hit_blend") or {}).get("calibrated") or {}
    logloss = (blends.get("blend") or {}).get("calibrated") or {}
    champion = report.get("champion")
    return {"label": label, "champion": champion,
            "champion_hit1": ((blends.get(champion) or {}).get("calibrated") or {}).get("hit1"),
            "champion_logloss": ((blends.get(champion) or {}).get("calibrated") or {}).get("logloss"),
            "competition_hit1": hit.get("hit1"), "competition_top3": hit.get("hit3"),
            "competition_logloss": hit.get("logloss"),
            "logloss_hit1": logloss.get("hit1"), "logloss_hit3": logloss.get("hit3"),
            "logloss_logloss": logloss.get("logloss"),
            "weights": report.get("hit_weights"), "eval_rows": report.get("eval_rows")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", default="h2h", choices=list(pg.CANDIDATE_MODELS))
    parser.add_argument("--window", type=int, default=800)
    parser.add_argument("--season", type=int, default=None)
    args = parser.parse_args()

    store = Store(DATA / "league.sqlite")
    history = pg.load_history(store)
    season = args.season or (history[-1].season if history else None)
    shipped = tuple(pg.BACKTEST_MODELS)
    candidate = shipped + (args.candidate,)
    print(f"archive revision {store.version} · {len(history)} recorded fixtures · season {season} · "
          f"window {args.window} · candidate {args.candidate}")
    without = evaluate(store, history, season, args.window, shipped)
    with_candidate = evaluate(store, history, season, args.window, candidate)
    first, second = summarise(f"without {args.candidate}", without), summarise(f"with {args.candidate}", with_candidate)
    for entry in (first, second):
        print(json.dumps(entry, indent=1))
    better = (second["competition_hit1"] or 0) > (first["competition_hit1"] or 0) and \
             (second["competition_logloss"] or 9) <= (first["competition_logloss"] or 9)
    print(f"\nverdict: {'ship it' if better else 'leave it as a candidate'} — "
          f"competition exact hits {first['competition_hit1']} -> {second['competition_hit1']}, "
          f"log loss {first['competition_logloss']} -> {second['competition_logloss']}")
    store.close()


if __name__ == "__main__":
    main()
