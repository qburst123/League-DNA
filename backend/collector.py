from __future__ import annotations
import asyncio
import contextlib
import time

from .source import PublicFeed, SourceError, COMPETITION, START_SEASON, parse_result, parse_upcoming, parse_ongoing, clean_markets
from .discovery import SeasonScanner
from .patterns import KINDS
from .gold import compare_correct_scores
from .trail import observe_single_hits


class Collector:
    def __init__(self, store):
        self.store = store
        self.feed = PublicFeed(store)
        self.tasks = []
        self.wake = asyncio.Event()
        self.refresh_requested = asyncio.Event()
        self.stopping = False
        self.last_closed_revision = -1
        # The season-id scan that finds whole seasons the rolling ten-season list never showed us.
        self.scanner = SeasonScanner(store, self.feed)
        self.requests = 0                 # requests the history worker has spent, for the ETA
        self.filled = 0                   # matchdays closed by the history worker this run
        self.stop_scan = False            # set by the API when the operator wants discovery paused

    async def start(self):
        self.store.log("system", "Read-only collector started · Betika public feeds · maximum 1 request/second")
        self.tasks = [asyncio.create_task(self.loop("fixtures", self.fixtures, 10)),
                      asyncio.create_task(self.loop("ongoing", self.ongoing, 5)),
                      asyncio.create_task(self.loop("results", self.latest, 18)),
                      asyncio.create_task(self.loop("markets", self.markets, 12)),
                      asyncio.create_task(self.history()),
                      asyncio.create_task(self.warm_comparisons())]
        self.store.log("history", "Gap filler started · published matchdays first, then a season-id scan "
                                  "for seasons the source never announced")

    async def loop(self, name, action, seconds):
        while not self.stopping:
            try:
                await action()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.log(name, f"{name.title()} sync: {str(e)[:200]}", "warning")
            try:
                await asyncio.wait_for(self.refresh_requested.wait(), timeout=seconds)
                self.refresh_requested.clear()
            except TimeoutError:
                pass

    async def fixtures(self):
        payload, digest, stamp = await self.feed.get("matches", {"competition_id": COMPETITION}, "upcoming")
        groups = parse_upcoming(payload)
        if not groups:
            self.store.log("fixtures", "Source returned no upcoming fixtures; keeping the last observation", "warning")
            return
        # The API's first group corresponds to the Lite website's topmost group.
        first = groups[0]
        rows = first["rows"]
        if len(rows) != 8:
            raise ValueError("Incomplete upcoming matchday; waiting for all eight matches")
        old = self.store.get_meta("upcoming", {})
        self.store.ingest(rows, digest, stamp)
        for row in rows:
            self.store.save_markets(row["event_id"], row["markets"], digest, stamp, full=False)
        up = {"season": first["season"], "day": first["day"], "start_time": first["start_time"],
              "source_timer": first["source_timer"], "fetched_at": stamp, "source_at": self.store.source_time("matches"),
              "event_ids": [r["event_id"] for r in rows],
              "teams": [n for r in rows for n in (r["home"], r["away"])],
              "source_hash": digest,
              "following": [{"season": g["season"], "day": g["day"], "start_time": g["start_time"]} for g in groups[1:3]]}
        self.store.set_meta("upcoming", up)
        self.store.bump()
        if (old.get("season"), old.get("day")) != (first["season"], first["day"]):
            self.store.log("fixtures", f"New upcoming matchday · S{first['season']} / MD{first['day']} · 16 teams")
            self.wake.set()

    async def ongoing(self):
        payload, digest, stamp = await self.feed.get("matches/ongoing", {"competition_id": COMPETITION}, "live")
        rows = parse_ongoing(payload)
        if not rows:
            return
        self.store.ingest(rows, digest, stamp)
        first = rows[0]
        self.store.set_meta("ongoing", {"season": first["season"], "day": first["day"], "fetched_at": stamp,
                                         "source_at": self.store.source_time("matches/ongoing"),
                                         "source_hash": digest, "start_time": first["start_time"]})
        self.store.bump()

    async def latest(self):
        payload, digest, stamp = await self.feed.get("matches/results", {"competition_id": COMPETITION}, "results")
        parsed = parse_result(payload)
        self.store.discover(parsed["seasons"])
        self.store.record_published(parsed["season"], parsed["matchdays"])
        self.store.ingest(parsed["rows"], digest, stamp)
        self.store.set_meta("latest_result", {"season": parsed["season"], "day": parsed["day"], "fetched_at": stamp})
        self.store.set_meta("published_seasons", {"ids": parsed["seasons"], "fetched_at": stamp, "source_hash": digest})
        self.wake.set()

    async def markets(self):
        up = self.store.get_meta("upcoming", {})
        for event_id in up.get("event_ids", []):
            current = self.store.get_meta("upcoming", {})
            if event_id not in current.get("event_ids", []):
                return
            stored = self.store.one("SELECT full,fetched_at FROM markets WHERE event_id=?", (event_id,))
            if stored and stored["full"] and time.time() - stored["fetched_at"] < 60:
                continue
            # After the cutoff, keep timestamped historical odds, not stale 'live' odds.
            if up.get("start_time") and up["start_time"] <= time.time():
                return
            payload, digest, stamp = await self.feed.get("match", {"id": event_id}, "markets")
            markets = clean_markets(payload.get("data", []))
            meta = payload.get("meta", [])
            meta = meta[0] if isinstance(meta, list) and meta else (meta if isinstance(meta, dict) else {})
            if not markets:
                self.store.log("markets", f"Markets not published for event {event_id}; retained last observation", "warning")
                continue
            if meta.get("parent_virtual_id") and str(meta["parent_virtual_id"]) != event_id:
                raise ValueError("Market response does not match requested fixture")
            # Match IDs were read from competition 26, not enumerated or guessed.
            self.store.save_markets(event_id, markets, digest, stamp, full=True, meta=meta)

    # ------------------------------------------------------------------ what to fetch, in order
    def current_season(self):
        up = self.store.get_meta("upcoming", {})
        latest = self.store.get_meta("latest_result", {})
        return up.get("season") or latest.get("season")

    def next_history_job(self):
        """The next matchday worth a request, or None.

        Order of value, not of convenience:

        1. a matchday the source has **published results for** and this archive has not closed —
           newest season first, so a gap in recent history is filled before an old one;
        2. a season the source has published where we do not yet know its day list — one request
           (its first matchday) teaches the whole list, which is also how a newly found season
           starts collecting;
        3. otherwise nothing: the caller moves on to the id scan or the verification sweep.
        """
        current = self.current_season()
        known = self.store.one("""
            SELECT m.season,m.day FROM matchdays m
            JOIN season_published p ON p.season=m.season AND p.day=m.day
            WHERE m.status!='complete' AND m.next_retry<=?
            ORDER BY (m.season=?) DESC, m.season DESC, m.day ASC LIMIT 1""", (time.time(), current))
        if known:
            known = dict(known)
            known["reason"] = "published matchday still open"
            return known
        # A season that has open matchdays but no published list yet: one request teaches the
        # whole list, and without it we cannot tell an unplayed matchday from a missing one.
        unpublished = self.store.one("""
            SELECT m.season,MIN(m.day) day FROM matchdays m
            WHERE m.status!='complete' AND m.next_retry<=?
              AND NOT EXISTS (SELECT 1 FROM season_published p WHERE p.season=m.season)
            GROUP BY m.season ORDER BY (m.season=?) DESC, m.season DESC LIMIT 1""", (time.time(), current))
        if unpublished:
            unpublished = dict(unpublished)
            unpublished["reason"] = "learn what this season has published"
            return unpublished
        return None

    def next_verify_job(self):
        """Last in line: record what a finished season published, then re-check closed matchdays.

        This runs only when there is nothing left to fill or discover, so it never competes with the
        gap work. Re-checking matters because the source can correct a score, and a corrected score
        must reach the archive.
        """
        unlisted = self.store.one("""
            SELECT s.id season FROM seasons s
            WHERE NOT EXISTS (SELECT 1 FROM season_published p WHERE p.season=s.id)
              AND EXISTS (SELECT 1 FROM matchdays m WHERE m.season=s.id AND m.status='complete')
            ORDER BY s.id DESC LIMIT 1""")
        if unlisted:
            return {"season": unlisted["season"], "day": 1, "reason": "record what this season published"}
        row = self.store.one("""
            SELECT m.season,m.day FROM matchdays m
            WHERE m.status='complete' AND m.next_retry<=?
            ORDER BY COALESCE(m.last_attempt,0) ASC, m.season ASC, m.day ASC LIMIT 1""", (time.time(),))
        if not row:
            return None
        job = dict(row)
        job["reason"] = "verification sweep"
        return job

    def plan(self):
        """What is left to do and how long it should take at the shared one-request-per-second pace."""
        queue = self.store.queue_state()
        scan_pending = self.scanner.pending() if not self.stop_scan else 0
        unknown_seasons = self.store.one("""
            SELECT COUNT(*) n FROM seasons s WHERE NOT EXISTS(
              SELECT 1 FROM season_published p WHERE p.season=s.id)""")["n"]
        unknown_seasons += self.store.one("""
            SELECT COUNT(*) n FROM matchdays m WHERE m.status!='complete'
              AND NOT EXISTS (SELECT 1 FROM season_published p WHERE p.season=m.season)""")["n"]
        requests = queue["published_unfilled"] + unknown_seasons + scan_pending
        return {**queue, "unknown_seasons": unknown_seasons, "scan_pending": scan_pending,
                "requests_estimate": requests, "eta_seconds": requests, "scan_paused": bool(self.stop_scan),
                "filled_this_run": self.filled, "requests_this_run": self.requests}

    def progress(self):
        up = self.store.get_meta("upcoming", {})
        latest = self.store.get_meta("latest_result", {})
        season = up.get("season") or latest.get("season")
        if not season:
            return {"state": "discovering", "completed": 0, "expected": 0, "percent": 0}
        current_day = max(up.get("day", 1) - 1, latest.get("day", 0) if latest.get("season") == season else 0)
        row = self.store.one("""SELECT COUNT(*) expected,SUM(status='complete') completed,
            SUM(error IS NOT NULL) errors FROM matchdays WHERE season<? OR (season=? AND day<=?)""", (season, season, current_day))
        row["completed"] = row["completed"] or 0
        row["percent"] = round(row["completed"] / row["expected"] * 100, 1) if row["expected"] else 0
        row["state"] = "paused" if self.store.get_meta("paused", False) else ("caught_up" if row["completed"] == row["expected"] else "collecting")
        row["updated_at"] = time.time()
        return row

    async def history(self):
        """Fill published matchdays, discover missing seasons, verify closed ones — in that order.

        One request per call: the shared rate limiter is what sets the pace, so this loop never
        sleeps longer than it must and never issues a request of its own outside that budget.
        """
        cycle = 0
        while not self.stopping:
            try:
                if self.store.get_meta("paused", False):
                    self.store.set_meta("backfill", {**self.progress(), "phase": "paused"})
                    await asyncio.sleep(2)
                    continue
                plan = self.plan()
                self.store.set_meta("backfill", {**self.progress(), "phase": "filling", **plan})
                job = self.next_history_job()
                if job:
                    await self.fetch_matchday(job)
                    cycle += 1
                    # Every fifth request, look for seasons we have never been told about. A hit
                    # is worth a season of fill work, so discovery must not starve behind it.
                    if not self.stop_scan and self.scanner.pending() and cycle % 5 == 0:
                        result = await self.scanner.step()
                        self.requests += 1
                        if result.get("valid"):
                            self.wake.set()
                    continue
                if not self.stop_scan and self.scanner.pending():
                    self.store.set_meta("backfill", {**self.progress(), "phase": "scanning", **self.plan()})
                    result = await self.scanner.step()
                    self.requests += 1
                    if result.get("valid"):
                        self.wake.set()
                    continue
                verify = self.next_verify_job()
                if verify:
                    self.store.set_meta("backfill", {**self.progress(), "phase": "verifying", **self.plan()})
                    await self.fetch_matchday(verify, verify=True)
                    # The sweep is a tidy-up, not a race: it yields so live collection and any new
                    # gap work always win the shared request budget.
                    await asyncio.sleep(6)
                    continue
                self.store.set_meta("backfill", {**self.progress(), "phase": "caught_up", **self.plan()})
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 15)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.log("backfill", str(e)[:220], "warning")
                await asyncio.sleep(10)

    async def fetch_matchday(self, job, verify=False):
        """Fetch one matchday and store it. Partial rounds stay partial — no fabricated scores."""
        season, day = job["season"], job["day"]
        current = self.current_season()
        try:
            payload, digest, stamp = await self.feed.get(
                "matches/results", {"season": season, "matchday": day, "competition_id": COMPETITION},
                "results", lane="archive")
            self.requests += 1
            parsed = parse_result(payload, season, day)
            self.store.discover(parsed["seasons"])
            self.store.record_published(season, parsed["matchdays"])
            self.store.ingest(parsed["rows"], digest, stamp)
            final_count = sum(r["status"] == "final" for r in parsed["rows"])
            error = None
            if len(parsed["rows"]) != 8 or final_count != 8:
                # A round may legitimately still be playing; say so instead of inventing zeros.
                upcoming = self.store.get_meta("upcoming", {})
                newer = season > (upcoming.get("season") or 0)
                playing_now = season == (upcoming.get("season") or season) and day >= (upcoming.get("day") or 0) - 1
                if not newer and not playing_now:
                    error = f"Source returned {len(parsed['rows'])}/8 matches and {final_count}/8 final scores"
            self.store.attempt(season, day, error)
            state = self.store.one("SELECT status FROM matchdays WHERE season=? AND day=?", (season, day))
            if state and state["status"] == "complete" and not verify:
                self.filled += 1
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.store.attempt(season, day, str(e)[:240])
            self.store.log("backfill", f"S{season} / MD{day}: {str(e)[:160]}", "warning")
        await asyncio.sleep(0.05)

    async def warm_comparisons(self):
        """Warm the four symbol comparisons and the full-prefix-first FT comparator."""
        previous = None
        while not self.stopping:
            try:
                up = self.store.get_meta("upcoming", {})
                marker = (up.get("season"), up.get("day"), self.store.fp_revision)
                if marker != previous and up.get("teams"):
                    for kind in KINDS:
                        await asyncio.to_thread(self.store.compare, kind, 100.0, "all")
                        await asyncio.sleep(0)
                    await asyncio.to_thread(compare_correct_scores, self.store, "all")
                    await asyncio.sleep(0)
                    previous = marker
                # Capture each fresh, exact-count-one state, even if source
                # freshness recovered without changing the score revision.
                await asyncio.to_thread(observe_single_hits, self.store)
                await asyncio.sleep(4)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.store.log("comparator", str(e)[:200], "warning")
                await asyncio.sleep(10)

    def request_sync(self):
        self.refresh_requested.set()
        self.wake.set()

    async def stop(self):
        self.stopping = True
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.feed.close()
