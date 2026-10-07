"""Chiusura automatica delle giocate leggendo i risultati delle partite."""
import json
import traceback

from . import credits, db, sources
from .fmt import esc, eur
from .markets import holds

H = 3600_000
FINISHED_AFTER = 2.5 * H  # una partita si considera finita 2 ore e mezza dopo l'inizio
ODDS_API_WINDOW = 3 * 24 * H  # /scores di The Odds API arriva a 3 giorni indietro
FD_WINDOW = 7 * 24 * H

OUTCOME_SEL = {"1": {"t": "1x2", "o": "1"}, "X": {"t": "1x2", "o": "X"}, "2": {"t": "1x2", "o": "2"}}


def signal_won(outcome, home, away):
    return holds(OUTCOME_SEL[outcome], home, away)


def _pending(con):
    rows = con.execute(
        """SELECT b.id, b.kind, b.odds, b.stake, g.event_id, g.outcome, q.legs FROM bets b
           LEFT JOIN signals g ON b.kind = 'signal' AND g.id = b.ref_id
           LEFT JOIN boosts q ON b.kind = 'boost' AND q.id = b.ref_id
           WHERE b.result IS NULL""").fetchall()
    out = []
    for b in rows:
        legs = ([{"eventId": b["event_id"], "sels": [OUTCOME_SEL[b["outcome"]]]}] if b["kind"] == "signal"
                else json.loads(b["legs"]))
        out.append({"id": b["id"], "odds": b["odds"], "stake": b["stake"], "legs": legs})
    return out


def _event_row(con, eid):
    r = con.execute("SELECT * FROM events WHERE id = ?", (eid,)).fetchone()
    return dict(r) if r else None


def _set_score(con, eid, home, away):
    con.execute("UPDATE events SET home_score = ?, away_score = ?, completed = 1 WHERE id = ?", (home, away, eid))
    con.commit()


def settle_bets(cfg, con, now):
    """Legge i risultati delle partite finite su cui hai giocate aperte e le chiude. Restituisce i messaggi."""
    bets = _pending(con)
    if not bets:
        return []
    ids = {l["eventId"] for b in bets for l in b["legs"]}
    evs = {eid: _event_row(con, eid) for eid in ids}

    # Fonte dei risultati: football-data.org (gratis) se c'è la chiave e copre il campionato,
    # altrimenti /scores di The Odds API (2 crediti, ultimi 3 giorni).
    use_fd = lambda sport: bool(cfg.football_data_key) and sport in sources.FD_CODES
    window = lambda sport: FD_WINDOW if use_fd(sport) else ODDS_API_WINDOW
    waiting = [e for e in evs.values() if e and not e["completed"] and FINISHED_AFTER < now - e["commence"] < window(e["sport_key"])]
    for sport in {e["sport_key"] for e in waiting}:
        last = con.execute("SELECT last_scores FROM leagues WHERE sport_key = ?", (sport,)).fetchone()
        if now - (last["last_scores"] if last else 0) < (1 if use_fd(sport) else 3) * H:
            continue
        try:
            if use_fd(sport):
                mine = [e for e in waiting if e["sport_key"] == sport]
                matches = sources.fetch_fd_matches(cfg, sources.FD_CODES[sport], min(e["commence"] for e in mine) - 24 * H, now)
                for e in mine:
                    m = sources.find_fd_match(db._ev(e), matches)
                    sc = m and sources.regulation_score(m)
                    if sc:
                        _set_score(con, e["id"], *sc)
            else:
                if credits.remaining(con) < 2:
                    continue
                scores, rem, used = sources.fetch_scores(cfg, sport)
                credits.record_usage(con, now, rem, used)
                for s in scores:
                    if s["completed"] and s["home"] is not None and s["away"] is not None:
                        _set_score(con, s["event_id"], s["home"], s["away"])
        except Exception:
            traceback.print_exc()
        con.execute("INSERT INTO leagues (sport_key, last_scores) VALUES (?, ?) ON CONFLICT(sport_key) DO UPDATE SET last_scores = excluded.last_scores",
                    (sport, now))
        con.commit()
    evs = {eid: _event_row(con, eid) for eid in ids}

    out = []
    line = lambda e: f"{esc(e['home'])} {e['home_score']}-{e['away_score']} {esc(e['away'])}"
    for b in bets:
        legs = [(l, evs.get(l["eventId"])) for l in b["legs"]]
        ok = lambda l, e: all(holds(s, e["home_score"], e["away_score"]) for s in l["sels"])
        if all(e and e["completed"] for _, e in legs):
            won = all(ok(l, e) for l, e in legs)
            con.execute("UPDATE bets SET result = ? WHERE id = ?", ("won" if won else "lost", b["id"]))
            con.commit()
            money = f"+{eur(b['stake'] * (b['odds'] - 1))}" if won else f"−{eur(b['stake'])}"
            out.append(f"📋 <b>B{b['id']} {'vinta ✅' if won else 'persa ❌'}</b> {money}\n" + ", ".join(line(e) for _, e in legs))
            continue
        # multipla con una partita già persa: inutile aspettare le altre
        lost = next((e for l, e in legs if e and e["completed"] and not ok(l, e)), None)
        if lost:
            con.execute("UPDATE bets SET result = 'lost' WHERE id = ?", (b["id"],))
            con.commit()
            out.append(f"📋 <b>B{b['id']} persa ❌</b> −{eur(b['stake'])}\n{line(lost)}")
            continue
        # risultato non trovato (partita rinviata o supplementari senza parziale): avviso una volta
        if any(e and not e["completed"] and now - e["commence"] > window(e["sport_key"]) for _, e in legs) and not db.kv_get(con, f"stale_bet:{b['id']}"):
            db.kv_set(con, f"stale_bet:{b['id']}", "1")
            out.append(f"⚠️ Non trovo il risultato di B{b['id']} (partita rinviata?). Chiudila a mano: <code>/esito B{b['id']} v|p|r</code>")
    if out:
        pnl = con.execute("SELECT COALESCE(SUM(CASE result WHEN 'won' THEN stake * (odds - 1) WHEN 'lost' THEN -stake ELSE 0 END), 0) AS p "
                          "FROM bets WHERE result IS NOT NULL").fetchone()["p"]
        out.append(f"Profitto totale registrato: <b>{eur(pnl)}</b>")
    return out
