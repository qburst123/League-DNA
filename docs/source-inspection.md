# Public-source inspection record

**Inspection date:** 14 September 2026 (Africa/Nairobi)

**Requested page:** `https://www.betika.com/lite/en-ke/virtual`

## Identity verification

The Lite page displayed “Betika League” with these teams: Ba Zoo BSL, Bandarini BSL, Batoto BSL, Bidii Co. BSL, Gor BSL, KCBB BSL, Kach BSL, Kanairo Stars, Leopard Sakata, P Rangers BSL, Sakata Mathare, Sakata Nzoia, Sakata Sharks, Sakata Wazito, Tuska Sakata and Walinzi BSL.

The first-party public competition feed returned:

```json
{"competition_id":"26","competition_name":"Betika Sakata League"}
```

Other competition IDs belonged to different leagues and were not collected. A direct comparison between the Lite results table and competition 26's JSON confirmed all 16 names and all eight HT/FT pairs for the requested starting season, matchday 1:

| Home | Away | HT | FT |
|---|---|---|---|
| Ba Zoo BSL | Bandarini BSL | 0:2 | 0:2 |
| Gor BSL | Tuska Sakata | 2:0 | 2:0 |
| Sakata Wazito | Sakata Nzoia | 1:1 | 1:1 |
| P Rangers BSL | Kanairo Stars | 2:0 | 2:0 |
| Batoto BSL | Sakata Sharks | 0:0 | 1:1 |
| Kach BSL | Bidii Co. BSL | 3:0 | 3:0 |
| Walinzi BSL | KCBB BSL | 0:1 | 0:2 |
| Leopard Sakata | Sakata Mathare | 1:1 | 1:1 |

The extracted Lite table and original JSON are kept as regression fixtures in `tests/fixtures/lite-crosscheck.json` and `historical-results.json`. They are real source captures, not generated examples.

## Lite interface findings

- The results page's `select[name="season"]` publishes the available seasons.
- `select[name="matchday"]` publishes matchdays for the selected season.
- The page's filter handler uses `?season=<id>&matchday=<number>`.
- `.virtual__results__results` contains the visible team names, halftime and fulltime scores.
- `.virtual__ongoing__matches` contains the ongoing matchday. Its Lite score cells can be blank even when the JSON feed has ongoing observations; blank cells were not interpreted as zero.
- `.virtual__matchdays__item` groups upcoming matchdays. The first group is the topmost upcoming matchday.
- Fixture elements expose `parent_virtual_id`, `home_team`, `away_team`, `start_time` and displayed odds.
- `window.GEOLOCATION` was `Africa/Nairobi`.
- Full match detail pages contain public market display data in `window.side_bets`.

The root Lite URL initially returned a 403 to one basic request, while the site's own canonical `?ck=1` navigation URL and the public JSON routes were accessible. No challenge, authentication mechanism or access restriction was bypassed. Production collection uses the verified public JSON host and backs off on a refusal.

## How the JSON routes were located

The public website bootstrap listed `app.e55e678899d9c582e5e8.js`. That bundle's configuration set:

```text
virtualsUrl: https://virtuals.betika.com/v1/
```

Public virtual-football route chunks contained the following read-only GET requests:

| Public chunk inspected | Read route |
|---|---|
| 58 — virtual league container | `VIRTUALS_URL + "competition"` |
| 19 — match list | `VIRTUALS_URL + "matches?competition_id=" + id` |
| 45 — ongoing matches | `VIRTUALS_URL + "matches/ongoing?competition_id=" + id` |
| 102 — results | `VIRTUALS_URL + "matches/results?season=...&matchday=...&competition_id=..."` |
| 59 — market detail | `VIRTUALS_URL + "match?id=" + eventId` |

These are implementation details observed on this date, not an assurance of a permanently supported public API. They may change. No wagering or account method is connected to the application, and no third-party football feed is used.

## Verified response contract

### Upcoming fixtures

`data` is an ordered mapping from matchday keys to lists of fixture objects. Every fixture supplies its own `season` and `match_day`. The application takes the first group; it does not sort numeric matchdays across a season boundary or assume `current season + 1` is a valid next-season ID.

Read fields include `parent_virtual_id`, `home_team`, `away_team`, `start_time`, `remaining_time`, `competition_id`, `home_odd`, `neutral_odd`, `away_odd` and summary `markets`.

The source displayed countdown values such as `-00:02:21` for a future kickoff. The application therefore derives the countdown from the source's absolute Nairobi start time and retains the raw timer string only as evidence.

### Results

`data.query` identifies the **actual returned** season and matchday. The adapter rejects a response that silently returns another season/day instead of the requested one.

`data.results` supplies team names, `ht_score`, `ft_score`, optional `saved_ht_score` / `saved_ft_score`, and `meta.status` / `meta.match_status`. Historical finalized records were observed with `ended` status and saved HT/FT values. During an ongoing day the same endpoint could return fewer than eight rows and unsaved provisional scores. It is not safe to assume that every nonempty results response is final.

**Important:** `first_half_score` is used by the website's **first-goal** display. In the inspected Ba Zoo BSL–Bandarini BSL record it was `0:1`, while the actual halftime score was `0:2`. The collector uses the correct saved/HT fields instead.

`data.last_10_seasons` contained non-consecutive IDs. At first inspection, the relevant range was:

```text
3134579, 3134547, 3134516, 3134487, 3134462,
3134430, 3134406, 3134373, 3134345
```

The list also included an older season outside the requested lower bound; it was not ingested.

### Ongoing observations

`data` is a list of records carrying `parent_virtual_id`, season, matchday, team names, `ht_score`, `ft_score`, `status`, `match_status` and `match_time`.

An observed record with `status: live`, `match_status: started` and `ft_score: 0:1` was stored as an **observed live score**, not a finalized 0:1 result. Final values and live values have separate database columns.

### Full markets

`data` is the full catalogue of named markets and their `odds`. `meta` identifies the fixture. The number of published markets can vary; the UI displays the collected count, not a hard-coded 22 or 23.

Display labels, decimal strings and specifiers are preserved. The browser receives only reference data; odds are not selectable buttons and no bet-placement payload is generated.

## Collection limitations

- Public history discovery is a rolling list, not a guaranteed unbounded archive index.
- Provider responses, timing and finalization can be delayed or revised.
- Polling is not a provider push subscription; there is no zero-latency guarantee.
- The runtime respects HTTP 401/403/429 and network failures. Data remains accessible from SQLite while the source is unavailable.
- The captured payload hash verifies storage consistency, not a provider-signed authenticity guarantee.
