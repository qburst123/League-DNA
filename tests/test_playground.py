"""Unit tests for the FT score Prediction Playground engines.

The heavy walk-forward run is exercised by scripts/audit_playground.py; these tests pin the
pieces it is built from: the probability grids, the smoothing, the market reconstruction,
the calibration, the mixture fitting and the score-keeping helpers.
"""
from __future__ import annotations
import json
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import playground as pg                                    # noqa: E402
from backend.source import START_SEASON                                # noqa: E402

TEST_SEASON = START_SEASON + 60          # inside the archive's own season range


def synthetic_history(matches: int = 90, teams=("A", "B", "C", "D")) -> list:
    """A deterministic mini-league: every pair plays home and away, low-scoring and stable."""
    history = []
    index = 0
    for day in range(1, matches // (len(teams) // 2 * 2) + 2):
        for position in range(0, len(teams), 2):
            home, away = teams[position], teams[position + 1]
            if index >= matches:
                return history
            goals_home = (index + position) % 3
            goals_away = (index + position + 1) % 2
            history.append(pg.Match(index, TEST_SEASON + day // 30, day % 30 or 1, 1_700_000_000.0 + 600 * index,
                                    f"ev{index}", home, away, goals_home, goals_away, 1_700_000_000.0, day))
            index += 1
    return history


def store_row(event_id, season, day, home, away, ft, start_time=None, status="final"):
    """A row in the exact shape backend.store.ingest expects."""
    return {"event_id": event_id, "season": season, "day": day, "home": home, "away": away,
            "start_time": start_time, "status": status, "source_status": "ended", "phase": "FT",
            "ht": (max(0, ft[0] - 1), max(0, ft[1] - 1)), "ft": ft, "live_ht": None, "live_ft": None,
            "match_time": None}


# ------------------------------------------------------------------ grids

def test_grids_are_probability_distributions():
    for grid in (pg.uniform_grid(), pg.poisson_grid(1.4, 1.1), pg.dixon_coles_grid(1.4, 1.1, 0.05),
                 pg.grid_of_symbols({"1:0": 3, "0:0": 1})):
        total = sum(sum(row) for row in grid)
        assert total == pytest.approx(1.0, abs=1e-9)
        assert len(grid) == pg.SIDES and all(len(row) == pg.SIDES for row in grid)
        assert all(cell >= 0 for row in grid for cell in row)


def test_dixon_coles_correction_moves_the_low_scores():
    """Dixon-Coles' rho lifts the 1:0 / 0:1 cells and damps the two low draws."""
    plain = pg.poisson_grid(1.2, 1.0)
    tilted = pg.dixon_coles_grid(1.2, 1.0, 0.25)
    assert tilted[0][0] < plain[0][0]
    assert tilted[1][1] < plain[1][1]
    assert tilted[1][0] > plain[1][0] and tilted[0][1] > plain[0][1]
    assert tilted[2][1] == pytest.approx(plain[2][1], abs=1e-6)


def test_score_symbols_round_trip():
    assert pg.score_symbol(2, 1) == "2:1"
    assert pg.parse_symbol("2:1") == (2, 1)
    with pytest.raises(ValueError):
        pg.parse_symbol("not a score")


def test_flip_grid_swaps_perspectives():
    grid = pg.poisson_grid(1.6, 0.8)
    assert pg.flip_grid(pg.flip_grid(grid)) == grid
    assert pg.flip_grid(grid)[0][1] == grid[1][0]


# ------------------------------------------------------------------ sequence models

def test_markov_backoff_prefers_the_recorded_continuation():
    history = synthetic_history(120)
    library = pg.SequenceLibrary.build(history)
    rows = library.team_rows["A"]
    prefix = [row["symbol"] for row in rows]
    distribution, evidence = pg.markov_distribution(library, "A", prefix)
    assert distribution is not None
    assert evidence["chosen"] in (3, 2, 1)
    assert sum(distribution.values()) == pytest.approx(1.0, abs=1e-9)
    assert evidence["levels"][0]["order"] == 3 or len(prefix) < 3


def test_markov_without_context_is_flat_but_smoothed():
    empty = pg.SequenceLibrary()
    distribution, evidence = pg.markov_distribution(empty, "A", [])
    assert distribution is None and evidence["reason"] == "no context yet"

    history = synthetic_history(40)
    library = pg.SequenceLibrary.build(history)
    distribution, _ = pg.markov_distribution(library, "A", library.team["A"][:1])
    assert distribution is not None
    assert len(distribution) >= len(library.vocabulary())


def test_rescore_with_zero_gamma_is_the_reference():
    history = synthetic_history(120)
    library = pg.SequenceLibrary.build(history)
    prefix = [row["symbol"] for row in library.team_rows["B"]]
    distribution, _ = pg.markov_distribution(library, "B", prefix)
    reference = pg.poisson_grid(1.3, 1.0)
    assert pg.rescore(reference, distribution, pg.marginal_distribution(library), 0.0) is reference
    tilted = pg.rescore(reference, distribution, pg.marginal_distribution(library), 1.0)
    assert sum(sum(row) for row in tilted) == pytest.approx(1.0, abs=1e-9)
    assert tilted != reference


def test_knn_neighbours_come_from_the_recorded_sequences():
    history = synthetic_history(160)
    library = pg.SequenceLibrary.build(history)
    prefix = [row["symbol"] for row in library.team_rows["C"]]
    distribution, evidence = pg.knn_distribution(library, "C", prefix)
    assert distribution is not None and evidence["matched"] > 0
    assert set(evidence["neighbours"][0]) >= {"season", "team", "similarity", "next_score"}
    assert sum(distribution.values()) == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------------ market engine

MARKET_ROW = {
    "event_id": "ev1", "fetched_at": 1_700_000_000,
    "data": json.dumps([
        {"name": "CORRECT SCORE", "odds": [{"label": "0:0", "value": "12"}, {"label": "1:0", "value": "9"},
                                           {"label": "1:1", "value": "7"}, {"label": "0:1", "value": "11"},
                                           {"label": "OTHER", "value": "4"}]},
        {"name": "1X2", "odds": [{"label": "1", "value": "2.2"}, {"label": "X", "value": "3.4"},
                                 {"label": "2", "value": "3.1"}]},
        {"name": "TOTAL", "odds": [{"label": "OVER 2.5", "value": "1.9"}, {"label": "UNDER 2.5", "value": "1.9"}]},
        {"name": "BOTH TEAMS TO SCORE", "odds": [{"label": "YES", "value": "1.7"}, {"label": "NO", "value": "2.1"}]},
    ]),
}


def test_market_row_parses_the_needed_groups():
    record = pg.parse_market_row(MARKET_ROW)
    assert set(record) >= {"correct_score", "one_x_two", "total", "btts"}
    assert record["one_x_two"]["1"] == pytest.approx(2.2)
    assert pg.parse_market_row({"data": "[]"}) is None


def test_market_grid_devigs_and_rebuilds_the_other_bucket():
    record = pg.parse_market_row(MARKET_ROW)
    grid, explain = pg.market_grid(record, pg.poisson_grid(1.3, 1.1))
    assert sum(sum(row) for row in grid) == pytest.approx(1.0, abs=1e-9)
    assert explain["overround"] != 0 and explain["listed_scores"] == 4
    # de-vigged prices put 1:1 above 0:0
    assert grid[1][1] > grid[0][0]


def test_market_grid_reports_why_it_cannot_act():
    grid, explain = pg.market_grid({"correct_score": {}}, pg.uniform_grid())
    assert grid is None and "correct-score" in explain["reason"]


def test_reconcile_marginals_matches_the_book():
    home_heavy = pg.poisson_grid(2.4, 0.7)
    reconciled = pg.reconcile_marginals(home_heavy, {"one_x_two": {"1": 1.4, "X": 4.0, "2": 8.0}})
    probabilities = pg.result_probabilities(reconciled)
    assert probabilities["win"] > 55
    assert sum(sum(row) for row in reconciled) == pytest.approx(1.0, abs=1e-9)


# ------------------------------------------------------------------ scoring helpers

def test_log_loss_and_rps_prefer_the_true_score():
    grid = pg.grid_of_symbols({"1:0": 6, "2:0": 3, "0:1": 1})
    assert pg.log_loss(grid, "1:0") < pg.log_loss(grid, "0:1")
    assert pg.ranked_probability_score(grid, "1:0") < pg.ranked_probability_score(grid, "0:1")
    assert 0.0 <= pg.ranked_probability_score(grid, "1:0") <= 1.0


def test_wilson_interval_brackets_the_estimate_and_stays_in_range():
    low, high = pg.wilson(15, 100)
    assert 0 < low < 15 < high < 100
    assert pg.wilson(0, 0) == [0.0, 0.0]
    assert pg.wilson(30, 30)[1] == pytest.approx(100.0, abs=0.6)


def test_top_cells_and_agreement_report_the_winning_score():
    grids = {"a": pg.grid_of_symbols({"2:0": 5, "1:1": 2}), "b": pg.grid_of_symbols({"2:0": 4, "1:0": 1})}
    assert pg.top_cells(grids["a"], 1)[0]["score"] == "2:0"
    agreement = pg._agreement(["a", "b"], grids)
    assert agreement["unanimous"] and agreement["top_scores"][0] == {"score": "2:0", "models": 2}


# ------------------------------------------------------------------ calibration and mixture

def test_calibration_shrinks_toward_the_prior():
    sharp = pg.grid_of_symbols({"1:0": 50, "0:1": 1})
    prior = pg.uniform_grid()
    assert pg.calibrate(sharp, prior, 0.0) is sharp
    half = pg.calibrate(sharp, prior, 0.5)
    assert half[1][0] < sharp[1][0] and half[1][0] > prior[1][0]
    assert sum(sum(row) for row in half) == pytest.approx(1.0, abs=1e-9)


def test_blend_renormalises_over_the_engines_present():
    left = pg.grid_of_symbols({"1:0": 5})
    right = pg.grid_of_symbols({"0:0": 5})
    pooled = pg.blend_of({"left": left, "right": right}, {"left": 0.75, "right": 0.25})
    assert pooled[1][0] > pooled[0][0]
    only_left = pg.blend_of({"left": left}, {"left": 0.75, "right": 0.25})
    assert only_left[1][0] == pytest.approx(left[1][0], abs=1e-9)


def test_mixture_weights_are_fitted_and_never_abandon_an_engine():
    """A sharp engine and a flat one: the fit must stay on the simplex and keep both."""
    entries = []
    for index in range(60):
        sharp = index % 2 == 0
        good = pg.grid_of_symbols({"1:0": 60, "2:0": 40}) if sharp else pg.grid_of_symbols({"0:0": 10, "1:0": 10})
        bad = pg.grid_of_symbols({"0:0": 30, "1:1": 30, "2:0": 40})
        symbol = "1:0" if sharp else "0:0"
        entries.append({"grids": {"good": good, "bad": bad}, "ceiling_ok": True, "symbol": symbol,
                        "prior": pg.uniform_grid()})
    weights, report = pg.fit_pool_weights(entries)
    assert weights["good"] > weights["bad"]
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-3)
    assert weights["bad"] >= 0.02                      # Dirichlet floor keeps every engine
    assert report["fitted_on"] == len(entries)


def test_shrinkage_is_fitted_and_bounded():
    library = pg.SequenceLibrary.build(synthetic_history(120))
    prior = pg.prior_distribution(library)
    entries = []
    for index in range(60):
        grid = pg.poisson_grid(1.2 + 0.01 * (index % 5), 1.0)
        symbol = "1:0" if index % 3 else "0:0"
        entries.append({"grids": {"poisson": grid}, "prior": prior, "symbol": symbol, "ceiling_ok": True})
    shrinkage = pg.fit_shrinkage(entries)
    assert 0.0 <= shrinkage["poisson"] <= 1.0


# ------------------------------------------------------------------ registry and history

def test_model_registry_is_complete_and_documented():
    keys = [card["key"] for card in pg.MODEL_CARDS]
    assert keys == ["prior", "elo", "poisson", "markov", "knn", "market"]
    # A candidate engine is implemented and measurable but must not appear on the board until it has
    # earned a place in a leave-one-out walk-forward run (scripts/compare_engines.py).
    assert "h2h" in pg.CANDIDATE_MODELS and "h2h" not in keys
    assert pg.h2h_grid_for is not None and pg.mix_grids is not None
    for card in pg.MODEL_CARDS:
        assert card["name"] and card["blurb"] and card["assumptions"] and card["strengths"] and card["limits"]


def test_load_history_orders_the_fixture_calendar_and_numbers_matchdays(tmp_path):
    from backend.store import Store

    store = Store(tmp_path / "mini.sqlite")
    store.discover([TEST_SEASON])
    teams = [f"T{number:02d}" for number in range(16)]
    rows = []
    for day in (2, 1, 3):
        for index in range(8):
            home, away = teams[2 * index], teams[2 * index + 1]
            rows.append(store_row(f"e{day}{index}", TEST_SEASON, day, home, away, (index % 2, index % 3)))
    store.ingest(rows, "digest", 1_700_000_000)
    history = pg.load_history(store)
    assert [match.day for match in history] == [1] * 8 + [2] * 8 + [3] * 8
    assert [match.matchday_index for match in history[:8]] == [0] * 8
    assert history[-1].matchday_index == 2
    assert pg.timeline(history) == [(TEST_SEASON, 1), (TEST_SEASON, 2), (TEST_SEASON, 3)]
    assert len(pg.before(history, TEST_SEASON, 3)) == 16
    store.close()


def test_decay_weight_halves_over_the_half_life():
    assert pg.decay_weight(0) == 1.0
    assert pg.decay_weight(pg.HALF_LIFE_MATCHDAYS) == pytest.approx(0.5, abs=1e-9)
    assert pg.decay_weight(pg.HALF_LIFE_MATCHDAYS * 2) == pytest.approx(0.25, abs=1e-9)


def test_poisson_strengths_are_centred_and_ordered():
    history = synthetic_history(240)
    model = pg.PoissonStrength.fit(history)
    assert model.matches == len(history)
    assert sum(model.attack.values()) / len(model.attack) == pytest.approx(1.0, abs=0.05)
    assert sum(model.defence.values()) / len(model.defence) == pytest.approx(1.0, abs=0.05)
    lam_home, lam_away = model.lambdas("A", "B")
    assert 0.15 <= lam_home <= 5.5 and 0.15 <= lam_away <= 5.5
    assert abs(model.rho) <= 0.2


def test_market_lookup_is_cut_off_by_capture_time(tmp_path):
    from backend.store import Store

    store = Store(tmp_path / "markets.sqlite")
    store.discover([TEST_SEASON])
    store.ingest([store_row("e1", TEST_SEASON, 1, "A", "B", (1, 0))], "digest", 1_700_000_000)
    store.save_markets("e1", json.loads(MARKET_ROW["data"]), "digest", 1_700_000_500)
    assert pg.load_markets(store, ["e1"]) != {}
    assert pg.load_markets(store, ["e1"], cutoff=1_700_000_400) == {}
    assert pg.load_markets(store, [""]) == {}
    store.close()

def _target_rows(tmp_path, name="mirror.sqlite", season=TEST_SEASON, days=(1, 2)):
    """A store holding two finished matchdays, then the next matchday as the live target."""
    from backend.store import Store

    store = Store(tmp_path / name)
    store.discover([season])
    teams = [f"T{number:02d}" for number in range(16)]
    rows = []
    for day in days:
        for index in range(8):
            home, away = teams[2 * index], teams[2 * index + 1]
            rows.append(store_row(f"e{day}{index}", season, day, home, away,
                                  ((index + day) % 4, (index + day + 1) % 3)))
    for index in range(8):                                   # the matchday to predict
        rows.append(store_row(f"t{index}", season, days[-1] + 1, teams[2 * index], teams[2 * index + 1],
                              (0, 0), status="scheduled"))
    store.ingest(rows, "digest", 1_700_000_000)
    history = pg.load_history(store)
    return store, history, pg.current_targets(store, history)


def test_fixture_rows_mirror_under_heavy_shrinkage(tmp_path):
    """Regression: calibrating the away row toward a home-oriented reference broke the mirror.

    Shrinkage is normally tiny (the window-800 fit lands on zeros, which is why the live board
    looked clean), so this pins the property with deliberately heavy shrinkage and weights.
    """
    store, history, targets = _target_rows(tmp_path)
    assert targets, "no target matchday"
    shrinkage = {model: 0.35 for model in pg.MODEL_KEYS}
    weights = {model: 0.2 for model in pg.MODEL_KEYS}
    rows, _bundle = pg.predict_matchday(store, history, targets, weights=weights, shrinkage=shrinkage)
    index = {(row["event_id"], row["venue"]): row for row in rows}
    checked = 0
    for (event_id, venue), row in index.items():
        if venue != "H":
            continue
        away = index.get((event_id, "A"))
        if away is None:
            continue
        for label, home_grid, away_grid in (("blend", row["blend"]["grid"], away["blend"]["grid"]),
                                            ("hit_blend", row["hit_blend"]["grid"], away["hit_blend"]["grid"])):
            asymmetry = sum(abs(home_grid[a][b] - away_grid[b][a]) / 1000.0
                            for a in range(pg.SIDES) for b in range(pg.SIDES)) / 2.0
            assert asymmetry < 0.005, f"{label} {row['team']} vs {row['opponent']}: TV {asymmetry:.4f}"
        for model in set(row["models"]) & set(away["models"]):
            home_grid, away_grid = row["models"][model]["grid"], away["models"][model]["grid"]
            asymmetry = sum(abs(home_grid[a][b] - away_grid[b][a]) / 1000.0
                            for a in range(pg.SIDES) for b in range(pg.SIDES)) / 2.0
            assert asymmetry < 0.005, f"{model} {row['team']} vs {row['opponent']}: TV {asymmetry:.4f}"
        checked += 1
    assert checked, "no fixture had both rows"
    store.close()


def test_shrinking_all_the_way_lands_every_engine_on_the_league_prior(tmp_path):
    """Shrinkage 1.0 must reproduce the league prior exactly — that pins the shrink target.

    The live path used to shrink toward the fixture's home-oriented Dixon-Coles reference, so
    shrinking the away row by 1.0 produced a home-shaped grid. It must be the league prior,
    which is the only grid the walk-forward backtest ever calibrates toward.
    """
    store, history, targets = _target_rows(tmp_path, name="prior.sqlite")
    rows, bundle = pg.predict_matchday(store, history, targets, weights={},
                                       shrinkage={model: 1.0 for model in pg.MODEL_KEYS})
    prior = pg.prior_distribution(bundle.library)
    for row in rows:
        for model, payload in row["models"].items():
            grid = payload["grid"]
            worst = max(abs(grid[a][b] / 1000.0 - prior[a][b]) for a in range(pg.SIDES) for b in range(pg.SIDES))
            assert worst < 2e-3, f"{model} in the {row['venue']} row of {row['team']} stayed {worst:.4f} from the prior"
    store.close()


# ------------------------------------------------------------------ live ledger (the board's data)
def _ledger_store(tmp_path, name="ledger.sqlite", days=tuple(range(1, 13))):
    """Twelve finished matchdays (96 fixtures) plus the incoming one, dense enough to fit on."""
    return _target_rows(tmp_path, name=name, days=days)


def test_live_board_ledger_grades_the_pick_it_played(tmp_path):
    """The live workspace reads `live_board`: one graded row per team and matchday, both blends."""
    store, history, _targets = _ledger_store(tmp_path)
    report = pg.backtest(store, history, window=400)
    board = report["live_board"]
    assert board["season"] == TEST_SEASON
    assert set(board["blends"]) == {"hit_blend", "blend"}
    assert board["teams"] == board["blends"]["hit_blend"]["teams"]        # the primary view
    assert board["summary"] == board["blends"]["hit_blend"]["summary"]
    assert len(board["teams"]) == 16
    graded = 0
    for name in ("hit_blend", "blend"):
        view = board["blends"][name]
        summary = view["summary"]
        assert set(summary) >= {"season", "teams", "picks", "hits", "misses", "direction_hits",
                                "hit_rate", "direction_rate", "matchdays", "no_pick"}
        assert summary["picks"] == sum(len(entry["rows"]) for entry in view["teams"].values())
        assert summary["hits"] == sum(entry["summary"]["hits"] for entry in view["teams"].values())
        assert summary["hits"] + summary["misses"] == summary["picks"]
        assert summary["hit_rate"] == pytest.approx(round(100.0 * summary["hits"] / summary["picks"], 1))
        assert 0.0 <= summary["hit_rate"] <= 100.0 and summary["teams"] == len(view["teams"])
        for team, entry in view["teams"].items():
            rows = entry["rows"]
            assert [row["day"] for row in rows] == sorted(row["day"] for row in rows)
            assert entry["summary"]["picks"] == len(rows)
            assert entry["summary"]["hits"] == sum(1 for row in rows if row["verdict"] == "hit")
            assert entry["summary"]["direction_hits"] == sum(1 for row in rows if row["direction"] == "hit")
            for row in rows:
                assert row["team"] == team and row["season"] == TEST_SEASON
                assert pg.parse_symbol(row["pick"]) is not None
                assert 0.0 <= row["probability"] <= 100.0
                assert 0 < row["agrees"] <= row["engines"]
                assert len(row["top"]) == 3 and row["top"][0] == row["pick"]
                assert row["verdict"] == ("hit" if row["pick"] == row["actual"] else "miss")
                assert row["direction"] in ("hit", "miss")
                assert set(row) >= {"day", "team", "pick", "probability", "agrees", "engines",
                                    "top", "actual", "verdict", "direction"}
                graded += 1
    assert graded, "the ledger recorded no graded picks"
    store.close()


def test_live_board_only_grades_matchdays_that_were_played(tmp_path):
    """Rows exist only for evaluated matchdays: the ongoing matchday is never graded from the future."""
    store, history, targets = _ledger_store(tmp_path, name="ledger-days.sqlite")
    report = pg.backtest(store, history, window=400)
    rows = [row for entry in report["live_board"]["teams"].values() for row in entry["rows"]]
    played = {day for _season, day in pg.timeline(history)}
    evaluated = {row["day"] for row in rows}
    assert evaluated <= played
    assert max(evaluated) < targets[0].day, "the target matchday must not appear as a graded pick"
    assert report["live_board"]["summary"]["matchdays"] == len(evaluated)
    store.close()


def test_live_board_repeats_its_picks_when_later_matchdays_are_removed(tmp_path):
    """Walk-forward invariant: a pick fitted before matchday N cannot change when later days are dropped.

    The ledger covers the evaluated tail of the run (the part the headline numbers come from), so
    the two runs are compared on the matchdays they both evaluate. Those picks and verdicts must be
    identical, which is what makes the board's earlier cells trustworthy on a live payload.
    """
    store, history, _targets = _ledger_store(tmp_path, name="ledger-future.sqlite")
    full = pg.backtest(store, history, window=400)["live_board"]["blends"]["hit_blend"]["teams"]
    cutoff = max(day for _season, day in pg.timeline(history)) - 1
    head = [match for match in history if match.day <= cutoff]
    shrunk = pg.backtest(store, head, window=400)["live_board"]["blends"]["hit_blend"]["teams"]
    assert shrunk, "the truncated run produced no ledger"
    compared_days = set()
    for team, entry in shrunk.items():
        twin_rows = {row["day"]: row for row in full[team]["rows"]}
        for row in entry["rows"]:
            twin = twin_rows.get(row["day"])
            if twin is None:                       # outside the full run's evaluated tail
                continue
            assert (twin["pick"], twin["actual"], twin["verdict"]) == (row["pick"], row["actual"], row["verdict"]), \
                   f"{team} MD{row['day']} changed when later matchdays were removed"
            # The reported probability can move by a few tenths: the mixture refit starts from the
            # seed weights and walks the evaluated tail, and the tail boundary moves with the run
            # length. The pick itself, which is what the board plays, must not move.
            compared_days.add(row["day"])
    assert len(compared_days) >= 3, f"only {len(compared_days)} shared matchdays were comparable"
    store.close()


# ------------------------------------------------------------------ the background service
class _FakeStore:
    """Just enough store for the service: a revision, no meta row, and a log sink."""
    version = 7
    def __init__(self):
        self.messages = []
    def get_meta(self, _key, default=None):
        return default
    def log(self, channel, message, level="info"):
        self.messages.append((channel, message, level))


def test_a_failed_build_is_reported_instead_of_killing_the_worker(tmp_path):
    """Regression: an error before the guarded block killed the builder, leaving 'building' forever."""
    from backend.playground_service import PlaygroundService

    service = PlaygroundService(_FakeStore(), tmp_path / "data")
    service.build_report = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
    service.ensure(force=True)
    service.thread.join(timeout=15)
    status = service.status()
    assert status["attempts"] == 1, "the worker died before recording its attempt"
    assert status["status"] == "error" and "boom" in (status["error"] or "")


def test_the_board_snapshot_is_used_when_the_cache_is_missing(tmp_path, monkeypatch):
    """A fresh extraction has no data/playground.json; the board's copy must serve instead."""
    from backend.playground_service import PlaygroundService

    snapshot = tmp_path / "board" / "playground.json"
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_text(json.dumps({"meta": {"generated_at": 1_700_000_000.0, "build_seconds": 12.0},
                                    "data": {"revision": 11}, "target": {"label": "3135706/24"}}))
    monkeypatch.setattr(PlaygroundService, "board_snapshot", lambda self: snapshot)
    service = PlaygroundService(_FakeStore(), tmp_path / "data")
    status = service.status()
    assert status["has_payload"] and status["generated_at"] == 1_700_000_000.0
    assert status["loaded_from"] == str(snapshot)
    assert service.key() == "7|None|None|w800"          # freshness still keyed on the archive


def test_the_board_carries_the_recorded_score_of_every_matchday_it_shows(tmp_path):
    """The upper row is the ongoing season's recorded FT; the prediction row is graded against it.

    Without this map the board could only show scored matchdays that happen to fall in the
    evaluated tail of the walk-forward run, which is not what the Live FT board does.
    """
    store, history, _targets = _ledger_store(tmp_path, name="scores.sqlite")
    report = pg.backtest(store, history, window=400)
    board = report["live_board"]
    scores = board["scores"]
    assert scores, "the board carried no recorded scores"
    teams = {match.home for match in history} | {match.away for match in history}
    assert set(scores) == teams
    for team, days in scores.items():
        assert days, f"{team} has no recorded matchday"
        for day, symbol in days.items():
            assert pg.parse_symbol(symbol) is not None
    # the same scores the ledger graded its picks against
    graded = 0
    for team, entry in board["blends"]["hit_blend"]["teams"].items():
        for row in entry["rows"]:
            assert scores[team][str(row["day"])] == row["actual"], f"{team} MD{row['day']} disagrees"
            graded += 1
    assert graded, "no graded rows to cross-check"
    # and every matchday of the season that has been played appears on the board
    played = {match.day for match in history if match.season == board["season"]}
    assert {int(day) for days in scores.values() for day in days} == played
    store.close()


def test_the_board_shows_the_season_the_source_is_playing(tmp_path):
    """`season=` pins the board to the season of the target matchday, not the newest with rows.

    A new season starts while the previous one is still the newest season with a long ledger, so
    the board has to be told which season to display.
    """
    from backend.store import Store

    store = Store(tmp_path / "season-pin.sqlite")
    older, newer = TEST_SEASON, TEST_SEASON + 1
    store.discover([older, newer])
    teams = [f"T{number:02d}" for number in range(16)]
    rows = []
    for season, days in ((older, range(1, 9)), (newer, range(1, 4))):
        for day in days:
            for index in range(8):
                rows.append(store_row(f"{season}-{day}-{index}", season, day, teams[2 * index], teams[2 * index + 1],
                                      ((index + day) % 4, (index + day + 1) % 3)))
    store.ingest(rows, "digest", 1_700_000_000)
    history = pg.load_history(store)

    auto = pg.backtest(store, history, window=400)["live_board"]
    pinned = pg.backtest(store, history, window=400, season=older)["live_board"]
    assert auto["season"] == newer and pinned["season"] == older
    assert auto["scores"] != pinned["scores"], "the pinned season must not show the other season's scores"
    assert set(pinned["scores"]) and all(days for days in pinned["scores"].values())
    assert {int(day) for days in pinned["scores"].values() for day in days} == set(range(1, 9))
    store.close()


def test_the_service_reports_why_it_wants_to_rebuild():
    """The reason decides how long the builder waits, so it has to distinguish a moved matchday."""
    from backend.playground_service import PlaygroundService, key_parts

    class Store:
        version = 18485
        def __init__(self, season, day):
            self.target = {"season": season, "day": day}
        def get_meta(self, key, default=None):
            return self.target if key == "upcoming" else default

    class Ledger:
        graded = None
        def stats(self, _season=None):
            return {"last_graded_day": self.graded}

    service = PlaygroundService.__new__(PlaygroundService)      # no cache, no threads
    service.store, service.window, service.payload = Store(3135734, 21), 800, None
    service.ledger, service.report = Ledger(), None
    assert service.stale() == (True, "no payload")
    service.report = {"meta": {"key": "18485|3135734|20|w800"}}
    assert service.stale() == (True, "target matchday moved")
    service.report = {"meta": {"key": "18485|3135706|30|w800"}}
    assert service.stale() == (True, "new season")
    service.report = {"meta": {"key": "18485|3135734|21|w8000"}}
    assert service.stale() == (True, "archive changed")
    service.report = {"meta": {"key": "18485|3135734|21|w800"}}
    assert service.stale() == (False, "current")
    # a matchday graded since the measurement is a reason to refit the engines
    service.report = {"meta": {"key": "18485|3135734|21|w800", "graded_through": 8}}
    service.ledger.graded = 9
    assert service.stale() == (True, "new results")
    assert key_parts("18485|3135734|21|w800") == ("3135734", "21") and key_parts("junk") == (None, None)


def test_a_season_that_has_just_started_shows_pending_predictions_only(tmp_path):
    """Season rollover: the board must follow the new season with an empty score row and no ledger."""
    from backend.store import Store

    store = Store(tmp_path / "rollover.sqlite")
    old_season, new_season = TEST_SEASON, TEST_SEASON + 1
    store.discover([old_season, new_season])
    teams = [f"T{number:02d}" for number in range(16)]
    rows = []
    for day in range(1, 13):                                    # the finished season
        for index in range(8):
            rows.append(store_row(f"o{day}-{index}", old_season, day, teams[2 * index], teams[2 * index + 1],
                                  ((index + day) % 4, (index + day + 1) % 3)))
    for index in range(8):                                      # the new season, nothing played
        rows.append(store_row(f"n{index}", new_season, 1, teams[2 * index], teams[2 * index + 1],
                              (0, 0), status="scheduled"))
    store.ingest(rows, "digest", 1_700_000_000)
    history = pg.load_history(store)
    targets = pg.current_targets(store, history)
    assert targets and {fixture.season for fixture in targets} == {new_season}

    report = pg.backtest(store, history, window=400, season=new_season)
    board = report["live_board"]
    assert board["season"] == new_season
    assert board["scores"] == {}, "the new season has no recorded score to show yet"
    assert board["summary"]["picks"] == 0 and board["summary"]["hits"] == 0
    rows_payload, _bundle = pg.predict_matchday(store, history, targets)
    assert rows_payload and all(row["day"] == 1 for row in rows_payload)
    # the competition view is added by the service; here the prediction itself has to exist
    assert all(row["hit_blend"]["top"] and pg.parse_symbol(row["hit_blend"]["top"][0]["score"]) is not None
               for row in rows_payload)
    store.close()


def test_a_target_row_accounts_for_every_engine_even_when_one_cannot_price_it(tmp_path):
    """The engines workspace renders the whole roster: an engine that cannot price a matchday is
    listed as abstaining with its reason, so it must be named in the payload rather than dropped."""
    store, history, targets = _target_rows(tmp_path, name="engines.sqlite")
    assert targets
    rows, _bundle = pg.predict_matchday(store, history, targets)
    assert rows
    for row in rows:
        models, unavailable = row["models"], row["unavailable"]
        assert set(models) | set(unavailable) == set(pg.MODEL_KEYS), "every engine must be accounted for"
        assert not (set(models) & set(unavailable))
        for key, explain in unavailable.items():
            assert (explain or {}).get("reason"), f"{key} abstains without a reason"
        # no market catalogue was captured for this fixture, so that engine must abstain on the record
        assert "market" in unavailable
        assert "catalogue" in unavailable["market"]["reason"]
    store.close()


# ------------------------------------------------------------------ the frozen ledger
def _ledger_service(tmp_path, name="frozen.sqlite", days=tuple(range(1, 13))):
    """A service wired to a small store: finished matchdays, then one announced and unplayed."""
    from backend.playground_service import PlaygroundService

    store, history, _targets = _target_rows(tmp_path, name=name, days=days)
    service = PlaygroundService(store, tmp_path / "data")
    service.report = {"meta": {"key": service.key(), "graded_through": None},
                      "weights": {"prior": 1.0}, "hit_weights": {"prior": 1.0}}
    return store, history, service


def test_a_sheet_is_locked_once_and_never_rewritten(tmp_path):
    """The headline promise: after the result arrives the forecast is byte-identical."""
    store, _history, service = _ledger_service(tmp_path)
    try:
        service.sync()
        target_day = TEST_SEASON_DAY = max(fixture.day for fixture in pg.current_targets(store, pg.load_history(store)))
        locked = service.ledger.sheet_rows(TEST_SEASON, target_day)
        assert locked and locked["source"] == "live"
        before = {row["team"]: (row["pick"], row["probability"]) for row in locked["picks"]}
        assert before and all(pick for pick, _probability in before.values())
        assert all(row["actual"] is None for row in locked["picks"]), "an unplayed matchday must not be graded"

        # the source now records the matchday
        results = [store_row(f"t{index}", TEST_SEASON, target_day, f"T{2 * index:02d}", f"T{2 * index + 1:02d}",
                             (index % 3, (index + 1) % 3)) for index in range(8)]
        store.ingest(results, "digest-2", 1_700_000_100)
        service.sync()

        after = service.ledger.sheet_rows(TEST_SEASON, target_day)
        now = {row["team"]: (row["pick"], row["probability"]) for row in after["picks"]}
        assert now == before, "a played matchday was given a new forecast"
        assert all(row["actual"] for row in after["picks"]), "the recorded result was not written"
        assert all(row["verdict"] for row in after["picks"]), "the verdict was not written"
        assert not service.ledger.lock_sheet(TEST_SEASON, target_day, [], source="live"), \
            "a locked sheet accepted a second write"
    finally:
        service.ledger.close()
        store.close()


def test_the_board_keeps_the_locked_pick_while_results_come_in(tmp_path):
    """Refresh stability: the board's prediction row must not move when the score row fills in."""
    store, _history, service = _ledger_service(tmp_path, name="stable.sqlite")
    try:
        service.sync()
        pending_rows = service.compose()["target"]["rows"]
        pending_day = max(row["day"] for row in pending_rows)
        # the announced matchday is served from the locked sheet, before any of it has been played
        picks = {row["team"]: row["hit_blend"]["score"] for row in pending_rows if row["day"] == pending_day}
        assert picks, "the announced matchday was not on the board"

        results = [store_row(f"t{index}", TEST_SEASON, pending_day, f"T{2 * index:02d}", f"T{2 * index + 1:02d}",
                             (index % 4, (index + 2) % 3)) for index in range(8)]
        store.ingest(results, "digest-3", 1_700_000_200)
        service.sync()
        board_after = service.compose()["live_board"]
        graded = {team: row["pick"] for team, payload in board_after["blends"]["hit_blend"]["teams"].items()
                  for row in payload["rows"] if row["day"] == pending_day}
        assert graded == picks, "the prediction row changed after the results arrived"
        assert all(row["actual"] for team, payload in board_after["blends"]["hit_blend"]["teams"].items()
                   for row in payload["rows"] if row["day"] == pending_day)
    finally:
        service.ledger.close()
        store.close()


def test_a_sheet_locked_before_kick_off_is_proof_and_one_locked_after_is_marked_late(tmp_path):
    """`late` is the difference between a forecast and a description of the past."""
    store, history, service = _ledger_service(tmp_path, name="lead.sqlite")
    try:
        service.sync()
        day = max(fixture.day for fixture in pg.current_targets(store, pg.load_history(store)))
        sheet = service.ledger.sheet( TEST_SEASON, day)
        kickoff = min(fixture.kickoff for fixture in pg.current_targets(store, pg.load_history(store)))
        lead = kickoff - sheet["locked_at"]
        assert (sheet["late"] == 0) == (lead > 0), "the late flag does not match the clock"
        integrity = service.ledger.integrity(TEST_SEASON)
        assert integrity["live"] == integrity["sheets"] - integrity["backfilled"]
        assert integrity["on_time_share"] in (0.0, 100.0, None) or 0 <= integrity["on_time_share"] <= 100
    finally:
        service.ledger.close()
        store.close()
