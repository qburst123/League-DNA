"""Independently verify prefix-first Gold against a consistent real-data copy.

Stages MD3/4/5/6 are historical replays: only upcoming-round metadata changes
inside a temporary copy. No score is invented and production is never modified.
"""
import argparse
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.store import Store
from backend.gold import compare_correct_scores


def brute_force(query,history,scope,team):
    hits=[]
    for row in history:
        if scope=='same' and row['team']!=team:continue
        tokens=json.loads(row['home_away'])
        for start in range(31-len(query)):
            window=tokens[start:start+len(query)]
            if None not in window and window==query:
                hits.append((row['season'],row['team'],start+1,start+len(query)))
    return sorted(hits,key=lambda h:(-h[0],h[2],h[1]))


def check_view(store,scope):
    report=compare_correct_scores(store,scope)
    history=store.all('SELECT * FROM score_blueprints WHERE season<?',(report['current_season'],))
    chosen=0
    for team in report['teams']:
        query=[c['ft'] for c in team['full_cards']]
        if len(query)<2 or report['upcoming_day']<3 or None in query:
            assert team['status']=='waiting' and team['match_count']==0
            continue
        expected=[];expected_trace=[];used=1
        for removed in range(len(query)-1 if report['upcoming_day']>=5 else 1):
            expected=brute_force(query[removed:],history,scope,team['team'])
            expected_trace.append((removed+1,len(query)-removed,len(expected)))
            used=removed+1
            if expected:break
        actual_trace=[(a['current_start'],a['length'],a['match_count']) for a in team['attempts']]
        assert expected_trace==actual_trace,(team['team'],expected_trace,actual_trace)
        assert team['current_start']==used
        assert team['match_count']==len(expected)
        assert team['pattern']==query[used-1:]
        assert len(team['cards'])==len(query)-used+1
        actual=[(c['season'],c['team'],c['start'],c['end']) for c in team['candidates']]
        assert actual==expected[:20]
        assert all(c['length']==team['length'] for c in team['candidates'])
        if expected:
            assert team['status']==('full' if used==1 else 'shortened')
            assert all(a['match_count']==0 for a in team['attempts'][:-1])
        else:assert team['status']=='no_match'
        for c in team['candidates']:
            assert [card['ft'] for card in team['cards']]==[card['ft'] for card in c['cards'][c['start']-1:c['end']]]
        chosen+=len(actual)
    return {'scope':scope,'upcoming_day':report['upcoming_day'],'current_season':report['current_season'],
            'full_prefix_length':report['length'],'teams_verified':len(report['teams']),
            'full_sequence_teams':report['full_match_teams'],'shortened_sequence_teams':report['shortened_match_teams'],
            'selected_alignments_verified':chosen,'all_exact_alignments':report['match_count']}


def audit(path):
    reports=[]
    with tempfile.TemporaryDirectory(prefix='gold-replay-') as directory:
        dest=Path(directory)/'copy.sqlite'
        source=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True)
        copied=sqlite3.connect(dest);source.backup(copied);copied.close();source.close()
        store=Store(dest)
        reports.append({'scenario':'Latest captured upcoming state','details':[check_view(store,s) for s in ('all','same')]})
        replay_season=next(r['id'] for r in store.all('SELECT id FROM seasons ORDER BY id DESC') if store.prefix_length(r['id'])>=6)
        for day in (3,4,5,6):
            fixtures=store.all('SELECT home,away FROM matches WHERE season=? AND day=? ORDER BY id',(replay_season,day))
            names=[name for fixture in fixtures for name in (fixture['home'],fixture['away'])]
            assert len(set(names))==16
            store.set_meta('upcoming',{'season':replay_season,'day':day,'teams':names})
            details=[check_view(store,scope) for scope in ('all','same')]
            assert all(r['full_prefix_length']==day-1 for r in details)
            if day<5:assert all(r['shortened_sequence_teams']==0 for r in details)
            reports.append({'scenario':f'Historical replay: upcoming MD{day}, compare MD1–{day-1} first','details':details})
        store.close()
    return {'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'engine':'full_prefix_first_then_trim_oldest',
            'production_modified':False,'scores_invented':False,'verification':'Independent brute-force search of retained real Betika FT vectors',
            'scenarios':reports,'mismatches':[]}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/gold-prefix-audit.json'));args=parser.parse_args()
    report=audit(args.db);Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
