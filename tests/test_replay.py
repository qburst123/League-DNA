"""Retrospective research isolation. All altered records remain in temp stores."""
import copy
import json
from pathlib import Path
import time
import pytest
from backend.store import Store
from backend.source import parse_result
from backend.replay import replay_report

EARLIER=3134345
TARGET=3134373
LATER=3134406
INCOMING=3134430
CONTEXT=[(1,1),(0,1),(1,4),(2,0)]


def add_season(store,season,following=(3,0),complete=True):
    base=parse_result(json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text()))['rows']
    rows=[]
    for day in range(1,31 if complete else 12):
        score=CONTEXT[day-1] if day<=4 else following if day==5 else (9,9)
        for original in base:
            row=copy.deepcopy(original);row.update(season=season,day=day,ht=(0,0),ft=score,status='final')
            rows.append(row)
    store.ingest(rows,'temporary-replay-test',time.time())
    return base[0]['home']


@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path/'replay.sqlite')
    add_season(s,EARLIER,(3,0));add_season(s,TARGET,(4,2));add_season(s,LATER,(7,7))
    s.set_meta('upcoming',{'season':INCOMING,'day':5,'teams':[]})
    yield s
    s.close()


def first_team(store):
    return store.one('SELECT home FROM matches WHERE season=? ORDER BY id',(TARGET,))['home']


def test_only_completed_earlier_target_and_strict_reference_pool(store):
    result=replay_report(store,TARGET,first_team(store),4,4)
    assert result['mode']=='season-replay'
    assert result['reference_seasons']==[EARLIER]
    assert result['reference_matches']==240
    assert result['no_target_or_future_reference_data'] is True
    assert result['evidence']['pattern']==['1:1','0:1','1:4','2:0']
    assert [o['score'] for o in result['evidence']['outcomes']]==['3:0']
    assert result['evidence']['following_matches']==8
    assert result['evidence']['following_traces']==16
    assert all(e['season']<TARGET for e in result['evidence']['examples'])


def test_actual_next_result_is_omitted_until_reveal(store):
    hidden=replay_report(store,TARGET,first_team(store),4)
    shown=replay_report(store,TARGET,first_team(store),4,reveal=True)
    assert hidden['actual_next'] is None and hidden['revealed'] is False
    assert len(hidden['visible_cards'])==4 and hidden['following_day']==5
    assert shown['actual_next']['ft']=='4:2' and shown['actual_next']['day']==5
    assert hidden['evidence']==shown['evidence']
    assert hidden['length_study']==shown['length_study']


def test_changing_target_following_and_future_results_cannot_change_reference_counts(store):
    name=first_team(store);before=replay_report(store,TARGET,name,4,4)
    add_season(store,TARGET,(8,8));add_season(store,LATER,(6,6))
    after=replay_report(store,TARGET,name,4,4,reveal=True)
    assert before['evidence']==after['evidence']
    assert before['length_study']==after['length_study']
    assert after['actual_next']['ft']=='8:8'
    assert all(o['score']!='8:8' for o in after['evidence']['outcomes'])


def test_ongoing_season_is_replayable_and_future_seasons_are_rejected(store):
    name=add_season(store,INCOMING,(0,0),complete=False)
    ongoing=replay_report(store,INCOMING,name,4,4,reveal=True)
    assert ongoing['target_state']=='ongoing' and ongoing['season_complete'] is False
    assert ongoing['season_prefix']==11 and ongoing['max_cutoff']==11
    assert ongoing['actual_next']['ft']=='0:0' and ongoing['actual_next']['day']==5
    assert ongoing['reference_seasons']==[EARLIER,TARGET,LATER]
    assert ongoing['no_target_or_future_reference_data'] is True
    assert all(e['season']<INCOMING for e in ongoing['evidence']['examples'])
    tail=replay_report(store,INCOMING,name,11,11,reveal=True)
    assert tail['following_day']==12 and tail['next_day_recorded'] is False
    assert tail['actual_next'] is None and tail['revealed'] is False
    assert tail['evidence']['pattern']==['1:1','0:1','1:4','2:0','0:0']+['9:9']*6
    with pytest.raises(ValueError,match='MD1–MD11'):replay_report(store,INCOMING,name,12)
    add_season(store,INCOMING+10,(0,0))
    with pytest.raises(ValueError,match='Future'):replay_report(store,INCOMING+10,name)


def test_season_replays_only_up_to_its_finalized_prefix(store):
    incomplete=TARGET+1;name=add_season(store,incomplete,complete=False)
    result=replay_report(store,incomplete,name,6,2,reveal=True)
    assert result['target_state']=='ongoing' and result['season_prefix']==11 and result['max_cutoff']==11
    assert result['reference_seasons']==[EARLIER,TARGET]
    assert result['evidence']['pattern']==['3:0','9:9']
    assert result['visible_cards'][-1]['ft']=='9:9' and len(result['visible_cards'])==6
    with pytest.raises(ValueError,match='MD1–MD11'):replay_report(store,incomplete,name,14)
    empty=TARGET+2
    store.discover([empty])
    with pytest.raises(ValueError,match='no finalized matchday'):replay_report(store,empty,name,2)


def test_nonexistent_team_is_rejected(store):
    with pytest.raises(ValueError,match='complete, unambiguous'):replay_report(store,TARGET,'No such recorded team')


def test_cutoff_and_context_length_bounds(store):
    name=first_team(store)
    for cutoff in (0,30,31):
        with pytest.raises(ValueError):replay_report(store,TARGET,name,cutoff)
    for length in (0,5):
        with pytest.raises(ValueError):replay_report(store,TARGET,name,4,length)
    assert replay_report(store,TARGET,name,29,2,reveal=True)['actual_next']['day']==30


def test_all_trailing_contexts_end_at_cutoff_and_no_future_target_ft_is_in_query(store):
    result=replay_report(store,TARGET,first_team(store),4,2)
    assert result['context_start']==3
    assert result['evidence']['pattern']==['1:4','2:0']
    assert [r['length'] for r in result['length_study']]==[4,3,2,1]
    assert [r['pattern'][-1] for r in result['length_study']]==['2:0']*4


def test_earliest_season_reports_empty_reference_without_backfilling_from_later_data(store):
    team=store.one('SELECT home FROM matches WHERE season=? ORDER BY id',(EARLIER,))['home']
    result=replay_report(store,EARLIER,team,4,reveal=True)
    assert result['reference_seasons']==[] and result['reference_matches']==0
    assert result['evidence']['outcomes']==[] and result['evidence']['following_matches']==0
    assert result['actual_next']['ft']=='3:0'


def test_replay_does_not_change_any_stored_scores_or_trails(store):
    before=[tuple(r.values()) for r in store.all('SELECT * FROM matches ORDER BY id')]
    for length in (1,2,3,4):replay_report(store,TARGET,first_team(store),4,length,reveal=True)
    after=[tuple(r.values()) for r in store.all('SELECT * FROM matches ORDER BY id')]
    assert before==after
    assert store.one('SELECT COUNT(*) n FROM single_hit_rows')['n']==0
