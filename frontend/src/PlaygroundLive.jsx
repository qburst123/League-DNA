import React,{useMemo,useState} from 'react';
import {TrendingUp,Award,LayoutGrid,Info,RefreshCw,ShieldCheck,Clock3,Database,Scale,Compass,Radio,Loader2,Lock,BookOpen,ListTree,Activity} from 'lucide-react';
import {PageHead,Loading,ErrorBanner,Modal,Status,TeamBadge,num} from './shared';
import PlaygroundSheets from './PlaygroundSheets';
import {BOARD_DAYS,boardColumns,scopeOptions,targetDays,verdictOf,VERDICT_LABEL,VERDICT_SHORT,pct,usePlayground,engineLabel,weightList,timeOf,POLL_SECONDS,TICK_SECONDS,lockInfo,leadLabel,sinceLabel} from './playgroundModel';

function Countdown({kickoff,now}){
  const [,tick] = useState(0);
  React.useEffect(()=>{const t = setInterval(()=>tick(n => n + 1), 1000); return () => clearInterval(t);}, []);
  if(!kickoff) return <span>first kick-off pending</span>;
  const seconds = Math.round(kickoff - (now || Date.now()/1000));
  if(seconds <= 0) return <span>kick-off reached — waiting for the source's FT scores</span>;
  const minutes = Math.floor(seconds/60);
  return <span>{minutes > 0 ? `${minutes}m ` : ''}{seconds%60}s to the incoming matchday</span>;
}

function StatusChips({payload,status,tick,integrity,building,waiting,keyMoved,source,error,received,onRefresh,refreshing}){
  const meta = payload.meta || {}, data = payload.data || {}, target = payload.target || {};
  const days = targetDays(payload);
  const live = payload.live_board || {};
  const service = status?.status || {};
  const frozen = lockInfo(payload);
  // The ledger-check chip must not depend on the beat having landed: a page that was just reloaded
  // has the payload (and therefore the last synced facts) before its first tick, and a missed beat
  // is explicitly not an error. Prefer the live tick, fall back to what the payload already knows.
  const sync = tick?.sync
    ? {...tick.sync, graded: tick.graded?.picks ?? tick.sync.graded}
    : (payload.ledger?.sync?.at != null ? payload.ledger.sync : null);
  return <div className="pg-chips" role="status">
    <span className="chip">matchday set to play <b>{days.length ? days.map(day => `MD${day}`).join(' · ') : '—'}</b> · {target.fixtures || 0} fixtures</span>
    <span className="chip"><Clock3 size={11}/><Countdown kickoff={target.kickoff} now={received}/></span>
    <span className="chip">season <b>{live.season || '—'}</b> · {num(data.matches)} recorded results · {num(data.seasons)} seasons</span>
    <span className="chip">board updated <b>{timeOf(meta.generated_at)}</b> EAT · build <b>{meta.build_seconds != null ? `${meta.build_seconds}s` : '—'}</b></span>
    <span className="chip">predicted ledger <b>{num(live.summary?.picks)}</b> picks · <b>{num(live.summary?.hits)}</b> exact · {pct(live.summary?.hit_rate)}</span>
    <span className="chip" data-lock={frozen.source || 'none'}><Lock size={11}/>{frozen.source === 'live'
      ? <>frozen sheet locked <b>{timeOf(frozen.lockedAt)}</b> EAT{days.length === 0 ? ' — season complete' : ` · ${leadLabel(frozen.leadSeconds)}`}</>
      : frozen.source === 'backfill' ? 'this matchday was backfilled once, then frozen'
      : 'waiting for the next sheet to be locked'}</span>
    {sync && <span className="chip" data-sync={sync.error ? 'error' : 'ok'}><Activity size={11}/>ledger checked
      <b> {sinceLabel(sync.at, received)}</b>{sync.seconds != null ? ` in ${sync.seconds}s` : ''}
      {sync.graded ? ` · ${num(sync.graded)} graded` : ''}</span>}
    {building && <span className="chip live"><Loader2 size={11} className="spin"/>building a newer payload — the board stays on the last one</span>}
    {waiting && keyMoved && <span className="chip warn">the source moved to a newer matchday; the rebuild starts within a minute</span>}
    {source === 'live' && !building && <span className="chip live"><i/>live · ledger checked every {TICK_SECONDS}s, board refetched only when it changes</span>}
    {source === 'offline' && <span className="chip warn">no server · open the packaged app to read the live board</span>}
    {integrity?.live != null && <span className="chip"><ShieldCheck size={11}/><b>{integrity.live}</b> sheets locked live · <b>{integrity.backfilled || 0}</b> backfilled{integrity.on_time_share != null ? ` · ${pct(integrity.on_time_share)} before kick-off` : ''}</span>}
    {error && <span className="chip err">service: {error}</span>}
    {(service.error) && <span className="chip err">builder error: {service.error}</span>}
    <button className="chip chip-button" onClick={onRefresh} disabled={refreshing} title="Rebuild the payload and reload the board now">
      <RefreshCw size={11} className={refreshing ? 'spin' : ''}/>{refreshing ? 'refreshing…' : 'refresh now'}
    </button>
  </div>;
}

function Legend(){
  return <div className="forecast-controls pg-legend">
    <span className="forecast-legend"><i className="legend pick-exact"/>exact FT score</span>
    <span className="forecast-legend"><i className="legend pick-direction"/>right result, wrong score</span>
    <span className="forecast-legend"><i className="legend pick-miss"/>wrong</span>
    <span className="forecast-legend"><i className="legend pending"/>to play</span>
    <small>The upper row of each team is the recorded full-time score for that matchday; the row beneath it is what the mixture predicted before it was played. The incoming matchday carries the pending prediction until the collector records its FT score, and the board follows the source on its own every {POLL_SECONDS} seconds.</small>
  </div>;
}

function FixtureCards({payload,scope,onSelect,selected}){
  const rows = (payload.target?.rows || []).filter(row => row.venue === 'H');
  if(!rows.length) return <div className="notice warning"><Info size={17}/><span>No incoming matchday in this payload — the collector publishes one when the source opens the next round.</span></div>;
  return <section className="panel">
    <header className="pg-head"><div><span className="replay-kicker">INCOMING MATCHDAY {payload.target?.day} · {scope === 'hit_blend' ? 'COMPETITION BLEND' : 'LOG-LOSS BLEND'}</span>
      <h2>{rows.length} fixtures to be played</h2>
      <p>These are the predictions sitting in the last column of the board above. They turn into graded rows the moment the source publishes the full-time scores.</p></div>
      <Status tone="green">{rows.filter(row => row.market_available).length}/{rows.length} with a captured market catalogue</Status></header>
    <div className="pg-fixtures">{rows.map(row => {
      const away = (payload.target.rows || []).find(other => other.event_id === row.event_id && other.venue === 'A') || {};
      const blend = (scope === 'hit_blend' ? row.hit_blend : row.blend) || row.hit_blend || row.blend || {};
      const comp = row.competition || {};
      const result = blend.result || {};
      const pick = (blend.top?.[0] || {});
      const agree = row.agreement || {};
      return <button key={row.event_id} className={`pg-fixture ${selected === row.team ? 'active' : ''}`} onClick={() => onSelect(row.team)} aria-pressed={selected === row.team} title={`Open ${row.team} vs ${row.opponent}`}>
        <div className="pg-fixture-teams"><span><TeamBadge name={row.team} small/><strong>{row.team}</strong><i>home</i></span><span><TeamBadge name={row.opponent} small/><strong>{row.opponent}</strong><i>away</i></span></div>
        <div className="pg-fixture-pick"><span className="pick">{pick.score || '—'}</span><span className="prob">{pct(pick.probability)}</span><span className={`badge ${comp.confidence || 'thin'}`}>{comp.confidence || 'thin'} · {agree.top_scores?.[0]?.models || 0}/{agree.engines} engines</span></div>
        <div className="pg-fixture-top">{((blend.top || []).slice(0, 3)).map((item, index) => <span key={item.score} className={`pill ${index === 0 ? 'first' : ''}`}>{item.score} <b>{pct(item.probability)}</b></span>)}</div>
        <div className="bars">
          {[['win', 'home'], ['draw', 'draw'], ['loss', 'away']].map(([key, label]) => <div className="bar" key={key}><span>{label}</span><span className="track"><span className={`fill ${key === 'draw' ? 'x' : key === 'loss' ? 'y' : ''}`} style={{width:`${Math.min(100, result[key] || 0)}%`}}/></span><span className="val">{pct(result[key])}</span></div>)}
        </div>
        <div className="pg-fixture-foot">
          <span className="pill">{row.market_available ? 'market catalogue captured' : 'no market catalogue'}</span>
          {away.competition && <span className="pill">away frame {away.competition.pick}</span>}
          {row.blend?.top?.[0] && row.hit_blend?.top?.[0] && row.blend.top[0].score !== row.hit_blend.top[0].score && <span className="pill">log-loss view {row.blend.top[0].score}</span>}
          {row.context?.length > 0 && <span className="pill">sequence {row.context.slice(-3).join(' ')}</span>}
        </div>
      </button>;
    })}</div>
    <footer className="pg-foot"><ShieldCheck size={13}/>Percentages under each pick are the blended home / draw / away split for the listed home team. A missing market catalogue reduces the engines used for that fixture; the row shows it rather than guessing.</footer>
  </section>;
}

function PickDetail({payload,scope,team,kind,column,onClose}){
  const live = payload.live_board || {};
  const view = live.blends?.[scope] || {};
  const summary = view.teams?.[team]?.summary || {};
  const weights = scope === 'hit_blend' ? (payload.target?.competition_weights || {}) : (payload.target?.logloss_weights || {});
  const upcoming = (payload.target?.rows || []).find(row => row.team === team);
  const verdict = verdictOf(column);
  const blendForDay = column?.pending ? ((scope === 'hit_blend' ? upcoming?.hit_blend : upcoming?.blend) || upcoming?.hit_blend || {}) : null;
  return <Modal title={`${team} · matchday ${column.day} ${kind === 'pending' ? 'prediction' : 'ledger entry'}`} onClose={onClose} wide>
    <div className="methodology pick-detail">
      <div className="pick-verdict">
        <div className="pick-score"><strong>{column.pick || '—'}</strong><small>{kind === 'pending' ? 'prediction to play' : 'walk-forward pick'}</small></div>
        <div className="pg-detail-facts">
          <span>blended probability <b>{pct(column.probability)}</b></span>
          <span>engines agreeing <b>{column.agrees ?? '—'}/{column.engines ?? '—'}</b></span>
          <span>recorded FT <b>{column.score || 'not published yet'}</b></span>
          {kind !== 'pending' && <span>verdict <b data-verdict={verdict}>{VERDICT_LABEL[verdict]}</b></span>}
        </div>
      </div>
      <p className="lead">{kind === 'pending'
        ? <>This is the {scope === 'hit_blend' ? 'competition' : 'log-loss'} blend reading for the matchday the source is publishing{column.opponent ? ` — ${team} vs ${column.opponent}${column.venue === 'A' ? ' (away)' : ' (home)'}` : ''}. It is already written into the frozen ledger and will not change; a graded verdict is added to this same row once the FT score is recorded.</>
        : <>This pick was <b>{column.source === 'backfill' ? 'backfilled once from the walk-forward measurement and frozen' : 'locked in the ledger before the matchday was played'}</b> by the {scope === 'hit_blend' ? 'competition' : 'log-loss'} blend, then graded against the FT score the collector recorded for that matchday: <b>{column.score || '—'}</b>. The prediction itself is never recomputed.</>}</p>
      {column.top?.length > 0 && <><h3>Most likely scores that day</h3><div className="pg-pills">{column.top.slice(0, 5).map((score, index) => <span key={score} className={`pill ${index === 0 ? 'first' : ''}`}>{score}</span>)}</div></>}
      {blendForDay?.top?.length > 0 && <><h3>Top five for the incoming fixture</h3><div className="pg-pills">{blendForDay.top.map((item, index) => <span key={item.score} className={`pill ${index === 0 ? 'first' : ''}`}>{item.score} <b>{pct(item.probability)}</b></span>)}</div></>}
      <h3>Provenance</h3>
      <div className="pg-facts">
        <span>sheet source <b>{column.source === 'backfill' ? 'backfilled then frozen' : column.source === 'live' ? 'locked live' : 'frozen sheet'}</b></span>
        <span>locked <b>{column.locked_at ? timeOf(column.locked_at) : 'before this run'}</b></span>
        <span>ledger <b>data/predictions.sqlite</b></span>
      </div>
      {summary.picks != null && <><h3>{team} this season</h3><div className="pg-facts">
        <span><b>{summary.picks}</b> graded picks</span><span><b>{summary.hits}</b> exact</span><span><b>{summary.direction_hits}</b> right result ({pct(summary.direction_rate)})</span><span><b>{summary.misses - (summary.picks - summary.direction_hits - summary.hits)}</b> wrong</span><span>matchdays MD{summary.first_day}–MD{summary.last_day}</span></div></>}
      <h3>Mixture weights behind this prediction</h3>
      <p className="note">{weightList(payload, weights).join(' · ') || 'the payload did not publish weights for this blend'}</p>
      <div className="notice"><Info size={17}/><span>An exact score is rare in a 30-matchday league: even a well-calibrated distribution lands the exact FT about one time in eight. The green cells are the exceptions; the amber ones called the result without the score.</span></div>
    </div>
  </Modal>;
}

export default function PlaygroundLive(){
  const {payload, status, tick, building, waiting, keyMoved, refreshNow, refreshing, loading, error, source, received} = usePlayground();
  const [scope,setScope] = useState('hit_blend');
  const [tab,setTab] = useState('live');
  const [filter,setFilter] = useState('');
  const [detail,setDetail] = useState(null);
  const [selected,setSelected] = useState('');
  const scopes = scopeOptions(payload);
  const live = payload?.live_board || {};
  const view = live.blends?.[scope] || {teams:{}};
  const seasonTeams = useMemo(() => Object.keys(live.blends?.hit_blend?.teams || {}).sort((a, b) => a.localeCompare(b)), [live]);
  const teams = useMemo(() => seasonTeams.filter(name => name.toLowerCase().includes(filter.trim().toLowerCase())), [seasonTeams, filter]);

  if(loading && !payload) return <div className="replay-lab"><PageHead eyebrow="PLAYGROUND" title="FT score Prediction Playground" description="Loading the walk-forward predictions…"/><Loading text="Reading the prediction payload…"/></div>;
  if(!payload) return <div className="replay-lab"><PageHead eyebrow="PLAYGROUND" title="FT score Prediction Playground" description="This workspace reads /api/playground from the league DNA server."/>
    <ErrorBanner error={error || 'The payload is not available in this installation.'}/>
    <div className="notice warning"><Info size={17}/><span>The playground needs the running app: start the launcher and open <b>/playground-live</b>, or read the standalone board in the <b>FT score Prediction Playground</b> folder. Predictions are never invented offline.</span></div></div>;

  const summary = view.summary || {};
  const target = payload.target || {};
  const days = targetDays(payload);
  const nextDay = days.length ? days[0] : target.day;
  const lastPlayed = Math.max(0, ...Object.values(view.teams || {}).flatMap(entry => entry.rows.map(row => row.day)),
                              ...Object.values(live.scores || {}).flatMap(entry => Object.keys(entry).map(Number)));
  const pendingDay = days.find(day => day > lastPlayed) ?? nextDay;
  return <div className="replay-lab playground-live">
    <PageHead eyebrow="FT SCORE PREDICTION PLAYGROUND"
      title="FT score Prediction Playground"
      description={<>The ongoing season exactly like the Live FT board: the recorded full-time score on top, the prediction the mixture made for that same matchday underneath — green for an exact score, amber for the right result, red for a miss. The matchday the source is publishing carries its prediction until its FT scores are recorded.</>}>
      <Status tone={source === 'live' ? 'green' : 'amber'}>{source === 'live' ? (building ? 'Live · rebuilding in the background' : 'Live and auto-refreshing') : 'Reading the last published payload'}</Status>
    </PageHead>
    <div className="pg-view-switch" role="tablist" aria-label="Playground views">
      <button role="tab" aria-selected={tab === 'live'} className={`button ${tab === 'live' ? '' : 'secondary'}`} onClick={() => setTab('live')}>
        <LayoutGrid size={13}/>Live FT board</button>
      <button role="tab" aria-selected={tab === 'sheets'} className={`button ${tab === 'sheets' ? '' : 'secondary'}`} onClick={() => setTab('sheets')}>
        <ListTree size={13}/>Season sheets — {num(payload.ledger?.stats?.picks)} frozen picks</button>
    </div>
    <StatusChips payload={payload} status={status} tick={tick} integrity={payload.ledger?.integrity} building={building} waiting={waiting} keyMoved={keyMoved} source={source} error={error} received={received} onRefresh={refreshNow} refreshing={refreshing}/>
    {tab === 'sheets' && <PlaygroundSheets boardKey={payload.meta?.board_key} scope={scope}/>}
    {tab === 'live' && <>
    <div className="notice replay-boundary"><Radio size={18}/><span><strong>Season {live.season} · {seasonTeams.length} teams · {num(summary.picks)} graded predictions ({num(summary.hits)} exact, {pct(summary.direction_rate)} called the result, {summary.misses != null ? num(summary.misses - (summary.picks - summary.direction_hits - summary.hits)) : '—'} wrong).</strong> {lastPlayed ? `Matchday ${lastPlayed} is the last completed one` : 'No matchday of this season has been recorded yet'}; the source has {days.length ? `${days.length} matchday${days.length === 1 ? '' : 's'} to play (${days.map(day => `MD${day}`).join(', ')})` : 'nothing left to play in this season'} and those columns hold the pending predictions.</span></div>
    <section className="panel live-board">
      <header>
        <div><span className="replay-kicker">{source === 'live' ? `COLLECTOR DATA · AUTO-UPDATING EVERY ${POLL_SECONDS} SECONDS` : 'LAST PUBLISHED PAYLOAD'}</span>
          <h2>Season {live.season} · {lastPlayed ? `completed through MD${lastPlayed}` : 'no matchday completed yet'} · to play {days.length ? days.map(day => `MD${day}`).join(', ') : 'nothing scheduled'}</h2>
          <p>{scope === 'hit_blend' ? 'Competition blend — the engines mixed to maximise exact hits. This is the prediction the playground plays.' : 'Log-loss blend — the same engines mixed to minimise log loss, the calibrated cross-check on the same matchdays.'} Every score is written in the row team's own frame (goals for : goals against).</p></div>
        <div><input aria-label="Filter live prediction teams" value={filter} onChange={event => setFilter(event.target.value)} placeholder="Filter team…"/>
          <button className="button secondary small-button" onClick={() => setSelected('')}><LayoutGrid size={13}/>All teams</button>
          {scopes.map(option => <button key={option.key} className={`button small-button ${scope === option.key ? '' : 'secondary'}`} onClick={() => setScope(option.key)} disabled={!option.available} title={option.note}>{option.label}</button>)}</div>
      </header>
      <div className="forecast-summary">
        <div className="forecast-totals">
          <div><span>predicted</span><strong>{num(summary.picks)}<small>graded</small></strong></div>
          <div><span>exact FT</span><strong>{num(summary.hits)}<small>{pct(summary.hit_rate)}</small></strong></div>
          <div><span>right result</span><strong>{num(summary.direction_hits)}<small>{pct(summary.direction_rate)}</small></strong></div>
          <div><span>matchdays</span><strong>{num(summary.matchdays)}<small>MD1–MD{lastPlayed}</small></strong></div>
          <div><span>to play now</span><strong>{num(target.fixtures)}<small>{days.length ? days.map(day => `MD${day}`).join(' · ') : '—'}</small></strong></div>
        </div>
        <Legend/>
      </div>
      <div className="live-board-scroll"><div className="live-board-canvas">
        <div className="live-board-grid live-board-ruler"><div className="live-board-team">RECORDED FT / PREDICTION</div>{Array.from({length:BOARD_DAYS},(_,index)=><span key={index} className={days.includes(index+1) ? 'incoming' : ''}>{String(index+1).padStart(2,'0')}</span>)}</div>
        {teams.map(team => {
          const entry = view.teams[team];
          const columns = boardColumns(payload, scope, team);
          const upcoming = (target.rows || []).find(row => row.team === team);
          const wrong = entry.summary.picks - entry.summary.hits - entry.summary.direction_hits;
          return <React.Fragment key={team}>
            <div className="live-board-grid live-board-row" data-team={team} data-scope={scope}>
              <div className="live-board-team"><span><TeamBadge name={team} small/><strong>{team}</strong></span>
                <small>{(upcoming?.venue === 'A' ? 'Away' : 'Home')} · vs {upcoming?.opponent || 'season complete'} · {entry.summary.hits} exact / {entry.summary.picks} predicted</small></div>
              {columns.map(column => column.score
                ? <button key={column.day} className={`live-board-score recorded ${days.includes(column.day) ? 'incoming-day' : ''}`} data-day={column.day} data-score={column.score} data-state="recorded"
                    title={`MD${column.day} · recorded FT ${column.score}${column.pick ? ` · predicted ${column.pick} (${VERDICT_LABEL[verdictOf(column)]})` : ''}`}
                    onClick={() => column.pick && setDetail({team, kind:'graded', column})}>
                    <strong>{column.score}</strong><small>FT</small></button>
                : <span key={column.day} className="live-board-score empty" data-day={column.day} data-verdict="none" aria-hidden="true">·</span>)}
            </div>
            <div className="live-board-grid live-board-ledger-row" data-team={team} data-scope={scope}>
              <div className="live-board-team ledger-team"><span><TrendingUp size={12}/><strong>{team} prediction</strong></span>
                <small data-summary={`${entry.summary.hits}/${entry.summary.picks}`}>{entry.summary.picks} predicted · {entry.summary.hits} exact · {entry.summary.direction_hits} right result · {wrong} wrong</small></div>
              {columns.map(column => <PredictionCell key={column.day} column={column} team={team} onOpen={() => setDetail({team, kind: column.pending ? 'pending' : 'graded', column})}/>)}
            </div>
          </React.Fragment>;
        })}
      </div></div>
      <footer><ShieldCheck size={13}/>{teams.length} of {seasonTeams.length} teams shown. A matchday with no recorded score stays blank rather than being filled with 0:0. Click a prediction for its evidence, or use the scope switch to grade the same matchdays with the log-loss mixture. Last checked {timeOf(received)} EAT.</footer>
    </section>
    <FixtureCards payload={payload} scope={scope} selected={selected} onSelect={team => setSelected(selected === team ? '' : team)}/>
    {selected && (() => {
      const row = (payload.target.rows || []).find(item => item.team === selected);
      if(!row) return null;
      const order = Object.keys(row.models || {}).sort((a, b) => (row.models[b].probability || 0) - (row.models[a].probability || 0));
      return <section className="panel">
        <header className="pg-head"><div><span className="replay-kicker">ENGINE BREAKDOWN · MATCHDAY {row.day}</span><h2>{row.team} vs {row.opponent}</h2>
          <p>The incoming fixture's engines, ordered by the probability each one gives to its own top score. Open the engines workspace for the full explanatory space behind each of them.</p></div>
          <button className="button secondary small-button" onClick={() => setSelected('')}>Close</button></header>
        <div className="pg-table-scroll"><table className="pg-table">
          <thead><tr><th>Engine</th><th>top score</th><th className="num">probability</th><th className="num">competition weight</th><th className="num">log-loss weight</th><th>runner-up</th><th className="num">home / draw / away</th></tr></thead>
          <tbody>{order.map(key => {
            const model = row.models[key] || {};
            const result = model.result || {};
            return <tr key={key}><td><b>{engineLabel(payload, key)}</b></td><td>{model.score || '—'}</td><td className="num">{pct(model.probability)}</td>
              <td className="num">{pct(100 * (payload.target.competition_weights?.[key] || 0))}</td>
              <td className="num">{pct(100 * (payload.target.logloss_weights?.[key] || 0))}</td>
              <td>{model.top?.[1]?.score || '—'} <small>{pct(model.top?.[1]?.probability)}</small></td>
              <td className="num">{pct(result.win)} / {pct(result.draw)} / {pct(result.loss)}</td></tr>;
          })}
          <tr className="pg-total"><td><b>{engineLabel(payload, 'hit_blend')}</b></td><td>{row.hit_blend?.top?.[0]?.score || '—'}</td><td className="num">{pct(row.hit_blend?.top?.[0]?.probability)}</td>
            <td className="num" colSpan={2}>the mixture formed from the weights above</td><td>{row.hit_blend?.top?.[1]?.score || '—'}</td><td className="num">—</td></tr>
          </tbody></table></div>
        <footer className="pg-foot"><Scale size={13}/>Weights come from the walk-forward fit and move matchday by matchday; they are reproduced with every prediction, so any cell on the board can be re-derived from this table.</footer>
      </section>;
    })()}
    </>}
    <section className="panel pg-prose">
      <header className="pg-head"><div><span className="replay-kicker">WHY THE BOARD IS NOT A PROMISE</span><h2>Read the predictions the way the competition scores them</h2></div></header>
      <div className="pg-prose-body">
        <div><Award size={16}/><h3>Exact scores are the prize, not the expectation</h3><p>A 30-matchday league produces roughly one exact-score hit in eight attempts for a well-calibrated model, so most cells on the prediction row are amber or red — that is the honest picture, and the board counts them ({num(summary.hits)} exact from {num(summary.picks)}).</p></div>
        <div><Lock size={16}/><h3>Every cell is frozen when it is made</h3><p>A sheet is written the moment the source announces the matchday and is never rewritten — not by a refit, not by a restart, not by the next season. The board you refreshed yesterday is the board you read today, which is the only way to know afterwards which matchdays the predictor actually got right.</p></div>
        <div><Database size={16}/><h3>Where the numbers come from</h3><p>Elo, k-nearest historical windows, captured market catalogues, Markov transition counts, a Dixon-Coles strength model and the league prior, each calibrated by shrinkage and mixed by the walk-forward weight fit. The archive is the recorded Betika League history in <b>data/league.sqlite</b>; nothing is fabricated.</p></div>
        <div><RefreshCw size={16}/><h3>How it keeps itself current</h3><p>The service checks the source every {TICK_SECONDS} seconds. The instant a matchday is announced its sheet is locked — usually minutes before the first kick-off — and the instant a result is recorded it is written into the sheet that predicted it. A page refresh is a read of that ledger, so it never waits for a model: the engines refit in the background and never gate the board.</p></div>
      </div>
      <footer className="pg-foot"><Info size={13}/>{payload.live_board?.note} Payload key {payload.meta?.key} · revision {payload.data?.revision}.</footer>
    </section>
    {detail && <PickDetail payload={payload} {...detail} onClose={() => setDetail(null)}/>}
  </div>;
}

function PredictionCell({column,team,onOpen}){
  if(column.empty && !column.pick) return <span className="ledger-cell blank" data-verdict="none" aria-hidden="true">·</span>;
  if(column.stale) return <button className="ledger-cell prediction stale" data-day={column.day} data-verdict="stale" data-pick={column.pick || ''} data-actual={column.score || ''} data-team={team}
    title={`MD${column.day} · recorded FT ${column.score} · the prediction made before it is being regraded by the next rebuild`} onClick={onOpen}>
    <b>{column.pick || '—'}</b><small>REGRADING</small></button>;
  const verdict = verdictOf(column);
  return <button className={`ledger-cell prediction ${verdict}`} data-day={column.day} data-verdict={verdict} data-pick={column.pick || ''} data-actual={column.score || ''} data-team={team} data-probability={column.probability ?? ''} data-agrees={`${column.agrees ?? ''}/${column.engines ?? ''}`}
    title={`MD${column.day} · predicted ${column.pick || '—'}${column.score ? ` · recorded FT ${column.score} · ${VERDICT_LABEL[verdict]}` : ' · awaiting the source'}`} onClick={onOpen}>
    <b>{column.pick || '—'}</b><small>{VERDICT_SHORT[verdict]}</small>
  </button>;
}
