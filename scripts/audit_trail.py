"""Check retained single-hit invariants and frozen scores against source receipts."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time
import zlib

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.source import parse_result,parse_ongoing


def audit(path):
    source=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True)
    db=sqlite3.connect(':memory:');source.backup(db);source.close();db.row_factory=sqlite3.Row
    errors=[];payloads={};verified_scores=0;streams={};observed=0
    rows=db.execute('SELECT * FROM single_hit_rows ORDER BY current_season,current_team,scope,sequence_no').fetchall()
    for raw in rows:
        row=dict(raw);data=json.loads(row['data']);history=data['historical_cards'];current=data['latest_current_cards']
        stream=(row['current_season'],row['current_team'],row['scope']);streams.setdefault(stream,[]).append(row['sequence_no'])
        if len(history)!=30:errors.append(['historical_length',row['id']])
        obs=db.execute('SELECT * FROM single_hit_observations WHERE row_id=? ORDER BY observed_at',(row['id'],)).fetchall()
        if len(obs)!=row['observation_count']:errors.append(['observation_count',row['id']])
        for item in obs:
            o=json.loads(item['data']);observed+=1
            if o['match_count']!=1 or len(o['scores'])<2:errors.append(['not_single_hit',row['id']])
            if o['historical_season']>=o['current_season']:errors.append(['non_historical_season',row['id']])
            if o['current_start']-o['historical_start']!=row['alignment_offset']:errors.append(['offset',row['id']])
            h=[c['ft'] for c in history[o['historical_start']-1:o['historical_end']]]
            c=[c['ft'] for c in current[o['current_start']-1:o['current_end']]]
            if h!=o['scores'] or c!=o['scores']:errors.append(['captured_pair_mismatch',row['id']])
        for season,cards in [(row['historical_season'],history),(row['current_season'],current)]:
            for card in cards:
                if card.get('ft') is None:continue
                digest=card.get('source_hash')
                receipt=db.execute('SELECT body FROM receipts WHERE hash=?',(digest,)).fetchone()
                if not receipt:errors.append(['missing_retained_receipt',row['id'],card.get('match_id')]);continue
                if digest not in payloads:
                    raw_payload=zlib.decompress(receipt['body'])
                    if hashlib.sha256(raw_payload).hexdigest()!=digest:errors.append(['receipt_hash',digest])
                    body=json.loads(raw_payload)
                    payloads[digest]=parse_result(body)['rows'] if isinstance(body.get('data'),dict) and 'results' in body['data'] else parse_ongoing(body)
                source_match=[m for m in payloads[digest] if m['season']==season and m['day']==card['day'] and m['home']==card['home'] and m['away']==card['away']]
                if len(source_match)!=1 or source_match[0]['status']!='final' or ':'.join(map(str,source_match[0]['ft']))!=card['ft']:
                    errors.append(['frozen_ft_source_mismatch',row['id'],card.get('match_id')])
                else:verified_scores+=1
    for stream,numbers in streams.items():
        if numbers!=list(range(1,len(numbers)+1)):errors.append(['sequence_order',stream])
    return {'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'rows':len(rows),'team_season_scope_streams':len(streams),
            'unique_observations':observed,'frozen_scores_verified_against_receipts':verified_scores,'errors':errors,
            'note':'Uniqueness is recorded at capture against the then-available database. Later history additions are not retroactively substituted into that count.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/trail-audit.json'));args=parser.parse_args()
    report=audit(args.db);Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2));sys.exit(1 if report['errors'] else 0)
