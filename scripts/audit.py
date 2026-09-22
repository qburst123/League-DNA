"""Verify persisted rows and fingerprints against retained Betika source evidence.

This does not refetch the source or assert a provider-signed authenticity proof.
It checks consistency with the actual public payloads collected by this app.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import time
import zlib
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.source import parse_result, parse_ongoing
from backend.patterns import outcome


def audit(path):
    source=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True)
    db=sqlite3.connect(':memory:');source.backup(db);source.close();db.row_factory=sqlite3.Row
    rows=db.execute("SELECT * FROM matches WHERE status='final'").fetchall()
    verified=0;errors=[];receipts={}
    for match in rows:
        digest=match['final_source_hash']
        if digest not in receipts:
            receipt=db.execute('SELECT * FROM receipts WHERE hash=?',(digest,)).fetchone()
            if not receipt:
                errors.append({'type':'missing_receipt','match_id':match['id']});continue
            raw=zlib.decompress(receipt['body'])
            if hashlib.sha256(raw).hexdigest()!=digest:
                errors.append({'type':'hash_mismatch','hash':digest});continue
            body=json.loads(raw)
            try:
                receipts[digest]=parse_result(body)['rows'] if isinstance(body.get('data'),dict) and 'results' in body['data'] else parse_ongoing(body)
            except Exception as error:
                errors.append({'type':'payload_parse','match_id':match['id'],'error':str(error)});continue
        candidates=[r for r in receipts[digest] if (r['season'],r['day'],r['home'],r['away'])==(match['season'],match['day'],match['home'],match['away'])]
        if len(candidates)!=1:
            errors.append({'type':'identity_mismatch','match_id':match['id']});continue
        row=candidates[0]
        if row['status']!='final' or row['ht']!=(match['ht_home'],match['ht_away']) or row['ft']!=(match['ft_home'],match['ft_away']):
            errors.append({'type':'score_mismatch','match_id':match['id']});continue
        verified+=1
    byid={m['id']:m for m in rows};observations=0
    for fp in db.execute('SELECT * FROM fingerprints'):
        cards=json.loads(fp['cards']);sig=''
        for card in cards:
            if not card['code']:
                sig+='.';continue
            m=byid.get(card['match_id'])
            if not m or outcome(fp['kind'],m['ft_home'],m['ft_away'],m['home']==fp['team'])!=card['code']:
                errors.append({'type':'fingerprint_mismatch','team':fp['team'],'season':fp['season'],'day':card['day']})
            else:observations+=1
            sig+=card['code']
        if sig!=fp['signature'] or len(cards)!=30:
            errors.append({'type':'signature_mismatch','team':fp['team'],'kind':fp['kind']})
    correct_score_observations=0
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='score_blueprints'").fetchone():
        for fp in db.execute('SELECT * FROM score_blueprints'):
            cards=json.loads(fp['cards']);ha=json.loads(fp['home_away']);relative=json.loads(fp['team_relative'])
            if len(cards)!=30 or len(ha)!=30 or len(relative)!=30:
                errors.append({'type':'correct_score_blueprint_length','team':fp['team'],'season':fp['season']});continue
            for index,card in enumerate(cards):
                if card['match_id'] is None:
                    if ha[index] is not None or relative[index] is not None:
                        errors.append({'type':'correct_score_unknown_filled','team':fp['team'],'day':index+1})
                    continue
                match=byid.get(card['match_id'])
                if not match or fp['team'] not in (match['home'],match['away']) or match['season']!=fp['season'] or match['day']!=index+1:
                    errors.append({'type':'correct_score_identity','match_id':card['match_id']});continue
                expected=f"{match['ft_home']}:{match['ft_away']}"
                normalized=expected if fp['team']==match['home'] else f"{match['ft_away']}:{match['ft_home']}"
                if ha[index]!=expected or relative[index]!=normalized or card['ft']!=expected or card['relative_score']!=normalized:
                    errors.append({'type':'correct_score_orientation','match_id':card['match_id'],'team':fp['team']})
                else:correct_score_observations+=2
    completed_rounds=db.execute("SELECT season,day FROM matchdays WHERE status='complete'").fetchall()
    for r in completed_rounds:
        matches=db.execute("SELECT home,away,ht_home,ht_away,ft_home,ft_away FROM matches WHERE season=? AND day=? AND status='final'",(r['season'],r['day'])).fetchall()
        names={n for m in matches for n in (m['home'],m['away'])}
        if len(matches)!=8 or len(names)!=16 or any(None in list(m)[2:] for m in matches):
            errors.append({'type':'incomplete_closed_round','season':r['season'],'day':r['day']})
    seasons=[dict(r) for r in db.execute("SELECT season,COUNT(*) matches,COUNT(DISTINCT day) days FROM matches WHERE status='final' GROUP BY season ORDER BY season")]
    integrity=db.execute('PRAGMA integrity_check').fetchone()[0]
    db.close()
    return {'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'final_records':len(rows),'verified_final_records':verified,
            'verified_blueprint_observations':observations,'verified_correct_score_observations':correct_score_observations,'verified_closed_rounds':len(completed_rounds),
            'source_mismatches':errors,'integrity':integrity,'seasons':seasons}


def benchmark(base):
    import httpx
    http,local=[],[]
    with httpx.Client(base_url=base,timeout=30) as client:
        for _ in range(30):
            began=time.perf_counter();response=client.get('/api/overview');response.raise_for_status()
            http.append((time.perf_counter()-began)*1000);local.append(response.json()['read_ms'])
    return {'requests':30,'loopback_http_p50_ms':round(statistics.median(http),2),'loopback_http_p95_ms':round(sorted(http)[28],2),
            'database_overview_p50_ms':round(statistics.median(local),2),'database_overview_p95_ms':round(sorted(local)[28],2),
            'note':'Measured on the running workspace; not a zero-latency guarantee. Internet/browser overhead is additional.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/data-audit.json'));parser.add_argument('--benchmark-url')
    args=parser.parse_args();report=audit(args.db)
    if args.benchmark_url:report['benchmark']=benchmark(args.benchmark_url)
    Path(args.output).parent.mkdir(parents=True,exist_ok=True);Path(args.output).write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))
    sys.exit(0 if not report['source_mismatches'] and report['integrity']=='ok' else 1)
