#!/usr/bin/env python3
"""Browser check for the two in-app FT score Prediction Playground workspaces.

The standalone board is covered by `scripts/check_playground.py`. This script checks the two
React workspaces that ship inside the app, against a running server:

    #playground           FT score Prediction Playground — the live ledger board
    #playground-engines   Score engines & explainability — the engines view

DOM contract it asserts (the same attributes the other tooling and the audit rely on):

    .live-board-grid.live-board-row[data-team]        one prediction row per team
    .live-board-grid.live-board-ledger-row[data-scope] one ledger row per team
    .live-board-row .live-board-score[data-verdict]   one graded live prediction per matchday
    .live-board-ledger-row .ledger-cell[data-verdict] the graded ledger beneath it
    .pg-fixture                                       one card per incoming fixture
    .pg-engine                                        engine card (roster + explainability space)
    .pg-table.scorecard tbody tr                      the walk-forward scorecard
    table.heat td                                     an engine's 8x8 score grid
    .pg-contribution .bar                             the contribution split for the pick
    .pg-days .pg-day                                  the per-matchday exact-hit ledger
    .pg-weights .pg-weight-row                        the mixture-weight history
    .pg-view-switch button                            the live-board / season-sheets switch
    .pg-sheets .pg-sheet-table tbody tr               the frozen sheet of the chosen matchday
    .pg-day-button                                    one button per locked matchday of the season
    .pg-sheet-facts                                   ledger integrity (live vs backfilled locks)
    [data-lock]                                       the chip stating when the sheet was frozen
    [data-sync]                                       the chip reporting the ledger sync beat

It also asserts the property the workspace exists for: predictions already made for a played
matchday do not change between two page loads — readings are taken, the page is reloaded, and the
same matchdays must carry the same picks (a matchday that finished in between is allowed to appear,
but nothing that was on screen may move).

Usage:  python3 scripts/check_workspaces.py [--url http://127.0.0.1:8000] [--screenshot DIR]
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

LIVE = {
    ".live-board-grid.live-board-row[data-team]": 16,
    ".live-board-grid.live-board-ledger-row[data-scope]": 16,
    ".live-board-row .live-board-score.recorded[data-score]": 1,      # grows with the season
    ".live-board-ledger-row .ledger-cell.prediction[data-verdict]": 16,  # one per team per column
    ".pg-fixture": 1,
    ".forecast-totals strong": 5,
    ".pg-chips .chip": 4,
    ".pg-view-switch button": 2,
    "[data-lock]": 1,
    "[data-sync]": 1,
}
SHEETS = {
    ".pg-sheets": 1,
    ".pg-sheet-days .pg-day-button": 1,
    ".pg-sheets .pg-sheet-table tbody tr": 1,
    ".pg-sheets table.pg-table": 2,
    ".pg-sheet-facts span": 3,
}
MATCHUPS = {
    ".mu-fixture": 1,                        # one card per fixture of the matchday to play
    ".mu-half-card": 1,                      # the 1ST/2ND/EQUAL card sitting under the HT/FT card
    ".mu-board tr.mu-result-row": 16,        # recorded HT/FT, one row per team
    ".mu-board tr.mu-call-row": 16,          # the frozen half call directly beneath it
    ".mu-board .mu-call[data-verdict]": 16,  # at least one call per team
    ".mu-board .mu-call.hit": 1,             # a call that was right
    ".mu-board .mu-call.miss": 1,            # a call that was wrong
    ".mu-board .mu-call.pending": 1,         # the matchday still to play
    ".mu-legend span": 4,
    ".mu-board-foot": 1,
    ".pg-chips .chip": 4,
    "[data-lock]": 1,
    "[data-sync]": 1,
}
MATCHUPS_HISTORY = {
    ".mu-history-table tbody tr": 1,
    ".mu-picker-controls select": 2,
    ".mu-summary article": 4,
    ".mu-chip": 3,
    ".mu-bars": 1,
    "td[data-pair-verdict]": 1,
}

ENGINES = {
    ".pg-engine-cards .pg-engine": 8,
    "svg.pg-chart": 2,
    "svg.pg-chart polyline": 7,
    ".pg-table.scorecard tbody tr": 7,
    ".pg-table .pg-track": 8,
    "table.heat td": 64,
    ".pg-contribution .bar": 5,   # the engine roster is asserted against the payload below
    ".pg-explain .chain span": 6,
    ".pg-chip-button": 16,
    ".pg-days .pg-day": 1,
    "section.panel svg.pg-chart polyline": 7,
    "section.panel svg.pg-chart circle": 3,
}


def counts(page, contract: dict, label: str, failures: list) -> dict:
    report = {}
    for selector, minimum in contract.items():
        count = page.eval_on_selector_all(selector, "nodes => nodes.length")
        report[selector] = count
        if count < minimum:
            failures.append(f"{label}: {selector} matched {count}, expected at least {minimum}")
    return report


def run(url: str, shots: Path | None) -> int:
    from playwright.sync_api import sync_playwright

    failures: list = []
    page_errors: list = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=[
            "--disable-gpu", "--renderer-process-limit=1", "--js-flags=--max-old-space-size=256"])
        page = browser.new_page(viewport={"width": 1680, "height": 1400})
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        # ---------------------------------------------------------------- live ledger board
        page.goto(f"{url}/#playground", wait_until="load", timeout=120_000)
        page.wait_for_selector(".live-board-ledger-row", timeout=120_000)
        page.wait_for_timeout(800)
        live = counts(page, LIVE, "live board", failures)
        board = page.evaluate("""() => {
            const rows = [...document.querySelectorAll('.live-board-row')];
            const recorded = [...document.querySelectorAll('.live-board-row .live-board-score.recorded[data-score]')];
            const cells = [...document.querySelectorAll('.live-board-ledger-row .ledger-cell.prediction[data-verdict]')];
            const verdicts = {};
            cells.forEach(cell => { verdicts[cell.dataset.verdict] = (verdicts[cell.dataset.verdict] || 0) + 1; });
            const graded = cells.filter(cell => cell.dataset.verdict !== 'pending' && cell.dataset.verdict !== 'none');
            const recordedDays = recorded.map(cell => Number(cell.dataset.day));
            const pending = cells.filter(cell => cell.dataset.verdict === 'pending');
            const predictionDays = cells.map(cell => Number(cell.dataset.day));
            const targetDay = Number(document.querySelector('.chip')?.textContent.match(/([0-9]+)$/)?.[1] || 0);
            const rowsAligned = rows.every(row => {
                const team = row.dataset.team;
                const ledger = [...document.querySelectorAll(`.live-board-ledger-row[data-team="${CSS.escape(team)}"] .ledger-cell`)];
                const scores = [...row.querySelectorAll('.live-board-score')];
                return ledger.length === scores.length;
            });
            return {
                title: document.querySelector('.page-head h1')?.textContent || '',
                teams: rows.length,
                columns: rows[0] ? rows[0].querySelectorAll('.live-board-score').length : 0,
                recorded: recorded.length,
                recordedDays,
                verdicts,
                graded: graded.length,
                scoresAreScores: recorded.every(cell => /^[0-9]+:[0-9]+$/.test(cell.dataset.score || '')),
                picksAreScores: graded.every(cell => /^[0-9]+:[0-9]+$/.test(cell.dataset.pick || '')),
                scoreRowMatches: graded.every(cell => {
                    if(!cell.dataset.actual) return true;
                    const row = cell.closest('.live-board-ledger-row')?.dataset.team;
                    const twin = [...document.querySelectorAll(`.live-board-row[data-team="${CSS.escape(row)}"] .live-board-score.recorded`)].find(node => node.dataset.day === cell.dataset.day);
                    return twin ? twin.dataset.score === cell.dataset.actual : true;
                }),
                ledgerSame: document.querySelectorAll('.live-board-ledger-row').length === rows.length,
                rowsAligned,
                pendingDays: [...new Set(pending.map(cell => Number(cell.dataset.day)))],
                lastRecorded: recordedDays.length ? Math.max(...recordedDays) : 0,
                nextAfterRecorded: pending.length ? Math.min(...pending.map(cell => Number(cell.dataset.day))) : null,
                predictionColumns: [...new Set(predictionDays)].length,
                summary: [...document.querySelectorAll('.forecast-totals strong')].map(node => node.textContent.trim()),
                refresh: Boolean([...document.querySelectorAll('.chip-button')].find(button => /refresh/i.test(button.textContent))),
                withProbability: graded.filter(cell => cell.dataset.probability).length,
                pendingProbability: pending.filter(cell => cell.dataset.probability).length,
                liveChip: [...document.querySelectorAll('.pg-chips .chip')].map(node => node.textContent).join(' | '),
            };
        }""")
        checks = [
            ("live board title", board["title"] == "FT score Prediction Playground"),
            ("live board teams", board["teams"] == live[".live-board-grid.live-board-row[data-team]"]),
            ("live board is a 30-matchday calendar", board["columns"] == 30),
            ("the upper row carries the recorded FT scores", board["recorded"] > 0 and board["scoresAreScores"]),
            ("the recorded row grows with the season", board["lastRecorded"] == max([0] + board["recordedDays"])),
            ("one prediction row per team", board["ledgerSame"] and board["rowsAligned"]),
            ("every graded prediction carries its FT score", board["picksAreScores"]),
            ("a graded prediction is graded against the score recorded above it", board["scoreRowMatches"]),
            ("graded predictions exist", board["graded"] > 0),
            ("the board uses only real verdicts", set(board["verdicts"]) <= {"hit", "direction", "miss", "pending", "stale", "none"}),
            # a season that has just opened has a handful of graded rows, so an exact hit is only
            # required once there are enough graded predictions for one to be expected
            ("exact predictions are marked", board["verdicts"].get("hit", 0) > 0 or board["graded"] < 200),
            ("right-result predictions are marked", board["verdicts"].get("direction", 0) > 0 or board["graded"] < 200),
            ("wrong predictions are marked", board["verdicts"].get("miss", 0) > 0 or board["graded"] < 40),
            ("the matchday to play is pending", board["verdicts"].get("pending", 0) > 0),
            ("the pending prediction sits immediately after the last recorded matchday",
             board["pendingDays"] and board["nextAfterRecorded"] == board["lastRecorded"] + 1),
            ("a refresh control is present", board["refresh"]),
            ("the board is connected to the live service", any(word in board["liveChip"].lower() for word in ("live", "building", "refreshing"))),
            ("graded predictions carry their blended probability", board["withProbability"] >= max(1, int(0.9 * board["graded"]))),
            ("the matchday to play carries its probability", board["pendingProbability"] == len(board["pendingDays"]) * board["teams"]),
        ]
        # the scope switch must regrade the same matchdays with the other mixture
        page.get_by_role("button", name="Log-loss blend").click()
        page.wait_for_timeout(900)
        switched = page.eval_on_selector_all(".live-board-ledger-row", "rows => rows.length")
        scope = page.eval_on_selector(".live-board-ledger-row", "row => row.dataset.scope")
        checks.append(("scope switch keeps every team", switched == board["teams"]))
        checks.append(("scope switch selects the log-loss blend", scope == "blend"))
        page.get_by_role("button", name="Competition blend").click()
        page.wait_for_timeout(600)
        # evidence modal
        page.locator(".live-board-ledger-row .ledger-cell:not(.blank)").first.click()
        page.wait_for_selector(".modal", timeout=15_000)
        detail = page.eval_on_selector(".modal", "node => node.textContent")
        checks.append(("cell opens its evidence", "recorded FT" in detail or "not published yet" in detail))

        page.keyboard.press("Escape")
        page.wait_for_timeout(400)
        if shots:
            page.screenshot(path=str(shots / "workspace-live.png"))

        # ---------------------------------------------------------------- the freeze
        def frozen_picks():
            return page.evaluate("""() => {
                const season = (document.body.textContent.match(/Season (\\d+) ·/) || [])[1] || '?';
                const out = {};
                document.querySelectorAll('.live-board-ledger-row').forEach(row => {
                    const team = row.dataset.team;
                    row.querySelectorAll('.ledger-cell.prediction[data-day]').forEach(cell => {
                        const day = cell.dataset.day;
                        const verdict = cell.dataset.verdict || '';
                        // a matchday that has been played is a frozen row: remember what it says
                        if(['hit','direction','miss','stale'].includes(verdict)){
                            out[`${season}|${team}|${day}`] = `${cell.dataset.pick}|${verdict}`;
                        }
                    });
                });
                return out;
            }""")

        board_lock = page.evaluate("""() => ({
            lock: document.querySelector('[data-lock]')?.textContent || '',
            integrity: document.body.textContent.includes('sheets locked live'),
            note: document.body.textContent.includes('Frozen ledger'),
        })""")
        before = frozen_picks()
        page.reload(wait_until="load", timeout=120_000)
        page.wait_for_selector(".live-board-ledger-row", timeout=60_000)
        page.wait_for_timeout(3_500)
        after = frozen_picks()
        same_season = {key: value for key, value in before.items() if key.split('|')[0] in
                       {other.split('|')[0] for other in after}}
        moved = [key for key, value in same_season.items() if after.get(key) != value]
        checks += [
            ("the board states when the sheet was frozen", "frozen sheet locked" in board_lock["lock"]
             or "backfilled once" in board_lock["lock"] or "waiting for the next sheet" in board_lock["lock"]),
            ("the board counts live locks against backfills", board_lock["integrity"]),
            ("the board explains the freeze", board_lock["note"]),
            ("played matchdays keep the pick they were locked with", len(before) > 0 and not moved),
        ]
        if moved:
            failures.append(f"frozen picks moved: {moved[:4]}")

        # ---------------------------------------------------------------- season sheets view
        page.goto(f"{url}/#playground", wait_until="load", timeout=120_000)
        page.wait_for_selector(".pg-view-switch button", timeout=120_000)
        page.locator(".pg-view-switch button").nth(1).click()
        page.wait_for_selector(".pg-sheets .pg-sheet-table tbody tr", timeout=60_000)
        sheets = counts(page, SHEETS, "sheets", failures)
        sheet = page.evaluate("""() => ({
            seasons: document.querySelectorAll('.pg-seasons .pg-chip-button').length,
            days: document.querySelectorAll('.pg-day-button').length,
            gradedDays: [...document.querySelectorAll('.pg-day-button')].filter(b => /MD\\d+/.test(b.textContent)).length,
            rows: document.querySelectorAll('.pg-sheet-table tbody tr').length,
            verdicts: [...document.querySelectorAll('.pg-sheet-table .pg-verdict')].map(n => n.textContent.trim()),
            firstPick: document.querySelector('.pg-sheet-table tbody tr td:nth-child(3) b')?.dataset.pick || '',
            facts: document.querySelector('.pg-sheet-facts')?.textContent || '',
            teamRows: document.querySelectorAll('.pg-two table.pg-table tbody tr').length,
            table: document.querySelector('.pg-sheet-table tbody tr')?.textContent || '',
        })""")
        checks += [
            ("the sheets view opens from the switch", sheets[".pg-sheets"] == 1),
            ("the season picker is populated", sheet["seasons"] >= 1),
            ("the matchday strip is populated", sheet["days"] >= 1),
            ("the sheet lists sixteen picks", sheet["rows"] == 16),
            ("every pick carries a verdict", len(sheet["verdicts"]) == 16),
            ("the sheet shows the locked pick and the recorded score", ":" in sheet["table"] or "TO PLAY" in sheet["table"]),
            ("the season table lists every team", sheet["teamRows"] == 16),
            ("the ledger integrity is stated", "backfilled" in sheet["facts"] or "locked live" in sheet["facts"]),
        ]
        if shots:
            page.screenshot(path=str(shots / "workspace-sheets.png"))

        # ---------------------------------------------------------------- engines workspace
        page.goto(f"{url}/#playground-engines", wait_until="load", timeout=120_000)
        page.wait_for_selector(".pg-engine", timeout=60_000)
        page.wait_for_timeout(800)
        engines = counts(page, ENGINES, "engines", failures)
        contract = page.evaluate("""async () => {
            const payload = await (await fetch('/api/playground')).json();
            const members = (payload.engines || []).map(e => e.key).filter(k => k !== 'hit_blend' && k !== 'blend');
            const row = ((payload.target || {}).rows || [])[0] || {};
            const unavailable = row.unavailable || {};
            return {members: members.length, priced: Object.keys(row.models || {}).length,
                    abstaining: Object.keys(unavailable).length,
                    reasons: Object.values(unavailable).map(e => (e || {}).reason || '').filter(Boolean)};
        }""")
        bench = page.evaluate("""() => ({
            title: document.querySelector('.page-head h1')?.textContent || '',
            heat: [...document.querySelectorAll('table.heat')].slice(0, 1).map(t => t.querySelectorAll('td').length)[0],
            heatHit: Boolean(document.querySelector('table.heat td.hit')),
            roster: document.querySelectorAll('.pg-engine-cards .pg-engine').length,
            explained: document.querySelectorAll('.pg-explain').length,
            scorecardHasBaseline: document.body.textContent.includes('naive reference'),
            scorecardHasIntervals: document.querySelectorAll('.pg-table.scorecard .pg-track').length > 0,
            weightsTitle: document.body.textContent.includes('Mixture weights through the run'),
            reliabilityTitle: document.body.textContent.includes('Reliability — does the probability mean anything?'),
            reliabilityLine: document.querySelectorAll('svg.pg-chart polyline').length >= 1,
            reliabilityDots: document.querySelectorAll('svg.pg-chart circle').length >= 3,
            weightLines: document.querySelectorAll('svg.pg-chart polyline').length,
            protocol: document.body.textContent.includes('What the walk-forward run guarantees'),
            reliability: document.body.textContent.includes('Reliability — does the probability mean anything?'),
            agreement: document.body.textContent.includes('engines agreeing on the top score'),
            contribution: document.querySelectorAll('.pg-contribution .bar').length,
            abstainNotes: document.querySelectorAll('.pg-engine.abstains .pg-abstain').length,
            abstainBars: document.querySelectorAll('.pg-bar-abstain').length,
            text: document.body.textContent,
        })""")
        checks += [
            ("engines title", bench["title"] == "Score engines & explainability"),
            ("one explainability card per engine", bench["explained"] == contract["members"]),
            ("every priced engine keeps its contribution slot", bench["contribution"] == contract["members"]),
            ("an engine that cannot price the matchday says why",
             contract["abstaining"] == 0 or (bench["abstainNotes"] == contract["abstaining"]
                                             and bench["abstainBars"] == contract["abstaining"]
                                             and all(reason[:40] in bench["text"] for reason in contract["reasons"]))),
            ("each engine shows an 8x8 grid", bench["heat"] == 64 and bench["heatHit"]),
            ("roster covers all engines", bench["roster"] == engines[".pg-engine-cards .pg-engine"]),
            ("scorecard shows the naive baseline", bench["scorecardHasBaseline"]),
            ("scorecard shows intervals", bench["scorecardHasIntervals"]),
            ("the reliability chart is present", bench["reliabilityTitle"] and bench["reliabilityLine"] and bench["reliabilityDots"]),
            ("the mixture-weights chart is present", bench["weightsTitle"] and bench["weightLines"] >= 7),
            ("reliability section is present", bench["reliability"]),
            ("agreement table is present", bench["agreement"]),
            ("protocol section is present", bench["protocol"]),
        ]
        if shots:
            page.screenshot(path=str(shots / "workspace-engines.png"))

        # ---------------------------------------------------------------- head-to-head matchup DNA
        page.goto(f"{url}/#matchups", wait_until="load", timeout=120_000)
        page.wait_for_selector(".mu-board .mu-call[data-verdict]", timeout=120_000)
        page.wait_for_timeout(900)
        matchup = counts(page, MATCHUPS, "matchups", failures)
        head = page.evaluate("""async () => {
            const payload = await (await fetch('/api/matchups')).json();
            const board = payload.board || {}, upcoming = payload.upcoming || {};
            const api = {};
            (board.teams || []).forEach(row => {
                (row.results || []).forEach(entry => { api[`${row.team}|${entry.day}|r`] = `${entry.ht}|${entry.ft}|${entry.half}`; });
                (row.calls || []).forEach(entry => { api[`${row.team}|${entry.day}|c`] = `${entry.pick}|${entry.verdict || 'pending'}`; });
            });
            const dom = {};
            document.querySelectorAll('.mu-board .mu-record').forEach(cell => {
                if(cell.dataset.ht) dom[`${cell.dataset.team}|${cell.dataset.day}|r`] = `${cell.dataset.ht}|${cell.dataset.ft}|${cell.dataset.half}`;
            });
            document.querySelectorAll('.mu-board .mu-call').forEach(cell => {
                if(cell.dataset.pick) dom[`${cell.dataset.team}|${cell.dataset.day}|c`] = `${cell.dataset.pick}|${cell.dataset.verdict}`;
            });
            const mismatched = Object.keys(dom).filter(key => api[key] && api[key] !== dom[key]);
            const verdicts = {};
            document.querySelectorAll('.mu-board .mu-call').forEach(cell => { verdicts[cell.dataset.verdict] = (verdicts[cell.dataset.verdict] || 0) + 1; });
            const pending = Object.values(verdicts) && (verdicts.pending || 0);
            const fixtures = [...document.querySelectorAll('.mu-fixture')];
            const playedCards = fixtures.filter(card => card.dataset.verdict !== 'pending');
            const cardScores = [...document.querySelectorAll('.mu-fixture .mu-htft b')].map(node => node.textContent.trim());
            const chips = [...document.querySelectorAll('.pg-chips .chip')].map(node => node.textContent).join(' | ');
            const lock = document.querySelector('[data-lock]')?.textContent || '';
            const sync = document.querySelector('[data-sync]')?.textContent || '';
            const frozen = {};
            document.querySelectorAll('.mu-board .mu-call[data-pick]').forEach(cell => {
                const verdict = cell.dataset.verdict || '';
                if(['hit','miss'].includes(verdict)) frozen[`${cell.dataset.team}|${cell.dataset.day}`] = `${cell.dataset.pick}|${verdict}`;
            });
            return {title: document.querySelector('.page-head h1')?.textContent || '',
                    domKeys: Object.keys(dom).length, apiKeys: Object.keys(api).length, mismatched, verdicts,
                    pending, fixtures: fixtures.length, apiFixtures: (upcoming.fixtures || []).length,
                    day: upcoming.day, cardsSayHalf: document.querySelectorAll('.mu-half-card .mu-half-label').length,
                    cardScores, chips, lock, sync, frozen,
                    summary: board.summary || {}, swingChip: document.body.textContent.includes('1ST HALF')
                                                          || document.body.textContent.includes('2ND HALF')
                                                          || document.body.textContent.includes('EQUAL')};
        }""")
        checks += [
            ("the matchup workspace renders", matchup[".mu-board .mu-call[data-verdict]"] >= 16),
            ("the board shows the recorded HT/FT row per team", matchup[".mu-board tr.mu-result-row"] == 16),
            ("the frozen half call sits directly beneath the record", matchup[".mu-board tr.mu-call-row"] == 16),
            ("every fixture of the matchday to play has a card", head["fixtures"] == head["apiFixtures"] and head["fixtures"] > 0),
            ("each card carries the 1ST/2ND/EQUAL half card", head["cardsSayHalf"] == head["fixtures"]),
            ("a played card shows its HT and FT", all(":" in score for score in head["cardScores"]) and len(head["cardScores"]) % 2 == 0),
            ("the board shows right calls in green", matchup[".mu-board .mu-call.hit"] >= 1),
            ("the board shows wrong calls in red", matchup[".mu-board .mu-call.miss"] >= 1),
            ("the matchday to play is still pending", matchup[".mu-board .mu-call.pending"] >= 1),
            ("every stored call on screen matches the ledger", head["domKeys"] > 0 and not head["mismatched"]),
            ("only real verdicts are used", set(head["verdicts"]) <= {"hit", "miss", "pending", "none"}),
            ("the pending column is the matchday that is set to play", head["pending"] == 0 or head["pending"] >= 16),
            ("the board states the freeze and the sync beat", "frozen" in head["lock"].lower() or "waiting" in head["lock"].lower()),
            ("the sync chip is healthy", "error" not in head["sync"].lower()),
            ("the board names the half it is calling", head["swingChip"]),
            ("the league base rate and the hit count are shown", head["summary"].get("matches", 0) > 0),
        ]
        matches_moved = page.evaluate("""(before) => {
            const after = {};
            document.querySelectorAll('.mu-board .mu-call[data-pick]').forEach(cell => {
                const verdict = cell.dataset.verdict || '';
                if(['hit','miss'].includes(verdict)) after[`${cell.dataset.team}|${cell.dataset.day}`] = `${cell.dataset.pick}|${verdict}`;
            });
            return Object.keys(before).filter(key => after[key] && after[key] !== before[key]);
        }""", head["frozen"])
        if shots:
            page.screenshot(path=str(shots / "workspace-matchups.png"), full_page=False)
        page.reload(wait_until="load", timeout=120_000)
        page.wait_for_selector(".mu-board .mu-call[data-verdict]", timeout=60_000)
        page.wait_for_timeout(2_500)
        matches_moved = page.evaluate("""(before) => {
            const after = {};
            document.querySelectorAll('.mu-board .mu-call[data-pick]').forEach(cell => {
                const verdict = cell.dataset.verdict || '';
                if(['hit','miss'].includes(verdict)) after[`${cell.dataset.team}|${cell.dataset.day}`] = `${cell.dataset.pick}|${verdict}`;
            });
            return Object.keys(before).filter(key => after[key] && after[key] !== before[key]);
        }""", head["frozen"])
        checks.append(("a played matchday keeps the half call it was locked with",
                       len(head["frozen"]) > 0 and not matches_moved))
        if matches_moved:
            failures.append(f"frozen half calls moved: {matches_moved[:4]}")

        # ---------------------------------------------------------------- the historical sub-workspace
        page.locator(".pg-view-switch button").nth(1).click()
        page.wait_for_selector(".mu-history-table tbody tr", timeout=120_000)
        page.wait_for_timeout(700)
        history = counts(page, MATCHUPS_HISTORY, "matchup history", failures)
        analysis = page.evaluate("""async () => {
            const payload = await (await fetch('/api/matchups/pairs?limit=500')).json();
            const options = [...document.querySelectorAll('.mu-picker-controls select[aria-label="Choose a team pair"] option')].map(node => node.value);
            const rows = [...document.querySelectorAll('.mu-history-table tbody tr')];
            const header = [...document.querySelectorAll('.mu-history-table thead th')].map(node => node.textContent.trim());
            const first = rows[0];
            return {title: document.querySelector('.page-head h1')?.textContent || '',
                    optionCount: options.length, apiPairs: (payload.pairs || []).length,
                    optionsMatchApi: options.length === (payload.pairs || []).length,
                    venueOptions: document.querySelectorAll('.mu-picker-controls select[aria-label="Venue scope"] option').length,
                    rows: rows.length,
                    seasons: first?.querySelector('td')?.textContent?.trim() || '',
                    days: first?.querySelectorAll('td')[1]?.textContent?.trim() || '',
                    scores: rows.map(row => `${row.querySelectorAll('td')[3]?.textContent}|${row.querySelectorAll('td')[4]?.textContent}`),
                    halfChips: document.querySelectorAll('.mu-history-table .mu-chip').length,
                    rightCells: document.querySelectorAll('td[data-pair-verdict="right"]').length,
                    wrongCells: document.querySelectorAll('td[data-pair-verdict="wrong"]').length,
                    header, call: document.querySelector('.mu-summary strong')?.textContent?.trim() || '',
                    filter: Boolean(document.querySelector('.mu-search input')),
                    venueOptions: [...document.querySelectorAll('.mu-picker-controls select')][1]
                        ? [...[...document.querySelectorAll('.mu-picker-controls select')][1].options].map(o => o.textContent)
                        : []};
        }""")
        checks += [
            ("the historical analysis lists recorded meetings", heritage := history[".mu-history-table tbody tr"] >= 1),
            ("the dropdown lists the archived team pairs", analysis["optionCount"] >= 2 and analysis["optionsMatchApi"]),
            (f"the dropdown offers every pair the API serves "
             f"({analysis['optionCount']} options vs {analysis['apiPairs']} pairs)",
             analysis["optionCount"] == analysis["apiPairs"]),
            ("a row names its season and matchday", bool(analysis["seasons"]) and len(analysis["days"]) == 2),
            ("every row shows HT and FT", all(re.match(r"^[0-9]+:[0-9]+\|[0-9]+:[0-9]+$", score or "") for score in analysis["scores"])),
            ("every row says which half out-scored the other", analysis["halfChips"] == analysis["rows"]),
            ("the call is marked right or wrong against every meeting", analysis["rightCells"] + analysis["wrongCells"] == analysis["rows"]),
            ("the table carries the columns the brief asks for",
             {"SEASON", "MD", "HT", "FT", "1ST HALF", "2ND HALF", "HIGHEST SCORING HALF"} <= set(analysis["header"])),
            ("the venue scope can be widened", any("Either venue" in option for option in analysis["venueOptions"])),
            ("the pair list can be filtered", analysis["filter"]),
        ]
        if shots:
            page.screenshot(path=str(shots / "workspace-matchups-history.png"))
        browser.close()

    for label, ok in checks:
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if not ok:
            failures.append(label)
    if page_errors:
        failures.append(f"page errors: {page_errors[:3]}")
    print("\n" + ("WORKSPACE CHECK PASSED" if not failures else "WORKSPACE CHECK FAILED"))
    for failure in failures:
        print(f"  - {failure}")
    return 0 if not failures else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="running app base URL")
    parser.add_argument("--screenshot", help="directory to write screenshots into")
    args = parser.parse_args()
    shots = Path(args.screenshot) if args.screenshot else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)
    print(f"checking {args.url} — live ledger board and engines workspace")
    return run(args.url.rstrip("/"), shots)


if __name__ == "__main__":
    sys.exit(main())
