"""Cross-check materialized FT continuation counts against the browser's raw-data scan.
The reference fixture is a consistent, temporary copy of real source records.
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
from backend.sequences import SequenceIndex


def audit(path):
    with tempfile.TemporaryDirectory(prefix='ft-sequence-audit-') as directory:
        target=Path(directory)/'source.sqlite'
        source=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True);copy=sqlite3.connect(target);source.backup(copy);copy.close();source.close()
        store=Store(target);index=SequenceIndex(Path(directory)/'index.sqlite');summary=index.sync(store)
        patterns=[['0:0','0:1'],['1:2','0:1'],['1:1','0:1','1:4'],['0:0'],['99:99','98:98']]
        for length in [2,3,4,8,15,29,30]:patterns.extend(r['pattern'] for r in index.catalogue(length,0,5)['rows'])
        data={'workspace':build_workspace(store,False),'queries':[index.query(p) for p in patterns],
              'catalogues':[index.catalogue(length,0,25) for length in [1,2,3,4,8,15,29,30]]}
        file=Path(directory)/'input.json';file.write_text(json.dumps(data,separators=(',',':')))
        code='''import fs from 'node:fs';
import {historicalFollowers,observedCatalogue} from './frontend/src/sequenceModel.js';
const data=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));let errors=[];
const compact=x=>({pattern:x.pattern,occurrences:x.occurrences,context_seasons:x.context_seasons,following_matches:x.following_matches,following_traces:x.following_traces,without_recorded_followup:x.without_recorded_followup,outcomes:x.outcomes});
for(const expected of data.queries){const actual=historicalFollowers(data.workspace,expected.pattern);if(JSON.stringify(compact(actual))!==JSON.stringify(compact(expected)))errors.push({kind:'query',pattern:expected.pattern,actual:compact(actual),expected:compact(expected)});}
const cc=rows=>rows.map(r=>({pattern:r.pattern,occurrences:r.occurrences,following_matches:r.following_matches,seasons:r.seasons}));
for(const expected of data.catalogues){const rows=observedCatalogue(data.workspace,expected.length);if(rows.length!==expected.total||JSON.stringify(cc(rows.slice(0,25)))!==JSON.stringify(cc(expected.rows)))errors.push({kind:'catalogue',length:expected.length,actualTotal:rows.length,expectedTotal:expected.total});}
console.log(JSON.stringify({queries:data.queries.length,catalogues:data.catalogues.length,errors}));process.exit(errors.length?1:0);
'''
        process=subprocess.run(['node','--input-type=module','-e',code,str(file)],cwd=ROOT,capture_output=True,text=True)
        if process.returncode:print(process.stdout[:12000]);print(process.stderr);raise SystemExit(process.returncode)
        result={'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'source':'Real retained Betika FT records; temporary consistent copy',
                'source_final_matches':summary['source_final_matches'],'unique_scores':summary['unique_scores'],'observed_patterns':summary['observed_patterns'],**json.loads(process.stdout)}
        index.close();store.close();return result


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/sequence-audit.json'));args=parser.parse_args()
    result=audit(args.db);Path(args.output).write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
