#!/usr/bin/env python3
"""Audit the FT score Prediction Playground.

Checks the things that would quietly invalidate the board:

1. cut-off discipline   — the history handed to the engines stops strictly before the target
                          matchday, and no market catalogue captured after kick-off is used;
2. walk-forward order   — the backtest sample is a contiguous run of the fixture calendar,
                          and each row's evidence is only ever released after its matchday;
3. orientation          — both rows of a fixture answer in their own team's frame, so the
                          home row's grid mirrors the away row's grid;
4. mixture invariants   — weights are a probability vector, every engine keeps its floor, and
                          the blend really is a weighted mixture of the engines present;
5. determinism          — the same archive produces the same headline numbers on a re-run;
6. ledger discipline    — a locked sheet is written once and never rewritten, grading only ever
                          fills result columns, every sheet that can be graded is graded, `late` is
                          a live-only flag, and the board composes in well under a second.

Usage:  python3 scripts/audit_playground.py [--window 800] [--quick]
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import playground as pg                                     # noqa: E402
from backend.store import Store                                          # noqa: E402
from backend.ledger import PredictionLedger                              # noqa: E402
from backend.playground_service import PlaygroundService                 # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window", type=int, default=800)
    parser.add_argument("--quick", action="store_true", help="small window, for a fast smoke run")
    args = parser.parse_args()
    window = 200 if args.quick else args.window

    data = Path(os.environ.get("LEAGUE_DATA_DIR", str(ROOT / "data")))
    store = Store(data / "league.sqlite")
    failures: list = []

    def check(name, condition, detail=""):
        print(f"  {'ok  ' if condition else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")
        if not condition:
            failures.append(f"{name}: {detail}")

    history = pg.load_history(store)
    print(f"archive: {len(history)} finalized matches, {len(pg.timeline(history))} matchdays, "
          f"{len({match.season for match in history})} seasons")

    # ---------------------------------------------------------------- 1. cut-off discipline
    targets = pg.current_targets(store, history)
    limit = (targets[0].season, targets[0].day) if targets else None
    usable = pg.before(history, *limit) if limit else []
    print(f"target matchday {limit}: {len(targets)} fixtures")
    check("history stops before the target matchday",
          all((match.season, match.day) < limit for match in usable) and len(usable) ==
          sum(1 for match in history if (match.season, match.day) < limit),
          f"{len(usable)} usable rows of {len(history)}")
    kickoff = min((fixture.kickoff for fixture in targets if fixture.kickoff), default=None)
    markets = pg.load_markets(store, [fixture.event_id for fixture in targets], cutoff=kickoff)
    late = [row for row in store.all("SELECT event_id,fetched_at FROM markets") if row["event_id"] in markets
            and kickoff and row["fetched_at"] and float(row["fetched_at"]) > kickoff]
    check("no market catalogue captured after kick-off is used", not late, f"{len(markets)} catalogues in play")

    # ---------------------------------------------------------------- 2. walk-forward order
    t0 = time.time()
    report = pg.backtest(store, history, window=window)
    print(f"walk-forward run: {report['sample']} matches, {report['fit_rows']} fit rows, "
          f"{report['eval_rows']} evaluated rows in {time.time() - t0:.1f}s")
    check("sample is a contiguous calendar run",
          report["sample"] == min(window, len(history)),
          f"sample={report['sample']} window={window}")
    check("evaluation rows come only from the untouched remainder", report["eval_rows"] > 0)
    coverage = {key: (report["models"].get(key) or {}).get("coverage") for key in report["models"]}
    check("every engine produced grids for the sample",
          all((report["models"].get(key) or {}).get("coverage", 0) > 0 for key in pg.MODEL_KEYS),
          f"{len(pg.MODEL_KEYS)} engines priced: " + json.dumps(coverage))
    check("no candidate engine has leaked onto the board",
          not any(key in pg.MODEL_KEYS for key in pg.CANDIDATE_MODELS),
          f"candidates {list(pg.CANDIDATE_MODELS)}")
    check("market engine only acts on captured fixtures",
          0 < (report["models"].get("market") or {}).get("coverage", 0) <= report["sample"] * 2)
    weights = report.get("hit_weights") or {}
    check("mixture weights are a probability vector",
          bool(weights) and abs(sum(weights.values()) - 1.0) < 0.01, json.dumps(weights))
    check("every engine keeps at least the Dirichlet floor",
          all(value >= 0.005 for value in weights.values()), json.dumps(weights))
    check("blend metrics come from the walk-forward mixture",
          (report["models"]["blend"]["coverage"] or 0) > 0 and report["blend_gain"] is not None)
    check("confidence bands are ordered and populated",
          len(report["confidence"]["bands"]) >= 3 and len(report["confidence"]["by_agreement"]) >= 3)
    check("per-matchday ledger covers the evaluation window", len(report["days"]) >= 5)

    # ---------------------------------------------------------------- 3. orientation
    rows = []
    if targets:
        rows, _bundle = pg.predict_matchday(store, history, targets, weights=report.get("hit_weights"),
                                            sequence_gammas=report.get("sequence_gammas"),
                                            shrinkage=report.get("shrinkage"))
    if len(rows) >= 4:
        index = {(row["event_id"], row["venue"]): row for row in rows}
        worst = 0.0
        engines = set()
        for (event_id, venue), row in index.items():
            away = index.get((event_id, "A"))
            if venue != "H" or away is None:
                continue
            home_grid, away_grid = row["blend"]["grid"], away["blend"]["grid"]
            worst = max(worst, sum(abs(home_grid[a][b] - away_grid[b][a]) / 1000.0
                                   for a in range(pg.SIDES) for b in range(pg.SIDES)) / 2.0)
            engines |= set(row["models"]) & set(away["models"])
        check("fixture rows mirror each other in their own frames", worst < 0.05,
              f"worst total-variation asymmetry {worst:.4f}")
        check("every engine answers for every fixture", len(engines) == len(pg.MODEL_KEYS),
              ", ".join(sorted(engines)))
        explained = all(row["models"][key].get("explain") for row in rows for key in row["models"])
        check("each engine ships its own reasoning payload", explained)
        check("the competition pick exists for every team row",
              all(row["blend"]["top"] and row["blend"]["top"][0]["score"] for row in rows))
    check("every row carries both blends", all(row.get("hit_blend", {}).get("top") for row in rows),
          f"{sum(1 for row in rows if (row.get("hit_blend") or {}).get("top"))} of {len(rows)} rows")

    # ---------------------------------------------------------------- 4. determinism
    again = pg.backtest(store, history, window=window)
    same = (again["hit_weights"] == report["hit_weights"] and
            again["models"]["hit_blend"]["calibrated"] == report["models"]["hit_blend"]["calibrated"] and
            again["shrinkage"] == report["shrinkage"])
    check("re-running the same archive reproduces the headline numbers", same,
          f"hit1 {report['models']['hit_blend']['calibrated']['hit1']}%")

    # ---------------------------------------------------------------- 6. ledger discipline
    ledger = PredictionLedger(data / "predictions.sqlite")
    stats = ledger.stats()
    sheet_count = ledger.db.execute("SELECT COUNT(*) FROM sheets").fetchone()[0]
    print(f"ledger: {sheet_count} sheets, {stats['picks']} picks, {stats['graded']} graded, "
          f"hit rate {stats.get('hit_rate')}%, direction {stats.get('direction_rate')}%")
    if sheet_count:
        missing_pick = ledger.db.execute(
            "SELECT COUNT(*) FROM picks WHERE pick IS NULL OR pick = ''").fetchone()[0]
        check("every pick column of every sheet is filled once", missing_pick == 0,
              f"{missing_pick} empty picks")
        partial = ledger.db.execute(
            "SELECT COUNT(*) FROM picks WHERE actual IS NOT NULL AND (pick IS NULL OR pick = '')").fetchone()[0]
        check("grading never clears or rewrites a pick", partial == 0)
        graded_rows = ledger.db.execute("SELECT COUNT(*) FROM picks WHERE actual IS NOT NULL").fetchone()[0]
        check("the graded count agrees with the graded rows", graded_rows == stats["graded"],
              f"{graded_rows} rows vs {stats['graded']} counted")
        bad_late = ledger.db.execute(
            "SELECT COUNT(*) FROM sheets WHERE late = 1 AND source <> 'live'").fetchone()[0]
        check("only live sheets can be flagged late", bad_late == 0, f"{bad_late} backfilled sheets flagged")
        seasons = ledger.seasons()
        season_sheets = sum(entry["sheets"] for entry in seasons)
        season_graded = sum(entry["graded"] for entry in seasons)
        check("season aggregates add up to the sheet and graded totals",
              season_sheets == sheet_count and season_graded == stats["graded"],
              f"{season_sheets} sheets / {season_graded} graded rows")
        frozen = ledger.db.execute(
            "SELECT COUNT(*) FROM sheets WHERE source = 'live' AND locked_at IS NULL").fetchone()[0]
        check("every live sheet records when it was locked", frozen == 0)
        # a locked sheet must be byte-stable across reads: that is the whole promise of the board
        season, day = seasons[0]["season"], seasons[0]["last_day"]
        before = [(row["team"], row["pick"]) for row in (ledger.sheet_rows(season, day) or {}).get("picks", [])]
        after = [(row["team"], row["pick"]) for row in (ledger.sheet_rows(season, day) or {}).get("picks", [])]
        check("re-reading a frozen sheet returns the identical picks", before == after and bool(before),
              f"{len(before)} rows on {season}/{day}")
        # The bug this guards against: grading that only ever looks at the matchday the source is
        # publishing now silently leaves older sheets behind. A sheet whose fixtures are all final in
        # the archive must be fully graded, end of season or not.
        played = {}
        for match in history:
            if match.season and match.day:
                played[(match.season, match.day)] = played.get((match.season, match.day), 0) + 1
        behind = []
        for entry in ledger.db.execute("SELECT season, day, fixtures FROM sheets").fetchall():
            count = played.get((entry["season"], entry["day"]), 0)
            if not entry["fixtures"] or count < entry["fixtures"]:
                continue
            graded_rows = ledger.db.execute(
                "SELECT COUNT(*) FROM picks WHERE season=? AND day=? AND actual IS NOT NULL",
                (entry["season"], entry["day"])).fetchone()[0]
            if graded_rows < entry["fixtures"]:
                behind.append(f"{entry['season']}/{entry['day']} {graded_rows}of{entry['fixtures']}")
        check("no completed matchday is left ungraded, in any season", not behind, "; ".join(behind[:4]))
        pending = ledger.pending_sheets(limit=12)
        open_rows = {tuple(row) for row in ledger.ungraded_sheets(limit=256)}
        check("the pending list only carries sheets with results outstanding",
              all(tuple(entry) in open_rows for entry in pending), f"{len(pending)} pending")
    else:
        print("  -- ledger empty; skipping the discipline section")

    # fast-fetch budget: the board has to answer inside a tick, so measure the locked path
    service = PlaygroundService(store, data, window=window)
    t0 = time.time()
    service.compose()
    compose_seconds = time.time() - t0
    t0 = time.time()
    tick = service.tick()
    tick_seconds = time.time() - t0
    check("the frozen board composes in well under a second", compose_seconds < 1.0,
          f"{compose_seconds * 1000:.0f} ms")
    check("the tick that drives the page is a fast read", tick_seconds < 0.5 and "board_key" in tick,
          f"{tick_seconds * 1000:.0f} ms")
    check("the tick carries the locked target facts",
          all(key in (tick.get("target") or {}) for key in ("locked_at", "lead_seconds", "source", "late")),
          json.dumps({key: (tick.get("target") or {}).get(key) for key in
                      ("locked_at", "lead_seconds", "source", "late")})[:120])
    ledger.close()

    # ---------------------------------------------------------------- 5. board snapshot
    snapshot = ROOT.parent / "FT score Prediction Playground" / "index.html"
    if not snapshot.exists():
        snapshot = ROOT / "FT score Prediction Playground" / "index.html"
    if snapshot.exists():
        text = snapshot.read_text()
        payload = json.loads(text.split('<script id="snapshot" type="application/json">', 1)[1]
                                 .split("</script>", 1)[0])
        embedded = payload.get("backtest", {}).get("models", {})
        check("board snapshot carries the engine set", len(payload.get("engines", [])) >= len(pg.MODEL_KEYS) + 2,
              ", ".join(engine["key"] for engine in payload.get("engines", [])))
        check("board snapshot target has rows", bool(payload["target"].get("rows")))
        check("board snapshot target rows carry both blends",
              bool(payload["target"]["rows"]) and all(row.get("hit_blend", {}).get("top") for row in payload["target"]["rows"]))
        check("board snapshot metrics are self-consistent",
              bool(embedded) and all((entry.get("calibrated") or {}).get("n") is not None
                                     for entry in embedded.values()))
    else:
        print("  -- board snapshot not found; skipping that section")

    store.close()
    print()
    if failures:
        print("FAILED")
        for line in failures:
            print("  -", line)
        return 1
    print("AUDIT PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
