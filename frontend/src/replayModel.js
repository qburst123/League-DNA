// Season replay for completed AND ongoing seasons. The reference pool stays
// strictly earlier than the target season, and nothing is invented or filled in.
import {archiveFor} from './model.js';
import {historicalFollowers} from './sequenceModel.js';

export function seasonPrefix(workspace,id){
 const season=(workspace.overview.seasons||[]).find(s=>s.id===Number(id));
 if(!season)return 0;
 const complete=new Set((season.days||[]).filter(d=>d.status==='complete').map(d=>d.day));
 let day=0;while(complete.has(day+1))day++;
 return day;
}
export function replaySeasons(workspace){
 const current=workspace.overview.current_season;
 return (workspace.overview.seasons||[]).filter(s=>s.id<=current)
  .map(s=>({...s,prefix:seasonPrefix(workspace,s.id)}))
  .map(s=>({...s,ongoing:s.prefix<30}))
  .filter(s=>s.prefix>=1)
  .sort((a,b)=>b.id-a.id);
}
export const eligibleReplaySeasons=replaySeasons;
export function maxCutoff(season){return season.prefix>=30?29:Math.min(29,season.prefix);}
export function replayTeams(workspace,season,upto){
 const teams=archiveFor(workspace).get(Number(season))||new Map();
 const limit=Math.max(1,Math.min(30,upto||30));
 return [...teams].filter(([,cards])=>cards.slice(0,limit).every(c=>c.ft!=null)).map(([team])=>team).sort();
}
export function replayReport(workspace,season,team,cutoff=4,length=cutoff,reveal=false){
 season=Number(season);cutoff=Number(cutoff);length=Number(length);
 if(season>workspace.overview.current_season)throw Error('Future seasons have no recorded matches to replay. Choose the ongoing season or an earlier one.');
 const entry=replaySeasons(workspace).find(s=>s.id===season);
 if(!entry)throw Error('This season has no finalized matchday yet, so a replay cutoff cannot be chosen.');
 const max=maxCutoff(entry);
 if(!Number.isInteger(cutoff)||cutoff<1||cutoff>max)throw Error(`The replay cutoff must be MD1–MD${max} for this season (MD${max} is its finalized prefix).`);
 if(!Number.isInteger(length)||length<1||length>cutoff)throw Error('Context length must be within the visible replay prefix.');
 const target=archiveFor(workspace).get(season)?.get(team);
 if(!target)throw Error('This target team has no recorded FT results in this season.');
 if(target.slice(0,cutoff).some(c=>c.ft==null))throw Error(`This team has no finalized FT record for every day through MD${cutoff}.`);
 // Copy only references strictly before the target season. The target/future
 // seasons are never inputs to outcome counting, including in offline mode.
 const references={...workspace,archive_id:`${workspace.archive_id}:replay-before-${season}`,
  results:workspace.results.filter(m=>m.season<season&&m.status==='final')};
 const evidence=historicalFollowers(references,target.slice(cutoff-length,cutoff).map(c=>c.ft));
 const study=[];
 for(let n=cutoff;n>=1;n--){const result=n===length?evidence:historicalFollowers(references,target.slice(cutoff-n,cutoff).map(c=>c.ft));study.push({length:n,pattern:result.pattern,occurrences:result.occurrences,following_matches:result.following_matches,context_seasons:result.context_seasons});}
 const referenceSeasons=[...new Set(references.results.map(m=>m.season))].sort((a,b)=>a-b);
 const following=cutoff<30?target[cutoff]:null;
 return {mode:'season-replay',target_state:entry.prefix>=30?'completed':'ongoing',season_prefix:entry.prefix,
  season_complete:entry.prefix>=30,max_cutoff:max,target_season:season,team,cutoff,length,context_start:cutoff-length+1,
  visible_cards:target.slice(0,cutoff),following_day:cutoff+1,next_day_recorded:Boolean(following?.ft),
  actual_next:reveal&&following?following:null,revealed:Boolean(reveal&&following),
  evidence,length_study:study,reference_seasons:referenceSeasons,reference_matches:references.results.length,
  reference_team_seasons:[...archiveFor(references).values()].reduce((n,teams)=>n+teams.size,0),
  no_target_or_future_reference_data:referenceSeasons.every(s=>s<season)};
}
// Earlier-season continuation ledger computed by the backend for the live board.
export function forecastScopes(workspace){return workspace.forecast?.scopes||null;}
export function forecastFor(workspace,scope='all'){
 const scopes=forecastScopes(workspace);
 if(!scopes||!scopes[scope])return null;
 const teams=scopes[scope].teams||[];
 const direction=teams.reduce((n,team)=>n+(team.summary?.direction_hits||0),0);
 return {...scopes[scope],scope,teams,direction_hits:direction,engine:workspace.forecast?.engine,
  season:workspace.forecast?.season,prefix:workspace.forecast?.prefix,upcoming_day:workspace.forecast?.upcoming_day,
  minimum_matches:workspace.forecast?.minimum_matches,
  direction_rate:scopes[scope].picks?Math.round(direction/scopes[scope].picks*1000)/10:null};
}
export function ledgerFor(teamView,day){
 return (teamView?.ledger||[]).find(row=>row.day===Number(day))||null;
}
