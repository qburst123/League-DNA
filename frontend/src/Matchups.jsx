import React,{useMemo,useState} from 'react';
import {GitCompareArrows,LayoutGrid,ListTree,RefreshCw,ShieldCheck,Clock3,Activity,Lock,Info,ArrowLeftRight,Loader2,Search,Check,Trophy} from 'lucide-react';
import {PageHead,Loading,ErrorBanner,Modal,Status,num} from './shared';
import {HALF_LABEL,HALF_SHORT,HALVES,TICK_SECONDS,boardColumns,timeOf,dateOf,sinceLabel,leadLabel,
        seasonOptions,teamList,useMatchups,usePair,usePairs} from './matchupModel';

const TONE = {hit:'hit', miss:'miss', pending:'pending', null:'none'};
const verdictTone = call => call?.verdict === 'hit' ? 'hit' : call?.verdict === 'miss' ? 'miss' : call?.frozen ? 'pending' : 'none';

function HalfBars({counts: raw, compact=false}){
  let counts = raw;
  if(typeof counts === 'string'){ try{ counts = JSON.parse(counts); }catch{ counts = null; } }
  const total = HALVES.reduce((sum, key) => sum + (counts?.[key] || 0), 0) || 0;
  if(!total) return <span className="mu-empty-note">no recorded meetings for this pair</span>;
  return <div className={`mu-bars ${compact?'compact':''}`} role="img" aria-label="how often each half out-scored the other">
    {HALVES.map(key => <span key={key} className={`mu-bar half-${key}`} style={{width:`${((counts[key] || 0) / total) * 100}%`}}
                         title={`${HALF_LABEL[key]}: ${counts[key]} of ${total}`}>
      {((counts[key] || 0) / total) > 0.14 ? `${Math.round(((counts[key] || 0) / total) * 100)}%` : ''}
    </span>)}
  </div>;
}

function HalfCard({call, result, expected}){
  const tone = verdictTone(call || {verdict: result?.half === expected ? 'hit' : 'miss', frozen: true});
  return <div className={`mu-half-card tone-${tone}`} data-verdict={tone}>
    <span className="mu-half-label">{call?.label || (expected ? HALF_SHORT[expected] : '—')}</span>
    {call?.probability != null && <span className="mu-half-prob">{Number(call.probability).toFixed(1)}%</span>}
    {result?.half && <span className="mu-half-actual">actual {HALF_SHORT[result.half]}</span>}
    {call?.verdict && <span className={`mu-half-verdict ${call.verdict}`}>{call.verdict === 'hit' ? 'RIGHT' : 'WRONG'}</span>}
    {!call?.frozen && <span className="mu-half-verdict pending">TO PLAY</span>}
  </div>;
}

function FixtureCard({fixture, parent, onOpen}){
  const played = Boolean(fixture.verdict);
  const lockedAt = fixture.locked_at || parent?.locked_at, lead = fixture.lead_seconds ?? parent?.lead_seconds;
  return <article className={`mu-fixture ${played ? fixture.verdict : 'pending'}`} data-verdict={played ? fixture.verdict : 'pending'}
      data-pick={fixture.pick || ''} data-probability={fixture.probability ?? ''} onClick={() => onOpen(fixture)}>
    <header><strong>{fixture.home}</strong><span className="mu-vs">vs</span><strong>{fixture.away}</strong></header>
    <div className="mu-fixture-record">
      {fixture.verdict ? <>
        <div className="mu-htft">
          <span>HT <b>{fixture.ht || '—'}</b></span><span>FT <b>{fixture.ft || '—'}</b></span>
        </div>
        <HalfCard call={null} result={{half: fixture.actual}} expected={fixture.pick} />
      </> : <div className="mu-fixture-pending">
        <span className="mu-pending-note">{fixture.frozen ? `locked ${timeOf(lockedAt)} EAT` : 'awaiting the lock'}
          {lead != null ? ` · ${leadLabel(lead)}` : ''}</span>
        <HalfCard call={fixture} />
      </div>}
    </div>
    <footer>
      <span className="mu-sample">{fixture.sample} meeting{fixture.sample === 1 ? '' : 's'}
        {fixture.basis === 'either' ? ' (either venue)' : ' of this fixture'}</span>
      <HalfBars counts={fixture.counts} compact/>
    </footer>
  </article>;
}

function CellModal({cell, team, onClose}){
  if(!cell) return null;
  const call = cell;
  return <Modal title={`MD${call.day} · ${team} ${call.venue === 'A' ? 'at' : 'vs'} ${call.opponent}`} onClose={onClose}>
    <div className="mu-modal">
      <div className="mu-modal-grid">
        <div><small>PREDICTED</small><strong className={`mu-half-${call.pick}`}>{call.label || '—'}</strong>
          {call.probability != null && <span>{Number(call.probability).toFixed(2)}% of {call.sample} meetings</span>}
          <span className="mu-muted">basis: {call.basis === 'either' ? 'both venues (thin exact-fixture sample)' : 'this exact fixture'}</span>
        </div>
        <div><small>PLAYED</small>
          {call.ft ? <><strong>HT {call.ht} · FT {call.ft}</strong>
            <span>1st half {call.first_half} · 2nd half {call.second_half} goals</span>
            <span className={`mu-verdict-chip ${call.verdict}`}>{call.verdict === 'hit' ? 'RIGHT' : 'WRONG'} — actual {call.actual_label || '—'}</span></>
            : <strong className="mu-muted">not played yet</strong>}
        </div>
        <div><small>FROZEN</small>
          <strong>{call.frozen ? `locked ${dateOf(call.locked_at)} EAT` : 'not locked yet'}</strong>
          {call.source && <span>{call.source === 'live' ? 'locked live before kick-off' : 'backfilled from meetings recorded before this season'}</span>}
        </div>
      </div>
      {call.history?.length > 0 && <div className="mu-history">
        <h4>How this pair has finished, most recent first</h4>
        <table className="pg-table"><thead><tr><th>Season</th><th>MD</th><th>HT</th><th>FT</th><th>1st half</th><th>2nd half</th><th>Highest</th></tr></thead>
          <tbody>{call.history.slice(0, 12).map((row, index) => <tr key={`${row.season}-${row.day}-${index}`}>
            <td>{row.season}</td><td>{row.day}</td><td>{row.ht}</td><td>{row.ft}</td>
            <td>{row.first_half}</td><td>{row.second_half}</td><td><span className={`mu-chip half-${row.half}`}>{HALF_SHORT[row.half]}</span></td>
          </tr>)}</tbody></table>
      </div>}
    </div>
  </Modal>;
}

function Historical({league}){
  const {pairs, error: pairError} = usePairs('');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(null);
  const [venue, setVenue] = useState('ordered');
  const [open, setOpen] = useState(null);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if(!needle) return pairs;
    return pairs.filter(pair => pair.label.toLowerCase().includes(needle));
  }, [pairs, query]);
  const chosen = selected || pairs[0] || null;
  const {analysis, error, loading} = usePair(chosen?.home, chosen?.away, venue);
  const modalHalf = analysis?.prediction?.pick;
  const right = analysis ? (analysis.rows || []).filter(row => row.half === modalHalf).length : 0;
  return <div className="mu-historical">
    <div className="panel mu-picker">
      <div className="mu-picker-controls">
        <label className="mu-search"><Search size={15}/><input value={query} onChange={event => setQuery(event.target.value)}
          placeholder="Filter the pair list…" aria-label="Filter team pairs"/></label>
        <label><span>TEAM PAIR</span>
          <select aria-label="Choose a team pair" value={chosen ? `${chosen.home}|${chosen.away}` : ''}
                  onChange={event => {const [home, away] = event.target.value.split('|'); setSelected({home, away});}}>
            {filtered.map(pair => <option key={pair.label} value={`${pair.home}|${pair.away}`}>
              {pair.label} · {pair.meetings} meetings</option>)}
          </select></label>
        <label><span>VENUE</span>
          <select aria-label="Venue scope" value={venue} onChange={event => setVenue(event.target.value)}>
            <option value="ordered">This exact fixture (home vs away)</option>
            <option value="either">Either venue (both directions)</option>
          </select></label>
      </div>
      <ErrorBanner error={error || pairError}/>
      {!chosen && <p className="mu-muted">No pair matches that filter.</p>}
    </div>
    {analysis && <>
      <div className="mu-summary">
        <article className="panel"><small>MEETINGS ON RECORD</small><strong>{num(analysis.rows.length)}</strong>
          <span>{analysis.rows.length ? `${Math.min(...analysis.rows.map(r => r.season))} – ${Math.max(...analysis.rows.map(r => r.season))}` : '—'}</span></article>
        <article className="panel"><small>LEAGUE-WIDE BASE RATE</small>
          <div className="mu-rate-row">{HALVES.map(key => <span key={key} className={`mu-chip half-${key}`}>
            {HALF_SHORT[key]} {league?.rates?.[key] ?? '—'}%</span>)}</div>
          <span>{num(league?.matches)} recorded matches</span></article>
        <article className="panel"><small>THIS PAIR</small>
          <div className="mu-rate-row">{HALVES.map(key => <span key={key} className={`mu-chip half-${key}`}>
            {HALF_SHORT[key]} {analysis.pair_rates?.[key] ?? '—'}%</span>)}</div>
          <span>mean goals: 1st {analysis.league?.mean_first_half_goals} · 2nd {analysis.league?.mean_second_half_goals}</span></article>
        <article className="panel"><small>THE CALL THIS HISTORY MAKES</small>
          {analysis.prediction ? <>
            <strong className={`mu-half-${analysis.prediction.pick}`}>{analysis.prediction.label}</strong>
            <span>{analysis.prediction.probability}% · {analysis.prediction.confidence} sample ({analysis.prediction.sample})</span>
            <span className="mu-muted">right in {right} of {analysis.rows.length} recorded meetings</span>
          </> : <strong className="mu-muted">no meetings recorded</strong>}
        </article>
      </div>
      {analysis.note && <div className="notice"><Info size={16}/><span>{analysis.note}</span></div>}
      <HalfBars counts={analysis.counts}/>
      <div className="panel"><div className="panel-head"><div><span className="icon-title"><ListTree size={18}/><h2>Every recorded meeting</h2></span>
        <p>{analysis.home} vs {analysis.away} · {analysis.basis === 'either' ? 'both venues' : 'this exact fixture'} · newest first</p></div>
        <span className="mu-muted">{loading ? <Loader2 size={14} className="spin"/> : `${analysis.rows.length} rows`}</span></div>
        <div className="archive-table-scroll"><table className="archive-table mu-history-table">
          <thead><tr><th>SEASON</th><th>MD</th><th className="teams-col">MATCH</th><th>HT</th><th>FT</th><th>1ST HALF</th><th>2ND HALF</th><th>HIGHEST SCORING HALF</th><th>CALL</th></tr></thead>
          <tbody>{analysis.rows.map(row => <tr key={`${row.season}-${row.day}-${row.home}`}>
            <td><b>{row.season}</b></td>
            <td><span className="matchday-index">{String(row.day).padStart(2, '0')}</span></td>
            <td className="teams-col"><span className="mu-teams"><b>{row.home}</b> v <b>{row.away}</b></span></td>
            <td className="score-cell">{row.ht_home}:{row.ht_away}</td>
            <td className="score-cell ft">{row.ft_home}:{row.ft_away}</td>
            <td>{row.first_half}</td><td>{row.second_half}</td>
            <td><span className={`mu-chip half-${row.half}`}>{HALF_SHORT[row.half]}</span></td>
            <td data-pair-verdict={modalHalf ? (row.half === modalHalf ? 'right' : 'wrong') : 'none'}>{modalHalf ? (row.half === modalHalf
              ? <span className="mu-verdict-chip hit"><Check size={12}/>right</span>
              : <span className="mu-verdict-chip miss">wrong</span>) : '—'}</td>
          </tr>)}</tbody></table></div>
      </div>
    </>}
    {open && <Modal title={`${open.home} vs ${open.away}`} onClose={() => setOpen(null)}>
      <p className="mu-muted">Fixture history pulled from the archive on this date.</p></Modal>}
  </div>;
}

export default function Matchups(){
  const [tab, setTab] = useState('board');
  const [season, setSeason] = useState(null);
  const {payload, tick, error, loading, received} = useMatchups(season);
  const [cell, setCell] = useState(null);
  const [cellTeam, setCellTeam] = useState(null);

  const board = payload?.board;
  const upcoming = payload?.upcoming;
  const ledger = payload?.ledger || {};
  const chips = payload?.meta ? {
    target: upcoming?.label, starts_in: upcoming?.starts_in, locked_at: upcoming?.locked_at,
    lead_seconds: upcoming?.lead_seconds, source: upcoming?.source, frozen: upcoming?.locked,
  } : null;
  const teams = board ? teamList(board) : [];
  const summary = board?.summary || {};
  const seasons = payload ? seasonOptions(payload) : [];

  if(loading && !payload) return <Loading text="Reading the half-time ledger…"/>;
  return <>
    <PageHead eyebrow="HEAD-TO-HEAD MATCHUP DNA" title="Which half decides this fixture?"
              description="Every meeting of a team pair, the half that out-scored the other, and the frozen call the history made for the matchday to play. A prediction is written once, when the matchday is announced, and never rewritten.">
      <button className="button secondary" onClick={() => window.location.reload()}><RefreshCw size={15}/>Reload</button>
    </PageHead>
    <ErrorBanner error={error}/>
    <div className="pg-chips" role="status">
      <span className="chip">matchday to play <b>{upcoming?.label || '—'}</b> · {upcoming?.fixtures?.length || 0} fixtures</span>
      <span className="chip"><Clock3 size={11}/>{chips?.starts_in != null
        ? (chips.starts_in > 0 ? `${Math.floor(chips.starts_in / 60)}m ${Math.round(chips.starts_in % 60)}s to kick-off`
                               : 'kick-off reached — waiting for HT/FT')
        : 'kick-off pending'}</span>
      <span className="chip">season <b>{payload?.season || '—'}</b> · {num(summary.matchdays)} matchdays played · {num(summary.matches)} matches</span>
      <span className="chip" data-lock={chips?.frozen ? (chips.source || 'live') : 'none'}><Lock size={11}/>{chips?.frozen
        ? <>card frozen {timeOf(chips.locked_at)} EAT{chips.lead_seconds != null ? ` · ${leadLabel(chips.lead_seconds)}` : ''}</>
        : 'waiting for the next card to be locked'}</span>
      <span className="chip" data-sync={tick?.sync_error ? 'error' : 'ok'}><Activity size={11}/>ledger checked
        <b> {sinceLabel(tick?.sync?.at || payload?.ledger?.sync?.at, received)}</b>
        {tick?.sync?.seconds != null ? ` in ${tick.sync.seconds}s` : ''}</span>
      <span className="chip"><GitCompareArrows size={11}/><b>{num(payload?.index?.pairs)}</b> fixtures on record · <b>{num(payload?.index?.matches)}</b> matches</span>
      <span className="chip"><ShieldCheck size={11}/><b>{summary.hits ?? 0}</b> right · <b>{num(summary.graded)}</b> graded
        {summary.hit_rate != null ? ` · ${summary.hit_rate}%` : ''}</span>
      {seasons.length > 1 && <label className="chip chip-select">season
        <select value={payload?.season || ''} onChange={event => setSeason(Number(event.target.value))}>
          {seasons.map(value => <option key={value} value={value}>{value}</option>)}</select></label>}
      <button className="chip chip-button" onClick={() => window.location.reload()}><RefreshCw size={11}/>refresh now</button>
    </div>
    <div className="pg-view-switch" role="tablist">
      <button className={tab === 'board' ? 'active' : ''} onClick={() => setTab('board')}><LayoutGrid size={15}/>Live board</button>
      <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}><ListTree size={15}/>Historical analysis</button>
    </div>

    {tab === 'history' ? <Historical league={board?.league}/> : <>
      <section className="panel mu-upcoming">
        <div className="panel-head"><div><span className="icon-title"><Trophy size={18}/><h2>Matchday {upcoming?.day} — the card underneath it</h2></span>
          <p>HT/FT on top once it is played, and directly below it the half the history called.{upcoming?.late ? ' This sheet was locked after kick-off.' : ''}</p></div>
          <Status tone={chips?.frozen ? 'green' : 'amber'}>{chips?.frozen ? 'CARD FROZEN' : 'AWAITING LOCK'}</Status></div>
        <div className="mu-fixtures">{(upcoming?.fixtures || []).map(fixture =>
          <FixtureCard key={`${fixture.home}-${fixture.away}`} fixture={fixture} parent={upcoming} onOpen={() => {setCell(fixture); setCellTeam(fixture.home);}}/>)}</div>
      </section>

      <section className="panel mu-board-panel">
        <div className="panel-head"><div><span className="icon-title"><LayoutGrid size={18}/><h2>Season {payload?.season} · MD1–MD30</h2></span>
          <p>Top row of each pair: the recorded HT and FT. The row under it: the frozen half call, green when it was right, red when it was wrong, purple while it is still to play.</p></div>
          <div className="mu-legend">
            <span><i className="mu-key hit"/>right</span><span><i className="mu-key miss"/>wrong</span>
            <span><i className="mu-key pending"/>to play</span><span><i className="mu-key empty"/>no card</span>
          </div></div>
        <div className="mu-board-scroll">
          <table className="mu-board">
            <thead><tr><th className="mu-team-col">TEAM</th>
              {Array.from({length: 30}, (_, index) => <th key={index}>{String(index + 1).padStart(2, '0')}</th>)}</tr></thead>
            <tbody>{teams.map(team => {
              const columns = boardColumns(board, team);
              return <React.Fragment key={team}>
                <tr className="mu-result-row"><th className="mu-team-col">{team}</th>
                  {columns.map(column => <td key={column.day} className={`mu-record ${column.played ? '' : 'blank'}`}
                    data-day={column.day} data-team={team} data-ht={column.played ? column.ht : ''} data-ft={column.played ? column.ft : ''}
                    data-half={column.played ? (column.actual || '') : ''}>
                    {column.played ? <><span className="mu-record-ht">HT {column.ht}</span><span className="mu-record-ft">{column.ft}</span></> : '—'}
                  </td>)}</tr>
                <tr className="mu-call-row"><th className="mu-team-col"><small>half call</small></th>
                  {columns.map(column => <td key={column.day} className={`mu-call ${column.pick ? (column.verdict === 'hit' ? 'hit' : column.verdict === 'miss' ? 'miss' : 'pending') : 'blank'}`}
                    data-day={column.day} data-team={team} data-pick={column.pick || ''}
                    data-verdict={column.pick ? (column.verdict || 'pending') : 'none'}
                    data-actual={column.actual || ''} data-probability={column.probability ?? ''}
                    onClick={() => {if(column.pick || column.played){setCell(column); setCellTeam(team);}}}
                    title={column.pick ? `${HALF_SHORT[column.pick]} · ${column.probability}% of ${column.sample} meetings` : 'no card'}>
                    {column.pick ? <><b>{column.pick}</b>{column.verdict === 'miss' && <small>actual {column.actual ? HALF_SHORT[column.actual] : '—'}</small>}
                      {column.verdict === 'pending' && <small>to play</small>}</> : '—'}
                  </td>)}</tr>
              </React.Fragment>;
            })}</tbody></table>
        </div>
        <footer className="mu-board-foot"><ShieldCheck size={13}/>
          {num(summary.picks)} calls locked · {num(summary.graded)} graded · {num(summary.hits)} right
          {summary.hit_rate != null ? ` (${summary.hit_rate}%)` : ''} · last checked {timeOf(received)} EAT.
          A three-way call cannot beat chance by much: the league's own base rate is shown beside every prediction.
        </footer>
      </section>
    </>}
    {cell && <CellModal cell={cell} team={cellTeam} onClose={() => setCell(null)}/>}
  </>;
}
