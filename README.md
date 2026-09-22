# League DNA — v2.7 with the FT score Prediction Playground

**Windows runtime fix (package revision 2.0.1):** the runtime now explicitly installs `tzdata` for `Africa/Nairobi`. If an older installation reports a timezone error, run `.\.venv\Scripts\python.exe -m pip install tzdata` in its project folder and restart. See `WINDOWS-FIX.md`. The Gold workspace and live collector are both included.

## New: FT score Prediction Playground

Two new sidebar workspaces predict every upcoming full-time score and show their own record.

**FT score Prediction Playground** is the Live FT board with predictions in it. Each of the 16 teams
keeps the row of **recorded full-time scores** for the ongoing season, and directly beneath it the
**prediction the mixture made for that same matchday** — green for an exact FT score, amber for the
right result, red for a miss, purple for a matchday still to play. The prediction is **frozen**: it is
written once, when the matchday is announced, and never rewritten, so a played matchday keeps the pick
it was locked with and a refresh cannot change the past. The next matchday is locked by a background
sync within seconds of the source announcing it, the page beats a ~2 ms tick and pulls the full board
only when something actually changed, and the scope switch regrades the identical matchdays with the
**competition blend** (fitted on exact hits) or the **log-loss blend** (fitted on probability). Click
any cell for its evidence.

**Score engines & explainability** is the engines view. Six models — league prior, chronological
Elo, Dixon-Coles Poisson, KT-backoff FT sequence, similarity-weighted historical windows and the
captured market grid — are scored walk-forward with Wilson intervals, then shown one fixture at a
time: the processing chain, the full 8×8 score grid, the inputs that produced it, the recorded
evidence it used and each engine's share of the final probability. Both mixtures are published with
their weights, their per-matchday refit history and the reliability curve behind them.

The **Season sheets** tab of the same workspace is the stored record: pick a season, pick a matchday,
and read the sheet that was locked for it — the pick, its probability, every engine's vote, the recorded
FT score, the verdict — plus a per-team table for the whole season (picks, graded, exact, direction,
misses, claimed probability). Sheets live in `data/predictions.sqlite` and survive restarts and rebuilds.

All three views read the same ledger and the same `/api/playground` payload, which is composed from the
ledger in about 50 ms and refitted in the background (about 55 seconds) whenever the archive or the
target matchday changes. The standalone board in the **`FT score Prediction Playground/`** folder is
still shipped beside the app, carries the ledger in its embedded snapshot, and still works without a
server. Guide: `docs/playground.md`.

Exact-score repetition is rare: a well-calibrated model lands the exact FT score roughly one time
in eight, so the board reports the count it actually achieves and never presents a continuation
pattern as a promise. No wagering action is offered.

## New: Single-hit Trail

Open **Single-hit trail** in the sidebar, or use **Open this team’s trail** below Gold’s historical-comparison panel. The current team stays on a fixed MD1–30 row. Exactly-one-match observations create aligned historical rows; unbroken continuations extend their row, and broken or different pieces remain as separate saved rows. All incoming teams are watched by the live collector, with separate storage for every team, season and historical scope.

Existing records are preserved. Capturing starts after updating the collector; old one-hit events are not invented. The full guide is `docs/single-hit-trail.md`.

## New: FT Sequence Explorer

The **FT sequence explorer** workspace shows what historically followed any ordered FT context in the captured archive. It includes the observed score vocabulary, every observed context of lengths 1–30, recorded next-score counts, season coverage, source examples and a separate persistent `data/ft-sequences.sqlite` index.

This is historical analysis, not an upcoming-fixture prediction or wagering recommendation. Unseen theoretical combinations are not assigned invented outcomes. See `docs/ft-sequence-explorer.md`.

## New: Historical Replay Lab

Choose **Historical replay lab** in the sidebar. It now replays **any recorded season, including the ongoing one**: the target opens at its finalized prefix and follows the collector as each new matchday's FT scores are recorded, while future seasons stay rejected. Choose a cutoff, inspect continuations drawn only from strictly earlier seasons, then reveal the already-recorded next target result.

The second tab, the **live 16-team × MD1–30 FT board**, no longer stops at data display. Each team row carries **earlier-season continuation picks** for that team's own finalized FT prefix — the longest context with at least two distinct recorded source matches — and the row beneath it grades every matchday as an exact FT hit, a wrong FT, a pick still awaiting the source, or a matchday with no qualifying evidence. Click any cell for the context, the competing recorded continuations and the reason that context length was chosen.

`GET /api/forecast` and `/api/workspace` expose the same ledger, cached and recomputed only when a new FT score is finalized.

Gold, Single-hit Trail, FT Sequence Explorer, the archive and the live collector remain unchanged. See `docs/historical-replay-lab.md`.

## Open it immediately

**Double-click `League-DNA.html`.**

This is a self-contained application, not a loader that waits for a server. It includes a real saved Betika archive, all recorded incoming teams, visible current FT scores, the four blueprint views, Gold comparisons, results, markets and source receipts. Its scripts, styles and fonts are embedded.

**Saved data is clearly labeled as a snapshot, not a live feed.** Check the capture time in the banner. No teams or scores are generated to fill gaps.

## Collect new live results

Extract the complete **`League-DNA-v2.4.zip`** package, then:

- **Windows:** double-click `Start-League-DNA.cmd`.
- **macOS:** run `Start-League-DNA.command` (you may need to allow the downloaded script).
- **Linux/macOS Terminal:** `bash Start-League-DNA.sh`.

Requires **Python 3.11+** and Internet access. The launcher creates a virtual environment, installs the Python requirements and opens **http://localhost:8000/#gold**.

Keep its terminal open to collect new results. **Ctrl+C stops collection; it does not erase the database.** The application refuses to start a second collector on an occupied application port.

### Manual alternative

```bash
python -m venv .venv
# Windows:
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe run.py
# macOS / Linux:
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

Node.js is **not required to run** the included app. The built interface is included. `run.py --offline` opens the saved database without contacting the source; `--no-browser` starts without opening a browser window; `--port 8001` changes the port.

## Keep your existing archive

Do not overwrite a database you have been collecting into.

1. Stop the old application and export or back up its SQLite database.
2. Extract v2 into a **new folder** rather than mixing old/new application assets.
3. If keeping your own data, replace the new folder's `data` directory with your existing, stopped application's `data` directory.
4. Run the new launcher and reload the browser.

Original match records, HT/FT, source receipts, four persistent blueprints and saved alignments are preserved. See `UPDATE.md`.

## What was rebuilt

The previous screen obtained the team list and FT inputs from a successful Gold-engine response. That made data disappear when the response was unavailable/incompatible or the completed prefix was empty.

**V2 separates data from analysis:**

- Incoming teams come from actual fixture records, not from match-search results.
- Current FT tiles come directly from recorded matches. They do not require a fingerprint cache or any historical hit.
- Known later FT records remain visible even if an earlier matchday is missing and blocks comparison.
- A zero-match search does not hide the team or its scores.
- The Gold comparison runs locally against captured FT data; `/api/gold` is not a dependency of its display.
- The complete saved application still works when every network request is blocked, including in an opaque sandboxed file viewer.
- A single workspace feed replaces many fragile initial requests. When live collection connects, it replaces the saved snapshot with newer source observations.

## Workspaces

| View | Included functionality |
|---|---|
| **FT score Prediction Playground** | Live ledger board: 16 teams × MD1–30 of walk-forward picks with the competition or log-loss blend, a graded ledger row under each team, the incoming matchday pending, per-fixture cards and click-through evidence. |
| **Score engines & explainability** | Engine cards, the walk-forward scorecard with intervals and the naive baseline, the per-fixture explainability space (chain, 8×8 grid, inputs, recorded evidence), the contribution chart, per-matchday hit ledger, confidence bands, agreement table, reliability curve and mixture-weight history. |
| **Correct score gold** | Incoming roster, next opponents/venue, recent FT previews, large always-visible selected-team FT tiles, exact historical counts, full-prefix-first matching, explicit oldest-day fallback, manual historical alignment and source inspection. |
| **Single-hit trail** | Static current MD1–30 FT row, persistent uniquely matched historical fragments, fixed offsets, retained broken rows, archived seasons, and observation inspection. |
| **FT sequence explorer** | Manual historical context lookup, observed following outcomes, deduplicated source-match counts, season coverage, sparse pattern catalogue and unique score vocabulary. No fixture forecasts. |
| **Historical replay lab** | Completed-season cutoff/reveal experiments with strict earlier-season reference data, all trailing context lengths, source examples, replay export, and a separate live 16-team FT matrix. |
| **Overview** | Topmost source-published incoming round, fixtures, source-based timer, ongoing observations and full read-only market catalogues. |
| **DNA explorer** | Four separate 30-day blueprints: odd/even, BTTS, team-relative draw-no-bet and over/under 2.5; source FT cards, filters and compact view. |
| **Pattern comparator** | All incoming teams compared against earlier team-seasons at every valid offset; exact or thresholded symbol agreement, manual alignment, saved snapshots when connected. |
| **Results archive** | Recorded HT/FT and provisional data, season/day/team filters, CSV export and original source receipts. |
| **Data health** | Archive coverage and integrity, source observations, collection controls and online database backup. In snapshot mode these are explicitly captured checks, not live monitoring. |

Dark/light themes, mobile layouts, keyboard navigation and original source receipt inspection are included. The monograms are generated UI identifiers, not official team logos.

## Correct score Gold rules

1. Once incoming MD3 is published and MD1/MD2 are finalized, compare each team's **full MD1–2 FT sequence**.
2. Append each newly finalized round: MD1–3, MD1–4, etc.
3. Search the entire current prefix at every valid historical offset, using **earlier seasons only**.
4. If that team has full-sequence matches, do not shorten it.
5. From **incoming MD5**, if there are no full-sequence matches, exclude the oldest day one at a time.
6. Stop at the first matching length, with **at least two results**. All returned matches for that team use the same length.
7. Show zero honestly if no sequence matches. No missing score becomes `0:0`, and no sequence crosses a gap or season boundary.

The FT input panel remains visible in every case. Gold tiles identify the chosen input range; other captured scores remain visible. Historical rows use the same shared alignment grid as Pattern Comparator. The full guide is `docs/correct-score-gold.md`.

**Scores are literal website home:away FT pairs.** Historical equality is not a next-score forecast or betting-confidence percentage. The app contains no account integration, market auto-selection, wagering recommendation or bet placement.

## Real Betika data only

Collection starts at **season 3134345**. Competition **26, “Betika Sakata League,”** was verified against “Betika League” on the requested Lite page by comparing the actual teams and HT/FT results. Original evidence and a Lite/API cross-check remain in `tests/fixtures` and `docs/source-inspection.md`.

The collector uses these first-party read-only endpoints under `https://virtuals.betika.com/v1/`:

- `competition`
- `matches?competition_id=26`
- `matches/ongoing?competition_id=26`
- `matches/results?competition_id=26&season=…&matchday=…`
- `match?id=…` for source-observed fixture IDs

The original eight completed seasons from 3134345 were captured in full (30 matchdays × 8 matches per season), and later records/seasons continue being collected. Exact release snapshot counts are in `release.json` and `artifacts/data-audit.json`.

No external football database or generated production result is used. Test-only edge cases use temporary databases and never populate the live archive.

### Final scores and source timing

- Saved HT/FT fields and verified final status determine settled results.
- **`first_half_score` is the source's first-goal field, not the halftime score.** It is not used as HT.
- Live values are stored separately and are never treated as settled scores merely because they are nonempty.
- A current round closes only with eight finalized matches and sixteen distinct teams.
- Naive source kickoff timestamps are interpreted in **Africa/Nairobi**.
- Countdown expiry does not fabricate the next matchday. The next group must be observed from the source.

## Persistence and live limitations

`data/league.sqlite` uses transactions, indexes, WAL and idempotent upserts. It retains original matches, market observations, compressed source receipts, four materialized blueprints, correct-score vectors, comparison caches, saved alignments and the new single-hit rows/observations. Evidence referenced by a single-hit row is protected from live-receipt pruning.

A source receipt's SHA-256 verifies the retained compact JSON payload; it is **not a digital signature issued by Betika**. The standalone file includes compressed retained evidence for its recorded matches.

Target intervals: ongoing observations 5 seconds, upcoming fixtures 10 seconds, latest results 18 seconds, full markets approximately 60 seconds. A global maximum of **one source request per second** and respectful 401/403/429 backoff apply. Neither source polling nor local processing is a zero-latency guarantee.

The published season list is rolling, not an unbounded archive index. Previously discovered seasons remain stored, but an extended offline period can leave unobserved seasons undiscoverable. Continuous capture requires an **always-on host and persistent disk**; an Arena preview is not permanent hosting.

## Backup

When connected, use **Export data → Complete database**. This uses SQLite's online backup API. Do not copy only the main `.sqlite` file while WAL writes are active.

To restore, stop the server, preserve the old database, replace it with a consistent backup, remove obsolete stopped-database `-wal`/`-shm` companions if necessary, then restart.

## Development and verification

```bash
pip install -r requirements-dev.txt
npm --prefix frontend ci
python -m pytest -q
python scripts/audit.py
python scripts/audit_client.py
python tests/browser_v2.py
python tests/browser_trail.py
python scripts/audit_trail.py
python scripts/audit_sequences.py
python tests/browser_sequences.py
python scripts/audit_replay.py
python tests/browser_replay.py
python tests/browser_smoke.py
python scripts/audit_playground.py --quick      # full run: --window 800
python scripts/check_playground.py              # the standalone board, offline and live
python scripts/check_workspaces.py              # both in-app workspaces, against a running app
```

Browser tests require Playwright Chromium: `python -m playwright install --with-deps chromium`.

Build the interface and embed a new **actual collector snapshot**:

```bash
npm --prefix frontend run build
# With the collector running:
python scripts/build_app.py
```

This writes the snapshot-bearing `web/index.html` and self-contained `League-DNA.html`. The snapshot comes from `/api/workspace`; the script does not generate sample teams or scores.

The release verification includes **152 Python tests**, independent JavaScript/Python engine checks, all nine workspace pages, live workflows, fully offline HTML, network-blocked comparison routes, zero-history and missing-prefix scenarios, and an opaque `sandbox="allow-scripts"` iframe. See `docs/verification.md` and `artifacts/v2-browser-report.json`.

## Deployment

The supplied Docker configuration can serve the live app and bind-mount `./data`. Stop any standalone collector before starting it against the same files. For public hosting, use TLS and authentication in front of this single-user app and disable proxy buffering for `/api/events`.

Docker and platform-specific launcher files are supplied; the Python server, launcher CLI and browser workflows were tested in this workspace. Windows/macOS launchers and Docker were not executed here.

---
Independent read-only research application. Not affiliated with Betika. Respect source access restrictions and applicable terms.

## Repository and packaged data

This repository carries the source: the FastAPI collector and services in `backend/`, the React
workspaces in `frontend/src/`, the audit and packaging tools in `scripts/`, the checks in `tests/`,
and the built interface in `web/`.

The recorded datasets are **not** in git — `data/league.sqlite` is roughly 70 MB and
`data/ft-sequences.sqlite` roughly 26 MB. They ship inside the release asset
**`League-DNA-v2.8.0.zip`** (attached to the release, or in the workspace next to this repo), which is
a complete runnable build: extract it, install `requirements.txt`, and run `python3 run.py --port 8000`.

A fresh clone without the databases still starts: the archive reads empty and the collector fills it
from the published source. The one file that cannot be regenerated is `data/predictions.sqlite` — the
frozen half-call ledger — because a sheet is written once, when its matchday is announced, and never
recomputed. It also travels inside the zip.
