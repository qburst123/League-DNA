"""FT score Prediction Playground — prediction engines over the retained archive.

Every model returns a probability grid over the FT score from one team's point of
view (`for:against`, 0..7 each side). Predictions are always computed from matches
finalized strictly before the target matchday's first kick-off, so a backtest can
never read the future. No model is described as a certainty: each one publishes
its inputs, its arithmetic and its measured record.

Engines
-------
prior    league-wide empirical FT distribution (the honest baseline)
elo      chronological Elo ratings mapped to expected goals
poisson  decayed attack/defence Poisson fit with the Dixon-Coles low-score term
markov   per-team FT sequence model (orders 1-3, Krichevsky-Trofimov backoff)
knn      similarity search over historical team sequences
market   bookmaker correct-score odds, de-vigged and reconciled with 1X2/totals
blend    log-linear opinion pool of all available engines, fitted on past data

Pure standard library: the fit uses iterative proportional fitting, so no new
third-party dependency is required by the packaged application.
"""
from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

MAX_GOALS = 7
SIDES = MAX_GOALS + 1
CELLS = SIDES * SIDES
ALPHA = 0.5                      # Krichevsky-Trofimov smoothing for the sequence models
HALF_LIFE_MATCHDAYS = 45.0       # evidence decay half-life, in matchdays (a season is 30)
HALF_LIFE_DAYS = 120.0           # wall-clock fallback for engines that only see timestamps
ELO_K = 20.0
ELO_HOME = 40.0
ELO_SCALE = 400.0
ELO_BETA = 0.36                  # how strongly a rating gap moves expected goals
MIN_MATCHES = 3                  # evidence floor before a fitted model is offered
REFIT_EVERY = 3                  # matchdays between Poisson refits during a backtest
WEIGHT_WINDOW = 420              # trailing rows the walk-forward mixture refit looks back on


def score_symbol(goals_for, goals_against):
    return f"{int(goals_for)}:{int(goals_against)}"


def parse_symbol(symbol):
    home, away = str(symbol).split(":")
    return int(home), int(away)


def empty_grid():
    return [[0.0] * SIDES for _ in range(SIDES)]


def normalise(grid):
    total = sum(sum(row) for row in grid)
    if total <= 0:
        return uniform_grid()
    return [[value / total for value in row] for row in grid]


def uniform_grid():
    return [[1.0 / CELLS] * SIDES for _ in range(SIDES)]


def grid_of_symbols(counts):
    """Turn {symbol: weight} into a normalised grid, keeping unknown mass in a flat tail."""
    grid = empty_grid()
    known = 0.0
    for symbol, weight in counts.items():
        if weight <= 0:
            continue
        try:
            goals_for, goals_against = parse_symbol(symbol)
        except (ValueError, AttributeError):
            continue
        if goals_for < SIDES and goals_against < SIDES:
            grid[goals_for][goals_against] += weight
            known += weight
    if known <= 0:
        return uniform_grid()
    overflow = max(0.0, 1.0 - known)
    if overflow:
        corner = grid[MAX_GOALS][MAX_GOALS]
        grid[MAX_GOALS][MAX_GOALS] = corner + overflow
    return normalise(grid)


def top_cells(grid, limit=6):
    cells = [(score_symbol(for_goals, against), grid[for_goals][against])
             for for_goals in range(SIDES) for against in range(SIDES)]
    cells.sort(key=lambda item: (-item[1], item[0]))
    return [{"score": score, "probability": round(100.0 * probability, 2)} for score, probability in cells[:limit]]


def result_probabilities(grid):
    home = draw = away = 0.0
    for for_goals in range(SIDES):
        for against in range(SIDES):
            probability = grid[for_goals][against]
            if for_goals > against:
                home += probability
            elif for_goals == against:
                draw += probability
            else:
                away += probability
    return {"win": round(100.0 * home, 1), "draw": round(100.0 * draw, 1), "loss": round(100.0 * away, 1)}


def poisson_pmf(k, lam):
    return math.exp(-lam) * lam ** k / math.factorial(k)


def poisson_grid(lam_for, lam_against):
    grid = empty_grid()
    for goals_for in range(SIDES):
        p_for = poisson_pmf(goals_for, lam_for)
        for against in range(SIDES):
            grid[goals_for][against] = p_for * poisson_pmf(against, lam_against)
    # Re-normalise: the truncated tail (8+ goals) is folded into the last cell.
    tail = 1.0 - sum(sum(row) for row in grid)
    if tail > 0:
        grid[MAX_GOALS][MAX_GOALS] += tail
    return normalise(grid)


# --------------------------------------------------------------------------- data

@dataclass
class Match:
    index: int
    season: int
    day: int
    kickoff: float
    event_id: str
    home: str
    away: str
    ft_home: int
    ft_away: int
    final_at: float
    matchday_index: int = 0      # position of (season, day) on the fixture calendar


@dataclass
class Fixture:
    event_id: str
    season: int
    day: int
    home: str
    away: str
    kickoff: float
    start_label: str = ""


def load_history(store):
    """Finalized matches in fixture-calendar order.

    Sakata seasons are played back to back and a full season lasts roughly two hours of
    wall-clock time, so `start_time` is only useful for the handful of fixtures the app
    watched live. (season, day, id) is the one ordering that is complete and leak-free for
    every row, and every engine ages its evidence in matchdays rather than in days.
    """
    rows = store.all("SELECT id,season,day,event_id,home,away,start_time,status,ft_home,ft_away,final_at,final_source_hash "
                     "FROM matches WHERE status='final' AND ft_home IS NOT NULL AND ft_away IS NOT NULL "
                     "ORDER BY season,day,id")
    matchdays = {}
    history = []
    for row in rows:
        key = (row["season"], row["day"])
        if key not in matchdays:
            matchdays[key] = len(matchdays)
        history.append(Match(len(history), row["season"], row["day"], float(row["start_time"] or 0),
                             str(row["event_id"] or ""), row["home"], row["away"],
                             int(row["ft_home"]), int(row["ft_away"]),
                             float(row["final_at"] or row["start_time"] or 0), matchdays[key]))
    return history


def timeline(history):
    """Matchday keys in calendar order — the axis every recency weight is measured on."""
    keys = []
    for match in history:
        key = (match.season, match.day)
        if not keys or keys[-1] != key:
            keys.append(key)
    return keys


def before(history, season, day):
    """Every finalized match strictly earlier on the calendar than (season, day)."""
    return [match for match in history if (match.season, match.day) < (season, day)]


def completed_days(history):
    """(season, day) keys whose eight fixtures all have a recorded final score."""
    counts = Counter((match.season, match.day) for match in history)
    return counts


def current_targets(store, history, limit_days=None):
    """Every matchday that is set to be played, oldest first, as fixtures.

    The source publishes one round at a time but can open the next one before the previous one has
    been recorded — and it can skip a round the collector never saw as "upcoming". Predicting only
    the single next matchday then leaves a hole in the middle of the board, so every unplayed
    matchday the store knows about is predicted in matchday order. The first entry is the matchday
    that is about to be played, which is the one the board highlights.
    """
    upcoming = store.get_meta("upcoming", {})
    season = upcoming.get("season")
    if not season:
        newest = store.all("SELECT season FROM matches WHERE status!='final' ORDER BY season DESC, day ASC LIMIT 1")
        season = newest[0]["season"] if newest else None
    if not season:
        return []
    rows = store.all("SELECT id,season,day,event_id,home,away,start_time,status,ft_home,ft_away FROM matches "
                     "WHERE season=? AND status!='final' ORDER BY day, id", (season,))
    targets = []
    for row in rows:
        if row["status"] == "final" and row["ft_home"] is not None:
            continue
        targets.append(Fixture(str(row["event_id"] or row["id"]), row["season"], row["day"], row["home"], row["away"],
                               float(row["start_time"] or upcoming.get("start_time") or time.time())))
    if targets:
        if limit_days:
            days = []
            for fixture in targets:                      # keep whole matchdays, cap how many
                if fixture.day not in days:
                    if len(days) >= limit_days:
                        break
                    days.append(fixture.day)
            targets = [fixture for fixture in targets if fixture.day in days]
        return targets
    # Nothing scheduled: the newest matchday that still has unfinished fixtures.
    return []


def cutoff_for(targets, history):
    """First kick-off of the target matchday: nothing finalized at or after it may be used."""
    if not targets:
        return time.time()
    kickoffs = [fixture.kickoff for fixture in targets if fixture.kickoff]
    return min(kickoffs) if kickoffs else time.time()


# --------------------------------------------------------------------------- Elo

def _elo_update(ratings, match):
    """One World-Football-style Elo step, with a goal-margin multiplier."""
    home, away = match.home, match.away
    rating_home = ratings[home] + ELO_HOME
    rating_away = ratings[away]
    expected = 1.0 / (1.0 + 10 ** ((rating_away - rating_home) / ELO_SCALE))
    observed = 1.0 if match.ft_home > match.ft_away else 0.0 if match.ft_home < match.ft_away else 0.5
    margin = abs(match.ft_home - match.ft_away)
    weight = 1.0 if margin <= 1 else 1.5 if margin == 2 else (11.0 + margin) / 8.0
    change = ELO_K * weight * (observed - expected)
    ratings[home] += change
    ratings[away] -= change


def elo_frame(history, cutoff=None, limit=None):
    """Chronological Elo ratings, returned as the state just before `cutoff`/`limit`."""
    ratings = defaultdict(lambda: 1500.0)
    played = Counter()
    day_cursor = None
    snapshot = None
    for match in history:
        if cutoff is not None and match.kickoff and match.kickoff >= cutoff:
            break
        if limit is not None and (match.season, match.day) >= limit:
            break
        marker = (match.season, match.day)
        if marker != day_cursor:
            day_cursor = marker
            snapshot = dict(ratings)
        _elo_update(ratings, match)
        played[match.home] += 1
        played[match.away] += 1
    return {"ratings": dict(ratings), "previous": snapshot or dict(ratings), "played": dict(played)}


def elo_grid(rating_for, rating_against, home_edge, base_for, base_against):
    gap = (rating_for + home_edge - rating_against) / ELO_SCALE
    lam_for = base_for * math.exp(ELO_BETA * gap)
    lam_against = base_against * math.exp(-ELO_BETA * gap)
    return poisson_grid(lam_for, lam_against), lam_for, lam_against


# --------------------------------------------------------------------------- Poisson (Dixon-Coles)

def decay_weight(age_matchdays):
    """Evidence half-life on the fixture calendar (HALF_LIFE_MATCHDAYS matchdays)."""
    if age_matchdays <= 0:
        return 1.0
    return 0.5 ** (age_matchdays / HALF_LIFE_MATCHDAYS)


class PoissonStrength:
    """Iterative proportional fitting of attack/defence with an explicit home factor."""

    def __init__(self):
        self.attack = {}
        self.defence = {}
        self.home = 1.16
        self.base_for = 1.30
        self.base_against = 1.10
        self.rho = -0.03
        self.mu = 1.2
        self.matches = 0
        self.teams = []

    @classmethod
    def fit(cls, history, cutoff=None, iterations=12, max_history=4000, rho_steps=15):
        """Weighted iterative proportional fitting (Poisson MLE) with decayed evidence.

        Each parameter is re-estimated from the ratio of observed to expected goals
        while the others are held fixed, which is the standard multiplicative
        formulation: lam_for = mu * attack[for] * defence[against] * home_edge.
        """
        model = cls()
        usable = [match for match in history if cutoff is None or match.kickoff < cutoff]
        if max_history and len(usable) > max_history:
            usable = usable[-max_history:]
        if len(usable) < 40:
            return model
        teams = sorted({match.home for match in usable} | {match.away for match in usable})
        reference = max(match.matchday_index for match in usable)
        weights = [decay_weight(reference - match.matchday_index) for match in usable]
        attack = {team: 1.0 for team in teams}
        defence = {team: 1.0 for team in teams}
        home = 1.16
        mu = (sum(match.ft_home + match.ft_away for match in usable) / (2 * len(usable)))
        for _ in range(iterations):
            goal_for = defaultdict(float)
            expected_for = defaultdict(float)
            expected_against = defaultdict(float)
            goal_against = defaultdict(float)
            for match, weight in zip(usable, weights):
                goal_for[match.home] += weight * match.ft_home
                expected_for[match.home] += weight * mu * defence[match.away] * home
                goal_against[match.away] += weight * match.ft_home
                expected_against[match.away] += weight * mu * attack[match.home] * home
                goal_for[match.away] += weight * match.ft_away
                expected_for[match.away] += weight * mu * defence[match.home]
                goal_against[match.home] += weight * match.ft_away
                expected_against[match.home] += weight * mu * attack[match.away]
            for team in teams:
                if expected_for[team] > 0:
                    attack[team] = max(0.15, min(4.5, goal_for[team] / expected_for[team]))
                if expected_against[team] > 0:
                    defence[team] = max(0.15, min(4.5, goal_against[team] / expected_against[team]))
            home_goals = sum(weight * match.ft_home for match, weight in zip(usable, weights))
            home_expected = sum(weight * mu * attack[match.home] * defence[match.away]
                                for match, weight in zip(usable, weights))
            if home_expected > 0:
                home = max(0.75, min(1.9, home_goals / home_expected))
            total_goals = sum(weight * (match.ft_home + match.ft_away) for match, weight in zip(usable, weights))
            total_expected = sum(weight * (attack[match.home] * defence[match.away] * home + attack[match.away] * defence[match.home])
                                 for match, weight in zip(usable, weights))
            if total_expected > 0:
                mu = max(0.3, min(3.0, total_goals / total_expected))
            mean_attack = sum(attack.values()) / len(attack)
            mean_defence = sum(defence.values()) / len(defence)
            if mean_attack > 0 and mean_defence > 0:
                attack = {team: value / mean_attack for team, value in attack.items()}
                defence = {team: value / mean_defence for team, value in defence.items()}
                mu *= mean_attack * mean_defence
        model.attack, model.defence, model.home, model.mu = attack, defence, home, mu
        model.matches, model.teams = len(usable), teams
        model.matchdays = int(reference)
        model.base_for = sum(match.ft_home for match in usable) / len(usable)
        model.base_against = sum(match.ft_away for match in usable) / len(usable)
        model.rho = model.fit_rho(usable, weights, mu, rho_steps)
        return model

    def lambdas(self, home, away):
        attack_home = self.attack.get(home, 1.0)
        attack_away = self.attack.get(away, 1.0)
        defence_home = self.defence.get(home, 1.0)
        defence_away = self.defence.get(away, 1.0)
        lam_home = self.mu * attack_home * defence_away * self.home
        lam_away = self.mu * attack_away * defence_home
        return max(0.15, min(5.5, lam_home)), max(0.15, min(5.5, lam_away))

    def grid(self, home, away):
        return dixon_coles_grid(*self.lambdas(home, away), self.rho)

    def fit_rho(self, usable, weights, mu, steps=15):
        best_rho, best_score = 0.0, -1e18
        span = max(2, steps)
        for step in range(-span, span + 1):
            rho = step / (span * 6.0)
            score = 0.0
            for match, weight in zip(usable, weights):
                lam_home, lam_away = self.lambdas(match.home, match.away)
                score += weight * math.log(max(1e-12, dixon_coles_cell(match.ft_home, match.ft_away, lam_home, lam_away, rho)))
            if score > best_score:
                best_score, best_rho = score, rho
        return best_rho


def dixon_coles_cell(goals_for, goals_against, lam_for, lam_against, rho):
    base = poisson_pmf(goals_for, lam_for) * poisson_pmf(goals_against, lam_against)
    if goals_for == 0 and goals_against == 0:
        base *= 1 - lam_for * lam_against * rho
    elif goals_for == 0 and goals_against == 1:
        base *= 1 + lam_for * rho
    elif goals_for == 1 and goals_against == 0:
        base *= 1 + lam_against * rho
    elif goals_for == 1 and goals_against == 1:
        base *= 1 - rho
    return max(1e-12, base)


def dixon_coles_grid(lam_for, lam_against, rho):
    grid = empty_grid()
    for goals_for in range(SIDES):
        for goals_against in range(SIDES):
            grid[goals_for][goals_against] = dixon_coles_cell(goals_for, goals_against, lam_for, lam_against, rho)
    tail = 1.0 - sum(sum(row) for row in grid)
    if tail > 0:
        grid[MAX_GOALS][MAX_GOALS] += tail
    return normalise(grid)


# --------------------------------------------------------------------------- sequence library

class SequenceLibrary:
    """Per-team chronological FT symbols plus the metadata the search models need."""

    def __init__(self):
        self.team = defaultdict(list)          # team -> [symbol, ...]
        self.team_rows = defaultdict(list)     # team -> [{opponent, venue, season, day, symbol}]
        self.season = defaultdict(list)        # (season, team) -> [symbol, ...]
        self.season_rows = defaultdict(list)
        self.symbols = Counter()
        self.contexts = {1: defaultdict(Counter), 2: defaultdict(Counter), 3: defaultdict(Counter)}
        self.season_order = []                 # (season, team) keys, most recent last

    def add(self, match):
        for team, opponent, venue, goals_for, goals_against in (
                (match.home, match.away, "H", match.ft_home, match.ft_away),
                (match.away, match.home, "A", match.ft_away, match.ft_home)):
            symbol = score_symbol(goals_for, goals_against)
            row = {"opponent": opponent, "venue": venue, "season": match.season, "day": match.day,
                   "symbol": symbol, "goals_for": goals_for, "goals_against": goals_against,
                   "kickoff": match.kickoff, "event_id": match.event_id}
            key = (match.season, team)
            if not self.season[key]:
                self.season_order.append(key)
            self.team[team].append(symbol)
            self.team_rows[team].append(row)
            self.season[key].append(symbol)
            self.season_rows[key].append(row)
            self.symbols[symbol] += 1
        # Keep the context index current: it is what makes the sequence models O(1) per match.
        for team in (match.home, match.away):
            symbols = self.team[team]
            for order, index in self.contexts.items():
                if len(symbols) > order:
                    index[tuple(symbols[-order - 1:-1])][symbols[-1]] += 1

    @classmethod
    def build(cls, matches):
        library = cls()
        for match in matches:
            library.add(match)
        return library

    def vocabulary(self):
        return sorted(self.symbols)

    def copy(self):
        clone = SequenceLibrary()
        clone.team = defaultdict(list, {key: list(value) for key, value in self.team.items()})
        clone.team_rows = defaultdict(list, {key: list(value) for key, value in self.team_rows.items()})
        clone.season = defaultdict(list, {key: list(value) for key, value in self.season.items()})
        clone.season_rows = defaultdict(list, {key: list(value) for key, value in self.season_rows.items()})
        clone.symbols = Counter(self.symbols)
        clone.contexts = {order: defaultdict(Counter, {key: Counter(value) for key, value in index.items()})
                          for order, index in self.contexts.items()}
        clone.season_order = list(self.season_order)
        return clone


def markov_distribution(library, team, prefix, alpha=ALPHA, orders=(3, 2, 1), min_counts=(2, 3, 4)):
    """Krichevsky-Trofimov smoothed next-symbol distribution with explicit backoff.

    Returns the raw evidence (per-order counts) and the smoothed distribution; the
    caller decides how far to trust it via a likelihood ratio against the league
    marginal, so a sparse context can never swamp the statistical shape.
    """
    vocabulary = library.vocabulary() or ["0:0"]
    size = len(vocabulary)
    levels = []
    for order, floor in zip(orders, min_counts):
        if len(prefix) < order:
            continue
        context = tuple(prefix[-order:])
        counts = Counter(library.contexts[order].get(context, {}))
        total = sum(counts.values())
        levels.append({"order": order, "context": list(context), "occurrences": total, "floor": floor,
                       "counts": [{"score": symbol, "count": count}
                                  for symbol, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:5]]})
    if not levels:
        return None, {"levels": [], "chosen": None, "reason": "no context yet"}
    chosen = None
    for level in levels:  # levels arrive as 3, 2, 1
        if level["occurrences"] >= level["floor"]:
            chosen = level
            break
    chosen = chosen or levels[-1]
    counts = library.contexts[chosen["order"]].get(tuple(chosen["context"]), Counter())
    spread = {symbol: count + alpha for symbol, count in counts.items()}
    for symbol in vocabulary:
        spread.setdefault(symbol, alpha)
    total = sum(spread.values())
    distribution = {symbol: value / total for symbol, value in spread.items()}
    return distribution, {"levels": levels, "chosen": chosen["order"], "smoothed_symbols": len(vocabulary),
                          "reason": (f"order {chosen['order']} context recorded {chosen['occurrences']} time(s)"
                                     if chosen["occurrences"] else "nothing recorded for this run; no sequence evidence")}


def knn_distribution(library, team, prefix, neighbours=120, recency_halflife=6.0, alpha=ALPHA, scan_seasons=140,
                     minimum_similarity=0.34):
    """Similarity-weighted next-symbol distribution over historical team sequences.

    Similarity compares the most recent symbols (weighted Hamming) and rewards long
    common subsequences, so "teams that were in a similar place" contribute more.
    """
    if not prefix:
        # Same contract as the Markov engine: no context means "cannot act", never a grid.
        # Returning uniform_grid() here crashed rescore() whenever a team had no recorded run yet.
        return None, {"neighbours": [], "matched": 0, "reason": "no context yet"}
    window = prefix[-8:]
    scored = []
    keys = library.season_order[-scan_seasons:]
    for season, other in keys:
        symbols = library.season.get((season, other)) or []
        if len(symbols) < len(window) + 1:
            continue
        for index in range(len(window), len(symbols)):
            context = symbols[index - len(window):index]
            if len(context) < 2:
                continue
            matches = sum(1 for left, right in zip(context, window) if left == right)
            similarity = matches / len(window)
            if similarity < minimum_similarity:
                continue
            age = (index - len(window))
            scored.append((similarity, -age, other, season, symbols[index], context[-1]))
    scored.sort(key=lambda item: (-item[0], item[1]))
    top = scored[:neighbours]
    if not top:
        return None, {"neighbours": [], "matched": 0, "reason": "no historical window looked similar enough"}
    best = top[0][0]
    weighted = Counter()
    detail = []
    for similarity, negative_age, other, season, symbol, last in top:
        weight = (similarity ** 3) * (0.5 ** ((best - similarity) * 8)) * (0.5 ** ((-negative_age) / recency_halflife))
        weighted[symbol] += weight
        if len(detail) < 5:
            detail.append({"season": season, "team": other, "similarity": round(100 * similarity, 1),
                           "last_score": last, "next_score": symbol, "age": -negative_age})
    smoothed = {symbol: weight + alpha for symbol, weight in weighted.items()}
    for symbol in library.vocabulary():
        smoothed.setdefault(symbol, alpha)
    total = sum(smoothed.values())
    return ({symbol: value / total for symbol, value in smoothed.items()},
            {"neighbours": detail, "matched": len(top), "best_similarity": round(100 * best, 1),
             "reason": f"{len(top)} historical windows at or above {round(100 * minimum_similarity)}% prefix similarity"})


def mix_grids(reference, other, weight):
    """(1 - weight) * reference + weight * other, renormalised."""
    if other is None or weight <= 0:
        return reference
    grid = empty_grid()
    for goals_for in range(SIDES):
        for goals_against in range(SIDES):
            grid[goals_for][goals_against] = ((1.0 - weight) * reference[goals_for][goals_against] +
                                              weight * other[goals_for][goals_against])
    return normalise(grid)


def h2h_grid_for(library, team, opponent, venue, window=24, venue_minimum=6, minimum_rows=4,
                 ceiling=0.6, strength=8.0):
    """This pairing's own recorded meetings, counted, then shrunk toward the league prior.

    A different question from every other engine here: not "how strong is this team" and not "what
    usually follows this team's run of scores", but "what has actually happened between these two,
    at this venue" — evidence the statistical models smooth away. The sample is small by nature, so
    the count is mixed into the league prior in proportion to how much of it there is (n/(n+8), at
    most 60%) rather than trusted outright.
    """
    rows = (library.team_rows or {}).get(team) or []
    meetings = [row for row in rows if row.get("opponent") == opponent]
    same_venue = [row for row in meetings if row.get("venue") == venue]
    used = same_venue if len(same_venue) >= venue_minimum else meetings
    used = used[-window:]
    if len(used) < minimum_rows:
        return None, {"reason": f"only {len(meetings)} recorded meetings with {opponent} so far"}
    total = float(len(used))
    counts = Counter(row["symbol"] for row in used)
    empirical = grid_of_symbols({symbol: count / total for symbol, count in counts.items()})
    reference = prior_distribution(library)
    weight = min(ceiling, total / (total + strength))
    grid = mix_grids(reference, empirical, weight)
    recent = [row["symbol"] for row in used[-6:]]
    return grid, {"meetings": len(meetings), "used": int(total),
                  "basis": "this venue" if used is same_venue else "both venues",
                  "blend_weight": round(weight, 3), "recent": recent,
                  "reference": "league prior", "floor": "never leaves the prior entirely"}


def prior_distribution(library):
    counts = Counter()
    for team, symbols in library.team.items():
        for symbol in symbols:
            counts[symbol] += 1
    return grid_of_symbols(counts)


def marginal_distribution(library):
    """Context-free next-score frequencies — the denominator of every likelihood ratio."""
    total = sum(library.symbols.values()) or 1
    return {symbol: count / total for symbol, count in library.symbols.items()}


def rescore(reference_grid, distribution, marginal, gamma, floor=1e-4):
    """P(score) proportional to the statistical shape times (sequence evidence / marginal) ** gamma.

    gamma = 0 reproduces the reference engine exactly, so an over-confident sequence
    model can only ever move the shape as far as the fitted exponent allows.
    """
    if not distribution or gamma <= 0:
        return reference_grid
    grid = empty_grid()
    for goals_for in range(SIDES):
        for goals_against in range(SIDES):
            symbol = score_symbol(goals_for, goals_against)
            ratio = max(floor, distribution.get(symbol, 0.0)) / max(floor, marginal.get(symbol, floor))
            grid[goals_for][goals_against] = reference_grid[goals_for][goals_against] * (ratio ** gamma)
    return normalise(grid)


GAMMA_CANDIDATES = (0.0, 0.15, 0.3, 0.45, 0.6, 0.8, 1.0, 1.3, 1.6, 2.0)


def fit_gamma(entries, model, candidates=GAMMA_CANDIDATES):
    """Pick the sequence exponent on the fitting half; 0 means 'no evidence, keep the shape'."""
    rows = [entry for entry in entries if entry["sequence"].get(model) and entry["ceiling_ok"]]
    if len(rows) < 30:
        return 0.0, {"reason": "too few rows"}
    best, best_value = 0.0, None
    for gamma in candidates:
        total = 0.0
        for entry in rows:
            distribution, _ = entry["sequence"][model]
            total += log_loss(rescore(entry["reference"], distribution, entry["marginal"], gamma), entry["symbol"])
        if best_value is None or total < best_value:
            best, best_value = gamma, total
    return best, {"fitted_on": len(rows), "logloss": round(best_value / len(rows), 3)}


# --------------------------------------------------------------------------- market odds

def parse_market_row(row):
    """De-vig-free extraction of the markets this playground can use."""
    try:
        markets = json.loads(row["data"])
    except (TypeError, ValueError):
        return None
    found = {"correct_score": None, "one_x_two": None, "total": None, "btts": None, "captured_at": row.get("fetched_at")}
    for market in markets:
        name = str(market.get("name", "")).upper().strip()
        odds = {}
        for entry in market.get("odds", []):
            try:
                odds[str(entry.get("label")).strip().upper()] = float(entry.get("value"))
            except (TypeError, ValueError):
                continue
        if name == "CORRECT SCORE" and odds:
            found["correct_score"] = odds
        elif name == "1X2" and len(odds) >= 3:
            found["one_x_two"] = odds
        elif name == "TOTAL" and odds:
            found["total"] = odds
        elif name == "BOTH TEAMS TO SCORE" and odds:
            found["btts"] = odds
    return found if found["correct_score"] else None


def market_grid(markets_for_event, reference_grid):
    """Turn correct-score odds into a probability grid.

    Steps: invert every listed price, remove the book's overround, spread the
    'OTHER' bucket over unlisted scores in proportion to the statistical reference
    shape, then reconcile the grid with the de-vigged 1X2 and 2.5-goal marginals.
    """
    odds = markets_for_event.get("correct_score") or {}
    if not odds:
        return None, {"reason": "no correct-score market captured"}
    implied = {}
    for label, price in odds.items():
        if price <= 1.0:
            continue
        implied[label] = 1.0 / price
    if not implied:
        return None, {"reason": "prices unusable"}
    overround = sum(implied.values())
    listed = {label: value / overround for label, value in implied.items()}
    other = listed.pop("OTHER", 0.0)
    grid = empty_grid()
    for label, probability in listed.items():
        try:
            goals_for, goals_against = parse_symbol(label)
        except ValueError:
            continue
        if goals_for < SIDES and goals_against < SIDES:
            grid[goals_for][goals_against] = probability
    listed_mass = sum(sum(row) for row in grid)
    unlisted_cells = [(a, b) for a in range(SIDES) for b in range(SIDES) if grid[a][b] <= 0]
    reference_mass = sum(reference_grid[a][b] for a, b in unlisted_cells) or 1.0
    for a, b in unlisted_cells:
        grid[a][b] = other * reference_grid[a][b] / reference_mass
    grid = normalise(grid)
    stay = max(0.0, 1.0 - listed_mass - other)
    reconciled = reconcile_marginals(grid, markets_for_event)
    return reconciled, {
        "overround": round(100 * (overround - 1.0), 2),
        "listed_scores": len(listed),
        "other_share": round(100 * other, 2),
        "stay_share": round(100 * stay, 2),
        "one_x_two": markets_for_event.get("one_x_two"),
        "total": markets_for_event.get("total"),
        "btts": markets_for_event.get("btts"),
        "reconciled": True,
        "reason": "de-vigged correct-score prices, OTHER spread by the statistical shape, then re-projected onto the 1X2 marginals",
    }


def reconcile_marginals(grid, markets_for_event, rounds=6):
    """Iterative proportional fitting of a score grid onto the market's 1X2 (and 2.5) marginals."""
    one_x_two = markets_for_event.get("one_x_two") or {}
    target = None
    if len(one_x_two) >= 3:
        raw = {key: 1.0 / value for key, value in one_x_two.items() if value > 1.0}
        if len(raw) == 3:
            scale = sum(raw.values())
            target = {key: value / scale for key, value in raw.items()}
    if not target:
        return grid
    total_market = markets_for_event.get("total") or {}
    over = total_market.get("OVER 2.5")
    under = total_market.get("UNDER 2.5")
    total_target = None
    if over and under and over > 1.0 and under > 1.0:
        raw_over, raw_under = 1.0 / over, 1.0 / under
        scale = raw_over + raw_under
        total_target = raw_over / scale
    for _ in range(rounds):
        home = sum(grid[a][b] for a in range(SIDES) for b in range(SIDES) if a > b)
        draw = sum(grid[a][b] for a in range(SIDES) for b in range(SIDES) if a == b)
        away = sum(grid[a][b] for a in range(SIDES) for b in range(SIDES) if a < b)
        for a in range(SIDES):
            for b in range(SIDES):
                if a > b and home > 0:
                    grid[a][b] *= target["1"] / home
                elif a == b and draw > 0:
                    grid[a][b] *= target["X"] / draw
                elif a < b and away > 0:
                    grid[a][b] *= target["2"] / away
        if total_target is not None:
            current = sum(grid[a][b] for a in range(SIDES) for b in range(SIDES) if a + b > 2.5)
            if 0 < current < 1:
                factor = total_target / current
                for a in range(SIDES):
                    for b in range(SIDES):
                        grid[a][b] *= factor if a + b > 2.5 else (1 - total_target) / (1 - current)
        grid = normalise(grid)
    return grid


def flip_grid(grid):
    return [[grid[against][for_goals] for against in range(SIDES)] for for_goals in range(SIDES)]


# --------------------------------------------------------------------------- pools and scoring

def linear_pool(grids, weights):
    """Weighted mixture of distributions — the blend the metrics are measured on."""
    pooled = empty_grid()
    for grid, weight in zip(grids, weights):
        if weight <= 0:
            continue
        for a in range(SIDES):
            row = pooled[a]
            source = grid[a]
            for b in range(SIDES):
                row[b] += weight * source[b]
    return normalise(pooled)


# Proper scoring rules need a floor: an engine that assigns exactly zero to the recorded score
# would otherwise take the whole run down with math domain error. 1e-9 changes no real number.
SCORE_FLOOR = 1e-9


def log_loss(grid, symbol):
    goals_for, goals_against = parse_symbol(symbol)
    cell = grid[goals_for][goals_against] if goals_for < SIDES and goals_against < SIDES else 0.0
    return -math.log(max(float(cell), SCORE_FLOOR))


def ranked_probability_score(grid, symbol):
    """Ranked probability score over the goal-difference ladder, normalised to [0, 1].

    Ordinary RPS only knows three outcomes (1X2). Because this playground predicts the
    exact score, the ladder is every goal difference the grid can express, so a model is
    rewarded for being close on the margin as well as right on the winner.
    """
    goals_for, goals_against = parse_symbol(symbol)
    actual = max(-MAX_GOALS, min(MAX_GOALS, goals_for - goals_against))
    cumulative_model = 0.0
    total = 0.0
    ladder = 2 * MAX_GOALS
    for step in range(-MAX_GOALS, MAX_GOALS):
        cumulative_model += sum(grid[a][b] for a in range(SIDES) for b in range(SIDES) if a - b == step)
        observed_cdf = 1.0 if actual <= step else 0.0
        total += (cumulative_model - observed_cdf) ** 2
    return total / ladder


def top_scores(grid, limit=3):
    cells = sorted(((score_symbol(a, b), grid[a][b]) for a in range(SIDES) for b in range(SIDES)),
                   key=lambda item: (-item[1], item[0]))
    return [score for score, _ in cells[:limit]]


def direction_of(symbol):
    goals_for, goals_against = parse_symbol(symbol)
    return "1" if goals_for > goals_against else "X" if goals_for == goals_against else "2"


def direction_probabilities(grid):
    home = draw = away = 0.0
    for a in range(SIDES):
        for b in range(SIDES):
            if a > b:
                home += grid[a][b]
            elif a == b:
                draw += grid[a][b]
            else:
                away += grid[a][b]
    return {"1": home, "X": draw, "2": away}


# --------------------------------------------------------------------------- engine registry

MODEL_CARDS = [
    {"key": "prior", "name": "League baseline", "family": "Frequency",
     "blurb": "Every recorded FT score from the whole archive, counted.",
     "assumptions": "All teams and situations are interchangeable.",
     "strengths": "Cannot be overfitted and sets the floor every other engine must beat.",
     "limits": "Ignores the opponent, the venue, team form and the market."},
    {"key": "elo", "name": "Elo ratings", "family": "Rating system",
     "blurb": "Chronological Elo, converted into expected goals and a score grid.",
     "assumptions": "One strength number per team; rating gaps map to goal expectancy on a log scale.",
     "strengths": "Fast to update, transparent, handles the fixture calendar without refitting.",
     "limits": "No notion of squad changes, motivation or scoreline shape beyond the Poisson grid."},
    {"key": "poisson", "name": "Dixon-Coles Poisson", "family": "Generalised linear model",
     "blurb": "Decayed attack/defence strengths fitted by maximum likelihood, plus the low-score correction.",
     "assumptions": "Goals arrive as two Poisson processes; a rho term fixes the well-known 0-0/1-1 bias.",
     "strengths": "Directly models the scoreline, calibrated totals, standard in the literature.",
     "limits": "Independent halves; struggles when a team's style breaks the Poisson shape."},
    {"key": "markov", "name": "FT sequence (KT backoff)", "family": "Sequential / language model",
     "blurb": "What historically followed this exact run of FT scores, smoothed with Krichevsky-Trofimov backoff.",
     "assumptions": "A team's own recent score sequence carries signal; unseen contexts must be smoothed.",
     "strengths": "Reads the current run directly and reports the raw counts behind every pick.",
     "limits": "Sparse at order 3; a short prefix can fit noise instead of form."},
    {"key": "knn", "name": "Similar-sequence search", "family": "Instance-based",
     "blurb": "Nearest historical team sequences weighted by similarity, recency and what happened next.",
     "assumptions": "Teams in a similar recorded position are informative neighbours.",
     "strengths": "Non-parametric, no shared shape imposed, easy to inspect case by case.",
     "limits": "Costly to scan, and similarity in FT scores alone is a coarse notion of 'similar'."},
    {"key": "market", "name": "Market-implied", "family": "External odds",
     "blurb": "Captured Betika correct-score prices, de-vigged, with the OTHER bucket rebuilt and reconciled to 1X2.",
     "assumptions": "A liquid book's prices summarise information the archive alone does not contain.",
     "strengths": "Normally the sharpest available signal for exact scores.",
     "limits": "Only exists for fixtures whose market catalogue was captured; it is evidence, not an edge."},
]
MODEL_KEYS = [card["key"] for card in MODEL_CARDS]


@dataclass
class Bundle:
    """Everything a set of predictions needs, in the order the models consume it."""
    elo: dict
    poisson: PoissonStrength
    library: SequenceLibrary
    markets: dict
    reference: dict
    marginal: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    sequence_gammas: dict = field(default_factory=dict)
    shrinkage: dict = field(default_factory=dict)


def reference_for(library, poisson, home, away):
    """Statistical shape used to spread the market's OTHER bucket."""
    blended = linear_pool([poisson.grid(home, away), prior_distribution(library)], [0.65, 0.35])
    return blended


def model_grid(bundle, key, home, away, team, prefix):
    """One grid from team perspective for a single engine."""
    if key == "prior":
        return prior_distribution(bundle.library), {}
    if key == "elo":
        ratings = bundle.elo["ratings"]
        rating_for = ratings.get(team, 1500.0)
        edge = ELO_HOME if team == home else 0.0
        grid, lam_for, lam_against = elo_grid_for(bundle, home, away, team)
        return grid, {"rating": round(rating_for), "opponent_rating": round(ratings.get(away if team == home else home, 1500.0)),
                      "home_edge": edge, "lambda_for": round(lam_for, 2), "lambda_against": round(lam_against, 2),
                      "played": bundle.elo["played"].get(team, 0)}
    if key == "poisson":
        lam_home, lam_away = bundle.poisson.lambdas(home, away)
        grid_home = dixon_coles_grid(lam_home, lam_away, bundle.poisson.rho)
        attack_for = bundle.poisson.attack.get(team, 1.0)
        attack_against = bundle.poisson.attack.get(away if team == home else home, 1.0)
        defence_for = bundle.poisson.defence.get(team, 1.0)
        defence_against = bundle.poisson.defence.get(away if team == home else home, 1.0)
        explain = {"mu": round(bundle.poisson.mu, 3), "home_edge": round(bundle.poisson.home, 3),
                   "rho": round(bundle.poisson.rho, 3), "attack_for": round(attack_for, 2),
                   "defence_for": round(defence_for, 2), "attack_against": round(attack_against, 2),
                   "defence_against": round(defence_against, 2),
                   "lambda_for": round(lam_home if team == home else lam_away, 2),
                   "lambda_against": round(lam_away if team == home else lam_home, 2),
                   "matches_used": bundle.poisson.matches}
        return (grid_home if team == home else flip_grid(grid_home)), explain
    if key in ("markov", "knn"):
        distribution, explain = (markov_distribution if key == "markov" else knn_distribution)(bundle.library, team, prefix)
        reference = bundle.reference.get(fixture_key(bundle, home, away))
        if reference is None or distribution is None:
            return None, dict(explain, reason=explain.get("reason", "no sequence evidence"))
        gamma = (bundle.sequence_gammas or {}).get(key, 0.0)
        # The stored reference shape is home-oriented; a team's sequence evidence is written
        # from that team's own point of view, so rescore inside the team's frame and flip back.
        own_frame = reference if team == home else flip_grid(reference)
        grid = rescore(own_frame, distribution, bundle.marginal, gamma)
        boosts = []
        for goals_for in range(SIDES):
            for goals_against in range(SIDES):
                symbol = score_symbol(goals_for, goals_against)
                ratio = max(1e-4, distribution.get(symbol, 0.0)) / max(1e-4, bundle.marginal.get(symbol, 1e-4))
                lifts = ratio ** gamma if gamma else 1.0
                if abs(lifts - 1.0) > 0.02:
                    boosts.append({"score": symbol, "ratio": round(lifts, 2),
                                   "probability": round(100.0 * grid[goals_for][goals_against], 2)})
        boosts.sort(key=lambda item: -item["ratio"])
        explain = dict(explain, gamma=gamma, reference="Dixon-Coles x league prior",
                       boosted=boosts[:6], damped=boosts[-4:] if len(boosts) > 6 else [])
        return grid, explain
    if key == "h2h":
        opponent = away if team == home else home
        venue = "H" if team == home else "A"
        grid, explain = h2h_grid_for(bundle.library, team, opponent, venue)
        if grid is None:
            return None, explain
        if team != home:
            grid = flip_grid(grid)
        return grid, explain
    if key == "market":
        record = bundle.markets.get(_fixture_event(bundle, home, away))
        if not record:
            return None, {"reason": "this fixture's market catalogue has not been captured yet"}
        grid, explain = market_grid(record, bundle.reference.get((home, away)) or prior_distribution(bundle.library))
        if grid is None:
            return None, explain
        return (grid if team == home else flip_grid(grid)), explain
    raise KeyError(key)


def elo_grid_for(bundle, home, away, team):
    """Elo expected goals from the team's own perspective, then flipped for the row."""
    frame = bundle.elo
    rating_home = frame["ratings"].get(home, 1500.0)
    rating_away = frame["ratings"].get(away, 1500.0)
    base_for = bundle.poisson.base_for
    base_against = bundle.poisson.base_against
    gap = (rating_home + ELO_HOME - rating_away) / ELO_SCALE
    lam_home = base_for * math.exp(ELO_BETA * gap)
    lam_away = base_against * math.exp(-ELO_BETA * gap)
    grid = poisson_grid(lam_home, lam_away)
    if team == home:
        return grid, lam_home, lam_away
    return flip_grid(grid), lam_away, lam_home


def fixture_key(bundle, home, away):
    """The reference grid for a fixture is stored under the full calendar key."""
    return (bundle.season, bundle.day, home, away)


def _fixture_event(bundle, home, away):
    return bundle.fixture_events.get((bundle.season, bundle.day, home, away), "")


def bundle_for(store, history, cutoff, season=None, day=None):
    library = SequenceLibrary.build(history)
    bundle = Bundle(elo=elo_frame(history, cutoff), poisson=PoissonStrength.fit(history, cutoff),
                    library=library, markets={}, reference={})
    bundle.season, bundle.day = season, day
    bundle.fixture_events = {}
    for match in history:
        if match.event_id:
            bundle.fixture_events[(match.season, match.day, match.home, match.away)] = match.event_id
    return bundle


def load_markets(store, event_ids, cutoff=None):
    """Stored market catalogues, optionally only the ones captured before a kick-off."""
    wanted = [event_id for event_id in event_ids if event_id]
    if not wanted:
        return {}
    parsed = {}
    for start in range(0, len(wanted), 400):
        chunk = wanted[start:start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = store.all(f"SELECT event_id,data,fetched_at FROM markets WHERE event_id IN ({placeholders})", tuple(chunk))
        for row in rows:
            if cutoff and row["fetched_at"] and float(row["fetched_at"]) > cutoff:
                continue
            record = parse_market_row(row)
            if record:
                parsed[row["event_id"]] = record
    return parsed


def predict_matchday(store, history, targets, cutoff=None, weights=None, hit_weights=None, sequence_gammas=None,
                     shrinkage=None):
    """Predictions for the target matchday, one row per team, with per-engine explanations.

    Nothing at or after the target matchday is visible: the history is cut on the fixture
    calendar, and only market catalogues captured before the first kick-off are used.
    """
    if not targets:
        return [], None
    limit = (targets[0].season, targets[0].day)
    usable = before(history, *limit)
    library = SequenceLibrary.build(usable)
    kickoff = min((fixture.kickoff for fixture in targets if fixture.kickoff), default=None)
    markets = load_markets(store, [fixture.event_id for fixture in targets], cutoff=kickoff)
    bundle = Bundle(elo=elo_frame(usable, limit=limit), poisson=PoissonStrength.fit(usable),
                    library=library, markets=markets, reference={},
                    marginal=marginal_distribution(library),
                    sequence_gammas=sequence_gammas or {}, shrinkage=shrinkage or {})
    bundle.season = targets[0].season if targets else None
    bundle.day = targets[0].day if targets else None
    bundle.fixture_events = {(fixture.season, fixture.day, fixture.home, fixture.away): fixture.event_id
                             for fixture in targets}
    rows = []
    for fixture in targets:
        key = (fixture.season, fixture.day, fixture.home, fixture.away)
        reference_grid = reference_for(library, bundle.poisson, fixture.home, fixture.away)
        bundle.reference[key] = reference_grid
        for team, opponent, venue in ((fixture.home, fixture.away, "H"), (fixture.away, fixture.home, "A")):
            prefix = list(library.team.get(team, []))
            grids, explains = {}, {}
            for model in MODEL_KEYS:
                grid, explain = model_grid(bundle, model, fixture.home, fixture.away, team, prefix)
                if grid is not None:
                    grids[model] = grid
                explains[model] = explain
            # Shrink exactly the way the walk-forward backtest shrinks: toward the league prior,
            # which is the same grid for both rows of a fixture. Shrinking toward `reference_grid`
            # would drag the away row toward a home-oriented shape whenever shrinkage is non-zero,
            # so the two rows of a fixture would stop mirroring (audited: TV up to 0.068 vs 0.0000).
            shrink_target = grids.get("prior") or prior_distribution(library)
            calibrated = {model: calibrate(grid, shrink_target, bundle.shrinkage.get(model, 0.0))
                          for model, grid in grids.items()}
            for model, explain in explains.items():
                if model in calibrated:
                    explains[model] = dict(explain, shrinkage=bundle.shrinkage.get(model, 0.0))
            ordered = [model for model in MODEL_KEYS if model in calibrated]
            active_weights = _active_weights(weights or {}, ordered)
            hit_active = _active_weights(hit_weights or {}, ordered)
            pooled = linear_pool([calibrated[model] for model in ordered], active_weights) if ordered else uniform_grid()
            pooled_hits = (linear_pool([calibrated[model] for model in ordered], hit_active)
                           if ordered else uniform_grid())
            cells = top_cells(pooled, 5)
            hit_cells = top_cells(pooled_hits, 5)
            rows.append({
                "team": team, "opponent": opponent, "venue": venue, "event_id": fixture.event_id,
                "season": fixture.season, "day": fixture.day, "kickoff": fixture.kickoff,
                "context": prefix[-6:], "context_length": len(prefix),
                "market_available": fixture.event_id in markets,
                "models": {model: {"score": top_cells(calibrated[model], 1)[0]["score"],
                                   "probability": top_cells(calibrated[model], 1)[0]["probability"],
                                   "top": top_cells(calibrated[model], 3),
                                   "raw_score": top_cells(grids[model], 1)[0]["score"],
                                   "raw_probability": top_cells(grids[model], 1)[0]["probability"],
                                   "result": result_probabilities(calibrated[model]),
                                   "grid": [[round(1000.0 * calibrated[model][a][b]) for b in range(SIDES)] for a in range(SIDES)],
                                   "explain": explains[model]} for model in ordered},
                "unavailable": {model: explains[model] for model in MODEL_KEYS if model not in calibrated},
                "blend": {"score": cells[0]["score"], "probability": cells[0]["probability"], "top": cells,
                          "result": result_probabilities(pooled),
                          "weights": {model: round(weight, 6) for model, weight in zip(ordered, active_weights)},
                          "weight_vector": active_weights,
                          "grid": [[round(1000.0 * pooled[a][b]) for b in range(SIDES)] for a in range(SIDES)]},
                "hit_blend": {"score": hit_cells[0]["score"], "probability": hit_cells[0]["probability"],
                              "top": hit_cells, "result": result_probabilities(pooled_hits),
                              "weights": {model: round(weight, 6) for model, weight in zip(ordered, hit_active)},
                              "weight_vector": hit_active,
                              "grid": [[round(1000.0 * pooled_hits[a][b]) for b in range(SIDES)] for a in range(SIDES)]},
                "agreement": _agreement(ordered, calibrated),
            })
    return rows, bundle


def _active_weights(weights, ordered):
    if not weights:
        share = 1.0 / len(ordered) if ordered else 1.0
        return [share] * len(ordered)
    values = [max(0.0, float(weights.get(model, 0.0))) for model in ordered]
    total = sum(values)
    if total <= 0:
        share = 1.0 / len(ordered) if ordered else 1.0
        return [share] * len(ordered)
    return [value / total for value in values]


def _agreement(ordered, grids):
    """How many engines put the same score first — the playground's confidence read-out."""
    if not ordered:
        return {"top_scores": [], "unanimous": False}
    winners = Counter(top_cells(grids[model], 1)[0]["score"] for model in ordered)
    best, count = winners.most_common(1)[0]
    return {"top_scores": [{"score": score, "models": number} for score, number in winners.most_common()],
            "unanimous": count == len(ordered) and len(ordered) > 1,
            "engines": len(ordered)}


# --------------------------------------------------------------------------- walk-forward backtest

def calibrate(grid, prior_grid, shrinkage):
    """Shrink an engine's grid toward the league prior — the per-engine reliability dial."""
    if not shrinkage:
        return grid
    return linear_pool([grid, prior_grid], [1.0 - shrinkage, shrinkage])


def fit_shrinkage(entries, steps=20):
    """Choose each engine's shrinkage on the fitting half by minimising log loss."""
    calibration = {}
    for model in MODEL_KEYS:
        rows = [entry for entry in entries if model in entry["grids"] and entry["ceiling_ok"]]
        if len(rows) < 30:
            continue
        best, best_value = 0.0, None
        for step in range(steps + 1):
            value = step / float(steps)
            total = 0.0
            for entry in rows:
                total += log_loss(calibrate(entry["grids"][model], entry["prior"], value), entry["symbol"])
            if best_value is None or total < best_value:
                best, best_value = value, total
        calibration[model] = best
    return calibration


def pool_entries(entry, weights, calibration):
    """The blend as it is scored and served: calibrated engines, weighted, renormalised."""
    grids = {model: calibrate(grid, entry["prior"], calibration.get(model, 0.0))
             for model, grid in entry["grids"].items()}
    return blend_of(grids, weights)


def _grid_probability(grid, symbol):
    goals_for, goals_against = parse_symbol(symbol)
    if goals_for >= SIDES or goals_against >= SIDES:
        return 1e-9
    return max(1e-12, grid[goals_for][goals_against])


def blend_of(grids, weights):
    """Mixture of the grids available for one row, renormalised over whatever is present."""
    keys = list(grids)
    values = [max(0.0, float(weights.get(key, 0.0))) for key in keys]
    total = sum(values)
    if total <= 0:
        values = [1.0] * len(keys)
        total = float(len(keys)) or 1.0
    return linear_pool([grids[key] for key in keys], [value / total for value in values])


def _tally(bucket, grid, entry):
    symbol = entry["symbol"]
    goals_for, goals_against = parse_symbol(symbol)
    if goals_for >= SIDES or goals_against >= SIDES:
        bucket["excluded"] += 1
        return
    bucket["n"] += 1
    ordered = top_scores(grid, 3)
    if ordered and ordered[0] == symbol:
        bucket["hit1"] += 1
    if symbol in ordered:
        bucket["hit3"] += 1
    probabilities = direction_probabilities(grid)
    if max(probabilities, key=lambda key: probabilities[key]) == entry["direction"]:
        bucket["result_hits"] += 1
    bucket["logloss"] += log_loss(grid, symbol)
    bucket["rps"] += ranked_probability_score(grid, symbol)


def wilson(hits, total, z=1.96):
    """95% interval for a hit rate — with a few hundred rows, ~2pp gaps are noise."""
    if not total:
        return [0.0, 0.0]
    phat = hits / total
    denominator = 1 + z * z / total
    centre = (phat + z * z / (2 * total)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / total + z * z / (4 * total * total)) / denominator
    return [round(100.0 * max(0.0, centre - spread), 1), round(100.0 * min(1.0, centre + spread), 1)]


def _blank_metrics():
    return {"n": 0, "hit1": 0, "hit3": 0, "logloss": 0.0, "rps": 0.0, "result_hits": 0, "excluded": 0}


def _finalise(bucket):
    n = bucket["n"] or 1
    return {"n": bucket["n"], "excluded": bucket["excluded"],
            "hit1": round(100.0 * bucket["hit1"] / n, 1), "hit3": round(100.0 * bucket["hit3"] / n, 1),
            "hit1_ci": wilson(bucket["hit1"], bucket["n"]), "hit3_ci": wilson(bucket["hit3"], bucket["n"]),
            "logloss": round(bucket["logloss"] / n, 3), "rps": round(bucket["rps"] / n, 4),
            "result": round(100.0 * bucket["result_hits"] / n, 1),
            "result_ci": wilson(bucket["result_hits"], bucket["n"])}


def pool_inputs(entry, calibration, depth=6):
    """Compact per-row view used to search mixture weights without rebuilding 8x8 grids."""
    view = {}
    for model, grid in entry["grids"].items():
        if grid is None:
            continue
        calibrated = calibrate(grid, entry["prior"], calibration.get(model, 0.0))
        goals_for, goals_against = parse_symbol(entry["symbol"])
        actual = calibrated[goals_for][goals_against] if goals_for < SIDES and goals_against < SIDES else 1e-9
        top = {item["score"]: item["probability"] / 100.0 for item in top_cells(calibrated, depth)}
        view[model] = {"actual": max(1e-12, actual), "top": top}
    return view


def _pooled_top(view, weights):
    keys = list(view)
    share = sum(weights.get(key, 0.0) for key in keys) or 1.0
    combined = defaultdict(float)
    for key in keys:
        weight = weights.get(key, 0.0) / share
        if weight <= 0:
            continue
        for score, probability in view[key]["top"].items():
            combined[score] += weight * probability
    if not combined:
        return None, 0.0
    best = max(combined, key=lambda score: (combined[score], score))
    return best, combined[best]


def _pooled_actual(view, weights):
    keys = list(view)
    share = sum(weights.get(key, 0.0) for key in keys) or 1.0
    return sum(weights.get(key, 0.0) * view[key]["actual"] for key in keys) / share


def fit_pool_weights(entries, calibration=None, starts=16, iterations=60, seed=11, warm_start=None, floor=0.03,
                     objective="logloss", rounds=5, factors=(0.35, 0.6, 0.85, 1.2, 1.8, 2.8)):
    """Fit the mixture weights by MAP expectation-maximisation on the fitting rows.

    Two practical wrinkles are handled explicitly:

    * coverage is sparse — only fixtures whose market catalogue was captured have a market
      grid — so every candidate weight vector is renormalised per row and a missing engine
      simply hands its share to the engines that are present;
    * a Dirichlet floor keeps a minimum share (3% by default) on each engine, which stops
      the fit collapsing onto one model and keeps the ensemble honest about its members.
    """
    rows = [entry for entry in entries if entry["grids"] and entry["ceiling_ok"]]
    if not rows:
        return {}, {"reason": "no fitted observations"}
    calibration = calibration or {}
    present = sorted({model for entry in rows for model in entry["grids"] if entry["grids"].get(model)})
    if len(rows) < 40:
        share = round(1.0 / max(1, len(present)), 4)
        return {model: share for model in present}, {"reason": "too few observations; equal weights", "n": len(rows)}
    views = [entry.get("pools") or pool_inputs(entry, calibration) for entry in rows]
    count = len(present)
    alpha = floor * len(rows) / max(1e-9, 1.0 - floor * count)

    def logloss(weights):
        total = 0.0
        for view in views:
            total -= math.log(max(1e-12, _pooled_actual(view, weights)))
        return total / len(views)

    def hit_rates(weights):
        hits = top3 = 0
        for entry, view in zip(rows, views):
            keys = list(view)
            share = sum(weights.get(key, 0.0) for key in keys) or 1.0
            combined = defaultdict(float)
            for key in keys:
                weight = weights.get(key, 0.0) / share
                if weight <= 0:
                    continue
                for score, probability in view[key]["top"].items():
                    combined[score] += weight * probability
            ranked = sorted(combined, key=lambda score: (-combined[score], score))
            if ranked and ranked[0] == entry["symbol"]:
                hits += 1
            if entry["symbol"] in ranked[:3]:
                top3 += 1
        return hits / len(rows), top3 / len(rows)

    def fit_curve(weights):
        """Competition objective: exact hits first, near misses next, log loss as a guard rail."""
        hit1, hit3 = hit_rates(weights)
        return hit1 + 0.35 * hit3 - 0.02 * logloss(weights)

    def em(start):
        weights = dict(start)
        for _ in range(iterations):
            acc = defaultdict(float)
            for view in views:
                keys = list(view)
                share = sum(weights.get(key, 0.0) for key in keys) or 1.0
                pooled = max(_pooled_actual(view, weights), 1e-12)
                for key in keys:
                    acc[key] += (weights.get(key, 0.0) * view[key]["actual"] / share) / pooled
            denominator = sum(acc.values()) + alpha * count or 1.0
            weights = {key: (acc[key] + alpha) / denominator for key in present}
        return weights

    proposals = [{model: (1.0 if model == other else 0.0) for model in present} for other in present]
    proposals.append({model: 1.0 for model in present})
    if warm_start:
        carry = {model: max(0.0, float(warm_start.get(model, 0.0))) for model in present}
        if sum(carry.values()) > 0:
            proposals.append(carry)
    state = seed
    for _ in range(starts):
        proposal = {}
        for model in present:
            state = (1103515245 * state + 12345) % (2 ** 31)
            proposal[model] = 0.05 + state / (2 ** 31)
        total = sum(proposal.values())
        proposals.append({model: value / total for model, value in proposal.items()})
    best_weights, best_score = None, None
    for proposal in proposals:
        weights = em(proposal)
        value = logloss(weights)
        if best_score is None or value < best_score:
            best_score, best_weights = value, weights
    if objective == "hits":
        best_weights, best_score = _climb_hits(best_weights, present, views, fit_curve, floor, rounds, factors)
        hit1, hit3 = hit_rates(best_weights)
        detail = {"fitted_on": len(rows), "hit1": round(100.0 * hit1, 1), "hit3": round(100.0 * hit3, 1),
                  "logloss": round(logloss(best_weights), 3), "objective": "hit1 + 0.35 x hit3 - 0.02 x log loss"}
    else:
        detail = {"fitted_on": len(rows), "logloss": round(best_score, 3), "objective": "log loss"}
    ordered = {model: weight for model, weight in best_weights.items() if weight > 0.002}
    total = sum(ordered.values()) or 1.0
    return ({model: round(weight / total, 4) for model, weight in ordered.items()},
            dict(detail, models_present=present, floor=floor,
                 method="MAP expectation-maximisation with a Dirichlet floor"
                        + (" and multiplicative coordinate ascent" if objective == "hits" else "")))


def _pooled_three(view, weights):
    keys = list(view)
    share = sum(weights.get(key, 0.0) for key in keys) or 1.0
    combined = defaultdict(float)
    for key in keys:
        weight = weights.get(key, 0.0) / share
        if weight <= 0:
            continue
        for score, probability in view[key]["top"].items():
            combined[score] += weight * probability
    return set(sorted(combined, key=lambda score: (-combined[score], score))[:3])


def _climb_hits(weights, present, views, fitness, floor, rounds=5, factors=(0.35, 0.6, 0.85, 1.2, 1.8, 2.8)):
    """Multiplicative coordinate ascent on the simplex, scored by the competition objective."""
    best = dict(weights)
    best_value = fitness(best)
    for _ in range(rounds):
        improved = False
        for model in present:
            for factor in factors:
                trial = dict(best)
                trial[model] = max(0.0, trial.get(model, 0.0)) * factor + 1e-6
                total = sum(trial.values()) or 1.0
                trial = {key: value / total for key, value in trial.items()}
                if min(trial.get(key, 0.0) for key in present) < floor * 0.5:
                    continue
                value = fitness(trial)
                if value > best_value + 1e-6:
                    best, best_value, improved = trial, value, True
        if not improved:
            break
    return best, best_value


# The engines the walk-forward run evaluates. A candidate engine is measured against the same
# archive snapshot with and without it before it is allowed into MODEL_CARDS (and therefore into the
# board) — see scripts/compare_engines.py. `h2h` was measured that way and did not earn a place:
# on 640 evaluated rows it moved the competition mixture from 17.2% to 15.9% exact hits and the
# log-loss blend from 2.748 to 2.755, so it stays a candidate rather than a shipped engine.
BACKTEST_MODELS = ("prior", "elo", "poisson", "market")
CANDIDATE_MODELS = ("h2h",)


def _backtest_grid(model, ratings, poisson, library, markets, match, team, prefix, reference_grid):
    if model == "prior":
        return prior_distribution(library)
    if model == "elo":
        rating_home = ratings.get(match.home, 1500.0)
        rating_away = ratings.get(match.away, 1500.0)
        gap = (rating_home + ELO_HOME - rating_away) / ELO_SCALE
        lam_home = max(0.15, poisson.base_for * math.exp(ELO_BETA * gap))
        lam_away = max(0.15, poisson.base_against * math.exp(-ELO_BETA * gap))
        grid = poisson_grid(lam_home, lam_away)
        return grid if team == match.home else flip_grid(grid)
    if model == "poisson":
        lam_home, lam_away = poisson.lambdas(match.home, match.away)
        grid = dixon_coles_grid(lam_home, lam_away, poisson.rho)
        return grid if team == match.home else flip_grid(grid)
    if model == "h2h":
        opponent = match.away if team == match.home else match.home
        venue = "H" if team == match.home else "A"
        grid, _explain = h2h_grid_for(library, team, opponent, venue)
        if grid is None:
            return None
        return grid if team == match.home else flip_grid(grid)
    if model == "market":
        record = markets.get(match.event_id)
        if not record:
            return None
        grid, _ = market_grid(record, reference_grid)
        if grid is None:
            return None
        return grid if team == match.home else flip_grid(grid)
    return None


def backtest(store, history, window=600, refit_every=REFIT_EVERY, split=0.6, season=None):
    """One chronological pass over finalized matches: every engine predicts from earlier data only.

    The SequenceLibrary and the Elo table are released matchday by matchday, the Poisson
    strengths are refitted every `refit_every` matchdays, and the market engine only sees
    catalogues that were captured before the target matchday kicked off.
    """
    usable = list(history)
    if len(usable) < 80:
        # A short archive still has to answer with the payload contract the workspaces read.
        return {"sample": len(usable), "models": {}, "reason": "not enough finalized history",
                "weights": {}, "hit_weights": {}, "days": [], "confidence": {"bands": []},
                "live_board": {"season": season, "blends": {}, "scores": {}, "teams": {}, "summary": {}}}
    sample = usable[-window:] if window and len(usable) > window else usable
    warm = usable[:len(usable) - len(sample)]
    library = SequenceLibrary.build(warm)
    ratings = defaultdict(lambda: 1500.0)
    for match in warm:
        _elo_update(ratings, match)
    poisson = PoissonStrength.fit(warm)
    markets = load_markets(store, [match.event_id for match in sample if match.event_id]) if store else {}
    index_of = {}
    for position, item in enumerate(usable):
        index_of[id(item)] = position
    entries = []
    pending = []
    marker = (sample[0].season, sample[0].day)
    since_refit = 0
    for match in sample:
        if (match.season, match.day) != marker:
            for previous in pending:
                library.add(previous)
                _elo_update(ratings, previous)
            pending = []
            marker = (match.season, match.day)
            since_refit += 1
            if since_refit >= refit_every:
                since_refit = 0
                poisson = PoissonStrength.fit(usable[:index_of[id(match)]], iterations=9, max_history=2600, rho_steps=7)
        reference_grid = reference_for(library, poisson, match.home, match.away)
        marginal = marginal_distribution(library)
        for team, goals_for, goals_against in ((match.home, match.ft_home, match.ft_away),
                                               (match.away, match.ft_away, match.ft_home)):
            prefix = library.team.get(team, [])
            grids = {}
            for model in BACKTEST_MODELS:
                grid = _backtest_grid(model, ratings, poisson, library, markets, match, team, prefix, reference_grid)
                if grid is not None:
                    grids[model] = grid
            if not grids:
                continue
            symbol = score_symbol(goals_for, goals_against)
            entries.append({"matchday": (match.season, match.day), "team": team, "symbol": symbol,
                            "direction": direction_of(symbol), "grids": grids, "prior": grids["prior"],
                            "reference": reference_grid, "marginal": marginal,
                            "reference_team": reference_grid if team == match.home else flip_grid(reference_grid),
                            "sequence": {"markov": markov_distribution(library, team, prefix),
                                         "knn": knn_distribution(library, team, prefix)},
                            "ceiling_ok": goals_for < SIDES and goals_against < SIDES})
        pending.append(match)
    for previous in pending:
        library.add(previous)
        _elo_update(ratings, previous)

    split_index = int(len(entries) * split)
    sequence_gammas, gamma_report = {}, {}
    for model in ("markov", "knn"):
        sequence_gammas[model], gamma_report[model] = fit_gamma(entries[:split_index], model)
    for entry in entries:
        for model in ("markov", "knn"):
            raw = entry["sequence"].get(model)
            if not raw or raw[0] is None:
                continue
            entry["grids"][model] = rescore(entry["reference_team"], raw[0], entry["marginal"],
                                            sequence_gammas.get(model, 0.0))
    modelling = [model for model in MODEL_KEYS if any(model in entry["grids"] for entry in entries)]
    calibration = fit_shrinkage(entries[:split_index])
    raw = {model: _blank_metrics() for model in modelling}
    tuned = {model: _blank_metrics() for model in modelling}
    per_team = defaultdict(_blank_metrics)
    series = defaultdict(lambda: defaultdict(lambda: {"n": 0, "hit1": 0, "logloss": 0.0}))
    for entry in entries:
        for model, grid in entry["grids"].items():
            _tally(raw[model], grid, entry)
            calibrated = calibrate(grid, entry["prior"], calibration.get(model, 0.0))
            _tally(tuned[model], calibrated, entry)
            _tally(per_team[entry["team"]], calibrated, entry)
            bucket = series[model][entry["matchday"]]
            if entry["ceiling_ok"]:
                bucket["n"] += 1
                bucket["hit1"] += 1 if top_scores(grid, 1)[0] == entry["symbol"] else 0
                bucket["logloss"] += log_loss(grid, entry["symbol"])
    weights, weight_report = fit_pool_weights(entries[:split_index], calibration)
    hit_weights, hit_report = fit_pool_weights(entries[:split_index], calibration, objective="hits",
                                               warm_start=weights, rounds=4)
    for entry in entries:
        entry["pools"] = pool_inputs(entry, calibration)
    # From here on the mixture is refit walk-forward: at the start of every matchday both
    # weight vectors are re-estimated on the rows that came before it, then frozen for that day.
    trackers = {"blend": dict(weights), "hit_blend": dict(hit_weights)}
    blend_bucket = {name: _blank_metrics() for name in trackers}
    blend_team = defaultdict(_blank_metrics)
    weight_history = []
    confidence = []
    current_day = None
    daily = {}
    # Every walk-forward pick is also kept as a per-team, per-matchday ledger row. The live board
    # reads this: for each team it shows what the competition blend would have played that
    # matchday — using only the weights fitted from rows before it — next to the recorded FT.
    live_rows = defaultdict(lambda: defaultdict(list))      # blend name -> team -> rows
    for index, entry in enumerate(entries):
        if entry["matchday"] != current_day:
            current_day = entry["matchday"]
            if index >= 40:
                trailing = entries[max(0, index - WEIGHT_WINDOW):index]
                for name, objective in (("blend", "logloss"), ("hit_blend", "hits")):
                    refreshed, report = fit_pool_weights(trailing, calibration, starts=1, iterations=8,
                                                         warm_start=trackers[name], objective=objective, rounds=2,
                                                         factors=(0.5, 0.8, 1.25, 2.0))
                    if refreshed:
                        trackers[name] = refreshed
                weight_history.append({"label": f"{current_day[0]}/{current_day[1]}", "index": index,
                                       "weights": dict(trackers["blend"]), "hit_weights": dict(trackers["hit_blend"]),
                                       "rows": len(trailing)})
        for name, chosen in trackers.items():
            pooled = pool_entries(entry, chosen, calibration)
            if index < split_index:
                continue
            _tally(blend_bucket[name], pooled, entry)
            if name == "blend":
                _tally(blend_team[entry["team"]], pooled, entry)
            bucket = series[name][entry["matchday"]]
            if entry["ceiling_ok"]:
                bucket["n"] += 1
                pick = top_scores(pooled, 1)[0]
                bucket["hit1"] += 1 if pick == entry["symbol"] else 0
                bucket["logloss"] += log_loss(pooled, entry["symbol"])
            if True:
                pick = top_scores(pooled, 1)[0]
                goals_for, goals_against = parse_symbol(pick)
                probability = pooled[goals_for][goals_against] if goals_for < SIDES and goals_against < SIDES else 0.0
                live_rows[name][entry["team"]].append({
                    "season": entry["matchday"][0], "day": entry["matchday"][1], "team": entry["team"],
                    "pick": pick, "probability": round(100.0 * probability, 2),
                    "agrees": sum(1 for grid in entry["grids"].values() if top_scores(grid, 1)[0] == pick),
                    "engines": len(entry["grids"]),
                    "top": top_scores(pooled, 3),
                    "actual": entry["symbol"],
                    "verdict": "hit" if pick == entry["symbol"] else "miss",
                    "direction": "hit" if direction_of(pick) == entry["direction"] else "miss",
                })
            if name == "blend":
                shot = daily.setdefault(entry["matchday"], {"fixtures": 0, "hits": 0, "picks": 0})
                shot["picks"] += 1
                pick = top_scores(pooled, 1)[0]
                if pick == entry["symbol"]:
                    shot["hits"] += 1
                agrees = sum(1 for model, grid in entry["grids"].items() if top_scores(grid, 1)[0] == pick)
                confidence.append({"probability": round(100.0 * pooled[parse_symbol(pick)[0]][parse_symbol(pick)[1]], 2),
                                   "pick": pick, "agrees": agrees, "engines": len(entry["grids"]),
                                   "hit": 1 if pick == entry["symbol"] else 0,
                                   "in_top3": 1 if entry["symbol"] in top_scores(pooled, 3) else 0,
                                   "competition_pick": top_scores(pool_entries(entry, trackers["hit_blend"], calibration), 1)[0]})
    # ---------------------------------------------------------------- live board ledger
    # The newest season in the sample is the one the board displays. Every row here is a
    # walk-forward pick (weights fitted on trailing rows only), so the ledger grades the engines
    # exactly the way the headline metrics do — nothing is recomputed with hindsight.
    def _live_view(rows_by_team, season):
        """Per-team ledger plus totals for one blend, restricted to the season on display."""
        teams, summary = {}, {"season": season, "teams": 0, "picks": 0, "hits": 0, "misses": 0,
                              "direction_hits": 0, "no_pick": 0, "matchdays": 0}
        if season is None:
            return teams, summary
        days = set()
        for team, rows in rows_by_team.items():
            kept = sorted((row for row in rows if row["season"] == season), key=lambda row: row["day"])
            if not kept:
                continue
            hits = sum(1 for row in kept if row["verdict"] == "hit")
            direction_hits = sum(1 for row in kept if row["direction"] == "hit")
            teams[team] = {"rows": kept,
                           "summary": {"picks": len(kept), "hits": hits, "misses": len(kept) - hits,
                                       "direction_hits": direction_hits,
                                       "direction_rate": round(100.0 * direction_hits / len(kept), 1),
                                       "hit_rate": round(100.0 * hits / len(kept), 1), "no_pick": 0,
                                       "first_day": kept[0]["day"], "last_day": kept[-1]["day"]}}
            days |= {row["day"] for row in kept}
            summary["teams"] += 1
            summary["picks"] += len(kept)
            summary["hits"] += hits
            summary["misses"] += len(kept) - hits
            summary["direction_hits"] += direction_hits
        summary["matchdays"] = len(days)
        summary["hit_rate"] = round(100.0 * summary["hits"] / max(1, summary["picks"]), 1)
        summary["direction_rate"] = round(100.0 * summary["direction_hits"] / max(1, summary["picks"]), 1)
        return teams, summary

    live_season = season or max((row["season"] for blend_rows in live_rows.values()
                       for team_rows in blend_rows.values() for row in team_rows), default=None)
    blends = {}
    for name in ("hit_blend", "blend"):
        teams, summary = _live_view(live_rows.get(name, {}), live_season)
        blends[name] = {"teams": teams, "summary": summary}
    scores = {}
    if live_season is not None:
        for match in history:
            if match.season != live_season:
                continue
            scores.setdefault(match.home, {})[str(match.day)] = score_symbol(match.ft_home, match.ft_away)
            scores.setdefault(match.away, {})[str(match.day)] = score_symbol(match.ft_away, match.ft_home)
    primary = blends.get("hit_blend") or {"teams": {}, "summary": {}}
    live_board = {"season": live_season, "blends": blends, "scores": scores,
                  "teams": primary["teams"], "summary": primary["summary"],
                  "note": ("The upper row is the recorded full-time score of each matchday; the row beneath it "
                           "is the walk-forward pick for the same matchday, using only the rows "
                           "recorded before it, graded against the recorded FT score. The scope switch chooses "
                           "the competition blend (tuned on exact hits) or the log-loss blend (tuned on probability).")}
    weight_report = dict(weight_report, refit="walk-forward, every matchday, trailing "
                                              f"{WEIGHT_WINDOW} rows, warm-started")
    hit_report = dict(hit_report, refit="same walk-forward schedule as the log-loss blend")
    models = {}
    for model in modelling:
        models[model] = {"raw": _finalise(raw[model]), "calibrated": _finalise(tuned[model]),
                         "coverage": raw[model]["n"], "shrinkage": calibration.get(model, 0.0),
                         "gamma": sequence_gammas.get(model)}
    models["blend"] = {"raw": _finalise(blend_bucket["blend"]), "calibrated": _finalise(blend_bucket["blend"]),
                       "coverage": len(entries) - split_index, "shrinkage": 0.0,
                       "weights": dict(trackers["blend"])}
    models["hit_blend"] = {"raw": _finalise(blend_bucket["hit_blend"]), "calibrated": _finalise(blend_bucket["hit_blend"]),
                           "coverage": len(entries) - split_index, "shrinkage": 0.0,
                           "weights": dict(trackers["hit_blend"])}
    for label, shot in sorted(daily.items()):
        shot["fixtures"] = shot["picks"] // 2
        shot["label"] = f"{label[0]}/{label[1]}"
    day_rows = [{"label": shot["label"], "season": label[0], "day": label[1], "fixtures": shot["fixtures"],
                 "hits": shot["hits"]} for label, shot in sorted(daily.items())]
    confidence_table = _confidence_table(confidence)
    reliability = _reliability(confidence)
    ranked = sorted((model for model in modelling if models[model]["coverage"] > 40),
                    key=lambda model: models[model]["calibrated"]["logloss"])
    champion = ranked[0] if ranked else None
    fit_rows = [entry for entry in entries[:split_index]]
    modal_symbol = Counter(entry["symbol"] for entry in fit_rows).most_common(1)[0][0] if fit_rows else "1:0"
    modal_hits = sum(1 for entry in entries[split_index:] if entry["symbol"] == modal_symbol)
    baselines = {
        "modal": {"score": modal_symbol, "n": len(entries) - split_index,
                  "hit1": round(100.0 * modal_hits / max(1, len(entries) - split_index), 1),
                  "note": f"always playing the most common exact score of the fitting half ({modal_symbol})"},
        "best_engine": {"model": champion, "hit1": models[champion]["calibrated"]["hit1"] if champion else None,
                        "logloss": models[champion]["calibrated"]["logloss"] if champion else None},
        "matchday_best": max((shot["hits"] for shot in day_rows), default=0),
        "matchday_mean": round(sum(shot["hits"] for shot in day_rows) / len(day_rows), 2) if day_rows else 0.0,
    }
    return {"sample": len(sample), "split": split, "fit_rows": split_index, "eval_rows": len(entries) - split_index,
            "window": window, "weights": dict(trackers["blend"]), "hit_weights": dict(trackers["hit_blend"]),
            "seed_weights": weights, "hit_report": hit_report,
            "weight_history": weight_history, "weight_report": weight_report, "models": models,
            "days": day_rows, "confidence": confidence_table, "baselines": baselines,
            "live_board": live_board,
            "reliability": reliability,
            "champion": champion, "sequence_gammas": sequence_gammas, "gamma_report": gamma_report,
            "shrinkage": calibration,
            "blend_gain": (round(models[champion]["calibrated"]["logloss"] - models["blend"]["calibrated"]["logloss"], 3)
                           if champion else None),
            "series": _series_payload(series),
            "teams": {team: _finalise(bucket) for team, bucket in sorted(per_team.items())},
            "blend_teams": {team: _finalise(bucket) for team, bucket in sorted(blend_team.items())},
            "market_rows": sum(1 for entry in entries if "market" in entry["grids"]),
            "engines": modelling,
            "protocol": ("Walk-forward: the sequence library and Elo table are released matchday by matchday, "
                         "Poisson is refitted on earlier data only, market grids use catalogues captured before "
                         "kick-off, shrinkage and blend weights are fitted on the first "
                         f"{round(100 * split)}% of rows and every headline number comes from the untouched remainder.")}


def _confidence_table(rows, buckets=5):
    """Empirical hit rate by how confident the blend was — the competition's cheat sheet."""
    if len(rows) < 25:
        return {"note": "not enough evaluated rows for a confidence table", "bands": []}
    ordered = sorted(rows, key=lambda row: row["probability"])
    size = max(1, len(ordered) // buckets)
    bands = []
    for start in range(0, len(ordered), size):
        chunk = ordered[start:start + size]
        if len(chunk) < 5:
            continue
        hits = sum(row["hit"] for row in chunk)
        top3 = sum(row["in_top3"] for row in chunk)
        bands.append({"low": chunk[0]["probability"], "high": chunk[-1]["probability"],
                      "n": len(chunk), "hit1": round(100.0 * hits / len(chunk), 1),
                      "hit1_ci": wilson(hits, len(chunk)),
                      "hit3": round(100.0 * top3 / len(chunk), 1),
                      "mean_probability": round(sum(row["probability"] for row in chunk) / len(chunk), 2)})
    by_agreement = []
    for count in sorted({row["agrees"] for row in rows}):
        chunk = [row for row in rows if row["agrees"] == count]
        hits = sum(row["hit"] for row in chunk)
        by_agreement.append({"agrees": count, "n": len(chunk), "hit1": round(100.0 * hits / len(chunk), 1),
                             "hit1_ci": wilson(hits, len(chunk))})
    return {"bands": bands, "by_agreement": by_agreement,
            "note": ("Bands are ordered by the blend's own probability for its top score, so the top band is "
                     "where the playground was most confident.")}


def _reliability(rows, buckets=6):
    """Calibration curve: claimed probability vs observed frequency, over the top pick."""
    if len(rows) < 25:
        return []
    ordered = sorted(rows, key=lambda row: row["probability"])
    size = max(1, len(ordered) // buckets)
    curve = []
    for start in range(0, len(ordered), size):
        chunk = ordered[start:start + size]
        if len(chunk) < 5:
            continue
        claimed = sum(row["probability"] for row in chunk) / len(chunk)
        observed = 100.0 * sum(row["hit"] for row in chunk) / len(chunk)
        curve.append({"claimed": round(claimed, 2), "observed": round(observed, 2), "n": len(chunk)})
    return curve


def _series_payload(series, keep=60):
    """Per-matchday rolling metrics, newest last, so the UI can draw a trajectory."""
    payload = {}
    for model, days in series.items():
        ordered = sorted(days.items())[-keep:]
        payload[model] = [{"season": marker[0], "day": marker[1], "label": f"{marker[0]}/{marker[1]}",
                           "n": value["n"], "hit1": round(100.0 * value["hit1"] / (value["n"] or 1), 1),
                           "logloss": round(value["logloss"] / (value["n"] or 1), 3)}
                          for marker, value in ordered if value["n"]]
    return payload
