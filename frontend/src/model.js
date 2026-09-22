// All calculations consume recorded Betika results. No fixture or score seeds.
export const DEFINITIONS={
 parity:{name:'Odd / Even',labels:{O:'ODD',E:'EVEN'}},
 btts:{name:'Both teams to score',labels:{Y:'YES',N:'NO'}},
 dnb:{name:'Draw no bet',labels:{W:'WIN',L:'LOSS',V:'VOID'}},
 total:{name:'Over / Under 2.5',labels:{O:'O2.5',U:'U2.5'}}
};
export function outcome(kind,h,a,home=true){
 if(kind==='parity')return (h+a)%2?'O':'E';
 if(kind==='btts')return h>0&&a>0?'Y':'N';
 if(kind==='total')return h+a>=3?'O':'U';
 if(kind==='dnb')return h===a?'V':(home?h>a:a>h)?'W':'L';
 throw Error('Unknown blueprint');
}
const emptyCards=()=>Array.from({length:30},(_,i)=>({day:i+1,ft:null,code:null,label:'—',match_id:null}));
let archiveKey='',archiveValue=null;
export function archiveFor(ws){
 const key=`${ws.archive_id||''}:${ws.results_revision}:${ws.results.length}`;
 if(key===archiveKey&&archiveValue)return archiveValue;
 const seasons=new Map();
 for(const m of ws.results){
  if(m.status!=='final'||m.ft_home==null||m.ft_away==null)continue;
  if(!seasons.has(m.season))seasons.set(m.season,new Map());
  const teams=seasons.get(m.season);
  for(const isHome of [true,false]){
   const team=isHome?m.home:m.away;
   if(!teams.has(team))teams.set(team,emptyCards());
   const cards=teams.get(team),position=m.day-1;
   if(position<0||position>=30)continue;
   if(cards[position].match_id){cards[position]={...emptyCards()[position],ambiguous:true};continue;}
   if(cards[position].ambiguous)continue;
   const ft=`${m.ft_home}:${m.ft_away}`;
   cards[position]={day:m.day,ft,code:ft,score:ft,label:'CS',ht:`${m.ht_home}:${m.ht_away}`,home:m.home,away:m.away,
    home_goals:m.ft_home,away_goals:m.ft_away,venue:isHome?'H':'A',opponent:isHome?m.away:m.home,match_id:m.id};
  }
 }
 archiveKey=key;archiveValue=seasons;return seasons;
}
function patternCards(cards,kind){return cards.map(c=>{const code=c.ft==null?null:outcome(kind,c.home_goals,c.away_goals,c.venue==='H');return {...c,code,label:code?DEFINITIONS[kind].labels[code]:'—'};});}
export function blueprints(ws,season,kind){
 const rows=archiveFor(ws).get(Number(season))||new Map();
 const names=new Set(rows.keys());
 if(Number(season)===ws.overview.current_season)for(const t of ws.roster)names.add(t.team);
 return {season:Number(season),kind,definition:DEFINITIONS[kind],revision:ws.results_revision,
  teams:[...names].sort().map(team=>{const cards=patternCards(rows.get(team)||emptyCards(),kind);return {team,season:Number(season),kind,cards,signature:cards.map(c=>c.code||'.').join(''),played:cards.filter(c=>c.code!=null).length};})};
}
export function compareSymbols(ws,kind='parity',minimum=100,scope='all'){
 const began=performance.now(),up=ws.overview.upcoming,n=ws.overview.completed_prefix||0,current=ws.overview.current_season;
 const forming=blueprints(ws,current,kind).teams;
 const history=[];
 for(const [season] of archiveFor(ws))if(season<current)history.push(...blueprints(ws,season,kind).teams);
 let total=0,windows=0,exactCount=0;
 const teams=ws.roster.map(t=>{
  const cards=(forming.find(f=>f.team===t.team)?.cards||emptyCards()).slice(0,n),query=cards.map(c=>c.code);let candidates=[],searched=0;
  if(n&&query.every(c=>c!=null))for(const h of history){
   if(scope==='same'&&h.team!==t.team)continue;
   for(let start=0;start<=30-n;start++){
    const segment=h.cards.slice(start,start+n);if(segment.some(c=>c.code==null))continue;
    searched++;const matches=segment.reduce((v,c,i)=>v+Number(c.code===query[i]),0),similarity=Math.round(matches/n*10000)/100;
    if(similarity>=Number(minimum))candidates.push({team:h.team,season:h.season,start:start+1,end:start+n,length:n,similarity,exact:matches===n,matches,matched:matches,cards:h.cards});
   }
  }
  candidates.sort((a,b)=>b.similarity-a.similarity||b.season-a.season||a.start-b.start||(a.team<b.team?-1:a.team>b.team?1:0));
  const exact=candidates.filter(c=>c.exact).length;total+=candidates.length;windows+=searched;exactCount+=exact;
  return {team:t.team,cards,prefix:query.map(c=>c||'.').join(''),length:n,match_count:candidates.length,exact_count:exact,windows_scanned:searched,candidates:candidates.slice(0,20)};
 });
 return {kind,minimum:Number(minimum),scope,current_season:current,upcoming_day:up.day,length:n,teams,windows_scanned:windows,match_count:total,
 exact_count:exactCount,historical_blueprints:history.length,revision:ws.results_revision,computed_at:Date.now()/1000,compute_ms:Math.round((performance.now()-began)*100)/100,cached:false};
}

export class ExactIndex{
 constructor(history){this.history=history;this.lengths=new Map();}
 lookup(query,team=null){
  const n=query.length;if(n<2||query.some(c=>c==null))return {hits:[],count:0};
  if(!this.lengths.has(n)){
   const map=new Map(),counts=new Map();let count=0;
   for(const h of this.history)for(let start=0;start<=30-n;start++){
    const window=h.cards.slice(start,start+n);if(window.some(c=>c.ft==null))continue;
    const key=window.map(c=>c.ft).join('|');if(!map.has(key))map.set(key,[]);
    map.get(key).push({team:h.team,season:h.season,start:start+1,end:start+n,length:n,cards:h.cards,exact:true});count++;counts.set(h.team,(counts.get(h.team)||0)+1);
   }
   this.lengths.set(n,{map,count,counts});
  }
  const found=this.lengths.get(n),hits=found.map.get(query.join('|'))||[];
  return {hits:team?hits.filter(h=>h.team===team):[...hits],count:team?(found.counts.get(team)||0):found.count};
 }
}
export function tracePrefix(query,index,upcomingDay,team=null){
 const n=query.length,base={status:'waiting',ready:false,full_length:n,current_start:1,current_end:n,length:n,trimmed_count:0,match_count:0,full_match_count:0,candidates:[],attempts:[],windows_scanned:0};
 if(upcomingDay<3||n<2||query.some(c=>c==null))return base;
 base.ready=true;
 const maximumStart=upcomingDay>=5?n-1:1;
 for(let currentStart=1;currentStart<=maximumStart;currentStart++){
  const selected=query.slice(currentStart-1),{hits,count}=index.lookup(selected,team);
  base.windows_scanned+=count;base.current_start=currentStart;base.length=selected.length;base.trimmed_count=currentStart-1;
  base.attempts.push({current_start:currentStart,current_end:n,length:selected.length,match_count:hits.length,windows_scanned:count});
  if(currentStart===1)base.full_match_count=hits.length;
  if(hits.length){hits.sort((a,b)=>b.season-a.season||a.start-b.start||(a.team<b.team?-1:a.team>b.team?1:0));return {...base,status:currentStart===1?'full':'shortened',match_count:hits.length,candidates:hits.slice(0,20).map(c=>({...c,current_start:currentStart,current_end:n}))};}
 }
 return {...base,status:'no_match'};
}
export function compareGold(ws,scope='all'){
 const begin=performance.now(),current=ws.overview.current_season,day=ws.overview.upcoming.day||0,n=Math.min(ws.overview.completed_prefix||0,Math.max(0,day-1));
 const history=[];
 for(const [season,teams] of archiveFor(ws))if(season<current)for(const [team,cards] of teams)history.push({season,team,cards});
 const index=new ExactIndex(history);
 const teams=ws.roster.map(t=>{
  const fullCards=t.cards.slice(0,n),query=fullCards.map(c=>c.ft),result=tracePrefix(query,index,day,scope==='same'?t.team:null);
  return {...result,team:t.team,full_cards:fullCards,cards:fullCards.slice(result.current_start-1),pattern:query.slice(result.current_start-1)};
 });
 return {engine:'gold-data-first-v3',method:'full_prefix_first_then_trim_oldest',teams,current_season:current,upcoming_day:day,length:n,current_end:n,scope,
 ready:day>=3&&n>=2,match_count:teams.reduce((v,t)=>v+t.match_count,0),windows_scanned:teams.reduce((v,t)=>v+t.windows_scanned,0),
 full_match_teams:teams.filter(t=>t.status==='full').length,shortened_match_teams:teams.filter(t=>t.status==='shortened').length,
 historical_blueprints:history.length,fallback_enabled:day>=5,computed_at:Date.now()/1000,compute_ms:Math.round((performance.now()-begin)*100)/100};
}
