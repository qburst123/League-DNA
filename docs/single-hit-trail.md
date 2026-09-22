# Single-hit Trail — v2.1

A persistent visual history of uniquely matching correct-score fragments for a current team.

## Open it

- Choose **Single-hit trail** in the sidebar; or
- In **Correct score gold**, select a team and use **Open this team’s trail** below the historical-comparison panel. The team and all-team/same-team scope carry over.

## The fixed current row

The first row always represents the selected current team's **MD1–30**, starting at MD1. Newly finalized FT values fill their original day positions. It is not shifted when the compared suffix changes.

The one-match condition governs **historical captures**, not visibility of actual current FT records. Missing/unplayed scores remain unknown rather than becoming 0:0.

## When a historical row is recorded

The live collector runs the same Gold full-prefix-first comparison used by the app:

1. Search the complete finalized MD1-based prefix.
2. From incoming MD5, if no full match exists, remove oldest days one at a time until the first matching length, never below two results.
3. For a team whose resulting **exact-match count is exactly 1**, save that one historical alignment.
4. A count of **0, 2, or more** adds no row and does not extend an existing captured band.

Both comparison scopes are observed separately. A single match within **same team only** is not labeled as unique across **all historical teams**. Every trail is keyed by **current season + current team + scope**.

The collector watches all sixteen incoming teams, not only the one currently selected in the browser. Keep the collector running for continuous observation.

## Continuing and broken fragments

- Repeated refreshes of the same score window are idempotent: they do not duplicate the row or observation.
- If a later one-match result uses the same historical team, historical season and alignment offset, and every intervening score still matches without a gap, the saved band extends on the same row.
- If that relationship changes or a differing score breaks the continuation, the earlier row is retained. A later qualifying one-match result appends a new row below it.
- The historical FT snapshot and its alignment offset remain frozen. Previously saved rows are not erased when the exact count changes or a new matching team appears.

The observation inspector lists each distinct one-match capture, its current/historical ranges, actual FT sequence, source observation time and the historical pool size at that point.

## Alignment coordinates

Each row uses the current season's fixed day grid. If current MD20–23 matches historical MD26–29, the historical row is shifted so historical MD26 sits under current MD20.

Formally:

```
alignment offset = current match start − historical match start
current column   = historical day + alignment offset
```

All retained rows share the same current columns. Gold marks each saved exact fragment. A later first difference is outlined separately; it does not delete the earlier matching band.

Some nonmatching historical context can fall outside current MD1–30 when shifted. Those cells are clipped from the fixed canvas, not removed from storage. **Inspect saved row** exposes the full historical MD1–30 blueprint.

Use **MD1** to return to the start, or **Latest capture** to focus the newest matching band. Horizontal scrolling moves the shared canvas, not the logical alignment between rows.

## Persistence and season changes

SQLite stores the rows, deduplication keys, individual one-match observations and references to their retained source evidence. Restarting, refreshing or selecting another team does not erase the trail.

A new current season gets a separate trail. Completed seasons remain available in the season selector. If the previous season's final matchday is still genuinely playing, the automatic view keeps that ending season visible until its results close. Its top row can then complete MD30 without overwriting the retained historical fragments.

Tracking begins when the updated collector is enabled. It does **not invent earlier one-match events** by replaying the season after the fact.

## Readiness and uniqueness limits

- Source observations must be fresh before new live captures are recorded.
- The current FT prefix must be caught up near the playing round. Old prefixes being backfilled are not treated as newly occurring live matchday events.
- Uniqueness means one alignment in the **available database and selected scope at capture time**. Additional historical records collected later may change the current count; the original observation remains preserved and labeled with its original pool size.
- A saved HTML file can display captured trails, but cannot observe new live events without a running collector. Its snapshot timestamp remains visible.
- A source failure or trail-capture failure does not hide the original incoming roster or raw FT inputs.

These are descriptive historical matches, not future-score predictions, confidence percentages or wagering recommendations.

## Verification

```bash
python -m pytest -q
python scripts/audit_trail.py
python tests/browser_trail.py
```

Tests cover exact-count-one gating, repeat suppression, unbroken extension, broken-pattern retention, repeated historical identities after a break, different offsets, team/scope/season separation, restart persistence, stale source/backfill suppression and protected source receipts. The retained-score audit verifies frozen FT values against their original captured source payloads.
