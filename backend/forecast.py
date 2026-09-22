"""Earlier-season FT continuation picks and their honest season ledger.

The live FT board reads a team's own finalized FT prefix, looks for the *longest*
context that has recorded continuations in seasons strictly earlier than the
target season, and reports the most frequently recorded following FT score.

Nothing is invented:
  * only finalized source FT pairs are used, in literal home:away order;
  * a context never bridges a gap, a missing day or a season boundary;
  * the reference pool excludes the target season and every later season;
  * a length with no qualifying continuation drops its oldest day and retries
    (full prefix first), exactly like the Gold comparison;
  * the recorded continuation a pick reports must itself appear in at least two
    distinct source matches, otherwise the day is reported as no_pick instead of
    a guess.

`season_ledger` then grades every earlier matchday of the same season with the
context that was available *before* that round, so the board can label each
recorded pick as right or wrong. This is retrospective research: exact FT
repetition is rare, and the ledger reports it honestly instead of claiming an
edge.
"""
from __future__ import annotations
import time

ENGINE = "earlier-season-continuation-v1"
MIN_CONTEXT = 2
MAX_CONTEXT = 30
DEFAULT_MINIMUM_MATCHES = 2
TOP_OUTCOMES = 3


def score_order(score):
    return tuple(int(value) for value in score.split(":"))


def direction(score):
    home, away = (int(value) for value in score.split(":"))
    return "H" if home > away else "A" if away > home else "D"


def build_cards(rows):
    """(season, team) -> 30 FT cards. Duplicate team/day entries are dropped."""
    teams = {}
    for row in rows:
        if row["status"] != "final" or row["ft_home"] is None or row["ft_away"] is None:
            continue
        day = row["day"]
        if not 1 <= day <= 30:
            continue
        for name in (row["home"], row["away"]):
            key = (row["season"], name)
            cards = teams.setdefault(key, [None] * 30)
            index = day - 1
            if cards[index] is not None:
                cards[index] = False
                continue
            cards[index] = {"day": day, "ft": f"{row['ft_home']}:{row['ft_away']}", "match_id": row["id"],
                            "home": row["home"], "away": row["away"]}
    return {key: tuple(card for card in cards) for key, cards in teams.items()}


class ReferencePool:
    """Recorded FT windows from strictly earlier seasons, bucketed for lookup."""

    def __init__(self, cards):
        self.cards = cards
        self.team_seasons = len(cards)
        self.seasons = sorted({season for season, _ in cards})
        self.buckets = {}
        for (season, team), values in cards.items():
            for start in range(MAX_CONTEXT - 1):
                first, second = values[start], values[start + 1]
                if not first or not second:
                    continue
                self.buckets.setdefault((first["ft"], second["ft"]), []).append((values, season, team, start))

    def lookup(self, pattern, team=None):
        """Occurrences and following FT outcomes of one exact context."""
        length = len(pattern)
        if not 1 <= length <= MAX_CONTEXT:
            raise ValueError("A context must contain 1 to 30 FT scores")
        outcomes, traces, seasons, occurrences, without = {}, {}, {}, 0, 0
        if length >= MIN_CONTEXT:
            for values, season, owner, start in self.buckets.get((pattern[0], pattern[1]), ()):
                if team is not None and owner != team:
                    continue
                if start + length > MAX_CONTEXT:
                    continue
                window = values[start:start + length]
                if any(not card or card["ft"] != pattern[i] for i, card in enumerate(window)):
                    continue
                occurrences += 1
                after = values[start + length] if start + length < MAX_CONTEXT else None
                if not after:
                    without += 1
                    continue
                traces[after["ft"]] = traces.get(after["ft"], 0) + 1
                outcomes.setdefault(after["ft"], set()).add(after["match_id"])
                seasons.setdefault(after["ft"], set()).add(season)
        rows = [{"score": score, "matches": len(ids), "traces": traces.get(score, 0),
                 "seasons": len(seasons.get(score, ()))} for score, ids in outcomes.items()]
        rows.sort(key=lambda row: (-row["matches"], -row["traces"], score_order(row["score"])))
        return {"pattern": list(pattern), "length": length, "occurrences": occurrences,
                "following_matches": sum(row["matches"] for row in rows),
                "following_traces": sum(row["traces"] for row in rows),
                "without_recorded_followup": without, "outcomes": rows}

    def continuation(self, pattern, team=None, minimum_matches=DEFAULT_MINIMUM_MATCHES):
        """Longest context first; drop the oldest day until a continuation exists."""
        attempts = []
        for start in range(0, len(pattern) - MIN_CONTEXT + 1):
            context = list(pattern[start:])
            evidence = self.lookup(context, team)
            attempts.append({"context_start": start + 1, "length": len(context),
                             "occurrences": evidence["occurrences"],
                             "following_matches": evidence["following_matches"]})
            if evidence["outcomes"] and evidence["outcomes"][0]["matches"] >= minimum_matches:
                top = evidence["outcomes"][0]
                return {"found": True, "context": context, "context_start": start + 1, "length": len(context),
                        "trimmed": start, "score": top["score"], "matches": top["matches"], "traces": top["traces"],
                        "seasons": top["seasons"], "share": round(top["matches"] / evidence["following_matches"] * 100, 1),
                        "following_matches": evidence["following_matches"], "occurrences": evidence["occurrences"],
                        "outcomes": evidence["outcomes"][:TOP_OUTCOMES], "attempts": attempts}
        return {"found": False, "context": [], "context_start": None, "length": 0, "trimmed": None, "score": None,
                "matches": 0, "traces": 0, "seasons": 0, "share": 0.0, "following_matches": 0, "occurrences": 0,
                "outcomes": [], "attempts": attempts}


def compact_pick(pick, attempts=False):
    """Client payload for one pick; the trim attempts are only kept when asked."""
    if not pick or not pick["found"]:
        return None
    compact = {key: pick[key] for key in ("score", "matches", "traces", "seasons", "share", "context_start", "length",
                                          "trimmed", "following_matches", "occurrences")} | {"outcomes": pick["outcomes"]}
    if attempts:
        compact["attempts"] = pick["attempts"]
    return compact


def season_ledger(store, season=None, scopes=("all", "same"), minimum_matches=DEFAULT_MINIMUM_MATCHES):
    """Picks for every matchday of a season, graded against the recorded FT."""
    began = time.perf_counter()
    with store.lock:
        incoming = store.get_meta("upcoming", {}).get("season")
        season = int(season or incoming)
        reference_rows = store.all("SELECT * FROM matches WHERE season<? AND status='final' ORDER BY season,day,id", (season,))
        target_rows = store.all("SELECT * FROM matches WHERE season=? AND status='final' ORDER BY day,id", (season,))
        prefix = min(MAX_CONTEXT, store.prefix_length(season))
    pool = ReferencePool(build_cards(reference_rows))
    target = {key: cards for key, cards in build_cards(target_rows).items() if key[0] == season}
    teams = sorted(name for _, name in target)
    payload = {"engine": ENGINE, "mode": "earlier-season-continuation", "season": season, "prefix": prefix,
               "upcoming_day": min(MAX_CONTEXT, prefix + 1), "minimum_matches": minimum_matches,
               "reference_seasons": pool.seasons, "reference_team_seasons": pool.team_seasons,
               "reference_policy": "Only seasons strictly earlier than the target season are used.",
               "no_target_or_future_reference_data": all(item < season for item in pool.seasons),
               "interpretation": "The most frequently recorded earlier-season continuation of a team's own finalized "
                                 "FT prefix. The reported score must appear in at least two distinct recorded source "
                                 "matches. Exact-score repetition is rare; historical repetition is not a forecast, a "
                                 "confidence score or a wagering recommendation.",
               "scopes": {}}
    for scope in scopes:
        if scope not in ("all", "same"):
            raise ValueError("Unknown continuation scope")
        rows = []
        for team in teams:
            values = target[(season, team)]
            fts = [card["ft"] if card else None for card in values]
            limit = 0
            while limit < MAX_CONTEXT and fts[limit] is not None:
                limit += 1
            upto = min(prefix, limit)
            scope_team = team if scope == "same" else None
            ledger = []
            for day in range(2, upto + 1):
                actual = values[day - 1]["ft"]
                if day - 1 < MIN_CONTEXT:
                    ledger.append({"day": day, "status": "no_pick", "reason": "context_too_short", "pick": None,
                                   "context_start": None, "context_length": 0, "actual": actual,
                                   "match_id": values[day - 1]["match_id"], "direction_hit": None})
                    continue
                pick = pool.continuation(fts[:day - 1], scope_team, minimum_matches)
                ledger.append({"day": day, "status": "no_pick" if not pick["found"] else "hit" if pick["score"] == actual else "miss",
                               "reason": None if pick["found"] else "no_recorded_continuation",
                               "pick": compact_pick(pick),
                               "context_start": pick["context_start"], "context_length": pick["length"], "actual": actual,
                               "match_id": values[day - 1]["match_id"],
                               "direction_hit": bool(pick["found"] and direction(pick["score"]) == direction(actual))})
            pending = None
            if upto + 1 <= MAX_CONTEXT and upto >= MIN_CONTEXT:
                pick = pool.continuation(fts[:upto], scope_team, minimum_matches)
                pending = {"day": upto + 1, "status": "pending" if pick["found"] else "no_pick",
                           "reason": None if pick["found"] else "no_recorded_continuation", "context": fts[:upto],
                           "pick": compact_pick(pick, attempts=True),
                           "context_start": pick["context_start"], "context_length": pick["length"], "actual": None,
                           "match_id": None, "direction_hit": None}
                ledger.append(pending)
            graded = [row for row in ledger if row["status"] in ("hit", "miss")]
            hits = sum(1 for row in graded if row["status"] == "hit")
            directions = sum(1 for row in graded if row["direction_hit"])
            rows.append({"team": team, "scope": scope, "played": upto, "upcoming_day": upto + 1, "pending": pending,
                         "ledger": ledger,
                         "summary": {"picks": len(graded), "hits": hits, "misses": len(graded) - hits, "graded": len(graded),
                                     "no_pick": sum(1 for row in ledger if row["status"] == "no_pick"),
                                     "pending": 1 if pending and pending["status"] == "pending" else 0,
                                     "exact_hit_rate": round(hits / len(graded) * 100, 1) if graded else None,
                                     "direction_hits": directions,
                                     "direction_rate": round(directions / len(graded) * 100, 1) if graded else None,
                                     "coverage": round(len(graded) / (len(graded) + sum(1 for row in ledger if row["status"] == "no_pick")) * 100, 1) if ledger else 0}})
        payload["scopes"][scope] = {"teams": rows, "picks": sum(row["summary"]["picks"] for row in rows),
                                    "hits": sum(row["summary"]["hits"] for row in rows),
                                    "misses": sum(row["summary"]["misses"] for row in rows),
                                    "no_pick": sum(row["summary"]["no_pick"] for row in rows)}
    payload["computed_at"] = time.time()
    payload["compute_ms"] = round((time.perf_counter() - began) * 1000, 2)
    return payload


_CACHE = {}


def cached_season_ledger(store, season=None, scopes=("all", "same"), minimum_matches=DEFAULT_MINIMUM_MATCHES):
    """One ledger per finalized-data revision; reused by every workspace rebuild."""
    with store.lock:
        incoming = store.get_meta("upcoming", {}).get("season")
        key = (str(store.path), store.get_meta("archive_id"), store.fp_revision, int(season or incoming or 0),
               tuple(scopes), minimum_matches)
    ready = _CACHE.get(key)
    if ready is not None:
        return {**ready, "cached": True}
    value = season_ledger(store, season, scopes, minimum_matches)
    _CACHE.clear()
    _CACHE[key] = value
    return {**value, "cached": False}
