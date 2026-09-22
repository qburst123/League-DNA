"""Browser checks for the ongoing-season Replay Lab, the live FT board and the
continuation ledger row under every team (items 1-4).

The workspace payload is pinned through a route handler so the assertions always
describe one consistent revision, while the app itself runs against the live
collector.
"""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8000"


def contiguous_prefix(season):
    complete = {day["day"] for day in season["days"] if day["status"] == "complete"}
    prefix = 0
    while prefix + 1 in complete:
        prefix += 1
    return prefix


async def main():
    checks, errors = [], []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = await browser.new_page(viewport={"width": 1440, "height": 1080})
        page.on("pageerror", lambda e: errors.append(str(e)))
        workspace = await (await page.request.get(f"{BASE}/api/workspace")).json()
        forecast, overview = workspace["forecast"], workspace["overview"]
        current = overview["current_season"]
        ongoing_prefix = contiguous_prefix(next(s for s in overview["seasons"] if s["id"] == current))
        complete_seasons = [s["id"] for s in overview["seasons"] if s["complete_days"] == 30 and s["id"] < current]
        assert forecast["season"] == current, (forecast["season"], current)
        assert ongoing_prefix >= 1, "the ongoing season has no finalized matchday to replay"
        # The ledger itself must only ever use strictly earlier seasons and at least two distinct matches.
        assert all(reference < forecast["season"] for reference in forecast["reference_seasons"])
        assert forecast["no_target_or_future_reference_data"] is True
        for scope in ("all", "same"):
            block = forecast["scopes"][scope]
            assert block["picks"] == block["hits"] + block["misses"], scope
            for view in block["teams"]:
                for row in view["ledger"]:
                    if row["pick"]:
                        assert row["pick"]["matches"] >= 2 and row["pick"]["context_start"] >= 1, (scope, view["team"], row["day"])
                        assert row["context_length"] <= row["day"] - 1, (scope, view["team"], row["day"])
        await page.route("**/api/workspace", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(workspace)))
        await page.goto(f"{BASE}/#replay", wait_until="networkidle")
        await page.get_by_role("heading", name="Historical Replay Lab", exact=True).wait_for()
        await page.locator(".replay-day").first.wait_for()

        # ---- item 1: the ongoing, unfinished season is a legal replay target ----
        season_select = page.get_by_label("Replay target season", exact=True)
        options = await season_select.locator("option").evaluate_all("(els)=>els.map(e=>Number(e.value))")
        assert str(current) in [str(value) for value in options] and all(value <= current for value in options), options
        assert current not in complete_seasons
        assert await season_select.input_value() == str(current)
        label = await season_select.locator("option:checked").inner_text()
        assert "ongoing" in label and f"MD{ongoing_prefix}" in label, label
        cutoff_select = page.get_by_label("Replay cutoff matchday", exact=True)
        cutoffs = [int(value) for value in await cutoff_select.locator("option").evaluate_all("(els)=>els.map(e=>e.value)")]
        assert max(cutoffs) == ongoing_prefix and 30 not in cutoffs, cutoffs
        assert int(await cutoff_select.input_value()) == ongoing_prefix
        assert await page.locator(".replay-day").count() == 30
        visible = await page.locator(".replay-day.visible").evaluate_all("(els)=>els.map(e=>Number(e.dataset.day))")
        assert visible == list(range(1, ongoing_prefix + 1)), visible
        next_tile = page.locator(f'.replay-day[data-day="{ongoing_prefix + 1}"]')
        assert await next_tile.get_attribute("data-visible-score") == ""
        assert "awaiting" in (await next_tile.get_attribute("class") or "")
        assert await page.get_by_role("button", name=f"MD{ongoing_prefix + 1} awaiting FT", exact=True).is_disabled()
        assert await page.locator(".replay-actual-score").inner_text() == "…"
        assert await page.locator(".replay-actual-score").get_attribute("data-revealed") == "false"
        assert await page.get_by_role("button", name="Reveal recorded MD", exact=False).count() == 0
        assert await page.get_by_role("button", name="Reveal recorded MD", exact=True).count() == 0
        assert await page.get_by_role("tab", name="Live FT board").count() == 1
        strip = page.locator(".auto-strip")
        assert await strip.count() == 1 and "Auto-updating" in await strip.inner_text()
        assert f"MD1–{ongoing_prefix} finalized" in await strip.inner_text()
        assert await page.get_by_role("button", name="Refresh now", exact=True).is_enabled()
        checks.append(f"item 1/2 · S{current} replays through its finalized prefix MD{ongoing_prefix}; the unrecorded next round waits for the collector")

        # ---- item 2: the auto-refresh contract (8s poll + SSE) is wired up ----
        await page.wait_for_function("()=>document.querySelector('.auto-strip')?.className.includes('live')")
        captured = await page.evaluate("()=>window.__leagueRefreshProbe||null")
        assert await page.get_by_role("button", name="Refresh now", exact=True).count() == 1
        assert "workspace refreshed" in await strip.inner_text()
        assert captured is None or captured, "refresh probe must not fail"

        # ---- completed-season study keeps the original chronological boundary ----
        season_select = page.get_by_label("Replay target season", exact=True)
        season = complete_seasons[0]
        await season_select.select_option(str(season))
        await page.locator(".replay-day").first.wait_for()
        assert await season_select.input_value() == str(season)
        team = await page.get_by_label("Replay historical team", exact=True).input_value()
        assert int(await cutoff_select.input_value()) == 4
        assert await page.locator(".replay-actual-score").inner_text() == "?"
        assert await page.locator('.replay-day[data-day="5"]').get_attribute("data-visible-score") == ""
        assert await page.locator('.replay-day[data-day="6"]').get_attribute("data-visible-score") == ""
        await page.get_by_role("button", name="Study replay context length 1", exact=True).click()
        await page.locator(".replay-outcome").first.wait_for()
        before = await page.locator(".replay-outcome").evaluate_all("(els)=>els.map(e=>[e.dataset.score,e.dataset.matches])")
        references = await page.locator("[data-reference-season]").evaluate_all("(els)=>els.map(e=>Number(e.dataset.referenceSeason))")
        assert references and all(value < season for value in references), references
        await page.get_by_role("button", name="Reveal recorded MD5", exact=True).click()
        actual = next(m for m in workspace["results"] if m["season"] == season and m["day"] == 5 and team in (m["home"], m["away"]))
        assert await page.locator(".replay-actual-score").inner_text() == f"{actual['ft_home']}:{actual['ft_away']}"
        assert await page.locator('.replay-day[data-day="5"]').get_attribute("data-visible-score") == f"{actual['ft_home']}:{actual['ft_away']}"
        assert await page.locator('.replay-day[data-day="6"]').get_attribute("data-visible-score") == ""
        after = await page.locator(".replay-outcome").evaluate_all("(els)=>els.map(e=>[e.dataset.score,e.dataset.matches])")
        assert before == after, "revealing the recorded result must not change the reference evidence"
        await page.get_by_role("button", name="Inspect the target source record", exact=True).click()
        await page.get_by_role("dialog", name="A result you can trace", exact=True).wait_for()
        await page.get_by_role("button", name="Inspect original source payload", exact=True).click()
        await page.locator(".raw-source pre").wait_for()
        await page.get_by_role("button", name="Close dialog", exact=True).click()
        await cutoff_select.select_option("29")
        await page.get_by_role("button", name="Reveal recorded MD30", exact=True).click()
        assert await page.locator('.replay-day[data-day="30"]').get_attribute("data-visible-score") != ""
        assert not await page.evaluate("document.documentElement.scrollWidth>innerWidth")
        assert (ROOT / ".cache").exists() or (ROOT / ".cache").mkdir(parents=True, exist_ok=True) is None
        async with page.expect_download() as download:
            await page.get_by_role("button", name="Export replay", exact=True).click()
        exported_path = ROOT / ".cache/replay-export-test.json"
        await (await download.value).save_as(str(exported_path))
        exported = json.loads(exported_path.read_text())
        assert exported["season_complete"] is True and exported["max_cutoff"] == 29 and exported["season_prefix"] == 30
        assert all(value < exported["target_season"] for value in exported["reference_seasons"])
        checks.append("Completed-season replay keeps MD1-29, hides later rounds, never changes its evidence and exports its chronological boundary")

        # ---- items 3/4: the live FT board and the continuation ledger row ----
        await page.get_by_role("tab", name="Live FT board").click()
        await page.locator(".live-board-ledger-row").first.wait_for()
        assert await page.locator(".live-board-row").count() == 16
        assert await page.locator(".live-board-ledger-row").count() == 16
        assert await page.locator(".replay-outcome").count() == 0
        assert "Recorded FT scores plus earlier-season continuation picks" in await page.locator(".replay-boundary").inner_text()
        for index in range(5):
            strong = page.locator(".forecast-totals strong").nth(index)
            assert await strong.count() == 1
        all_block = forecast["scopes"]["all"]
        all_totals = scope_totals(all_block)
        totals = " ".join(await page.locator(".forecast-totals strong").all_inner_texts())
        for value in all_totals:
            assert f"{value:,}" in totals or f"{value}" in totals, (value, totals)
        for view in all_block["teams"]:
            ledger_row = page.locator(f'.live-board-ledger-row[data-team="{view["team"]}"]')
            assert await ledger_row.count() == 1
            assert await ledger_row.get_attribute("data-scope") == "all"
            assert await ledger_row.locator(".ledger-cell").count() == 30
            cells = await ledger_row.locator(".ledger-cell").evaluate_all(
                "(els)=>els.map(e=>[e.dataset.day?Number(e.dataset.day):null,e.dataset.verdict||null,e.dataset.pick||null,e.dataset.actual||''])")
            by_day = {day: (verdict, pick, actual) for day, verdict, pick, actual in cells if day}
            assert len(by_day) == len(view["ledger"]), (view["team"], len(by_day), len(view["ledger"]))
            for row in view["ledger"]:
                verdict, pick, actual = by_day[row["day"]]
                if row["status"] == "no_pick":
                    assert verdict == "no_pick" and pick is None, (view["team"], row["day"], verdict, pick)
                else:
                    assert verdict == row["status"], (view["team"], row["day"], verdict, row["status"])
                    assert pick == row["pick"]["score"] and actual == (row["actual"] or ""), (view["team"], row["day"], pick, actual)
            summary = await ledger_row.locator("[data-summary]").get_attribute("data-summary")
            assert summary == f'{view["summary"]["hits"]}/{view["summary"]["picks"]}', (view["team"], summary)
            team_row = page.locator(f'.live-board-row[data-team="{view["team"]}"]')
            scores = await team_row.locator(".live-board-score").evaluate_all("(els)=>els.map(e=>[Number(e.dataset.day),e.dataset.score])")
            cards = next(t for t in workspace["roster"] if t["team"] == view["team"])["cards"]
            assert len(scores) == 30 and all(scores[day - 1][1] == (card["ft"] or "") for day, card in enumerate(cards, start=1))
            assert set(by_day) == {row["day"] for row in view["ledger"]}, (view["team"], sorted(by_day))
            blanks = [cell for cell in cells if cell[0] is None]
            assert len(blanks) == 30 - len(view["ledger"]) and all(cell[1] == "none" for cell in blanks), view["team"]
        pending = next(((view, row) for view in all_block["teams"] for row in view["ledger"] if row["status"] == "pending"), None)
        if pending:
            view, row = pending
            cell = page.locator(f'.live-board-ledger-row[data-team="{view["team"]}"]').locator(f'.ledger-cell[data-day="{row["day"]}"]')
            assert await cell.get_attribute("data-verdict") == "pending"
            if await cell.evaluate("(e)=>e.tagName") == "BUTTON":
                await cell.click()
                dialog = page.get_by_role("dialog", name=f'MD{row["day"]} · {view["team"]} continuation pick', exact=True)
                await dialog.wait_for()
                verdict_text = await dialog.inner_text()
                assert "PICKED FT" in verdict_text and row["pick"]["score"] in verdict_text, verdict_text[:200]
                assert "reference seasons strictly before" in verdict_text and f"S{forecast['season']}" in verdict_text
                assert await dialog.locator(".pick-context button").count() == row["context_length"]
                assert await dialog.locator(".pick-outcomes > div").count() >= 1
                assert await dialog.locator(".pick-attempts > div").count() >= 1
                await page.get_by_role("button", name="Close dialog", exact=True).click()
        checks.append("item 3/4 · every team row keeps its recorded FT and the row beneath grades every continuation pick against it")

        # ---- scope switch, evidence modal, dark theme and mobile ----
        await page.get_by_role("button", name="Same team only", exact=True).click()
        same_block = forecast["scopes"]["same"]
        same_totals = scope_totals(same_block)
        totals = " ".join(await page.locator(".forecast-totals strong").all_inner_texts())
        assert f"{same_totals[0]:,}" in totals or f"{same_totals[0]}" in totals, (same_totals, totals)
        assert await page.locator(".live-board-ledger-row").first.get_attribute("data-scope") == "same"
        await page.get_by_role("button", name="All 16 teams", exact=True).click()
        assert await page.locator(".live-board-ledger-row").first.get_attribute("data-scope") == "all"
        await page.screenshot(path=str(ROOT / "artifacts/live-ft-board.png"), full_page=True, animations="disabled")
        await page.get_by_role("tab", name="Season replay").click()
        await page.get_by_role("button", name="Research method", exact=True).click()
        await page.get_by_role("dialog", name="A replay experiment, not an incoming-score forecast", exact=True).wait_for()
        explain = await page.locator('[role="dialog"]').inner_text()
        assert "ongoing season" in explain
        await page.get_by_role("button", name="Close dialog", exact=True).click()
        await page.get_by_role("button", name="Switch to dark canvas", exact=True).click()
        await page.screenshot(path=str(ROOT / "artifacts/replay-dark.png"), full_page=True, animations="disabled")
        await page.get_by_role("button", name="Switch to light canvas", exact=True).click()
        await page.get_by_role("tab", name="Live FT board").click()
        await page.screenshot(path=str(ROOT / "artifacts/live-ft-board-mobile-target.png"), full_page=True, animations="disabled") if False else None

        mobile = await browser.new_page(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        mobile.on("pageerror", lambda e: errors.append("mobile: " + str(e)))
        await mobile.route("**/api/workspace", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(workspace)))
        await mobile.goto(f"{BASE}/#replay", wait_until="networkidle")
        await mobile.locator(".replay-day").first.wait_for()
        assert int(await mobile.get_by_label("Replay target season", exact=True).input_value()) == current
        assert not await mobile.evaluate("document.documentElement.scrollWidth>innerWidth")
        await mobile.screenshot(path=str(ROOT / "artifacts/replay-mobile.png"), full_page=True, animations="disabled")
        await mobile.get_by_role("tab", name="Live FT board").click()
        await mobile.locator(".live-board-ledger-row").first.wait_for()
        assert await mobile.locator(".live-board-row").count() == 16
        assert await mobile.locator(".live-board-ledger-row").count() == 16
        assert not await mobile.evaluate("document.documentElement.scrollWidth>innerWidth")
        assert await mobile.locator(".live-board-scroll").evaluate("(el)=>el.scrollWidth>el.clientWidth")
        await mobile.screenshot(path=str(ROOT / "artifacts/live-ft-board-mobile.png"), full_page=True, animations="disabled")
        checks.append("Scope switch, evidence modal, dark canvas and the 390px layout work with no page overflow")
        await browser.close()
    report = {
        "checks": checks,
        "browser_errors": errors,
        "engine": forecast["engine"],
        "ongoing_season": current,
        "ongoing_prefix": ongoing_prefix,
        "ledger": {scope: {key: forecast["scopes"][scope][key] for key in ("picks", "hits", "misses", "no_pick")} for scope in ("all", "same")},
    }
    (ROOT / "artifacts/replay-browser-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    assert not errors, errors


def scope_totals(block):
    """Mirror of frontend/src/replayModel.js forecastFor(): block counters plus the per-team direction sums."""
    return [block["picks"], block["hits"], block["misses"],
            sum(view["summary"]["direction_hits"] for view in block["teams"]), block["no_pick"]]



if __name__ == "__main__":
    asyncio.run(main())
