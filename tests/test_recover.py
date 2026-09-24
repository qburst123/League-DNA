"""The on-demand recovery worker.

No network is touched: the collector is faked, and its ``fetch_matchday`` either
closes the requested matchday (success) or leaves it open (failure), so the
queue, the retry/backoff reset, the recovered/failed accounting and the
"recover all" selection rules are verified offline.
"""
import asyncio
import time

import pytest
from backend.store import Store
from backend.recover import Recoverer


class FakeFeed:
    def __init__(self):
        self.shapes = {"archive": 0.55, "live": 0.45}
        self.set_share_calls = []

    def set_lane_share(self, share):
        self.set_share_calls.append(share)
        self.shapes = {"archive": share, "live": 1.0 - share}


class FakeCollector:
    def __init__(self, store, succeed):
        self.store = store
        self.feed = FakeFeed()
        self.succeed = succeed          # set of (season, day) that a fetch will close
        self.fetched = []

    def current_season(self):
        return self.store.get_meta("upcoming", {}).get("season")

    async def fetch_matchday(self, job, verify=False):
        season, day = job["season"], job["day"]
        self.fetched.append((season, day))
        if (season, day) in self.succeed:
            with self.store.lock:
                self.store.db.execute(
                    "UPDATE matchdays SET status='complete', match_count=8, final_count=8, error=NULL "
                    "WHERE season=? AND day=?", (season, day))
                self.store.bump()
        else:
            self.store.attempt(season, day, "Source returned matchday 30, not %d" % day)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "recover.sqlite")
    now = time.time()
    # Season 100 is in the past, season 200 is the live season.
    for season in (100, 200):
        s.db.execute("INSERT INTO seasons(id, discovered_at) VALUES(?, ?)", (season, now))
        s.db.executemany("INSERT INTO matchdays(season, day) VALUES(?, ?)",
                         [(season, d) for d in range(1, 31)])
    # Season 100: days 1-28 complete, day 29 partial-with-error, day 30 pending.
    s.db.execute("UPDATE matchdays SET status='complete', match_count=8, final_count=8 WHERE season=100 AND day<=28")
    s.db.execute("UPDATE matchdays SET status='partial', match_count=5, final_count=5, error='earlier failure', "
                 "next_retry=? WHERE season=100 AND day=29", (now + 9999,))
    # Season 100 published all 30 days.
    s.db.executemany("INSERT INTO season_published(season, day, first_seen, last_seen) VALUES(100, ?, ?, ?)",
                     [(d, now, now) for d in range(1, 31)])
    # Season 200 (live): days 1-3 complete, day 4 partial, days 5+ pending (future).
    s.db.execute("UPDATE matchdays SET status='complete', match_count=8, final_count=8 WHERE season=200 AND day<=3")
    s.db.execute("UPDATE matchdays SET status='partial', match_count=4, final_count=4 WHERE season=200 AND day=4")
    s.set_meta("upcoming", {"season": 200, "day": 5})
    yield s
    s.close()


def drain(recoverer, timeout=2.0):
    """Run the worker until the queue empties (or a timeout)."""
    async def _go():
        recoverer.start()
        deadline = time.time() + timeout
        while (recoverer.queue or recoverer.inflight) and time.time() < deadline:
            await asyncio.sleep(0.01)
        await recoverer.stop()
    asyncio.run(_go())


def test_enqueue_cell_clears_backoff_and_recovers(store):
    collector = FakeCollector(store, succeed={(100, 29)})
    rec = Recoverer(store, collector)
    state = rec.enqueue_cell(100, 29)
    assert state["queue"] == 1
    # The 180 s retry backoff and the stored error were cleared immediately.
    row = store.one("SELECT next_retry, error FROM matchdays WHERE season=100 AND day=29")
    assert row["next_retry"] == 0 and row["error"] is None
    drain(rec)
    status = rec.status()
    assert status["recovered"] == 1 and status["failed"] == 0 and status["active"] is False
    assert store.one("SELECT status FROM matchdays WHERE season=100 AND day=29")["status"] == "complete"


def test_failed_cell_is_reported_not_faked(store):
    collector = FakeCollector(store, succeed=set())      # nothing can be closed
    rec = Recoverer(store, collector)
    rec.enqueue_cell(200, 4)
    drain(rec)
    status = rec.status()
    assert status["recovered"] == 0 and status["failed"] == 1
    assert status["recent"][0]["recovered"] is False
    assert store.one("SELECT status FROM matchdays WHERE season=200 AND day=4")["status"] != "complete"


def test_enqueue_all_recovers_published_and_past_not_future(store):
    # Only season 100 day 30 can actually be closed.
    collector = FakeCollector(store, succeed={(100, 30), (100, 29)})
    rec = Recoverer(store, collector)
    state = rec.enqueue_all()
    queued = {(j["season"], j["day"]) for j in rec.queue}
    # Published-but-unfilled and past-season matchdays are queued …
    assert (100, 29) in queued and (100, 30) in queued and (200, 4) in queued
    # … but future matchdays of the live season (day >= upcoming day 5) are not.
    assert not any(s == 200 and d >= 5 for (s, d) in queued)
    # Newest season first.
    assert rec.queue[0]["season"] == 200
    drain(rec)
    status = rec.status()
    assert status["recovered"] == 2 and status["failed"] == 1
    assert store.one("SELECT status FROM matchdays WHERE season=100 AND day=30")["status"] == "complete"


def test_no_duplicate_queue_entries(store):
    collector = FakeCollector(store, succeed=set())
    rec = Recoverer(store, collector)
    rec.enqueue_cell(100, 29)
    rec.enqueue_cell(100, 29)
    assert len(rec.queue) == 1
