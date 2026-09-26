"""Public-endpoint fetcher for the Betika Virtual Edge Analyzer.

Used by:
* `backend/betika_edge/edge.bootstrap()` to populate the markets cache
  from the live collector when present, or from `tests/fixtures/`
  when it isn't.
* `betika_analyzer/scraper.py` as a thin CLI.

Same guardrails as the live collector
(`backend/source.PublicFeed`):
* GET-only against https://virtuals.betika.com/v1/ (competition_id=26).
* 1 request / 2 seconds.
* Every response is cached to ./cache/<sha256>.json.
* Falls back to `tests/fixtures/*.json` when the network fails.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import httpx as _http_lib

log = logging.getLogger("betika.scraper")

BASE = "https://virtuals.betika.com/v1/"
COMPETITION = 26
RATE_LIMIT_SECONDS = 2.0
USER_AGENT = (
    "Mozilla/5.0 (compatible; BetikaVirtualEdgeAnalyzer/1.0; "
    "+https://example.invalid/betika-virtual-edge-analyzer) "
    "read-only public data; rate-limited to 1req/2s"
)

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "betika_analyzer" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class RateLimitedSession:
    def __init__(self, min_interval: float = RATE_LIMIT_SECONDS):
        self.min_interval = min_interval
        self._last = 0.0
        if hasattr(_http_lib, "Session"):
            self.session = _http_lib.Session()
            self.session.headers.update({
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "en-KE,en;q=0.9",
            })
            self._is_async = False
        else:
            self.session = _http_lib.Client(timeout=_http_lib.Timeout(20),
                                            headers={"User-Agent": USER_AGENT,
                                                     "Accept": "application/json"})
            self._is_async = True

    def _sleep(self):
        elapsed = time.time() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha256(url.encode()).hexdigest()[:24]
        return CACHE_DIR / f"{h}.json"

    def fetch(self, url: str, use_cache: bool = True, timeout: float = 15.0) -> Optional[Any]:
        path = self._cache_path(url)
        if use_cache and path.exists():
            try:
                with path.open() as fh:
                    return json.load(fh)
            except Exception:
                pass
        self._sleep()
        try:
            if self._is_async:
                r = self.session.get(url)
                self._last = time.time()
                if r.status_code != 200:
                    log.warning("HTTP %s for %s", r.status_code, url)
                    return None
                payload = r.json()
            else:
                r = self.session.get(url, timeout=timeout)
                self._last = time.time()
                if r.status_code != 200:
                    log.warning("HTTP %s for %s", r.status_code, url)
                    return None
                payload = r.json()
            with path.open("w") as fh:
                json.dump(payload, fh)
            return payload
        except Exception as e:
            log.warning("request failed for %s: %s", url, e)
            return None


@dataclass
class ScrapeResult:
    fixtures: list = field(default_factory=list)
    markets: list = field(default_factory=list)
    results: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def summary(self) -> str:
        return (f"fixtures={len(self.fixtures)} markets={len(self.markets)} "
                f"results={len(self.results)} errors={len(self.errors)} "
                f"notes={len(self.notes)}")


class BetikaVirtualScraper:
    """Public-endpoint fetcher. Safe to call; falls back to fixtures on error."""

    def __init__(self, session: Optional[RateLimitedSession] = None):
        self.session = session or RateLimitedSession()

    def probe_competition(self) -> dict:
        url = f"{BASE}competition?id={COMPETITION}"
        data = self.session.fetch(url)
        return {"reachable": bool(data), "preview": str(data)[:400] if data else None}

    def fetch_fixtures(self) -> list:
        url = f"{BASE}matches?competition_id={COMPETITION}"
        data = self.session.fetch(url)
        if not data or not isinstance(data, dict):
            return []
        return list(data.get("data", {}).items())

    def fetch_ongoing(self) -> list:
        url = f"{BASE}matches/ongoing?competition_id={COMPETITION}"
        data = self.session.fetch(url)
        if not data:
            return []
        return data.get("data") if isinstance(data, dict) else []

    def fetch_results(self, season: int, matchday: int) -> list:
        url = f"{BASE}matches/results?competition_id={COMPETITION}&season={season}&matchday={matchday}"
        data = self.session.fetch(url)
        if not data or not isinstance(data, dict):
            return []
        body = data.get("data") or {}
        return body.get("results") or []

    def fetch_markets(self, event_id: str) -> Optional[dict]:
        url = f"{BASE}match?event_id={event_id}"
        return self.session.fetch(url)

    def warm_archive(self, seasons_and_matchdays) -> ScrapeResult:
        r = ScrapeResult()
        try:
            r.notes.append(str(self.probe_competition()))
            for season, day in seasons_and_matchdays:
                rows = self.fetch_results(season, day)
                r.results.extend(rows)
        except Exception as e:
            r.errors.append(f"warm_archive crashed: {e}")
            log.exception("warm_archive failed")
        return r
