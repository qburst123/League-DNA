"""Verify the v2 app's core promise: teams and FT stay visible without a Gold API."""
import asyncio
import copy
import html
import json
from pathlib import Path
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parent.parent

async def assert_inputs(page,expected):
    await page.locator('.incoming-team').first.wait_for()
    assert await page.locator('.incoming-team').count()==len(expected['roster'])==16
    for team in expected['roster']:
        await page.get_by_label('Choose incoming team').select_option(team['team'])
        actual=await page.locator('.ft-input-card.recorded').evaluate_all('(els)=>els.map(e=>[Number(e.dataset.day),e.dataset.ft])')
        wanted=[[c['day'],c['ft']] for c in team['cards'][:team['visible_until']] if c['ft'] is not None]
        assert actual==wanted,(team['team'],actual,wanted)


async def main():
    checks=[];errors=[]
    async with async_playwright() as p:
        browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
        context=await browser.new_context(viewport={'width':1440,'height':1050})
        page=await context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        # Supply one stable, real snapshot while deliberately disabling all old
        # comparison routes. This never changes the actual collector database.
        request=await page.request.get('http://127.0.0.1:8000/api/workspace');source=await request.json()
        if source['overview']['completed_prefix']<2:
            source=json.loads((ROOT/'artifacts/workspace-live.json').read_text())  # actual earlier source capture for the matching scenarios
        await page.route('**/api/workspace',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps(source)))
        blocked=[]
        async def fail_old(route):blocked.append(route.request.url);await route.abort()
        await page.route('**/api/gold?*',fail_old);await page.route('**/api/compare?*',fail_old)
        await page.goto('http://127.0.0.1:8000/#gold',wait_until='networkidle')
        await assert_inputs(page,source)
        assert blocked==[]
        checks.append('All 16 fixture-derived teams and every captured FT score verified; no Gold/comparison API request is needed')
        await page.get_by_label('Choose incoming team').select_option(source['roster'][0]['team'])
        await page.locator('.gold-alignment-note').wait_for()
        current=page.locator('.gold-history-panel .current-row .pattern-card')
        historical=page.locator('.gold-history-panel .historical-row .pattern-card')
        assert await historical.count()==30
        start=int(await page.get_by_role('slider',name='Historical correct score start').input_value())
        for i in range(await current.count()):
            assert await current.nth(i).get_attribute('data-score')==await historical.nth(start-1+i).get_attribute('data-score')
            a,b=await current.nth(i).bounding_box(),await historical.nth(start-1+i).bounding_box()
            assert abs(a['x']-b['x'])<1
        await page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await page.screenshot(path=str(ROOT/'artifacts/v2-gold-desktop.png'),full_page=True,animations='disabled')
        checks.append('Exact FT inputs align vertically with historical score pairs; full historical 30-day row is retained')
        # A real current-only archive has no earlier blueprints. Names and scores
        # must still render, and zero matches must not turn into an empty input pane.
        no_history=copy.deepcopy(source);season=source['overview']['current_season']
        no_history['results']=[m for m in no_history['results'] if m['season']==season]
        empty=await context.new_page();empty.on('pageerror',lambda e:errors.append(str(e)))
        await empty.route('**/api/workspace',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps(no_history)))
        await empty.goto('http://127.0.0.1:8000/#gold',wait_until='networkidle')
        await empty.get_by_role('heading',name='No exact historical match',exact=True).wait_for()
        assert await empty.locator('.incoming-team').count()==16
        assert await empty.locator('.ft-input-card.recorded').count()>0
        checks.append('Zero historical matches: all incoming names and actual current FT inputs remain visible')
        # Controlled missing-MD1 condition: remove that observation in this test
        # response, retaining genuine later FT values. No score is fabricated.
        gap=copy.deepcopy(source);gap['results_revision']+=100000
        gap['overview']['completed_prefix']=0;gap['diagnostics']['current_prefix']=0
        for team in gap['roster']:
            team['prefix_length']=0
            team['cards'][0].update(ft=None,code=None,score=None,state='missing',comparison_eligible=False)
            for card in team['cards']:card['comparison_eligible']=False
        gap_page=await context.new_page();gap_page.on('pageerror',lambda e:errors.append(str(e)))
        await gap_page.route('**/api/workspace',lambda r:r.fulfill(status=200,content_type='application/json',body=json.dumps(gap)))
        await gap_page.goto('http://127.0.0.1:8000/#gold',wait_until='networkidle')
        await gap_page.get_by_role('heading',name='Two finalized rounds are needed',exact=True).wait_for()
        assert await gap_page.locator('.incoming-team').count()==16
        assert await gap_page.locator('.ft-input-card.recorded').count()>0
        assert await gap_page.locator('.ft-input-card[data-day="1"]').get_attribute('data-ft')==''
        checks.append('Missing early FT / zero closed prefix: later captured FT scores still display; matching waits without fabricating data')
        await context.close()
        # Fully offline, no server, no fonts/scripts/CDNs fetched.
        offline=await browser.new_context(viewport={'width':1440,'height':1000},offline=True)
        saved=await offline.new_page();saved.on('pageerror',lambda e:errors.append('offline: '+str(e)))
        await saved.goto((ROOT/'League-DNA.html').as_uri(),wait_until='load')
        captured=await saved.evaluate('window.__LEAGUE_BOOTSTRAP__')
        await assert_inputs(saved,captured)
        assert 'not a live feed' in await saved.locator('.workspace-connection strong').inner_text()
        if await saved.locator('.ft-input-card.recorded').count():
            await saved.locator('.ft-input-card.recorded').first.click()
        else:
            await saved.get_by_role('button',name='Results archive',exact=True).click()
            await saved.get_by_label('Results season').select_option('3134345')
            await saved.locator('.archive-table tbody tr').first.get_by_role('button').click()
        await saved.get_by_role('dialog',name='A result you can trace').wait_for()
        await saved.get_by_role('button',name='Inspect original source payload').click()
        await saved.locator('.raw-source pre').wait_for()
        await saved.get_by_role('button',name='Close dialog',exact=True).click()
        checks.append('Standalone HTML with browser offline: 16 teams, all their FT inputs, exact matches and original compressed receipts work')
        for label,heading in [('Overview','The league, decoded.'),('DNA explorer','A season. A sequence. A story.'),('Pattern comparator','Find the familiar.'),('Results archive',None),('Data health','Durable by design.')]:
            await saved.get_by_role('button',name=label,exact=False).first.click()
            if heading:await saved.get_by_role('heading',name=heading,exact=True).wait_for()
        checks.append('All six workspace pages open from the saved file without a backend')
        await offline.close()
        # Arena-like opaque sandbox: scripts permitted, no same-origin storage,
        # and every network request blocked. This matches the file viewer model.
        sandbox=await browser.new_context(viewport={'width':1440,'height':1000},offline=True)
        host=await sandbox.new_page();host.on('pageerror',lambda e:errors.append('sandbox: '+str(e)))
        content=(ROOT/'League-DNA.html').read_text()
        await host.set_content('<iframe title="Standalone app" sandbox="allow-scripts" style="width:100vw;height:100vh;border:0" srcdoc="'+html.escape(content,quote=True)+'"></iframe>')
        frame=host.frame_locator('iframe')
        await frame.locator('.incoming-team').first.wait_for()
        assert await frame.locator('.incoming-team').count()==16
        assert await frame.locator('.ft-input-card').count()>=2
        await frame.get_by_role('button',name='Overview',exact=True).click()
        await frame.get_by_role('heading',name='The league, decoded.',exact=True).wait_for()
        checks.append('Opaque allow-scripts iframe with networking disabled still displays teams/FT and supports navigation')
        await sandbox.close()
        mobile_context=await browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,offline=True)
        mobile=await mobile_context.new_page();mobile.on('pageerror',lambda e:errors.append('mobile: '+str(e)))
        await mobile.goto((ROOT/'League-DNA.html').as_uri(),wait_until='load')
        await mobile.locator('.ft-input-card').first.wait_for()
        assert await mobile.locator('.incoming-team').count()==16
        assert not await mobile.evaluate('document.documentElement.scrollWidth>innerWidth')
        card=await mobile.locator('.ft-input-card').first.bounding_box()
        assert card['x']>=0 and card['x']+card['width']<=390
        await mobile.screenshot(path=str(ROOT/'artifacts/v2-gold-mobile.png'),full_page=True,animations='disabled')
        await mobile.get_by_role('button',name='Open navigation').click()
        await mobile.get_by_role('button',name='Switch to dark canvas',exact=True).click()
        await mobile.get_by_role('button',name='Correct score gold',exact=False).click()
        await mobile.screenshot(path=str(ROOT/'artifacts/v2-gold-mobile-dark.png'),full_page=True,animations='disabled')
        checks.append('390px mobile + dark mode: actual FT inputs are in view and the page has no horizontal overflow')
        await browser.close()
    report={'checks':checks,'browser_errors':errors}
    (ROOT/'artifacts/v2-browser-report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2));assert not errors

if __name__=='__main__':asyncio.run(main())
