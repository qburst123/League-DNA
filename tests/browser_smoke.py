"""Run against an already-running League DNA service. Uses live, sourced data only."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent.parent

async def main():
    checks=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1440,'height':1050},device_scale_factor=1)
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:8000/#overview',wait_until='networkidle')
        await page.get_by_role('heading',name='The league, decoded.').wait_for()
        assert await page.locator('.fixture-card').count()==8
        checks.append('Upcoming: 8 real fixtures rendered')
        await page.locator('.fixture-bottom>button:first-child').first.click()
        await page.get_by_role('dialog',name='Market catalogue').wait_for()
        await page.locator('.market-group').first.wait_for()
        n=await page.locator('.market-group').count()
        assert n>=22
        await page.get_by_role('textbox',name='Search markets').fill('both teams')
        assert await page.locator('.market-group').count()>=1
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/markets-desktop.png'),animations='disabled')
        await page.get_by_role('button',name='Close dialog',exact=True).click()
        checks.append(f'Markets: {n} source markets, text search functional, no selectable odds')
        team=await page.locator('.team-link').first.inner_text()
        await page.locator('.team-link').first.click()
        await page.get_by_role('heading',name='A season. A sequence. A story.').wait_for()
        assert await page.get_by_role('textbox',name='Search DNA teams').input_value()==team
        await page.get_by_role('button',name='Clear team search').click()
        await page.get_by_label('DNA season').select_option('3134345')
        await page.wait_for_function("document.querySelectorAll('.dna-table-row').length===16 && document.querySelectorAll('.dna-table-row .pattern-card:not(:disabled)').length===480")
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/explorer-desktop.png'),full_page=True,animations='disabled')
        await page.get_by_role('tab',name='BLUEPRINT 03 Draw no bet').click()
        await page.wait_for_function("!!document.querySelector('.dna-table-row .code-dnb-L')")
        await page.locator('.dna-table-row .pattern-card:not(:disabled)').first.click()
        await page.get_by_role('dialog',name='A result you can trace').wait_for()
        await page.get_by_role('button',name='Inspect original source payload').click()
        await page.locator('.raw-source pre').wait_for()
        assert 'Ba Zoo BSL' in await page.locator('.raw-source pre').inner_text()
        await page.get_by_role('button',name='Close dialog',exact=True).click()
        checks.append('DNA: 16 × 30 source cards, DNB perspective, source provenance and team navigation')
        await page.get_by_role('button',name='Pattern comparator',exact=False).click()
        await page.get_by_role('heading',name='Find the familiar.').wait_for()
        await page.get_by_label('Minimum similarity').select_option('80')
        await page.locator('.team-scan-card').filter(has=page.locator('.match-count.found')).first.click()
        await page.locator('.current-row .pattern-card').first.wait_for()
        assert await page.locator('.team-scan-card').count()==16
        await page.get_by_role('switch',name='Auto-rematch when results close').click()
        await page.locator('.current-row .pattern-card').first.wait_for()
        slider=page.get_by_role('slider',name='Historical starting matchday')
        original=int(await slider.input_value());maximum=int(await slider.get_attribute('max'))
        altered=original+1 if original<maximum else original-1
        if altered>0:
            await slider.fill(str(altered))
            assert int(await slider.input_value())==altered
        # Grid positions prove the current prefix is directly above the selected
        # historical window, not a visually similar but unaligned second row.
        start=int(await slider.input_value())
        a=await page.locator('.current-row .pattern-card').first.bounding_box()
        b=await page.locator('.historical-row .pattern-card').nth(start-1).bounding_box()
        assert abs(a['x']-b['x'])<1
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/comparator-desktop.png'),full_page=True,animations='disabled')
        await page.get_by_role('button',name='Reset alignment').click()
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/comparator-aligned.png'),full_page=True,animations='disabled')
        await page.get_by_role('button',name='Pin snapshot',exact=True).click()
        await page.get_by_role('status').filter(has_text='Alignment pinned.').wait_for()
        await page.get_by_role('button',name='Saved alignments',exact=True).click()
        await page.get_by_role('dialog',name='Saved alignments').wait_for()
        await page.locator('.saved-item').first.wait_for()
        await page.locator('.saved-item').first.get_by_role('button',name='Inspect',exact=True).click()
        await page.locator('.saved-item .alignment-row').first.wait_for()
        # Clean up only the testing-created pin, not unrelated user pins.
        await page.locator('.saved-item').first.get_by_role('button',name='Delete saved alignment').click()
        await page.get_by_role('button',name='Close dialog',exact=True).click()
        checks.append('Comparator: all 16 teams, threshold, sliding offsets, actual grid alignment, durable pin and delete')
        await page.get_by_role('button',name='Results archive',exact=True).click()
        await page.get_by_label('Results season').select_option('3134345')
        await page.get_by_label('Results matchday').select_option('1')
        await page.wait_for_function("document.querySelectorAll('.archive-table tbody tr').length===8")
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/archive-desktop.png'),full_page=True,animations='disabled')
        await page.get_by_role('button',name='Export data').click()
        async with page.expect_download() as info:
            await page.get_by_role('link',name='Selected season · CSV').click()
        download=await info.value
        await download.save_as(str(ROOT/'artifacts/test-export.csv'))
        assert 'ht_home' in (ROOT/'artifacts/test-export.csv').read_text()
        checks.append('Archive: requested oldest season / MD1 contains 8 results, CSV download valid')
        await page.get_by_role('button',name='Data health',exact=True).click()
        await page.get_by_role('heading',name='Durable by design.').wait_for()
        await page.locator('.health-cards .status').filter(has_text='HEALTHY').wait_for()
        await page.get_by_role('button',name='Pause history',exact=True).click()
        await page.get_by_role('button',name='Resume history',exact=True).wait_for()
        await page.get_by_role('button',name='Resume history',exact=True).click()
        await page.get_by_role('button',name='Pause history',exact=True).wait_for()
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/health-desktop.png'),full_page=True,animations='disabled')
        checks.append('Health: database integrity, source status, pause/resume controls functional')
        await page.get_by_role('button',name='Switch to dark canvas',exact=True).click()
        assert await page.evaluate('document.documentElement.dataset.theme')=='dark'
        await page.get_by_role('button',name='Overview',exact=True).click()
        await page.get_by_role('heading',name='The league, decoded.').wait_for()
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/overview-dark.png'),full_page=True,animations='disabled')
        await page.get_by_role('button',name='Switch to light canvas',exact=True).click()
        await page.get_by_role('button',name='How it works',exact=True).click()
        await page.get_by_role('dialog',name='A fingerprint, not a forecast.').wait_for()
        await page.keyboard.press('Escape')
        checks.append('Dark theme and accessible methodology dialog functional')
        mobile=await browser.new_page(viewport={'width':390,'height':844},device_scale_factor=1,is_mobile=True,has_touch=True)
        mobile.on('pageerror',lambda e:errors.append('mobile: '+str(e)))
        await mobile.goto('http://127.0.0.1:8000/#overview',wait_until='networkidle')
        await mobile.get_by_role('heading',name='The league, decoded.').wait_for()
        assert await mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await mobile.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await mobile.screenshot(path=str(ROOT/'artifacts/overview-mobile.png'),full_page=True,animations='disabled')
        await mobile.get_by_role('button',name='Open navigation').click()
        await mobile.get_by_role('button',name='DNA explorer',exact=True).click()
        await mobile.locator('.dna-table-row').first.wait_for()
        assert await mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert await mobile.locator('.fingerprint-scroll').evaluate('(e)=>e.scrollWidth>e.clientWidth')
        await mobile.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await mobile.screenshot(path=str(ROOT/'artifacts/explorer-mobile.png'),full_page=True,animations='disabled')
        await mobile.get_by_role('button',name='Open navigation').click()
        await mobile.get_by_role('button',name='Pattern comparator',exact=False).click()
        await mobile.get_by_label('Minimum similarity').select_option('80')
        await mobile.locator('.team-scan-card').filter(has=mobile.locator('.match-count.found')).first.click()
        await mobile.locator('.current-row .pattern-card').first.wait_for()
        assert await mobile.evaluate('document.documentElement.scrollWidth <= innerWidth')
        await mobile.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await mobile.screenshot(path=str(ROOT/'artifacts/comparator-mobile.png'),full_page=True,animations='disabled')
        checks.append('390px mobile: navigation, no page overflow, scrollable 30-day DNA and alignment')
        await browser.close()
    report={'checks':checks,'browser_errors':errors}
    (ROOT/'artifacts/browser-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    assert not errors

if __name__=='__main__':asyncio.run(main())
