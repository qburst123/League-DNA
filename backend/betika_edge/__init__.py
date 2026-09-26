"""Betika Virtual Edge Analyzer — backend integration package.

Public surface (used by `backend.app` to wire the FastAPI routes)::

    from backend.betika_edge import edge
    edge.status()
    edge.teams()
    edge.matches(season=...)
    edge.markets_for(event_id=...)
    edge.no_vig(event_id=...)
    edge.h2h(home, away)
    edge.predict(home, away)
    edge.value_bets(event_id=...)
    edge.matchday(season=...)
    edge.ledger()
    edge.place_bet(event_id=..., stake=...)
    edge.settle_pending()
    edge.settings_get()
    edge.settings_set(key, value)
    edge.seed_demo_h2h(home, away, n=5)
    edge.refresh()

The package owns a thin SQLite at ``data/edge.sqlite`` with four
edge-only tables (`edge_markets_cache`, `edge_h2h`, `edge_ledger`,
`edge_settings`).  Reads go through the live collector's
``data/league.sqlite`` whenever it has a populated `matches` table;
otherwise the bundled fixtures under ``tests/fixtures/`` are used.
"""

from . import edge  # noqa: F401
