"""
poisson_model.py — Bivariate Poisson fit + prediction, reading from
the unified store via `db.list_matches()` (which already merges the
live archive and the bundled fixtures).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from math import exp, lgamma
from typing import Optional

import numpy as np
from scipy.optimize import minimize

from . import db


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return exp(-lam) * (lam ** k) / exp(lgamma(k + 1))


# ----------------------------------------------------------------------
# Data container
# ----------------------------------------------------------------------
@dataclass
class TeamStrength:
    team_id: int
    name: str
    attack: float = 1.0
    defence: float = 1.0
    matches: int = 0
    goals_for: int = 0
    goals_against: int = 0


@dataclass
class LeagueFit:
    home_advantage: float = 1.30
    league_avg_home: float = 1.40
    league_avg_away: float = 1.05
    teams: dict[int, TeamStrength] = field(default_factory=dict)
    n_matches: int = 0
    log_likelihood: float = 0.0

    def team(self, team_id: int) -> TeamStrength:
        if team_id not in self.teams:
            self.teams[team_id] = TeamStrength(team_id, next(
                (t["name"] for t in db.list_teams() if t["team_id"] == team_id), f"#{team_id}"
            ))
        return self.teams[team_id]


# ----------------------------------------------------------------------
# Fit
# ----------------------------------------------------------------------
def fit_league() -> LeagueFit:
    matches = db.list_matches()
    finished = [m for m in matches if m.ft_home is not None and m.ft_away is not None]
    if not finished:
        return LeagueFit()

    fit = LeagueFit()
    fit.n_matches = len(finished)

    avg_home = float(np.mean([m.ft_home for m in finished]))
    avg_away = float(np.mean([m.ft_away for m in finished]))
    fit.league_avg_home = avg_home if avg_home > 0 else 1.4
    fit.league_avg_away = avg_away if avg_away > 0 else 1.05

    teams = {t["team_id"]: t["name"] for t in db.list_teams()}
    if not teams:
        teams = {m.home_team_id: m.home_team for m in finished}
        teams.update({m.away_team_id: m.away_team for m in finished})

    def _unpack(x):
        home_adv = float(x[0])
        att = {tid: float(x[1 + i]) for i, tid in enumerate(sorted(teams))}
        deff = {tid: float(x[1 + len(teams) + i]) for i, tid in enumerate(sorted(teams))}
        return home_adv, att, deff

    def _neg_loglik(x):
        home_adv, att, deff = _unpack(x)
        ll = 0.0
        for m in finished:
            th, ta = m.home_team_id, m.away_team_id
            lam_h = max(0.05, att[th] * deff[ta] * home_adv * fit.league_avg_home)
            lam_a = max(0.05, att[ta] * deff[th] * fit.league_avg_away)
            ll += np.log(_poisson_pmf(int(m.ft_home), lam_h) + 1e-12)
            ll += np.log(_poisson_pmf(int(m.ft_away), lam_a) + 1e-12)
        return -ll

    n_teams = len(teams)
    x0 = [1.3] + [1.0] * n_teams + [1.0] * n_teams
    bounds = [(0.5, 3.0)] + [(0.2, 3.5)] * (2 * n_teams)
    try:
        res = minimize(_neg_loglik, x0, method="L-BFGS-B", bounds=bounds, options={"maxiter": 200})
        home_adv, att, deff = _unpack(list(res.x))
        fit.home_advantage = home_adv
        fit.log_likelihood = -float(res.fun)
    except Exception:
        home_adv, att, deff = 1.3, {tid: 1.0 for tid in teams}, {tid: 1.0 for tid in teams}

    for tid, name in teams.items():
        ts = TeamStrength(team_id=tid, name=name, attack=att.get(tid, 1.0), defence=deff.get(tid, 1.0))
        for m in finished:
            if m.home_team_id == tid:
                ts.matches += 1
                ts.goals_for += int(m.ft_home); ts.goals_against += int(m.ft_away)
            elif m.away_team_id == tid:
                ts.matches += 1
                ts.goals_for += int(m.ft_away); ts.goals_against += int(m.ft_home)
        fit.teams[tid] = ts
    return fit


# ----------------------------------------------------------------------
# Score grid
# ----------------------------------------------------------------------
def score_grid(fit: LeagueFit, home_id: int, away_id: int, max_goals: int = 7):
    th = fit.team(home_id); ta = fit.team(away_id)
    lam_h = max(0.02, th.attack * ta.defence * fit.home_advantage * fit.league_avg_home)
    lam_a = max(0.02, ta.attack * th.defence * fit.league_avg_away)
    grid = np.zeros((max_goals + 1, max_goals + 1))
    for h, a in product(range(max_goals + 1), range(max_goals + 1)):
        grid[h, a] = _poisson_pmf(h, lam_h) * _poisson_pmf(a, lam_a)
    s = grid.sum()
    if s > 0:
        grid /= s
    return lam_h, lam_a, grid


# ----------------------------------------------------------------------
# Prediction
# ----------------------------------------------------------------------
@dataclass
class Prediction:
    home_team: str
    away_team: str
    lam_home: float
    lam_away: float
    grid: np.ndarray
    p_home: float
    p_draw: float
    p_away: float
    p_over_05: float
    p_over_15: float
    p_over_25: float
    p_over_35: float
    p_under_25: float
    p_btts_yes: float
    p_btts_no: float
    most_likely: list
    expected_total: float
    expected_home: float
    expected_away: float
    exact_goals: dict
    away_exact_goals: dict
    home_exact_goals: dict
    sample_size: int


def predict_fixture(fit: LeagueFit, home_id: int, away_id: int,
                    max_goals: int = 7) -> Prediction:
    lam_h, lam_a, grid = score_grid(fit, home_id, away_id, max_goals=max_goals)
    home_name = fit.team(home_id).name
    away_name = fit.team(away_id).name

    p_home = float(np.tril(grid, -1).sum())
    p_draw = float(np.diag(grid).sum())
    p_away = float(np.triu(grid, 1).sum())

    p_over_05 = 1.0 - float(grid[0, :].sum() + grid[:, 0].sum() - grid[0, 0])
    p_over_15 = float(sum(grid[h, a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a > 1.5))
    p_over_25 = float(sum(grid[h, a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a > 2.5))
    p_over_35 = float(sum(grid[h, a] for h in range(max_goals + 1) for a in range(max_goals + 1) if h + a > 3.5))
    p_under_25 = 1.0 - p_over_25
    p_btts_yes = float(sum(grid[h, a] for h in range(1, max_goals + 1) for a in range(1, max_goals + 1)))
    p_btts_no = 1.0 - p_btts_yes

    flat = [(h, a, grid[h, a]) for h in range(max_goals + 1) for a in range(max_goals + 1)]
    flat.sort(key=lambda t: -t[2])
    most_likely = [((h, a), float(p)) for h, a, p in flat[:5]]

    expected_total = lam_h + lam_a
    expected_home = lam_h
    expected_away = lam_a

    exact_goals = {}
    for k in range(max_goals + 1):
        exact_goals[str(k)] = float(grid[:, k].sum() + grid[k, :].sum() - grid[k, k])
    exact_goals[f"{max_goals + 1}+"] = max(0.0, 1.0 - sum(exact_goals.values()))
    away_exact_goals = {str(k): float(grid[k, :].sum()) for k in range(max_goals + 1)}
    away_exact_goals[f"{max_goals + 1}+"] = max(0.0, 1.0 - sum(away_exact_goals.values()))
    home_exact_goals = {str(k): float(grid[:, k].sum()) for k in range(max_goals + 1)}
    home_exact_goals[f"{max_goals + 1}+"] = max(0.0, 1.0 - sum(home_exact_goals.values()))

    return Prediction(
        home_team=home_name, away_team=away_name,
        lam_home=lam_h, lam_away=lam_a, grid=grid,
        p_home=p_home, p_draw=p_draw, p_away=p_away,
        p_over_05=p_over_05, p_over_15=p_over_15, p_over_25=p_over_25,
        p_over_35=p_over_35, p_under_25=p_under_25,
        p_btts_yes=p_btts_yes, p_btts_no=p_btts_no,
        most_likely=most_likely,
        expected_total=expected_total, expected_home=expected_home, expected_away=expected_away,
        exact_goals=exact_goals, away_exact_goals=away_exact_goals, home_exact_goals=home_exact_goals,
        sample_size=fit.n_matches,
    )


# ----------------------------------------------------------------------
# Team profile
# ----------------------------------------------------------------------
def team_profile(fit: LeagueFit, team_id: int) -> dict:
    t = fit.team(team_id)
    matches = [m for m in db.list_matches()
               if (m.home_team_id == team_id or m.away_team_id == team_id)
               and m.ft_home is not None]

    gf = []; ga = []
    distribution = {str(k): 0 for k in range(0, 5)}
    distribution["5+"] = 0
    for m in matches:
        if m.home_team_id == team_id:
            goals_for, goals_against = m.ft_home, m.ft_away
        else:
            goals_for, goals_against = m.ft_away, m.ft_home
        gf.append(goals_for); ga.append(goals_against)
        total = goals_for + goals_against
        if total >= 5:
            distribution["5+"] += 1
        else:
            distribution[str(total)] += 1
    n = max(1, len(matches))
    distribution = {k: v / n for k, v in distribution.items()}

    last5 = matches[-5:]
    last10 = matches[-10:]

    def _form(rows):
        if not rows:
            return 0.0
        pts = 0
        for r in rows:
            if r.home_team_id == team_id:
                if r.ft_home > r.ft_away: pts += 3
                elif r.ft_home == r.ft_away: pts += 1
            else:
                if r.ft_away > r.ft_home: pts += 3
                elif r.ft_away == r.ft_home: pts += 1
        return pts / (len(rows) * 3)

    return {
        "team_id": team_id, "name": t.name, "matches": len(matches),
        "attack": t.attack, "defence": t.defence,
        "avg_goals_for": float(np.mean(gf)) if matches else 0.0,
        "avg_goals_against": float(np.mean(ga)) if matches else 0.0,
        "avg_ht_for": 0.0, "avg_ht_against": 0.0,
        "distribution": distribution,
        "form_last5": _form(last5), "form_last10": _form(last10),
    }


if __name__ == "__main__":
    fit = fit_league()
    print(f"matches fitted: {fit.n_matches}  home advantage: {fit.home_advantage:.2f}")
    teams = sorted(fit.teams.values(), key=lambda t: -t.attack)
    for t in teams[:5]:
        print(f"  {t.name:>20s}  att={t.attack:.2f} def={t.defence:.2f} n={t.matches}")
    if len(fit.teams) >= 2:
        ids = sorted(fit.teams.keys())
        pred = predict_fixture(fit, ids[0], ids[1])
        print(f"{pred.home_team} vs {pred.away_team}: 1X2={pred.p_home:.0%}/{pred.p_draw:.0%}/{pred.p_away:.0%}")
