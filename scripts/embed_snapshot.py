#!/usr/bin/env python3
"""Rebuild the FT score Prediction Playground payload and embed it into the standalone board.

The board at "FT score Prediction Playground/index.html" always carries a full snapshot of
the last build inside the file, so it renders with no server, no network and no build step.
This script regenerates that snapshot:

    python3 scripts/embed_snapshot.py                  # reuse data/playground.json
    python3 scripts/embed_snapshot.py --build          # rebuild the engines first (~1-2 min)
    python3 scripts/embed_snapshot.py --build --window 1200

It writes the snapshot into the page between the <script id="snapshot"> tags, refreshes the
adjacent playground.json, and prints a short summary of what is now on the board.
"""
from __future__ import annotations
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get("LEAGUE_DATA_DIR", str(ROOT / "data")))
def _board_path() -> Path:
    for candidate in (ROOT.parent / "FT score Prediction Playground", ROOT / "FT score Prediction Playground"):
        if (candidate / "index.html").exists():
            return candidate / "index.html"
    return ROOT.parent / "FT score Prediction Playground" / "index.html"


BOARD = _board_path()
BOARD_JSON = BOARD.with_name("playground.json")
PAYLOAD = DATA / "playground.json"
MARKER = re.compile(r'(<script id="snapshot" type="application/json">)(.*?)(</script>)', re.S)


def build(window: int) -> dict:
    from backend.store import Store
    from backend.playground_service import PlaygroundService
    store = Store(DATA / "league.sqlite")
    service = PlaygroundService(store, DATA, window=window)
    began = time.time()
    payload = service.build_payload()
    payload["meta"]["build_seconds"] = round(time.time() - began, 1)
    serialised = json.dumps(payload)
    PAYLOAD.write_text(serialised)
    print(f"built {len(serialised):,} bytes in {payload['meta']['build_seconds']}s "
          f"for target {payload['target']['label']}")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="re-run the engines before embedding")
    parser.add_argument("--window", type=int, default=800, help="matches in the walk-forward window")
    parser.add_argument("--from-file", type=Path, default=None, help="embed an existing payload file")
    args = parser.parse_args()

    if args.build:
        payload = build(args.window)
    else:
        source = args.from_file or PAYLOAD
        if not source.exists():
            print(f"no payload at {source}; run with --build first", file=sys.stderr)
            return 2
        payload = json.loads(source.read_text())

    if not BOARD.exists():
        print(f"board not found: {BOARD}", file=sys.stderr)
        return 2
    html = BOARD.read_text()
    if not MARKER.search(html):
        print("snapshot markers missing from the board", file=sys.stderr)
        return 2
    serialised = json.dumps(payload).replace("</script", "<\\/script")
    BOARD.write_text(MARKER.sub(lambda match: match.group(1) + serialised + match.group(3), html, count=1))
    BOARD_JSON.write_text(serialised)

    backtest = payload.get("backtest") or {}
    models = backtest.get("models") or {}
    champion = (models.get("hit_blend") or {}).get("calibrated") or {}
    print(f"embedded snapshot into {BOARD}")
    print(f"wrote plain snapshot to {BOARD_JSON}")
    print(f"  target matchday : {payload['target']['label']} ({payload['target']['fixtures']} fixtures, "
          f"{payload['target']['markets']} with a market catalogue)")
    print(f"  competition blend: exact hit {champion.get('hit1')}% "
          f"(95% CI {champion.get('hit1_ci')}) on {champion.get('n')} team-fixtures")
    print(f"  engines         : {', '.join(engine['key'] for engine in payload.get('engines', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
