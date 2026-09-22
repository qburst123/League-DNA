# Release history

Every archive below was produced by `scripts/package_release.py`, never by hand, and every one was
verified by a **real extraction** before it was called good — not just a CRC test. The packager
writes plain single-method zips with explicit directory entries, no ZIP64 records and no extra
fields, so Windows Explorer, macOS Archive Utility and Android file managers all open them.

---

## v2.7.0 — the frozen prediction ledger  ·  current

The board no longer recomputes a prediction for a matchday that has already been played. Every
matchday's sheet is locked once, when the source announces it, in `data/predictions.sqlite`, and only
its result columns are ever filled afterwards — so a page refresh, a restart or a fresh engines build
shows the same pick it showed before kick-off. Every season's sheet is browsable in the app (season
picker, matchday strip, per-team table), the next matchday is locked by a 5-second background sync the
moment it is announced (live sheets were locked up to 206 s before the first kick-off), the page beats
a ~2 ms tick and pulls the full payload only when the board actually changes, and the stored sheets,
pending rows and report are cached on disk so the board composes in ~50 ms. A seventh engine
(head-to-head & venue) was implemented, measured leave-one-out on identical data, found to *lower* the
mixture (17.2% → 15.9% exact hits), and held back as a documented candidate instead of being shipped.
Ledger QA: `WORKSPACE CHECK PASSED` and `AUDIT PASSED` on the live board, 152 Python tests.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.7.0.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |
| `League-DNA-v2.7.0-lite.zip` | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

## v2.6.5 — the engines workspace keeps every engine

v2.6.4 with the engine roster fixed: an engine that cannot price the target matchday (no captured market
catalogue) keeps its card and contribution slot and states its reason instead of silently dropping out of
the chart, and the browser check asserts the roster against the payload rather than a hard-coded six.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.6.5.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |
| `League-DNA-v2.6.5-lite.zip` | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

## v2.6.4 — season rollover

v2.6.3 with the season-start path fixed and pinned: a season that has just opened shows an empty recorded
row with pending predictions on the matchdays set to play (heading: "no matchday completed yet"), the
tiny-archive path reports the season it was asked for, and the browser checker no longer assumes a
mid-season board.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.6.4.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |
| `League-DNA-v2.6.4-lite.zip` | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

## v2.6.3 — live board, auto-refresh and the two charts

Same feature set as v2.6.0/v2.6.1 with the playground rebuilt around the Live FT board:

* each team's row of **recorded FT scores** for the ongoing season, with the **prediction for the same
  matchday directly beneath** — green exact, amber right result, red wrong, purple to play;
* the matchday the source is publishing is predicted (and every other unplayed matchday the store knows
  about), not the tail of the previous season;
* the board polls, auto-rebuilds when the matchday moves, and refreshes on demand; the service reports why
  it wants to rebuild and moves within 45 seconds of a matchday change;
* the engines workspace carries the **reliability** and **mixture-weights** charts again;
* `ConnectionResetError` (WinError 10054) tracebacks are suppressed, and the workspace fetch timeout is 30 s.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.6.3.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |
| `League-DNA-v2.6.3-lite.zip` | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

## v2.6.1 — startup fix

Same feature set as v2.6.0 with the startup path fixed: a freshly extracted package now answers the
first page load immediately instead of starting a minute-long engine build at boot, `/api/workspace`
is cached on the archive revision instead of an 8-second clock bucket, and the two playground
workspaces no longer wait for the archive payload before painting.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.6.1.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |
| `League-DNA-v2.6.1-lite.zip` | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

Measured on a clean extraction: `/` 2 ms · `/api/playground` 14 ms (was: nothing until a 90 s build
finished) · `/api/workspace` 0.79 s (was 24–37 s) · live ledger board visible 0.3 s after navigation.
`python3 scripts/verify_archive.py League-DNA-v2.6.1.zip --launch-test` passes.

## v2.6.0 — the playground as two workspaces

The published checksum lives in **`SHA256SUMS.txt`**, written next to the archive at build time (a
file inside an archive cannot state the hash of the archive that contains it). Check your copy with
`sha256sum -c SHA256SUMS.txt`, or run the full structural and boot check with
`python3 scripts/verify_archive.py League-DNA-v2.6.0.zip --launch-test`.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.6.0.zip` (full) | see `SHA256SUMS.txt` beside the archive | `SHA256SUMS.txt` |

Contents: the whole app — backend, built interface, docs, tests, the collector, the captured archive
(`data/league.sqlite`, `data/ft-sequences.sqlite`, snapshotted, checkpointed, vacuumed and
quick-checked at package time), and the standalone board folder
(`FT score Prediction Playground/index.html`) so the packaged sidebar entry works from a fresh unzip.

What is new against v2.4.0 (the base of this build) and v2.5.0:

* **FT score Prediction Playground** is now a first-class app workspace: a live ledger board in the
  shape of the Historical Replay Lab's Live FT board, where every cell is a walk-forward pick with
  its blended probability, graded `EXACT` / `DIR` / `WRONG`, with the incoming matchday `PENDING`,
  and a graded ledger row under each of the 16 teams;
* a scope switch that regrades the identical matchdays with the **competition blend** (fitted on
  exact hits) or the **log-loss blend** (fitted on probability), so the two objectives are visible
  side by side on the same evidence;
* **Score engines & explainability** as its own workspace: engine cards, the walk-forward scorecard
  with Wilson intervals and the naive always-play baseline, the per-fixture explainability space,
  the contribution chart, the per-matchday hit ledger, confidence bands, the agreement table, the
  reliability curve and the mixture-weight history;
* `backend/playground.py` now records the per-team, per-matchday ledger during the walk-forward run
  and returns it as `live_board`; the service publishes it at the top level of `/api/playground`;
* two latent cold-start defects fixed, both found by the new tests: the similarity engine returned a
  bare grid when a team had no recorded run (breaking sequence rescoring) and `log_loss` raised a
  domain error when an engine assigned exactly zero to the recorded score;
* three new regression tests pin the ledger contract, the "only played matchdays are graded" rule
  and the walk-forward invariant that earlier picks do not move when later matchdays are removed —
  **142 Python tests pass**;
* `release.json` carries the playground block (board folder, routes, six engines, two blends, the
  ledger) and `last_known_good` still points at v2.5.0.

Everything in v2.4.0 and v2.5.0 is preserved: Gold, Single-hit Trail, the FT Sequence Explorer, the
Historical Replay Lab (including ongoing-season replay and its continuation ledger), the live
collector, the archive and the launchers. The database schema is unchanged, so an existing
`data/league.sqlite` and `data/ft-sequences.sqlite` keep working.

## v2.5.0 — FT score Prediction Playground

The published checksum lives in **`SHA256SUMS.txt`**, written next to the archive at build time (a file inside an archive cannot state the hash of the archive that contains it). Check your copy with `sha256sum -c SHA256SUMS.txt`, or run the full structural and boot check with `python3 scripts/verify_archive.py League-DNA-v2.5.0.zip --launch-test`.

| artifact | size | sha256 |
| --- | --- | --- |
| `League-DNA-v2.5.0.zip` (full) | 18,464,806 bytes | `0f9093315e6fe707044330a7d7581e4c3b4a0ad271863e8e3e3bae02e927a16f` |
| `League-DNA-v2.5.0-lite.zip` | 1,596,925 bytes | `cd3daca503a116bd89b9443fd181be2707cbed2a73f400e422e04678ef1db3a6` |

Those two files were superseded by v2.6.0 and removed from the workspace; the hashes stay here so an
existing copy can still be identified. The v2.4.0 rollback reference is
`League-DNA-v2.4.0.zip` — 23.50 MB, sha256 `469bca889a73d2b5...` (prefix as recorded at release
time); the database schema is unchanged across v2.4.0 → v2.6.0.

Contents: the whole app — backend, built interface, docs, tests, the collector, the captured
archive (`data/league.sqlite`, `data/ft-sequences.sqlite`, both snapshotted, checkpointed, vacuumed
and quick-checked at package time), **and the playground board folder**
(`FT score Prediction Playground/index.html`) so the sidebar workspace works from a fresh unzip.

What is new against v2.4.0:

* six prediction engines for the upcoming matchday (decayed Dixon-Coles Poisson, KT-backoff FT
  sequence, similarity-weight sequence search, Elo, captured market grid, league prior) with two
  mixtures — one fitted for log loss, one fitted for **exact hits**;
* a walk-forward, calendar-gated backtest with 95% Wilson intervals, confidence bands, a
  per-matchday hit ledger and the mixture-weight history;
* the explainability space: per fixture and per engine, the 8×8 score grid, the numbers behind it,
  the calibration applied and each engine's share of the final probability;
* new routes `/api/playground`, `/api/playground/rebuild`, `/api/playground/status`, `/playground`
  and a sidebar workspace that frames the board;
* the sequence-engine frame fix: every engine now answers in the row team's own frame, so the home
  and away rows of a fixture mirror each other (audited worst total-variation asymmetry 0.0000);
* the storage fix that keeps the folder portable: the app now truncates the write-ahead logs of
  **both** databases every 90 seconds (previously `league.sqlite-wal` and
  `ft-sequences.sqlite-wal` only shrank at shutdown, and a long run left tens of megabytes of
  side files behind), plus `scripts/compact_databases.py` to checkpoint, vacuum and verify both
  databases on demand;
* the calibration-target fix: the live path used to shrink every row toward the fixture's
  home-oriented Dixon-Coles reference, which dragged the away row out of its own frame whenever
  shrinkage was non-zero (the audit caught total-variation asymmetry up to 0.0680 there). It now
  shrinks toward the league prior, exactly as the walk-forward backtest does, so the served grids
  and the quoted metrics come from the same procedure. Two regression tests pin it.

Verification recorded for this build: **139 Python tests passed**, playground audit **PASSED**
(cut-off discipline, walk-forward order, row orientation, mixture invariants, determinism, board
snapshot), browser check **OK** offline *and* live, packaged app booted from the extracted archive
and served `/`, `/playground`, `/api/playground/status` and `/api/workspace`.

Rebuild it from source at any time:

```bash
python3 scripts/package_release.py --only full      # full + server + lite, all verified
python3 scripts/package_release.py --only lite      # code, interface and docs only (~1.5 MB)
python3 scripts/package_release.py --version v2.4.0 # same tree, a different label
```

---

## v2.4.0 — ongoing-season replay and the continuation ledger  ·  last known good

| artifact | size | sha256 (as reported) |
| --- | --- | --- |
| `League-DNA-v2.4.0.zip` | 23.50 MB | `469bca889a73d2b5…` |

Stored in **main**, and confirmed working by the operator. It contains ongoing-season replay, the
live auto-updating FT board, the earlier-season continuation ledger with per-team hit/miss rows,
and the nine workspaces that shipped with it.

**Keep this archive.** It is the rollback target: v2.5.0 adds the playground and the sequence-frame
fix and changes **no schema**, so restoring this zip together with your `data/league.sqlite` returns
you to the last known-good build.

> Note on size: v2.4.0 is listed at 23.50 MB where v2.5.0 is 17.08 MB. The difference is not lost
> content — the v2.5.0 databases are snapshotted with SQLite's backup API and then **vacuumed**
> before packing, which removes the free pages a long-lived WAL leaves behind, and the archive ships
> as a single compression method throughout. File counts and table row counts are higher in v2.5.0,
> not lower.

---

## Rebuilding a release from scratch

```bash
# 1. dependencies (an environment reset wipes these)
pip install -r requirements.txt
npm --prefix frontend ci && npm --prefix frontend run build

# 2. compact the databases (checkpoint both WALs, vacuum free pages, verify)
python3 scripts/compact_databases.py

# 3. the engine payload the board embeds (~80 s), then re-embed it into the board
python3 scripts/embed_snapshot.py --build

# 4. prove the app, the engines and the board are healthy
python3 -m pytest -q
python3 scripts/audit_playground.py --quick
python3 scripts/check_playground.py

# 5. package and verify (extraction + hashes are printed as JSON)
python3 scripts/package_release.py --only full
```

Ship only archives that step 4 printed with `"extra_fields": 0`, `"zip64": 0` and an
`extracted_files` count that matches the entry count. A package that fails any of those is
rebuilt, never patched by hand.

---

## Verifying an archive yourself

Before shipping an archive, or after downloading one, run:

```bash
python3 scripts/verify_archive.py League-DNA-v2.5.0.zip                 # structure + extraction
python3 scripts/verify_archive.py League-DNA-v2.5.0.zip --launch-test   # ...and boot the app from it
```

It checks every fault that makes an extractor say "invalid archive": ZIP64 records, data descriptors
(flag bit 3), extra fields, unusual compression methods, non-ASCII names, a directory stored as a
file, a missing parent directory, CRC failures and truncation. Then it extracts the archive with the
system `unzip` **and** with Python, compares the file counts, checks the launcher permissions and the
shipped databases, and — with `--launch-test` — starts the app out of the extracted folder and probes
`/`, `/playground`, `/api/playground/status` and `/api/workspace`. Exit code 0 means it is good.
