from __future__ import annotations
import asyncio
import gzip
import threading
import json
import os
import re
import tempfile
import time
import zlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from .collector import Collector
from .recover import Recoverer
from .source import START_SEASON
from .store import Store, dumps
from .patterns import KINDS
from .gold import compare_correct_scores
from .workspace import build_workspace
from .trail import observe_single_hits, trail_rows
from .sequences import SequenceIndex
from .replay import replay_report, replay_target_state
from .forecast import cached_season_ledger
from .playground_service import PlaygroundService
from .matchup import MatchupService
from .betika_edge import edge as betika_edge

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("LEAGUE_DATA_DIR", str(ROOT / "data")))
store = Store(DATA / "league.sqlite")
collector = Collector(store)
recoverer = Recoverer(store, collector)
sequence_index = SequenceIndex(DATA / 'ft-sequences.sqlite')
# Heavy multi-model work; never rebuilt inside a request (see backend/playground_service.py).
playground_service = PlaygroundService(store, DATA)
# Head-to-head matchup DNA: the pair index, the frozen half-time ledger and the workspace views.
matchup_service = MatchupService(store, DATA)
def _playground_board():
    """The board lives in its own workspace next to the project, or inside it when packaged."""
    for candidate in (ROOT.parent / "FT score Prediction Playground", ROOT / "FT score Prediction Playground"):
        page = candidate / "index.html"
        if page.exists():
            return page
    return ROOT.parent / "FT score Prediction Playground" / "index.html"


PLAYGROUND_HTML = _playground_board()
PLAYGROUND_OFFLINE = os.environ.get("LEAGUE_PLAYGROUND") == "0"


CHECKPOINT_EVERY = 2          # background ticks between WAL checkpoints (2 x 45 s = 90 seconds)


def checkpoint_databases():
    """Truncate the write-ahead logs of both databases.

    The collector writes to `league.sqlite` continuously and the sequence index rewrites whole
    partitions of `ft-sequences.sqlite`, so both carry `-wal` side files that grow until something
    checkpoints them. Those files are part of every backup, zip and workspace copy, so they are
    kept small on a timer rather than only at shutdown.
    """
    store.checkpoint()
    sequence_index.checkpoint()


async def refresh_playground():
    """Refresh the playground payload, and keep the write-ahead log from growing without bound.

    The collector writes continuously, so `league.sqlite-wal` grows until it is checkpointed.
    Truncating it every few minutes keeps the database directory a predictable size — the WAL
    side file is part of what any backup or workspace copy has to carry.
    """
    tick = 0
    while True:
        try:
            # every few seconds: lock the sheets for the matchdays the source has just published and
            # grade the ones whose results have arrived. This is what keeps the board in step with the
            # source without ever making a reader wait for a model.
            await asyncio.to_thread(playground_service.sync)
            # the half-time ledger syncs on the same beat: lock the announced matchday, grade the
            # played ones, keep the matchup board in step
            await asyncio.to_thread(matchup_service.sync)
            # every few cycles: refit the engines in the background (a measurement, never a gate)
            if tick % REPORT_EVERY == 0:
                await asyncio.to_thread(playground_service.ensure)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            store.log("playground", f"refresh failed: {error}"[:200], "warning")
        tick += 1
        if tick % CHECKPOINT_EVERY == 0:
            try:
                await asyncio.to_thread(checkpoint_databases)
            except Exception as error:
                store.log("storage", f"checkpoint failed: {error}"[:200], "warning")
        await asyncio.sleep(SYNC_SECONDS)


async def index_sequences():
    while True:
        try:
            sync_task = asyncio.create_task(asyncio.to_thread(sequence_index.sync, store))
            try:
                await asyncio.shield(sync_task)
            except asyncio.CancelledError:
                await sync_task
                raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            store.log('sequence-index', str(error)[:200], 'warning')
        await asyncio.sleep(15)


# Windows (and any host where a browser drops a connection mid-download) floods the terminal with
# `Exception in callback _ProactorBasePipeTransport._call_connection_lost ... ConnectionResetError`.
# Those resets are the client closing the socket — a reload or a cancelled preload — not a fault in
# this app, so they are swallowed here instead of being printed dozens of times.
BENIGN_RESETS = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)


# The ledger sync runs every few seconds; the checkpoint and the heavy engine refit are far apart
# from it on purpose (see backend/playground_service.py).
SYNC_SECONDS = 5
REPORT_EVERY = 9            # refit the engines roughly every 45 s of idle time
CHECKPOINT_EVERY = 24       # checkpoint both databases roughly every two minutes


def quiet_connection_resets(loop):
    previous = loop.get_exception_handler()

    def handler(loop, context):
        error = context.get("exception")
        if isinstance(error, BENIGN_RESETS):
            return
        message = str(context.get("message", ""))
        if "connection lost" in message.lower() and error is None:
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


@asynccontextmanager
async def lifespan(app):
    quiet_connection_resets(asyncio.get_running_loop())
    if os.environ.get("LEAGUE_OFFLINE") != "1":
        await collector.start()
        recoverer.start()
    indexing_task = asyncio.create_task(index_sequences())
    playground_task = None if PLAYGROUND_OFFLINE else asyncio.create_task(refresh_playground())
    yield
    indexing_task.cancel()
    if playground_task:
        playground_task.cancel()
    await asyncio.gather(indexing_task, *( [playground_task] if playground_task else []), return_exceptions=True)
    await recoverer.stop()
    await collector.stop()
    sequence_index.close()
    store.close()


app = FastAPI(title="League DNA · Read-only public league archive", version="2.5.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")


@app.middleware("http")
async def headers(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # Mutations only manage THIS app's collector/bookmarks, never the source.
        if request.headers.get("x-requested-with") != "LeagueDNA":
            return JSONResponse({"detail": "Expected same-origin application request"}, status_code=403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "Cross-origin modifications are not allowed"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/api/") or request.url.path in ("/", "/index.html"):
        response.headers["Cache-Control"] = "no-store"
    return response


Kind = Literal["parity", "btts", "dnb", "total"]


workspace_cache = {"key": None, "body": None, "gzip": None}
workspace_lock = threading.RLock()


@app.get("/api/workspace")
def workspace(request: Request):
    key = str(store.version)          # a new key only when the archive changes, so polls get 304s
    etag = '"' + key + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    with workspace_lock:
        if workspace_cache["key"] != key:
            payload = build_workspace(store, record_trails=os.environ.get("LEAGUE_OFFLINE") != "1")
            payload['sequence_index'] = sequence_index.public_state
            body = dumps(payload).encode("utf-8")
            workspace_cache.update(key=key, body=body, gzip=gzip.compress(body, compresslevel=4))
        encoded = "gzip" in request.headers.get("accept-encoding", "")
        return Response(workspace_cache["gzip"] if encoded else workspace_cache["body"], media_type="application/json",
                        headers={"ETag": etag, "Vary": "Accept-Encoding", **({"Content-Encoding": "gzip"} if encoded else {})})


@app.get("/api/forecast")
def forecast_ledger(season: int | None = Query(None, ge=3134345), scope: Literal["all", "same"] | None = None):
    """Earlier-season FT continuation picks plus the graded season ledger."""
    payload = cached_season_ledger(store, season)
    if scope:
        return {**payload, "scopes": {scope: payload["scopes"][scope]}}
    return payload


@app.get("/api/replay")
def historical_replay(season: int = Query(ge=3134345), team: str = Query(min_length=1, max_length=100),
                      cutoff: int = Query(4, ge=1, le=29), length: int | None = Query(None, ge=1, le=29), reveal: bool = False):
    try:
        return JSONResponse(content=replay_report(store, season, team, cutoff, length, reveal))
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/sequences/status")
def sequence_status():
    return {**sequence_index.public_state, "scores": sequence_index.vocabulary()}


@app.get("/api/sequences/query")
def sequence_query(pattern: str = Query(min_length=3, max_length=200)):
    try:
        return sequence_index.query([value.strip() for value in pattern.split(',')])
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@app.get("/api/export/sequence-index")
def export_sequence_index():
    fd, path = tempfile.mkstemp(prefix='sequence-index-', suffix='.sqlite', dir=DATA)
    os.close(fd)
    try:
        sequence_index.backup(path)
    except Exception:
        os.unlink(path)
        raise
    return FileResponse(path, filename='ft-sequences.sqlite', media_type='application/vnd.sqlite3',
                        background=BackgroundTask(os.unlink, path))


@app.get("/api/sequences/catalogue")
def sequence_catalogue(length: int = Query(2, ge=1, le=30), offset: int = Query(0, ge=0), limit: int = Query(25, ge=1, le=100)):
    return sequence_index.catalogue(length, offset, limit)


@app.get("/api/trails")
def trails(season: int | None = Query(None, ge=3134345), team: str | None = Query(None, max_length=100),
           scope: Literal["all", "same"] | None = None):
    return {"rows": trail_rows(store, season, team, scope), "tracker": store.get_meta("single_hit_tracker", {})}


@app.post("/api/trails/sync")
def sync_trails():
    if os.environ.get("LEAGUE_OFFLINE") == "1":
        raise HTTPException(409, "Saved-database mode does not record new live single-hit events.")
    return observe_single_hits(store)


@app.get("/api/overview")
def overview():
    return store.overview()


@app.get("/api/blueprints")
def blueprints(season: int = Query(ge=3134345), kind: Kind = "parity"):
    return store.fingerprints(season, kind)


@app.get("/api/compare")
def compare(kind: Kind = "parity", minimum: float = Query(100, ge=50, le=100), scope: Literal["all", "same"] = "all"):
    return store.compare(kind, minimum, scope)


@app.get("/api/gold")
def gold(request: Request, scope: Literal["all", "same"] = "all"):
    if any(key in request.query_params for key in ("mode", "minimum")):
        raise HTTPException(409, "The Gold engine was rebuilt. Refresh the browser to load the v1.2 interface; the old score-mode and seed-length controls are no longer used.")
    return JSONResponse(content=compare_correct_scores(store, scope))


@app.get("/api/results")
def results(season: int | None = Query(None, ge=3134345), day: int | None = Query(None, ge=1, le=30), team: str | None = Query(None, max_length=100)):
    return {"matches": store.results(season, day, team), "server_time": time.time()}


@app.get("/api/matches/{ident}")
def match_record(ident: int):
    row = store.one("SELECT * FROM matches WHERE id=?", (ident,))
    if not row:
        raise HTTPException(404, "Result not found in the local archive")
    return {"match": row}


@app.get("/api/markets/{event_id}")
def markets(event_id: str):
    if not re.fullmatch(r"\d{1,20}", event_id):
        raise HTTPException(400, "Invalid event ID")
    data = store.market(event_id)
    if not data:
        raise HTTPException(404, "Markets have not yet been collected for this fixture")
    data["server_time"] = time.time()
    return data


@app.get("/api/health")
def health():
    began = time.perf_counter()
    with store.lock:
        check = store.db.execute("PRAGMA quick_check").fetchone()[0]
        pages = store.db.execute("PRAGMA page_count").fetchone()[0]
        page_size = store.db.execute("PRAGMA page_size").fetchone()[0]
    return {"database": {"engine": "SQLite", "journal": "WAL", "integrity": check, "size_bytes": pages * page_size,
                         "file": "data/league.sqlite", "read_ms": round((time.perf_counter() - began) * 1000, 2)},
            "playground": playground_service.summary(),
            "matchups": matchup_service.summary(),
            "collector": {"source": "Betika public website feeds", "upcoming_interval": 10, "live_interval": 5,
                          "results_interval": 18, "market_interval": 60, "max_requests_per_second": 1,
                          "paused": store.get_meta("paused", False), "read_only": True},
            "discovery": store.get_meta("published_seasons", {}), "backfill": store.get_meta("backfill", {}),
            "gap_fill": {**(store.get_meta("backfill", {}) or {}),
                         "scan": collector.scanner.summary() if collector else None,
                         "coverage": store.published_coverage()[:12]},
            "endpoints": {r["route"]: json.loads(r["data"]) for r in store.all("SELECT * FROM endpoints")},
            "seasons": store.season_summary(), "activity": store.all("SELECT * FROM activity ORDER BY id DESC LIMIT 80"),
            "limitations": ["Public history discovery is limited to the source's rolling ten-season list. Previously discovered IDs remain archived.",
                            "Completed HT and FT must both be present; live values never become settled fingerprints by assumption.",
                            "Source polling is not a guaranteed push stream. Network, provider caching and rate limits can delay updates.",
                            "The collector must stay running to capture new seasons. A stopped preview does not collect.",
                            "No wager recommendations, market auto-selection, account access or bet placement."]}


class ScanModel(BaseModel):
    reset: bool = False
    paused: bool | None = None


@app.get("/api/history/scan")
async def scan_state():
    """What the season-id scan has found, what is left, and where it is looking now."""
    return {"ok": True, "scan": collector.scanner.summary(), "plan": collector.plan(),
            "coverage": store.published_coverage()}


@app.post("/api/history/scan")
async def scan_command(body: ScanModel):
    """Start or restart the gap scan, or pause it while other work has priority.

    Body is optional: `{}` restarts the scan over the newest gaps, `{"reset": false}` just reports.
    """
    if body.paused is not None:
        collector.stop_scan = bool(body.paused)
    if body.reset:
        collector.scanner.reset()
        collector.stop_scan = False
        store.log("discovery", "Season-id scan restarted · probing the newest gaps first")
    collector.wake.set()
    return {"ok": True, "scan": collector.scanner.summary(), "plan": collector.plan(),
            "paused": bool(collector.stop_scan)}


@app.get("/api/provenance/{digest}")
def provenance(digest: str):
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise HTTPException(400, "Invalid receipt hash")
    row = store.one("SELECT * FROM receipts WHERE hash=?", (digest,))
    if not row:
        raise HTTPException(404, "Receipt unavailable under live snapshot retention")
    row["payload"] = json.loads(zlib.decompress(row.pop("body")))
    return row


@app.get("/api/events")
async def events(request: Request):
    async def stream():
        previous, last_heartbeat = None, 0
        while not await request.is_disconnected():
            version = store.version
            if previous != version or time.time() - last_heartbeat > 15:
                yield "data: " + dumps({"version": version, "fp_revision": store.fp_revision, "server_time": time.time()}) + "\n\n"
                previous, last_heartbeat = version, time.time()
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


last_sync = 0.0


@app.post("/api/sync")
async def sync_now():
    global last_sync
    if time.time() - last_sync < 10:
        return {"ok": True, "message": "A refresh is already queued. Source rate limits are still respected."}
    last_sync = time.time()
    collector.request_sync()
    return {"ok": True, "message": "Refresh queued; the collector will check the next public snapshot."}


class PauseModel(BaseModel):
    paused: bool


@app.post("/api/history/pause")
async def pause_history(body: PauseModel):
    store.set_meta("paused", body.paused)
    store.bump()
    collector.wake.set()
    store.log("system", "Historical backfill paused · live sync remains active" if body.paused else "Historical backfill resumed")
    return {"ok": True, "paused": body.paused}


@app.post("/api/history/retry")
async def retry_history():
    with store.lock:
        store.db.execute("UPDATE matchdays SET next_retry=0,error=NULL WHERE status!='complete'")
        store.bump()
    collector.wake.set()
    return {"ok": True, "message": "Incomplete matchdays requeued; source rate limits still apply."}


class RecoverCellModel(BaseModel):
    season: int = Field(ge=START_SEASON)
    day: int = Field(ge=1, le=30)


@app.get("/api/recover/status")
async def recover_status():
    """Progress of the on-demand recovery worker (queued, in flight, recovered, failed)."""
    return {"ok": True, **recoverer.status()}


@app.post("/api/recover/cell")
async def recover_cell(body: RecoverCellModel):
    """Recover one matchday now: the operator clicked an empty cell in the data health table.

    The results feed that backs Betika's results page is queried in the background —
    no browser is opened. The matchday is stored only if the source answers with that
    exact day's finals; a clamped or partial answer is recorded as an error, never misfiled.
    """
    try:
        state = recoverer.enqueue_cell(body.season, body.day)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "queued": {"season": body.season, "day": body.day}, **state}


@app.post("/api/recover/all")
async def recover_all():
    """Queue every missing-but-recoverable matchday: published days still open, and any
    matchday in a season that is already in the past. Newest season first."""
    state = recoverer.enqueue_all()
    return {"ok": True, **state}


class PinModel(BaseModel):
    kind: Kind
    team: str = Field(max_length=100)
    historical_team: str = Field(max_length=100)
    historical_season: int = Field(ge=3134345)
    start: int = Field(ge=1, le=30)
    current_season: int = Field(ge=3134345)
    length: int = Field(ge=1, le=30)
    current_signature: str = Field(min_length=1, max_length=30)


@app.get("/api/pins")
def pins():
    return {"pins": [{"id": r["id"], "created_at": r["created_at"], **json.loads(r["data"])} for r in store.all("SELECT * FROM pins ORDER BY id DESC")]}


@app.post("/api/pins")
async def pin(body: PinModel):
    up = store.get_meta("upcoming", {})
    current = up.get("season")
    length = min(store.prefix_length(current) if current else 0, up.get("day", 1) - 1)
    if body.team not in up.get("teams", []) or not length or body.start + length - 1 > 30:
        raise HTTPException(400, "This alignment is not available")
    if body.historical_season >= current:
        raise HTTPException(400, "Historical season must precede the current season")
    a = store.one("SELECT * FROM fingerprints WHERE kind=? AND season=? AND team=?", (body.kind, current, body.team))
    b = store.one("SELECT * FROM fingerprints WHERE kind=? AND season=? AND team=?", (body.kind, body.historical_season, body.historical_team))
    if not a or not b:
        raise HTTPException(404, "Blueprint not found")
    prefix, history = a["signature"][:length], b["signature"][body.start - 1:body.start - 1 + length]
    if body.current_season != current or body.length != length or body.current_signature != prefix:
        raise HTTPException(409, "The finalized prefix changed. Refresh the comparison before pinning.")
    if "." in prefix + history:
        raise HTTPException(400, "Cannot pin an alignment with missing results")
    payload = {**body.model_dump(), "current_season": current, "length": length, "revision": store.fp_revision,
               "current_cards": json.loads(a["cards"])[:length], "historical_cards": json.loads(b["cards"]),
               "matched": sum(x == y for x, y in zip(prefix, history))}
    with store.lock:
        if store.one("SELECT COUNT(*) n FROM pins")["n"] >= 100:
            raise HTTPException(400, "Archive limit is 100 pins. Remove a pin first.")
        cur = store.db.execute("INSERT INTO pins(data,created_at) VALUES(?,?)", (dumps(payload), time.time()))
        ident = cur.lastrowid
    return {"ok": True, "id": ident}


@app.delete("/api/pins/{ident}")
async def delete_pin(ident: int):
    with store.lock:
        store.db.execute("DELETE FROM pins WHERE id=?", (ident,))
    return {"ok": True}


@app.get("/api/export/results.csv")
def export_csv(season: int | None = Query(None, ge=3134345)):
    return Response("\ufeff" + store.export_csv(season), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="league-results-{season or "all"}.csv"'})


@app.get("/api/export/database")
def export_database():
    DATA.mkdir(exist_ok=True)
    descriptor, name = tempfile.mkstemp(suffix=".sqlite", prefix="league-backup-", dir=DATA)
    os.close(descriptor)
    store.backup(name)
    return FileResponse(name, filename="league-dna.sqlite", media_type="application/vnd.sqlite3", background=BackgroundTask(os.unlink, name))


web = ROOT / "web"
web.mkdir(exist_ok=True)
@app.get("/api/playground")
def playground(request: Request):
    """Multi-engine FT-score predictions with the walk-forward evidence behind them."""
    payload = playground_service.serve()
    body, compressed = playground_service.encoded(payload)
    etag = '"pg-' + str(zlib.crc32(playground_service.board_key().encode())) + '-' + \
           str(playground_service.state.get("status")) + '"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    encoded = "gzip" in request.headers.get("accept-encoding", "")
    return Response(compressed if encoded else body, media_type="application/json",
                    headers={"ETag": etag, "Vary": "Accept-Encoding",
                             **({"Content-Encoding": "gzip"} if encoded else {})})


@app.post("/api/playground/rebuild")
def playground_rebuild():
    """Ask for a fresh build; the result arrives through /api/playground when it is ready."""
    return {"status": playground_service.ensure(force=True)}


@app.get("/api/playground/status")
def playground_status():
    return {"status": playground_service.status(), "summary": playground_service.summary()}


@app.get("/api/playground/tick")
def playground_tick():
    """The fast poll: a few hundred bytes that change the moment the board should."""
    return playground_service.tick()


@app.get("/api/playground/history")
def playground_history(season: int | None = None):
    """The frozen sheets, season by season — what the playground said and how it scored."""
    ledger = playground_service.ledger
    seasons = ledger.seasons()
    chosen = season if season is not None else (seasons[0]["season"] if seasons else None)
    if chosen is None:
        return {"seasons": [], "season": None, "days": [], "stats": {}, "teams": [], "integrity": {}}
    return {"seasons": seasons, "season": chosen, "days": ledger.days(chosen),
            "stats": ledger.stats(chosen), "teams": ledger.team_stats(chosen),
            "integrity": ledger.integrity(chosen),
            "rows": ledger.season_rows(chosen)}


@app.get("/api/playground/sheet")
def playground_sheet(season: int, day: int):
    """One locked matchday sheet, exactly as it was written before kick-off."""
    sheet = playground_service.ledger.sheet_rows(season, day)
    if sheet is None:
        raise HTTPException(404, "This matchday has no frozen sheet in the ledger.")
    return sheet


PLAYGROUND_ASSIST = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>FT score Prediction Playground</title></head>
<body style="margin:0;background:#0a1020;color:#e8eefc;font:15px/1.6 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
<div style="max-width:760px;margin:8vh auto;padding:26px;border:1px solid #22314f;border-radius:14px;background:#111a2e">
<h1 style="margin:0 0 6px;font-size:20px">FT score Prediction Playground</h1>
<p style="color:#93a4c4;margin:0 0 18px">The board file is not present in this installation, so there is nothing to show here yet.</p>
<p>The board is its own workspace folder. Put it next to the project (or inside it) as
<code style="background:#0d1526;border:1px solid #22314f;border-radius:5px;padding:1px 5px">FT score Prediction Playground/index.html</code>.</p>
<p>If you are running from a package, re-extract it completely — the folder travels with the archive.
If you are working from source, rebuild the board with:</p>
<pre style="background:#0d1526;border:1px solid #22314f;border-radius:10px;padding:12px;overflow:auto">python3 scripts/embed_snapshot.py --build</pre>
<p style="color:#93a4c4">That re-runs the engines (~80 s) and writes the board with a fresh snapshot.
The API side is already live: <a style="color:#5aa9ff" href="/api/playground/status">/api/playground/status</a>.</p>
</div></body></html>"""


@app.get("/api/matchups/tick")
def matchups_tick():
    """The cheap beat the matchup workspace polls: board key, lock facts, graded counts."""
    return matchup_service.tick()


@app.get("/api/matchups")
def matchups(season: int | None = Query(default=None, ge=3134345)):
    """The workspace payload: the board, the upcoming matchday and the ledger health."""
    return matchup_service.payload(season)


@app.get("/api/matchups/pairs")
def matchups_pairs(q: str | None = Query(default=None, max_length=80),
                   limit: int = Query(default=400, ge=1, le=1000)):
    """The selectable list for the historical analysis: every pair that has ever met."""
    return {"pairs": matchup_service.index.pairs(q, limit), "league": matchup_service.index.league_rates()}


@app.get("/api/matchups/pair")
def matchups_pair(home: str = Query(min_length=1, max_length=100),
                  away: str = Query(min_length=1, max_length=100),
                  venue: Literal["ordered", "either"] = "ordered"):
    """Every recorded meeting of one pair, with its HT/FT record and the half that decided each."""
    if home.strip().lower() == away.strip().lower():
        raise HTTPException(status_code=400, detail="A pair needs two different teams")
    analysis = matchup_service.index.analysis(home.strip(), away.strip(), either=(venue == "either"))
    return {"ok": True, "analysis": analysis}


@app.post("/api/matchups/rebuild")
def matchups_rebuild():
    """Rebuild the pair index from the stored results. Safe at any time; the ledger is untouched."""
    rebuilt = matchup_service.index.rebuild_all()
    return {"ok": True, "seasons": len(rebuilt), "matches": sum(rebuilt.values()),
            "index": matchup_service.index.revision()}


WORKSPACE_HTML = web / "index.html"


@app.get("/matchups")
def matchups_page():
    """A direct link into the head-to-head workspace; the client routes from there."""
    if WORKSPACE_HTML.exists():
        return FileResponse(WORKSPACE_HTML, media_type="text/html")
    return HTMLResponse(PLAYGROUND_ASSIST, status_code=200)


@app.get("/playground")
def playground_page():
    """The standalone board; it renders from its embedded snapshot if the API is absent."""
    if PLAYGROUND_HTML.exists():
        return FileResponse(PLAYGROUND_HTML, media_type="text/html")
    return HTMLResponse(PLAYGROUND_ASSIST, status_code=200)


# ----------------------------------------------------------------------
# Betika Virtual Edge Analyzer workspace routes
# ----------------------------------------------------------------------
# All writers go to data/edge.sqlite (owned by this package).  Readers
# route through the live archive when it has data, otherwise the bundled
# fixtures under tests/fixtures/ are used.  The exact same patterns as
# the rest of the v2.9.0 backend (FASTAPI-only handlers, JSON in/out).
betika_edge.bootstrap()


def _betika_int(value, default=None, ge=None, le=None):
    try:
        out = int(value) if value is not None else default
    except (TypeError, ValueError):
        out = default
    if out is None:
        return None
    if ge is not None and out < ge:
        out = ge
    if le is not None and out > le:
        out = le
    return out


@app.get("/api/betika-edge/status")
def betika_status():
    return betika_edge.status()


@app.post("/api/betika-edge/refresh")
def betika_refresh():
    return betika_edge.refresh()


@app.get("/api/betika-edge/teams")
def betika_teams():
    return {"teams": betika_edge.teams(),
            "profiles": betika_edge.team_profiles()}


@app.get("/api/betika-edge/matches")
def betika_matches(season: int | None = None, matchday: int | None = None):
    season = _betika_int(season, ge=3134345)
    matchday = _betika_int(matchday, ge=1, le=30)
    return {"matches": betika_edge.matches(season=season, matchday=matchday)}


@app.get("/api/betika-edge/matchday")
def betika_matchday(season: int = Query(ge=3134345)):
    return betika_edge.matchday(season=season)


@app.get("/api/betika-edge/markets")
def betika_markets(match_id: str = Query(min_length=1, max_length=120)):
    return {"markets": betika_edge.markets_for(match_id)}


@app.get("/api/betika-edge/no-vig")
def betika_no_vig(match_id: str = Query(min_length=1, max_length=120)):
    return betika_edge.no_vig_table_for(match_id)


@app.get("/api/betika-edge/cross-flags")
def betika_cross_flags(match_id: str = Query(min_length=1, max_length=120),
                       threshold: float = Query(0.03, ge=0.0, le=1.0)):
    return {"threshold": threshold, "flags": betika_edge.cross_flags(match_id, threshold=threshold)}


@app.get("/api/betika-edge/h2h")
def betika_h2h(home: str = Query(min_length=1, max_length=80),
               away: str = Query(min_length=1, max_length=80)):
    return betika_edge.h2h(home.strip(), away.strip())


@app.get("/api/betika-edge/predict")
def betika_predict(home: str = Query(min_length=1, max_length=80),
                   away: str = Query(min_length=1, max_length=80)):
    return betika_edge.predict(home.strip(), away.strip())


@app.get("/api/betika-edge/value-bets")
def betika_value_bets(match_id: str = Query(min_length=1, max_length=120),
                      min_edge: float | None = Query(None, ge=0.0, le=1.0),
                      kelly: float | None = Query(None, ge=0.0, le=1.0)):
    return {"bets": betika_edge.value_bets_for(match_id, min_edge=min_edge, kelly=kelly)}


@app.get("/api/betika-edge/two-market")
def betika_two_market(match_id: str = Query(min_length=1, max_length=120)):
    return betika_edge.two_market_view(match_id)


@app.get("/api/betika-edge/ledger")
def betika_ledger():
    return {"rows": betika_edge.ledger_rows(), "summary": betika_edge.ledger_summary()}


@app.post("/api/betika-edge/place")
def betika_place(payload: dict):
    match_id = str(payload.get("match_id") or "").strip()
    stake = payload.get("stake")
    if stake is not None:
        try:
            stake = float(stake)
        except (TypeError, ValueError):
            stake = None
    return betika_edge.place_bet(match_id, stake=stake)


@app.post("/api/betika-edge/settle")
def betika_settle(payload: dict | None = None):
    """Settle paper bets.

    With no body: settle every bet whose match has a final score in the live archive.
    With body ``{"bet_id": N, "away_goals": int}``: settle one bet by simulated result.
    """
    if not payload or "bet_id" not in payload:
        return betika_edge.settle_pending()
    bid = int(payload.get("bet_id"))
    away_goals = int(payload.get("away_goals", 0))
    from .betika_edge import ledger as _ledger
    _ledger.settle_bet(bid, away_goals)
    return {"settled": 1, "bet_id": bid, "away_goals": away_goals}


@app.get("/api/betika-edge/settings")
def betika_settings_get():
    return betika_edge.settings_get()


@app.post("/api/betika-edge/settings")
def betika_settings_set(payload: dict):
    return betika_edge.settings_set({str(k): str(v) for k, v in payload.items()})


@app.post("/api/betika-edge/seed-h2h")
def betika_seed(payload: dict):
    home = str(payload.get("home") or "").strip()
    away = str(payload.get("away") or "").strip()
    n = _betika_int(payload.get("n"), default=5, ge=1, le=20)
    return betika_edge.seed_demo_h2h(home, away, n=n)


app.mount("/", StaticFiles(directory=web, html=True), name="web")
