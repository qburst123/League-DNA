import {blueprints,compareSymbols,compareGold} from './model';
let workspace=globalThis.__LEAGUE_BOOTSTRAP__||null;
let online=false;
const memo=new Map();
export const getWorkspace=()=>workspace;
export const isOnline=()=>online;
export function setWorkspace(data,live=false){workspace=data;online=live;}
export function setOnline(value){online=value;}
export function offlineMessage(){return 'This is a saved snapshot. Start the included live collector to use this control; the displayed teams and results remain available.';}
export async function network(url,options={}){
 const response=await fetch(url,{...options,headers:{'Content-Type':'application/json','X-Requested-With':'LeagueDNA',...options.headers}});
 if(!response.ok){let message;try{const detail=(await response.json()).detail;message=Array.isArray(detail)?detail.map(d=>d.msg).join('; '):detail;}catch{}throw Error(message||`Request failed (${response.status})`);}
 return response.json();
}
function remembered(key,fn){if(memo.has(key))return {...memo.get(key),cached:true};const value=fn();memo.set(key,value);if(memo.size>32)memo.delete(memo.keys().next().value);return value;}
export async function api(url,options={}){
 if(options.signal?.aborted)throw new DOMException('Aborted','AbortError');
 const method=options.method||'GET';
 if(method!=='GET'){
  if(!online)throw Error(offlineMessage());
  return network(url,options);
 }
 const data=workspace;
 if(!data)return network(url,options);
 const parsed=new URL(url,'https://league-dna.local'),path=parsed.pathname,q=parsed.searchParams;
 if(path==='/api/overview')return data.overview;
 if(path==='/api/blueprints')return blueprints(data,Number(q.get('season')),q.get('kind')||'parity');
 if(path==='/api/compare'){
  const key=`${data.archive_id}:${data.results_revision}:${data.overview.current_season}:${data.overview.upcoming.day}:${url}`;
  return remembered(key,()=>compareSymbols(data,q.get('kind')||'parity',Number(q.get('minimum')||100),q.get('scope')||'all'));
 }
 if(path==='/api/gold')return compareGold(data,q.get('scope')||'all');
 if(path==='/api/results')return {matches:data.results.filter(m=>(!q.has('season')||m.season===Number(q.get('season')))&&(!q.has('day')||m.day===Number(q.get('day')))&&(!q.get('team')||m.home===q.get('team')||m.away===q.get('team'))).sort((a,b)=>b.season-a.season||a.day-b.day||a.id-b.id),server_time:data.captured_at};
 if(path.startsWith('/api/matches/')){const row=data.results.find(m=>m.id===Number(path.split('/').pop()));if(!row)throw Error('This match is not in the recorded archive.');return {match:row};}
 if(path.startsWith('/api/markets/')){const found=data.markets[path.split('/').pop()];if(!found)throw Error('This fixture’s market catalogue has not yet been captured.');return found;}
 if(path==='/api/health')return {...data.health,snapshot_mode:!online,captured_at:data.captured_at};
 if(path==='/api/pins'){if(!online)return {pins:[]};return network(url,options);}
 if(path.startsWith('/api/provenance/')){
  const digest=path.split('/').pop(),row=data.receipts?.[digest];
  if(row){
   if(typeof DecompressionStream==='undefined'){if(online)return network(url,options);throw Error('Your browser cannot decompress the saved receipt. The original evidence remains in the included SQLite database.');}
   const bytes=Uint8Array.from(atob(row.body_deflate),c=>c.charCodeAt(0));
   const text=await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate'))).text();
   const {body_deflate,...meta}=row;return {...meta,payload:JSON.parse(text)};
  }
  if(online)return network(url,options);
  throw Error('This receipt was not included in the saved snapshot. The score record and its hash are still available.');
 }
 if(!online)throw Error(offlineMessage());
 return network(url,options);
}
export function downloadText(name,text,type='text/plain'){
 const blob=new Blob([text],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);
}
export function exportCsv(season){
 const data=workspace;if(!data)return;
 const fields=['season','day','home','away','status','ht_home','ht_away','ft_home','ft_away','final_source_hash'];
 const quote=value=>'"'+String(value??'').replaceAll('"','""')+'"';
 const rows=data.results.filter(r=>!season||r.season===Number(season));
 downloadText(`league-dna-${season||'all-results'}.csv`,[fields.join(','),...rows.map(r=>fields.map(f=>quote(r[f])).join(','))].join('\n'),'text/csv;charset=utf-8');
}
