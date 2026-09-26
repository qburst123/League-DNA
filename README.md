# League DNA v2.9.0 — release archive

This branch contains the v2.9.0 release archives for **qburst123/League-DNA**.

## Download

| File | Size | What's inside |
|---|---|---|
| **League-DNA-v2.9.0.zip** | 3.0 MB | Full source tree + offline `League-DNA.html` bundle + live archive data |
| League-DNA-v2.9.0.tar.gz | 2.9 MB | Same as above in tar.gz form |
| League-DNA-v2.9.0-lite.zip | 1.7 MB | Source tree + offline bundle, **no** live archive data |
| League-DNA-v2.9.0-lite.tar.gz | 1.6 MB | Same as above in tar.gz form |
| League-DNA-v2.9.0-server.zip | 1.7 MB | Source tree only — runs as a server, no DB snapshot |
| League-DNA-v2.9.0-server.tar.gz | 1.6 MB | Same as above in tar.gz form |

## Direct download links

```
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0.zip
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0.tar.gz
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0-lite.zip
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0-lite.tar.gz
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0-server.zip
https://raw.githubusercontent.com/qburst123/League-DNA/releases/v2.9.0/League-DNA-v2.9.0-server.tar.gz
```

## To run after downloading

```bash
unzip League-DNA-v2.9.0.zip        # or download the .tar.gz and run tar -xzf
cd League-DNA-v2.9.0
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python run.py                      # FastAPI on http://localhost:8000
# open http://localhost:8000/ → click "Betika virtual edge analyzer" in the sidebar
```

For **offline browsing only** (no server), extract and open the included `League-DNA.html` in a browser.

## What's new in v2.9.0

- **Betika Edge Analyzer** — integrated as a native React workspace. FastAPI service at `backend/betika_edge/` (11 modules), 18 new routes registered in `backend/app.py`, React component at `frontend/src/BetikaEdge.jsx` (10 tabs).
- 16 BSL teams, 23 market families, H2H aggregations, bivariate Poisson/Dixon–Coles, no-vig + cross-market + EV, two-market paper ledger with strategy + entry rule, matchday 1–30 tracker.
- Auto-bootstraps from `tests/fixtures/` on first request; analyzer-owned `data/edge.sqlite`, reuses existing `data/league.sqlite` for live reads.

## Source

The source code lives on the `arena/01a0da8f-league-dna` branch:
https://github.com/qburst123/League-DNA/tree/arena/01a0da8f-league-dna

## Disclaimer

Betika Virtual is RNG-driven. The analyzer models historical market structure, it does **not** predict RNG outcomes and does **not** guarantee profit. All settlement is paper-tracking.
