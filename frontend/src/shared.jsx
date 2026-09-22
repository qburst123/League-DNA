import React, {useState, useEffect, useRef} from 'react';
import {ArrowUpRight, Check, X, Loader2, Clock3, Info, Fingerprint, CircleDot, Columns3, GitCompareArrows, Download, ShieldCheck, AlertTriangle, ChevronDown} from 'lucide-react';

import {api as workspaceApi,getWorkspace,setWorkspace,setOnline,isOnline,exportCsv,offlineMessage} from './bridge';

export const PATTERNS = {
  parity: {name:'Odd / Even', short:'O / E', number:'01', description:'Combined full-time goals', labels:{O:'Odd', E:'Even'}, icon:CircleDot},
  btts: {name:'Both teams to score', short:'BTTS', number:'02', description:'Both teams score at full time', labels:{Y:'Yes', N:'No'}, icon:GitCompareArrows},
  dnb: {name:'Draw no bet', short:'DNB', number:'03', description:'Team-relative · draws are void', labels:{W:'Win', L:'Loss', V:'Void'}, icon:ShieldCheck},
  total: {name:'Over / Under 2.5', short:'O / U', number:'04', description:'Combined full-time goals', labels:{O:'Over 2.5', U:'Under 2.5'}, icon:Columns3},
};
export const num = n => Number(n || 0).toLocaleString('en-KE');
export const pad = n => String(n).padStart(2,'0');
export const timeLabel = t => t ? new Date(t*1000).toLocaleTimeString('en-GB',{timeZone:'Africa/Nairobi',hour:'2-digit',minute:'2-digit',second:'2-digit'}) : 'Not yet synced';
export const dateLabel = t => t ? new Date(t*1000).toLocaleString('en-GB',{timeZone:'Africa/Nairobi',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'}) + ' EAT' : '—';
export const ago = t => {if(!t)return 'pending'; const s=Math.max(0,Math.floor(Date.now()/1000-t));return s<5?'just now':s<60?`${s}s ago`:s<3600?`${Math.floor(s/60)}m ago`:`${Math.floor(s/3600)}h ago`;};
export const scoreText = (a,b) => a == null || b == null ? '—' : `${a}:${b}`;
export const api=(url,options={})=>workspaceApi(url,options);
export function useResource(url, revision=0){
  const [data,setData]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(true);const previousUrl=useRef(null);
  useEffect(()=>{if(!url){setData(null);setLoading(false);return;} if(previousUrl.current!==url){setData(null);previousUrl.current=url;} let live=true;const ctrl=new AbortController();setLoading(true);setError('');
    api(url,{signal:ctrl.signal}).then(d=>{if(live)setData(d);}).catch(e=>{if(live&&e.name!=='AbortError')setError(e.message);}).finally(()=>{if(live)setLoading(false);});
    return()=>{live=false;ctrl.abort();};
  },[url,revision]);
  const sameUrl=previousUrl.current===url;
  return {data:sameUrl?data:null,error:sameUrl?error:'',loading:loading||Boolean(url&&!sameUrl)};
}
export function useLive(){
  const [workspace,setData]=useState(()=>getWorkspace()),[mode,setMode]=useState('snapshot'),[error,setError]=useState(''),[connected,setConnected]=useState(false);
  const busy=useRef(false),alive=useRef(true),last=useRef(0),etag=useRef(''),current=useRef(workspace);
  async function refresh(){
    if(busy.current)return;
    if(!['http:','https:'].includes(location.protocol)){setMode('snapshot');return;}
    busy.current=true;last.current=Date.now();const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),30000);
    try{
      const response=await fetch('/api/workspace',{signal:controller.signal,headers:etag.current?{'If-None-Match':etag.current}:{}});
      if(response.status===304){if(alive.current){setMode('live');setOnline(true);setError('');}return;}
      if(!response.ok)throw Error(`Collector connection returned ${response.status}`);
      const next=await response.json();
      if(next.schema!=='league-dna-workspace-1'||!Array.isArray(next.roster)||!Array.isArray(next.results))throw Error('The live collector needs the v2 application update.');
      if(!alive.current)return;
      if(next.roster.length===0&&current.current?.roster.length){setMode('snapshot');setOnline(false);setError('The collector is starting. Showing the recorded roster until the new fixture feed is available.');return;}
      etag.current=response.headers.get('etag')||'';current.current=next;setWorkspace(next,true);setData(next);setMode('live');setError('');
    }catch(e){if(alive.current){setMode('snapshot');setOnline(false);setConnected(false);setError(e.name==='AbortError'?'Live connection timed out. Saved teams and scores are still available.':e.message);}}
    finally{clearTimeout(timeout);busy.current=false;}
  }
  useEffect(()=>{alive.current=true;refresh();let events;
    if(['http:','https:'].includes(location.protocol)){
      try{events=new EventSource('/api/events');events.onopen=()=>setConnected(true);events.onerror=()=>setConnected(false);events.onmessage=()=>{if(Date.now()-last.current>1700)refresh();};}catch{setConnected(false);}
    }
    const interval=setInterval(refresh,8000);
    return()=>{alive.current=false;events?.close();clearInterval(interval);};
  },[]);
  const data=workspace?{...workspace.overview,offline:mode!=='live',server_time:mode==='live'?workspace.overview.server_time:Date.now()/1000,
    source:{...workspace.overview.source,stale:mode!=='live'||workspace.overview.source.stale},snapshot_at:workspace.captured_at}:null;
  return {data,workspace,mode,error,connected:connected&&mode==='live',refresh};
}
export function TeamBadge({name='',small=false}){
  const hash=[...name].reduce((a,c)=>a+c.charCodeAt(0),0);
  const colors=['violet','mint','blue','rose','amber','teal'];
  const letters=name.replace(/\bBSL\b/g,'').trim().split(/\s+/).slice(0,2).map(w=>w[0]).join('').toUpperCase();
  return <span className={`team-badge ${colors[hash%colors.length]} ${small?'small':''}`} title={`${name} · generated monogram, not an official crest`}>{letters}</span>;
}
export function Status({children,tone='green',dot=true}){return <span className={`status ${tone}`}>{dot&&<i/>}{children}</span>;}
export function PageHead({eyebrow='BETIKA LEAGUE',title,description,children}){
  return <div className="page-head"><div><div className="eyebrow"><span/>{eyebrow}</div><h1>{title}</h1><p>{description}</p></div><div className="page-actions">{children}</div></div>;
}
export function Loading({text='Reading the local archive…'}){return <div className="empty-state"><Loader2 className="spin" size={26}/><h3>{text}</h3><p>No external or generated match data is used.</p></div>;}
export function Empty({title='Nothing here yet',description,icon:Icon=Fingerprint,children}){return <div className="empty-state"><div className="empty-icon"><Icon size={28}/></div><h3>{title}</h3><p>{description}</p>{children}</div>;}
export function ErrorBanner({error}){return error?<div className="notice warning"><AlertTriangle size={17}/><span>{error}</span></div>:null;}
export function PatternTabs({kind,onChange,compact=false}){return <div className={`pattern-tabs ${compact?'compact':''}`} role="tablist" aria-label="Fingerprint blueprint">{Object.entries(PATTERNS).map(([k,p])=><button key={k} role="tab" aria-selected={kind===k} className={kind===k?'active':''} onClick={()=>onChange(k)}><span className="pattern-icon"><p.icon size={19}/></span><span><small>BLUEPRINT {p.number}</small><strong>{p.name}</strong>{!compact&&<em>{p.description}</em>}</span>{kind===k&&<Check className="pattern-check" size={16}/>}</button>)}</div>;}
export function Legend({kind,missing=true}){return <div className="legend">{Object.entries(PATTERNS[kind].labels).map(([code,label])=><span key={code}><i className={`code-${kind}-${code}`}/>{label}</span>)}{missing&&<span><i className="missing"/>Not finalized</span>}</div>;}
export function PatternCard({card,kind,muted=false,onInspect,mini=false,withDay=false,mismatch=false}){
  const filled=card?.code; const title=filled?`MD ${card.day} · ${card.home} ${card.ft} ${card.away} · HT ${card.ht} · ${PATTERNS[kind].labels[card.code]}${card.venue?' · Team is '+(card.venue==='H'?'home':'away'):''}`:`Matchday ${card?.day || '—'} · No confirmed full-time result`;
  return <button disabled={!filled||!onInspect} onClick={()=>onInspect?.(card.match_id)} title={title} aria-label={title} className={`pattern-card ${filled?`code-${kind}-${card.code}`:'missing'} ${muted?'muted':''} ${mini?'mini':''} ${mismatch?'mismatch':''}`}>
    {withDay&&<small className="card-day">{pad(card?.day || 0)}</small>}{!mini&&<><strong>{filled?card.label:'—'}</strong><span>{card?.ft || '·'}</span></>}{mismatch&&<b className="mismatch-dot">×</b>}
  </button>;
}
export function Countdown({start,serverTime,compact=false,stale=false}){
  const [now,setNow]=useState(Date.now()/1000);const offset=useRef(0);
  useEffect(()=>{if(serverTime)offset.current=serverTime-Date.now()/1000;},[serverTime]);
  useEffect(()=>{const t=setInterval(()=>setNow(Date.now()/1000),1000);return()=>clearInterval(t);},[]);
  const seconds=Math.ceil((start||0)-(now+offset.current));
  return <div className={`countdown ${compact?'compact':''} ${seconds<=0||stale?'waiting':''}`}><Clock3 size={16}/><span>{stale?'Source delayed':seconds<=0?'Awaiting next update':compact?'Starts in':'NEXT MATCHDAY IN'}</span>{seconds>0&&!stale&&<strong>{seconds>=3600?`${pad(Math.floor(seconds/3600))}:`:''}{pad(Math.floor(seconds%3600/60))}<b>:</b>{pad(seconds%60)}</strong>}</div>;
}
export function Modal({title,children,onClose,wide=false,drawer=false}){
  const ref=useRef(null),prevFocus=useRef(null);
  useEffect(()=>{prevFocus.current=document.activeElement;ref.current?.querySelector('button')?.focus();const previous=document.body.style.overflow;document.body.style.overflow='hidden';
    function key(e){if([...document.querySelectorAll('[role="dialog"]')].at(-1)!==ref.current)return;if(e.key==='Escape')onClose();if(e.key==='Tab'){const all=ref.current?.querySelectorAll('button:not(:disabled),a,input,select,textarea,[tabindex="0"]');if(!all?.length)return;const first=all[0],last=all[all.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus();}}}
    window.addEventListener('keydown',key);return()=>{document.body.style.overflow=previous;window.removeEventListener('keydown',key);prevFocus.current?.focus?.();};
  },[]);
  return <div className={`modal-overlay ${drawer?'drawer-overlay':''}`} onMouseDown={e=>{if(e.target===e.currentTarget)onClose();}}><section ref={ref} className={`modal ${wide?'wide':''} ${drawer?'drawer':''}`} role="dialog" aria-modal="true" aria-label={title}><header className="modal-head"><div><div className="eyebrow">LEAGUE DNA / WORKSPACE</div><h2>{title}</h2></div><button className="icon-button" onClick={onClose} aria-label="Close dialog"><X size={21}/></button></header>{children}</section></div>;
}
export function ExportButton({season,toast}){
  const [open,setOpen]=useState(false);
  return <div className="export-wrap"><button className="button secondary" onClick={()=>setOpen(!open)} aria-expanded={open}><Download size={16}/><span>Export data</span><ChevronDown size={14}/></button>{open&&<><button className="menu-scrim" aria-label="Close export menu" onClick={()=>setOpen(false)}/><div className="dropdown-menu"><a href={`/api/export/results.csv${season?`?season=${season}`:''}`} download onClick={e=>{setOpen(false);if(!isOnline()){e.preventDefault();exportCsv(season);}toast?.('CSV prepared from recorded results.');}}><Download size={16}/>{season?'Selected season · CSV':'All results · CSV'}</a><a href="/api/export/database" download onClick={e=>{setOpen(false);if(!isOnline()){e.preventDefault();toast?.('The complete SQLite database is in the downloadable project. Run the collector to create a newer backup.','info');return;}toast?.('Preparing a consistent SQLite database snapshot…');}}><ShieldCheck size={16}/>Complete database · SQLite</a></div></>}</div>;
}
export function Methodology({onClose}){return <Modal title="A fingerprint, not a forecast." onClose={onClose} wide><div className="methodology"><p className="lead">A transparent workspace for reading Betika League records. Every colored card comes from a confirmed full-time result in the local archive.</p><div className="method-grid">{Object.entries(PATTERNS).map(([k,p])=><article key={k}><p.icon size={22}/><h3>{p.number} / {p.name}</h3><p>{k==='parity'?'Home goals + away goals. Odd or even; 0:0 is even.':k==='btts'?'Yes when both sides score at least once. Otherwise, no.':k==='dnb'?'Win or loss from the selected team’s perspective. Every draw has its own VOID state.':'Over 2.5 is a combined total of at least 3 goals. Under 2.5 is a total of 0, 1 or 2.'}</p><Legend kind={k} missing={false}/></article>)}</div><h3>How sliding alignment works</h3><p>The current sequence starts at matchday 1 and stops at the last consecutively closed matchday. The comparator checks that entire sequence at every possible starting position within earlier seasons, for all 16 upcoming teams. It does not wrap across seasons, bridge missing scores, or include the playing matchday before it closes.</p><div className="method-example"><span>Current season</span><code>01 → 08</code><GitCompareArrows size={20}/><span>Historical season</span><code>05 → 12</code></div><h3>Similarity is not predictive confidence</h3><p>100% means the observed symbols are identical in that window. Repeated sequences can occur by chance, especially with short binary sequences and many searched windows. Two opponents also share parity, BTTS and total-goals outcomes. Matches are not independent evidence and do not establish a reliable betting edge.</p><div className="notice"><Info size={18}/><span>Scores are always shown in home:away order. Blank cards are unknown, not 0:0. Team monograms are generated UI identifiers, not official team logos.</span></div><p className="fine-print">Independent, read-only research tool. Not affiliated with Betika. No account access, wagering recommendations, bet placement or market auto-selection. Public-feed updates depend on provider availability; zero latency cannot be guaranteed.</p></div></Modal>;}
