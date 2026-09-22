#!/usr/bin/env python3
"""Browser check for the FT score Prediction Playground board.

Runs the same page twice:

* offline  — straight from the file, with no server and no network, proving the embedded
             snapshot renders on its own (this is how the workspace preview sees it);
* live     — from the running app at /playground, proving the page picks up /api/playground
             and keeps the same DOM contract.

It then asserts the contract the rest of the tooling relies on:

    .fixture[data-fixture][data-team]            one card per fixture of the target matchday
    .pick                                        the blended pick and its probability
    #team-table tbody tr[data-team]              the Live-FT-board style team rows
    #engine-table tbody tr                       one row per engine plus the naive baseline
    #contribution .bar                           the explainability contribution split
    #explain .engine                             one card per engine for the selected fixture
    .heat td                                     the score grid of an engine
    #ledger .day                                 the per-matchday hit ledger
    #confidence tbody tr                          confidence bands
    #reliability svg / #weights svg               the reliability curve and weight history

Usage:  python3 scripts/check_playground.py [--port 8000] [--screenshot DIR]
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOARD = ROOT.parent / "FT score Prediction Playground" / "index.html"
if not BOARD.exists():
    BOARD = ROOT / "FT score Prediction Playground" / "index.html"

CONTRACT = {
    ".fixture[data-fixture][data-team]": 8,
    ".fixture .pick": 16,
    ".fixture .bars .bar": 24,
    "#team-table tbody tr[data-team]": 16,
    "#team-table tbody tr .seq i": 80,
    "#engine-table tbody tr": 8,
    "#contribution .bar": 6,
    "#explain .engine": 6,
    "#explain .engine .heat td": 64,
    "#ledger .day": 10,
    "#confidence tbody tr": 5,
    "#reliability svg polyline": 1,
    "#weights svg polyline": 2,
    "#status-chips .chip": 4,
}


def check(page, label: str, failures: list) -> dict:
    report = {}
    for selector, minimum in CONTRACT.items():
        count = page.eval_on_selector_all(selector, "nodes => nodes.length")
        report[selector] = count
        if count < minimum:
            failures.append(f"{label}: {selector} matched {count}, expected at least {minimum}")
    # clicking the second fixture must move the explainability space onto that team
    target = page.eval_on_selector_all(".fixture", "nodes => nodes[1].dataset.team")
    page.click(".fixture >> nth=1")
    page.wait_for_timeout(250)
    selected = page.eval_on_selector("#explain-head", "node => node.textContent")
    if target not in (selected or ""):
        failures.append(f"{label}: explainability space did not follow the click on {target}")
    pick = page.eval_on_selector(".fixture >> nth=1 >> .pick", "node => node.textContent")
    offline_note = page.eval_on_selector_all("#status-chips .chip", "nodes => nodes.map(n => n.textContent).join(' | ')")
    report["status"] = offline_note
    pick_from_table = page.eval_on_selector("#team-table tbody tr >> nth=0 >> td >> nth=3", "node => node.textContent")
    report["first_table_pick"] = pick_from_table
    report["clicked_team"] = target
    report["clicked_pick"] = pick
    report["engines"] = page.eval_on_selector_all("#engine-table tbody tr", "nodes => nodes.length")
    report["rows"] = page.eval_on_selector_all("#explain .engine .heat td", "nodes => nodes.length")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--screenshot", type=Path, default=None)
    parser.add_argument("--only", choices=["offline", "live"], default=None)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    failures: list = []
    with sync_playwright() as play:
        browser = play.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 1180})
        errors: list = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        if args.only in (None, "offline"):
            page.goto(BOARD.as_uri())
            page.wait_for_timeout(600)
            report = check(page, "offline", failures)
            print("offline:", report)
            if errors:
                failures.append(f"offline page errors: {errors}")
            if args.screenshot:
                page.screenshot(path=str(args.screenshot / "playground-offline.png"), full_page=True)

        if args.only in (None, "live"):
            page.goto(f"http://127.0.0.1:{args.port}/playground", wait_until="load")
            page.wait_for_timeout(1200)
            source = page.eval_on_selector_all("#status-chips .chip", "nodes => nodes.map(n => n.textContent).join(' | ')")
            report = check(page, "live", failures)
            report["source"] = source
            print("live:", report)
            if errors:
                failures.append(f"live page errors: {errors}")
            if "live /api/playground" not in source:
                failures.append(f"live: board did not switch to the API (chips say {source!r})")
            if args.screenshot:
                page.screenshot(path=str(args.screenshot / "playground-live.png"), full_page=True)
        browser.close()

    if failures:
        print("\nFAIL")
        for line in failures:
            print("  -", line)
        return 1
    print("\nOK: the board renders offline and live, and the DOM contract holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
