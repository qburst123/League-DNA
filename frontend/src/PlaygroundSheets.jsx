// Season sheets — the frozen ledger, browsable.
//
// Every matchday this workspace has predicted is stored as a sheet: the sixteen picks, the
// probability behind each one, the engine votes and the recorded result it was graded against. The
// sheet is written when the matchday is announced and never rewritten, so this view is a record of
// what the playground said at the time, not a back-fit to what happened.
import React,{useEffect,useMemo,useState} from 'react';
import {BookOpen,CalendarDays,Info,ShieldCheck,Lock,LockOpen,Target,Trophy,Users} from 'lucide-react';
import {num} from './shared';
import {pct,timeOf} from './playgroundModel';

const VERDICT_TONE = {hit:'exact', direction:'right', miss:'wrong', pending:'waiting'};
const VERDICT_TEXT = {hit:'EXACT', direction:'RIGHT RESULT', miss:'WRONG', pending:'TO PLAY'};

const toneOf = row => !row || !row.actual ? 'pending' : row.verdict === 'hit' ? 'hit'
  : row.verdict === 'direction' ? 'direction' : 'miss';

function useSeasonSheets(season, boardKey){
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    if(!['http:', 'https:'].includes(location.protocol)){setLoading(false); return;}
    let alive = true;
    const url = season ? `/api/playground/history?season=${season}` : '/api/playground/history';
    setLoading(true);
    fetch(url, {cache:'no-store'})
      .then(response => response.ok ? response.json() : Promise.reject(Error(`history ${response.status}`)))
      .then(body => {if(alive){setData(body); setError('');}})
      .catch(error => {if(alive) setError(error.message);})
      .finally(() => {if(alive) setLoading(false);});
    return () => {alive = false;};
  }, [season, boardKey]);
  return {data, error, loading};
}

function SeasonPicker({seasons, season, onPick}){
  return <div className="pg-fixture-picker pg-seasons">
    {(seasons || []).map(entry => <button key={entry.season} className={`pg-chip-button ${entry.season === season ? 'active' : ''}`}
      onClick={() => onPick(entry.season)} title={`Season ${entry.season} · ${entry.days} matchdays · ${entry.hits} exact`}>
      <CalendarDays size={12}/>{entry.season}
      <i>{entry.live ? `${entry.live} live` : 'backfilled'} · {entry.hits} exact / {entry.graded || 0}</i>
    </button>)}
    {!(seasons || []).length && <span className="note">No sheets have been locked yet — the ledger fills as the source publishes matchdays.</span>}
  </div>;
}

function DayStrip({days, day, onPick}){
  return <div className="pg-days pg-sheet-days">
    {(days || []).map(entry => {
      const graded = entry.graded || 0, total = entry.fixtures || 0;
      return <button key={entry.day} className={`pg-day-button ${entry.day === day ? 'active' : ''}`} onClick={() => onPick(entry.day)}
        title={`MD${entry.day} · ${graded}/${total} graded · ${entry.hits} exact · ${entry.source}`}>
        <span>MD{entry.day}</span>
        <b>{entry.hits}</b>
        <i>{entry.source === 'live' ? (entry.late ? 'late lock' : 'locked early') : 'backfilled'}</i>
        <span className="pg-day-track"><span className="pg-day-fill" style={{width:`${total ? (100 * graded / total).toFixed(0) : 0}%`}}/></span>
      </button>;
    })}
  </div>;
}

function SheetTable({sheet, scope, onOpen}){
  const picks = sheet?.picks || [];
  const pickOf = row => scope === 'blend' ? row.pick_logloss : row.pick;
  const probabilityOf = row => scope === 'blend' ? row.probability_logloss : row.probability;
  if(!picks.length) return <div className="notice warning"><Info size={17}/><span>This matchday has no sheet in the ledger.</span></div>;
  return <div className="pg-table-scroll"><table className="pg-table pg-sheet-table">
    <thead><tr><th>Team (own frame)</th><th>opponent</th><th className="num">locked pick</th><th className="num">probability</th>
      <th className="num">engine votes</th><th className="num">recorded FT</th><th>verdict</th><th>top three that day</th></tr></thead>
    <tbody>{picks.slice().sort((a, b) => (a.team || '').localeCompare(b.team || '')).map(row => {
      const tone = toneOf(row);
      return <tr key={row.team} className={`pg-sheet-row ${tone}`} onClick={() => onOpen(row)} title="Open the frozen evidence for this pick">
        <td><b>{row.team}</b> <small>{row.venue === 'A' ? 'away' : 'home'}</small></td>
        <td>{row.opponent}</td>
        <td className="num"><b data-pick={pickOf(row)}>{pickOf(row) || '—'}</b></td>
        <td className="num">{pct(probabilityOf(row))}</td>
        <td className="num">{row.engines_n ? `${row.agrees ?? '—'}/${row.engines_n}` : Object.keys(row.engines || {}).length ? `${Object.values(row.engines || {}).filter(v => v.score === pickOf(row)).length}/${Object.keys(row.engines).length}` : '—'}</td>
        <td className="num">{row.actual || '—'}</td>
        <td><span className={`pg-verdict ${tone}`}>{VERDICT_TEXT[tone]}</span></td>
        <td>{(row.top3 || []).join(' · ') || '—'}</td></tr>;
    })}</tbody></table></div>;
}

function TeamTable({teams, scope, onPick}){
  const rows = useMemo(() => (teams || []).slice().sort((a, b) => (b.hits || 0) - (a.hits || 0) || (b.hit_rate || 0) - (a.hit_rate || 0)), [teams]);
  return <div className="pg-table-scroll"><table className="pg-table">
    <thead><tr><th>Team</th><th className="num">picks</th><th className="num">graded</th><th className="num">exact</th><th className="num">right result</th>
      <th className="num">wrong</th><th className="num">exact rate</th><th className="num">mean probability</th></tr></thead>
    <tbody>{rows.map(row => <tr key={row.team} className="pg-sheet-row" onClick={() => onPick(row.team)} title={`Show ${row.team}'s sheets`}>
      <td><b>{row.team}</b></td><td className="num">{num(row.picks)}</td><td className="num">{num(row.graded)}</td>
      <td className="num">{num(row.hits)}</td><td className="num">{num(row.direction)}</td><td className="num">{num(row.misses)}</td>
      <td className="num">{pct(row.hit_rate)}</td><td className="num">{pct(row.mean_probability)}</td></tr>)}</tbody></table></div>;
}

export default function PlaygroundSheets({boardKey, scope}){
  const [season, setSeason] = useState(null);
  const {data, error, loading} = useSeasonSheets(season, boardKey);
  const [day, setDay] = useState(null);
  const seasons = data?.seasons || [];
  const days = data?.days || [];
  const chosen = season ?? data?.season ?? null;
  const chosenDay = day != null && days.some(entry => entry.day === day) ? day : (days.length ? days[days.length - 1].day : null);
  const sheet = useMemo(() => {
    if(chosenDay == null || !data?.rows) return null;
    const picks = (data.rows || []).filter(row => row.day === chosenDay);
    const entry = days.find(item => item.day === chosenDay) || {};
    return {...entry, picks};
  }, [data, chosenDay, days]);
  const integrity = data?.integrity || {};
  const stats = data?.stats || {};

  return <section className="panel pg-sheets">
    <header className="pg-head"><div>
      <span className="replay-kicker">FROZEN PREDICTION SHEETS</span>
      <h2>Browse a season, a matchday and a team</h2>
      <p>Every matchday the playground has predicted is stored as a sheet: the sixteen picks, the probability behind each one and the engine votes that produced it. The sheet is written when the matchday is announced and is never rewritten afterwards — a result only adds the verdict to the row that already existed.</p></div>
      <BookOpen size={18}/></header>

    <div className="pg-sheet-facts">
      <span><Lock size={13}/><b>{num(stats.picks)}</b> picks in the ledger</span>
      <span><Target size={13}/><b>{num(stats.graded)}</b> graded · <b>{pct(stats.hit_rate)}</b> exact</span>
      <span><ShieldCheck size={13}/><b>{integrity.live ?? 0}</b> sheets locked live, <b>{integrity.backfilled ?? 0}</b> backfilled</span>
      <span><LockOpen size={13}/>{integrity.on_time_share == null ? 'no live lock yet' : `${pct(integrity.on_time_share)} of them locked before the first kick-off`}</span>
      <span><Users size={13}/><b>{num((data?.teams || []).length)}</b> teams in this season</span>
      {loading && <span className="note">reading the ledger…</span>}
      {error && <span className="chip err">history: {error}</span>}
    </div>

    <SeasonPicker seasons={seasons} season={chosen} onPick={value => {setSeason(value); setDay(null);}}/>
    <DayStrip days={days} day={chosenDay} onPick={setDay}/>

    {sheet && <div className="pg-sheet-head">
      <div>
        <h3>Matchday {sheet.day} · {sheet.fixtures} fixtures</h3>
        <p className="note">{sheet.source === 'live'
          ? <>Locked <b>{timeOf(sheet.locked_at)}</b> EAT{sheet.lead_seconds != null ? ` — ${Math.abs(Math.round(sheet.lead_seconds)) >= 60 ? `${Math.floor(Math.abs(sheet.lead_seconds) / 60)}m ` : ''}${Math.abs(Math.round(sheet.lead_seconds)) % 60}s ${sheet.lead_seconds >= 0 ? 'before' : 'after'} the first kick-off` : ''}{sheet.late ? ' · late lock' : ''}.</>
          : <>Backfilled once from the walk-forward measurement, then frozen like every other sheet — this matchday was played before the ledger existed.</>}</p>
      </div>
      <div className="pg-facts">
        <span><b>{sheet.graded ?? 0}</b> graded</span><span><b>{sheet.hits ?? 0}</b> exact</span>
        <span><b>{sheet.direction ?? 0}</b> right result</span><span><b>{sheet.misses ?? 0}</b> wrong</span>
        <span>{scope === 'blend' ? 'log-loss mixture' : 'competition mixture'}</span>
      </div>
    </div>}

    <SheetTable sheet={sheet} scope={scope} onOpen={row => setSeason(chosen) /* opening a pick keeps the sheet on screen */}/>

    <div className="pg-two">
      <section className="panel">
        <header className="pg-head"><div><span className="replay-kicker">SEASON BY TEAM</span><h3>How the picks scored, team by team</h3></div><Trophy size={16}/></header>
        <TeamTable teams={data?.teams || []} scope={scope} onPick={team => {setDay(null);}}/>
      </section>
      <section className="panel">
        <header className="pg-head"><div><span className="replay-kicker">THE FREEZE, EXPLAINED</span><h3>Why this page does not move</h3></div></header>
        <div className="pg-prose-body">
          <div><Lock size={16}/><h3>Written once</h3><p>A sheet is stored the moment the source announces the matchday. Nothing later — not a refit, not a new season, not a restart — rewrites the pick, the probability or the engine votes in it.</p></div>
          <div><Target size={16}/><h3>Graded, not regraded</h3><p>When the recorded full-time score arrives it is written next to the existing pick and the verdict is computed from the two. The prediction stays exactly as it was locked.</p></div>
          <div><ShieldCheck size={16}/><h3>Honest about backfills</h3><p>Matchdays played before this ledger existed were copied in once from the walk-forward measurement and are labelled <b>backfilled</b>. Only sheets marked <b>live</b> were locked while the round was still to play, and the share locked before kick-off is shown above rather than claimed.</p></div>
        </div>
      </section>
    </div>
    <footer className="pg-foot"><Info size={13}/>Sheets are stored in <b>data/predictions.sqlite</b> and served by <b>/api/playground/history</b>; the same ledger paints the live board, which is why a played matchday can no longer change its prediction.</footer>
  </section>;
}
