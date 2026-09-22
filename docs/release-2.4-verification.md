# v2.4 verification record — ongoing-season replay and the continuation ledger

Captured against the retained database in `data/league.sqlite` while the live collector was running
(`run.py`, source `virtuals.betika.com`). Every number below comes from recorded Betika rows only;
nothing is generated or interpolated.

## What changed

| Item | Behaviour before | Behaviour now |
| --- | --- | --- |
| 1 · ongoing season as a replay target | only 30/30 seasons earlier than the incoming season could be selected | any season with at least one finalized matchday is selectable, including the ongoing season; future seasons and seasons without a prefix stay rejected |
| 2 · ongoing season updates | not applicable | the ongoing target opens at its finalized prefix, the target range grows with the collector, the next round is labeled **awaiting FT**, and the workspace auto-refreshes (8 s poll + SSE push, **Refresh now** on demand) |
| 3 · live FT board | explicitly *data display only* | each of the 16 teams carries earlier-season continuation picks for its own finalized FT prefix |
| 4 · picks row | none | a ledger row under every team row grades each matchday `hit` / `miss` / `pending` / `no_pick` (blank before kick-off), with per-team summary and a click-through evidence modal |

Reference policy is unchanged and enforced in both implementations: evidence may only come from seasons
**strictly earlier than the target season**, the target season is never its own evidence, and a pick must
appear in at least two distinct recorded source matches.

## Live evidence at capture time

- target season `3135588`, finalized prefix `MD1–13`, incoming matchday 14
- scope **all 16 teams**: 172 picks, 23 exact FT hits, 149 wrong FT, 21 matchdays with no qualifying evidence
- scope **same team only**: 87 picks, 14 exact FT hits, 73 wrong FT, 116 matchdays with no qualifying evidence
- ledger rows rendered: 16 team rows + 16 ledger rows, 172 graded cells, 15 pending cells, 21 no-evidence cells
- replay mode identifier `season-replay`, engine `earlier-season-continuation-v1`, ledger cached per finalized-FT revision

The exact-score hit rate is a retrospective repetition measurement on sparse samples. It is displayed with
the word *retrospective* in the interface and is not a probability, a confidence score or a wagering signal.

## Checks executed

```bash
python3 -m pytest -q                      # 113 passed
python3 scripts/audit_replay.py           # 40 cases incl. the ongoing prefix, errors []
python3 scripts/audit_forecast.py         # every ledger row recomputed independently, errors []
node scripts/check_client.mjs <snapshot>  # 10 comparisons + 4 blueprints, failures []
python3 tests/browser_replay.py           # ongoing-season replay, live board, ledger rows, mobile
python3 tests/browser_smoke.py            # unchanged workspaces
python3 scripts/build_app.py              # League-DNA.html rebuilt with the ledger embedded
```

`scripts/audit_forecast.py` (new) re-derives every pick with a deliberately naive scan, re-checks every
verdict against the stored FT, verifies the trim decision and the two-match floor, re-adds the per-team
counters, and proves that changing every later season's FT values leaves an earlier target's ledger
byte-identical.

`tests/browser_v2.py` is environment-bound: in this 2 GB sandbox the full run needs the browser launched
with `--disable-dev-shm-usage` and the 14 MB `srcdoc` phase needs a long timeout; both phases were
verified separately (`/tmp/phases.py` semantics) and the standalone file was additionally verified
end-to-end offline, including the new board (16 rows, 16 ledger rows, 172 graded cells).

## Artifacts

- `artifacts/live-ft-board.png` — live board with the ledger row under every team
- `artifacts/live-ft-board-mobile.png`, `artifacts/replay-mobile.png` — 390 px layouts
- `artifacts/replay-desktop.png`, `artifacts/replay-dark.png` — replay tab, light and dark
- `artifacts/live-ft-board-standalone.png` — the offline `League-DNA.html` snapshot
- `artifacts/replay-browser-report.json`, `artifacts/forecast-audit.json` — machine-readable results
