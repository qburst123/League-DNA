"""Cross-check browser engines against independent Python implementations.
Uses a temporary consistent copy of the real archive; no production modification.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.store import Store
from backend.workspace import build_workspace
from backend.gold import compare_correct_scores


def audit(path):
    with tempfile.TemporaryDirectory(prefix='league-client-audit-') as folder:
        copy=Path(folder)/'audit.sqlite'
        source=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True);dest=sqlite3.connect(copy);source.backup(dest);dest.close();source.close()
        store=Store(copy);workspace=build_workspace(store,False)
        season=workspace['overview']['current_season']
        kinds=['parity','btts','dnb','total']
        data={'workspace':workspace,'gold':{scope:compare_correct_scores(store,scope) for scope in ['all','same']},
              'symbols':{f'{kind}-{minimum}':store.compare(kind,minimum,'all') for kind in kinds for minimum in [100,80]},
              'blueprints':{kind:store.fingerprints(season,kind) for kind in kinds}}
        file=Path(folder)/'input.json';file.write_text(json.dumps(data,separators=(',',':')))
        result=subprocess.run(['node',str(ROOT/'scripts/check_client.mjs'),str(file)],capture_output=True,text=True,cwd=ROOT)
        if result.returncode:
            print(result.stdout[:18000]);print(result.stderr);raise SystemExit(result.returncode)
        report={'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'data_origin':'Consistent real Betika archive copy',
                'incoming_teams':len(workspace['roster']),'prefix':workspace['overview']['completed_prefix'],**json.loads(result.stdout)}
        store.close();return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/client-audit.json'));args=parser.parse_args()
    report=audit(args.db);Path(args.output).write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
