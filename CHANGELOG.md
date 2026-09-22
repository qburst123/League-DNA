## 2.8.0 — head-to-head matchup DNA, and a collector that sees the gaps

* **Head-to-head matchup DNA** (new workspace, `#/matchups` and `/matchups`). Every matchday is called
  by the highest scoring half — 1ST HALF, 2ND HALF or EQUAL — from the recorded meetings of that exact
  fixture. The matchday to play carries its HT/FT card with the half card directly beneath it; MD1–MD30
  for the whole season sit under the recorded scores, green when the call was right, red when it was
  wrong, purple while it is still to play. A sheet is written once, when the matchday is announced, and
  never rewritten — grading only fills the recorded result in.
* **Historical analysis sub-workspace**: a dropdown over every archived team pair, a venue scope, and the
  full record of that fixture — season, matchday, HT, FT, both halves, which half out-scored the other,
  and whether the call this history would have made was right.
* **Live board data**: `GET /api/matchups` (board) and `GET /api/matchups/tick` (a few hundred bytes every
  four seconds); `GET /api/matchups/pairs` and `/api/matchups/pair` for the historical queries. The board
  key moves only when a sheet, a grading or a recorded result does.
* **Collector: gaps now fill fast.** The history worker fills published-but-missing matchdays first (current
  season first, newest day first), then discovers seasons the archive never knew about with a likelihood-ordered
  walk of season ids (22–33 apart in this competition, most likely distance first) and finishes each window
  with an exhaustive sweep so completeness is proved rather than assumed. A reserved-share lane scheduler keeps
  the archive lane from being starved by the live fixtures lane.
* **Storage**: `wal_autocheckpoint=256` and a 4 MB `journal_size_limit` on every database, so `-wal` side files
  stay inside the workspace budget instead of growing to tens of megabytes between checkpoints.

# Changelog

## 2.7.0 — the frozen prediction ledger
- Reported: "whenever the page refreshes we get different predictions across the already played
  matchdays", per-team predictions drifting, and picks still moving long after the matchday had been
  played. Root cause: the board was recomputed from the engines on every tick, so a played matchday's
  pick changed whenever the archive grew. Fixed by making the published pick a **stored record**:
  `backend/ledger.py` writes a sheet per matchday into `data/predictions.sqlite` the moment the
  matchday is announced, and **no code path ever writes into a pick column again**. Grading only fills
  the result columns (`actual`, verdict, recorded FT score). Verified live: 25 sheets, 400 picks, 384
  graded, and a played matchday's pick survives a page reload, a restart and a fresh engines build.
- New: **season sheets**. Every season's prediction sheet is browsed in the app — season picker,
  matchday strip, the locked pick with every engine's vote and the recorded FT score, and a per-team
  table (picks, graded, exact, direction, misses, mean probability). Served by
  `GET /api/playground/history?season=` and `GET /api/playground/sheet?season=&day=`.
- New: the **upcoming matchday is predicted the instant the source announces it**, in the background
  sync loop (`sync()` every 5 s, ~1 s of SQLite work): it locks every unplayed matchday, grades every
  ungraded sheet **across all seasons**, and refreshes the pending rows. The sheet records the lock's
  lead time, so "in sync" is a number rather than a claim — the current season's live sheets were
  locked up to **206 s before the first kick-off**, and only live sheets can ever be flagged late.
- New: a **tick** beat for the browser. `/api/playground/tick` answers in ~2 ms (~0.5 KB) with
  `board_key`, the target's lock facts and the graded counts; the page beats every 4 s and fetches the
  full payload only when `board_key` moves. `board_key` is `ledger version | archive revision | target
  matchday`, so a new sheet, a graded result or the source moving on all move it. The heavy
  walk-forward refit (~55 s) stays in the background thread and never blocks a page load.
- Caching for fast fetch: `predictions.sqlite` (sheets), `live-sheets.json` (pending rows) and
  `backtest.json` (the report) live on disk; `compose()` rebuilds the board from the ledger in ~50 ms.
- Engine confidence, measured rather than assumed: `h2h` (head-to-head & venue) was implemented in
  `backend/playground.py` and added to a **candidate lane** (`CANDIDATE_MODELS`) instead of the board.
  `scripts/compare_engines.py` runs the walk-forward with and without it on identical data
  (640 evaluated rows): competition exact hits **17.2% → 15.9%**, log-loss blend **2.748 → 2.755** —
  worse on both, so it is documented as a candidate and deliberately not shipped as an engine card.
- UI: frozen-lock chip, ledger-checked chip and an integrity chip on the board; each cell states
  whether its sheet was locked live or backfilled; prose rewritten to "Every cell is frozen when it is
  made"; the modal gains a provenance block.
- QA: `scripts/check_workspaces.py` now carries the ledger contract (the freeze test reloads the page
  and compares season-keyed `pick|verdict` cells across every team, plus the season-sheets section) and
  `scripts/audit_playground.py` gained a ledger-discipline section (no empty picks, grading never
  rewrites a pick, `late` is live-only, season aggregates add up, no completed matchday left ungraded
  in any season, compose/tick inside a tick budget). Both pass on the live board:
  `WORKSPACE CHECK PASSED` (80 s) and `AUDIT PASSED`. Suite: **152 passed**.
- Upgrading is safe: the ledger is a new file created on first start (existing sheets are backfilled
  from the archive and labelled `backfilled`), and neither `league.sqlite` nor `ft-sequences.sqlite`
  changes schema.

## 2.6.5 — every engine keeps its place in the engines workspace
- Fixed the gap reported against v2.6.4: when a target fixture has no captured market catalogue the
  engine roster showed **five** explainability cards and five contribution bars instead of six, and the
  count check only passed because the roster size itself had shrunk. The engines workspace now renders
  the **whole roster**: an engine that cannot price that matchday keeps its card and its contribution
  slot, marked "abstains — takes no weight", with the reason stated on the card, in the bar and in a
  summary line under the chart. The mixture renormalises over the engines that did price the matchday,
  which is what the blend already did.
- New payload-contract test: `models` plus `unavailable` must cover exactly the engine roster, the two
  sets must not overlap, and every abstaining engine must carry a human-readable reason. Suite: **149 passed**.
- `scripts/check_workspaces.py` now derives the engine roster from `/api/playground` instead of hard-coding
  six cards and six bars, and fails if an abstaining engine is shown without its reason. Verified on the
  live board and against a payload whose market catalogues were removed (the one live case that abstains).

## 2.6.4 — season rollover
- The board handles the start of a season correctly, which the live source produced while this was being
  tested: the new season opens with an empty recorded row and pending predictions on the matchdays that are
  set to play, and the heading says "no matchday completed yet" instead of "completed through MD0".
- The tiny-archive path of the walk-forward run now reports the season it was asked for, so a board pinned
  to a brand-new season never falls back to `null` while the ledger is still empty.
- One new regression covers the rollover: a finished season plus a new season with nothing played must
  produce a board that follows the new season, an empty score map, an empty ledger and a real prediction
  for the matchday to play. Suite: **148 passed**.
- `scripts/check_workspaces.py` is season-aware: it no longer demands a mid-season board (an exact hit is
  only required once enough predictions have been graded), and it fails if the board ever uses a verdict
  outside the published set. Verified against a live season that was one matchday old.

## 2.6.3 — the live board the playground was meant to be
- **The FT score Prediction Playground is now the Live FT board with predictions in it.** Each team has the
  row of recorded full-time scores for the ongoing season, and directly beneath it the prediction the
  mixture made for the *same* matchday: **green for an exact FT score, amber for the right result, red for
  a miss, purple for the matchday that is still to play**. Every cell carries its blended probability and
  agreement count, and opens its evidence.
- **The matchday to play is predicted, not the tail of the archive.** The playground used to anchor on the
  newest season with a long ledger (the previous season, 202 days earlier) and predict an already played
  round; it now anchors on the season the collector is publishing. It also predicts *every* unplayed
  matchday, because the source opens the next round before the previous one is recorded — the board shows
  pending predictions on each of those columns instead of a gap.
- **The board refreshes itself.** It polls `/api/playground` every 15 seconds (ETag, so an unchanged payload
  costs one millisecond), reads `/api/playground/status`, asks the service to rebuild when the published
  matchday moves on, keeps painting the last good payload while the build runs, and pauses while the tab is
  hidden. A "refresh now" chip rebuilds on demand and shows the current state.
- **The service rebuilds promptly when the matchday moves** (45-second floor instead of 240) and reports a
  precise reason (`target matchday moved` / `new season` / `archive changed`), which two regressions pin.
- **The two charts the standalone board has are back in the engines workspace**: "Reliability — does the
  probability mean anything?" (claimed probability against the exact-hit rate that followed, with the
  calibration diagonal, a windowed axis and a gap table) and "Mixture weights through the run" (one line per
  engine per matchday refit, thick = competition mixture, thin = log loss, plus the movement table).
- **The Windows terminal spam is gone.** `ConnectionResetError`/`WinError 10054` from
  `_ProactorBasePipeTransport._call_connection_lost` is the browser closing a socket mid-download, not an
  app fault; the loop's exception handler now swallows those resets instead of printing dozens of tracebacks.
- Fixed the "Live connection timed out" and "connected but not updating" reports: the workspace fetch timeout
  went from 4.5 s to 30 s (a 20 MB payload over a slow localhost response), the two playground workspaces no
  longer wait for that payload at all, and the payload is polled on its own fast schedule.
- Suite: **147 Python tests** (four new: the recorded-score row, the season pin, the board following the
  published season, and the service's rebuild reasons) and the browser contract check now pins the two-row
  board, the green/red verdicts, the adjacent pending matchday and both charts.

## 2.6.1 — startup fix
- **The app opens immediately on a fresh installation.** A freshly extracted package had no `data/playground.json`, so the app began a full walk-forward build (about a minute of CPU) at boot — exactly while the browser was opening the first page. The service now falls back to the snapshot that ships inside the board folder, the packager ships `data/playground.json` as well, the first background build waits 25 seconds, and the builder thread runs at low priority so it can never starve the web loop.
- **The workspace payload is no longer rebuilt every 8 seconds.** `/api/workspace` was cached under a `store.version + wall-clock bucket` key, so the browser's 8-second poll re-serialised and re-gzipped 20 MB each time. The cache key is now the archive revision alone, and an unchanged payload is answered with a 304 in about a millisecond.
- **The two playground workspaces no longer wait for the archive payload.** They read `/api/playground` only, and the app used to hold the whole interface behind the 20 MB `/api/workspace` response; the client timeout for that request was also raised from 4.5 s to 30 s.
- Fixed a defect in the background builder introduced by the deferral above: it called `os.nice()` without importing `os`, which killed the worker thread silently and left the payload permanently stale while the status read "building". The call now sits inside the guarded block, and two regressions pin both behaviours: a failed build is reported as an error rather than a dead thread, and the board snapshot is used when the payload cache is missing. Suite: 144 passed.
- Measured on a clean extraction of the packaged archive: `/` in 2 ms, `/api/playground` in 14 ms, `/api/workspace` in 0.8 s (was 24–37 s), and the live ledger board painted 0.3 s after navigation.

## 2.6.0 — FT score Prediction Playground as two workspaces
- **New workspace: FT score Prediction Playground** — a live ledger board in the shape of the Historical Replay Lab's Live FT board. Each of the 16 teams gets a row of live walk-forward predictions, one per matchday, and a ledger row beneath it grading every earlier matchday with the pick that was made before it (`EXACT`, `DIR`, `WRONG`, `PENDING`). The incoming matchday shows the pending pick for each fixture.
- The scope switch regrades the identical matchdays with the **competition blend** (fitted on exact hits) or the **log-loss blend** (fitted on probability), so the two objectives can be compared on the same evidence instead of asserted apart.
- **New workspace: Score engines & explainability** — engine cards, the walk-forward scorecard with Wilson intervals and the always-play baseline, the per-fixture explainability space (processing chain, full 8×8 grid, input values, recorded neighbour/back-off evidence), the contribution chart, per-matchday hit ledger, confidence bands, agreement table, reliability curve, mixture-weight history and the protocol notes.
- `backend/playground.py` now records a per-team, per-matchday walk-forward ledger during the backtest (both blends) and returns it as `live_board`; `backend/playground_service.py` publishes the same key at the top level of the payload, and the report shape moved from six engines only to engines plus both mixtures.
- Fixed a latent cold-start crash: the similarity engine returned a plain grid when a team had no recorded run yet, which broke the sequence rescoring; it now reports "cannot act" like the Markov engine, and every engine keeps an explicit `reason` for the fixtures it skips.
- Fixed a latent scoring crash: `log_loss` now floors impossible scores at 1e-9 instead of raising a domain error when an engine assigns exactly zero to the recorded score.
- Three new regression tests pin the ledger (grading, row contract, both blends), the "only played matchdays are graded" rule and the walk-forward invariant that earlier picks do not move when later matchdays are removed. Suite: 142 passed.
- Documentation, release metadata and the standalone board snapshot updated; `release.json` now carries the playground block (board folder, routes, blends, ledger).

## 2.4.0 — Ongoing-season replay and the continuation ledger
- The Historical Replay Lab now accepts the ongoing, unfinished season as a replay target up to its finalized prefix; future seasons and seasons without a finalized matchday stay rejected.
- The ongoing season replays through live data: the cutoff range grows with the collector, the unplayed next round is labeled `awaiting FT`, and the workspace keeps auto-refreshing every 8 seconds with SSE pushes.
- The Live FT board no longer stops at data display: each of the 16 teams carries earlier-season continuation picks for its own finalized FT prefix (longest usable context first, at least two distinct recorded source matches, reference seasons strictly earlier than the incoming season).
- Added a picks row beneath every team row grading each matchday as hit, miss, pending or no qualifying evidence, with a click-through evidence modal, a per-team summary and an all-teams/same-team scope switch.
- Added `GET /api/forecast` and a cached continuation ledger (`backend/forecast.py`) embedded in `/api/workspace`; the ledger is recomputed only when a new FT score is finalized.
- Added `scripts/audit_forecast.py`, which recomputes every pick with an independent naive scan, re-checks every verdict and proves later seasons cannot change an earlier target's ledger, plus an ongoing-season contract in the replay audit.
- Added `diagnostics.forecast_error` so a deferred ledger is visible without reading the activity log, plus two workspace regressions covering the embedded ledger and the deferral.
- Documentation, browser checks, screenshots and metadata updated to v2.4.0; 113 Python tests pass and no collected data is dropped.

## 2.3.0 — Historical Replay Lab
- Added completed-season replay with a selectable historical target/team, MD1–29 cutoff and full-prefix or trailing-context study.
- Excluded the entire target season and all later seasons from reference evidence, in both browser and Python implementations.
- Hid the actual following target FT until reveal; revealing or changing that result does not change the reference counts.
- Added an independent live FT board showing all 16 incoming teams across MD1–30 without continuation recommendations.
- Added replay JSON export and source-result inspection. Existing workspaces, source collection, index data and trails are preserved.
- Added 10 regression tests (102 total), real-data cross-implementation replay checks and desktop/mobile workflow checks.

## 2.2.0 — Historical FT Sequence Explorer
- Added manual recorded-context lookup for FT sequences of lengths 1–30, all observed following outcomes, season coverage and traceable source examples.
- Scanned the observed FT vocabulary and added a paginated catalogue of every actually observed context, not unbounded zero-evidence combinations.
- Added `data/ft-sequences.sqlite`: compact two-byte context encoding, binary next-outcome counters and compressed per-season incremental contributions.
- Deduplicated physical next-match IDs while retaining separate team-sequence observation counts.
- Preserved gaps, season boundaries, terminal contexts, original FT orientation and explicit unknown outcomes.
- Added small-sample warnings and historical-only labels. No upcoming-fixture predictions or betting recommendations were added.
- Added 14 index regression tests (92 total), independent materialized-vs-browser checks and new browser workflows.

## 2.1.0 — Single-hit Trail
- Added a dedicated Single-hit Trail workspace and a contextual panel below Gold historical comparisons.
- A fixed current MD1–30 row fills with source FT scores; only exact-count-one events create/extend historical rows.
- Same-team/season/offset continuations extend the existing captured band; different or broken pieces append new rows without deleting prior data.
- All 16 incoming teams are observed in both historical scopes by the live collector, even when the page is closed.
- Durable SQLite rows, idempotent observations, frozen historical score snapshots and retained source evidence; completed seasons remain separate.
- Added stale-source/current-backfill protection, season rollover handling, 30-day historical inspection and mobile alignment controls.
- Retained the explicit Windows tzdata dependency and all existing data-first FT functionality.

## 2.0.1 — Windows runtime packaging fix
- Added explicit `tzdata==2026.3` dependency so `ZoneInfo("Africa/Nairobi")` works when the operating system has no IANA timezone database.
- Added an early timezone preflight and actionable install instructions before opening the browser or advertising the server as started.
- Verified source adapter and full-app import with an empty `PYTHONTZPATH`.
- Added three runtime regression tests; the full Python suite passes 63 tests.
- No interface, matching logic or stored match data is removed. Gold and the live FT collector remain part of the same app.

## 2.0.0 — 2026-09-15

### Complete data-first app rebuild
- Introduced a coherent `/api/workspace` feed built directly from recorded fixtures and match rows.
- Incoming teams no longer depend on a Gold response or a legacy `teams` metadata array. Complete fixture records are authoritative.
- Current FT inputs stay visible when the prefix is incomplete, materialized blueprints are missing, comparisons fail, or no historical matches exist.
- Added large, wrapping FT tiles, fixture-derived incoming team list, next opponents, recent FT previews and an always-available team selector.
- Moved the browser's Gold and four-symbol searches onto the loaded real archive; live-source collection and persistent storage remain on the server.
- Added a self-contained `League-DNA.html` with embedded real data, compressed source receipts, scripts, fonts and styles. It works fully offline and in an opaque sandboxed viewer.
- Added explicit live/saved status and capture timestamps. Missing source/network data never silently clears a loaded workspace.
- Added Windows/macOS/Linux launchers and a one-process browser-opening Python entry point.
- Retained all six workspaces, source evidence, persistent original results, original blueprints and saved alignments.
- Added 9 data-layer regression tests (60 total), cross-language engine equivalence checks, and offline/no-history/missing-prefix/sandbox browser tests.

## 1.2.0 — 2026-09-15

### Gold rebuilt to match the Pattern Comparator workflow
- Start at upcoming MD3 with finalized MD1–2. Append every newly finalized round to the full MD1-based sequence.
- Search the whole sequence first at all historical offsets for all 16 upcoming teams.
- From upcoming MD5 only, zero-match teams drop oldest days one at a time, stopping at the first successful length or at two results.
- Counts/candidate lists use one selected length per team, never a mixture of long runs and short seeds.
- Reuse Pattern Comparator's actual alignment grid; preserve the real current-day labels after shortening.
- Show full-prefix attempt, each removal, chosen range and excluded original scores. No result is deleted.
- Automatically focus the aligned scores on mobile so an offset match does not look like an empty row.
- Remove seed-length and team-relative-score controls from Gold. Literal website FT home:away scores only.
- Add old-interface/old-backend version warnings and clear restart/reload instructions.
- 51 backend tests, browser regressions, and independent real-score replays of upcoming MD3/MD4/MD5/MD6.

## 1.1.0 — 2026-09-15

### Correct score gold hit pattern
- Added a dedicated **Correct score gold** sidebar view.
- Exact-score matching for all 16 upcoming teams, using at least two latest consecutive finalized results from closed matchdays.
- Searches every eligible historical endpoint and extends exact matches backward toward current matchday 1.
- Longest-run-first ranking, full-current-prefix indicators, and all-alignment counts by extension length.
- Complete current/history rows on a shared coordinate grid, including negative offsets where a late current suffix matches early historical days.
- Interactive seed replay, one-result-at-a-time extension, historical endpoint controls, reset and focus actions.
- Original website home:away and normalized team goals-for:against score orientations.
- Explicit gap, mismatch, season-boundary and insufficient-data states.
- Automatic rematching and a frozen inspection view.
- Original result/source receipt inspection from score cards.

### Storage and compatibility
- Added `score_blueprints` with both 30-position score orientations and source match metadata.
- Automatic non-destructive upgrade from the previous database schema; original matches, receipts, four blueprints and pins are retained.
- Correct-score vectors update in the final-score transaction. Both default Gold comparisons are warmed and persistently cached.
- Uses existing Betika records and does not add external data feeds or new third-party runtime dependencies.
- Added a code-only upgrade ZIP and `UPDATE.md` so existing installations do not overwrite their collected database.

### Verification
- 17 new engine/migration tests; 45 backend tests in total.
- Dedicated Gold desktop/mobile/dark-mode browser regression checks.
- Source audit now validates both correct-score orientations in addition to all finalized records and the original four blueprints.
- Live candidate audit validates exact equality, maximal backward extension, historical-only scope and invalid-parameter handling.

Historical run length is not predictive confidence. No wagering recommendations, next-score forecast, market auto-selection or bet placement were added.

## 1.0.0

Initial read-only Betika League collector, persistent archive, four market blueprints, sliding-window comparator, live fixtures/markets, provenance, exports and data-health workspace.
