// Data layer for the Head-to-head matchup DNA workspace.
//
// Two reads, deliberately separate:
//   * /api/matchups/tick   — a few hundred bytes, every few seconds. It carries the board key, the
//                            lock facts of the matchday to play and the graded counts. The full
//                            payload is only pulled when the board key moves.
//   * /api/matchups        — the board itself: recorded HT/FT per team and matchday, with the frozen
//                            half call locked underneath it.
// The historical analysis reads /api/matchups/pairs (the dropdown) and /api/matchups/pair (one pair),
// on demand, because those are queries rather than a live board.
import {useCallback, useEffect, useRef, useState} from 'react';

export const BOARD_DAYS = 30;
export const TICK_SECONDS = 4;
export const POLL_SECONDS = 20;

export const HALVES = ['1H', '2H', 'EQ'];
export const HALF_LABEL = {'1H': '1st half', '2H': '2nd half', 'EQ': 'Equal'};
export const HALF_SHORT = {'1H': '1ST HALF', '2H': '2ND HALF', 'EQ': 'EQUAL'};

export const timeOf = t => t
  ? new Date(t * 1000).toLocaleTimeString('en-GB', {timeZone: 'Africa/Nairobi', hour: '2-digit', minute: '2-digit', second: '2-digit'})
  : '—';

export const dateOf = t => t
  ? new Date(t * 1000).toLocaleString('en-GB', {timeZone: 'Africa/Nairobi', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit'})
  : '—';

export function sinceLabel(then, now){
  if(!then) return 'not yet';
  const seconds = Math.max(0, Math.round((now || Date.now() / 1000) - then));
  if(seconds < 60) return `${seconds}s ago`;
  if(seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export function leadLabel(seconds){
  if(seconds == null) return 'lead not recorded';
  const value = Math.abs(Math.round(seconds));
  const text = value >= 3600 ? `${(value / 3600).toFixed(1)}h` : value >= 60 ? `${Math.floor(value / 60)}m ${value % 60}s` : `${value}s`;
  return seconds < 0 ? `locked ${text} after kick-off` : `locked ${text} before kick-off`;
}

// The recorded HT/FT of a call, once the matchday has been played.
export function resultOf(call){
  if(!call) return null;
  if(call.ht && call.ft) return {ht: call.ht, ft: call.ft, half: call.actual, half_label: call.actual_label};
  return null;
}

// One column per matchday: what happened, and the frozen call that sat underneath it.
export function boardColumns(board, team){
  const row = (board?.teams || []).find(entry => entry.team === team) || {results: [], calls: []};
  const results = new Map((row.results || []).map(entry => [entry.day, entry]));
  const calls = new Map((row.calls || []).map(entry => [entry.day, entry]));
  const columns = [];
  for(let day = 1; day <= BOARD_DAYS; day += 1){
    const result = results.get(day) || null;
    const call = calls.get(day) || null;
    columns.push({
      day,
      played: Boolean(result),
      opponent: result?.opponent || call?.opponent || '',
      venue: result?.venue || '',
      ht: result?.ht || call?.ht || null,
      ft: result?.ft || call?.ft || null,
      actual: result?.half || call?.actual || null,
      first_half: result?.first_half ?? call?.first_half ?? null,
      second_half: result?.second_half ?? call?.second_half ?? null,
      pick: call?.pick || null,
      label: call?.label || null,
      probability: call?.probability ?? null,
      sample: call?.sample ?? null,
      basis: call?.basis || null,
      verdict: call?.verdict || (call ? 'pending' : null),
      source: call?.source || null,
      lead_seconds: call?.lead_seconds ?? null,
      frozen: Boolean(call),
      empty: !result && !call,
    });
  }
  return columns;
}

export function teamList(board){
  return (board?.teams || []).map(row => row.team);
}

export function seasonOptions(payload){
  const seasons = (payload?.ledger?.seasons || []).map(entry => entry.season);
  const current = payload?.season;
  return [...new Set([...(current ? [current] : []), ...seasons])];
}

// The beat that keeps the board in step: cheap tick, full payload only when something changed.
export async function fetchJson(url, options){
  const response = await fetch(url, {cache: 'no-store', ...options});
  if(!response.ok){
    let detail = `${response.status}`;
    try{ const body = await response.json(); detail = body.detail || detail; }catch{/* keep the status */}
    throw new Error(detail);
  }
  return response.json();
}

export function useMatchups(season){
  const [payload, setPayload] = useState(null);
  const [tick, setTick] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);
  const [received, setReceived] = useState(Date.now() / 1000);
  const [revision, setRevision] = useState(0);
  const boardKey = useRef('');
  const payloadRef = useRef(null);
  const busy = useRef(false);

  const pull = useCallback(async () => {
    if(busy.current) return;
    busy.current = true;
    try{
      const url = season ? `/api/matchups?season=${season}` : '/api/matchups';
      const body = await fetchJson(url);
      payloadRef.current = body;
      boardKey.current = body?.meta?.board_key || '';
      setPayload(body);
      setReceived(Date.now() / 1000);
      setError(null);
    }catch(problem){
      if(!payloadRef.current) setError(problem.message);
    }finally{
      busy.current = false;
      setLoading(false);
    }
  }, [season]);

  const beat = useCallback(async () => {
    if(!['http:', 'https:'].includes(location.protocol)) return;
    if(document.visibilityState === 'hidden') return;
    try{
      const info = await fetchJson('/api/matchups/tick');
      setTick(info);
      const moved = !season && info.board_key && info.board_key !== boardKey.current;
      if(!payloadRef.current || moved) await pull();
    }catch{/* a missed beat is not an error: the slow poll still runs */}
  }, [pull, season]);

  useEffect(() => {
    let alive = true;
    pull();
    const fast = setInterval(() => { if(alive) beat(); }, TICK_SECONDS * 1000);
    const slow = setInterval(() => { if(alive && document.visibilityState !== 'hidden') pull(); }, POLL_SECONDS * 1000);
    const onVisible = () => { if(document.visibilityState === 'visible') beat(); };
    document.addEventListener('visibilitychange', onVisible);
    return () => {alive = false; clearInterval(fast); clearInterval(slow); document.removeEventListener('visibilitychange', onVisible);};
  }, [beat, pull, revision]);

  return {payload, tick, error, loading, received, reload: () => setRevision(value => value + 1)};
}

// The dropdown and the analysis for the historical sub-workspace.
export function usePairs(query, limit = 500){
  const [pairs, setPairs] = useState([]);
  const [error, setError] = useState(null);
  useEffect(() => {
    let alive = true;
    const handle = setTimeout(async () => {
      try{
        const body = await fetchJson(`/api/matchups/pairs?limit=${limit}${query ? `&q=${encodeURIComponent(query)}` : ''}`);
        if(alive){ setPairs(body.pairs || []); setError(null); }
      }catch(problem){ if(alive && !pairs.length) setError(problem.message); }
    }, query ? 250 : 0);
    return () => {alive = false; clearTimeout(handle);};
  }, [query, limit, pairs.length]);
  return {pairs, error};
}

export function usePair(home, away, venue){
  const [analysis, setAnalysis] = useState(null);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if(!home || !away){ setAnalysis(null); return; }
    let alive = true;
    setLoading(true);
    fetchJson(`/api/matchups/pair?home=${encodeURIComponent(home)}&away=${encodeURIComponent(away)}&venue=${venue}`)
      .then(body => { if(alive){ setAnalysis(body.analysis); setError(null); } })
      .catch(problem => { if(alive) setError(problem.message); })
      .finally(() => { if(alive) setLoading(false); });
    return () => {alive = false;};
  }, [home, away, venue]);
  return {analysis, error, loading};
}
