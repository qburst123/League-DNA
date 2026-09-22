"""Background builder, frozen ledger and cache for the FT score Prediction Playground payload.

Three jobs, deliberately separated by cost:

* **the ledger sync** (`sync`) — cheap (about a second). It locks a prediction sheet for every
  matchday the source has published but not played yet, and grades the sheets whose results have
  arrived. Sheets are locked the moment the matchday is announced, so the next round is already
  predicted while the current one is being played.
* **the composer** (`compose`) — cheaper still (tens of milliseconds). The board it serves is read
  out of the ledger, so a page refresh never waits for a model to run and never shows a different
  prediction for a matchday that has already been played.
* **the walk-forward report** (`build_report`, in a worker thread) — expensive (a minute of CPU).
  It is a *measurement* of the engines, not the board: it supplies the fitted mixture weights, the
  shrinkage and the sequence exponents that new sheets are locked with, and it feeds the engines
  workspace. It refreshes in the background on a slow cadence and never blocks a request.
"""
from __future__ import annotations
import gzip
import json
import os
import threading
import time
from pathlib import Path

from .store import dumps
from .ledger import PredictionLedger

from . import playground as pg

# The archive moves every few seconds while the collector is running, so the heavy report is
# throttled: it is a measurement, and a measurement a few minutes old is still a good measurement.
MIN_SECONDS_BETWEEN_BUILDS = 240

# When the matchday the source is publishing moves on, the engines are refitted sooner than for a
# plain archive refresh — but not on every matchday. A refit takes ~100 s of CPU on the two cores
# this app is built to run on, and the collector closes a matchday every couple of minutes while it
# fills the archive; refitting at every single move starves the web loop and the page stops opening
# quickly. The board itself is composed from the ledger on every request and the frozen sheet is
# written the moment a matchday is announced, so a measurement that is a few minutes behind changes
# nothing a visitor can see — the next sheet is locked from the last fitted report either way.
MIN_SECONDS_ON_TARGET_CHANGE = 240

# A servable payload on disk means nobody is waiting for the first build: let the app answer the
# opening page load without competing for the CPU, then refit.
FIRST_BUILD_DELAY_SECONDS = 25

# How often the ledger sync runs. The source announces the next matchday about one round ahead, and
# a round here is a few minutes, so a five-second tick locks every sheet with minutes to spare.
SYNC_SECONDS = 5


def key_parts(key):
    """(season, day) out of cache key "revision|season|day|window"; None when unparseable."""
    fields = str(key or "").split("|")
    if len(fields) < 4:
        return None, None
    return fields[-3] or None, fields[-2] or None


def _team_symbols(match):
    """{'team': 'h:a'} for both frames of a finished fixture, else {}."""
    if getattr(match, "ft_home", None) is None or getattr(match, "ft_away", None) is None:
        return {}
    return {match.home: f"{match.ft_home}:{match.ft_away}", match.away: f"{match.ft_away}:{match.ft_home}"}


def _ledger_pick(row):
    """One prediction row, compacted to what the ledger has to keep forever."""
    competition = row.get("hit_blend") or row.get("blend") or {}
    logloss = row.get("blend") or competition
    engines = {}
    for key, value in (row.get("models") or {}).items():
        engines[key] = {"score": value.get("score"), "probability": value.get("probability")}
    return {"team": row.get("team"), "opponent": row.get("opponent"), "venue": row.get("venue"),
            "event_id": row.get("event_id"), "pick": competition.get("score"),
            "probability": competition.get("probability"),
            "top3": [item.get("score") for item in (competition.get("top") or [])[:3]],
            "engines": engines,
            "pick_logloss": logloss.get("score"), "probability_logloss": logloss.get("probability")}


def _confidence(agrees, engines):
    if engines and agrees == engines and engines > 1:
        return "unanimous"
    if agrees >= max(2, engines - 1):
        return "strong"
    if agrees >= 2:
        return "mixed"
    return "thin"


class PlaygroundService:
    def __init__(self, store, data_dir: Path, window: int = 800, enabled: bool = True):
        self.store = store
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "playground.json"
        self.report_path = self.data_dir / "backtest.json"
        self.sheets_path = self.data_dir / "live-sheets.json"
        self.window = window
        self.enabled = enabled
        self.lock = threading.RLock()
        self.payload = None
        self.report = None
        self.live_rows = {}
        self.ledger = PredictionLedger(self.data_dir / "predictions.sqlite")
        self.state = {"status": "idle", "generated_at": None, "build_seconds": None, "error": None,
                      "built_revision": None, "built_target": None, "attempts": 0, "loaded_from": None}
        self.sync_state = {"at": None, "locked": [], "graded": 0, "backfilled": 0, "seconds": None,
                           "error": None, "checks": 0}
        self.started = time.time()
        self.last_build = 0.0
        self.thread = None
        self._encoded_key = None
        self._encoded = (None, None)
        self._load_from_disk()

    # ------------------------------------------------------------------ cache
    def board_snapshot(self) -> Path | None:
        """The standalone board ships its own snapshot, which is a valid first payload.

        A freshly extracted package has no `data/playground.json`, and the first walk-forward
        build costs about a minute of CPU. Reading the board's copy means a new installation
        answers with a complete board immediately and refits quietly in the background.
        """
        root = Path(__file__).resolve().parent.parent
        for candidate in (root.parent / "FT score Prediction Playground", root / "FT score Prediction Playground"):
            snapshot = candidate / "playground.json"
            if snapshot.exists() and snapshot.stat().st_size > 1024:
                return snapshot
        return None

    def _load_from_disk(self):
        try:
            source = self.path if self.path.exists() else self.board_snapshot()
            if source is not None:
                payload = json.loads(source.read_text())
                self.payload = payload
                self.state.update({"generated_at": payload.get("meta", {}).get("generated_at"),
                                   "build_seconds": payload.get("meta", {}).get("build_seconds"),
                                   "built_revision": payload.get("data", {}).get("revision"),
                                   "built_target": payload.get("target", {}).get("label"),
                                   "loaded_from": str(source),
                                   "status": "idle"})
        except Exception as error:            # a corrupt cache must never stop the app
            self.state["error"] = f"cache unreadable: {error}"[:200]
        try:
            if self.report_path.exists():
                self.report = json.loads(self.report_path.read_text())
        except Exception:
            self.report = None
        try:
            if self.sheets_path.exists():
                self.live_rows = json.loads(self.sheets_path.read_text())
        except Exception:
            self.live_rows = {}

    def _save_report(self):
        try:
            tmp = self.report_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.report))
            tmp.replace(self.report_path)
        except Exception as error:
            self.state["error"] = f"report not cached: {error}"[:200]

    def _save_live_rows(self):
        try:
            tmp = self.sheets_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self.live_rows))
            tmp.replace(self.sheets_path)
        except Exception:
            pass

    def encoded(self, payload):
        """JSON body (bytes) and its gzip twin, memoised on the payload's identity."""
        key = (payload.get("meta", {}).get("board_key"), payload.get("meta", {}).get("generated_at"),
               (payload.get("status") or {}).get("status"), payload.get("stale"),
               (payload.get("target") or {}).get("label"))
        with self.lock:
            if self._encoded_key != key:
                body = dumps(payload).encode("utf-8")
                self._encoded = (body, gzip.compress(body, compresslevel=4))
                self._encoded_key = key
            return self._encoded

    # ------------------------------------------------------------------ freshness
    def key(self):
        """What the heavy report depends on: the archive revision and the target matchday."""
        target = self.store.get_meta("upcoming", {}) or {}
        return f"{self.store.version}|{target.get('season')}|{target.get('day')}|w{self.window}"

    def board_key(self):
        """What the served board depends on: the ledger's contents and the published matchday."""
        target = self.store.get_meta("upcoming", {}) or {}
        return f"{self.ledger.version()}|{target.get('season')}|{target.get('day')}"

    def stale(self):
        """Why a report refresh is due — the reason decides how long the builder waits."""
        payload = self.payload
        if not self.report:
            return True, "no payload"
        built = (self.report.get("meta") or {}).get("key")
        current = self.key()
        if built != current:
            built_season, built_day = key_parts(built)
            current_season, current_day = key_parts(current)
            if built_season != current_season:
                return True, "new season"
            if built_day != current_day:
                return True, "target matchday moved"
            return True, "archive changed"
        graded = self.ledger.stats().get("last_graded_day")
        if graded and (self.report.get("meta") or {}).get("graded_through") not in (None, graded):
            return True, "new results"
        return False, "current"

    # ------------------------------------------------------------------ the cheap loop: ledger
    def sync(self, force=False):
        """Lock a sheet for every published unplayed matchday; grade what has been recorded.

        This is the heartbeat of the workspace. It costs about a second, so it runs every few
        seconds: the moment the source publishes the next matchday its prediction sheet is written
        and frozen — minutes before the first kick-off, and before the current round even ends.
        """
        started = time.time()
        summary = {"at": started, "locked": [], "graded": 0, "backfilled": 0, "error": None, "seconds": None}
        try:
            history = pg.load_history(self.store)
            targets = pg.current_targets(self.store, history)
            season = targets[0].season if targets else (self.store.get_meta("upcoming", {}) or {}).get("season")
            grouped = {}
            for fixture in targets:
                grouped.setdefault((fixture.season, fixture.day), []).append(fixture)

            for (target_season, day), fixtures in sorted(grouped.items()):
                if self.ledger.sheet(target_season, day) is not None:
                    continue
                rows, _bundle = self.predict(fixtures, history)
                kickoff = min((fixture.kickoff for fixture in fixtures if fixture.kickoff), default=None)
                wrote = self.ledger.lock_sheet(
                    target_season, day, [_ledger_pick(row) for row in rows], source="live",
                    kickoff=kickoff, revision=self.store.version,
                    weights=(self.report or {}).get("weights"),
                    hit_weights=(self.report or {}).get("hit_weights"),
                    detail={"weight_source": (self.report or {}).get("meta", {}).get("key"),
                            "engines": [card["key"] for card in pg.MODEL_CARDS]})
                if wrote:
                    self.live_rows[f"{target_season}/{day}"] = rows
                    lead = None if not kickoff else round(kickoff - started, 1)
                    summary["locked"].append({"season": target_season, "day": day, "fixtures": len(rows),
                                              "kickoff": kickoff, "lead_seconds": lead})
                    self._save_live_rows()

            # grade every sheet whose results have arrived, then drop the full rows it no longer needs.
            # This is driven by the ledger, not by the published matchday: a matchday in progress is
            # graded fixture by fixture, and a season that has ended still takes the results it is owed.
            recorded = {}
            for match in history:
                symbols = _team_symbols(match)
                if symbols:
                    recorded.setdefault((match.season, match.day), {}).update(symbols)
            for sheet_season, day in self.ledger.ungraded_sheets():
                results = recorded.get((sheet_season, day)) or {}
                if results:
                    summary["graded"] += self.ledger.grade(sheet_season, day, results)
                if f"{sheet_season}/{day}" in self.live_rows and self._sheet_is_graded(sheet_season, day):
                    self.live_rows.pop(f"{sheet_season}/{day}", None)
                    self._save_live_rows()
            if season is not None:
                summary["backfilled"] = self._backfill(season, history, grouped)
            self.ledger.checkpoint()
            summary["seconds"] = round(time.time() - started, 2)
        except Exception as error:
            summary["error"] = f"{type(error).__name__}: {error}"[:200]
            try:
                self.store.log("playground", f"ledger sync failed: {error}"[:200], "warning")
            except Exception:
                pass
        with self.lock:
            summary["checks"] = (self.sync_state.get("checks") or 0) + 1
            self.sync_state = summary
        return summary

    def _sheet_is_graded(self, season, day):
        with self.ledger.lock:
            row = self.ledger.db.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN actual IS NOT NULL THEN 1 ELSE 0 END) AS graded"
                " FROM picks WHERE season=? AND day=?", (season, day)).fetchone()
        return bool(row and row["total"]) and row["graded"] == row["total"]

    def predict(self, fixtures, history):
        """One prediction pass with the freshest fitted parameters the service holds."""
        report = self.report or {}
        return pg.predict_matchday(self.store, history, fixtures,
                                   weights=report.get("weights") or {},
                                   hit_weights=report.get("hit_weights") or {},
                                   sequence_gammas=report.get("sequence_gammas"),
                                   shrinkage=report.get("shrinkage"))

    def _backfill(self, season, history, grouped):
        """Freeze the current season's already-played matchdays from the measurement, exactly once.

        Before this workspace had a ledger, the board was rebuilt from the walk-forward run every
        time the archive grew, which is why a played matchday's prediction used to move. Those days
        are copied in once — labelled `backfill`, so nobody mistakes them for a live lock — and from
        then on they are as frozen as any sheet locked before kick-off.
        """
        board = (self.report or {}).get("live_board") or {}
        blends = board.get("blends") or {}
        if board.get("season") != season or not blends:
            return 0
        played = {match.day for match in history
                  if match.season == season and match.ft_home is not None and match.ft_away is not None}
        covered = self.ledger.covered_days(season)
        missing = sorted(day for day in played if day not in covered and (season, day) not in grouped)
        written = 0
        for day in missing:
            picks = {}
            for scope, pick_field, probability_field in (("hit_blend", "pick", "probability"),
                                                         ("blend", "pick_logloss", "probability_logloss")):
                for team, payload in ((blends.get(scope) or {}).get("teams") or {}).items():
                    row = next((item for item in (payload.get("rows") or []) if item.get("day") == day), None)
                    if not row:
                        continue
                    entry = picks.setdefault(team, {"team": team, "engines": {}})
                    entry[pick_field] = row.get("pick")
                    entry[probability_field] = row.get("probability")
                    if scope == "hit_blend":
                        entry.update({"top3": row.get("top") or [], "agrees": row.get("agrees"),
                                      "engines_n": row.get("engines"), "actual": row.get("actual")})
            if not picks:
                continue
            kickoff = min((match.kickoff for match in history
                           if match.season == season and match.day == day and match.kickoff), default=None)
            if self.ledger.lock_sheet(season, day, list(picks.values()), source="backfill", kickoff=kickoff,
                                      revision=self.store.version, detail={"measurement": "walk-forward"}):
                written += 1
                results = {}
                for match in history:
                    if match.season == season and match.day == day:
                        results.update(_team_symbols(match))
                self.ledger.grade(season, day, results)
        return written

    # ------------------------------------------------------------------ the cheap loop: payload
    def compose(self):
        """Build the served payload from the ledger — no model runs, no waiting."""
        report = self.report or {}
        history = pg.load_history(self.store)
        targets = pg.current_targets(self.store, history)
        season = self.current_season(targets)
        board = self.ledger_board(season, history)
        target = self.target_view(season, targets)

        engines = []
        for card in pg.MODEL_CARDS:
            metrics = (report.get("models") or {}).get(card["key"]) or {}
            engines.append(dict(card, coverage=metrics.get("coverage"), raw=metrics.get("raw"),
                                calibrated=metrics.get("calibrated"), shrinkage=metrics.get("shrinkage"),
                                gamma=metrics.get("gamma"),
                                weight=(report.get("hit_weights") or {}).get(card["key"])))
        for key, name, weights_key, blurb in (
                ("hit_blend", "Competition blend", "hit_weights",
                 "The mixture tuned on exact hits rather than on log loss — the pick this board plays."),
                ("blend", "Log-loss blend", "weights",
                 "The same engines mixed to minimise log loss — the shape used for probabilities.")):
            metrics = (report.get("models") or {}).get(key) or {}
            engines.append({"key": key, "name": name, "family": "Ensemble", "blurb": blurb,
                            "assumptions": "A mixture of the engines above, refitted as the archive grows.",
                            "strengths": "Reads every engine at once, so a single engine's blind spot is diluted.",
                            "limits": "Weights are fitted on recorded matchdays only, so they are as good as the archive.",
                            "coverage": metrics.get("coverage"), "raw": metrics.get("raw"),
                            "calibrated": metrics.get("calibrated"), "shrinkage": 0.0, "weight": None,
                            "weights": report.get(weights_key) or {}})

        return {
            "meta": {"version": "2.0.0", "generated_at": time.time(), "key": self.key(),
                     "board_key": self.board_key(), "window": self.window,
                     "protocol": report.get("protocol"),
                     "build_seconds": (self.report or {}).get("meta", {}).get("build_seconds"),
                     "source": "local archive + captured public market catalogues",
                     "ledger": "a sheet is locked when its matchday is announced and never rewritten afterwards"},
            "data": {"revision": self.store.version, "matches": len(history),
                     "matchdays": len(pg.timeline(history)),
                     "seasons": len({match.season for match in history}),
                     "target_matchday": target.get("label")},
            "engines": engines,
            "backtest": report,
            "live_board": board,
            "target": target,
            "ledger": self.ledger_summary(season),
        }

    def current_season(self, targets):
        """Which season the board shows: the one being played, else the last one the ledger knows.

        A season can end, and the collector can be paused; neither may blank the board. Falling back
        to the ledger keeps the last season on screen until the source publishes the next one.
        """
        if targets:
            return targets[0].season
        announced = (self.store.get_meta("upcoming", {}) or {}).get("season")
        if announced:
            return announced
        seasons = self.ledger.seasons()
        return seasons[0]["season"] if seasons else None

    def ledger_board(self, season, history):
        """The frozen board: every locked pick plus the recorded results, in the legacy shape."""
        empty = {"season": None, "blends": {}, "scores": {}, "teams": {}, "summary": {}, "days": []}
        if season is None:
            return empty
        scores = {}
        for match in history:
            if match.season != season or match.ft_home is None or match.ft_away is None:
                continue
            scores.setdefault(match.home, {})[str(match.day)] = f"{match.ft_home}:{match.ft_away}"
            scores.setdefault(match.away, {})[str(match.day)] = f"{match.ft_away}:{match.ft_home}"

        rows = self.ledger.season_rows(season)
        blends = {}
        for scope, pick_field, probability_field in (("hit_blend", "pick", "probability"),
                                                     ("blend", "pick_logloss", "probability_logloss")):
            holders, days = {}, set()
            for row in rows:
                pick = row.get(pick_field)
                actual = row.get("actual")
                if not pick or not actual:
                    continue
                agrees = row.get("agrees")
                if agrees is None:
                    engines = row.get("engines") or {}
                    agrees = sum(1 for value in engines.values() if value.get("score") == pick)
                engine_count = row.get("engines_n") or len(row.get("engines") or {}) or None
                verdict = row.get("verdict") or ""
                holder = holders.setdefault(row["team"], {"team": row["team"], "rows": []})
                holder["rows"].append({
                    "season": season, "day": row["day"], "team": row["team"], "pick": pick,
                    "probability": row.get(probability_field), "agrees": agrees, "engines": engine_count,
                    "top": row.get("top3") or [], "actual": actual,
                    "verdict": "hit" if verdict == "hit" else "miss",
                    "direction": "hit" if verdict in ("hit", "direction") else "miss",
                    "confidence": _confidence(agrees or 0, engine_count or 1),
                    "source": row.get("source")})
                days.add(row["day"])
            summary = {"season": season, "teams": len(holders), "picks": 0, "hits": 0, "misses": 0,
                       "direction_hits": 0, "no_pick": 0, "matchdays": len(days)}
            for holder in holders.values():
                kept = holder["rows"]
                hits = sum(1 for row in kept if row["verdict"] == "hit")
                direction_hits = sum(1 for row in kept if row["direction"] == "hit")
                holder["summary"] = {"picks": len(kept), "hits": hits, "misses": len(kept) - hits,
                                     "direction_hits": direction_hits,
                                     "hit_rate": round(100.0 * hits / len(kept), 1) if kept else None,
                                     "direction_rate": round(100.0 * direction_hits / len(kept), 1) if kept else None,
                                     "no_pick": 0, "first_day": kept[0]["day"] if kept else None,
                                     "last_day": kept[-1]["day"] if kept else None}
                summary["picks"] += len(kept)
                summary["hits"] += hits
                summary["misses"] += len(kept) - hits
                summary["direction_hits"] += direction_hits
            summary["hit_rate"] = round(100.0 * summary["hits"] / max(1, summary["picks"]), 1)
            summary["direction_rate"] = round(100.0 * summary["direction_hits"] / max(1, summary["picks"]), 1)
            blends[scope] = {"teams": holders, "summary": summary}
        primary = blends.get("hit_blend") or {"teams": {}, "summary": {}}
        return {"season": season, "blends": blends, "scores": scores, "teams": primary["teams"],
                "summary": primary["summary"], "days": self.ledger.days(season),
                "note": ("Frozen ledger: the pick shown for a matchday is the one locked when the matchday was "
                         "announced, and it is never rewritten after the result arrives. The upper row is the "
                         "recorded full-time score, the row beneath it the locked prediction graded against it.")}

    def target_view(self, season, targets):
        """The locked sheets for the matchdays that are still to play."""
        days = sorted({fixture.day for fixture in targets})
        rows = []
        for day in days:
            stored = self.live_rows.get(f"{season}/{day}")
            if stored:
                rows.extend(dict(row, frozen=True, locked=True) for row in stored)
                continue
            # the full grid evidence is gone (restart, or the sheet predates this run): serve the
            # frozen record the ledger kept instead of inventing a fresh prediction
            for record in self.ledger.season_rows(season):
                if record["day"] != day or record.get("actual") is not None:
                    continue
                engines = record.get("engines") or {}
                pick = record.get("pick")
                agrees = record.get("agrees")
                if agrees is None:
                    agrees = sum(1 for value in engines.values() if value.get("score") == pick)
                rows.append({
                    "team": record["team"], "opponent": record.get("opponent"), "venue": record.get("venue"),
                    "event_id": record.get("event_id"), "season": season, "day": day, "kickoff": None,
                    "models": {}, "market_available": False,
                    "unavailable": {"market": {"reason": "the frozen record keeps the pick and the engine votes, "
                                                         "not the full grid of every engine"}},
                    "hit_blend": {"score": pick, "probability": record.get("probability"),
                                  "top": [{"score": score} for score in (record.get("top3") or [])], "weights": {}},
                    "blend": {"score": record.get("pick_logloss"),
                              "probability": record.get("probability_logloss"), "top": [], "weights": {}},
                    "frozen": True, "locked": True,
                    "competition": {"pick": pick, "probability": record.get("probability"),
                                    "runner_up": None, "third": None, "agreement": agrees,
                                    "engines": record.get("engines_n") or len(engines) or None,
                                    "agreeing": [key for key, value in engines.items()
                                                 if value.get("score") == pick],
                                    "second_opinion": None, "second_opinion_score": None,
                                    "confidence": _confidence(agrees or 0, (record.get("engines_n") or len(engines)) or 1),
                                    "weights": {}, "frozen": True}})
        for row in rows:
            if "competition" not in row:
                row["competition"] = _competition(row)
        kickoff = min((fixture.kickoff for fixture in targets if fixture.kickoff), default=None)
        sheet = self.ledger.sheet(season, days[0]) if days else None
        return {"season": season, "day": days[0] if days else None, "days": days,
                "label": f"{season}/{days[0]}" if days else None, "kickoff": kickoff,
                "fixtures": len(targets), "rows": rows,
                "markets": sum(1 for row in rows if row.get("market_available")),
                "locked_at": (sheet or {}).get("locked_at"), "lead_seconds": (sheet or {}).get("lead_seconds"),
                "late": bool((sheet or {}).get("late")), "source": (sheet or {}).get("source"),
                "competition_weights": (self.report or {}).get("hit_weights") or {},
                "logloss_weights": (self.report or {}).get("weights") or {},
                "blends": {"competition": "hit-tuned mixture, the pick on this board",
                           "logloss": "probability-tuned mixture, the calibrated cross-check"}}

    def ledger_summary(self, season):
        stats = self.ledger.stats()
        season_stats = self.ledger.stats(season) if season else {}
        return {"season": season, "stats": stats, "season_stats": season_stats,
                "integrity": self.ledger.integrity(), "season_integrity": self.ledger.integrity(season),
                "seasons": self.ledger.seasons(), "version": self.ledger.version(),
                "sync": {key: value for key, value in self.sync_state.items() if key != "error"},
                "sync_error": self.sync_state.get("error"),
                "note": "A sheet is locked when its matchday is announced and graded when the result arrives; "
                        "the prediction columns are never rewritten afterwards."}

    def tick(self):
        """A tiny status body for the browser's fast poll: it changes the instant the board should.

        It reads the *ledger and the store*, not the cached payload, so the answer is current even
        when nothing has been recomposed yet — that is what makes the board follow the source within
        a few seconds without a model ever running on the request path.
        """
        payload = self.payload or {}
        announced = self.store.get_meta("upcoming", {}) or {}
        pending = self.ledger.pending_sheets(limit=1)
        target = {"label": None, "days": None, "kickoff": announced.get("start_time"),
                  "locked_at": None, "lead_seconds": None, "source": None, "late": False}
        if pending:
            season, day = pending[0]
            sheet = self.ledger.sheet(season, day) or {}
            days = sorted(day for (sheet_season, day) in self.ledger.pending_sheets(limit=12)
                          if sheet_season == season)
            target.update({"label": f"{season}/{day}", "days": days, "locked_at": sheet.get("locked_at"),
                           "lead_seconds": sheet.get("lead_seconds"), "source": sheet.get("source"),
                           "late": bool(sheet.get("late"))})
            if sheet.get("kickoff"):
                target["kickoff"] = sheet["kickoff"]
        elif announced.get("season"):
            target["label"] = f"{announced.get('season')}/{announced.get('day')}"
        status = self.status()
        stats = self.ledger.stats()
        kickoff = target.get("kickoff")
        return {"board_key": self.board_key(), "ledger": self.ledger.version(),
                "target": {"label": target.get("label"), "days": target.get("days"), "kickoff": kickoff,
                           "starts_in": round(kickoff - time.time(), 1) if kickoff else None,
                           "locked_at": target.get("locked_at"), "lead_seconds": target.get("lead_seconds"),
                           "source": target.get("source"), "late": bool(target.get("late"))},
                "graded": {"picks": stats.get("graded"), "hits": stats.get("hits"),
                           "last_day": stats.get("last_graded_day"),
                           "season": (self.ledger.seasons() or [{}])[0].get("season")
                                     or (payload.get("live_board") or {}).get("season")},
                "sync": {"at": self.sync_state.get("at"), "seconds": self.sync_state.get("seconds"),
                         "locked": len(self.sync_state.get("locked") or []),
                         "graded_now": self.sync_state.get("graded"), "checks": self.sync_state.get("checks"),
                         "error": self.sync_state.get("error")},
                "build": {"status": status.get("status"), "attempts": status.get("attempts"),
                          "report_key": (self.report or {}).get("meta", {}).get("key"),
                          "error": status.get("error")}}

    # ------------------------------------------------------------------ the heavy loop: report
    def ensure(self, force=False):
        """Start a background walk-forward refit when the measurement is missing or out of date."""
        with self.lock:
            if not self.enabled:
                return self.status()
            if self.thread and self.thread.is_alive():
                return self.status()
            is_stale, reason = self.stale()
            if not force:
                if not is_stale:
                    return self.status()
                settling = self.payload is not None and self.state["attempts"] == 0 and \
                    time.time() - self.started < FIRST_BUILD_DELAY_SECONDS
                moved = reason in ("target matchday moved", "new season")
                floor = MIN_SECONDS_ON_TARGET_CHANGE if moved else MIN_SECONDS_BETWEEN_BUILDS
                if settling or time.time() - self.last_build < floor:
                    self.state["status"] = "waiting"
                    self.state["error"] = None
                    return self.status()
            self.state.update({"status": "building", "reason": "manual" if force else reason,
                               "started_at": time.time(), "error": None})
            self.thread = threading.Thread(target=self._build, name="playground-build", daemon=True)
            self.thread.start()
            return self.status()

    def _build(self):
        started = time.time()
        with self.lock:
            self.state["attempts"] += 1
        try:
            try:
                os.nice(10)                 # the walk-forward run must never starve the web loop
            except (AttributeError, OSError):
                pass                        # not a POSIX host, or not permitted: cosmetic only
            report = self.build_report()
            report.setdefault("meta", {}).update(
                {"key": self.key(), "graded_through": self.ledger.stats().get("last_graded_day"),
                 "build_seconds": round(time.time() - started, 1)})
            with self.lock:
                self.report = report
            self._save_report()
            self.refresh_payload()
            with self.lock:
                self.state.update({"status": "idle", "generated_at": time.time(),
                                   "build_seconds": report["meta"]["build_seconds"], "error": None,
                                   "built_revision": self.store.version,
                                   "built_target": (self.payload or {}).get("target", {}).get("label")})
        except Exception as error:
            with self.lock:
                self.state.update({"status": "error", "error": f"{type(error).__name__}: {error}"[:300]})
            try:
                self.store.log("playground", f"build failed: {error}"[:200], "warning")
            except Exception:
                pass
        finally:
            self.last_build = time.time()

    def build_report(self):
        """The heavy walk-forward measurement. Only ever called from the worker thread."""
        history = pg.load_history(self.store)
        targets = pg.current_targets(self.store, history)
        return pg.backtest(self.store, history, window=self.window,
                           season=(targets[0].season if targets else None))

    def build_payload(self):
        """The heavy path: refit the report, then compose. Used by manual rebuild and the tests."""
        report = self.build_report()
        report.setdefault("meta", {}).update(
            {"key": self.key(), "graded_through": self.ledger.stats().get("last_graded_day")})
        with self.lock:
            self.report = report
        return self.compose()

    def refresh_payload(self):
        """Recompose and cache the payload. Cheap: no model runs."""
        payload = self.compose()
        payload["status"] = self.status()
        try:
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload))
            tmp.replace(self.path)
        except Exception:
            pass
        with self.lock:
            self.payload = payload
        return payload

    # ------------------------------------------------------------------ serving
    def status(self):
        with self.lock:
            state = dict(self.state)
        state["has_payload"] = self.payload is not None
        state["payload_key"] = (self.payload or {}).get("meta", {}).get("key")
        state["current_key"] = self.key()
        state["board_key"] = self.board_key()
        state["sync"] = {key: value for key, value in self.sync_state.items() if key != "error"}
        state["ledger"] = self.ledger.version()
        return state

    def serve(self, force=False):
        """Payload plus freshness information. Composed from the ledger, so it is always instant."""
        self.ensure(force=force)
        payload = self.compose()
        payload["status"] = self.status()
        payload["stale"], payload["stale_reason"] = self.stale()
        with self.lock:
            self.payload = payload
        return payload

    def summary(self):
        """Cheap health row for /api/workspace and /api/health."""
        with self.lock:
            payload = self.payload
            state = dict(self.state)
        stats = self.ledger.stats()
        season = (payload or {}).get("live_board", {}).get("season")
        if not payload:
            return {"ready": False, "status": state.get("status"), "error": state.get("error"),
                    "ledger": self.ledger.version(), "ledger_picks": stats.get("picks")}
        target = payload.get("target") or {}
        champion = ((self.report or {}).get("models") or {}).get("hit_blend") or {}
        return {"ready": True, "status": state.get("status"), "error": state.get("error"),
                "generated_at": payload.get("meta", {}).get("generated_at"),
                "build_seconds": (self.report or {}).get("meta", {}).get("build_seconds"),
                "target": target.get("label"), "fixtures": target.get("fixtures"),
                "matches": payload.get("data", {}).get("matches"),
                "competition_hit1": (champion.get("calibrated") or {}).get("hit1"),
                "ledger": self.ledger.version(), "ledger_picks": stats.get("picks"),
                "ledger_graded": stats.get("graded"), "ledger_hit_rate": stats.get("hit_rate"),
                "season": season, "board_key": self.board_key(), "stale": self.stale()[0]}


def _competition(row):
    """The competition view the target rows carry, rebuilt from a frozen record."""
    blend = row.get("hit_blend") or {}
    models = row.get("models") or {}
    top = {"score": blend.get("score"), "probability": blend.get("probability")}
    agreeing = [key for key, value in models.items() if value.get("score") == top["score"]]
    return {"pick": top["score"], "probability": top["probability"], "runner_up": None, "third": None,
            "agreement": len(agreeing), "engines": len(models), "agreeing": agreeing,
            "second_opinion": None, "second_opinion_score": None,
            "confidence": _confidence(len(agreeing), len(models) or 1),
            "weights": (blend.get("weights") or {}), "frozen": True}
