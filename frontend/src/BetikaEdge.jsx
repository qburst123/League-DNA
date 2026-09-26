import React,{useState,useEffect,useMemo,useCallback} from 'react';
import {Activity,BarChart3,Brain,Calculator,Database,FileBarChart,FlaskConical,Gauge,LineChart,ListChecks,PieChart,RefreshCw,Settings,ShieldAlert,Sparkles,Target,TrendingUp,Wand2,X,Zap,ArrowDownUp,ChevronDown,PlayCircle,Copy,ExternalLink} from 'lucide-react';
import {PageHead,TeamBadge,Status,Loading,ErrorBanner,Empty,Modal,api,num} from './shared';
import {BETIKA_PATHS,LEAGUE_HEADERS,Pct,Fixed,Money,buildMatchId} from './betikaModel';

async function postJson(url, body) {
  const r = await fetch(url, {method: 'POST', headers:{'Content-Type':'application/json',...LEAGUE_HEADERS},
                               body: JSON.stringify(body || {})});
  if(!r.ok) throw Error((await r.text()) || `Request failed (${r.status})`);
  return r.json();
}
async function getJson(url) {
  const r = await fetch(url, {headers:{...LEAGUE_HEADERS}});
  if(!r.ok) throw Error((await r.text()) || `Request failed (${r.status})`);
  return r.json();
}

const TABS = [
  {id:'data',    label:'Data Collection', icon:Database},
  {id:'teams',   label:'Teams',           icon:Activity},
  {id:'h2h',     label:'H2H Explorer',    icon:Gauge},
  {id:'goals',   label:'Goal Distribution', icon:BarChart3},
  {id:'predict', label:'Predictions',     icon:Brain},
  {id:'bias',    label:'Market Bias',     icon:ShieldAlert},
  {id:'value',   label:'Value Bets',      icon:Sparkles},
  {id:'ledger',  label:'Paper Ledger',    icon:Calculator},
  {id:'md',      label:'Matchday Tracker',icon:ListChecks},
  {id:'settings',label:'Settings',        icon:Settings},
];

function useResource(path, deps = []) {
  const [data,setData] = useState(null); const [error,setError] = useState('');
  const [loading,setLoading] = useState(true); const [version,setVersion] = useState(0);
  useEffect(()=>{let live=true;setLoading(true);setError('');
    getJson(path).then(d=>{if(live){setData(d);}}).catch(e=>{if(live)setError(e.message||'request failed');}).finally(()=>{if(live)setLoading(false);});
    return()=>{live=false;};},[path, ...deps, version]);
  return {data,error,loading,refresh:()=>setVersion(v=>v+1)};
}

function NoDataHint() {
  return <Empty title="Live archive isn't connected" description="This workspace reads from the live league.sqlite when present and falls back to the bundled fixtures when it isn't — both are available on this server." icon={Database}/>;
}

function SettingsEditor({settings, onChange}) {
  const items = [
    ['min_edge','Minimum edge (positive EV)', {min:0,max:1,step:0.01,fmt:'pct'}],
    ['kelly_fraction','Kelly fraction',       {min:0,max:1,step:0.05,fmt:'float'}],
    ['h2h_both_gt_05','H2H both-teams > 0.5 threshold', {min:0,max:1,step:0.05,fmt:'pct'}],
    ['h2h_min_meetings','Minimum H2H meetings',{min:0,max:50,step:1,fmt:'int'}],
    ['stake','Default stake per column',      {min:1,step:10,fmt:'float'}],
    ['bankroll','Starting bankroll',           {min:0,step:100,fmt:'float'}],
    ['min_odds','Minimum odds required (both markets)', {min:1.01,step:0.05,fmt:'float'}],
    ['cross_market_threshold','Cross-market disagreement threshold', {min:0,max:1,step:0.01,fmt:'pct'}],
    ['current_season','Current season ID',     {fmt:'int'}],
  ];
  const [busy,setBusy] = useState(false); const [saved,setSaved] = useState('');
  function update(key, v) { onChange(key, v); }
  async function saveAll() { setBusy(true); try { await postJson(BETIKA_PATHS.settingsSet, settings); setSaved('saved'); setTimeout(()=>setSaved(''),1500); }
                              catch(e){setSaved(e.message);} finally{setBusy(false);} }
  return <div className="panel">
    <table className="archive-table" style={{width:'100%'}}><thead><tr><th>Setting</th><th style={{textAlign:'right'}}>Value</th></tr></thead>
      <tbody>{items.map(([key,label,opts])=>{
        const fmt = opts.fmt || 'float';
        let value = settings[key] ?? '';
        if (fmt === 'int') value = parseInt(value || 0,10);
        else if (fmt === 'pct') value = (parseFloat(value || 0)*100).toFixed(0);
        else value = parseFloat(value || 0);
        return <tr key={key}><td>{label}<div className="fine-print"><code>{key}</code></div></td>
          <td style={{textAlign:'right'}}>
            {fmt === 'int' ?
              <input type="number" min={opts.min} max={opts.max} step={opts.step || 1} value={value}
                     style={{width:140, textAlign:'right'}}
                     onChange={e=>update(key, parseInt(e.target.value||0,10))}/> :
              <input type="number" min={opts.min} max={opts.max} step={opts.step || 0.01} value={value}
                     style={{width:140, textAlign:'right'}}
                     onChange={e=>update(key, parseFloat(e.target.value||0))}/>
            }
            <span style={{marginLeft:6, fontSize:11, opacity:0.7}}>{fmt==='pct' ? '%' : ''}</span>
          </td>
        </tr>;
      })}</tbody></table>
    <div style={{display:'flex',gap:12,alignItems:'center',marginTop:12}}>
      <button className="button secondary" onClick={saveAll} disabled={busy}>{busy?'Saving…':saved||'Save settings'}</button>
      <span className="fine-print">Settings are stored in <code>data/edge.sqlite → edge_settings</code>.</span>
    </div>
  </div>;
}

function PnL({value, suffix=''}) {
  if(value == null || Number.isNaN(value)) return <span className="fine-print">—</span>;
  const tone = value > 0.005 ? 'green' : value < -0.005 ? 'red' : 'muted';
  return <span className={`status ${tone}`}>{Money(value)}{suffix}</span>;
}

export default function BetikaEdge({toast}) {
  const [tab,setTab] = useState('data');
  const [statusD,statusE,statusL] = [(useResource.status=null),(useResource.statusE=''),(useResource.statusL=true),(useResource.refresh)];
  const status = useResource(BETIKA_PATHS.status, []);
  const teams  = useResource(BETIKA_PATHS.teams, []);
  const ledger = useResource(BETIKA_PATHS.ledger, []);

  const refreshAll = useCallback(()=>{ status.refresh(); teams.refresh(); ledger.refresh(); toast?.('Refreshed.', 'success'); },
                                [status, teams, ledger, toast]);

  const data = status.data || {};
  const summary = (ledger.data && ledger.data.summary) || {};

  const tabs = TABS;
  return <>
    <PageHead eyebrow="BETIKA EDGE ANALYZER" title="A statistical view of every fixture" description="Bivariate Poisson fit, raw / no-vig market tables, value-bet scan and a two-market paper-betting ledger — all reading the live league.sqlite when it has data, otherwise the bundled fixtures.">
      <button className="button secondary" onClick={refreshAll}><RefreshCw size={16}/><span>Refresh</span></button>
    </PageHead>

    <div className="betika-tabs" role="tablist" aria-label="Betika workspaces">
      {tabs.map(t=>{const Icon = t.icon; const active = tab===t.id;
        return <button key={t.id} role="tab" aria-selected={active} className={`betika-tab ${active?'active':''}`} onClick={()=>setTab(t.id)}>
          <Icon size={17}/><span>{t.label}</span>
        </button>;
      })}
    </div>

    {statusE ? <ErrorBanner error={statusE}/> :
      statusL ? <Loading text="Loading analyzer status…"/> :
        !data.live_archive ? (
          <div className="notice"><Database size={17}/><span>
            <b>Snapshot mode.</b> Reading the bundled fixtures under <code>tests/fixtures/</code>.  When the live archive at <code>data/league.sqlite</code> starts collecting, this workspace picks up new results automatically.
          </span></div>) : null}

    <div className="panel" style={{marginBottom:14}}>
      <div className="betika-stat-row">
        <Stat label="Live archive" value={data.live_archive ? 'connected' : 'snapshot'} tone={data.live_archive?'green':'amber'}/>
        <Stat label="Edge H2H rows" value={num(data.edge_h2h_rows ?? 0)}/>
        <Stat label="Markets cached" value={num(data.edge_markets_cached ?? 0)}/>
        <Stat label="Teams" value={num(data.teams_count ?? 0)}/>
        <Stat label="Matches visible" value={num(data.matches_count ?? 0)}/>
        <Stat label="Edge DB" value={(data.edge_db || '').split('/').slice(-2).join('/')} mono/>
      </div>
    </div>

    {tab === 'data' && <DataTab status={data} onRefresh={refreshAll}/>}
    {tab === 'teams' && <TeamsTab teams={teams}/>}
    {tab === 'h2h' && <H2HTab teams={teams}/>}
    {tab === 'goals' && <GoalsTab/>}
    {tab === 'predict' && <PredictTab teams={teams}/>}
    {tab === 'bias' && <BiasTab teams={teams}/>}
    {tab === 'value' && <ValueTab teams={teams}/>}
    {tab === 'ledger' && <LedgerTab ledger={ledger} onChange={refreshAll} settings={data}/>}
    {tab === 'md' && <MatchdayTab/>}
    {tab === 'settings' && <SettingsTab/>}

    <div className="responsible-footer">
      Independent, read-only research workspace · Bivariate Poisson is a small-sample model — fit values are dominated by 1–2 outlier matches when fewer than ~50 games are recorded.
    </div>
  </>;
}

function Stat({label,value,tone,mono}){
  return <div className="betika-stat"><span>{label}</span><b className={mono?'mono':''}>{value}</b>{tone && <Status tone={tone}>{value}</Status>}</div>;
}

function FixturePicker({teams, label='Fixture'}) {
  const matches = useResource(BETIKA_PATHS.matches, []);
  const list = (matches.data && matches.data.matches) || [];
  const teamMap = ((teams.data && teams.data.teams) || []).reduce((acc,t)=>{acc[t.id]=t.name;return acc;},{});
  if(!list.length) return <Empty title="No matches" description="Add data on the Data Collection tab." icon={Database}/>;
  const opts = list.map(m=>`${m.home} vs ${m.away} · MD${m.matchday} · ${m.status}`);
  const [sel,setSel] = useState(0);
  const m = list[Math.min(sel, list.length-1)];
  return (
    <div>
      <label><span>{label}</span>
        <select onChange={e=>setSel(parseInt(e.target.value,10))}>
          {opts.map((o,i)=><option key={i} value={i}>{o}</option>)}
        </select>
      </label>
      {m && <FixtureRef m={m}/>}
    </div>
  );
}

function FixtureRef({m}) {
  if(!m) return null;
  return <div className="archive-teams" style={{marginTop:8}}>
    <span><TeamBadge name={m.home} small/>{m.home}</span>
    <small>vs</small>
    <span><TeamBadge name={m.away} small/>{m.away}</span>
    <span className="fine-print">MD{m.matchday} · {m.season}{m.ft_home!=null?` · FT ${m.ft_home}:${m.ft_away}`:''}</span>
  </div>;
}

/* ----------------------------- Tabs ------------------------------- */

function DataTab({status, onRefresh}) {
  const [busy,setBusy] = useState(false);
  const [msg,setMsg] = useState('');
  async function refresh(){
    setBusy(true); setMsg('');
    try { await postJson(BETIKA_PATHS.refresh, {}); setMsg('Refreshed H2H + markets cache from archive + fixtures.'); onRefresh(); }
    catch(e){setMsg(e.message);} finally{setBusy(false);}
  }
  async function seed(){
    setBusy(true); setMsg('');
    try { const r = await postJson(BETIKA_PATHS.seedH2H, {home:'Bandarini BSL', away:'Walinzi BSL', n:5});
          setMsg(`Seeded ${r.added} synthetic H2H meetings (season=0).`); onRefresh(); }
    catch(e){setMsg(e.message);} finally{setBusy(false);}
  }
  const c = status.collector || {};
  const errs = status.last_endpoint_errors || [];
  const activity = status.activity || [];
  const lastFetch = errs.length ? errs[0].at : null;
  const collectorHealthy = c.max_requests_per_second && !c.stale && c.receipts_cached > 0;
  return <div>
    <div className="panel">
      <h3><Database size={18}/> Live data collector</h3>
      <p className="fine-print">
        Source: <b>{c.source || 'Betika public website feeds'}</b> ({c.domain || 'virtuals.betika.com'}). Read-only, ≤{c.max_requests_per_second || 1} req/s.
      </p>
      <div className="betika-stat-row" style={{marginBottom:12}}>
        <Stat label="State"
              value={c.paused ? 'paused' : collectorHealthy ? 'live' : (c.stale ? 'stale (no fresh data)' : 'starting')}
              tone={collectorHealthy ? 'green' : (c.paused ? 'amber' : 'amber')}/>
        <Stat label="Current season" value={c.current_season ? `S${c.current_season}` : 'none'} mono/>
        <Stat label="Live matches" value={num(c.live_matches || 0)}/>
        <Stat label="Completed days" value={num(c.complete_days || 0)}/>
        <Stat label="Receipts cached" value={num(c.receipts_cached || 0)}/>
        <Stat label="Source age" value={c.source_age_seconds != null ? `${num(c.source_age_seconds)}s` : '—'}/>
      </div>
      {errs.length ? (
        <div className="notice" style={{background:'#fff7e6', borderColor:'#f0c674'}}>
          <ShieldAlert size={17}/>
          <span>
            <b>Collector cannot reach {c.domain || 'virtuals.betika.com'}.</b>{' '}
            The worker is running but every request returns a network error (most recent: <code>{(errs[0].error || '').slice(0, 90)}</code>).{' '}
            Check your firewall, VPN, or corporate proxy. On a host with normal internet access this fills <code>{status.live_db}</code> automatically; until then the analyzer reads the bundled fixtures in <code>{status.fixtures_dir}</code>.
          </span>
        </div>
      ) : c.live_matches > 0 ? (
        <div className="notice">
          <Info size={17}/>
          <span>Live archive has <b>{c.live_matches}</b> matches and <b>{c.complete_days}</b> completed matchdays. The Betika Edge analyzer is reading it directly.</span>
        </div>
      ) : (
        <div className="notice">
          <Info size={17}/>
          <span>Collector just started — first fixtures / results / ongoing polls are in flight. Refresh this page in a minute to see fresh data.</span>
        </div>
      )}
      {activity.length ? (
        <details style={{marginTop:10}}>
          <summary className="fine-print">Last {activity.length} collector log entries</summary>
          <ul style={{fontSize:'0.85rem', marginTop:6}}>
            {activity.map((a,i)=>(
              <li key={a.id || i}>
                <code>{new Date(a.at * 1000).toLocaleTimeString('en-GB')}</code>{' '}
                <b>[{a.category}/{a.level}]</b> {a.message}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
    <div className="panel">
      <h3><RefreshCw size={18}/> Edge cache</h3>
      <p className="fine-print">
        Edge DB: <code>{status.edge_db}</code>.<br/>
        Bootstrapped at: <b>{status.bootstrapped_at ? new Date(status.bootstrapped_at * 1000).toLocaleTimeString('en-GB') : '—'}</b>.<br/>
        H2H rows: <b>{num(status.edge_h2h_rows || 0)}</b>, Markets cached: <b>{num(status.edge_markets_cached || 0)}</b>.
      </p>
      <div style={{display:'flex', gap:12, flexWrap:'wrap'}}>
        <button className="button secondary" onClick={refresh} disabled={busy}><RefreshCw size={15}/>{busy?'Refreshing…':'Refresh from archive + fixtures'}</button>
        <button className="button secondary" onClick={seed} disabled={busy}><PlayCircle size={15}/>Seed synthetic H2H (Bandarini vs Walinzi)</button>
      </div>
      {msg && <div className="notice"><Info size={17}/><span>{msg}</span></div>}
    </div>
  </div>;
}

function TeamsTab({teams}) {
  const profiles = (teams.data && teams.data.profiles) || [];
  if(teams.loading) return <Loading/>;
  if(!profiles.length) return <NoDataHint/>;
  return <div className="panel">
    <h3><Activity size={18}/> Teams</h3>
    <div className="archive-table-scroll">
      <table className="archive-table">
        <thead><tr><th>Team</th><th>Matches</th><th>Attack</th><th>Defence</th><th>Avg goals for</th><th>Avg goals against</th><th>Form L5</th><th>Form L10</th></tr></thead>
        <tbody>{profiles.sort((a,b)=>b.attack-a.attack).map(p=>(
          <tr key={p.team_id}>
            <td><div className="archive-teams"><span><TeamBadge name={p.name} small/>{p.name}</span></div></td>
            <td>{num(p.matches)}</td>
            <td>{Fixed(p.attack,3)}</td>
            <td>{Fixed(p.defence,3)}</td>
            <td>{Fixed(p.avg_goals_for)}</td>
            <td>{Fixed(p.avg_goals_against)}</td>
            <td>{Pct(p.form_last5,0)}</td>
            <td>{Pct(p.form_last10,0)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  </div>;
}

function H2HTab({teams}) {
  const teamList = (teams.data && teams.data.teams) || [];
  const [home,setHome] = useState(teamList[0]?.name || '');
  const [away,setAway] = useState(teamList[1]?.name || '');
  const [seedN,setSeedN] = useState(5);
  const [msg,setMsg] = useState('');
  const path = useMemo(()=> home && away ? `/api/betika-edge/h2h?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}` : null, [home,away]);
  const pair = useResource(path || '/api/betika-edge/h2h?home=&away=', [home,away]);
  async function seed(){
    try { const r = await postJson(BETIKA_PATHS.seedH2H, {home, away, n:seedN});
          setMsg(`Seeded ${r.added} synthetic H2H meetings (season=0).`); pair.refresh(); }
    catch(e){setMsg(e.message);}
  }
  if(!teamList.length) return <NoDataHint/>;
  const d = pair.data || {};
  return <div className="panel">
    <h3><Gauge size={18}/> Head-to-Head</h3>
    <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
      <label><span>Home</span><select value={home} onChange={e=>setHome(e.target.value)}>{teamList.map(t=><option key={t.id}>{t.name}</option>)}</select></label>
      <label><span>Away</span><select value={away} onChange={e=>setAway(e.target.value)}>{teamList.map(t=><option key={t.id}>{t.name}</option>)}</select></label>
    </div>
    {pair.loading ? <Loading/> :
     <div>
       <div className="betika-stat-row" style={{marginTop:10}}>
         <Stat label="Played" value={num(d.played||0)}/>
         <Stat label={`${home} wins`} value={num(d.home_wins||0)}/>
         <Stat label="Draws" value={num(d.draws||0)}/>
         <Stat label={`${away} wins`} value={num(d.away_wins||0)}/>
       </div>
       <div className="betika-stat-row">
         <Stat label="Avg FT (H)" value={Fixed(d.avg_ft_home)}/>
         <Stat label="Avg FT (A)" value={Fixed(d.avg_ft_away)}/>
         <Stat label="Avg FT total" value={Fixed(d.avg_ft_total)}/>
         <Stat label="BTTS YES" value={Pct(d.btts_yes,0)}/>
         <Stat label="Over 1.5" value={Pct(d.over_15,0)}/>
         <Stat label="Over 2.5" value={Pct(d.over_25,0)}/>
         <Stat label="Both > 0.5" value={Pct(d.both_gt_05,0)}/>
       </div>
       {d.meetings && d.meetings.length > 0 ? <div className="archive-table-scroll" style={{marginTop:10}}>
         <table className="archive-table">
           <thead><tr><th>Season</th><th>MD</th><th>HT</th><th>FT</th><th>FT total</th><th>Source</th></tr></thead>
           <tbody>{d.meetings.map((m,i)=>(
             <tr key={i}><td>{m.synthetic?'<b>0</b> (synthetic)':m.season}</td>
               <td>{m.matchday}</td>
               <td>{m.ht_home}:{m.ht_away}</td>
               <td><b>{m.ft_home}:{m.ft_away}</b></td>
               <td>{m.ft_total}</td>
               <td>{m.synthetic ? 'synthetic' : 'recorded'}</td>
             </tr>
           ))}</tbody>
         </table>
       </div> : <p className="fine-print">No recorded meetings yet.</p>}
     </div>}
    <details style={{marginTop:14}}><summary>Need synthetic H2H for this pair?</summary>
      <p className="fine-print">Injects <code>n</code> synthetic meetings labelled <code>season=0</code> so the two-market ledger rule can be exercised even when there's no real archive history.</p>
      <label><span>Number of synthesized meetings</span><input type="number" min={1} max={20} value={seedN} onChange={e=>setSeedN(parseInt(e.target.value||5,10))}/></label>
      <button className="button secondary" onClick={seed}>Seed synthetic H2H</button>
      {msg && <p className="fine-print">{msg}</p>}
    </details>
  </div>;
}

function GoalsTab() {
  const teams = useResource(BETIKA_PATHS.teams, []);
  const list = (teams.data && teams.data.profiles) || [];
  if(teams.loading) return <Loading/>;
  if(!list.length) return <NoDataHint/>;
  return <div className="panel">
    <h3><BarChart3 size={18}/> Goal Distribution & Poisson Fit</h3>
    <p className="fine-print">
      Attack and defence values come from a bivariate Poisson / Dixon-Coles style fit.  League average goals scale the home / away expectations.
    </p>
    <div className="archive-table-scroll">
      <table className="archive-table">
        <thead><tr><th>Team</th><th>Matches</th><th>Attack</th><th>Defence</th><th>0</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5+</th><th>Avg goals F</th><th>Avg goals A</th></tr></thead>
        <tbody>{list.sort((a,b)=>b.attack-a.attack).map(p=>(
          <tr key={p.team_id}>
            <td><div className="archive-teams"><span><TeamBadge name={p.name} small/>{p.name}</span></div></td>
            <td>{num(p.matches)}</td>
            <td>{Fixed(p.attack,3)}</td>
            <td>{Fixed(p.defence,3)}</td>
            <td>{Pct(p.distribution['0'],0)}</td>
            <td>{Pct(p.distribution['1'],0)}</td>
            <td>{Pct(p.distribution['2'],0)}</td>
            <td>{Pct(p.distribution['3'],0)}</td>
            <td>{Pct(p.distribution['4'],0)}</td>
            <td>{Pct(p.distribution['5+'],0)}</td>
            <td>{Fixed(p.avg_goals_for)}</td>
            <td>{Fixed(p.avg_goals_against)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  </div>;
}

function PredictTab({teams}) {
  const teamList = (teams.data && teams.data.teams) || [];
  const [home,setHome] = useState(''); const [away,setAway] = useState('');
  useEffect(()=>{ if(!home && teamList.length) setHome(teamList[0].name);
                  if(!away && teamList.length>1) setAway(teamList[1].name); }, [teamList.length]);
  const path = useMemo(()=> home && away ? `/api/betika-edge/predict?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}` : null, [home,away]);
  const r = useResource(path || '/api/betika-edge/predict?home=&away=', [home,away]);
  if(!teamList.length) return <NoDataHint/>;
  return <div className="panel">
    <h3><Brain size={18}/> Predictions</h3>
    <div style={{display:'flex', gap:8, flexWrap:'wrap'}}>
      <label><span>Home</span><select value={home} onChange={e=>setHome(e.target.value)}>{teamList.map(t=><option key={t.id}>{t.name}</option>)}</select></label>
      <label><span>Away</span><select value={away} onChange={e=>setAway(e.target.value)}>{teamList.map(t=><option key={t.id}>{t.name}</option>)}</select></label>
    </div>
    {r.loading ? <Loading/> : r.error ? <ErrorBanner error={r.error}/> : !r.data || r.data.error ? <p className="fine-print">No data for this pair.</p> :
    <div>
      <div className="betika-stat-row">
        <Stat label="P(Home)" value={Pct(r.data.p_home,1)}/>
        <Stat label="P(Draw)" value={Pct(r.data.p_draw,1)}/>
        <Stat label="P(Away)" value={Pct(r.data.p_away,1)}/>
        <Stat label="Over 0.5" value={Pct(r.data.p_over_05,1)}/>
        <Stat label="Over 1.5" value={Pct(r.data.p_over_15,1)}/>
        <Stat label="Over 2.5" value={Pct(r.data.p_over_25,1)}/>
        <Stat label="Over 3.5" value={Pct(r.data.p_over_35,1)}/>
        <Stat label="BTTS YES" value={Pct(r.data.p_btts_yes,1)}/>
      </div>
      <h4>Most likely exact scores</h4>
      <table className="archive-table"><thead><tr><th>Score</th><th>Probability</th></tr></thead>
        <tbody>{(r.data.most_likely||[]).map((k,i)=>(<tr key={i}><td><b>{k[0][0]}-{k[0][1]}</b></td><td>{Pct(k[1],1)}</td></tr>))}</tbody>
      </table>
      <h4>Expected totals</h4>
      <div className="betika-stat-row">
        <Stat label="λ home" value={Fixed(r.data.lambda_home)}/>
        <Stat label="λ away" value={Fixed(r.data.lambda_away)}/>
        <Stat label="Expected total" value={Fixed(r.data.expected_total)}/>
      </div>
      <p className="fine-print">Sample size used: {num(r.data.sample_size)} matches. With fewer than ~50 records the Poisson fit is dominated by outliers.</p>
    </div>}
  </div>;
}

function BiasTab({teams}) {
  const matches = useResource(BETIKA_PATHS.matches, []);
  const list = (matches.data && matches.data.matches) || [];
  const upcoming = list.filter(m=>m.ft_home == null);
  if(!list.length) return <NoDataHint/>;
  const [sel,setSel] = useState(0);
  const m = list[Math.min(sel, list.length-1)];
  const path = useMemo(()=> m ? `/api/betika-edge/no-vig?match_id=${encodeURIComponent(m.match_id)}` : null, [m]);
  const flags = useMemo(()=> m ? `/api/betika-edge/cross-flags?match_id=${encodeURIComponent(m.match_id)}&threshold=0.03` : null, [m]);
  const noVig = useResource(path, [m]);
  const flagsD = useResource(flags, [m]);
  return <div className="panel">
    <h3><ShieldAlert size={18}/> Market Bias / No-Vig</h3>
    <label><span>Fixture</span>
      <select onChange={e=>setSel(parseInt(e.target.value,10))}>
        {list.map((mm,i)=><option key={i} value={i}>{mm.home} vs {mm.away} · MD{mm.matchday}</option>)}
      </select>
    </label>
    {m && <>
      <div className="archive-teams" style={{margin:'8px 0'}}><span><TeamBadge name={m.home} small/>{m.home}</span><small>vs</small><span><TeamBadge name={m.away} small/>{m.away}</span></div>
      {noVig.loading ? <Loading/> :
        (()=>{
          const groups = (noVig.data && Object.entries(noVig.data)) || [];
          if(!groups.length) return <p className="fine-print">No markets cached for this fixture (cache via Data Collection).</p>;
          return <>{groups.map(([name, rows])=>(
            <details key={name} open={name==='1X2'}>
              <summary><b>{name}</b> · {rows.length} outcomes</summary>
              <table className="archive-table">
                <thead><tr><th>Outcome</th><th>Decimal odds</th><th>Raw</th><th>Overround</th><th>No-vig</th><th>Margin</th></tr></thead>
                <tbody>{rows.map((r,i)=>(<tr key={i}><td>{r.outcome}</td><td>{Fixed(r.decimal_odds)}</td>
                  <td>{Pct(r.raw_implied,2)}</td><td>{Fixed(r.overround,3)}</td><td>{Pct(r.no_vig,2)}</td><td>{Fixed(r.margin,4)}</td></tr>))}</tbody>
              </table>
            </details>
          ))}
          <h4 style={{marginTop:10}}>Cross-market consistency</h4>
          {flagsD.data && flagsD.data.flags && flagsD.data.flags.length ?
            flagsD.data.flags.map((f,i)=><div key={i} className="notice warning"><Info size={15}/><span><b>{f.kind}</b> — {f.detail}</span></div>) :
            <div className="notice"><Info size={15}/><span>No flags — all cross-market checks are within the threshold.</span></div>}
          </>;
        })()}
    </>}
  </div>;
}

function ValueTab({teams}) {
  const matches = useResource(BETIKA_PATHS.matches, []);
  const list = (matches.data && matches.data.matches) || [];
  if(!list.length) return <NoDataHint/>;
  const [sel,setSel] = useState(0);
  const m = list[Math.min(sel, list.length-1)];
  const tmPath = useMemo(()=> m ? `/api/betika-edge/two-market?match_id=${encodeURIComponent(m.match_id)}` : null, [m]);
  const vbPath = useMemo(()=> m ? `/api/betika-edge/value-bets?match_id=${encodeURIComponent(m.match_id)}` : null, [m]);
  const tm = useResource(tmPath, [m]);
  const vb = useResource(vbPath, [m]);
  return <div className="panel">
    <h3><Sparkles size={18}/> Value Bets</h3>
    <label><span>Fixture</span>
      <select onChange={e=>setSel(parseInt(e.target.value,10))}>
        {list.map((mm,i)=><option key={i} value={i}>{mm.home} vs {mm.away} · MD{mm.matchday}</option>)}
      </select>
    </label>
    {m && <div>
      <div className="archive-teams" style={{margin:'8px 0'}}><span><TeamBadge name={m.home} small/>{m.home}</span><small>vs</small><span><TeamBadge name={m.away} small/>{m.away}</span></div>
      <h4>Two-market view</h4>
      <div className="betika-stat-row">
        {tm.data && tm.data.market_a && <><Stat label="A: Away Exact = 1 — odds" value={Fixed(tm.data.market_a.decimal_odds)}/>
          <Stat label="A: no-vig" value={Pct(tm.data.market_a.no_vig,2)}/><Stat label="A: model fair" value={Pct(tm.data.market_a.fair,2)}/><Stat label="A: EV" value={Money(tm.data.market_a.ev*100)+'%'}/></>}
        {tm.data && tm.data.market_b && <><Stat label="B: Away Total Over 1.5 — odds" value={Fixed(tm.data.market_b.decimal_odds)}/>
          <Stat label="B: no-vig" value={Pct(tm.data.market_b.no_vig,2)}/><Stat label="B: model fair" value={Pct(tm.data.market_b.fair,2)}/><Stat label="B: EV" value={Money(tm.data.market_b.ev*100)+'%'}/></>}
      </div>
      <h4>All positive-EV bets</h4>
      {vb.loading ? <Loading/> :
        vb.data && vb.data.bets && vb.data.bets.length ?
        <table className="archive-table">
          <thead><tr><th>Market</th><th>Outcome</th><th>Odds</th><th>Fair</th><th>EV</th><th>Edge</th><th>Kelly stake</th></tr></thead>
          <tbody>{vb.data.bets.map((b,i)=>(<tr key={i}>
            <td>{b.market_name}</td><td>{b.outcome_label}</td>
            <td>{Fixed(b.decimal_odds)}</td><td>{Pct(b.no_vig_prob,2)}</td>
            <td>{Pct(b.expected_value,2)}</td><td>{Pct(b.edge,2)}</td><td>{Pct(b.kelly_stake_fraction,2)}</td>
          </tr>))}</tbody>
        </table> :
        <p className="fine-print">No positive-EV bets above the threshold.</p>}
    </div>}
  </div>;
}

function LedgerTab({ledger, onChange, settings}) {
  const matches = useResource(BETIKA_PATHS.matches, []);
  const list = (matches.data && matches.data.matches) || [];
  if(!list.length) return <NoDataHint/>;
  const [sel,setSel] = useState(0);
  const [stake,setStake] = useState(Number(settings?.stake) || 100);
  const [msg,setMsg] = useState('');
  const m = list[Math.min(sel, list.length-1)];
  const decisionPath = useMemo(()=> m ? `/api/betika-edge/two-market?match_id=${encodeURIComponent(m.match_id)}` : null, [m]);
  const decisionD = useResource(decisionPath, [m]);
  async function place(){
    try { const r = await postJson(BETIKA_PATHS.place, {match_id: m.match_id, stake});
          if(r.ok){ setMsg(`Bet #${r.bet_id} recorded.`); } else { setMsg(r.error || 'NO BET.'); }
          onChange();
        } catch(e){ setMsg(e.message); }
  }
  async function settle(){
    try { const r = await postJson(BETIKA_PATHS.settle, {});
          setMsg(`Settled ${r.settled} bets.`); onChange();
        } catch(e){ setMsg(e.message); }
  }
  return <div className="panel">
    <h3><Calculator size={18}/> Paper Ledger</h3>
    <p className="fine-print"><b>Strategy</b> — Person A: Away team EXACT GOALS = 1. Person B: Away team TOTAL goals OVER 1.5. Same stake each column.</p>
    <label><span>Fixture</span>
      <select onChange={e=>setSel(parseInt(e.target.value,10))}>
        {list.map((mm,i)=><option key={i} value={i}>{mm.home} vs {mm.away} · MD{mm.matchday}</option>)}
      </select>
    </label>
    <label><span>Stake per column</span><input type="number" min={1} step={10} value={stake} onChange={e=>setStake(parseFloat(e.target.value||0))}/></label>

    {m && decisionD.data && (
      <BetDecisionView m={m} decision={decisionD.data} stake={stake} onPlace={place} onSettle={settle} msg={msg}/>
    )}
    {msg && <div className="notice"><Info size={15}/><span>{msg}</span></div>}

    <h4>Ledger</h4>
    {ledger.data && ledger.data.rows.length ?
      <table className="archive-table">
        <thead><tr><th>#</th><th>Match</th><th>A odds</th><th>A result</th><th>B odds</th><th>B result</th><th>Stake A</th><th>Stake B</th><th>P/L</th></tr></thead>
        <tbody>{ledger.data.rows.map((r,i)=>(<tr key={i}>
          <td>{r.bet_id}</td><td>{r.match_id.slice(-32)}</td>
          <td>{Fixed(r.market_a_odds)}</td><td><Status tone={r.market_a_result==='WIN'?'green':r.market_a_result==='LOSS'?'red':'muted'}>{r.market_a_result||'—'}</Status></td>
          <td>{Fixed(r.market_b_odds)}</td><td><Status tone={r.market_b_result==='WIN'?'green':r.market_b_result==='LOSS'?'red':'muted'}>{r.market_b_result||'—'}</Status></td>
          <td>{Fixed(r.market_a_stake)}</td><td>{Fixed(r.market_b_stake)}</td>
          <td><PnL value={r.profit_or_loss}/></td>
        </tr>))}</tbody>
      </table> :
      <p className="fine-print">No bets yet.</p>
    }

    <h4>Summary</h4>
    {ledger.data && ledger.data.summary ? <div className="betika-stat-row">
      <Stat label="Bets" value={num(ledger.data.summary.n_bets)}/>
      <Stat label="Strike A" value={Pct(ledger.data.summary.strike_a,0)}/>
      <Stat label="Strike B" value={Pct(ledger.data.summary.strike_b,0)}/>
      <Stat label="ROI combined" value={Pct(ledger.data.summary.roi_total,2)}/>
      <Stat label="P/L A" value={Money(ledger.data.summary.pnl_a)}/>
      <Stat label="P/L B" value={Money(ledger.data.summary.pnl_b)}/>
      <Stat label="Total P/L" value={Money(ledger.data.summary.pnl_total)}/>
      <Stat label="Max drawdown" value={Money(ledger.data.summary.max_drawdown)}/>
      <Stat label="Win / Loss streak" value={`${ledger.data.summary.longest_win_streak} / ${ledger.data.summary.longest_loss_streak}`}/>
    </div> : null}
  </div>;
}

function BetDecisionView({m, decision, stake, onPlace, onSettle, msg}) {
  const a = decision.market_a, b = decision.market_b;
  const aOdds = a?.decimal_odds, bOdds = b?.decimal_odds;
  const ok = aOdds != null && bOdds != null && aOdds >= 2.00 && bOdds >= 2.00;
  return <>
    <div className="betika-stat-row" style={{marginTop:8}}>
      <Stat label="Market A odds" value={aOdds? Fixed(aOdds):'—'}/>
      <Stat label="Market B odds" value={bOdds? Fixed(bOdds):'—'}/>
      <Stat label="Both odds ≥ 2.00?" value={ok?'yes':'no'}/>
    </div>
    {ok ? <button className="button primary" onClick={onPlace}>Place paper bet (stake {stake})</button>
        : <p className="notice warning"><Info size={15}/>NO BET — one of the two required markets is missing or its odds are below 2.00.</p>}
    <p className="fine-print">Once the FT result is recorded for this fixture the ledger auto-settles. To force-settle every pending row now: <button className="text-button" onClick={onSettle}>Settle all pending</button>.</p>
    {msg && <div className="notice"><Info size={17}/><span>{msg}</span></div>}
  </>;
}

function MatchdayTab() {
  const matches = useResource(BETIKA_PATHS.matches, []);
  const seasons = Array.from(new Set(((matches.data && matches.data.matches) || []).map(m=>m.season).filter(Boolean))).sort((a,b)=>b-a);
  const [sel,setSel] = useState(0);
  if(!seasons.length) return <NoDataHint/>;
  const season = seasons[Math.min(sel, seasons.length-1)];
  const path = useMemo(()=> `/api/betika-edge/matchday?season=${season}`, [season]);
  const md = useResource(path, [season]);
  return <div className="panel">
    <h3><ListChecks size={18}/> Matchday Tracker</h3>
    <label><span>Season</span>
      <select onChange={e=>setSel(parseInt(e.target.value,10))}>
        {seasons.map((s,i)=><option key={i} value={i}>{s}</option>)}
      </select>
    </label>
    {md.loading ? <Loading/> : md.data && <>
      <p className="fine-print">Total matchdays: {num(md.data.summary.total_matchdays)} · Bets: {num(md.data.summary.with_bet)} · No-bet days: {num(md.data.summary.no_bet_days)} · Total P/L: {Money(md.data.summary.total_pnl)} · ROI: {Pct(md.data.summary.roi,2)}</p>
      <div className="archive-table-scroll">
        <table className="archive-table">
          <thead><tr><th>MD</th><th>Match</th><th>HT</th><th>FT</th><th>Both &gt; 0.5?</th><th>A odds</th><th>B odds</th><th>Rule</th><th>Bet</th><th>A result</th><th>B result</th><th>P/L</th></tr></thead>
          <tbody>{md.data.rows.map((r,i)=>(
            <tr key={i}>
              <td>{r.matchday}</td>
              <td>{r.match_label}</td>
              <td>{r.ht||'—'}</td><td>{r.ft||'—'}</td>
              <td>{r.both_gt_05||'—'}</td>
              <td>{r.market_a_odds?Fixed(r.market_a_odds):'—'}</td>
              <td>{r.market_b_odds?Fixed(r.market_b_odds):'—'}</td>
              <td>{r.rule_met?<Status tone="green">met</Status>:<Status tone="muted">no</Status>}</td>
              <td>{r.bet_placed?'✓':'·'}</td>
              <td>{r.market_a_result||'—'}</td>
              <td>{r.market_b_result||'—'}</td>
              <td>{r.pnl==null?'—':<PnL value={r.pnl}/>}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </>}
  </div>;
}

function SettingsTab() {
  const r = useResource(BETIKA_PATHS.settingsGet, []);
  const [draft,setDraft] = useState({});
  useEffect(()=>{ if(r.data) setDraft(r.data); }, [r.data]);
  function onChange(k,v){ setDraft(s=>({...s,[k]:v})); }
  return <>{r.loading ? <Loading/> : r.error ? <ErrorBanner error={r.error}/> : <SettingsEditor settings={draft} onChange={onChange}/>}</>;
}
