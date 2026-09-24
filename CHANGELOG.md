## 2.9.0 — clickable data-health recovery

* **Data health table is now clickable.** Every season×matchday box in the Data health page can be
  clicked. Boxes that are empty, partial or flagged open an on-demand recovery of that one matchday;
  a **Recover all missing** button queues every recoverable matchday in the archive at once, newest
  season first. A live strip shows what the background worker is fetching, how many are queued,
  recovered, and still missing, and cells pulse while they are being fetched.
* **Background recovery, no browser.** Recovery queries the public results feed that backs Betika's
  results page directly in the background — nothing visible is opened. A matchday is stored only when
  the source answers with that exact day's eight HT/FT finals; a clamped or partial answer is reported
  honestly and never misfiled or invented. The shared one-request-per-second budget is respected, and
  the archive lane is given priority while a recovery queue is draining.
* **First full sweep:** 484 missing matchdays recovered across 62 seasons, reducing
  published-but-unfilled matchdays to zero (1,840 complete matchdays · 14,720 final matches).
* New endpoints: `GET /api/recover/status`, `POST /api/recover/cell`, `POST /api/recover/all`.

