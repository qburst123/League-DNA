"""Earlier-season FT continuation picks: isolation, trimming and honest grading.

Every store here is temporary. Nothing in this file can populate the archive.
"""
import copy
import json
from pathlib import Path
import time
import pytest
from backend.store import Store
from backend.source import parse_result
from backend.forecast import ReferencePool, build_cards, cached_season_ledger, season_ledger

PAST = 3134345
PAST2 = 3134373
CURRENT = 3134406
FUTURE = 3134430
FILLER = (5, 5)
TEAM = 'Ba Zoo BSL'


@pytest.fixture
def base():
    return parse_result(json.loads((Path(__file__).parent / 'fixtures/historical-results.json').read_text()))['rows']


def season_rows(base, season, plan, days=30):
    """plan maps a matchday to the FT of the first fixture; every other fixture uses FILLER."""
    rows = []
    for day in range(1, days + 1):
        for index, original in enumerate(base):
            row = copy.deepcopy(original)
            row.update(season=season, day=day, ft=plan.get(day, FILLER) if index == 0 else FILLER)
            rows.append(row)
    return rows


def make_store(tmp_path, base, seasons, upcoming=CURRENT):
    """seasons: [(season, plan, days)] where only the first fixture follows the plan."""
    store = Store(tmp_path / 'forecast.sqlite')
    for season, plan, days in seasons:
        store.ingest(season_rows(base, season, plan, days=days), 'temporary-forecast-test', time.time())
    store.set_meta('upcoming', {'season': upcoming, 'day': 1, 'teams': []})
    return store


def add_days(store, base, season, plan, days):
    store.ingest(season_rows(base, season, plan, days=days), 'temporary-forecast-test', time.time())


def team_ledger(payload, team=TEAM, scope='all'):
    return next(row for row in payload['scopes'][scope]['teams'] if row['team'] == team)


def row_for(ledger, day):
    return next(row for row in ledger['ledger'] if row['day'] == day)


def test_picks_are_recorded_earlier_continuations_and_each_day_is_graded(tmp_path, base):
    plan = {1: (1, 0), 2: (0, 1), 3: (2, 0), 4: (1, 1), 5: (3, 0)}
    store = make_store(tmp_path, base, [(PAST, plan, 6), (PAST2, plan, 6), (CURRENT, plan, 5)])
    payload = season_ledger(store, CURRENT, ('all',))
    ledger = team_ledger(payload)
    assert (payload['season'], payload['prefix'], payload['upcoming_day']) == (CURRENT, 5, 6)
    assert payload['no_target_or_future_reference_data'] is True
    assert payload['reference_seasons'] == [PAST, PAST2] and CURRENT not in payload['reference_seasons']
    assert row_for(ledger, 2)['status'] == 'no_pick' and row_for(ledger, 2)['reason'] == 'context_too_short'
    third = row_for(ledger, 3)
    assert third['status'] == 'hit' and third['pick']['score'] == '2:0' and third['actual'] == '2:0'
    assert (third['pick']['context_start'], third['pick']['length'], third['pick']['trimmed']) == (1, 2, 0)
    assert third['pick']['matches'] == 2 and third['pick']['seasons'] == 2
    fourth = row_for(ledger, 4)
    assert fourth['status'] == 'hit' and fourth['pick']['score'] == '1:1' and fourth['pick']['length'] == 3
    assert ledger['summary'] == {'picks': 3, 'hits': 3, 'misses': 0, 'graded': 3, 'no_pick': 1, 'pending': 1,
                                 'exact_hit_rate': 100.0, 'direction_hits': 3, 'direction_rate': 100.0, 'coverage': 75.0}
    pending = ledger['pending']
    assert pending['status'] == 'pending' and pending['day'] == 6 and pending['pick']['score'] == '5:5'
    assert pending['actual'] is None and pending['match_id'] is None and pending['context'] == ['1:0', '0:1', '2:0', '1:1', '3:0']
    assert payload['scopes']['all']['teams'] and payload['scopes']['all']['picks'] >= 3
    store.close()


def test_wrong_picks_are_labelled_wrong_and_direction_is_separate(tmp_path, base):
    plan = {1: (1, 0), 2: (0, 1), 3: (2, 0)}
    store = make_store(tmp_path, base, [(PAST, plan, 4), (PAST2, plan, 4), (CURRENT, {1: (1, 0), 2: (0, 1), 3: (3, 0)}, 3)])
    ledger = team_ledger(season_ledger(store, CURRENT, ('all',)))
    missed = row_for(ledger, 3)
    assert missed['status'] == 'miss' and missed['pick']['score'] == '2:0' and missed['actual'] == '3:0'
    assert missed['direction_hit'] is True
    assert ledger['summary']['hits'] == 0 and ledger['summary']['misses'] == 1
    assert ledger['summary']['exact_hit_rate'] == 0.0 and ledger['summary']['direction_rate'] == 100.0
    store.close()


def test_the_target_season_is_never_reference_evidence(tmp_path, base):
    """The trap: only the target season contains this context, so nothing may be picked."""
    store = make_store(tmp_path, base, [(PAST, {}, 30), (PAST2, {}, 30), (CURRENT, {1: (9, 1), 2: (9, 2), 3: (7, 7)}, 3)])
    payload = season_ledger(store, CURRENT, ('all',))
    ledger = team_ledger(payload)
    third = row_for(ledger, 3)
    assert third['status'] == 'no_pick' and third['reason'] == 'no_recorded_continuation' and third['pick'] is None
    assert third['actual'] == '7:7'
    assert ledger['summary']['picks'] == 0 and ledger['summary']['no_pick'] == 3
    assert ledger['summary']['pending'] == 0 and ledger['pending']['status'] == 'no_pick'
    assert all(row['pick'] is None or row['pick']['score'] != '7:7' for row in ledger['ledger'])
    store.close()


def test_full_prefix_is_tried_before_the_oldest_day_is_dropped(tmp_path, base):
    past = {1: (6, 6), 2: (2, 2), 3: (3, 3), 4: (0, 2), 5: (0, 2)}
    store = make_store(tmp_path, base, [(PAST, past, 5), (PAST2, past, 5),
                                        (CURRENT, {1: (1, 1), 2: (2, 2), 3: (3, 3), 4: (0, 2)}, 4)])
    ledger = team_ledger(season_ledger(store, CURRENT, ('all',)))
    fourth = row_for(ledger, 4)
    assert fourth['status'] == 'hit' and fourth['pick']['trimmed'] == 1
    assert (fourth['pick']['context_start'], fourth['pick']['length'], fourth['pick']['score']) == (2, 2, '0:2')
    pending = ledger['pending']['pick']
    assert pending['attempts'][0]['length'] == 4 and pending['attempts'][0]['following_matches'] == 0
    assert pending['context_start'] == 2 and pending['length'] == 3 and pending['score'] == '0:2'
    store.close()


def test_a_pick_needs_two_recorded_source_matches(tmp_path, base):
    single = {1: (1, 0), 2: (0, 1), 3: (2, 0)}
    store = make_store(tmp_path, base, [(PAST, single, 4), (PAST2, {}, 4),
                                        (CURRENT, {1: (1, 0), 2: (0, 1), 3: (2, 0)}, 3)])
    strict = team_ledger(season_ledger(store, CURRENT, ('all',), minimum_matches=2))
    # One earlier trace is not enough evidence, so the day is reported as no pick.
    assert row_for(strict, 3)['status'] == 'no_pick' and row_for(strict, 3)['pick'] is None
    relaxed = team_ledger(season_ledger(store, CURRENT, ('all',), minimum_matches=1))
    assert row_for(relaxed, 3)['status'] == 'hit' and row_for(relaxed, 3)['pick']['matches'] == 1
    store.close()


def test_shared_source_match_counts_once_and_duplicate_team_days_cannot_bridge(tmp_path, base):
    repeat = {1: (7, 0), 2: (7, 0), 3: (7, 0), 4: (7, 0)}
    store = make_store(tmp_path, base, [(PAST, repeat, 6)])
    cards = build_cards(store.all("SELECT * FROM matches WHERE season=? AND status='final'", (PAST,)))
    window = ReferencePool(cards).lookup(['7:0', '7:0', '7:0'])
    # Two team sequences share each physical next match, so matches < traces.
    assert window['following_matches'] == 2 and window['following_traces'] == 4
    shared = next(row for row in window['outcomes'] if row['score'] == '7:0')
    assert shared['matches'] == 1 and shared['traces'] == 2 and shared['seasons'] == 1
    assert ReferencePool(cards).lookup(['7:0', '7:0', '7:0'], TEAM)['following_matches'] == 2
    extra = copy.deepcopy(base[0])
    extra.update(season=PAST, day=2, home='Leopard Sakata', away=TEAM)
    store.ingest([extra], 'temporary-forecast-test', time.time())
    cards = ReferencePool(build_cards(store.all("SELECT * FROM matches WHERE season=? AND status='final'", (PAST,))))
    # The conflicted team can no longer bridge day 2, while the clean sequence still can.
    assert cards.lookup(['7:0', '7:0', '7:0'], TEAM)['following_matches'] == 0
    assert cards.lookup(['7:0', '7:0', '7:0'], 'Bandarini BSL')['following_matches'] == 2
    assert cards.lookup(['7:0', '7:0'], TEAM)['following_matches'] > 0
    store.close()


def test_ledger_stops_at_the_finalized_prefix_and_never_wraps(tmp_path, base):
    plan = {day: (1, 1) for day in range(1, 8)}
    store = make_store(tmp_path, base, [(PAST, plan, 9), (CURRENT, plan, 7)])
    payload = season_ledger(store, CURRENT, ('all',))
    ledger = team_ledger(payload)
    assert payload['prefix'] == 7 and payload['upcoming_day'] == 8
    assert max(row['day'] for row in ledger['ledger']) == 8
    assert row_for(ledger, 8)['status'] == 'pending' and row_for(ledger, 8)['match_id'] is None
    store.close()


def test_ledger_is_cached_until_finalized_ft_changes(tmp_path, base):
    past = {day: (1, 1) for day in range(1, 6)} | {6: (2, 2)}
    current = {day: (1, 1) for day in range(1, 6)}
    store = make_store(tmp_path, base, [(PAST, past, 6), (PAST2, past, 6), (CURRENT, current, 5)])
    first = cached_season_ledger(store, CURRENT)
    assert first['cached'] is False and first['prefix'] == 5
    assert cached_season_ledger(store, CURRENT)['cached'] is True
    add_days(store, base, CURRENT, {**current, 6: (2, 2)}, 6)
    second = cached_season_ledger(store, CURRENT)
    assert second['cached'] is False and second['prefix'] == 6
    assert row_for(team_ledger(second), 6)['status'] == 'hit'
    store.close()


def test_scopes_are_separate_and_unknown_scope_is_rejected(tmp_path, base):
    plan = {1: (1, 0), 2: (0, 1), 3: (2, 0), 4: (1, 1)}
    store = make_store(tmp_path, base, [(PAST, plan, 5), (PAST2, plan, 5), (CURRENT, plan, 4)])
    payload = season_ledger(store, CURRENT, ('all', 'same'))
    assert set(payload['scopes']) == {'all', 'same'}
    assert payload['scopes']['same']['teams'][0]['scope'] == 'same'
    assert payload['scopes']['all']['picks'] >= payload['scopes']['same']['picks']
    with pytest.raises(ValueError, match='Unknown continuation scope'):
        season_ledger(store, CURRENT, ('sideways',))
    with pytest.raises(ValueError, match='1 to 30'):
        ReferencePool({}).lookup(['1:1'] * 31)
    store.close()
