"""Unit/integration tests. Synthetic edge cases are confined to temporary test DBs.
Production data is ALWAYS collected directly from the Betika public feeds.
"""
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest
from backend.patterns import KINDS, outcome, scan_windows
from backend.source import parse_result, parse_upcoming, parse_ongoing, score, start_timestamp, clean_markets
from backend.store import Store

F = Path(__file__).parent / 'fixtures'


def payload(name):
    return json.loads((F / name).read_text())


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / 'test.sqlite')
    yield s
    s.close()


@pytest.mark.parametrize('home,away,parity,btts,total', [(0,0,'E','N','U'),(1,0,'O','N','U'),(1,1,'E','Y','U'),(2,1,'O','Y','O'),(0,4,'E','N','O'),(3,3,'E','Y','O')])
def test_four_market_meanings(home,away,parity,btts,total):
    assert outcome('parity',home,away)==parity
    assert outcome('btts',home,away)==btts
    assert outcome('total',home,away)==total


def test_dnb_is_team_relative_and_void():
    assert outcome('dnb',3,1,True)=='W'
    assert outcome('dnb',3,1,False)=='L'
    assert outcome('dnb',0,2,False)=='W'
    for home in (True,False):
        assert outcome('dnb',0,0,home)=='V'
        assert outcome('dnb',2,2,home)=='V'


def test_unknown_not_zero():
    assert score('') is None
    assert score(None) is None
    assert score('null') is None
    assert score('0:0')==(0,0)
    with pytest.raises(ValueError):outcome('parity',None,0)


def test_time_zone_is_east_africa():
    assert start_timestamp('2026-09-14 23:05:12')==1789416312
    assert start_timestamp('2026-09-14T20:05:12+00:00')==1789416312
    assert start_timestamp('unknown') is None


def test_exact_sliding_offset_including_last_possible_start():
    found,n=scan_windows('OYV','EEEEOYV',100)
    assert n==5
    assert [x['start'] for x in found]==[5]
    assert found[0]['end']==7
    assert found[0]['exact'] is True
    assert scan_windows('OYE','OYE',100)[0][0]['start']==1


def test_missing_slots_are_never_bridged_or_counted():
    assert scan_windows('O.E','OOOOEE',100)==([],0)
    found,n=scan_windows('OE','O.E',50)
    assert found==[] and n==0
    assert scan_windows('OOOO','OOO')==([],0)
    assert scan_windows('','OOO')==([],0)


def test_similarity_is_symbol_agreement_not_probability():
    found,n=scan_windows('OEOE','OEEE',75)
    assert found[0]['similarity']==75
    assert found[0]['matched']==3
    assert not found[0]['exact']
    assert scan_windows('OEOE','OEEE',100)[0]==[]


def test_api_names_and_ht_ft_match_lite_exactly():
    parsed=parse_result(payload('historical-results.json'),3134345,1)
    lite=payload('lite-crosscheck.json')['rows']
    normalized=[[r['home']+' - '+r['away'],f"{r['ht'][0]}:{r['ht'][1]}",f"{r['ft'][0]}:{r['ft'][1]}"] for r in parsed['rows']]
    assert normalized==lite
    assert len(parsed['rows'])==8
    assert len({n for r in parsed['rows'] for n in (r['home'],r['away'])})==16
    assert len(parsed['seasons'])==9
    assert min(parsed['seasons'])==3134345


def test_first_goal_field_is_not_halftime():
    p=payload('historical-results.json')
    assert p['data']['results'][0]['first_half_score']=='0:1'
    assert parse_result(p)['rows'][0]['ht']==(0,2)


def test_wrong_identity_rejected():
    with pytest.raises(ValueError,match='not 3134346'):
        parse_result(payload('historical-results.json'),3134346,1)
    with pytest.raises(ValueError,match='not 2'):
        parse_result(payload('historical-results.json'),3134345,2)
    p=payload('historical-results.json');p['data']['results'][0]['competition_id']='6'
    with pytest.raises(ValueError,match='Wrong competition'):
        parse_result(p)


def test_bad_team_count_and_invalid_scores_rejected():
    p=payload('historical-results.json');p['data']['results'].append(p['data']['results'][0])
    with pytest.raises(ValueError):parse_result(p)
    p=payload('historical-results.json');p['data']['results'][0]['saved_ht_score']='5:5'
    with pytest.raises(ValueError,match='halftime exceeds'):parse_result(p)


def test_live_result_scores_do_not_become_fingerprints(store):
    parsed=parse_result(payload('provisional-results.json'))
    assert all(row['status']=='live' for row in parsed['rows'])
    store.ingest(parsed['rows'],'provisional',time.time())
    assert len(store.results())>0
    assert store.one('SELECT count(*) n FROM fingerprints')['n']==0
    assert store.prefix_length(parsed['season'])==0
    assert all(m['ft_home'] is None for m in store.results())


def test_upcoming_and_full_markets_are_read_only_display_data():
    groups=parse_upcoming(payload('upcoming.json'))
    assert groups[0]['day']==10
    assert len(groups[0]['rows'])==8
    assert groups[0]['start_time']==1789416532
    markets=clean_markets(payload('markets.json')['data'])
    assert len(markets)>=22
    assert any(m['name']=='BOTH TEAMS TO SCORE' for m in markets)
    assert all(set(odd)=={'label','value','specifier'} for m in markets for odd in m['odds'])


def test_idempotency_final_immutable_from_live_and_resume(store):
    parsed=parse_result(payload('historical-results.json'))
    store.ingest(parsed['rows'],'original',time.time())
    rev=store.fp_revision
    store.ingest(parsed['rows'],'repeat',time.time())
    assert len(store.results())==8
    assert store.fp_revision==rev
    assert store.one('SELECT count(*) n FROM fingerprints')['n']==64
    assert store.prefix_length(3134345)==1
    live=copy.deepcopy(parsed['rows'])
    for r in live:r.update(status='live',ht=None,ft=None,live_ht=(0,0),live_ft=(0,0))
    store.ingest(live,'live',time.time())
    assert all(r['status']=='final' for r in store.results())
    assert store.results()[0]['ft_away']==2
    assert store.fp_revision==rev


def test_live_not_downgraded_to_scheduled(store):
    rows=parse_ongoing(payload('ongoing.json'))
    store.ingest(rows,'live',time.time())
    staged=copy.deepcopy(rows)
    for r in staged:r.update(status='scheduled',live_ht=None,live_ft=None)
    store.ingest(staged,'scheduled',time.time())
    assert all(r['status']=='live' for r in store.results())
    assert store.results()[0]['live_ft_away']==1


def test_partial_round_no_global_prefix(store):
    parsed=parse_result(payload('historical-results.json'))
    store.ingest(parsed['rows'][:7],'partial',time.time())
    assert store.prefix_length(3134345)==0
    row=store.one('SELECT status,final_count FROM matchdays WHERE season=3134345 AND day=1')
    assert row=={'status':'partial','final_count':7}
    store.ingest(parsed['rows'][7:],'last',time.time())
    assert store.prefix_length(3134345)==1


def test_gap_stops_prefix(store):
    parsed=parse_result(payload('historical-results.json'))
    third=copy.deepcopy(parsed['rows'])
    for r in third:r['day']=3
    store.ingest(parsed['rows'],'first',time.time())
    store.ingest(third,'third',time.time())
    assert store.prefix_length(3134345)==1


def test_full_catalogue_not_replaced_by_summary(store):
    store.save_markets('123',[{'name':'full'}],'a',time.time(),full=True)
    store.save_markets('123',[{'name':'summary'}],'b',time.time(),full=False)
    assert store.market('123')['markets'][0]['name']=='full'


def test_persistent_backup_retains_evidence_and_patterns(store,tmp_path):
    raw=json.dumps(payload('historical-results.json'));digest=hashlib.sha256(raw.encode()).hexdigest()
    store.receipt(digest,'https://virtuals.betika.com/v1/matches/results','results',raw,time.time(),'Mon, 14 Sep 2026 20:00:00 GMT')
    store.ingest(parse_result(json.loads(raw))['rows'],digest,time.time())
    dest=tmp_path/'backup.sqlite';store.backup(dest)
    reopened=Store(dest)
    assert len(reopened.results())==8
    assert reopened.one('SELECT count(*) n FROM receipts')['n']==1
    assert reopened.one('SELECT count(*) n FROM fingerprints')['n']==64
    assert reopened.fingerprints(3134345,'dnb')['teams'][0]['cards'][0]['code']=='L'
    assert reopened.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    reopened.close()


def test_comparator_excludes_current_season_and_uses_team_scope(store):
    parsed=parse_result(payload('historical-results.json'))
    store.ingest(parsed['rows'],'old',time.time())
    current=copy.deepcopy(parsed['rows'])
    for r in current:r['season']=3134579
    store.ingest(current,'current',time.time())
    teams=[n for r in current for n in (r['home'],r['away'])]
    store.set_meta('upcoming',{'season':3134579,'day':2,'teams':teams})
    result=store.compare('parity',100,'all')
    assert len(result['teams'])==16 and result['length']==1
    assert result['historical_blueprints']==16
    assert all(c['season']<3134579 for t in result['teams'] for c in t['candidates'])
    same=store.compare('dnb',100,'same')
    assert all(c['team']==t['team'] for t in same['teams'] for c in t['candidates'])
    assert all(t['match_count']==1 for t in same['teams'])
    assert store.compare('dnb',100,'same')['cached'] is True
    # The cached key depends on the upcoming season/day; a rollover has no prefix.
    store.set_meta('upcoming',{'season':3134580,'day':1,'teams':teams})
    assert store.compare('dnb',100,'same')['length']==0


def test_unknown_closed_status_is_not_assumed_final(store):
    p=payload('historical-results.json')
    for r in p['data']['results']:
        r['meta']['status']='closed';r['meta']['match_status']='closed'
        r['saved_ht_score']=None;r['saved_ft_score']=None
    parsed=parse_result(p)
    assert all(r['status']=='live' for r in parsed['rows'])


def test_http_date_detects_stale_source(store):
    now=time.time()
    store.set_meta('upcoming',{'season':3134579,'day':10,'fetched_at':now,'source_at':now-120})
    result=store.overview()
    assert result['source']['stale'] is True
    assert result['source']['age_seconds']>=120


def test_feed_is_host_locked_and_access_denial_does_not_fill_data(store):
    import asyncio
    import httpx
    from backend.source import PublicFeed,SourceError
    async def run():
        feed=PublicFeed(store)
        await feed.client.aclose()
        calls=[]
        def handle(request):
            calls.append(request)
            return httpx.Response(403,json={'detail':'access denied'})
        feed.client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        with pytest.raises(ValueError):
            await feed.get('not-in-the-read-only-allowlist')
        assert calls==[]
        with pytest.raises(SourceError):
            await feed.get('matches',{'competition_id':26})
        assert len(calls)==1
        assert calls[0].method=='GET'
        assert calls[0].url.host=='virtuals.betika.com'
        assert 'authorization' not in calls[0].headers
        assert feed.blocked_until>time.monotonic()+290
        assert len(store.results())==0
        assert store.one('SELECT COUNT(*) n FROM receipts')['n']==0
        await feed.close()
    asyncio.run(run())
