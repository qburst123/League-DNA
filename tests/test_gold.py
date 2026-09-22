"""Prefix-first Gold v2 regression. Synthetic inputs are confined to temp DBs."""
import json
import time
from pathlib import Path
import pytest
from backend.gold import ENGINE_VERSION,WindowIndex,trace_full_sequence,compare_correct_scores
from backend.source import parse_result
from backend.store import Store

HISTORY=3134345
CURRENT=3134579
FUTURE=3134635
A,B,C,D,E='1:0','2:0','3:0','4:0','5:0'


def index(tokens,team='Historic',season=HISTORY):
    return WindowIndex([{'team':team,'season':season,'tokens':tokens,'cards':[]}])


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'test.sqlite');yield s;s.close()


def source_round(season,day,score):
    raw=json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text())
    rows=parse_result(raw)['rows']
    for row in rows:row.update(season=season,day=day,ht=(0,0),ft=score,status='final')
    return rows


def put(store,season,day,score=(1,0),count=8):
    store.ingest(source_round(season,day,score)[:count],'isolated-test-only',time.time())


def upcoming(store,day,season=CURRENT):
    teams=[n for r in source_round(season,1,(1,0)) for n in (r['home'],r['away'])]
    store.set_meta('upcoming',{'season':season,'day':day,'teams':teams})
    return teams


def test_md3_starts_with_md1_and_md2_at_any_historical_offset():
    hit=trace_full_sequence([A,B],index([C,D,E,'0:0',A,B]),3)
    assert hit['status']=='full' and hit['length']==2 and hit['current_start']==1
    assert hit['match_count']==1 and hit['trimmed_count']==0
    assert hit['candidates'][0]['start']==5 and hit['candidates'][0]['end']==6
    assert [x['length'] for x in hit['attempts']]==[2]


def test_does_not_start_before_upcoming_md3_or_before_two_final_results():
    assert trace_full_sequence([A,B],index([A,B]),2)['status']=='waiting'
    assert trace_full_sequence([A],index([A,B]),3)['status']=='waiting'
    assert trace_full_sequence([],index([A,B]),3)['status']=='waiting'


def test_every_new_result_is_appended_to_the_full_query():
    idx=index([A,B,C,D])
    for n in [2,3,4]:
        hit=trace_full_sequence([A,B,C,D][:n],idx,n+1)
        assert hit['status']=='full' and hit['length']==n
        assert hit['attempts'][0]['current_start']==1
        assert hit['attempts'][0]['current_end']==n


def test_md4_does_not_fall_back_to_a_two_score_match():
    hit=trace_full_sequence([A,B,C],index([B,C]),4)
    assert hit['status']=='no_match' and hit['match_count']==0
    assert hit['length']==3 and hit['current_start']==1
    assert len(hit['attempts'])==1
    assert hit['reason']=='full_prefix_only_before_md5'


def test_full_match_prevents_shorter_hits_being_mixed_into_count():
    hit=trace_full_sequence([A,B,C,D],index([A,B,C,D,E,C,D]),5)
    assert hit['status']=='full' and hit['match_count']==1
    assert len(hit['attempts'])==1
    assert all(c['length']==4 for c in hit['candidates'])
    assert hit['full_match_count']==hit['match_count']


def test_from_md5_drop_md1_only_if_full_sequence_has_no_match():
    hit=trace_full_sequence([A,B,C,D],index([E,B,C,D,C,D]),5)
    assert hit['status']=='shortened'
    assert hit['current_start']==2 and hit['current_end']==4 and hit['length']==3
    assert hit['trimmed_count']==1 and hit['match_count']==1
    assert [(s['length'],s['match_count']) for s in hit['attempts']]==[(4,0),(3,1)]
    assert all(c['length']==3 for c in hit['candidates'])


def test_drop_oldest_one_at_a_time_and_stop_at_first_successful_length():
    hit=trace_full_sequence([A,B,C,D,E],index(['0:1',C,D,E,C,D,E]),6)
    assert [(s['current_start'],s['length'],s['match_count']) for s in hit['attempts']]==[(1,5,0),(2,4,0),(3,3,2)]
    assert hit['match_count']==2 and hit['length']==3 and hit['trimmed_count']==2
    assert all(c['current_start']==3 and c['current_end']==5 and c['length']==3 for c in hit['candidates'])


def test_never_remove_the_newest_result_instead_of_the_oldest():
    hit=trace_full_sequence([A,B,C,D],index([A,B,C]),5)
    assert hit['match_count']==0 and hit['status']=='no_match'
    assert hit['current_end']==4
    assert [s['current_start'] for s in hit['attempts']]==[1,2,3]


def test_never_fall_below_two_or_fabricate_a_hit():
    hit=trace_full_sequence([A,B,C,D],index([D]),5)
    assert hit['status']=='no_match' and hit['reason']=='two_result_floor'
    assert [s['length'] for s in hit['attempts']]==[4,3,2]
    assert hit['candidates']==[] and hit['length']==2 and hit['match_count']==0


def test_missing_current_scores_are_not_a_reason_to_trim():
    hit=trace_full_sequence([A,None,B,C],index([B,C]),5)
    assert hit['status']=='waiting' and hit['reason']=='missing_current_scores'
    assert hit['attempts']==[] and hit['trimmed_count']==0


def test_exact_home_away_pairs_not_total_goals_or_team_reversal():
    assert trace_full_sequence(['1:2','0:0'],index(['2:1','0:0']),3)['match_count']==0
    assert trace_full_sequence(['3:0','1:1'],index(['2:1','1:1']),3)['match_count']==0
    assert trace_full_sequence(['0:0','0:0'],index(['0:0','0:0']),3)['match_count']==1


def test_unknown_historical_slots_never_bridge_and_windows_stop_at_day30():
    candidates,scanned=index([A,None,B]).lookup([A,B])
    assert candidates==[] and scanned==0
    candidates,scanned=index([A,B]+[None]*26+[A,B]).lookup([A,B])
    assert [c['start'] for c in candidates]==[1,29] and [c['end'] for c in candidates]==[2,30]
    assert scanned==2
    candidates,_=index([None]*29+[A,B]).lookup([A,B])
    assert candidates==[]


def test_scope_counts_and_matches_only_the_selected_team():
    idx=WindowIndex([{'team':'A','season':HISTORY,'tokens':[A,B],'cards':[]},{'team':'B','season':HISTORY,'tokens':[A,B,A,B],'cards':[]}])
    all_hits=trace_full_sequence([A,B],idx,3)
    same=trace_full_sequence([A,B],idx,3,team='A')
    assert all_hits['match_count']==3 and same['match_count']==1
    assert same['windows_scanned']==1
    assert all(c['team']=='A' for c in same['candidates'])


def test_report_count_includes_all_exact_positions_not_just_dropdown_limit():
    hit=trace_full_sequence(['0:0','0:0'],index(['0:0']*30),3)
    assert hit['match_count']==29
    assert len(hit['candidates'])==20
    assert hit['attempts'][0]['match_count']==29


def test_persisted_original_scores_and_both_legacy_vectors_are_preserved(store):
    put(store,HISTORY,1,(2,1))
    away=source_round(HISTORY,1,(2,1))[0]['away']
    row=store.one('SELECT * FROM score_blueprints WHERE team=?',(away,))
    assert json.loads(row['home_away'])[0]=='2:1'
    assert json.loads(row['team_relative'])[0]=='1:2'
    assert len(json.loads(row['cards']))==30
    assert store.one('SELECT count(*) n FROM fingerprints')['n']==64


def test_each_of_the_16_teams_can_independently_keep_or_shorten_its_prefix(store):
    for day in range(1,5):
        put(store,HISTORY,day,(day,0));put(store,CURRENT,day,(day,0))
    # Only one fixture's two teams lose the MD1 part of the otherwise exact prefix.
    put(store,CURRENT,1,(7,0),count=1)
    upcoming(store,5)
    result=compare_correct_scores(store)
    assert len(result['teams'])==16
    assert result['full_match_teams']==14 and result['shortened_match_teams']==2
    for t in result['teams']:
        assert all(c['length']==t['length'] for c in t['candidates'])
        assert t['cards'][0]['day']==t['current_start']
        assert t['cards'][-1]['day']==4
        assert len(t['full_cards'])==4
        if t['status']=='full':assert t['length']==4 and len(t['attempts'])==1
        else:assert t['length']==3 and t['current_start']==2 and t['full_match_count']==0


def test_current_and_future_seasons_are_excluded_from_history(store):
    for season in [HISTORY,CURRENT,FUTURE]:
        for day in [1,2]:put(store,season,day,(day,0))
    upcoming(store,3)
    result=compare_correct_scores(store)
    assert result['historical_blueprints']==16
    assert all(c['season']==HISTORY for t in result['teams'] for c in t['candidates'])
    same=compare_correct_scores(store,'same')
    assert all(c['team']==t['team'] for t in same['teams'] for c in t['candidates'])


def test_incomplete_round_does_not_extend_query_and_gap_is_not_skipped(store):
    for day in range(1,5):put(store,HISTORY,day,(day,0))
    for day in [1,2]:put(store,CURRENT,day,(day,0))
    put(store,CURRENT,3,(3,0),count=7)
    put(store,CURRENT,4,(4,0))
    upcoming(store,5)
    result=compare_correct_scores(store)
    assert result['length']==2 and result['closed_after_prefix']==[4]
    assert all(t['current_end']==2 for t in result['teams'])
    put(store,CURRENT,3,(3,0))
    result=compare_correct_scores(store)
    assert result['length']==4 and result['closed_after_prefix']==[]
    assert all(t['length']==4 and t['current_start']==1 for t in result['teams'])


def test_finished_md3_rechecks_three_scores_even_before_upcoming_changes(store):
    for day in [1,2,3]:put(store,HISTORY,day,(day,0))
    for day in [1,2]:put(store,CURRENT,day,(day,0))
    upcoming(store,4)
    first=compare_correct_scores(store)
    assert first['length']==2 and compare_correct_scores(store)['cached']
    put(store,CURRENT,3,(3,0))
    result=compare_correct_scores(store)
    assert result['revision']>first['revision'] and result['length']==3
    assert all(t['length']==3 for t in result['teams'])


def test_corrections_rebuild_and_new_season_waits_for_md1_md2(store):
    for season in [HISTORY,CURRENT]:
        for day in [1,2,3]:put(store,season,day,(day,0))
    upcoming(store,4)
    assert compare_correct_scores(store)['match_count']>0
    for day in [1,2,3]:put(store,CURRENT,day,(day,1))
    assert compare_correct_scores(store)['match_count']==0
    upcoming(store,1,season=FUTURE)
    result=compare_correct_scores(store)
    assert result['length']==0 and result['match_count']==0
    assert all(t['status']=='waiting' for t in result['teams'])


def test_cache_version_cannot_reuse_old_mixed_length_gold_results(store):
    upcoming(store,3)
    store.memory_cache['correct-score-gold-v1']={'match_count':99999}
    result=compare_correct_scores(store)
    assert result['engine']==ENGINE_VERSION and result['match_count']==0
    assert compare_correct_scores(store)['cached'] is True
    with pytest.raises(ValueError):compare_correct_scores(store,'future')


def test_upgrade_keeps_old_results_blueprints_and_saved_pins(tmp_path):
    path=tmp_path/'archive.sqlite';s=Store(path)
    for season in [HISTORY,CURRENT]:
        for day in [1,2]:put(s,season,day,(day,0))
    upcoming(s,3);s.db.execute('INSERT INTO pins(data,created_at) VALUES(?,?)',('{"kind":"parity"}',time.time()))
    first=compare_correct_scores(s);s.close()
    s=Store(path)
    assert len(s.results())==32
    assert s.one('SELECT count(*) n FROM pins')['n']==1
    assert s.one('SELECT count(*) n FROM fingerprints')['n']==128
    assert compare_correct_scores(s)['cached']
    assert compare_correct_scores(s)['match_count']==first['match_count']
    s.close()


def test_original_v1_database_materialization_remains_non_destructive(tmp_path):
    path=tmp_path/'v1.sqlite';s=Store(path);put(s,HISTORY,1)
    s.db.execute('DROP TABLE score_blueprints');s.db.execute("DELETE FROM meta WHERE key='correct_score_blueprint_version'")
    s.db.execute('PRAGMA user_version=1');s.close();s=Store(path)
    assert len(s.results())==8 and s.one('SELECT count(*) n FROM score_blueprints')['n']==16
    assert s.one('SELECT count(*) n FROM fingerprints')['n']==64
    s.close()
