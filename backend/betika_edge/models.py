"""models.py — UI helpers."""

from __future__ import annotations

from typing import Optional

from . import db
from .poisson_model import fit_league, predict_fixture, team_profile


def all_team_profiles():
    fit = fit_league()
    return [team_profile(fit, t["team_id"]) for t in db.list_teams()]


def predict_for_match(home_id: int, away_id: int):
    fit = fit_league()
    return fit, predict_fixture(fit, home_id, away_id)
