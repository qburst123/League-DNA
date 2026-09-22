// Shared helpers for the FT score Prediction Playground workspaces.
// The payload is served by /api/playground (see backend/playground_service.py). Nothing here
// invents a number: every helper reads the walk-forward payload the server published.
import {useCallback, useEffect, useRef, useState} from 'react';

export const BOARD_DAYS = 30;

export function parseScore(symbol){
  const [home, away] = String(symbol || '').split(':');
  const forGoals = Number.parseInt(home, 10), against = Number.parseInt(away, 10);
  return [Number.isFinite(forGoals) ? forGoals : 0, Number.isFinite(against) ? against : 0];
}

export const pct = (value, digits = 1) => value == null || Number.isNaN(value) ? '—' : `${Number(value).toFixed(digits)}%`;
export const fixed = (value, digits = 3) => value == null || Number.isNaN(value) ? '—' : Number(value).toFixed(digits);
export const timeOf = t => t ? new Date(t * 1000).toLocaleTimeString('en-GB', {timeZone:'Africa/Nairobi', hour:'2-digit', minute:'2-digit', second:'2-digit'}) : '—';

// EXACT hit is green, the right side of the result amber, everything else red — the same marks on
// the prediction row and in the modal.
export function verdictOf(row){
  if(!row) return 'none';
  if(row.empty) return 'none';
  if(row.pending) return 'pending';
  if(row.verdict === 'hit') return 'hit';
  if(row.direction === 'hit') return 'direction';
  return 'miss';
}

export const VERDICT_LABEL = {hit:'exact', direction:'right result', miss:'wrong', pending:'awaiting result', none:'no pick'};
export const VERDICT_SHORT = {hit:'EXACT', direction:'DIR', miss:'WRONG', pending:'TO PLAY', none:'—'};

// One column per matchday (MD1–30). Every column carries the recorded FT score when it exists and
// the walk-forward prediction for the same matchday; the incoming matchday is the pending pick.
export const targetDays = payload => {
  const target = payload?.target || {};
  const days = Array.isArray(target.days) && target.days.length ? target.days : (target.day ? [target.day] : []);
  return [...days].sort((a, b) => a - b);
};

export function boardColumns(payload, scope, team){
  const live = payload?.live_board || {};
  const recorded = live.scores?.[team] || {};
  const ledger = new Map(((live.blends?.[scope]?.teams?.[team] || {}).rows || []).map(row => [row.day, row]));
  const upcoming = (payload?.target?.rows || []).filter(row => row.team === team);
  const columns = [];
  for(let day = 1; day <= BOARD_DAYS; day += 1){
    const graded = ledger.get(day);
    const score = recorded[String(day)] || graded?.actual || '';
    const next = upcoming.find(row => row.day === day);
    if(graded){
      columns.push({day, score, pick:graded.pick, probability:graded.probability, agrees:graded.agrees,
                    engines:graded.engines, confidence:graded.confidence, top:graded.top,
                    verdict:graded.verdict, direction:graded.direction, source:graded.source,
                    pending:false, empty:false});
    } else if(next && score){
      columns.push({day, score, pick:next.competition?.pick, probability:next.competition?.probability,
                    agrees:next.competition?.agreement, engines:next.competition?.engines, top:[],
                    verdict:'stale', direction:'', pending:false, stale:true, empty:false});
    } else if(next){
      const comp = next.competition || {};
      columns.push({day, score, pick:comp.pick, probability:comp.probability, agrees:comp.agreement,
                    engines:comp.engines, confidence:comp.confidence, top:(next.hit_blend?.top || []).map(item => item.score),
                    verdict:'pending', direction:'', pending:true, empty:false, event_id:next.event_id,
                    opponent:next.opponent, venue:next.venue, market:next.market_available});
    } else {
      columns.push({day, score, empty:!score, pending:false, verdict:'none', pick:''});
    }
  }
  return columns;
}

export const scopeOptions = payload => {
  const blends = payload?.live_board?.blends || {};
  return [
    {key:'hit_blend', label:'Competition blend', note:'engines mixed to maximise exact hits — the pick this board plays', available:Boolean(blends.hit_blend)},
    {key:'blend', label:'Log-loss blend', note:'the same engines mixed to minimise log loss — the calibrated cross-check', available:Boolean(blends.blend)},
  ];
};

export const summaryFor = (payload, scope, team) => {
  const blends = payload?.live_board?.blends || {};
  if(team) return blends?.[scope]?.teams?.[team]?.summary || null;
  return blends?.[scope]?.summary || payload?.live_board?.summary || null;
};

export function engineLabel(payload, key){
  const card = (payload?.engines || []).find(item => item.key === key);
  return card?.name || key;
}

export const engineOrder = payload => (payload?.engines || []).map(card => card.key);

export const weightList = (payload, weights) =>
  Object.keys(weights || {}).map(key => `${engineLabel(payload, key)} ${pct(100 * weights[key])}`);

/* ------------------------------------------------------------------ live payload polling
   The board refreshes itself in two speeds:

   * `/api/playground/tick` is a few hundred bytes and is polled every few seconds. It carries the
     board key — a signature of the ledger and the published matchday — so the page knows within a
     couple of seconds that a sheet has been locked or a result graded.
   * `/api/playground` is only fetched when that key moves (or on the slow heartbeat), and it costs
     a gzip round trip of a payload composed from the ledger, not a model run.

   Refreshing stops while the tab is hidden, so a background tab does not keep polling. */
export const TICK_SECONDS = 4;
export const POLL_SECONDS = 20;

export function usePlayground(){
  const [payload, setPayload] = useState(null);
  const [status, setStatus] = useState(null);
  const [tick, setTick] = useState(null);
  const [state, setState] = useState({loading:true, error:'', source:'waiting', received:0, refreshing:false});
  const [revision, setRevision] = useState(0);
  const etag = useRef('');
  const busy = useRef(false);
  const asked = useRef(0);
  const payloadRef = useRef(null);
  const boardKey = useRef('');

  const pull = useCallback(async (force) => {
    if(busy.current) return;
    if(!['http:', 'https:'].includes(location.protocol)){setState(s => ({...s, loading:false, source:'offline'})); return;}
    busy.current = true;
    setState(s => ({...s, refreshing:true}));
    try{
      const headers = etag.current && !force ? {'If-None-Match': etag.current} : {};
      const response = await fetch('/api/playground', {headers, cache:'no-store'});
      if(response.status === 304){
        setState(s => ({...s, loading:false, source:'live', refreshing:false, error:''}));
      } else if(response.ok){
        const next = await response.json();
        if(!next.live_board && !next.target) throw Error('The collector published an older playground payload. Rebuild it from the service.');
        boardKey.current = next.meta?.board_key || '';
        etag.current = response.headers.get('etag') || '';
        payloadRef.current = next;
        setPayload(next);
        setState(s => ({...s, loading:false, error:'', source:'live', received:Date.now()/1000, refreshing:false}));
      } else {
        throw Error(`the playground service answered ${response.status}`);
      }
      const probe = await fetch('/api/playground/status', {cache:'no-store'});
      if(probe.ok){
        const info = await probe.json();
        setStatus(info);
        const st = info.status || {};
        const stale = st.has_payload && st.current_key !== st.payload_key;
        if(stale && Date.now()/1000 - asked.current > 60 && st.status !== 'building'){
          asked.current = Date.now()/1000;
          fetch('/api/playground/rebuild', {method:'POST'}).catch(() => {});
        }
      }
    }catch(error){
      setState(s => ({...s, loading:s.loading && !payloadRef.current, refreshing:false, error:error.message}));
    }finally{busy.current = false;}
  }, []);

  // The fast poll: a tiny body that changes the moment the ledger does. The full payload is only
  // pulled when its board key moves, which is what makes a refresh feel instant.
  const beat = useCallback(async () => {
    if(!['http:', 'https:'].includes(location.protocol)) return;
    if(document.visibilityState === 'hidden') return;
    try{
      const response = await fetch('/api/playground/tick', {cache:'no-store'});
      if(!response.ok) return;
      const info = await response.json();
      setTick(info);
      const changed = info.board_key && info.board_key !== boardKey.current;
      if(changed || !payloadRef.current) await pull(false);
    }catch{/* a missed beat is not an error: the slow poll still runs */}
  }, [pull]);

  useEffect(() => {
    let alive = true;
    const slow = () => {if(alive && document.visibilityState !== 'hidden') pull(false);};
    const fast = () => {if(alive) beat();};
    pull(false).then(() => {boardKey.current = payloadRef.current?.meta?.board_key || '';});
    const beatTimer = setInterval(fast, TICK_SECONDS * 1000);
    const slowTimer = setInterval(slow, POLL_SECONDS * 1000);
    const onVisible = () => {if(document.visibilityState === 'visible') beat();};
    document.addEventListener('visibilitychange', onVisible);
    return () => {alive = false; clearInterval(beatTimer); clearInterval(slowTimer); document.removeEventListener('visibilitychange', onVisible);};
  }, [beat, pull, revision]);

  const refreshNow = useCallback(async () => {
    await fetch('/api/playground/rebuild', {method:'POST'}).catch(() => {});
    await pull(true);
    setRevision(value => value + 1);
  }, [pull]);

  const building = (status?.status?.status || '') === 'building';
  const waiting = (status?.status?.status || '') === 'waiting';
  const keyMoved = Boolean(status?.status?.has_payload) && status?.status?.current_key !== status?.status?.payload_key;
  return {payload, status, tick, building, waiting, keyMoved, refreshNow, ...state};
}

/* ------------------------------------------------------------------ the freeze, in words
   These helpers read the ledger's own bookkeeping. Nothing here estimates: the lock facts come
   from the sheet that was written when the matchday was announced, and the integrity share counts
   only the sheets that were genuinely locked before their first kick-off. */
export const lockInfo = payload => {
  const target = payload?.target || {};
  const integrity = payload?.ledger?.season_integrity || payload?.ledger?.integrity || {};
  return {lockedAt: target.locked_at, leadSeconds: target.lead_seconds, late: Boolean(target.late),
          source: target.source, onTimeShare: integrity.on_time_share, sheets: integrity.sheets,
          live: integrity.live, backfilled: integrity.backfilled, meanLead: integrity.mean_lead};
};

export const leadLabel = seconds => {
  if(seconds == null) return 'announced before this run started';
  const value = Math.abs(Math.round(seconds));
  const text = value >= 3600 ? `${Math.floor(value / 3600)}h ${Math.round((value % 3600) / 60)}m`
    : value >= 60 ? `${Math.floor(value / 60)}m ${value % 60}s` : `${value}s`;
  return seconds >= 0 ? `${text} before kick-off` : `${text} after kick-off`;
};

export const sinceLabel = (then, now) => {
  if(!then) return 'never';
  const value = Math.max(0, Math.round((now || Date.now() / 1000) - then));
  if(value < 60) return `${value}s ago`;
  if(value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s ago`;
  return `${Math.floor(value / 3600)}h ${Math.round((value % 3600) / 60)}m ago`;
};
