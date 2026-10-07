"""Fonti esterne: The Odds API (quote, calendario, risultati) e football-data.org (risultati gratis)."""
from datetime import datetime, timezone

import requests

from .teams import EventInfo, aliases_for, normalize

ODDS = "https://api.the-odds-api.com/v4"
TIMEOUT = 25


def _ms(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def _credits(r):
    rem, last = r.headers.get("x-requests-remaining"), r.headers.get("x-requests-last")
    return (int(float(rem)) if rem else None), (int(float(last)) if last else None)


def fetch_events(cfg, sport):
    """Calendario di un campionato. Non consuma crediti."""
    r = requests.get(f"{ODDS}/sports/{sport}/events", params={"apiKey": cfg.odds_api_key, "dateFormat": "iso"}, timeout=TIMEOUT)
    if r.status_code in (404, 422):
        return []
    r.raise_for_status()
    return [EventInfo(e["id"], e["sport_key"], e["home_team"], e["away_team"], _ms(e["commence_time"])) for e in r.json()]


def parse_odds(data):
    events, quotes = [], []
    for e in data:
        events.append(EventInfo(e["id"], e["sport_key"], e["home_team"], e["away_team"], _ms(e["commence_time"])))
        for b in e.get("bookmakers") or []:
            for m in b["markets"]:
                for o in m["outcomes"]:
                    if m["key"] == "h2h":
                        out = "1" if o["name"] == e["home_team"] else "2" if o["name"] == e["away_team"] else "X" if o["name"] == "Draw" else None
                        if out and o["price"] > 1:
                            quotes.append({"event_id": e["id"], "book": b["key"], "market": "h2h", "outcome": out, "point": None, "price": o["price"]})
                    elif m["key"] == "totals":
                        out = {"Over": "over", "Under": "under"}.get(o["name"])
                        if out and o["price"] > 1 and o.get("point") is not None:
                            quotes.append({"event_id": e["id"], "book": b["key"], "market": "totals", "outcome": out, "point": o["point"], "price": o["price"]})
    return events, quotes


def fetch_odds(cfg, sport, bookmakers, markets, event_ids=None):
    """Quote di un campionato. Costa 1 credito per mercato. Restituisce (eventi, quote, rimasti, usati)."""
    p = {"apiKey": cfg.odds_api_key, "bookmakers": ",".join(bookmakers), "markets": ",".join(markets),
         "oddsFormat": "decimal", "dateFormat": "iso"}
    if event_ids:
        p["eventIds"] = ",".join(event_ids)
    r = requests.get(f"{ODDS}/sports/{sport}/odds", params=p, timeout=TIMEOUT)
    rem, used = _credits(r)
    if r.status_code in (404, 422):
        return [], [], rem, used or 0
    if not r.ok:
        raise RuntimeError(f"odds {sport}: HTTP {r.status_code} {r.text[:200]}")
    ev, q = parse_odds(r.json())
    return ev, q, rem, used


def fetch_scores(cfg, sport):
    """Risultati degli ultimi 3 giorni da The Odds API. Costa 2 crediti."""
    r = requests.get(f"{ODDS}/sports/{sport}/scores", params={"apiKey": cfg.odds_api_key, "daysFrom": 3, "dateFormat": "iso"}, timeout=TIMEOUT)
    rem, used = _credits(r)
    r.raise_for_status()
    out = []
    for e in r.json():
        sc = {s["name"]: s["score"] for s in (e.get("scores") or [])}
        h, a = sc.get(e["home_team"]), sc.get(e["away_team"])
        out.append({"event_id": e["id"], "completed": e["completed"], "home": int(h) if h is not None else None, "away": int(a) if a is not None else None})
    return out, rem, used


# ---------------------------------------------------------------- football-data.org

# Campionati del piano gratuito di football-data.org
FD_CODES = {
    "soccer_italy_serie_a": "SA", "soccer_epl": "PL", "soccer_spain_la_liga": "PD", "soccer_germany_bundesliga": "BL1",
    "soccer_france_ligue_one": "FL1", "soccer_uefa_champs_league": "CL", "soccer_netherlands_eredivisie": "DED",
    "soccer_portugal_primeira_liga": "PPL", "soccer_efl_champ": "ELC",
}


def fetch_fd_matches(cfg, code, start_ms, end_ms):
    """Partite finite di una competizione tra due date. Gratuito (10 richieste al minuto)."""
    day = lambda ms: datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d")
    r = requests.get(f"https://api.football-data.org/v4/competitions/{code}/matches",
                     params={"status": "FINISHED", "dateFrom": day(start_ms), "dateTo": day(end_ms)},
                     headers={"X-Auth-Token": cfg.football_data_key}, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("matches") or []


def regulation_score(m):
    """Risultato valido per le scommesse (90 minuti). None se non determinabile."""
    if m.get("status") != "FINISHED":
        return None
    sc = m.get("score") or {}
    s = sc.get("fullTime") if sc.get("duration") in (None, "REGULAR") else sc.get("regularTime")
    if not s or s.get("home") is None or s.get("away") is None:
        return None
    return s["home"], s["away"]


_FD_STOP = {"fc", "ac", "as", "ss", "ssc", "us", "bc", "cf", "afc", "sc", "calcio", "club", "de", "the", "and", "milano",
            "1907", "1909", "1913", "1899"}


def _tokens(s):
    import re
    return {w for w in re.split(r"[\s.\-]+", normalize(s)) if len(w) > 1 and w not in _FD_STOP}


def name_score(odds_name, fd_team):
    a = _tokens(odds_name)
    for al in aliases_for(odds_name):
        a |= _tokens(al)
    best = 0.0
    for cand in (fd_team.get("name"), fd_team.get("shortName"), fd_team.get("tla")):
        if not cand:
            continue
        b = _tokens(cand)
        if b:
            best = max(best, len(b & a) / min(len(b), max(1, len(_tokens(odds_name)))))
    return best


def find_fd_match(ev, matches):
    """Stessa ora (±3 ore) e nomi di casa e trasferta più simili; None se non è chiaro."""
    best, best_score = None, 0.0
    for m in matches:
        if abs(_ms(m["utcDate"]) - ev.commence) > 3 * 3600_000:
            continue
        s = name_score(ev.home, m["homeTeam"]) + name_score(ev.away, m["awayTeam"])
        if s > best_score:
            best, best_score = m, s
    return best if best_score >= 0.5 else None
