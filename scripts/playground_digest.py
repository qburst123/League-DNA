#!/usr/bin/env python3
"""Print the FT score Prediction Playground digest: pick sheet and engine scorecard.

Reads ``data/playground.json`` (the payload the service serves) and prints markdown tables
you can paste anywhere — the workspace README, a competition thread, or a matchday note.

    python3 scripts/playground_digest.py            # pick sheet + scorecard
    python3 scripts/playground_digest.py --picks    # pick sheet only
    python3 scripts/playground_digest.py --engines  # scorecard only
    python3 scripts/playground_digest.py --write    # rewrite the README digest blocks in place
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAYLOAD = ROOT / "data" / "playground.json"
BOARD_DIR = ROOT.parent / "FT score Prediction Playground"
README = BOARD_DIR / "README.md"

LABEL = {"prior": "League baseline", "elo": "Elo ratings", "poisson": "Dixon-Coles Poisson",
         "markov": "FT sequence (KT backoff)", "knn": "Similar-sequence search",
         "market": "Market-implied", "blend": "Log-loss blend", "hit_blend": "Competition blend"}
ORDER = ["market", "poisson", "markov", "knn", "elo", "prior", "blend", "hit_blend"]


def load(path: Path | None = None) -> dict:
    return json.loads((path or PAYLOAD).read_text())


def picks_table(payload: dict) -> str:
    rows = payload["target"]["rows"]
    lines = ["| fixture (home row) | competition pick | p | confidence | next best | log-loss view |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if row["venue"] != "H":
            continue
        comp = row["competition"]
        blend = row.get("hit_blend") or row["blend"]
        cross = row["blend"]
        nextbest = " / ".join(item["score"] for item in blend["top"][1:3]) or "—"
        lines.append(f"| {row['team']} vs {row['opponent']} | **{comp['pick']}** | {comp['probability']:.1f}% | "
                     f"{comp['confidence']} ({comp['agreement']}/{comp['engines']}) | {nextbest} | {cross['score']} |")
    return "\n".join(lines)


def engine_table(payload: dict) -> str:
    backtest = payload["backtest"]
    lines = ["| engine | log loss | exact hit (95% CI) | top-3 | 1X2 | rows | weight (competition) |",
             "| --- | --- | --- | --- | --- | --- | --- |"]
    for key in ORDER:
        entry = backtest["models"].get(key)
        if not entry:
            continue
        metrics = entry["calibrated"]
        weight = backtest["hit_weights"].get(key)
        lines.append(f"| {LABEL[key]} | {metrics['logloss']:.3f} | **{metrics['hit1']:.1f}%** "
                     f"({metrics['hit1_ci'][0]:.1f}–{metrics['hit1_ci'][1]:.1f}) | {metrics['hit3']:.1f}% | "
                     f"{metrics['result']:.1f}% | {entry['coverage']} | "
                     + (f"{100 * weight:.1f}%" if weight is not None else "—") + " |")
    modal = backtest["baselines"]["modal"]
    lines.append(f"| *always play {modal['score']}* | — | {modal['hit1']:.1f}% | — | — | {modal['n']} | — |")
    return "\n".join(lines)


def confidence_table(payload: dict) -> str:
    table = payload["backtest"].get("confidence") or {}
    agreement = table.get("by_agreement") or []
    lines = ["| engines agreeing | rows | exact hit |", "| --- | --- | --- |"]
    for band in agreement:
        lines.append(f"| {band['agrees']} | {band['n']} | {band['hit1']:.1f}% |")
    return "\n".join(lines)


def band_table(payload: dict) -> str:
    table = payload["backtest"].get("confidence") or {}
    lines = ["| band | rows | exact hit | top-3 |", "| --- | --- | --- | --- |"]
    for band in table.get("bands") or []:
        lines.append(f"| {band['low']:.1f}–{band['high']:.1f}% | {band['n']} | {band['hit1']:.1f}% | {band['hit3']:.1f}% |")
    return "\n".join(lines)


def headline(payload: dict) -> str:
    meta, data, target, backtest = payload["meta"], payload["data"], payload["target"], payload["backtest"]
    bands = (backtest.get("confidence") or {}).get("bands") or []
    return (f"Target matchday **{target['label']}** · {target['fixtures']} fixtures · {target['markets']} with a captured "
            f"market catalogue · build {meta['build_seconds']} s over an archive of **{data['matches']:,} finalized "
            f"results** in {data['matchdays']:,} matchdays across {data['seasons']} seasons. Evaluated rows: "
            f"{backtest['eval_rows']} (fit {backtest['fit_rows']}). Matchday mean: {backtest['baselines']['matchday_mean']} of 8 "
            f"(best {backtest['baselines']['matchday_best']}), modal baseline {backtest['baselines']['modal']['hit1']:.1f}%, "
            f"{len(bands)} confidence bands.")


def write_readme(payload: dict) -> bool:
    if not README.exists():
        return False
    text = README.read_text()
    blocks = {"HEADLINE": headline(payload), "PICKS": picks_table(payload),
              "ENGINES": engine_table(payload), "AGREEMENT": confidence_table(payload),
              "BANDS": band_table(payload)}
    for name, body in blocks.items():
        pattern = re.compile(rf"(<!-- DIGEST:{name} -->\n)(.*?)(\n<!-- /DIGEST:{name} -->)", re.S)
        if not pattern.search(text):
            print(f"  ! no DIGEST:{name} block in {README}")
            continue
        text = pattern.sub(lambda match, body=body: match.group(1) + body + match.group(3), text, count=1)
    README.write_text(text)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=None)
    parser.add_argument("--picks", action="store_true")
    parser.add_argument("--engines", action="store_true")
    parser.add_argument("--write", action="store_true", help="rewrite the README digest blocks")
    args = parser.parse_args()
    payload = load(args.file)
    if args.write:
        ok = write_readme(payload)
        print("readme digest updated" if ok else "readme not found")
        return 0 if ok else 1
    if args.picks or not (args.picks or args.engines):
        print(headline(payload))
        print()
        print(picks_table(payload))
        print()
    if args.engines or not (args.picks or args.engines):
        print(engine_table(payload))
        print()
        print(confidence_table(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
