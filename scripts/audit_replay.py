"""Cross-implementation replay checks from a consistent copy of real records."""
import argparse,json,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from backend.store import Store
from backend.workspace import build_workspace
from backend.replay import replay_report


def audit(path):
    with tempfile.TemporaryDirectory(prefix='replay-audit-') as folder:
        copy=Path(folder)/'source.sqlite';src=sqlite3.connect(f'file:{Path(path).resolve()}?mode=ro',uri=True);dst=sqlite3.connect(copy);src.backup(dst);dst.close();src.close()
        store=Store(copy);workspace=build_workspace(store,False)
        overview=workspace['overview']
        eligible=[s['id'] for s in overview['seasons'] if s['complete_days']==30 and s['id']<overview['current_season']]
        selected=list(dict.fromkeys([eligible[0],eligible[len(eligible)//2],eligible[-1]]))
        ongoing=overview['current_season']
        ongoing_prefix=store.prefix_length(ongoing)
        cases=[]
        def add(season,prefix=None):
            team=store.one("SELECT home FROM matches WHERE season=? AND status='final' ORDER BY id",(season,))['home']
            pairs=[(2,2),(4,4),(4,2),(10,3),(29,2)]
            if prefix is not None:
                pairs=[(min(cutoff,prefix),min(length,cutoff)) for cutoff,length in pairs if cutoff<=prefix]
                if prefix>=1:pairs.append((prefix,min(2,prefix)))
            for cutoff,length in sorted(set(pairs)):
                for reveal in (False,True):cases.append(replay_report(store,season,team,cutoff,length,reveal))
        for season in selected:add(season)
        if ongoing_prefix>=2:add(ongoing,ongoing_prefix)
        input_file=Path(folder)/'data.json';input_file.write_text(json.dumps({'workspace':workspace,'cases':cases},separators=(',',':')))
        code='''import fs from 'node:fs';import {replayReport} from './frontend/src/replayModel.js';
const data=JSON.parse(fs.readFileSync(process.argv[1],'utf8'));const errors=[];
const evidence=x=>({pattern:x.pattern,length:x.length,occurrences:x.occurrences,following_matches:x.following_matches,following_traces:x.following_traces,without_recorded_followup:x.without_recorded_followup,context_seasons:x.context_seasons,outcomes:x.outcomes.map(o=>({score:o.score,matches:o.matches,traces:o.traces,seasons:o.seasons}))});
const compact=x=>({season:x.target_season,team:x.team,cutoff:x.cutoff,length:x.length,state:x.target_state,prefix:x.season_prefix,max_cutoff:x.max_cutoff,next_recorded:x.next_day_recorded,reference_seasons:x.reference_seasons,reference_matches:x.reference_matches,reference_team_seasons:x.reference_team_seasons,visible:x.visible_cards.map(c=>c.ft),actual:x.actual_next?.ft||null,evidence:evidence(x.evidence),study:x.length_study.map(r=>({length:r.length,pattern:r.pattern,occurrences:r.occurrences,following_matches:r.following_matches,context_seasons:r.context_seasons}))});
for(const expected of data.cases){const actual=replayReport(data.workspace,expected.target_season,expected.team,expected.cutoff,expected.length,expected.revealed);if(JSON.stringify(compact(actual))!==JSON.stringify(compact(expected)))errors.push({season:expected.target_season,cutoff:expected.cutoff,length:expected.length,revealed:expected.revealed,actual:compact(actual),expected:compact(expected)});if(actual.reference_seasons.some(s=>s>=actual.target_season))errors.push({kind:'reference leakage'});}
console.log(JSON.stringify({cases:data.cases.length,errors}));process.exit(errors.length?1:0);
'''
        result=subprocess.run(['node','--input-type=module','-e',code,str(input_file)],cwd=ROOT,capture_output=True,text=True)
        if result.returncode:print(result.stdout[:12000]);print(result.stderr);raise SystemExit(result.returncode)
        out={'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'target_seasons':selected+([ongoing] if ongoing_prefix>=2 else []),'ongoing_prefix':ongoing_prefix,'data_origin':'Consistent copy of retained Betika results','scope':'Recorded-season research including the ongoing prefix; strict earlier-season references',**json.loads(result.stdout)}
        store.close();return out

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--db',default=str(ROOT/'data/league.sqlite'));parser.add_argument('--output',default=str(ROOT/'artifacts/replay-audit.json'));args=parser.parse_args()
    out=audit(args.db);Path(args.output).write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
