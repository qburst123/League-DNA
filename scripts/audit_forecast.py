"""Independent audit of the live-board FT continuation ledger.

Recomputes every pick with a deliberately naive scan (no buckets, no caches),
checks the trim decision, the grading arithmetic and the reference boundary, and
proves that later seasons cannot influence an earlier target's ledger.
"""
import argparse
import copy
import hashlib
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.store import Store
from backend.forecast import season_ledger, cached_season_ledger, MIN_CONTEXT, DEFAULT_MINIMUM_MATCHES


def local_cards(rows):
    """Independent (season, team) -> 30 cards parser, written from the raw rows."""
    teams = {}
    for row in rows:
        if row["ft_home"] is None or row["ft_away"] is None or row["status"] != "final":
            continue
        for name, opponent in ((row["home"], row["away"]), (row["away"], row["home"])):
            cards = teams.setdefault((row["season"], name), [None] * 30)
            index = row["day"] - 1
            if cards[index] is not None:
                cards[index] = False
            else:
                cards[index] = {"ft": f"{row['ft_home']}:{row['ft_away']}", "match_id": row["id"], "opponent": opponent}
    return teams


def naive_outcomes(cards, context, team=None):
    """Plain scan of every window; returns {score: {'ids': set, 'traces': int, 'seasons': set}}."""
    length = len(context)
    outcomes = {}
    for (season, owner), values in cards.items():
        if team is not None and owner != team:
            continue
        for start in range(0, 31 - length):
            window = values[start:start + length]
            if any(not card or card["ft"] != context[i] for i, card in enumerate(window)):
                continue
            after = values[start + length] if start + length < 30 else None
            if not after:
                continue
            entry = outcomes.setdefault(after["ft"], {"ids": set(), "traces": 0, "seasons": set()})
            entry["ids"].add(after["match_id"])
            entry["traces"] += 1
            entry["seasons"].add(season)
    return outcomes


def naive_pick(cards, context, team=None, minimum=DEFAULT_MINIMUM_MATCHES, start=1):
    """Longest context first, drop the oldest day until the minimum is reached."""
    for trim in range(0, len(context) - MIN_CONTEXT + 1):
        segment = context[trim:]
        outcomes = naive_outcomes(cards, segment, team)
        following = sum(len(entry["ids"]) for entry in outcomes.values())
        ranked = sorted(outcomes.items(), key=lambda item: (-len(item[1]["ids"]), -item[1]["traces"],
                                                            tuple(int(part) for part in item[0].split(":"))))
        if ranked and len(ranked[0][1]["ids"]) >= minimum:
            score, entry = ranked[0]
            return {"score": score, "matches": len(entry["ids"]), "traces": entry["traces"],
                    "seasons": len(entry["seasons"]), "context_start": start + trim, "length": len(segment),
                    "trimmed": trim, "following_matches": following,
                    "longer_contexts_empty": all(
                        not (ranked := sorted(naive_outcomes(cards, context[step:], team).items(),
                                              key=lambda item: (-len(item[1]["ids"]), -item[1]["traces"],
                                                                tuple(int(part) for part in item[0].split(":")))))
                        or len(ranked[0][1]["ids"]) < minimum for step in range(0, trim))}
    return None


def audit(path):
    errors, checked = [], 0
    with tempfile.TemporaryDirectory(prefix="forecast-audit-") as folder:
        copy_path = Path(folder) / "source.sqlite"
        src = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
        dst = sqlite3.connect(copy_path)
        src.backup(dst)
        dst.close()
        src.close()
        store = Store(copy_path)
        volatile = ("computed_at", "compute_ms", "cached")
        strip = lambda payload: {key: value for key, value in payload.items() if key not in volatile}
        ledger = cached_season_ledger(store)
        fresh = season_ledger(store)
        if json.dumps(strip(ledger), sort_keys=True) != json.dumps(strip(fresh), sort_keys=True):
            errors.append({"kind": "cache mismatch"})
        if cached_season_ledger(store)["cached"] is not True:
            errors.append({"kind": "second call did not reuse the ledger"})
        season = ledger["season"]
        rows = store.all("SELECT * FROM matches ORDER BY season,day,id")
        references = local_cards([row for row in rows if row["season"] < season])
        target = local_cards([row for row in rows if row["season"] == season])
        stored = {(row["season"], row["home"], row["day"]): row for row in rows}
        for scope, block in ledger["scopes"].items():
            for view in block["teams"]:
                cards = target.get((season, view["team"]))
                if not cards:
                    errors.append({"kind": "team without cards", "team": view["team"]})
                    continue
                fts = [card["ft"] if card else None for card in cards]
                for row in view["ledger"]:
                    checked += 1
                    if row["day"] < 2:
                        continue
                    context = fts[:row["day"] - 1]
                    expected = naive_pick(references, context, view["team"] if scope == "same" else None) if len(context) >= MIN_CONTEXT else None
                    keys = ("score", "matches", "traces", "seasons", "context_start", "length", "trimmed", "following_matches")
                    audited = {key: expected[key] for key in keys} if expected else None
                    stored_pick = {key: row["pick"][key] for key in keys} if row["pick"] else None
                    if audited != stored_pick:
                        errors.append({"kind": "pick mismatch", "team": view["team"], "scope": scope, "day": row["day"],
                                       "audited": audited, "ledger": stored_pick})
                        continue
                    if expected and not expected["longer_contexts_empty"]:
                        errors.append({"kind": "trim jumped over usable evidence", "team": view["team"], "day": row["day"]})
                    if expected and expected["matches"] < DEFAULT_MINIMUM_MATCHES:
                        errors.append({"kind": "pick below the evidence minimum", "team": view["team"], "day": row["day"]})
                    actual = fts[row["day"] - 1] if row["day"] - 1 < 30 else None
                    if row["status"] == "pending":
                        if row["actual"] is not None or actual is not None:
                            errors.append({"kind": "pending day already has a recorded result", "team": view["team"], "day": row["day"]})
                        continue
                    if row["actual"] != actual:
                        errors.append({"kind": "graded row does not match the stored FT", "team": view["team"], "day": row["day"],
                                       "row": row["actual"], "stored": actual})
                    expected_status = "no_pick" if not expected else "hit" if expected["score"] == actual else "miss"
                    if row["status"] != expected_status:
                        errors.append({"kind": "verdict mismatch", "team": view["team"], "day": row["day"],
                                       "ledger": row["status"], "audited": expected_status})
        payload = fresh
        if not all(item < season for item in payload["reference_seasons"]):
            errors.append({"kind": "reference leakage into the target season or later"})
        if payload["no_target_or_future_reference_data"] is not True:
            errors.append({"kind": "reference flag not asserted"})
        for scope, block in payload["scopes"].items():
            picks = sum(view["summary"]["picks"] for view in block["teams"])
            hits = sum(view["summary"]["hits"] for view in block["teams"])
            misses = sum(view["summary"]["misses"] for view in block["teams"])
            if (picks, hits, misses) != (block["picks"], block["hits"], block["misses"]) or picks != hits + misses:
                errors.append({"kind": "summary arithmetic", "scope": scope, "team_totals": [picks, hits, misses],
                               "block": [block["picks"], block["hits"], block["misses"]]})
        # Later seasons can never influence an earlier target's ledger.
        earlier = sorted(season for season in {row["season"] for row in rows} if season < season)
        if earlier:
            with store.lock:
                store.db.execute("BEGIN IMMEDIATE")
                store.db.execute("UPDATE matches SET ft_home=ft_home+7,ft_away=ft_away+3 WHERE season>? AND status='final'", (season,))
                store.db.execute("COMMIT")
            store.fp_revision += 1
            mutated = season_ledger(store, season)
            if json.dumps(mutated["scopes"], sort_keys=True) != json.dumps(payload["scopes"], sort_keys=True):
                errors.append({"kind": "later seasons changed the ledger"})
        store.close()
    return {"at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "engine": fresh["engine"],
            "target_season": season, "prefix": fresh["prefix"], "rows_checked": checked,
            "scopes": {scope: {"picks": block["picks"], "hits": block["hits"], "no_pick": block["no_pick"]}
                       for scope, block in fresh["scopes"].items()},
            "data_origin": "Consistent copy of retained Betika results",
            "scope": "Earlier-season continuation only; later seasons proved inert", "errors": errors}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data/league.sqlite"))
    parser.add_argument("--output", default=str(ROOT / "artifacts/forecast-audit.json"))
    args = parser.parse_args()
    out = audit(args.db)
    Path(args.output).write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    raise SystemExit(1 if out["errors"] else 0)
