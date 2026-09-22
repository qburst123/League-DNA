"""Single-hit persistence rules; all invented edge cases use temporary stores."""
import copy
import json
import time
import pytest
from backend.store import Store
from backend.trail import capture_result,observe_single_hits,trail_rows

CURRENT=3134925
HISTORY=3134345
A,B,C,D,E='1:0','2:0','3:0','4:0','5:0'

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'trail.sqlite');yield s;s.close()


def cards(scores):
    return [{'day':i+1,'ft':score,'code':score,'label':'CS' if score else '—','match_id':None,'home':'H','away':'A','ht':'0:0'} for i,score in enumerate(scores)]


def result(current=None,history=None,start=1,hstart=5,count=1,team='Current team',historical='Historical team',scope='all',season=CURRENT):
    current=current or [A,B]
    history=history or [None]*4+[A,B,C,D,E]+[None]*21
    full=cards(current);length=len(current)-start+1
    hit={'team':historical,'season':HISTORY,'start':hstart,'end':hstart+length-1,'cards':cards(history),'length':length}
    return {'current_season':season,'upcoming_day':len(current)+1,'scope':scope,'revision':1,'historical_blueprints':32,'computed_at':100,
            'teams':[{'team':team,'ready':True,'match_count':count,'current_start':start,'current_end':len(current),'full_length':len(current),
                      'full_cards':full,'cards':full[start-1:],'candidates':[hit] if count else []}]}


def test_only_exactly_one_creates_a_row(store):
    for count in [0,2,3,20]:
        assert capture_result(store,result(count=count))['observations']==0
    assert trail_rows(store)==[]
    assert capture_result(store,result(),10)['inserted']==1
    row=trail_rows(store)[0]
    assert row['sequence_no']==1 and row['observation_count']==1
    assert row['observations'][0]['match_count']==1
    assert row['current_start']==1 and row['current_end']==2
    assert row['historical_start']==5 and row['historical_end']==6
    assert row['alignment_offset']==-4


def test_identical_refreshes_and_new_revisions_do_not_duplicate(store):
    payload=result();capture_result(store,payload,10)
    for n in range(4):
        changed=copy.deepcopy(payload);changed['revision']=n+20;changed['computed_at']=time.time();changed['upcoming_day']=5
        assert capture_result(store,changed,30+n)['observations']==0
    rows=trail_rows(store)
    assert len(rows)==1 and rows[0]['observation_count']==1 and rows[0]['last_seen']==10


def test_continuing_single_match_extends_same_row_and_keeps_history_frozen(store):
    capture_result(store,result(),10);old=trail_rows(store)[0]
    outcome=capture_result(store,result(current=[A,B,C]),20)
    new=trail_rows(store)[0]
    assert outcome['extended']==1 and outcome['inserted']==0
    assert new['id']==old['id'] and new['current_start']==1 and new['current_end']==3
    assert new['historical_end']==7 and new['first_seen']==10 and new['last_seen']==20
    assert new['historical_cards']==old['historical_cards']
    assert len(new['observations'])==2 and new['observation_count']==2


def test_multiple_matches_never_update_existing_capture(store):
    capture_result(store,result(),10);old=trail_rows(store)
    capture_result(store,result(current=[A,B,C],count=2),20)
    assert trail_rows(store)==old
    capture_result(store,result(current=[A,B,C,D]),30)
    row=trail_rows(store)[0]
    assert row['current_end']==4 and row['observation_count']==2
    assert [o['current_end'] for o in row['observations']]==[2,4]


def test_break_is_retained_and_later_one_hit_adds_a_new_row_even_same_identity(store):
    capture_result(store,result(),10);original=copy.deepcopy(trail_rows(store)[0])
    capture_result(store,result(current=[A,B,'9:0'],count=0),20)
    assert trail_rows(store)[0]==original
    # Same historical team/season/offset, but the differing current MD3 separates
    # the first piece from the new MD4-5 piece. They must not be merged.
    capture_result(store,result(current=[A,B,'9:0',D,E],start=4,hstart=8),30)
    rows=trail_rows(store)
    assert len(rows)==2 and rows[0]==original
    assert rows[1]['sequence_no']==2 and rows[1]['current_start']==4 and rows[1]['current_end']==5
    assert rows[1]['alignment_offset']==rows[0]['alignment_offset']


def test_a_different_historical_team_or_offset_creates_a_new_row(store):
    capture_result(store,result(),10)
    capture_result(store,result(current=[A,B,C],historical='Another historical team'),20)
    history=[None]*8+[B,C,D]+[None]*19
    capture_result(store,result(current=[A,B,C,D],history=history,start=2,hstart=9,historical='Another historical team'),30)
    rows=trail_rows(store)
    assert [r['sequence_no'] for r in rows]==[1,2,3]
    assert rows[2]['alignment_offset']==-7


def test_teams_scopes_and_seasons_have_independent_trails(store):
    for team,scope,season in [('A','all',CURRENT),('A','same',CURRENT),('B','all',CURRENT),('A','all',CURRENT+100)]:
        capture_result(store,result(team=team,scope=scope,season=season))
    assert len(trail_rows(store))==4
    assert all(r['sequence_no']==1 for r in trail_rows(store))
    assert len(trail_rows(store,CURRENT,'A','all'))==1


def test_saved_history_is_a_copy_not_a_mutable_result_reference(store):
    payload=result();capture_result(store,payload,10)
    before=trail_rows(store)[0]
    payload['teams'][0]['candidates'][0]['cards'][4]['ft']='8:8'
    payload['teams'][0]['full_cards'][0]['ft']='8:8'
    assert trail_rows(store)[0]==before


def test_captured_pair_validation_and_minimum_two_are_enforced(store):
    bad=result();bad['teams'][0]['candidates'][0]['cards'][4]['ft']='0:9'
    with pytest.raises(ValueError):capture_result(store,bad)
    with pytest.raises(ValueError):capture_result(store,result(current=[A]))
    assert trail_rows(store)==[]


def test_current_or_future_season_is_not_allowed_as_history(store):
    payload=result();payload['teams'][0]['candidates'][0]['season']=CURRENT
    with pytest.raises(ValueError):capture_result(store,payload)
    assert trail_rows(store)==[]


def test_regressed_prefix_cannot_be_replayed_as_a_new_live_piece(store):
    capture_result(store,result(current=[A,B,C,D]),10)
    assert capture_result(store,result(current=[A,B]),20)['observations']==0
    assert len(trail_rows(store))==1 and trail_rows(store)[0]['current_end']==4


def test_capture_and_deduplication_survive_restart(tmp_path):
    path=tmp_path/'persist.sqlite';s=Store(path);capture_result(s,result(),10);s.close()
    s=Store(path);assert len(trail_rows(s))==1
    assert capture_result(s,result(),20)['observations']==0
    capture_result(s,result(current=[A,B,C]),30)
    assert len(trail_rows(s))==1 and trail_rows(s)[0]['observation_count']==2
    s.close()


def test_stale_source_and_uncaught_current_prefix_do_not_capture(store,monkeypatch):
    import backend.gold
    def should_not_run(*a,**k):raise AssertionError('Comparison should not have run')
    monkeypatch.setattr(backend.gold,'compare_correct_scores',should_not_run)
    store.set_meta('upcoming',{'season':CURRENT,'day':24,'teams':['A'],'fetched_at':time.time()-200})
    assert observe_single_hits(store)['status']=='waiting_for_fresh_source'
    store.set_meta('upcoming',{'season':CURRENT,'day':24,'teams':['A'],'fetched_at':time.time()})
    status=observe_single_hits(store)
    assert status['status']=='waiting_for_current_backfill' and status['required_prefix']==22
    assert trail_rows(store)==[]


def test_capture_failure_does_not_hide_workspace_inputs(tmp_path,monkeypatch):
    from backend.workspace import build_workspace
    from backend.source import parse_result
    from pathlib import Path
    import backend.trail
    s=Store(tmp_path/'safe.sqlite')
    fixture=json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text())
    rows=parse_result(fixture)['rows'];s.ingest(rows,'fixture-test-only',time.time())
    s.set_meta('upcoming',{'season':HISTORY,'day':1,'teams':[n for r in rows for n in (r['home'],r['away'])]})
    def failed(*a,**k):raise RuntimeError('Trail temporarily unavailable')
    monkeypatch.setattr(backend.trail,'observe_single_hits',failed)
    data=build_workspace(s,False,True)
    assert len(data['roster'])==16 and len(data['results'])==8
    assert data['single_hit_rows']==[]
    s.close()


def test_retained_source_receipt_is_protected_from_live_pruning(store):
    capture_result(store,result(),10);row=trail_rows(store)[0]
    protected='a'*64
    store.db.execute('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)',(protected,'https://virtuals.betika.com/v1/matches/ongoing','live',b'x',1,1,None))
    store.db.execute('INSERT INTO single_hit_evidence VALUES(?,?)',(row['id'],protected))
    store.db.executemany('INSERT INTO receipts VALUES(?,?,?,?,?,?,?)',[(f'{i:064x}','url','live',b'x',100+i,100+i,None) for i in range(2001)])
    store.receipt('b'*64,'https://virtuals.betika.com/v1/matches/ongoing','live','{}',time.time(),None)
    assert store.one('SELECT hash FROM receipts WHERE hash=?',(protected,))
