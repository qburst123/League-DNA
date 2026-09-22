# Historical Replay Lab — v2.4

The lab has two separate views: **Season replay** and **Live FT board**. Existing Gold, Single-hit Trail, FT Sequence Explorer, archive, markets and live collection remain available.

## Season replay

1. Select a target season. The list contains every season with at least one finalized matchday — **including the ongoing season** — and never a future season.
2. The ongoing season opens at its finalized prefix (MD1–MDn) and grows automatically as the collector finalizes each new matchday. A completed season keeps the original MD1–29 study range.
3. The full MD1-based prefix is selected by default. You can instead study a shorter trailing context ending at the same cutoff.
4. Inspect recorded continuations from the permitted reference pool.
5. Use **Reveal recorded MD{cutoff+1}** to see the already-recorded next result — only available once the source has finalized it.
6. Compare whether that recorded score appeared among the earlier historical continuations. This is a retrospective observation, not a score recommendation.

Changing the team, season, cutoff or context length hides the actual result again. Moving the cutoff also restores the full visible prefix as the default context. A completed season can be studied up to MD29, where the reveal is the already recorded MD30 result; the replay never wraps to a new season. For the ongoing season the last selectable cutoff is its finalized prefix, and the following round is labeled **awaiting FT** until the source records it.

### Chronological reference boundary

The reference pool contains only source match records from seasons **strictly earlier than the selected target season**. The target season is excluded in its entirety — including when it is the ongoing season still being played — as are all later seasons. An ongoing target is therefore never part of its own evidence, and no future season can enter a replay.

The target prefix supplies only the lookup context. The target's following result does not enter the outcome counts, choose a reference outcome or influence the context-length study. Repeated context windows cannot bridge unknown slots or season boundaries.

### Counts and source evidence

The panel shows every recorded following FT outcome in the allowed reference pool, with distinct source-match counts, separate team-sequence observations and season coverage. The examples list contains only earlier-season rows. Click a source score to inspect its recorded HT/FT and retained response.

The context-length study checks the full visible prefix, then every shorter suffix ending at the cutoff. It shows evidence volume; it does not automatically select a score or optimize a betting pick.

The revealed actual result is displayed separately. The lab reports how many earlier recorded follow-ups had that score, including zero if the reference sample did not contain it.

### Visual hiding is not cryptographic blinding

A completed historical target remains accessible elsewhere in the archive; the replay hides later target scores in this view to support learning, not to prevent a user from looking them up. For the ongoing season nothing is withheld — only the rounds the source has not finalized yet are unavailable. The calculation enforces the earlier-season boundary independently.

## Live FT board

The second tab displays every recorded incoming team as a row, with fixed MD1–30 columns. Actual finalized FT values populate automatically from the workspace feed — the header strip shows the auto-update cadence, the current prefix and the incoming matchday, and **Refresh now** asks the running collector for the newest workspace immediately. Playing or unrecorded values are labeled rather than invented.

### Continuation ledger (the row under each team)

Below every team row the board adds a **picks row** with one cell per matchday:

- **hit** — the pick matched the recorded FT exactly (green).
- **miss** — the recorded FT was different (red).
- **pending** — the pick is waiting for the source to finalize that matchday (purple outline).
- **no pick** — no earlier-season context reached the two-distinct-match minimum (dash).
- **blank** — the matchday has not been played yet.

Each pick is the most frequently recorded continuation of that team's own finalized FT prefix in seasons strictly earlier than the incoming season: the longest context is tried first and the oldest day is dropped only when no continuation was recorded. A pick additionally requires the reported score to appear in at least two distinct recorded source matches. Click any graded or pending cell to open its evidence: the picked FT, the recorded FT, the context used, the other recorded continuations, and why the context length was chosen.

The **scope** switch is shared with the header totals: **All 16 teams** pools every team's earlier-season windows, **Same team only** restricts the reference pool to the same team's earlier seasons. The five headline counters are picks made, exact FT hits, wrong FT, same winner and matchdays with no qualifying evidence. These are retrospective counts, not a hit-rate promise.

Team filtering and MD1/latest-FT focus controls make the matrix usable on desktop and mobile. Clicking a recorded score opens its source record.

## Export

**Export replay** downloads the selected replay experiment as JSON, including the context, cutoff, target state, permitted reference seasons, counts, length study and earlier-source examples. If the target result is hidden or not yet finalized, `actual_next` is null in the export.

## Offline and live behavior

The self-contained saved HTML supports both views with its genuine captured records. It is labeled as a snapshot: the strip reads **Saved snapshot**, live refresh is disabled and the board shows the ledger that was computed when the snapshot was written. When the running collector connects, recorded data replaces the snapshot and the board recomputes on every finalized FT score.

Replay and ledger calculations are derived from existing records and do not change scores, trail captures, saved alignments or the sequence index.

## API

```
GET /api/replay?season=<recorded-season>&team=<recorded-team>&cutoff=4&length=4&reveal=false
GET /api/forecast?season=<recorded-season>&scope=all|same
```

`/api/replay` accepts any season with a finalized prefix up to its prefix; future seasons and seasons without a finalized matchday are rejected. `reveal=true` returns only the already-recorded following target result. `/api/forecast` returns the continuation ledger, and the same payload is embedded in `/api/workspace` as `forecast`.

## Verification

```bash
python -m pytest -q
python scripts/audit_replay.py
python scripts/audit_forecast.py
python tests/browser_replay.py
```

The regression suite checks the ongoing-season target, target/future exclusion, hidden-result behavior, unchanged reference counts when the target result is revealed or corrected, all trailing contexts, prefix bounds, missing target records and preservation of existing data. `audit_replay.py` compares the Python and browser replay implementations on real data across early, middle and recent seasons; `audit_forecast.py` recomputes every ledger pick with a naive scan, re-checks each verdict, and proves that a later season's results cannot change an earlier target's ledger.

Historical repetition and sparse samples do not establish precise prediction or a betting edge. The ledger reports recorded repetition; no probability, confidence score or wagering action is offered.
