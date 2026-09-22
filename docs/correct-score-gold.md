# Correct score Gold — v2 data-first app

## The two panels that always load first

### Incoming teams

All recorded incoming teams are derived from actual fixture rows for the source's topmost upcoming season/matchday. A complete eight-fixture set supplies all sixteen names, next opponents and home/away roles. A missing legacy `teams` metadata array no longer empties the roster.

Each team entry shows its latest recorded FT scores. Select an entry or use the **Choose incoming team** dropdown. The list remains available with zero matches, incomplete collection, no fingerprint cache or no comparison API.

### FT scores to compare

The selected team's recorded current-season scores appear as large, wrapping tiles. These come directly from the match records, not from a Gold response.

- Recorded FT remains visible even if an earlier missing matchday prevents a complete MD1-based prefix.
- A captured FT in a round that is still closing can be displayed but is not eligible for comparison yet.
- Live/provisional scores are not passed off as final scores.
- Missing or future FT is labeled as pending, never populated with an invented zero.
- Gold-colored tiles show the chosen comparison input. Other captured results remain readable.

The input tiles do not begin with alignment padding and are not hidden off-screen behind historical offsets. The historical two-row alignment is a separate section below.

## Matching rules — unchanged from the requested v1.2 logic

1. Begin from incoming MD3 once current MD1 and MD2 are finalized.
2. Compare the entire consecutive, closed MD1-based FT prefix.
3. Append every newly finalized round and start again from the complete prefix.
4. Search all valid offsets inside earlier team-season histories, for each incoming team separately.
5. If full-prefix matches exist, retain that full sequence.
6. From incoming MD5 only, a zero-match team excludes its oldest day one at a time until the first matching length is found.
7. Stop immediately on that first successful length. Never go below two results or mix smaller queries into that team's count.
8. Score matching uses literal website FT **home:away** pairs. It does not reverse away-team scores, bridge unknown days, sort results or cross a season boundary.

All historical positions for the chosen query count as matches; the inspection dropdown shows the first 20. One historical team-season may contain more than one matching starting position.

## Historical comparison

The browser computes comparisons locally from the loaded archive. It does not wait for `/api/gold`, `/api/compare`, or a prediction service to show team names and FT inputs.

The original shared alignment component renders the actual current query above the full historical MD1–30 row. Real current-day labels are preserved after excluding older days. Manual starting-position controls recompute agreement, and the input panel stays visible independently of that alignment.

Use **See the exact search path** to inspect the full-prefix attempt and each oldest-day removal. Removing a day from a query does not delete its result.

## Zero results is not missing data

A team can have valid FT inputs and zero historical matches. V2 explicitly distinguishes:

- **Data loaded; matching in progress**
- **Data shown; waiting for two closed rounds**
- **Data loaded; no exact historical match**
- **Exact matches available**

A failure in historical analysis affects only the history section. The roster and the raw FT panel remain available.

## Saved versus live

`League-DNA.html` embeds a real captured workspace. It works with all networking disabled, including in a sandboxed file viewer. Its banner says **Saved Betika snapshot — not a live feed** and includes the capture time.

The live application reads `/api/workspace`, a single coherent snapshot of actual fixtures, match rows, markets, archive coverage and compressed source evidence. When that connection succeeds, newer source observations replace the embedded snapshot. If the API becomes unavailable, the loaded data is retained rather than replaced with empty panels.

The collector remains necessary for new observations and durable SQLite updates. It uses the same verified, rate-limited, read-only Betika feeds; the browser never submits wagers or contacts an account service.

## Verification

- Python regressions cover absent roster metadata, absent materialized blueprints, missing MD1 with later FT present, partly closed rounds, incoming MD1 before matching can begin, and live values kept separate from FT.
- The browser's Gold and four-symbol engines are cross-checked against the Python implementations on a consistent copy of real source data.
- Browser tests verify every available FT tile for all 16 incoming teams, unavailable Gold API routes, zero history, missing prefix, fully offline HTML, compressed receipt inspection, all six pages, opaque `allow-scripts` iframe rendering, and mobile/dark layouts.

Historical equality is not a future-score forecast, a confidence percentage or a wagering recommendation.
