import fs from 'node:fs';
import {compareGold,compareSymbols,blueprints} from '../frontend/src/model.js';
const input=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const workspace=input.workspace;
const failures=[];
function compact(result){return {length:result.length,match_count:result.match_count,windows_scanned:result.windows_scanned,
 teams:result.teams.map(t=>({team:t.team,length:t.length,match_count:t.match_count,current_start:t.current_start,
 candidates:t.candidates.map(c=>({team:c.team,season:c.season,start:c.start,end:c.end,length:c.length,similarity:c.similarity}))}))};}
for(const scope of ['all','same']){
 const actual=compact(compareGold(workspace,scope)),expected=compact(input.gold[scope]);
 if(JSON.stringify(actual)!==JSON.stringify(expected))failures.push({type:'gold',scope,actual,expected});
}
for(const kind of ['parity','btts','dnb','total'])for(const minimum of [100,80]){
 const actual=compact(compareSymbols(workspace,kind,minimum,'all')),expected=compact(input.symbols[`${kind}-${minimum}`]);
 if(JSON.stringify(actual)!==JSON.stringify(expected))failures.push({type:'symbols',kind,minimum,actual,expected});
}
for(const kind of ['parity','btts','dnb','total']){
 const expected=input.blueprints[kind];const actual=blueprints(workspace,expected.season,kind);
 const a=actual.teams.map(t=>({team:t.team,signature:t.signature,played:t.played})),b=expected.teams.map(t=>({team:t.team,signature:t.signature,played:t.played}));
 const expectedByTeam=new Map(b.map(t=>[t.team,t]));
 const mismatch=b.some(t=>JSON.stringify(a.find(x=>x.team===t.team))!==JSON.stringify(t));
 const invalidExtra=a.some(t=>!expectedByTeam.has(t.team)&&(t.played!==0||t.signature!=='.'.repeat(30)));
 if(mismatch||invalidExtra)failures.push({type:'blueprints',kind,actual:a,expected:b});
}
console.log(JSON.stringify({comparisons_checked:10,blueprints_checked:4,failures},null,2));
process.exit(failures.length?1:0);
