"""Browser verification against real retained single-hit captures."""
import asyncio
import json
from collections import Counter
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent.parent

async def main():
    checks=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1060})
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        response=await page.request.get('http://127.0.0.1:8000/api/workspace');ws=await response.json()
        current=ws['overview']['current_season']
        if not any(r['current_season']==current for r in ws['single_hit_rows']):current=max(r['current_season'] for r in ws['single_hit_rows'])
        groups=Counter((r['current_team'],r['scope']) for r in ws['single_hit_rows'] if r['current_season']==current)
        assert groups,'No source-observed trail has been captured yet; do not manufacture test production rows.'
        (team,scope),count=groups.most_common(1)[0]
        expected=[r for r in ws['single_hit_rows'] if r['current_season']==current and r['current_team']==team and r['scope']==scope]
        await page.route('**/api/workspace',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps(ws)))
        await page.goto('http://127.0.0.1:8000/#trail',wait_until='networkidle')
        await page.get_by_label('Trail season').select_option('current' if current==ws['overview']['current_season'] else str(current))
        await page.get_by_label('Trail team').select_option(team)
        await page.get_by_label('Trail historical scope').select_option(scope)
        await page.wait_for_function('(n)=>document.querySelectorAll(".trail-history-row").length===n',arg=count)
        assert await page.locator('.trail-current-row .trail-score').count()==30
        days=await page.locator('.trail-current-row .trail-score').evaluate_all('(els)=>els.map(e=>Number(e.dataset.currentDay))')
        assert days==list(range(1,31))
        checks.append(f'Fixed current MD1–30 top row and {count} real retained historical rows are visible')
        for row in expected:
            visual=page.locator(f'.trail-history-row[data-row-id="{row["id"]}"]')
            assert await visual.locator('.trail-score[data-captured="true"]').count()==row['current_end']-row['current_start']+1
            for day in range(row['current_start'],row['current_end']+1):
                top=page.locator(f'.trail-current-row [data-current-day="{day}"]')
                bottom=visual.locator(f'[data-current-day="{day}"]')
                assert int(await bottom.get_attribute('data-historical-day'))==day-row['alignment_offset']
                a,b=await top.bounding_box(),await bottom.bounding_box();assert abs(a['x']-b['x'])<1
                assert await top.get_attribute('data-score')==await bottom.get_attribute('data-score')
        checks.append('Every captured FT fragment has the correct historical offset and shares exact X coordinates with the fixed current row')
        snapshot=await page.locator('.trail-history-row').evaluate_all('(els)=>els.map(e=>({id:e.dataset.rowId,offset:e.dataset.offset,values:[...e.querySelectorAll(".trail-score")].map(c=>[c.dataset.historicalDay,c.dataset.score])}))')
        await page.get_by_role('button',name='MD1',exact=True).click()
        await page.wait_for_function('document.querySelector(".trail-scroll").scrollLeft===0')
        await page.get_by_role('button',name='Latest capture',exact=True).click()
        assert snapshot==await page.locator('.trail-history-row').evaluate_all('(els)=>els.map(e=>({id:e.dataset.rowId,offset:e.dataset.offset,values:[...e.querySelectorAll(".trail-score")].map(c=>[c.dataset.historicalDay,c.dataset.score])}))')
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/trail-desktop.png'),full_page=True,animations='disabled')
        checks.append('Scrolling/focusing does not move logical MD1 or alter saved historical scores/offsets')
        await page.locator('.trail-inspect').first.click()
        await page.get_by_role('dialog',name='retained single-hit row',exact=False).wait_for()
        assert await page.locator('.saved-trail-alignment .historical-row .pattern-card').count()==30
        assert await page.locator('.saved-trail-observations>div').count()==expected[0]['observation_count']
        await page.get_by_role('button',name='Close dialog',exact=True).click()
        await page.reload(wait_until='networkidle')
        await page.get_by_label('Trail season').select_option('current' if current==ws['overview']['current_season'] else str(current))
        await page.get_by_label('Trail team').select_option(team);await page.get_by_label('Trail historical scope').select_option(scope)
        await page.wait_for_function('(n)=>document.querySelectorAll(".trail-history-row").length===n',arg=count)
        checks.append('Saved-row inspector includes all 30 historical days; rows remain after browser reload')
        await page.get_by_role('button',name='Correct score gold',exact=False).click()
        await page.get_by_label('Choose incoming team').select_option(team)
        await page.get_by_label('Gold historical team scope').select_option(scope)
        await page.get_by_role('button',name='Open this team’s trail',exact=True).click()
        assert await page.get_by_label('Trail team').input_value()==team
        assert await page.get_by_label('Trail historical scope').input_value()==scope
        checks.append('New panel below Gold historical comparison opens the selected team and scope in Single-hit Trail')
        await page.get_by_role('button',name='How rows are saved',exact=True).click()
        await page.get_by_role('dialog',name='Only one. Keep the whole trail.',exact=True).wait_for()
        await page.keyboard.press('Escape')
        await page.get_by_role('button',name='Switch to dark canvas',exact=True).click()
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/trail-dark.png'),full_page=True,animations='disabled')
        mobile=await browser.new_page(viewport={'width':390,'height':844},is_mobile=True,has_touch=True)
        mobile.on('pageerror',lambda e:errors.append('mobile: '+str(e)))
        await mobile.route('**/api/workspace',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps(ws)))
        await mobile.goto('http://127.0.0.1:8000/#trail',wait_until='networkidle')
        await mobile.get_by_label('Trail season').select_option('current' if current==ws['overview']['current_season'] else str(current))
        await mobile.get_by_label('Trail team').select_option(team);await mobile.get_by_label('Trail historical scope').select_option(scope)
        await mobile.locator('.trail-history-row').first.wait_for()
        assert not await mobile.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert await mobile.locator('.trail-scroll').evaluate('(e)=>e.scrollWidth>e.clientWidth')
        await mobile.get_by_role('button',name='Latest capture',exact=True).click()
        latest=max(expected,key=lambda r:r['sequence_no'])
        await mobile.wait_for_function('(day)=>{const s=document.querySelector(".trail-scroll"),l=s.querySelector(".trail-row-label"),c=s.querySelector(`.trail-current-row [data-current-day="${day}"]`);return c.getBoundingClientRect().left>=s.getBoundingClientRect().left+l.getBoundingClientRect().width-1&&c.getBoundingClientRect().right<=s.getBoundingClientRect().right+1;}',arg=latest['current_start'])
        await mobile.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await mobile.screenshot(path=str(ROOT/'artifacts/trail-mobile.png'),full_page=True,animations='disabled')
        checks.append('Dark and 390px mobile layouts retain the full 30-day grid, focus the last fragment, and have no page overflow')
        await browser.close()
    report={'checks':checks,'browser_errors':errors,'source_stream':{'season':current,'team':team,'scope':scope,'rows':count}}
    (ROOT/'artifacts/trail-browser-report.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));assert not errors

if __name__=='__main__':asyncio.run(main())
