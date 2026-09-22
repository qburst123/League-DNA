# FT score Prediction Playground

`backend/playground.py` is a self-contained, standard-library-only prediction lab for the
Sakata FT-score game. `backend/ledger.py` is the frozen prediction ledger it writes into,
`backend/playground_service.py` keeps both in sync and caches the output, `backend/app.py`
serves it, and the standalone board lives in the workspace folder `FT score Prediction
Playground/` next to the project.

## The promise the ledger makes

A prediction is only worth looking at if it is the prediction that was actually made. So:

* **a matchday's sheet is locked once, when the matchday is announced, and never rewritten** —
  the pick, the probability, the engine votes and the matchday's fixtures are written a single
  time into `data/predictions.sqlite`;
* **grading only ever fills result columns** (`actual`, `verdict`, the recorded FT score). No
  code path writes into a `pick` column after the sheet exists, which is why a matchday that was
  played an hour ago shows the same numbers it showed before kick-off;
* **the sheet for the next matchday is locked the moment the source announces it**, in the
  background sync loop, and the lock records how long before the first kick-off it happened
  (`lead_seconds`). A lock made before kick-off is `source='live'`; a sheet reconstructed from
  the archive for a matchday that was already played is `source='backfill'` and is labelled as
  such on the board;
* **every ungraded sheet is graded on every tick, across all seasons**, not only the matchday the
  collector is currently watching, so a season that ends mid-run still finishes its grading.

## What it produces

One payload (`data/playground.json`, ~500 KB) plus the ledger it is composed from:

| key | contents |
| --- | --- |
| `meta` | build time, protocol description, the cache key and board key the payload was built for |
| `data` | archive size, season/matchday counts, revision |
| `engines` | cards: family, blurb, assumptions, strengths, limits, coverage, raw and calibrated metrics, calibration, exponent, mixture weight |
| `backtest` | walk-forward report: per-engine metrics with Wilson intervals, mixture weights and their history, per-matchday ledger, confidence bands, reliability curve, baselines |
| `target` | the matchday set to play: fixture rows with per-engine grids, reasoning payloads, the blend and the competition pick — plus `locked_at`, `lead_seconds`, `source` and `late`, read from the frozen sheet |
| `live_board` | the board rows: recorded FT scores, the frozen picks for the same matchdays, per-team and season totals |
| `ledger` | ledger health: `stats`, `season_stats`, `seasons`, `integrity`, `version`, `sync`, `note` |

The sheets themselves live in **`data/predictions.sqlite`** (`sheets` + `picks`), so the record of
every season survives a restart, a rebuild and a package. The unplayed matchday rows the board
renders are cached in `data/live-sheets.json`, and the walk-forward report in `data/backtest.json`.

## The three workspaces inside the app

All three read the same ledger and the same `/api/playground` payload, so the board, the season
sheets, the engines view and the JSON always agree.

### 1. FT score Prediction Playground — the live board (`#playground`)

A 16-team × MD1–30 board in the shape of the Historical Replay Lab's Live FT board:

* every team has the row of **recorded full-time scores** for the ongoing season, and directly
  beneath it the **prediction the mixture locked for the same matchday**: green for an exact FT
  score, amber for the right result, red for a miss, purple for a matchday still to play;
* the pick in each cell is the **frozen** one — reload the page, wait through a matchday, install a
  new build: the played matchdays keep the exact pick they were locked with, which is what makes
  "did the predictor get this one right?" a question with an answer;
* the board **predicts the matchday set to play**, and every other unplayed matchday the store
  knows about, because the source can open the next round before the previous one is recorded;
  those columns carry pending predictions instead of a gap;
* each cell carries the source of its sheet (live or backfilled) and, for live sheets, the lock
  lead, so a late lock is visible rather than hidden;
* the scope switch regrades the identical matchdays with the **competition blend** (fitted on
  exact hits) or the **log-loss blend** (fitted on probability) — the only way to see the two
  objectives disagree on the same evidence;
* the fixture strip lists the incoming matchday: pick, probability, agreement split, top three
  scores, the blended home/draw/away split, whether the lock was in time and whether a market
  catalogue was captured;
* every cell opens its evidence: the pick, its probability, how many engines agreed, the recorded
  FT score, the team's season record and the mixture weights behind it.

### 2. Season sheets (`#playground`, second tab)

The stored record, browsable exactly as asked for:

* a **season picker** — every season in the ledger, newest first, with its sheet count;
* a **matchday strip** (`MD1…MDn`, played days filled, the pending day marked) so a season can be
  walked matchday by matchday;
* the **sheet table** for the selected matchday: the locked pick, its probability, every engine's
  vote and its probability, the recorded FT score, the verdict (exact / direction / miss /
  pending) and the top-three scores the engines ranked;
* the **team table** for the season: picks, graded, exact hits, direction hits, misses and the
  mean probability the board claimed — per team, which is where a systematically over-confident or
  under-confident team shows up;
* the freeze explainer and a facts bar (sheets, live, backfilled, graded, exact, direction,
  on-time share), so the numbers on screen state their own provenance;
* served by `GET /api/playground/history?season=` (all rows of a season, ~70 KB) and
  `GET /api/playground/sheet?season=&day=` (one sheet, 404 if it was never locked).

### 3. Score engines & explainability (`#playground-engines`)

The engines view:

* engine cards: what each model assumes, where it is strong, where it breaks, its coverage, its
  calibration and its mixture weight — an engine that cannot price the matchday keeps its card and
  its contribution slot and states why it abstains;
* the walk-forward scorecard with Wilson intervals, the naive always-play baseline and both
  mixtures' weights;
* the explainability space per fixture and engine: the processing chain (weight, the probability
  that engine gives the blend's pick, shrinkage, sequence exponent, reference), the full 8×8 score
  grid, the input values that produced it and the recorded evidence behind the sequence engines;
* the contribution chart: mixture weight × the probability an engine gives the pick, renormalised,
  which is why a blend can play a score no single engine ranks first;
* per-matchday exact-hit ledger, confidence bands, the agreement table and the mixture-weight
  history of the last refits;
* the **reliability chart** (\"does the probability mean anything?\"): claimed probability against
  the exact-hit rate that followed, with the perfect-calibration diagonal, a windowed axis and the
  per-band gap table;
* the **mixture-weights chart**: one line per engine for every matchday refit, thick for the
  competition mixture and thin for the log-loss mixture, with the movement table beside it;
* protocol and caveats, including the freshness of the payload currently on screen.

## Refresh: a tick, not a rebuild

The page must answer instantly and never show a stale pick. So the browser's fast path is a
**tick**, and the heavy work never touches it:

| layer | cadence | cost |
| --- | --- | --- |
| `GET /api/playground/tick` | every 4 s (paused while the tab is hidden) | ~2 ms, ~0.5 KB: `board_key`, the target's lock facts, graded counts, sync and build state |
| full `GET /api/playground` | only when the tick's `board_key` moves | ~140 ms, ~46 KB gzipped, ETag/304 |
| `GET /api/playground/history` | season tab open, or the board key moves | ~12 ms, ~72 KB |
| background sync (`PlaygroundService.sync`) | every 5 s in the app loop | ~1 s of SQLite work: lock the announced matchday, grade every ungraded sheet, refresh the pending rows |
| background rebuild (`ensure`) | every 9th loop, throttled to 240 s, 45 s after a target change, first one 25 s after boot | ~55 s walk-forward refit; the last good payload stays on screen throughout |

`board_key` is `ledger version | archive revision | target matchday`, so any event that changes
what the board should say — a new sheet, a graded result, the source moving on — moves the tick,
and the board reloads its data on that beat. Nothing on the board is recomputed from the engines
while a matchday is being played; the picks come out of the ledger, and the ledger is written once.

## Engines

| key | model |
| --- | --- |
| `prior` | league score frequencies |
| `elo` | chronological Elo (goal-margin weighted) mapped to expected goals |
| `poisson` | Dixon-Coles: IPF-fitted attack/defence, home factor, fitted `rho`, matchday decay |
| `markov` | KT-smoothed (alpha 0.5) next-score backoff over orders 3→2→1, floors 2/3/4 |
| `knn` | similarity- and recency-weighted historical team sequences, top 120 neighbours |
| `market` | captured correct-score prices, de-vigged, OTHER rebuilt, reconciled to 1X2 and 2.5 |
| `blend` | mixture fitted by MAP-EM on log loss |
| `hit_blend` | the same engines mixed by multiplicative coordinate ascent on `hit1 + 0.35·hit3 − 0.02·logloss` |

The two sequence engines are **likelihood-ratio rescorings** of the reference shape, not stand-alone distributions:

```
P(score) ∝ reference(score) · ( evidence(score) / marginal(score) ) ** gamma
```

with `gamma` fitted on the first 60% of the walk-forward run (0.0 on the current archive, i.e.
the evidence does not beat the statistical shape and the engines fall back to it). Rescoring
happens in the row team's own frame, which is what makes the two rows of a fixture mirror.

### Adding an engine — the candidate lane

An engine is only added to the board if it earns its place on identical data. `h2h` (a pairing's own
meetings at this venue, shrunk into the league prior) is implemented in `backend/playground.py` and
listed in `CANDIDATE_MODELS`, and `scripts/compare_engines.py` runs the walk-forward twice, with and
without it. On the 2026-09-18 archive (640 evaluated rows) adding it moved the competition mixture
from **17.2% to 15.9%** exact hits and the log-loss blend from **2.748 to 2.755** — worse on both —
so it stays a candidate and is deliberately **not** in `MODEL_CARDS` and not on the board.

## Protocol

* Timeline is the fixture calendar `(season, day, id)`; a Sakata season lasts about two hours of
  wall-clock, so every engine ages evidence in **matchdays** (`HALF_LIFE_MATCHDAYS = 45`).
  `start_time` exists for a minority of rows and is never used for ordering.
* The sequence library and Elo table are released matchday by matchday; Poisson is refitted
  every 3 matchdays; market grids only use catalogues captured before kick-off.
* Shrinkage (per engine, toward the league prior) and `gamma` are fitted on the first 60% of
  rows; both mixtures are refitted **walk-forward every matchday** on the trailing 420 rows.
* Headline metrics come only from the untouched remainder, with 95% Wilson intervals.
* `fit_pool_weights` uses a Dirichlet floor (3%) so a fit can never collapse onto one engine.
* A prediction is frozen at lock time: the sheet records the mixture weights it was locked with, so
  a later refit cannot silently change a pick that was already published.

Current full run (`--window 800`, 640 evaluated team-fixtures, 960 fitting rows, archive of
11,144 finalized results, target 3135935/14):

| engine | log loss | exact hit (95% CI) | top-3 | 1X2 | rows | weight (competition) |
| --- | --- | --- | --- | --- | --- | --- |
| Elo ratings | 2.832 | 13.4% (11.8–15.1) | 34.8% | 47.6% | 1600 | 36.4% |
| Dixon-Coles Poisson | 2.781 | 13.1% (11.6–14.9) | 34.5% | 49.4% | 1600 | 23.0% |
| Market-implied | 2.778 | 6.2% (2.5–15.0) | 34.4% | 53.1% | 64 | 14.3% |
| FT sequence (KT backoff) | 2.793 | 13.6% (12.0–15.4) | 36.5% | 49.4% | 1600 | 12.1% |
| League baseline | 2.891 | 13.8% (12.1–15.5) | 34.2% | 36.3% | 1600 | 8.2% |
| Similar-sequence search | 2.793 | 13.6% (12.0–15.4) | 36.5% | 49.4% | 1600 | 6.1% |
| Competition blend | 2.750 | **15.6%** (13.0–18.6) | 38.4% | 49.1% | 640 | — |
| Log-loss blend | **2.745** | 14.7% (12.2–17.6) | 40.3% | 49.7% | 640 | — |
| *always play 1:1* (naive) | — | 15.9% | — | — | 640 | — |

Numbers move as the archive grows, because the evaluation window slides with it; the board always
shows the build it came from, and the blend gain over the best single engine is +0.033 log loss.

The live ledger on the same build: **25 sheets** (9 locked live, 16 backfilled from the archive),
**400 picks**, 384 graded, 44 exact hits (11.5%), 110 right-result hits (28.6%); the current
season has 14 sheets, an on-time share of 85.7% and a live sheet locked **206 s before the first
kick-off**. Backfilled sheets carry no lead time, which is why the all-history figure is lower than
the live one — the board shows both.

## API

| route | behaviour |
| --- | --- |
| `GET /api/playground/tick` | the cheap beat: board key, lock facts, graded counts, sync and build state (~2 ms) |
| `GET /api/playground` | the payload (gzip when asked, ETag/304, composed from the ledger in ~50 ms, never blocks on a build) |
| `GET /api/playground/history?season=` | every sheet of a season: days, per-team stats, integrity, all rows |
| `GET /api/playground/sheet?season=&day=` | one locked sheet, `404` if that matchday was never locked |
| `POST /api/playground/rebuild` | request a fresh engines build; the answer appears through the GET |
| `GET /api/playground/status` | builder state, sync state, ledger version, health summary |
| `GET /playground` | the standalone board page |

The service builds in a daemon thread, writes the payload atomically and serves the last good copy
meanwhile; the ledger is written by the sync loop, which is cheap enough (about a second) to run
every five seconds. Rebuilds trigger when the archive revision or target matchday changes and are
throttled to one per 240 s. `LEAGUE_PLAYGROUND=0` disables the whole thing.

## Tools

```bash
python3 scripts/embed_snapshot.py --build      # rebuild engines and re-embed the board snapshot
python3 scripts/audit_playground.py [--quick]  # cut-off, order, orientation, mixture, determinism, ledger discipline
python3 scripts/compare_engines.py             # leave-one-out walk-forward for a candidate engine
python3 scripts/check_playground.py            # browser check, offline and live
python3 scripts/check_workspaces.py            # live board + season sheets + engines, against a running app
python3 -m pytest tests/test_playground.py     # 39 unit tests for the engines, ledger and service
```
