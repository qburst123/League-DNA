import {archiveFor} from './model.js';
export const scoreOrder=(a,b)=>{const [ah,aa]=a.split(':').map(Number),[bh,ba]=b.split(':').map(Number);return ah-bh||aa-ba;};
export function validateSequence(text){
 const tokens=text.trim().split(/[\s,;>→]+/).filter(Boolean);
 if(tokens.length<1||tokens.length>30)throw Error('Enter between 1 and 30 FT scores.');
 if(tokens.some(t=>!/^\d{1,2}:\d{1,2}$/.test(t)))throw Error('Use home:away scores separated by commas, for example 0:0, 0:1.');
 return tokens.map(t=>t.split(':').map(Number).join(':'));
}
export function scoreVocabulary(ws){
 const counts=new Map();
 for(const m of ws.results)if(m.status==='final'&&m.ft_home!=null&&m.ft_away!=null){const score=`${m.ft_home}:${m.ft_away}`;counts.set(score,(counts.get(score)||0)+1);}
 return [...counts].sort((a,b)=>scoreOrder(a[0],b[0])).map(([score,matches])=>({score,matches}));
}
export function historicalFollowers(ws,pattern){
 const started=performance.now(),n=pattern.length;
 const archive=archiveFor(ws),outcomes=new Map(),seenSeasons=new Set(),seasons=new Map(),examples=[];
 let occurrences=0,withoutNext=0,traces=0;
 for(const [season,teams] of archive)for(const [team,cards] of teams){
  for(let start=0;start<=30-n;start++){
   const context=cards.slice(start,start+n);if(context.some((c,i)=>c.ft==null||c.ft!==pattern[i]))continue;
   occurrences++;seenSeasons.add(season);
   if(!seasons.has(season))seasons.set(season,{season,occurrences:0,nextMatches:new Set(),nextScores:new Set()});
   const seasonData=seasons.get(season);seasonData.occurrences++;
   const next=cards[start+n];
   if(!next||next.ft==null){withoutNext++;continue;}
   traces++;seasonData.nextMatches.add(next.match_id);seasonData.nextScores.add(next.ft);
   if(!outcomes.has(next.ft))outcomes.set(next.ft,{score:next.ft,matches:new Set(),traces:0,seasons:new Set()});
   const item=outcomes.get(next.ft);item.matches.add(next.match_id);item.traces++;item.seasons.add(season);
   examples.push({team,season,start:start+1,end:start+n,context,next,next_day:next.day});
  }
 }
 const followingMatches=[...outcomes.values()].reduce((v,r)=>v+r.matches.size,0);
 const rows=[...outcomes.values()].map(o=>({score:o.score,matches:o.matches.size,traces:o.traces,seasons:o.seasons.size,historical_share:followingMatches?Math.floor((o.matches.size*20000+followingMatches)/(2*followingMatches))/100:0}));
 rows.sort((a,b)=>b.matches-a.matches||scoreOrder(a.score,b.score));
 examples.sort((a,b)=>b.season-a.season||a.start-b.start||(a.team<b.team?-1:a.team>b.team?1:0));
 return {pattern,length:n,occurrences,context_seasons:seenSeasons.size,following_matches:followingMatches,following_traces:traces,
  without_recorded_followup:withoutNext,outcomes:rows,examples,seasons:[...seasons.values()].map(s=>({season:s.season,occurrences:s.occurrences,following_matches:s.nextMatches.size,distinct_outcomes:s.nextScores.size})).sort((a,b)=>b.season-a.season),
  compute_ms:Math.round((performance.now()-started)*100)/100};
}
export function observedCatalogue(ws,length){
 const patterns=new Map();
 for(const [season,teams] of archiveFor(ws))for(const [,cards] of teams)for(let start=0;start<=30-length;start++){
  const context=cards.slice(start,start+length);if(context.some(c=>c.ft==null))continue;
  const pattern=context.map(c=>c.ft),key=pattern.join('|');
  if(!patterns.has(key))patterns.set(key,{pattern,occurrences:0,seasons:new Set(),nextMatches:new Set()});
  const entry=patterns.get(key);entry.occurrences++;entry.seasons.add(season);
  const next=cards[start+length];if(next?.ft!=null)entry.nextMatches.add(next.match_id);
 }
 return [...patterns.values()].map(p=>({pattern:p.pattern,occurrences:p.occurrences,seasons:p.seasons.size,following_matches:p.nextMatches.size})).sort((a,b)=>b.occurrences-a.occurrences||comparePatterns(a.pattern,b.pattern));
}
function comparePatterns(a,b){for(let i=0;i<Math.min(a.length,b.length);i++){const c=scoreOrder(a[i],b[i]);if(c)return c;}return a.length-b.length;}
