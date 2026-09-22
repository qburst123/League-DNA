import React,{useMemo,useState} from 'react';
import {Activity,Award,Scale,Sigma,Microscope,Compass,Layers,Info,ShieldCheck,Crosshair,ListTree,FlaskConical,RefreshCw} from 'lucide-react';
import {PageHead,Loading,ErrorBanner,Status,TeamBadge,num,timeLabel} from './shared';
import {pct,fixed,parseScore,usePlayground,engineLabel,engineOrder,weightList} from './playgroundModel';

const SKIP_EXPLAIN = new Set(['levels','neighbours','boosted','damped','counts','reference','reason']);

function Chips({payload,source,error}){
  const meta = payload.meta || {}, data = payload.data || {}, target = payload.target || {}, status = payload.status || {};
  return <div className="pg-chips" role="status">
    <span className="chip">engines <b>{(payload.engines || []).length}</b> · archive <b>{num(data.matches)}</b> results</span>
    <span className="chip">walk-forward sample <b>{num(payload.backtest?.sample)}</b> rows · fit <b>{num(payload.backtest?.fit_rows)}</b> · evaluated <b>{num(payload.backtest?.eval_rows)}</b></span>
    <span className="chip">window <b>{num(meta.window)}</b> · champion <b>{engineLabel(payload, payload.backtest?.champion)}</b></span>
    <span className="chip">target <b>{target.label || '—'}</b> · {target.fixtures || 0} fixtures</span>
    {source === 'live' && !status.error && <span className="chip live"><i/>live /api/playground</span>}
    {error && <span className="chip err">service: {error}</span>}
  </div>;
}

function Metrics({metrics,best}){
  if(!metrics) return <span className="pg-muted">no evaluated rows</span>;
  return <div className="pg-metrics">
    <span className="pg-metric"><b>{pct(metrics.hit1)}</b><small>exact hit</small><span className="pg-track"><span className="pg-fill" style={{width:`${Math.min(100, 100 * (metrics.hit1 || 0) / best).toFixed(1)}%`}}/></span><em>[{pct(metrics.hit1_ci?.[0])} – {pct(metrics.hit1_ci?.[1])}]</em></span>
    <span className="pg-metric"><b>{pct(metrics.hit3)}</b><small>top-3</small></span>
    <span className="pg-metric"><b>{fixed(metrics.logloss)}</b><small>log loss</small></span>
    <span className="pg-metric"><b>{pct(metrics.result)}</b><small>1X2 shape</small></span>
    <span className="pg-metric"><b>{num(metrics.n)}</b><small>rows</small></span>
  </div>;
}

function EngineRoster({payload}){
  const backtest = payload.backtest || {};
  const best = Math.max(1, ...Object.values(backtest.models || {}).map(entry => entry.calibrated?.hit1 || entry.raw?.hit1 || 0));
  return <div className="pg-engine-cards">
    {(payload.engines || []).map(card => {
      const metrics = backtest.models?.[card.key] || {};
      const weight = card.key === 'hit_blend' ? backtest.hit_weights : card.key === 'blend' ? backtest.weights : (backtest.hit_weights || {})[card.key];
      return <article className="pg-engine" key={card.key}>
        <header><div><span className="replay-kicker">{card.family || 'Engine'}</span><h3>{card.name}</h3></div>
          {weight != null && <span className="pg-weight">weight <b>{pct(100 * weight)}</b></span>}</header>
        <p>{card.blurb}</p>
        <Metrics metrics={metrics.calibrated || metrics.raw} best={best}/>
        <dl className="pg-kv">
          <dt>assumes</dt><dd>{card.assumptions}</dd>
          <dt>strength</dt><dd>{card.strengths}</dd>
          <dt>limit</dt><dd>{card.limits}</dd>
          <dt>calibration</dt><dd>{card.key === 'blend' || card.key === 'hit_blend' ? 'ensemble of the engines above' : `shrinkage ${fixed(card.shrinkage, 2)} toward the league prior${card.gamma != null ? ` · sequence exponent γ ${fixed(card.gamma, 3)}` : ''}`}</dd>
          <dt>coverage</dt><dd>{num(card.coverage)} fixtures where this engine produced a grid{card.coverage != null && backtest.sample ? ` (${pct(100 * card.coverage / backtest.sample)} of the sample)` : ''}</dd>
        </dl>
      </article>;
    })}
  </div>;
}

function Scorecard({payload}){
  const backtest = payload.backtest || {}, models = backtest.models || {};
  const order = engineOrder(payload).concat(Object.keys(models).filter(key => !engineOrder(payload).includes(key)));
  const modal = backtest.baselines?.modal || {};
  const best = Math.max(1, ...order.map(key => (models[key]?.calibrated?.hit1 || 0)));
  return <div className="pg-table-scroll"><table className="pg-table scorecard">
    <thead><tr><th>Engine / mixture</th><th className="num">exact hit</th><th>95% interval</th><th className="num">top-3</th><th className="num">log loss</th><th className="num">1X2</th><th className="num">rows</th><th className="num">shrink</th><th className="num">γ</th><th className="num">competition weight</th><th className="num">log-loss weight</th></tr></thead>
    <tbody>
      {order.map(key => {
        const entry = models[key]; if(!entry) return null;
        const metrics = entry.calibrated || entry.raw || {};
        return <tr key={key} className={backtest.champion === key ? 'pg-champion' : ''}>
          <td><b>{engineLabel(payload, key)}</b>{backtest.champion === key && <span className="badge">champion</span>}{entry.calibrated && entry.raw && entry.calibrated.hit1 !== entry.raw.hit1 && <small> raw {pct(entry.raw.hit1)}</small>}</td>
          <td className="num"><b>{pct(metrics.hit1)}</b></td>
          <td><span className="pg-track"><span className="pg-fill" style={{width:`${Math.min(100, 100 * (metrics.hit1 || 0) / best).toFixed(1)}%`}}/></span><small>[{pct(metrics.hit1_ci?.[0])} – {pct(metrics.hit1_ci?.[1])}]</small></td>
          <td className="num">{pct(metrics.hit3)}</td><td className="num">{fixed(metrics.logloss)}</td><td className="num">{pct(metrics.result)}</td>
          <td className="num">{num(metrics.n || entry.coverage)}</td>
          <td className="num">{entry.shrinkage != null ? fixed(entry.shrinkage, 2) : '—'}</td>
          <td className="num">{entry.gamma != null ? fixed(entry.gamma, 2) : '—'}</td>
          <td className="num">{pct(100 * ((backtest.hit_weights || {})[key] || 0))}</td>
          <td className="num">{pct(100 * ((backtest.weights || {})[key] || 0))}</td></tr>;
      })}
      <tr className="pg-total"><td><b>Always play {modal.score || '1:1'}</b><span className="badge">naive reference</span></td>
        <td className="num">{pct(modal.hit1)}</td><td colSpan={9}>the most common exact score of the fitting half, played on every fixture ({num(modal.n)} rows)</td></tr>
    </tbody></table></div>;
}

function Heat({grid,pick}){
  if(!grid?.length) return <p className="note">This engine did not produce a grid for this fixture.</p>;
  const [pickFor, pickAgainst] = parseScore(pick);
  const max = Math.max(1, ...grid.flat());
  return <div className="pg-heat-wrap"><table className="heat"><thead><tr><th/>{grid.map((_,index) => <th key={index}>{index}</th>)}</tr></thead>
    <tbody>{grid.map((row,index) => <tr key={index}><th>{index}</th>
      {row.map((cell,col) => <td key={col} className={index === pickFor && col === pickAgainst ? 'hit' : ''} style={{'--a':Math.min(0.75, 0.75 * cell / max).toFixed(3)}}>{fixed(cell/10, 1)}</td>)}</tr>)}</tbody></table>
    <p className="note">Full 8×8 grid in percent. Rows are goals for the row team, columns goals against; the outlined cell is the blend's pick. Cells are stored in tenths of a percent by the service.</p></div>;
}

function KV({explain}){
  const rows = Object.keys(explain || {}).filter(key => !SKIP_EXPLAIN.has(key) && explain[key] != null && typeof explain[key] !== 'object');
  if(!rows.length) return null;
  return <dl className="pg-kv">{rows.map(key => <React.Fragment key={key}><dt>{key.replace(/_/g,' ')}</dt><dd>{String(explain[key])}</dd></React.Fragment>)}</dl>;
}

function Reasons({explain}){
  const items = [];
  if(explain?.reason) items.push(<span key="reason">{explain.reason}</span>);
  (explain?.levels || []).forEach(level => {
    if(!level.counts?.length) return;
    items.push(<span key={`level-${level.order}`}>Order {level.order} saw the run [{(level.context || []).join(' ')}] {level.occurrences} time(s); most frequent follow-ups: {level.counts.slice(0,3).map(item => `${item.score}×${item.count}`).join(', ')}.</span>);
  });
  (explain?.neighbours || []).length && items.push(<span key="neighbours">Nearest historical windows: {explain.neighbours.map(n => `${n.team} ${n.season} (${n.similarity}% similar, ${n.last_score} → ${n.next_score}, ${n.age} matchdays old)`).join('; ')}.</span>);
  (explain?.boosted || []).length && items.push(<span key="boosted">Sequence evidence lifted {explain.boosted.slice(0,3).map(item => `${item.score} ×${item.ratio}`).join(', ')} against the statistical shape.</span>);
  (explain?.damped || []).length && items.push(<span key="damped">and pushed down {explain.damped.map(item => `${item.score} ×${item.ratio}`).join(', ')}.</span>);
  if(!items.length) return null;
  return <ul className="reasons">{items.map((item, index) => <li key={index}>{item}</li>)}</ul>;
}

const BLEND_KEYS = new Set(['hit_blend','blend']);

function EngineDetail({payload,row,pick,blend}){
  const models = row.models || {};
  const unavailable = row.unavailable || {};
  // every engine in the roster keeps its place in this view: an engine that cannot price this
  // particular matchday says so, instead of quietly disappearing from the chart and the cards
  const order = engineOrder(payload).filter(key => !BLEND_KEYS.has(key))
    .sort((a, b) => (a in models ? 0 : 1) - (b in models ? 0 : 1));
  const [pickFor, pickAgainst] = parseScore(pick);
  const weights = blend?.weights || {};
  const parts = order.map(key => {
    if(!(key in models)) return {key, weight: 0, probability: 0, share: 0,
      reason: unavailable[key]?.reason || 'no distribution for this matchday'};
    const grid = models[key].grid || [];
    const probability = (grid[pickFor] && grid[pickFor][pickAgainst] != null ? grid[pickFor][pickAgainst] : 0) / 1000;
    return {key, weight: weights[key] || 0, probability, share: (weights[key] || 0) * probability};
  });
  const total = parts.reduce((sum, item) => sum + item.share, 0) || 1;
  const abstaining = parts.filter(item => item.reason);
  return <>
    <div className="panel pg-contribution">
      <header className="pg-head"><div><span className="replay-kicker">WHO DECIDED {pick}</span><h2>Contribution = mixture weight × the probability that engine gives this pick</h2>
        <p>An engine can hold a large weight and still contribute almost nothing if it dislikes this particular score. That gap is the interesting part of the chart, and it is why the blend can play a score no single engine ranks first.</p>
        {abstaining.length > 0 && <p className="note">This matchday {abstaining.length === 1 ? 'has one engine abstaining' : `has ${abstaining.length} engines abstaining`}: {abstaining.map(item => `${engineLabel(payload, item.key)} — ${item.reason}`).join('; ')}. An abstaining engine takes no weight here, and the blend renormalises over the engines that did price it.</p>}</div><Crosshair size={18}/></header>
      <div className="bars">{parts.slice().sort((a, b) => b.share - a.share).map(item => <div className={`bar pg-bar-wide${item.reason ? ' pg-bar-abstain' : ''}`} key={item.key}>
        <span>{engineLabel(payload, item.key)}</span>
        <span className="track"><span className="fill" style={{width:`${(100 * item.share / total).toFixed(1)}%`}}/></span>
        <span className="val">{item.reason ? 'no share of the pick' : `${pct(100 * item.share / total)} of the pick`} <small>{item.reason ? `abstains — ${item.reason}` : `weight ${pct(100 * item.weight)} · gives the pick ${pct(100 * item.probability)}`}</small></span></div>)}</div>
    </div>
    <div className="pg-engine-cards">
      {order.map(key => {
        const model = models[key] || {};
        const explain = model.explain || unavailable[key] || {};
        const grid = model.grid || [];
        const own = grid[pickFor] && grid[pickFor][pickAgainst] != null ? grid[pickFor][pickAgainst] / 10 : null;
        if(!(key in models)) return <article className="pg-engine pg-explain abstains" key={key}>
          <header><div><span className="replay-kicker">EXPLAINABILITY SPACE</span><h3>{engineLabel(payload, key)}</h3></div>
            <span className="pg-weight">abstains · takes no weight</span></header>
          <div className="chain"><span>weight here <b>0%</b></span><span>gives {pick} <b>—</b></span></div>
          <div className="pg-abstain" role="note">{explain.reason || 'no distribution for this matchday'} — so it is left out of the mixture for this matchday and the blend renormalises over the engines that did price it.</div>
        </article>;
        return <article className="pg-engine pg-explain" key={key}>
          <header><div><span className="replay-kicker">EXPLAINABILITY SPACE</span><h3>{engineLabel(payload, key)}</h3></div>
            <span className="pg-weight">{model.raw_score === model.score ? `raw pick ${model.score}` : `raw ${model.raw_score} → ${model.score}`} · top {pct(model.probability)}</span></header>
          <div className="chain">
            <span>weight <b>{pct(100 * (weights[key] || 0))}</b></span>
            <span>probability it gives {pick} <b>{own == null ? '—' : pct(own)}</b></span>
            {explain.shrinkage != null && <span>pulled to the prior <b>{pct(100 * explain.shrinkage)}</b></span>}
            {explain.gamma != null && <span>sequence exponent γ <b>{fixed(explain.gamma, 3)}</b></span>}
            {explain.reference && <span>reference <b>{explain.reference}</b></span>}
          </div>
          <Heat grid={grid} pick={pick}/>
          <KV explain={explain}/>
          <Reasons explain={explain}/>
        </article>;
      })}
    </div>
  </>;
}

function LedgerAndBands({payload}){
  const backtest = payload.backtest || {};
  const days = backtest.days || [];
  const bands = backtest.confidence?.bands || [];
  const agreement = backtest.confidence?.by_agreement || [];
  const maxHits = Math.max(1, ...days.map(day => day.hits || 0));
  return <div className="pg-two">
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">PER-MATCHDAY LEDGER</span><h2>Exact hits, matchday by matchday</h2>
        <p>{days.length} evaluated matchdays. The competition blend's best round: {num(backtest.baselines?.matchday_best)} hits; the mean is {fixed(backtest.baselines?.matchday_mean, 1)} per round. Flat weeks are the normal state of an exact-score game.</p></div></header>
      <div className="pg-days">{days.map(day => <div className="pg-day" key={day.label} title={`${day.label} · ${day.fixtures} fixtures · ${day.hits} exact hits`}>
        <span>{day.day}</span><span className="pg-track"><span className="pg-fill" style={{width:`${100 * (day.hits || 0) / maxHits}%`}}/></span><b>{day.hits}</b></div>)}</div>
    </section>
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">CONFIDENCE BANDS</span><h2>Does a higher claimed probability hit more often?</h2>
        <p>Rows are bands of the blend's own probability for its top score, in the evaluated part of the walk-forward run. Overlapping intervals are the honest reading.</p></div></header>
      <div className="pg-table-scroll"><table className="pg-table">
        <thead><tr><th>claimed probability band</th><th className="num">rows</th><th className="num">mean claim</th><th className="num">exact hit</th><th>95% interval</th><th className="num">top-3</th></tr></thead>
        <tbody>{bands.map(band => <tr key={`${band.low}-${band.high}`}><td>{pct(band.low)} – {pct(band.high)}</td><td className="num">{num(band.n)}</td><td className="num">{pct(band.mean_probability)}</td>
          <td className="num"><b>{pct(band.hit1)}</b></td><td><small>[{pct(band.hit1_ci?.[0])} – {pct(band.hit1_ci?.[1])}]</small></td><td className="num">{pct(band.hit3)}</td></tr>)}
          </tbody></table></div>
      <div className="pg-table-scroll"><table className="pg-table">
        <thead><tr><th>engines agreeing on the top score</th><th className="num">rows</th><th className="num">exact hit</th><th>95% interval</th><th>reading</th></tr></thead>
        <tbody>{agreement.map(row => <tr key={`agree-${row.agrees}`} className="pg-total"><td>{row.agrees} of {payload.engines.length - 2} engines{row.agrees === 0 ? ' (no agreement)' : ''}</td><td className="num">{num(row.n)}</td>
            <td className="num"><b>{pct(row.hit1)}</b></td><td><small>[{pct(row.hit1_ci?.[0])} – {pct(row.hit1_ci?.[1])}]</small></td>
            <td><small>{row.agrees <= 1 ? 'below the always-play baseline — treat as no signal' : row.agrees >= 3 ? 'the band where the pick is worth writing down' : 'thin evidence either way'}</small></td></tr>)}
        </tbody></table></div>
      <footer className="pg-foot"><Award size={13}/>Actionable reading: when at most one engine agrees, the pick almost never lands; from three agreeing engines upward the exact hit rate reaches the mid-teens. That is a statement about this archive, not a guarantee.</footer>
    </section>
  </div>;
}

const WEIGHT_COLORS = {poisson:'#7c5cc4', market:'#4f9a72', elo:'#d1a04a', prior:'#9a86c9', markov:'#c9738c', knn:'#5c9ec4', hit_blend:'#4f9a72', blend:'#7c5cc4'};

// "Reliability — does the probability mean anything?": claimed probability against the hit rate that
// followed, with the diagonal a perfect calibration would sit on. Same chart as the standalone board.
function ReliabilityChart({rows}){
  const width = 540, height = 250, pad = 42;
  const lows = rows.map(row => Math.min(row.claimed || 0, row.observed || 0));
  const highes = rows.map(row => Math.max(row.claimed || 0, row.observed || 0));
  const top = Math.max(...highes, 20) * 1.08;
  const low = Math.max(0, Math.min(...lows) * 0.9);
  const span = Math.max(1e-6, top - low);
  const x = value => pad + (width - 2 * pad) * ((value - low) / span);
  const y = value => height - pad - (height - 2 * pad) * ((value - low) / span);
  return <svg viewBox={`0 0 ${width} ${height}`} className="pg-chart" role="img" aria-label="Claimed probability against observed hit rate">
    <rect x={pad} y={pad} width={width - 2 * pad} height={height - 2 * pad} fill="none" stroke="var(--line)"/>
    {[0.25, 0.5, 0.75].map(fraction => <g key={fraction}>
      <line x1={x(low + span * fraction)} y1={pad} x2={x(low + span * fraction)} y2={height - pad} stroke="var(--line)" strokeDasharray="3 4"/>
      <line x1={pad} y1={y(low + span * fraction)} x2={width - pad} y2={y(low + span * fraction)} stroke="var(--line)" strokeDasharray="3 4"/>
      <text x={x(low + span * fraction)} y={height - pad + 13} textAnchor="middle" className="pg-chart-label">{pct(low + span * fraction, 0)}</text>
      <text x={pad - 7} y={y(low + span * fraction) + 3} textAnchor="end" className="pg-chart-label">{pct(low + span * fraction, 0)}</text>
    </g>)}
    <line x1={x(low)} y1={y(low)} x2={x(top)} y2={y(top)} stroke="#a99cc4" strokeWidth="1.6" strokeDasharray="5 5"/>
    {rows.map(row => <line key={`guide-${row.claimed}`} x1={x(row.claimed)} y1={y(row.claimed)} x2={x(row.claimed)} y2={y(row.observed)} stroke="#e2dcea" strokeWidth="1"/>)}
    <polyline fill="none" stroke="#7c5cc4" strokeWidth="2.2" points={rows.map(row => `${x(row.claimed)},${y(row.observed)}`).join(' ')}/>
    {rows.map(row => <circle key={row.claimed} cx={x(row.claimed)} cy={y(row.observed)} r={row.n > 100 ? 4.6 : 3.4} fill="#7ce0b0" stroke="#4f9a72" strokeWidth="1">
      <title>{`claimed ${pct(row.claimed)} · observed ${pct(row.observed)} · ${num(row.n)} rows`}</title>
    </circle>)}
    <text x={width / 2} y={height - 10} textAnchor="middle" className="pg-chart-label">claimed probability of the pick (%)</text>
    <text x={14} y={height / 2} textAnchor="middle" transform={`rotate(-90 14 ${height / 2})`} className="pg-chart-label">observed exact-hit rate (%)</text>
  </svg>;
}

// "Mixture weights through the run": one line per engine across every matchday refit.
function WeightsChart({history,keys,payload}){
  const width = 540, height = 260, pad = 42;
  const count = history.length;
  const x = index => pad + (count <= 1 ? 0 : (width - 2 * pad) * (index / (count - 1)));
  const y = value => height - pad - (height - 2 * pad) * Math.max(0, Math.min(1, value));
  const vectors = name => history.map(item => (name === 'hit_blend' ? item.hit_weights : item.weights) || {});
  return <>
    <svg viewBox={`0 0 ${width} ${height}`} className="pg-chart" role="img" aria-label="Mixture weights through the run">
      <rect x={pad} y={pad} width={width - 2 * pad} height={height - 2 * pad} fill="none" stroke="var(--line)"/>
      {[0.25, 0.5, 0.75, 1].map(fraction => <g key={fraction}>
        <line x1={pad} y1={y(fraction)} x2={width - pad} y2={y(fraction)} stroke="var(--line)" strokeDasharray="3 4"/>
        <text x={pad - 7} y={y(fraction) + 3} textAnchor="end" className="pg-chart-label">{pct(100 * fraction, 0)}</text>
      </g>)}
      {keys.map(key => {
        const points = vectors('blend').map((vector, index) => `${x(index)},${y(vector[key] || 0)}`).join(' ');
        return <polyline key={key} fill="none" stroke={WEIGHT_COLORS[key] || '#93a4c4'} strokeWidth="1.6" strokeOpacity="0.75" points={points}><title>{`${engineLabel(payload, key)} — log-loss mixture`}</title></polyline>;
      })}
      {keys.map(key => {
        const points = vectors('hit_blend').map((vector, index) => `${x(index)},${y(vector[key] || 0)}`).join(' ');
        return <polyline key={`hit-${key}`} fill="none" stroke={WEIGHT_COLORS[key] || '#93a4c4'} strokeWidth="2.6" points={points}><title>{`${engineLabel(payload, key)} — competition mixture`}</title></polyline>;
      })}
      <text x={width / 2} y={height - 10} textAnchor="middle" className="pg-chart-label">{history.length ? `${history[0].label} → ${history[history.length - 1].label} (every matchday refit)` : 'no refits'}</text>
    </svg>
    <div className="pg-pills">{keys.map(key => <span key={key} className="pill" style={{borderColor:WEIGHT_COLORS[key] || '#93a4c4'}}>{engineLabel(payload, key)}</span>)}
      <span className="pg-muted">thick = competition mixture · thin = log-loss mixture</span></div>
  </>;
}

function ReliabilityAndWeights({payload}){
  const backtest = payload.backtest || {};
  const rows = backtest.reliability || [];
  const full = backtest.weight_history || [];
  const history = full.slice(-60);
  const keys = engineOrder(payload).filter(key => !['hit_blend','blend'].includes(key)).filter(key => history.some(item => (item.hit_weights || {})[key] || (item.weights || {})[key]));
  const last = history[history.length - 1] || {};
  const first = history[0] || {};
  return <div className="pg-two">
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">RELIABILITY</span><h2>Reliability — does the probability mean anything?</h2>
        <p>Every dot is a band of the blend's own probability for its pick, against the exact-hit rate that followed it. The dashed diagonal is perfect calibration; the table underneath shows the gap in points for each band.</p></div><Scale size={18}/></header>
      {rows.length ? <ReliabilityChart rows={rows}/> : <p className="note">No reliability table in this build.</p>}
      <div className="pg-table-scroll"><table className="pg-table">
        <thead><tr><th>claimed</th><th className="num">rows</th><th className="num">observed</th><th>gap</th></tr></thead>
        <tbody>{rows.map(row => <tr key={row.claimed}><td>{pct(row.claimed)}</td><td className="num">{num(row.n)}</td><td className="num"><b>{pct(row.observed)}</b></td>
          <td><small>{row.observed >= row.claimed ? '+' : ''}{fixed(row.observed - row.claimed, 1)} points</small></td></tr>)}</tbody></table></div>
      <footer className="pg-foot"><Info size={13}/>If the blend is honest the dots sit near the dashed diagonal: fixtures it calls 12% should come in about 12% of the time. Observed rates at the top of the range rest on very few rows, so the intervals in the scorecard widen accordingly.</footer>
    </section>
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">MIXTURE WEIGHTS THROUGH THE RUN</span><h2>Mixture weights through the run — last {history.length} refits</h2>
        <p>Weights are refitted every matchday from trailing rows only (window {num(backtest.window)}). The hit-tuned mixture is the noisier of the two, which is why it is reported next to the log-loss mixture rather than instead of it.</p></div><Layers size={18}/></header>
      {history.length ? <WeightsChart history={history} keys={keys} payload={payload}/> : <p className="note">No weight history in this build.</p>}
      {history.length > 0 && (() => {
        return <div className="pg-table-scroll"><table className="pg-table">
          <thead><tr><th>engine</th><th className="num">{first.label} (competition)</th><th className="num">latest</th><th className="num">latest log-loss</th><th>move</th></tr></thead>
          <tbody>{keys.map(key => {
            const start = (first.hit_weights || {})[key] || 0, end = (last.hit_weights || {})[key] || 0;
            return <tr key={key}><td><b>{engineLabel(payload, key)}</b></td><td className="num">{pct(100 * start)}</td><td className="num">{pct(100 * end)}</td>
              <td className="num">{pct(100 * ((last.weights || {})[key] || 0))}</td>
              <td><small>{end >= start ? '▲' : '▼'} {fixed(Math.abs(end - start) * 100, 1)} points since {first.label}</small></td></tr>;
          })}</tbody></table></div>;
      })()}
      <footer className="pg-foot"><Sigma size={13}/>One line per engine, refit at the start of every matchday on trailing rows only (window {num(backtest.window)}). Latest competition vector: {weightList(payload, last?.hit_weights || backtest.hit_weights).join(' · ')}.</footer>
    </section>
  </div>;
}

export default function PlaygroundEngines(){
  const {payload,loading,error,source} = usePlayground();
  const [rowTeam,setRowTeam] = useState('');
  if(loading && !payload) return <div className="replay-lab"><PageHead eyebrow="PLAYGROUND" title="Score engines & explainability" description="Loading the engine evidence…"/><Loading text="Reading the walk-forward evidence…"/></div>;
  if(!payload) return <div className="replay-lab"><PageHead eyebrow="PLAYGROUND" title="Score engines & explainability" description="This workspace reads /api/playground from the league DNA server."/>
    <ErrorBanner error={error || 'The payload is not available in this installation.'}/>
    <div className="notice warning"><Info size={17}/><span>Start the app and open <b>/playground-engines</b>, or read the standalone board in the <b>FT score Prediction Playground</b> folder. Engine evidence is never invented offline.</span></div></div>;
  const rows = payload.target?.rows || [];
  const homeRows = rows.filter(row => row.venue === 'H');
  const selected = rows.find(row => row.team === rowTeam) || homeRows[0] || rows[0];
  const blend = (payload.target?.competition_weights && selected?.hit_blend) || selected?.blend || {};
  const pick = selected?.competition?.pick || blend?.top?.[0]?.score || '—';
  const protocol = payload.backtest?.protocol || {};
  return <div className="replay-lab playground-engines">
    <PageHead eyebrow="FT SCORE PREDICTION PLAYGROUND" title="Score engines & explainability"
      description={<>The engines behind every pick on the live board: what each one assumes, how it scored in the walk-forward run, its mixture weight, and the exact path from its inputs to its top score for the incoming matchday.</>}>
      <Status tone={source === 'live' ? 'green' : 'amber'}>{source === 'live' ? 'Evidence live from the collector' : 'Evidence from the last published payload'}</Status>
    </PageHead>
    <Chips payload={payload} source={source} error={error}/>
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">THE ENGINES</span><h2>Six models, two mixtures, one ledger</h2>
        <p>Each engine answers the same question — the probability of every full-time score — from a different kind of evidence: frequency, rating, decayed attack/defence, recorded sequence, historical similarity and captured market prices. The two mixtures then decide how much each one's answer is worth.</p></div><Layers size={18}/></header>
      <EngineRoster payload={payload}/>
      <footer className="pg-foot"><Info size={13}/>Coverage is the number of walk-forward rows where the engine produced a grid; the market engine only answers fixtures with a captured catalogue, which is why its row count is lower.</footer>
    </section>
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">ENGINE SCORECARD · WALK-FORWARD EVIDENCE</span><h2>How each engine actually did, outside the fitting half</h2>
        <p>Every number comes from the untouched part of the run. <b>exact hit</b> means the engine's top score was the recorded FT score; <b>top-3</b> means the recorded score was among its three most likely; <b>log loss</b> and <b>1X2</b> describe the whole distribution. <b>shrink</b> is how far an engine was pulled toward the league prior.</p></div><Activity size={18}/></header>
      <Scorecard payload={payload}/>
      <footer className="pg-foot"><ShieldCheck size={13}/>Intervals overlap wherever engines are close — that is the correct reading of small differences on a few hundred fixtures. Blend gain over the champion engine: {fixed(backtestGain(payload), 3)} log loss.</footer>
    </section>
    <section className="panel">
      <header className="pg-head"><div><span className="replay-kicker">EXPLAINABILITY SPACE</span><h2>How this pick was reached</h2>
        <p>Choose an incoming fixture. Each engine then shows its chain (weight, the probability it gives to the blend's pick, shrinkage and sequence exponent), its full score grid, the input values that produced it and, where the engine reads history, the recorded evidence it used.</p></div><Microscope size={18}/></header>
      <div className="pg-fixture-picker">{rows.map(row => <button key={row.team} className={`pg-chip-button ${selected?.team === row.team ? 'active' : ''}`} onClick={() => setRowTeam(row.team)}>
        <TeamBadge name={row.team} small/><span>{row.team}</span><i>{row.venue === 'H' ? `vs ${row.opponent}` : `at ${row.opponent}`}</i></button>)}</div>
      {selected && <>
        <div className="pg-explain-head">
          <div><h3>{selected.team} vs {selected.opponent} <span className="badge">{selected.venue === 'H' ? 'home frame' : 'away frame'}</span> <span className={`badge ${selected.competition?.confidence || 'thin'}`}>{selected.competition?.confidence || 'thin'}</span></h3>
            <p className="note">Competition blend plays <b>{pick}</b> at {pct(selected.competition?.probability)} · runner-up {selected.competition?.runner_up?.[0] || '—'} at {pct(selected.competition?.runner_up?.[1])} · third {selected.competition?.third?.[0] || '—'} at {pct(selected.competition?.third?.[1])}. {selected.blend?.top?.[0]?.score && selected.blend.top[0].score !== pick ? `The log-loss blend reads ${selected.blend.top[0].score} instead, so this fixture is genuinely close between near-equal models.` : 'Both blends agree on this fixture.'}</p></div>
          <div className="pg-pills">{((selected.hit_blend?.top) || []).map((item, index) => <span key={item.score} className={`pill ${index === 0 ? 'first' : ''}`}>{item.score} <b>{pct(item.probability)}</b></span>)}</div>
        </div>
        {selected.context?.length > 0 && <p className="note pg-context"><ListTree size={13}/>Recent FT sequence in the row team's own frame: {selected.context.map((symbol, index) => <span className="pill" key={index}>{symbol}</span>)}</p>}
        <EngineDetail payload={payload} row={selected} pick={pick} blend={selected.hit_blend || selected.blend}/>
      </>}
    </section>
    <LedgerAndBands payload={payload}/>
    <ReliabilityAndWeights payload={payload}/>
    <section className="panel pg-prose">
      <header className="pg-head"><div><span className="replay-kicker">PROTOCOL &amp; CAVEATS</span><h2>What the walk-forward run guarantees</h2></div><FlaskConical size={18}/></header>
      <div className="pg-prose-body">
        <div><Layers size={16}/><h3>Chronology is enforced</h3><p>{protocol.note || 'The sequence library and Elo table are released matchday by matchday; Poisson is refitted on earlier rows only; market grids come from catalogues captured before kick-off; shrinkage and mixture weights are fitted on the first part of the run and the headline numbers come from the rest.'}</p></div>
        <div><Compass size={16}/><h3>Two honest objectives</h3><p>Exact hits and log loss disagree about which engine deserves the weight. The playground publishes both mixtures, both ledgers and the intervals, so a reader can see that disagreement instead of receiving a single number with no alternatives.</p></div>
        <div><Info size={16}/><h3>What it is not</h3><p>Exact-score repetition is rare and the per-team ledgers are short. Nothing here establishes a betting edge, and no wagering action is offered. The engines read recorded Betika League history plus captured public market catalogues.</p></div>
        <div><RefreshCw size={16}/><h3>Freshness</h3><p>Payload built {timeLabel(payload.meta?.generated_at)} · key {payload.meta?.key} · window {num(payload.meta?.window)} · {num(payload.backtest?.market_rows)} fixtures had a captured market catalogue. The service rebuilds at most every {num(240)} seconds and publishes a new payload only when the target matchday or the archive moves.</p></div>
      </div>
    </section>
  </div>;
}

function backtestGain(payload){
  const gain = payload.backtest?.blend_gain;
  return typeof gain === 'number' ? gain : (gain?.logloss ?? 0);
}
