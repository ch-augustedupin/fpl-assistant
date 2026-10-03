"""Thin client for the public Fantasy Premier League API (no login needed)."""
import json
import time
from pathlib import Path

import requests

BASE = "https://fantasy.premierleague.com/api/"
CACHE = Path(__file__).resolve().parent.parent / "cache"
HEADERS = {"User-Agent": "Mozilla/5.0 (fpl-assistant; personal use)"}

_session = requests.Session()
_session.headers.update(HEADERS)
_last_call = 0.0


def get(path, cache_key=None, min_interval=0.15):
    """GET an API path. With cache_key the JSON is stored in cache/ and reused."""
    global _last_call
    if cache_key:
        f = CACHE / f"{cache_key}.json"
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    wait = min_interval - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    for attempt in range(3):
        r = _session.get(BASE + path, timeout=30)
        _last_call = time.time()
        if r.status_code == 200:
            break
        if r.status_code in (429, 500, 502, 503) and attempt < 2:
            time.sleep(3 * (attempt + 1))
            continue
        raise RuntimeError(f"FPL API {path} -> HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    if cache_key:
        CACHE.mkdir(exist_ok=True)
        (CACHE / f"{cache_key}.json").write_text(json.dumps(data), encoding="utf-8")
    return data


def bootstrap():
    data = get("bootstrap-static/")
    for key in ("elements", "teams", "events", "element_types", "game_config"):
        if key not in data:
            raise RuntimeError(f"bootstrap-static is missing '{key}' — API changed?")
    return data


def fixtures():
    return get("fixtures/")


def current_gw(boot):
    """Last gameweek whose deadline has passed (0 before the season starts)."""
    cur = [e["id"] for e in boot["events"] if e["is_current"]]
    return cur[0] if cur else 0


def player_histories(boot, gw, progress=True):
    """element-summary for every player who has played this season (or last), cached per finished GW."""
    out = {}
    players = [p for p in boot["elements"] if p["minutes"] > 0 or p["status"] != "u"]
    todo = [p for p in players if not (CACHE / f"es_gw{gw}_{p['id']}.json").exists()]
    if progress and todo:
        print(f"Downloading history for {len(todo)} players (cached for GW{gw}, ~{len(todo) * 0.2 / 60:.0f} min)...")
    for i, p in enumerate(players):
        out[p["id"]] = get(f"element-summary/{p['id']}/", cache_key=f"es_gw{gw}_{p['id']}")
    if todo:
        for old in CACHE.glob("es_gw*_*.json"):
            if not old.name.startswith(f"es_gw{gw}_"):
                old.unlink()
    return out


def entry(tid):
    return get(f"entry/{tid}/")


def entry_history(tid):
    return get(f"entry/{tid}/history/")


def entry_picks(tid, gw):
    return get(f"entry/{tid}/event/{gw}/picks/")


def entry_transfers(tid):
    return get(f"entry/{tid}/transfers/")


def h2h_matches(league, gw):
    results, page = [], 1
    while True:
        d = get(f"leagues-h2h-matches/league/{league}/?event={gw}&page={page}")
        results += d["results"]
        if not d.get("has_next"):
            return results
        page += 1


def h2h_standings(league):
    return get(f"leagues-h2h/{league}/standings/")
