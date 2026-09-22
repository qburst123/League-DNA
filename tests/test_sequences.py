"""Observed-context index regressions; altered scores exist only in temp DBs."""
import json
from pathlib import Path
import time
import pytest
from backend.store import Store
from backend.source import parse_result
from backend.sequences import SequenceIndex,encode_pattern,decode_pattern,score_code,score_text,partition_counts

SEASON=3134345

@pytest.fixture
def pair(tmp_path):
    store=Store(tmp_path/'source.sqlite');index=SequenceIndex(tmp_path/'sequences.sqlite')
    yield store,index
    index.close();store.close()


def put(store,day,score,season=SEASON):
    payload=json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text())
    rows=parse_result(payload)['rows']
    for row in rows:row.update(season=season,day=day,ht=(0,0),ft=score,status='final')
    store.ingest(rows,'temporary-index-test',time.time())


def test_all_observed_orders_and_deduplicated_following_matches(pair):
    store,index=pair
    for day,score in [(1,(0,0)),(2,(0,1)),(3,(1,2))]:put(store,day,score)
    summary=index.sync(store)
    assert summary['unique_scores']==3 and summary['source_final_matches']==24
    row=index.query(['0:0','0:1'])
    assert row['occurrences']==16 and row['context_seasons']==1
    assert row['following_matches']==8 and row['following_traces']==16
    assert row['outcomes']==[{'score':'1:2','matches':8,'traces':16,'seasons':1,'historical_share':100.0}]
    terminal=index.query(['0:0','0:1','1:2'])
    assert terminal['occurrences']==16 and terminal['following_matches']==0
    assert terminal['without_recorded_followup']==16
    assert [r['order_len'] for r in summary['order_counts']]==[1,2,3]


def test_gaps_are_not_bridged_and_unknown_followup_is_not_zero(pair):
    store,index=pair
    put(store,1,(0,0));put(store,3,(0,1));put(store,4,(1,1));index.sync(store)
    assert index.query(['0:0','0:1'])['occurrences']==0
    assert index.query(['0:0'])['following_matches']==0
    assert index.query(['0:1'])['outcomes'][0]['score']=='1:1'


def test_sequences_do_not_cross_season_boundary(pair):
    store,index=pair
    put(store,30,(1,2));put(store,1,(0,1),season=SEASON+100);index.sync(store)
    assert index.query(['1:2','0:1'])['occurrences']==0
    assert index.query(['1:2'])['without_recorded_followup']==16


def test_every_pattern_is_exact_ordered_home_away(pair):
    store,index=pair
    put(store,1,(1,2));put(store,2,(0,1));put(store,3,(3,0));index.sync(store)
    assert index.query(['1:2','0:1'])['following_matches']==8
    assert index.query(['2:1','0:1'])['occurrences']==0
    assert index.query(['0:1','1:2'])['occurrences']==0
    assert index.query(['0:3','0:1'])['occurrences']==0


def test_repeated_contexts_deduplicate_next_matches_but_not_team_traces(pair):
    store,index=pair
    for day in [1,2,3]:put(store,day,(0,0))
    index.sync(store)
    one=index.query(['0:0']);two=index.query(['0:0','0:0'])
    assert one['occurrences']==48 and one['following_matches']==16 and one['following_traces']==32
    assert two['occurrences']==32 and two['following_matches']==8 and two['following_traces']==16


def test_updates_append_evidence_without_double_counting(pair):
    store,index=pair
    put(store,1,(0,0));put(store,2,(0,1));index.sync(store)
    assert index.query(['0:0','0:1'])['following_matches']==0
    put(store,3,(1,0));index.sync(store)
    assert index.query(['0:0','0:1'])['following_matches']==8
    before=index.query(['0:0']);stamp=index.summary()['updated_at'];index.sync(store)
    assert index.query(['0:0'])==before and index.summary()['updated_at']==stamp


def test_final_score_corrections_remove_old_outcomes(pair):
    store,index=pair
    put(store,1,(0,0));put(store,2,(0,1));put(store,3,(1,0));index.sync(store)
    put(store,3,(2,2));index.sync(store)
    row=index.query(['0:0','0:1'])
    assert [o['score'] for o in row['outcomes']]==['2:2'] and row['following_matches']==8
    assert '1:0' not in [s['score'] for s in index.vocabulary()]


def test_season_contributions_increment_and_can_be_reconciled(pair):
    store,index=pair
    for season in [SEASON,SEASON+100]:
        put(store,1,(0,0),season);put(store,2,(1,0),season)
    index.sync(store);row=index.query(['0:0'])
    assert row['context_seasons']==2 and row['following_matches']==16 and row['outcomes'][0]['seasons']==2
    store.db.execute('DELETE FROM matches WHERE season=?',(SEASON+100,));store.bump(fingerprint=True)
    index.sync(store);row=index.query(['0:0'])
    assert row['context_seasons']==1 and row['following_matches']==8 and row['outcomes'][0]['seasons']==1


def test_terminal_md30_patterns_are_indexed_without_fabricating_md31(pair):
    store,index=pair
    for day in range(1,31):put(store,day,(day-1,0))
    index.sync(store)
    full=[f'{i}:0' for i in range(30)]
    row=index.query(full)
    assert row['occurrences']==16 and row['following_matches']==0
    catalog=index.catalogue(30)
    assert catalog['total']==1 and catalog['rows'][0]['pattern']==full
    assert index.catalogue(2,offset=25,limit=25)['total']==29
    assert len(index.catalogue(2,offset=25,limit=25)['rows'])==4


def test_encoding_handles_zero_and_maximum_goal_values():
    pattern=['0:0','0:1','1:2','99:99']
    assert decode_pattern(encode_pattern(pattern))==pattern
    assert score_text(score_code(0,0))=='0:0'
    for invalid in [[],['1:2']*31,['100:0'],['1:-1'],['unknown']]:
        with pytest.raises(ValueError):encode_pattern(invalid)


def test_unseen_pattern_has_no_synthesized_outcome(pair):
    store,index=pair;put(store,1,(0,0));index.sync(store)
    row=index.query(['9:9','8:8'])
    assert row['occurrences']==0 and row['outcomes']==[] and row['following_matches']==0
    assert len(index.vocabulary())==1


def test_nonfinal_rows_are_not_indexed(pair):
    store,index=pair
    original=json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text())
    rows=parse_result(original)['rows']
    for row in rows:row.update(status='live',ht=None,ft=None,live_ht=(0,0),live_ft=(2,1))
    store.ingest(rows,'temporary-live',time.time());index.sync(store)
    assert index.summary()['observed_patterns']==0 and index.vocabulary()==[]


def test_duplicate_team_appearance_is_not_arbitrarily_used():
    data=[{'id':1,'day':1,'home':'A','away':'B','ft_home':1,'ft_away':0},
          {'id':2,'day':1,'home':'A','away':'C','ft_home':2,'ft_away':0},
          {'id':3,'day':2,'home':'A','away':'B','ft_home':3,'ft_away':0}]
    counts=partition_counts(data)
    # Only B has an unambiguous day1 -> day2 trajectory.
    assert counts[encode_pattern(['1:0'])][1][score_code(3,0)]==[1,1]
    assert encode_pattern(['2:0','3:0']) not in counts


def test_index_survives_restart_and_backups(tmp_path):
    s=Store(tmp_path/'main.sqlite');idx=SequenceIndex(tmp_path/'index.sqlite')
    put(s,1,(0,0));put(s,2,(1,0));idx.sync(s);expected=idx.query(['0:0'])
    idx.backup(tmp_path/'backup.sqlite');idx.close()
    reopened=SequenceIndex(tmp_path/'backup.sqlite')
    assert reopened.query(['0:0'])==expected
    assert reopened.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    reopened.close();s.close()
