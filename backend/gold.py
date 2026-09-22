"""Correct score Gold v2: full prefix first, then trim the oldest days.

Uses literal Betika FT home:away pairs. No mixed-length candidate sets, no
one-result matches, no fuzzy scoring, and no future-score predictions.
"""
from __future__ import annotations
from collections import defaultdict
import json
import time

ENGINE_VERSION = "correct-score-gold-prefix-v2"
MINIMUM_RESULTS = 2
FALLBACK_FROM_UPCOMING = 5
MAX_SHOWN = 20


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def rebuild_score_blueprints(store, season, matches=None):
    """Preserve verified FT vectors and legacy team-relative values per team.
    The rebuilt comparator reads only home_away.
    Called within the same transaction as final-score / four-pattern updates.
    """
    if matches is None:
        matches = store.all("SELECT * FROM matches WHERE season=? AND status='final' ORDER BY day", (season,))
    teams = sorted({name for m in matches for name in (m["home"], m["away"])})
    for team in teams:
        by_day = {m["day"]: m for m in matches if team in (m["home"], m["away"])}
        cards, home_away, team_relative = [], [], []
        for day in range(1, 31):
            match = by_day.get(day)
            if match is None:
                cards.append({"day": day, "ft": None, "relative_score": None, "match_id": None})
                home_away.append(None); team_relative.append(None)
                continue
            is_home = team == match["home"]
            ft = f'{match["ft_home"]}:{match["ft_away"]}'
            relative = ft if is_home else f'{match["ft_away"]}:{match["ft_home"]}'
            home_away.append(ft); team_relative.append(relative)
            cards.append({"day": day, "ft": ft, "relative_score": relative, "ht": f'{match["ht_home"]}:{match["ht_away"]}',
                          "home": match["home"], "away": match["away"], "venue": "H" if is_home else "A",
                          "opponent": match["away"] if is_home else match["home"], "match_id": match["id"]})
        store.db.execute("""INSERT INTO score_blueprints VALUES(?,?,?,?,?,?,?)
          ON CONFLICT(season,team) DO UPDATE SET home_away=excluded.home_away,
          team_relative=excluded.team_relative,cards=excluded.cards,played=excluded.played,updated_at=excluded.updated_at""",
                         (season, team, compact(home_away), compact(team_relative), compact(cards), len(by_day), time.time()))



class WindowIndex:
    """Exact, length-specific historical windows, shared across all 16 teams.

    Building each length once avoids rescanning every historical row for every
    team while preserving full-prefix-first search and accurate window counts.
    """
    def __init__(self, history):
        self.history = history
        self.by_length = {}
        self.counts = {}

    def lookup(self, query, team=None):
        length = len(query)
        if length < MINIMUM_RESULTS or length > 30 or None in query:
            return [], 0
        if length not in self.by_length:
            table = defaultdict(list)
            counts = defaultdict(int)
            for row in self.history:
                tokens = row['tokens'][:30]
                for start in range(len(tokens)-length+1):
                    key = tuple(tokens[start:start+length])
                    if None in key:
                        continue
                    table[key].append({'team': row['team'], 'season': row['season'],
                                       'start': start+1, 'end': start+length, 'length': length,
                                       'cards': row.get('cards', []), 'exact': True})
                    counts[row['team']] += 1
            self.by_length[length] = table
            self.counts[length] = counts
        candidates = self.by_length[length].get(tuple(query), [])
        if team is not None:
            candidates = [c for c in candidates if c['team'] == team]
            scanned = self.counts[length].get(team, 0)
        else:
            candidates = list(candidates)
            scanned = sum(self.counts[length].values())
        return candidates, scanned


def trace_full_sequence(current, index, upcoming_day, team=None):
    """Stop at the FIRST length with an exact match, independently per team.

    MD3 compares current MD1-2. Every new final round appends to that prefix.
    From upcoming MD5 only: if the full prefix has zero hits, remove MD1,
    then MD2, etc., stopping immediately on a hit or at the two-result floor.
    """
    n = len(current)
    out = {'status': 'waiting', 'ready': False, 'reason': None,
           'full_length': n, 'current_start': 1, 'current_end': n, 'length': n,
           'trimmed_count': 0, 'match_count': 0, 'full_match_count': 0,
           'windows_scanned': 0, 'candidates': [], 'attempts': []}
    if upcoming_day < 3:
        out['reason'] = 'before_matchday_3'
        return out
    if n < MINIMUM_RESULTS:
        out['reason'] = 'need_first_two_rounds'
        return out
    if None in current:
        out['reason'] = 'missing_current_scores'
        return out
    out['ready'] = True
    starts = range(1, n) if upcoming_day >= FALLBACK_FROM_UPCOMING else [1]
    for current_start in starts:
        query = current[current_start-1:]
        candidates, scanned = index.lookup(query, team)
        out['windows_scanned'] += scanned
        out['attempts'].append({'current_start': current_start, 'current_end': n,
                                'length': len(query), 'match_count': len(candidates),
                                'windows_scanned': scanned})
        if current_start == 1:
            out['full_match_count'] = len(candidates)
        out.update(current_start=current_start, length=len(query), trimmed_count=current_start-1)
        if candidates:
            candidates.sort(key=lambda c: (-c['season'], c['start'], c['team']))
            out.update(status='full' if current_start == 1 else 'shortened', match_count=len(candidates),
                       candidates=[{**c, 'current_start': current_start, 'current_end': n}
                                   for c in candidates[:MAX_SHOWN]])
            return out
    out['status'] = 'no_match'
    out['reason'] = 'two_result_floor' if upcoming_day >= FALLBACK_FROM_UPCOMING else 'full_prefix_only_before_md5'
    return out


def display_cards(row, end=30):
    if row is None:
        return [{'day': d, 'code': None, 'score': None, 'label': '—', 'ft': None,
                 'match_id': None} for d in range(1, end+1)]
    cards = json.loads(row['cards'])
    tokens = json.loads(row['home_away'])
    return [{**card, 'code': token, 'score': token, 'label': 'CS' if token is not None else '—'}
            for card, token in zip(cards[:end], tokens[:end])]


def compare_correct_scores(store, scope='all'):
    if scope not in {'all', 'same'}:
        raise ValueError('Unknown historical-team scope')
    began = time.perf_counter()
    with store.lock:
        up = store.get_meta('upcoming', {})
        season = up.get('season')
        upcoming_day = up.get('day') or 0
        from .workspace import resolved_fixtures
        fixtures = resolved_fixtures(store, up)
        recorded_names = [name for fixture in fixtures for name in (fixture['home'], fixture['away'])]
        teams = list(dict.fromkeys(recorded_names + up.get('teams', [])))
        # Exactly the same contiguous, closed MD1-based prefix as Pattern Comparator.
        n = min(store.prefix_length(season) if season else 0, max(0, upcoming_day-1))
        closed = [r['day'] for r in store.all(
            "SELECT day FROM matchdays WHERE season=? AND status='complete' AND day<? ORDER BY day",
            (season or 0, upcoming_day))]
        key = compact([ENGINE_VERSION, season, upcoming_day, n, scope, teams, closed])
        if key in store.memory_cache:
            return {**store.memory_cache[key], 'cached': True,
                    'compute_ms': round((time.perf_counter()-began)*1000, 2)}
        disk = store.one('SELECT data FROM comparisons WHERE cache_key=? AND revision=?', (key, store.fp_revision))
        if disk:
            data = json.loads(disk['data'])
            store.memory_cache[key] = data
            if len(store.memory_cache) > 24:
                store.memory_cache.pop(next(iter(store.memory_cache)))
            return {**data, 'cached': True, 'compute_ms': round((time.perf_counter()-began)*1000, 2)}
        historical = store.all('SELECT * FROM score_blueprints WHERE season<? ORDER BY season DESC,team', (season or 0,))
        history = [{'team': r['team'], 'season': r['season'], 'tokens': json.loads(r['home_away']),
                    'cards': display_cards(r)} for r in historical]
        forming = {r['team']: r for r in store.all('SELECT * FROM score_blueprints WHERE season=?', (season or 0,))}
        index = WindowIndex(history)
        result = []
        for team in teams:
            full_cards = display_cards(forming.get(team), n)
            sequence = [card['code'] for card in full_cards]
            trace = trace_full_sequence(sequence, index, upcoming_day, team if scope == 'same' else None)
            used_start = trace['current_start']
            result.append({'team': team, **trace, 'full_cards': full_cards,
                           'cards': full_cards[used_start-1:], 'discarded_cards': full_cards[:used_start-1],
                           'pattern': sequence[used_start-1:]})
        data = {'engine': ENGINE_VERSION, 'method': 'full_prefix_first_then_trim_oldest',
                'kind': 'correct_score', 'score_orientation': 'home_away', 'scope': scope,
                'current_season': season, 'upcoming_day': upcoming_day,
                'upcoming_start_time': up.get('start_time'), 'length': n, 'current_end': n,
                'closed_days': closed, 'closed_after_prefix': [d for d in closed if d > n],
                'ready': upcoming_day >= 3 and n >= MINIMUM_RESULTS,
                'fallback_from_upcoming': FALLBACK_FROM_UPCOMING, 'minimum_length': MINIMUM_RESULTS,
                'fallback_enabled': upcoming_day >= FALLBACK_FROM_UPCOMING,
                'teams': result, 'match_count': sum(t['match_count'] for t in result),
                'windows_scanned': sum(t['windows_scanned'] for t in result),
                'full_match_teams': sum(t['status']=='full' for t in result),
                'shortened_match_teams': sum(t['status']=='shortened' for t in result),
                'unmatched_teams': sum(t['status']=='no_match' for t in result),
                'historical_blueprints': len(historical), 'max_shown_per_team': MAX_SHOWN,
                'revision': store.fp_revision, 'computed_at': time.time(), 'cached': False,
                'compute_ms': round((time.perf_counter()-began)*1000, 2),
                'notice': 'Counts describe exact historical alignments of each displayed sequence, not future-score probabilities.'}
        store.memory_cache[key] = data
        if len(store.memory_cache) > 24:
            store.memory_cache.pop(next(iter(store.memory_cache)))
        store.db.execute('INSERT OR REPLACE INTO comparisons VALUES(?,?,?,?)',
                         (key, store.fp_revision, compact(data), time.time()))
        store.db.execute('DELETE FROM comparisons WHERE cache_key NOT IN (SELECT cache_key FROM comparisons ORDER BY created_at DESC LIMIT 24)')
        return data
