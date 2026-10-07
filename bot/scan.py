"""Lavoro programmato (ogni 15 minuti): calendario, chiusure, risultati, scansioni e avvisi."""
import json
import traceback

from . import credits, db, sources
from .config import BOOK_NAMES
from .fair import kelly_stake
from .fmt import esc, eur, league, match, num, outcome_label, pct, when
from .pricing import h2h_from, price_leg, totals_from
from .settle import settle_bets
from .telegram import send

H = 3600_000
OUTCOMES = ("1", "X", "2")


def bankroll(cfg, con):
    base = float(db.kv_get(con, "bankroll") or cfg.default_bankroll)
    r = con.execute(
        "SELECT COALESCE(SUM(CASE result WHEN 'won' THEN stake * (odds - 1) WHEN 'lost' THEN -stake ELSE 0 END), 0) AS pnl "
        "FROM bets WHERE result IS NOT NULL").fetchone()
    return base + r["pnl"]


def _league_row(con, sport):
    r = con.execute("SELECT * FROM leagues WHERE sport_key = ?", (sport,)).fetchone()
    return dict(r) if r else {"last_events": 0, "last_scan": 0, "last_scores": 0}


def _set_league(con, sport, col, now):
    con.execute(f"INSERT INTO leagues (sport_key, {col}) VALUES (?, ?) ON CONFLICT(sport_key) DO UPDATE SET {col} = excluded.{col}", (sport, now))
    con.commit()


def run_scheduled(cfg, con, now):
    alerts = []

    # 1. calendario (gratuito), al massimo 4 campionati per giro
    stale = [l for l in cfg.leagues if now - _league_row(con, l)["last_events"] > 12 * H][:4]
    for l in stale:
        try:
            db.upsert_events(con, sources.fetch_events(cfg, l))
        except Exception:
            traceback.print_exc()
        _set_league(con, l, "last_events", now)

    # 2. chiusura di Pinnacle per segnali e giocate che iniziano entro 35 minuti.
    #    La lettura copre tutto il campionato, quindi vale anche come scansione.
    for _, a in capture_closing(cfg, con, now).items():
        alerts.extend(a)

    # 3. risultati delle partite finite con giocate aperte
    alerts.extend(settle_bets(cfg, con, now))

    # 4. scansioni, entro il budget giornaliero di crediti
    budget = credits.daily_budget(credits.remaining(con), cfg.credit_reserve, now)
    spent = credits.used_today(con, now)
    soon = con.execute(
        "SELECT sport_key, MIN(commence) AS first FROM events WHERE commence BETWEEN ? AND ? GROUP BY sport_key ORDER BY first",
        (now, now + 72 * H)).fetchall()
    scans = 0
    for r in soon:
        sport = r["sport_key"]
        if sport not in cfg.leagues:
            continue
        if scans >= 3 or spent >= budget:
            break
        if now - _league_row(con, sport)["last_scan"] < cfg.scan_every_h * H:
            continue
        try:
            alerts.extend(scan_league(cfg, con, sport, now)[0])
            spent += 1
            scans += 1
        except Exception:
            traceback.print_exc()

    if alerts:
        send(cfg, "\n\n".join(alerts))

    # 5. pulizia una volta al giorno
    day = str(now // 86400_000)
    if db.kv_get(con, "pruned") != day:
        db.prune(con, now)
        db.kv_set(con, "pruned", day)
    return alerts


def scan_league(cfg, con, sport, now, markets=("h2h",)):
    events, quotes, rem, used = sources.fetch_odds(cfg, sport, cfg.bookmakers, list(markets))
    credits.record_usage(con, now, rem, used)
    db.upsert_events(con, events)
    # i segnali si calcolano PRIMA di salvare le nuove quote: il confronto è con la lettura precedente
    out = []
    for ev in events:
        if now <= ev.commence <= now + 96 * H:
            out.extend(_analyze_event(cfg, con, ev, [q for q in quotes if q["event_id"] == ev.id], now))
    db.insert_quotes(con, now, quotes)
    _set_league(con, sport, "last_scan", now)
    return out, quotes


def _insert_signal(con, event_id, outcome, kind, book, price, fair_odds, min_odds, now):
    cur = con.execute(
        "INSERT INTO signals (event_id, outcome, kind, book, price, fair_odds, min_odds, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (event_id, outcome, kind, book, price, fair_odds, min_odds, now))
    con.commit()
    return cur.lastrowid


def _analyze_event(cfg, con, ev, quotes, now):
    pin = h2h_from([q for q in quotes if q["book"] == "pinnacle" and q["market"] == "h2h"])
    if not pin:
        return []
    opening = h2h_from(db.first_quotes(con, ev.id, "pinnacle", "h2h"))
    bank = bankroll(cfg, con)
    out = []
    for i, o in enumerate(OUTCOMES):
        p = pin["fair"][i]
        fair_odds = 1 / p
        if fair_odds > cfg.max_odds:
            continue
        min_odds = fair_odds * (1 + cfg.ev_min)

        # a) un book italiano paga sopra il prezzo giusto
        for book in cfg.soft_books:
            price = next((q["price"] for q in quotes if q["book"] == book and q["market"] == "h2h" and q["outcome"] == o), None)
            if not price or price < min_odds:
                continue
            prev = con.execute("SELECT MAX(price) AS p FROM signals WHERE event_id = ? AND outcome = ? AND kind = 'auto' AND book = ?",
                               (ev.id, o, book)).fetchone()["p"]
            if prev and prev >= price:
                continue
            sid = _insert_signal(con, ev.id, o, "auto", book, price, fair_odds, min_odds, now)
            stake = kelly_stake(p, price, bank)
            out.append(
                f"🟢 <b>OCCASIONE S{sid}</b> — {esc(BOOK_NAMES.get(book, book))} paga <b>{num(price)}</b>\n"
                f"{esc(match(ev))} · {esc(league(ev.sport_key))} · {when(ev.commence)}\n"
                f"Esito {esc(outcome_label(ev, o))} · prezzo giusto {num(fair_odds)} · vantaggio <b>{pct(price * p - 1)}</b>\n"
                f"Puntata: {eur(stake)} (¼ Kelly)\n"
                f"Se giochi: <code>/giocata S{sid} {num(price)} {num(stake)}</code>")

        # b) Pinnacle ha accorciato: i book italiani potrebbero essere rimasti indietro
        last_move = con.execute("SELECT fair_odds FROM signals WHERE event_id = ? AND outcome = ? AND kind = 'move' ORDER BY id DESC LIMIT 1",
                                (ev.id, o)).fetchone()
        ref = last_move["fair_odds"] if last_move else (1 / opening["fair"][i] if opening else None)
        if ref and fair_odds <= ref * (1 - cfg.move_min):
            sid = _insert_signal(con, ev.id, o, "move", None, None, fair_odds, min_odds, now)
            out.append(
                f"🔔 <b>PINNACLE HA MOSSO S{sid}</b>\n"
                f"{esc(match(ev))} · {esc(league(ev.sport_key))} · {when(ev.commence)}\n"
                f"Esito {esc(outcome_label(ev, o))}: prezzo giusto da {num(ref)} a {num(fair_odds)}\n"
                f"Controlla i book italiani: gioca solo se trovi <b>≥ {num(min_odds)}</b>\n"
                f"Se giochi: <code>/giocata S{sid} quota puntata</code>")
    return out


def capture_closing(cfg, con, now):
    """Registra la chiusura di Pinnacle per segnali e super quote giocate (serve al CLV).
    Finestra larga (1-35 minuti prima) perché lo scheduler può ritardare: ogni partita è letta una volta sola."""
    start, end = now + 60_000, now + 35 * 60_000
    by_sport = {}
    for r in con.execute(
            "SELECT DISTINCT e.id, e.sport_key FROM signals g JOIN events e ON e.id = g.event_id "
            "WHERE g.close_prob IS NULL AND e.commence >= ? AND e.commence < ?", (start, end)):
        by_sport.setdefault(r["sport_key"], {"ids": set(), "totals": False})["ids"].add(r["id"])
    played = con.execute("SELECT id, legs FROM boosts WHERE id IN (SELECT ref_id FROM bets WHERE kind = 'boost')").fetchall()
    boost_events = set()
    for b in played:
        for leg in json.loads(b["legs"]):
            if leg.get("closeProb") is not None:
                continue
            ev = db.get_event(con, leg["eventId"])
            if ev and start <= ev.commence < end:
                boost_events.add(ev.id)
                x = by_sport.setdefault(ev.sport_key, {"ids": set(), "totals": False})
                x["ids"].add(ev.id)
                x["totals"] = True

    done = {}
    for sport, x in by_sport.items():
        try:
            alerts, quotes = scan_league(cfg, con, sport, now, ("h2h", "totals") if x["totals"] else ("h2h",))
            done[sport] = alerts
            for eid in x["ids"]:
                q = [r for r in quotes if r["event_id"] == eid and r["book"] == "pinnacle"]
                h2h = h2h_from([r for r in q if r["market"] == "h2h"])
                if not h2h:
                    continue
                for i, o in enumerate(OUTCOMES):
                    con.execute("UPDATE signals SET close_prob = ? WHERE event_id = ? AND outcome = ? AND close_prob IS NULL",
                                (h2h["fair"][i], eid, o))
                con.commit()
                if eid in boost_events:
                    totals = totals_from([r for r in q if r["market"] == "totals"])
                    for b in played:
                        legs = json.loads(b["legs"])
                        changed = False
                        for leg in legs:
                            if leg["eventId"] == eid and leg.get("closeProb") is None:
                                leg["closeProb"] = price_leg(h2h, totals, leg["sels"]).prob
                                changed = True
                        if changed:
                            con.execute("UPDATE boosts SET legs = ? WHERE id = ?", (json.dumps(legs), b["id"]))
                            con.commit()
        except Exception:
            traceback.print_exc()
    return done
