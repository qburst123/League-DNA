# FT Sequence Explorer — historical follow-up evidence

Open **FT sequence explorer** in the sidebar. This workspace analyzes recorded full-time score sequences and the results that actually followed them. It does **not** recommend an upcoming score, forecast a fixture or claim predictive precision.

## Pattern follow-ups

Enter a context such as:

- `0:0, 0:1`
- `1:2, 0:1`
- `1:1, 0:1, 1:4`

You can select each FT value from the observed score vocabulary, add/remove positions, or paste a comma-separated sequence. A context can contain 1–30 ordered FT pairs. Scores use literal website **home:away** order; `1:2` is not reversed to `2:1` for an away team.

The result shows:

- **Pattern occurrences:** matching contiguous segments in recorded team-season trajectories.
- **Known following matches:** distinct source match IDs with an immediately following finalized FT score.
- **Team-sequence observations:** the number of matching trajectories before those next matches.
- **Following FT outcomes:** every observed score, its historical count/share and season coverage.
- **Missing follow-ups:** occurrences at a season boundary, gap or unfinalized following round.
- **Traceable examples:** the actual team, season, context matchdays and following result. Click the following score to open its HT/FT and retained source receipt.

Click an outcome row to filter the historical examples. This is an evidence filter, not an outcome selection for a future match.

## Unique FT scores

The vocabulary is scanned from finalized source match records. Each physical match is counted once in this view, not twice for its two participating teams. Clicking a score opens the historical one-score context analysis.

## Observed pattern library

Choose a length from 1–30 to browse every **observed** pattern of that length, with pagination and occurrence counts. The interface also displays the size of the theoretical combination space.

With S distinct FT values and length L, S^L possible combinations exist mathematically. Most long combinations never occurred. Enumerating them would create enormous numbers of zero-evidence entries. The application instead stores a sparse catalogue of every context actually present in the archive. Queries for unobserved combinations return zero evidence; no outcome or probability is fabricated.

## Persistent database

The separate SQLite file is **`data/ft-sequences.sqlite`**. It contains:

- `scores`: observed score vocabulary and physical-match counts;
- `patterns`: all observed contexts, lengths, occurrence/season counts and compact following-outcome counters;
- `partitions`: compressed per-season contributions used for safe incremental updates;
- `meta`: source revision, archive identity, indexed timestamp and build information.

Score pairs are represented by stable two-byte codes. Contexts are binary sequences of those codes; outcome counters use compact fixed-width binary fields. The index updates only changed season contributions after new or corrected finalized results arrive. Repeated refreshes do not double-count evidence. It is a derived index: deleting it while the app is stopped allows it to be rebuilt from the authoritative match archive.

A connected application offers an **Index database** download using SQLite's consistent backup API. The original match database, existing Gold and Single-hit Trail data are not replaced.

## Boundary and counting rules

1. Patterns stay inside a single team-season's MD1–30 record.
2. Missing or ambiguous FT positions stop a context; gaps are not bridged.
3. A following outcome exists only when the immediately next matchday has a finalized score within that same season.
4. A context ending at MD30 has no MD31 outcome and does not wrap to another season's MD1.
5. Both opposing teams can sometimes produce the same context before the same next match. The primary following-outcome distribution deduplicates that next source match ID.
6. Counts are still dependent: overlapping contexts and related games are not independent trials. A historical share is not a calibrated future probability.

For a tiny sample, the interface emphasizes the count itself. A 1-of-1 historical continuation is not evidence of a certain next score.

## What is intentionally not included

There is no auto-fill from incoming fixtures, upcoming-team prediction card, correct-score betting recommendation, claimed AI accuracy or market auto-selection. Advanced model names cannot create evidence that is absent from the record. The workspace provides transparent historical analysis instead.

## Offline and live behavior

The browser independently calculates the selected historical query from the loaded real archive, so saved HTML remains usable without an API. The persistent database is maintained by the running collector application. Its indexed timestamp may lag the latest raw archive briefly while a season update is processed. Both use the same captured Betika FT records.

No external football dataset or synthetic production score is added.

## APIs

- `GET /api/sequences/status`
- `GET /api/sequences/query?pattern=0:0,0:1`
- `GET /api/sequences/catalogue?length=2&offset=0&limit=25`
- `GET /api/export/sequence-index`

## Verification

```bash
python -m pytest -q
python scripts/audit_sequences.py
python tests/browser_sequences.py
```

Tests cover physical-match deduplication, ordered score pairs, missing slots, terminal contexts, no season wrapping, incremental updates/corrections, repeated-refresh stability, vocabulary counts, unobserved patterns, durable backups and pagination. The materialized Python/SQLite index is independently compared with the browser's direct scan of actual source records.
