"""Find whole seasons the source still serves but this archive never learned about.

The collector's history worker can only fill matchdays it knows exist. That is why entire
seasons can be absent from the archive while the website still shows them: nothing ever told
this application that season 3135965 exists. The rolling ten-season list the source publishes
explains the rest — a season that scrolls out of that list is never announced again, even
though `matches/results?season=<id>&matchday=<n>` still serves it.

This module answers one question, as cheaply as possible in a request budget shared with live
collection: *which season ids between the ones we know are still valid?*

Measured behaviour of the source (2026-09-20):

* a valid season id returns its rows **and** a `season_matchdays` list of the matchdays it has
  published so far; a finished season lists all 30;
* an id that is not a season returns `query.matchday = 0` with an empty row list — a decisive
  negative, so a probe is unambiguous;
* valid ids are **single points**, not ranges (3135935 is a season, 3135936 is not), so a gap
  must be probed id by id. Across 2,287 ids only ~50 are seasons, so probing is affordable but
  it must be prioritised, resumable and observable rather than a blind sweep;
* asking for a matchday a season has not published is **clamped** to its last published day and
  answered with that day's finals — so a day number can never be used to prove a day exists.
  `season_matchdays` is the only trustworthy statement of what the source has.

The scan is therefore one request per id, newest-first, and it records every hit so the fill
worker can fetch the season (its day list arrives with the first probe).
"""
from __future__ import annotations

import time

DAY_MAX = 30

# Measured spacing between consecutive season ids in this competition: 22-33, clustered near 27.
# The walk probes the most likely distance first, so the next season is usually found in one or two
# requests instead of the ~28 a linear sweep would need. Anything the walk cannot see is caught by
# the sweep pass, which walks every remaining id in the window and is what *proves* completeness.
NEIGHBOUR_OFFSETS = (27, 28, 26, 29, 25, 30, 24, 31, 23, 32, 22, 33,
                     34, 35, 36, 37, 38, 39, 40, 41, 42, 21, 20)
WALK_LIMIT = 44                 # a season further away than this ends the fast walk for the window


def windows(known_seasons, newest_season, floor):
    """The id windows worth probing: the gaps between known seasons, newest first.

    ``known_seasons`` are the ids already in the archive, ``newest_season`` is the newest id the
    source has announced (rolling list or the upcoming matchday), ``floor`` is the archive's
    first season — nothing below it is collectable and the source no longer serves it.
    """
    known = sorted({int(s) for s in known_seasons if s})
    if not known:
        return [{"lo": int(floor) + 1, "hi": int(newest_season)}] if newest_season else []
    spans = []
    highest = max(known[-1], int(newest_season or 0))
    for lower, upper in zip([max(int(floor), known[0] - 1)] + known, known + [highest]):
        lo, hi = int(lower) + 1, int(upper) - 1
        if hi >= lo:
            spans.append({"lo": lo, "hi": hi})
    # Widest gap first, then newest. A gap of a few ids between two archived seasons is almost
    # always empty because consecutive seasons are 22-33 ids apart, while a gap of hundreds of ids
    # is where whole seasons are hiding; ordering by width finds seasons — and therefore starts
    # filling them — as early as possible, which is the point of the scan.
    return sorted(spans, key=lambda span: (-(span["hi"] - span["lo"]), -span["hi"]))


class SeasonScanner:
    """Resumable id scan. One probe per call, so the caller controls the request pace."""

    def __init__(self, store, feed, max_attempts=3):
        self.store = store
        self.feed = feed
        self.max_attempts = max_attempts

    # ------------------------------------------------------------------ state
    def state(self):
        state = self.store.get_meta("season_scan", None)
        if not state:
            state = self._fresh()
        return state

    def _fresh(self):
        newest = self.store.get_meta("published_seasons", {}).get("ids") or []
        upcoming = self.store.get_meta("upcoming", {}).get("season")
        newest = max([int(s) for s in newest] + ([int(upcoming)] if upcoming else []), default=None)
        if not newest:
            newest = self.store.one("SELECT MAX(id) id FROM seasons")["id"]
        known = [row["id"] for row in self.store.all("SELECT id FROM seasons")]
        floor = min(known) if known else self.store.start_season
        spans = [dict(span, base=span["lo"] - 1, next=span["lo"], checked=0, hits=0,
                      mode="walk", swept=0, misses=0) for span in windows(known, newest, floor)]
        return {"windows": spans, "newest_season": newest, "floor": floor,
                "found": [], "probes": 0, "started_at": time.time(), "updated_at": time.time()}

    def reset(self, newest_season=None):
        state = self._fresh()
        if newest_season:
            state["newest_season"] = int(newest_season)
            state["windows"] = [dict(span, base=span["lo"] - 1, next=span["lo"], checked=0, hits=0,
                                     mode="walk", swept=0, misses=0)
                                for span in windows([r["id"] for r in self.store.all("SELECT id FROM seasons")],
                                                    newest_season, state["floor"])]
        self.store.set_meta("season_scan", state)
        return state

    def refresh(self):
        """Extend the scan when the source announces a newer season than the scan knows about."""
        state = self.state()
        newest = self.store.get_meta("published_seasons", {}).get("ids") or []
        newest = max([int(s) for s in newest] + [state.get("newest_season") or 0], default=state.get("newest_season"))
        if newest and newest > (state.get("newest_season") or 0):
            return self.reset(newest_season=newest)
        return state

    # ------------------------------------------------------------------ scanning
    def _candidates(self, span):
        """The ids this window should ask about, in the order they are worth asking."""
        lo, hi = span["lo"], span["hi"]
        mode = span.get("mode", "walk")
        if mode == "walk":
            base = max(span.get("base", lo - 1), lo - 1)
            out = [base + offset for offset in NEIGHBOUR_OFFSETS
                   if lo <= base + offset <= hi]
            if out:
                return out
            span["mode"] = "sweep"
            span["swept"] = lo
            mode = "sweep"
        start = max(lo, span.get("swept", lo))
        return list(range(start, hi + 1))

    def next_probe(self):
        """The id to probe next, or None when every window is exhausted."""
        state = self.state()
        for span in state["windows"]:
            for candidate in self._candidates(span):
                if not self.store.has_probe(candidate):
                    return candidate
            # an exhausted window: if the walk could not see any further, sweep it to prove it
            if span.get("mode", "walk") == "walk":
                span["mode"] = "sweep"
                span["swept"] = span["lo"]
                self.store.set_meta("season_scan", state)
                for candidate in range(span["lo"], span["hi"] + 1):
                    if not self.store.has_probe(candidate):
                        return candidate
        return None

    def mode_of(self, probe):
        for span in self.state()["windows"]:
            if span["lo"] <= probe <= span["hi"]:
                return span.get("mode", "walk")
        return None

    def pending(self):
        """Requests the scan still intends to make, across the walk and the sweep."""
        state = self.state()
        total = 0
        for span in state["windows"]:
            total += sum(1 for candidate in self._candidates(dict(span))
                         if not self.store.has_probe(candidate))
        return total

    def record_hit(self, season, days=None, state=None):
        """Remember a season the source still serves. `step` passes its own copy so the single save at
        the end of the probe cannot clobber this list (it did, and the found seasons were lost)."""
        owned = state is None
        if owned:
            state = self.state()
        found = state.setdefault("found", [])
        if int(season) not in [int(entry["season"]) for entry in found]:
            found.append({"season": int(season), "found_at": time.time(),
                          "published_days": len(days or []), "days": [int(d) for d in (days or [])]})
        state["updated_at"] = time.time()
        if owned:
            self.store.set_meta("season_scan", state)

    async def step(self, lane="archive"):
        """Probe one id. Returns a small result dict; never raises for an ordinary negative."""
        state = self.refresh()
        probe = self.next_probe()
        if probe is None:
            return {"done": True, "probes": state["probes"]}
        span = next(span for span in state["windows"] if span["next"] <= span["hi"])
        result = {"season": probe, "valid": False, "published_days": []}
        try:
            payload, digest, stamp = await self.feed.get(
                "matches/results", {"competition_id": self.store.competition,
                                    "season": probe, "matchday": 1}, "discovery", lane=lane)
            data = payload.get("data") or {}
            query = data.get("query") or {}
            days = [int(d) for d in (data.get("season_matchdays") or []) if str(d).isdigit()]
            valid = (str(query.get("season")) == str(probe) and int(query.get("matchday") or 0) > 0
                     and bool(days) and bool(data.get("results")))
            if valid:
                result.update(valid=True, published_days=days)
                span["hits"] += 1
                span["base"] = probe                      # walk on from the season we just found
                span["misses"] = 0
                self.record_hit(probe, days, state)
                self.store.record_published(probe, days)
                self.store.discover([probe])
                self.store.log("discovery", f"Season {probe} is still published by the source "
                                            f"({len(days)} matchday(s) with results) · queued for collection")
        except Exception as exc:                      # noqa: BLE001 — a failed probe is retried later
            result["error"] = str(exc)[:160]
            self.store.log("discovery", f"Probe S{probe}: {str(exc)[:160]}", "warning")
            span["failures"] = span.get("failures", 0) + 1
            if span["failures"] <= self.max_attempts:
                self.store.set_meta("season_scan", state)
                return result
        self.store.record_probe(probe, result["valid"], len(result.get("published_days") or []))
        if not result["valid"] and span.get("mode", "walk") == "walk":
            span["misses"] = span.get("misses", 0) + 1
            if span["misses"] >= len(NEIGHBOUR_OFFSETS):
                # nothing within WALK_LIMIT ids of the last hit: let the slow sweep take the window
                span["mode"] = "sweep"
                span["swept"] = max(span["lo"], span.get("base", span["lo"] - 1))
        if span.get("mode") == "sweep":
            span["swept"] = probe + 1
        span["next"] = probe + 1
        span["checked"] += 1
        state["probes"] = state.get("probes", 0) + 1
        state["updated_at"] = time.time()
        if probe >= span["hi"]:
            self.store.log("discovery", f"Scan window {span['lo']}–{span['hi']} finished · "
                                        f"{span['hits']} season(s) found")
        self.store.set_meta("season_scan", state)
        return result

    def summary(self):
        state = self.state()
        found = state.get("found") or []
        checked, hits = self.store.probe_totals()
        return {"windows": [{"lo": s["lo"], "hi": s["hi"], "next": s["next"], "checked": s["checked"],
                             "hits": s["hits"], "mode": s.get("mode", "walk"),
                             "remaining": sum(1 for candidate in self._candidates(dict(s))
                                              if not self.store.has_probe(candidate))}
                            for s in state.get("windows") or []],
                "walk_windows": sum(1 for s in state.get("windows") or [] if s.get("mode", "walk") == "walk"),
                "sweep_windows": sum(1 for s in state.get("windows") or [] if s.get("mode") == "sweep"),
                "pending": self.pending(), "probes": state.get("probes", 0),
                "found": found[-12:], "found_total": len(found),
                "ids_checked": checked, "ids_valid": hits,
                "newest_season": state.get("newest_season"), "updated_at": state.get("updated_at")}
