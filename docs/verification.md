# League DNA v2.3 — Historical Replay Lab verification

**Source snapshot audit:** 2026-09-16T00:23:37Z

| Check | Result |
|---|---|
| Full Python regression suite | 102 passed |
| Independent real-data Python/browser replay cases | 30 checked |
| Replay implementation discrepancies | 0 |
| Replay/live-board browser workflows | 5 groups passed |
| Browser JavaScript errors | 0 |
| Finalized source records audited | 5,624 |
| Source-data mismatches | 0 |
| Main SQLite integrity | ok |

## New feature checks

- Only completed earlier target seasons are selectable; cutoff defaults to MD4 and later target scores are hidden
- Reveal shows the genuine archived next FT, changes no reference counts and exposes no later target day
- Cutoff/context changes reset the reveal; exported hidden replay retains chronological separation
- Separate live board shows all 16 teams × 30 source FT slots, without continuation or prediction outputs
- Research explanation, dark mode and 390px layouts work with no horizontal page overflow

The standalone HTML also passed offline cutoff, reveal and 16-team live-board rendering checks. All scores came from retained source records.

## Reference separation

Replay targets must be completed historical seasons before the incoming season. The reference pool excludes the entire target season and all later seasons. Unit tests change the target following score and later-season records and verify that the historical reference counts remain unchanged. Hidden exports omit the actual target continuation; reveal exposes only the already-recorded next target result.

The live FT board displays recorded scores only and has no outcome-recommendation panel. Context-length study is descriptive historical research, not an incoming-fixture score-picking system.

## Preservation

Existing Gold, Single-hit Trail, FT Sequence Explorer, markets, archive, source evidence and collector functionality remain. The original live browser regression suite and prior engine-equivalence checks also passed. No result records or trail rows were deleted; only regenerable comparison caches were compacted before packaging. The Windows timezone dependency remains included.

Python and browser workflows were tested in this workspace. Platform-specific Windows/macOS launchers and Docker configuration were not executed on those platforms here.
