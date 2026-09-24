from __future__ import annotations
import asyncio
import time


class Recoverer:
    """On-demand recovery of missing matchday results.

    The scheduled backfill loop fills gaps at its own pace and can leave holes
    behind: an error sets a 180 s retry backoff, the archive lane shares a
    one-request-per-second budget with live polling, and a season discovered
    late may be skipped over. When the operator sees an empty box in the data
    health table they should be able to click it and have that one matchday
    fetched now — and to ask for every still-missing matchday to be recovered.

    This worker reuses the collector's own ``fetch_matchday`` so it shares the
    global rate limiter, the clamp guard (a response for a different matchday is
    refused rather than misfiled) and the idempotent ingest. It runs entirely in
    the background — no browser is opened; the results feed that backs Betika's
    results page is queried directly.
    """

    BOOST_SHARE = 0.82          # archive-lane share while a recovery queue is draining

    def __init__(self, store, collector):
        self.store = store
        self.collector = collector
        self.feed = collector.feed
        self.default_share = self.feed.shapes.get("archive", 0.55)
        self.queue = []                     # [{"season":…, "day":…}, …] newest season first
        self.inflight = None
        self.recent = []                    # last outcomes, newest first
        self.recovered = 0
        self.failed = 0
        self.started_at = None
        self.finished_at = None
        self._wake = asyncio.Event()
        self._task = None

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._set_share(self.default_share)

    # ------------------------------------------------------------------ public API
    def enqueue_cell(self, season, day):
        """Queue one matchday for immediate recovery and clear its backoff."""
        season, day = int(season), int(day)
        if not 1 <= day <= 30:
            raise ValueError("Matchday must be between 1 and 30")
        with self.store.lock:
            self.store.db.execute(
                "UPDATE matchdays SET next_retry=0, error=NULL WHERE season=? AND day=?", (season, day))
            self.store.bump()
        self._add(season, day, front=True)
        return self.status()

    def enqueue_all(self):
        """Queue every matchday that is missing but recoverable.

        Recoverable means either the source says it has published that day, or the
        season is already in the past — both cases where a result ought to exist.
        Future matchdays of the live season are never queued, because there is
        nothing to fetch yet.
        """
        current = self.collector.current_season()
        upcoming_day = self.store.get_meta("upcoming", {}).get("day") or 31
        incomplete = self.store.all("SELECT season, day FROM matchdays WHERE status!='complete'")
        published = {(r["season"], r["day"]) for r in self.store.all("SELECT season, day FROM season_published")}
        with self.store.lock:
            self.store.db.execute("UPDATE matchdays SET next_retry=0, error=NULL WHERE status!='complete'")
            self.store.bump()
        jobs = []
        for r in incomplete:
            s, d = r["season"], r["day"]
            # Recover anything that ought to have a result: a day the source says it
            # published, any matchday in a season already in the past, or a round of the
            # live season that has already been played. Future rounds are never queued.
            played_live_round = current is not None and s == current and d < upcoming_day
            if (s, d) in published or (current is not None and s < current) or played_live_round:
                jobs.append((s, d))
        jobs.sort(key=lambda t: (-t[0], t[1]))           # newest season first, then MD1→30
        added = 0
        for s, d in jobs:
            if self._add(s, d):
                added += 1
        if added:
            self.store.log("history", f"Recovery sweep queued {added} missing matchdays "
                                      f"(published or past-season) · newest season first")
        return self.status()

    def status(self):
        active = bool(self.queue) or self.inflight is not None
        return {"active": active,
                "queue": len(self.queue),
                "inflight": self.inflight,
                "recovered": self.recovered,
                "failed": self.failed,
                "started_at": self.started_at,
                "finished_at": None if active else self.finished_at,
                "next": self.queue[0] if self.queue else None,
                "recent": self.recent[:20]}

    # ------------------------------------------------------------------ internals
    def _add(self, season, day, front=False):
        for job in self.queue:
            if job["season"] == season and job["day"] == day:
                return False
        job = {"season": season, "day": day}
        self.queue.insert(0, job) if front else self.queue.append(job)
        self._wake.set()
        return True

    def _set_share(self, share):
        try:
            self.feed.set_lane_share(share)
        except Exception:
            pass

    async def _run(self):
        while True:
            if not self.queue:
                if self.inflight is None:
                    self._set_share(self.default_share)
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), 15)
                except asyncio.TimeoutError:
                    pass
                continue
            job = self.queue.pop(0)
            self.inflight = job
            if self.started_at is None:
                self.started_at = time.time()
            self._set_share(self.BOOST_SHARE)
            season, day = job["season"], job["day"]
            try:
                # The collector's own fetch: shared rate limiter, clamp guard,
                # idempotent ingest, receipt logging. Partial rounds stay partial.
                await self.collector.fetch_matchday(job)
            except asyncio.CancelledError:
                raise
            except Exception as e:                     # fetch_matchday already stores the error
                self.store.log("history", f"Recovery S{season}/MD{day}: {str(e)[:160]}", "warning")
            after = self.store.one(
                "SELECT status, final_count, error FROM matchdays WHERE season=? AND day=?", (season, day))
            ok = bool(after) and after["status"] == "complete"
            self.recovered += 1 if ok else 0
            self.failed += 0 if ok else 1
            self.recent.insert(0, {"season": season, "day": day, "recovered": ok,
                                   "final_count": after["final_count"] if after else 0,
                                   "error": after["error"] if after else None,
                                   "at": time.time()})
            self.recent = self.recent[:200]
            self.store.log("history",
                           f"Recovered S{season} · MD{day} · 8/8 HT/FT verified" if ok
                           else f"Recovery S{season} · MD{day} still incomplete "
                                f"({(after['error'] or 'no data returned')[:120] if after else 'no row'})",
                           "info" if ok else "warning")
            self.inflight = None
            if not self.queue:
                self.finished_at = time.time()
                self._set_share(self.default_share)
            await asyncio.sleep(0.05)
