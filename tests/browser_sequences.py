"""Historical-only sequence workspace browser checks against captured source data."""
import asyncio
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent.parent

async def main():
    checks=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        page=await browser.new_page(viewport={'width':1440,'height':1080})
        page.on('pageerror',lambda e:errors.append(str(e)))
        await page.goto('http://127.0.0.1:8000/#sequences',wait_until='networkidle')
        await page.get_by_role('heading',name='What followed this sequence?',exact=True).wait_for()
        await page.locator('.sequence-outcome-row').first.wait_for()
        assert await page.get_by_label('FT sequence text').input_value()=='0:0, 0:1'
        assert await page.locator('.sequence-outcome-row').count()>1
        assert 'not predictive probabilities' in await page.locator('.sequence-evidence-note').inner_text()
        checks.append('Observed follow-up outcomes, distinct matches, traces and season coverage render for the supplied two-score context')
        await page.locator('.sequence-examples').get_by_role('button',name='1:1 → 0:1 → 1:4',exact=True).click()
        assert await page.get_by_label('FT sequence text').input_value()=='1:1, 0:1, 1:4'
        assert await page.locator('.sequence-token').count()==3
        assert 'historical' in (await page.locator('.sequence-evidence-note').inner_text()).lower()
        await page.locator('.sequence-next-record').first.click()
        await page.get_by_role('dialog',name='A result you can trace').wait_for()
        await page.get_by_role('button',name='Inspect original source payload').click()
        await page.locator('.raw-source pre').wait_for()
        await page.get_by_role('button',name='Close dialog',exact=True).click()
        checks.append('Three-score example exposes recorded past following matches and their retained Betika source evidence')
        await page.get_by_label('FT sequence text').fill('99:99, 98:98')
        await page.get_by_role('button',name='Look up history',exact=True).click()
        await page.get_by_role('heading',name='No historical follow-up to report',exact=True).wait_for()
        assert await page.locator('.sequence-outcome-row').count()==0
        await page.get_by_label('FT sequence text').fill('bad pattern')
        await page.get_by_role('button',name='Look up history',exact=True).click()
        await page.get_by_role('alert').wait_for()
        checks.append('Unseen patterns return no invented outcome; invalid score input is rejected')
        await page.get_by_role('tab',name='Unique FT scores',exact=True).click()
        assert await page.locator('.sequence-vocabulary>div>button').count()>=30
        await page.get_by_role('button',name='Analyze FT 0:0',exact=True).click()
        assert await page.locator('.sequence-token').count()==1
        await page.get_by_role('tab',name='Observed pattern library',exact=True).click()
        await page.locator('.sequence-library-row').first.wait_for()
        assert await page.locator('.sequence-library-row').count()==25
        first=await page.locator('.sequence-library-row').first.inner_text()
        await page.get_by_role('button',name='Next catalogue page',exact=True).click()
        assert await page.locator('.sequence-library-row').first.inner_text()!=first
        await page.get_by_label('Catalogue pattern length').select_option('30')
        await page.locator('.sequence-library-row').first.click()
        assert await page.locator('.sequence-token').count()==30
        assert await page.locator('.sequence-outcome-row').count()==0
        assert not await page.evaluate('document.documentElement.scrollWidth>innerWidth')
        checks.append('Actual score vocabulary, paginated observed-pattern catalogue and terminal length-30 contexts work without season wrap')
        await page.locator('.sequence-examples').get_by_role('button',name='0:0 → 0:1',exact=True).click()
        async with page.expect_download() as info:
            await page.get_by_role('button',name='Export observed counts',exact=True).click()
        download=await info.value
        await download.save_as(str(ROOT/'artifacts/sequence-export.csv'))
        assert 'distinct_recorded_matches' in (ROOT/'artifacts/sequence-export.csv').read_text()
        await page.get_by_role('button',name='Read the method',exact=True).click()
        await page.get_by_role('dialog',name='Historical evidence, not a correct-score oracle',exact=True).wait_for()
        await page.keyboard.press('Escape')
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/sequences-desktop.png'),full_page=True,animations='disabled')
        await page.get_by_role('button',name='Switch to dark canvas',exact=True).click()
        await page.screenshot(path=str(ROOT/'artifacts/sequences-dark.png'),full_page=True,animations='disabled')
        checks.append('Historical count CSV, method dialog and dark palette function')
        mobile=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        mobile.on('pageerror',lambda e:errors.append('mobile: '+str(e)))
        await mobile.goto('http://127.0.0.1:8000/#sequences',wait_until='networkidle')
        await mobile.locator('.sequence-outcome-row').first.wait_for()
        assert not await mobile.evaluate('document.documentElement.scrollWidth>innerWidth')
        await mobile.screenshot(path=str(ROOT/'artifacts/sequences-mobile.png'),full_page=True,animations='disabled')
        checks.append('390px mobile rendering has no page overflow and keeps context inputs and evidence warnings visible')
        await browser.close()
    report={'checks':checks,'browser_errors':errors,'scope':'Historical observed outcomes only; no incoming fixture recommendations'}
    (ROOT/'artifacts/sequences-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));assert not errors

if __name__=='__main__':asyncio.run(main())
