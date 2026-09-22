"""Data display regressions. Test mutations use temporary DBs, never production."""
import base64
import hashlib
import json
import time
import zlib
from pathlib import Path
import pytest
from backend.source import parse_result
from backend.store import Store
from backend.workspace import build_workspace

SEASON=3134579

@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path/'view.sqlite');yield value;value.close()


def rows(day,status='final'):
    original=json.loads((Path(__file__).parent/'fixtures/historical-results.json').read_text())
    parsed=parse_result(original)['rows']
    for r in parsed:
        r.update(season=SEASON,day=day,status=status,event_id=str(900000+day*10+parsed.index(r)))
        if status!='final':r.update(ht=None,ft=None,live_ht=None,live_ft=None)
    return parsed


def put(store,day,status='final',limit=8):
    store.ingest(rows(day,status)[:limit],'temporary-fixture-receipt',time.time())


def test_actual_fixtures_supply_all_teams_without_metadata_team_array(store):
    put(store,1);put(store,2);put(store,3,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':3})
    view=build_workspace(store,False)
    assert len(view['roster'])==16 and len(view['overview']['upcoming']['fixtures'])==8
    assert view['diagnostics']['source_published_team_names']==0
    assert all(r['known_final_scores']==2 for r in view['roster'])
    assert all(r['cards'][0]['ft'] is not None and r['cards'][1]['ft'] is not None for r in view['roster'])


def test_no_materialized_pattern_or_gold_result_is_required_for_scores(store):
    put(store,1);put(store,2);put(store,3,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':3})
    store.db.execute('DELETE FROM score_blueprints');store.db.execute('DELETE FROM fingerprints')
    def broken(*a,**k):raise RuntimeError('Comparison unavailable')
    store.compare=broken
    view=build_workspace(store,False)
    assert len(view['roster'])==16
    assert sum(c['ft'] is not None for r in view['roster'] for c in r['cards'][:2])==32


def test_earlier_gap_does_not_hide_later_captured_ft_scores(store):
    put(store,2);put(store,3);put(store,4,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':4})
    view=build_workspace(store,False)
    assert view['overview']['completed_prefix']==0
    for team in view['roster']:
        assert team['cards'][0]['ft'] is None
        assert team['cards'][1]['ft'] is not None and team['cards'][2]['ft'] is not None
        assert team['known_final_scores']==2
        assert all(not c['comparison_eligible'] for c in team['cards'])


def test_partial_round_exposes_known_scores_but_not_eligibility(store):
    put(store,1);put(store,2,limit=7);put(store,3,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':3})
    view=build_workspace(store,False)
    assert view['overview']['completed_prefix']==1
    assert sum(r['cards'][1]['ft'] is not None for r in view['roster'])==14
    assert all(not r['cards'][1]['comparison_eligible'] for r in view['roster'])


def test_md1_roster_is_visible_before_comparisons_can_begin(store):
    put(store,1,'scheduled');store.set_meta('upcoming',{'season':SEASON,'day':1})
    view=build_workspace(store,False)
    assert len(view['roster'])==16 and view['overview']['completed_prefix']==0
    assert all(r['visible_until']==2 for r in view['roster'])
    assert all(c['ft'] is None for r in view['roster'] for c in r['cards'])
    assert all(r['cards'][0]['state']=='not_played' for r in view['roster'])


def test_live_values_are_exposed_separately_never_as_ft(store):
    value=rows(1,'live')
    for r in value:r.update(ht=None,ft=None,live_ht=(0,0),live_ft=(1,0),phase='started',source_status='live')
    store.ingest(value,'live-test-only',time.time());put(store,2,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':2})
    view=build_workspace(store,False)
    assert all(r['cards'][0]['ft'] is None and r['cards'][0]['observed_score']=='1:0' for r in view['roster'])
    assert all(not r['cards'][0]['comparison_eligible'] for r in view['roster'])


def test_no_upcoming_source_means_no_invented_roster_from_global_teams(store):
    put(store,1)
    view=build_workspace(store,False)
    assert len(view['results'])==8 and view['roster']==[]


def test_saved_receipts_can_be_decompressed_and_verified(store):
    original=(Path(__file__).parent/'fixtures/historical-results.json').read_text()
    digest=hashlib.sha256(original.encode()).hexdigest()
    store.receipt(digest,'https://virtuals.betika.com/v1/matches/results?season=3134345&matchday=1&competition_id=26','results',original,time.time(),None)
    store.ingest(parse_result(json.loads(original))['rows'],digest,time.time())
    view=build_workspace(store)
    recovered=zlib.decompress(base64.b64decode(view['receipts'][digest]['body_deflate']))
    assert recovered.decode()==original and hashlib.sha256(recovered).hexdigest()==digest


def test_fixture_names_override_stale_metadata_names(store):
    put(store,1);put(store,2,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':2,'teams':['Old metadata name']})
    view=build_workspace(store,False)
    assert len(view['roster'])==16 and 'Old metadata name' not in [r['team'] for r in view['roster']]


def test_workspace_carries_the_continuation_ledger_for_the_incoming_season(store):
    """The live board reads workspace.forecast; the ledger must cover the incoming roster."""
    put(store,1);put(store,2);put(store,3,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':3})
    view=build_workspace(store)
    assert view['forecast'], 'the ledger must be embedded for the live board'
    assert view['forecast']['season']==SEASON and view['forecast']['prefix']==2
    assert view['forecast']['engine']=='earlier-season-continuation-v1'
    assert view['forecast']['no_target_or_future_reference_data'] is True
    ledger={row['day']:row for row in view['forecast']['scopes']['all']['teams'][0]['ledger']}
    assert set(ledger)=={2,3}
    assert ledger[2]['status']=='no_pick' and ledger[2]['reason']=='context_too_short'
    # The incoming matchday is graded as an upcoming row, never invented and never scored.
    assert ledger[3]['status']=='no_pick' and ledger[3]['reason']=='no_recorded_continuation'
    assert ledger[3]['pick'] is None and ledger[3]['actual'] is None
    assert len(view['roster'])==16 and all(len(team['cards'])==30 for team in view['roster'])
    assert [row['team'] for row in view['forecast']['scopes']['all']['teams']]==sorted(team['team'] for team in view['roster'])
    assert view['diagnostics']['forecast_error'] is None and view['diagnostics']['current_prefix']==2


def test_workspace_survives_a_broken_ledger(store,monkeypatch):
    put(store,1);put(store,2);put(store,3,'scheduled')
    store.set_meta('upcoming',{'season':SEASON,'day':3})
    def explode(*args,**kwargs):raise RuntimeError('index unavailable')
    monkeypatch.setattr('backend.workspace.cached_season_ledger',explode)
    view=build_workspace(store)
    assert view['forecast'] is None
    assert len(view['roster'])==16 and any(card['ft'] for team in view['roster'] for card in team['cards'])
    assert 'Continuation ledger deferred' in view['diagnostics']['forecast_error']
    # The deferral is durable in the activity log (health.activity is captured before the attempt).
    assert any('Continuation ledger deferred' in row['message'] for row in store.all('SELECT * FROM activity'))
