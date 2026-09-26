// Lightweight helpers for the Betika Edge workspace.
// The backend exposes JSON; this module just holds the API paths and
// the small helpers that keep the React component tidy.
export const BETIKA_PATHS = {
  status:        '/api/betika-edge/status',
  refresh:       '/api/betika-edge/refresh',
  teams:         '/api/betika-edge/teams',
  matches:       '/api/betika-edge/matches',
  matchday:      '/api/betika-edge/matchday',
  markets:       '/api/betika-edge/markets',
  noVig:         '/api/betika-edge/no-vig',
  crossFlags:    '/api/betika-edge/cross-flags',
  h2h:           '/api/betika-edge/h2h',
  predict:       '/api/betika-edge/predict',
  valueBets:     '/api/betika-edge/value-bets',
  twoMarket:     '/api/betika-edge/two-market',
  ledger:        '/api/betika-edge/ledger',
  place:         '/api/betika-edge/place',
  settle:        '/api/betika-edge/settle',
  settingsGet:   '/api/betika-edge/settings',
  settingsSet:   '/api/betika-edge/settings',
  seedH2H:       '/api/betika-edge/seed-h2h',
};

export const LEAGUE_HEADERS = {'X-Requested-With': 'LeagueDNA'};

export const Pct = (v, digits = 1) => v == null || Number.isNaN(v) ? '—' :
  `${(Number(v) * (Number(v) > 1 ? 1 : 1)).toFixed(digits)}%`;

export const Fixed = (v, digits = 2) => v == null || Number.isNaN(v) ? '—' : Number(v).toFixed(digits);

export const Money = (v) => v == null || Number.isNaN(v) ? '—' :
  (Number(v) >= 0 ? '+' : '') + Number(v).toFixed(2);

export function buildMatchId(season, matchday, home, away) {
  return `S${season}-MD${matchday}-${home}-vs-${away}`;
}
