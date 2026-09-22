"""Strict adapters for the public, read-only Betika website feeds.

These routes were found in the public website JavaScript. Competition 26 was
cross-checked against the Lite page (names AND HT/FT scores, season 3134345 MD1).
No account, betslip, placement, wallet, or authentication endpoints are used.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import re
import time
from collections import deque
from datetime import datetime
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

import httpx

BASE = "https://virtuals.betika.com/v1/"
COMPETITION = 26
START_SEASON = 3134345
ZONE = ZoneInfo("Africa/Nairobi")
ALLOWED_ROUTES = {"competition", "matches", "matches/ongoing", "matches/results", "match"}
# 'ended' is the state actually observed in Betika's match contract. Do not
# infer completion from an unfamiliar or merely betting-closed status.
FINAL_STATUSES = {"ended"}


def score(value):
    if value is None:
        return None
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:\-]\s*(\d{1,2})\s*", str(value))
    return (int(m[1]), int(m[2])) if m else None


def start_timestamp(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=ZONE).timestamp() if dt.tzinfo is None else dt.timestamp()
    except (ValueError, TypeError):
        return None


def parse_result(payload, expected_season=None, expected_day=None):
    data = payload.get("data")
    if not isinstance(data, dict) or not isinstance(data.get("query"), dict):
        raise ValueError("Results feed schema changed: no query metadata")
    season, day = int(data["query"]["season"]), int(data["query"]["matchday"])
    if expected_season is not None and season != expected_season:
        raise ValueError(f"Source returned season {season}, not {expected_season}")
    if expected_day is not None and day != expected_day:
        raise ValueError(f"Source returned matchday {day}, not {expected_day}")
    if not 1 <= day <= 30:
        raise ValueError("Unexpected matchday")
    records = data.get("results", [])
    if not isinstance(records, list):
        raise ValueError("Results must be a list")
    rows, seen = [], set()
    for row in records:
        if int(row.get("competition_id", -1)) != COMPETITION:
            raise ValueError("Wrong competition: refusing to mix leagues")
        item = normalize(row, season, day, result=True)
        for name in (item["home"], item["away"]):
            if name in seen:
                raise ValueError("A team appeared twice in the same matchday")
            seen.add(name)
        rows.append(item)
    if len(rows) > 8:
        raise ValueError("Unexpected number of matches")
    return {"season": season, "day": day, "rows": rows,
            "seasons": [int(s) for s in data.get("last_10_seasons", []) if int(s) >= START_SEASON],
            "matchdays": [int(d) for d in data.get("season_matchdays", []) if 1 <= int(d) <= 30]}


def normalize(row, season=None, day=None, result=False):
    season = int(season or row["season"])
    day = int(day or row["match_day"])
    if season < START_SEASON or not 1 <= day <= 30:
        raise ValueError("Record outside requested scope")
    home, away = str(row.get("home_team", "")).strip(), str(row.get("away_team", "")).strip()
    if not home or not away or home == away:
        raise ValueError("Invalid team names")
    meta = row.get("meta") or {}
    source_status = str(meta.get("status") or row.get("status") or "")
    phase = str(meta.get("match_status") or row.get("match_status") or "")
    live_ht = score(row.get("ht_score"))
    live_ft = score(row.get("ft_score"))
    saved_ht, saved_ft = score(row.get("saved_ht_score")), score(row.get("saved_ft_score"))
    is_final = (source_status.lower() in FINAL_STATUSES or phase.lower() in FINAL_STATUSES
                or (result and saved_ht is not None and saved_ft is not None))
    # first_half_score is the FIRST GOAL indicator in this feed, NOT the HT score.
    ht = saved_ht if saved_ht is not None else live_ht
    ft = saved_ft if saved_ft is not None else live_ft
    if is_final and (ht is None or ft is None):
        is_final = False
    if is_final and (ht[0] > ft[0] or ht[1] > ft[1]):
        raise ValueError("Invalid final score: halftime exceeds fulltime")
    return {"event_id": str(row["parent_virtual_id"]) if row.get("parent_virtual_id") else None,
            "season": season, "day": day, "home": home, "away": away,
            "start_time": start_timestamp(row.get("start_time")),
            "status": "final" if is_final else ("live" if result or source_status else "scheduled"),
            "source_status": source_status, "phase": phase,
            "ht": ht if is_final else None, "ft": ft if is_final else None,
            "live_ht": live_ht, "live_ft": live_ft,
            "match_time": row.get("match_time"), "raw": row}


def parse_ongoing(payload):
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ValueError("Ongoing feed schema changed")
    parsed = [normalize(row) for row in rows]
    for row in parsed:
        # Ongoing feed is scoped by the verified competition_id query.
        if row["status"] == "scheduled":
            row["status"] = "live"
    return parsed


def parse_upcoming(payload):
    data = payload.get("data")
    if not isinstance(data, (dict, list)):
        raise ValueError("Upcoming feed schema changed")
    groups = data.values() if isinstance(data, dict) else data
    result = []
    for raw_group in groups:
        if not isinstance(raw_group, list) or not raw_group:
            continue
        rows = []
        for raw in raw_group:
            if int(raw.get("competition_id", -1)) != COMPETITION:
                raise ValueError("Unexpected competition in upcoming feed")
            item = normalize(raw)
            item["main_odds"] = {"1": raw.get("home_odd"), "X": raw.get("neutral_odd"), "2": raw.get("away_odd")}
            item["markets"] = clean_markets(raw.get("markets", []))
            rows.append(item)
        first = rows[0]
        if len({n for r in rows for n in (r["home"], r["away"])}) != len(rows) * 2:
            raise ValueError("Duplicate team in upcoming matchday")
        if any((r["season"], r["day"]) != (first["season"], first["day"]) for r in rows):
            raise ValueError("Mixed seasons in upcoming group")
        result.append({"season": first["season"], "day": first["day"], "start_time": first["start_time"],
                       "source_timer": raw_group[0].get("remaining_time"), "rows": rows})
    # The first group is the website's topmost upcoming matchday. Do not invent a
    # next season by incrementing an identifier, even when the timer expires.
    return result


def clean_markets(markets):
    cleaned = []
    if not isinstance(markets, list):
        raise ValueError("Markets must be a list")
    for market in markets:
        odds = []
        for odd in market.get("odds", []):
            value = str(odd.get("odd_value", ""))
            try:
                if float(value) <= 0:
                    continue
            except (ValueError, TypeError):
                continue
            # Only public display data is exposed. No executable selection payload.
            odds.append({"label": str(odd.get("display", odd.get("odd_key", ""))),
                         "value": value, "specifier": str(odd.get("special_bet_value", ""))})
        if odds:
            cleaned.append({"id": str(market.get("sub_type_id", "")), "name": str(market.get("name", "")), "odds": odds})
    return cleaned


class SourceError(Exception):
    pass


class PublicFeed:
    """GET-only, host-locked, globally rate-limited. Never bypass access controls.

    The one-request-per-second ceiling is shared by every worker, so on its own the archive lane
    (gap filling and the season-id scan) would only ever get the leftovers of live polling. The
    scheduler below reserves a share of that budget for it: requests declare a lane, and when a lane
    has taken more than its share of the recent slots its next request is pushed back so the other
    lane is served first. The total rate never changes — only who gets the next slot.
    """

    ARCHIVE_SHARE = 0.55          # of the recent request slots, while gap work is outstanding
    SLOT_MEMORY = 12              # slots the share is measured over

    def __init__(self, store):
        self.store = store
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(20), follow_redirects=False,
                                        headers={"User-Agent": "LeagueDNA/1.0 (read-only results archive)",
                                                 "Accept": "application/json"},
                                        limits=httpx.Limits(max_connections=3, max_keepalive_connections=3))
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.blocked_until = 0.0
        self.failures = 0
        self.lanes = deque(maxlen=self.SLOT_MEMORY)
        self.shapes = {"archive": self.ARCHIVE_SHARE, "live": 1.0 - self.ARCHIVE_SHARE}

    def set_lane_share(self, share):
        """How much of the request budget the archive lane may hold, 0..1."""
        share = min(max(float(share), 0.0), 1.0)
        self.shapes = {"archive": share, "live": 1.0 - share}

    def lane_share(self):
        return dict(self.shapes)

    def wait_estimate(self):
        """Seconds until the next request could leave, for the progress readout."""
        return round(max(0.0, self.next_request - time.monotonic(),
                         self.blocked_until - time.monotonic()), 2)

    async def get(self, route, params=None, kind="live", lane="live"):
        if route not in ALLOWED_ROUTES:
            raise ValueError("Route is not in the read-only allowlist")
        async with self.lock:
            lane = lane if lane in self.shapes else "live"
            # Reserved-slot fairness: if this lane has taken more than its share of the recent slots,
            # it waits behind the other lane instead of taking the next one too.
            taken = sum(1 for entry in self.lanes if entry == lane)
            share = self.shapes.get(lane, 1.0)
            penalty = 0.45 if (self.lanes and taken >= share * (self.lanes.maxlen - 1) + 0.5) else 0.0
            wait = max(0, self.next_request - time.monotonic() + penalty,
                       self.blocked_until - time.monotonic())
            if wait:
                await asyncio.sleep(wait)
            self.next_request = time.monotonic() + 1.0
            self.lanes.append(lane)
        url = BASE + route + (("?" + urlencode(params)) if params else "")
        began = time.perf_counter()
        try:
            response = await self.client.get(url)
            duration = round((time.perf_counter() - began) * 1000, 1)
            if response.status_code in (401, 403):
                self.blocked_until = time.monotonic() + 300
                raise SourceError(f"Access refused (HTTP {response.status_code}); waiting 5 minutes, without bypass")
            if response.status_code == 429:
                retry = response.headers.get("retry-after", "60")
                try:
                    retry = float(retry)
                except ValueError:
                    try:
                        retry = parsedate_to_datetime(retry).timestamp() - time.time()
                    except (ValueError, TypeError):
                        retry = 60
                self.blocked_until = time.monotonic() + max(30, min(retry, 3600))
                raise SourceError("Source rate limit; respecting Retry-After")
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise SourceError("Non-object response from source")
            stamp = time.time()
            raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            source_date = response.headers.get("date")
            try:
                source_at = parsedate_to_datetime(source_date).timestamp() if source_date else None
            except (ValueError, TypeError):
                source_at = None
            self.store.receipt(digest, url, kind, raw, stamp, source_date)
            self.store.endpoint(route, {"ok": True, "status": response.status_code, "latency_ms": duration, "at": stamp,
                                        "source_at": source_at, "url": url})
            self.failures = 0
            return payload, digest, stamp
        except (httpx.HTTPError, ValueError, SourceError) as e:
            self.failures += 1
            if self.failures > 2:
                self.blocked_until = max(self.blocked_until, time.monotonic() + min(300, 2 ** min(self.failures, 8)))
            self.store.endpoint(route, {"ok": False, "at": time.time(), "error": str(e)[:240], "url": url})
            raise SourceError(str(e)) from e

    async def close(self):
        await self.client.aclose()
