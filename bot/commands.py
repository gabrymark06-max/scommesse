"""Messaggi e comandi Telegram."""
import json
import re
import traceback

from . import credits, db, sources
from .fmt import esc, eur, league, match, num, outcome_label, parse_num, pct, when
from .markets import describe, joint_prob
from .model import MAX_GOALS, fit_model, over_prob
from .parser import parse_boost
from .pricing import evaluate_boost, h2h_from, price_leg, totals_from
from .scan import bankroll
from .settle import signal_won
from .telegram import send

H = 3600_000

HELP = """<b>Come usarmi</b>

<b>Super quota</b>: scrivimi il testo e la quota, per esempio
<code>Inter vince e over 2,5 @ 3,50</code>
<code>Inter, Napoli e Milan vincono @ 4.00</code>
<code>Roma-Lazio: gol + over 2.5 @ 2,90</code>
Ti rispondo SÌ / NO con il prezzo giusto e la puntata.
Capisco: 1 X 2, doppia chance, over/under (anche di una squadra), gol/nogol, risultato esatto, multigol, vittoria con N gol di scarto. Non posso valutare marcatori, angoli, cartellini, primo tempo.

<b>Probabilità</b>: scrivimi la partita, anche con una giocata, senza quota
<code>Inter-Juventus</code> → 1X2, over/under, gol/nogol, risultati esatti
<code>Inter vince e over 2,5</code> → probabilità e quota giusta di quella giocata
(oppure <code>/prob ...</code>)

<b>Comandi</b>
/listino [squadra o campionato] — quota minima da cercare per le prossime partite
/giocata S12 4,10 10 — registra una giocata su un segnale (quota, puntata)
/giocata Q5 10 — registra una super quota analizzata (puntata)
/esito B3 v|p|r — chiude a mano una giocata (di solito lo faccio io leggendo i risultati)
/bankroll 1000 — imposta il capitale
/stats — risultati e CLV
/crediti — crediti The Odds API rimasti"""


def handle_message(cfg, con, msg, now):
    chat_id = str(msg["chat"]["id"])
    text = (msg.get("text") or "").strip()
    if not cfg.telegram_chat_id:
        return send(cfg, f"Il tuo chat id è <code>{chat_id}</code>. Impostalo come TELEGRAM_CHAT_ID nel file .env.", chat_id)
    if chat_id != str(cfg.telegram_chat_id) or not text:
        return  # il bot risponde solo al proprietario
    parts = text.split()
    c = re.sub(r"@.*$", "", parts[0].lower())
    args = parts[1:]
    try:
        if c in ("/start", "/aiuto", "/help"):
            return send(cfg, HELP)
        if c == "/listino":
            return send(cfg, listino(cfg, con, " ".join(args), now))
        if c == "/giocata":
            return send(cfg, giocata(cfg, con, args, now))
        if c == "/esito":
            return send(cfg, esito(cfg, con, args))
        if c == "/bankroll":
            return send(cfg, set_bankroll(cfg, con, args[0] if args else None))
        if c == "/stats":
            return send(cfg, stats(cfg, con))
        if c == "/crediti":
            return send(cfg, f"Crediti The Odds API rimasti: <b>{credits.remaining(con)}</b> (si ricaricano ogni mese)")
        if c == "/prob":
            return send(cfg, analyze(cfg, con, " ".join(args), now, prob_only=True))
        if c.startswith("/") and c != "/sq":
            return send(cfg, "Comando sconosciuto. /aiuto per l'elenco.")
        return send(cfg, analyze(cfg, con, " ".join(args) if c == "/sq" else text, now))
    except Exception as e:
        traceback.print_exc()
        return send(cfg, f"Errore: {esc(str(e))}")


def _fresh_quotes(cfg, con, evs, now, with_totals):
    """Aggiorna le quote Pinnacle delle partite richieste se hanno più di 30 minuti."""
    by_sport = {}
    for ev in evs:
        h = db.latest_quotes(con, ev.id, "pinnacle", "h2h")
        t = db.latest_quotes(con, ev.id, "pinnacle", "totals") if with_totals else [{"ts": now}]
        oldest = min(h[0]["ts"] if h else 0, t[0]["ts"] if t else 0)
        if now - oldest > 0.5 * H:
            by_sport.setdefault(ev.sport_key, []).append(ev.id)
    for sport, ids in by_sport.items():
        markets = ["h2h", "totals"] if with_totals else ["h2h"]
        if credits.remaining(con) < len(markets):
            continue
        events, quotes, rem, used = sources.fetch_odds(cfg, sport, cfg.bookmakers, markets, ids)
        credits.record_usage(con, now, rem, used)
        db.upsert_events(con, events)
        db.insert_quotes(con, now, quotes)


def _pinnacle(con, ev):
    h = db.latest_quotes(con, ev.id, "pinnacle", "h2h")
    t = db.latest_quotes(con, ev.id, "pinnacle", "totals")
    return h2h_from(h), totals_from(t), (h[0]["ts"] if h else 0)


def _pc(p):
    return f"{num(p * 100, 1)}%"


def analyze(cfg, con, text, now, prob_only=False):
    """Super quota (testo con quota) oppure probabilità (testo senza quota)."""
    events = db.upcoming_events(con, now - 2 * H, now + 10 * 24 * H)
    p = parse_boost(text, events)
    not_found = "Non trovo la partita. Controlla i nomi delle squadre (campionati seguiti: " + ", ".join(league(l) for l in cfg.leagues) + ")."

    if p.unsupported:
        return ("⚪ <b>Non valutabile</b>\nContiene mercati che non posso prezzare con le quote di Pinnacle: "
                f"<i>{esc('; '.join(p.unsupported))}</i>\n(marcatori, angoli, cartellini, tempi, statistiche)")
    if p.unknown:
        return f"Non ho capito questa parte: <i>{esc('; '.join(p.unknown))}</i>\nRiscrivila in modo più semplice (es. \"Inter vince\", \"over 2,5\", \"gol\")."
    if prob_only or p.odds is None or p.odds <= 1:
        return probabilities(cfg, con, p, events, now)
    if not p.legs:
        return "Manca la giocata. Per esempio: <code>Inter vince e over 2,5 @ 3,50</code>" if p.events else not_found
    if p.lone_teams:
        return "Una squadra è citata senza giocata. Riscrivila, per esempio: <code>Inter vince e Milan vince @ 3,20</code>"

    evs = [next((e for e in events if e.id == eid), None) or db.get_event(con, eid) for eid in p.legs]
    evs = [e for e in evs if e]
    need_totals = any(len(s) > 1 or any(x["t"] not in ("1x2", "dc") for x in s) for s in p.legs.values())
    _fresh_quotes(cfg, con, evs, now, need_totals)

    legs = []
    for ev in evs:
        h2h, totals, _ = _pinnacle(con, ev)
        if not h2h:
            return f"Pinnacle non quota ancora {esc(match(ev))}: non posso calcolare il prezzo giusto."
        sels = p.legs[ev.id]
        legs.append((ev, sels, price_leg(h2h, totals, sels)))

    bank = bankroll(cfg, con)
    r = evaluate_boost(p.odds, [(lp, sels) for _, sels, lp in legs], cfg.boost_min_direct, cfg.boost_min_model, bank)
    cur = con.execute(
        "INSERT INTO boosts (text, odds, prob, ev, verdict, legs, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (text, p.odds, r.prob, r.ev, r.verdict, json.dumps([{"eventId": ev.id, "sels": sels, "prob": lp.prob} for ev, sels, lp in legs]), now))
    con.commit()
    qid = cur.lastrowid

    head = {"SI": "✅ <b>SÌ, conviene giocarla</b>", "MARGINALE": "🟡 <b>Marginale: non abbastanza per giocarla</b>",
            "NO": "❌ <b>NO, non conviene</b>"}[r.verdict]
    lines = [
        f"{head}  (Q{qid})",
        f"Super quota <b>{num(p.odds)}</b> · prezzo giusto <b>{num(r.fair_odds)}</b> (probabilità {_pc(r.prob)})",
        f"Vantaggio: <b>{pct(r.ev)}</b> (soglia {pct(r.threshold, 0)})",
    ]
    if r.verdict == "SI":
        lines.append(f"Puntata: <b>{eur(r.stake)}</b> (¼ Kelly su {eur(bank)}). Se la super quota ha una puntata massima più bassa, gioca il massimo consentito.")
    lines.append("")
    for ev, sels, lp in legs:
        lines.append(f"• {esc(match(ev))} ({when(ev.commence)}): {esc(' + '.join(describe(x, ev.home, ev.away) for x in sels))} → "
                     f"{_pc(lp.prob)}{'' if lp.direct else ' [modello]'}")
    if r.model_used:
        lines += ["", "⚠️ Pinnacle non ha l'over/under di questa partita: gol attesi stimati con valori medi, soglia alzata."
                  if r.weak_model else "Combinazione calcolata con il modello dei gol tarato sulle quote di Pinnacle (1X2 + over/under)."]
    if r.verdict == "SI":
        lines += ["", f"Se la giochi: <code>/giocata Q{qid} {num(r.stake)}</code>"]
    return "\n".join(lines)


def probabilities(cfg, con, p, events, now):
    ids = list(p.legs) if p.legs else p.events[:3]
    if not ids:
        return "Non trovo la partita. Scrivi le squadre, per esempio <code>Inter-Juventus</code> oppure <code>Inter vince e over 2,5</code>."
    evs = [e for e in (next((x for x in events if x.id == i), None) or db.get_event(con, i) for i in ids) if e]
    _fresh_quotes(cfg, con, evs, now, True)

    # una giocata precisa: probabilità e quota giusta
    if p.legs:
        lines, prob, model = [], 1.0, False
        for ev in evs:
            h2h, totals, _ = _pinnacle(con, ev)
            if not h2h:
                return f"Pinnacle non quota ancora {esc(match(ev))}."
            sels = p.legs[ev.id]
            lp = price_leg(h2h, totals, sels)
            prob *= lp.prob
            model = model or not lp.direct
            lines.append(f"• {esc(match(ev))} ({when(ev.commence)}): {esc(' + '.join(describe(x, ev.home, ev.away) for x in sels))} → "
                         f"<b>{_pc(lp.prob)}</b>{'' if lp.direct else ' [modello]'}")
        threshold = cfg.boost_min_model if model else cfg.boost_min_direct
        out = [f"🎯 Probabilità <b>{_pc(prob)}</b> · quota giusta <b>{num(1 / prob)}</b>",
               f"Conviene giocarla da <b>{num(1 / prob * (1 + threshold))}</b> in su", "", *lines]
        if model:
            out += ["", "Calcolata con il modello dei gol tarato su Pinnacle (1X2 + over/under)."]
        return "\n".join(out)

    # solo la partita: scheda completa
    blocks = []
    for ev in evs:
        h2h, totals, ts = _pinnacle(con, ev)
        if not h2h:
            blocks.append(f"<b>{esc(match(ev))}</b>: Pinnacle non la quota ancora.")
            continue
        p1, px, p2 = h2h["fair"]
        m = fit_model(p1, px, p2, totals=totals)
        scores = sorted(((f"{h}-{a}", m.matrix[h][a]) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)), key=lambda x: -x[1])
        ou = []
        for l in (1.5, 2.5, 3.5):
            po = totals[1] if totals and totals[0] == l else over_prob(m.matrix, l)
            ou.append(f"   {num(l, 1)}: over {_pc(po)} · under {_pc(1 - po)}")
        btts = joint_prob(m.matrix, [{"t": "btts", "yes": True}])
        age = max(0, round((now - ts) / 60_000))
        b = [
            f"<b>{esc(match(ev))}</b> · {esc(league(ev.sport_key))} · {when(ev.commence)}",
            f"<i>quote Pinnacle di {age} min fa, senza margine</i>",
            f"1 <b>{_pc(p1)}</b> ({num(1 / p1)}) · X <b>{_pc(px)}</b> ({num(1 / px)}) · 2 <b>{_pc(p2)}</b> ({num(1 / p2)})",
            f"1X {_pc(p1 + px)} · X2 {_pc(px + p2)} · 12 {_pc(p1 + p2)}",
            f"Gol attesi: {esc(ev.home)} {num(m.lambda_home)} · {esc(ev.away)} {num(m.lambda_away)}",
            "Over/under:", *ou,
            f"Gol {_pc(btts)} · Nogol {_pc(1 - btts)}",
            "Risultati più probabili: " + " · ".join(f"{k} {_pc(v)}" for k, v in scores[:5]),
        ]
        if not totals:
            b.append("<i>Pinnacle non ha l'over/under: gol stimati con valori medi.</i>")
        blocks.append("\n".join(b))
    blocks.append("<i>Tra parentesi la quota giusta. Over/under, gol e risultati esatti vengono dal modello dei gol tarato su Pinnacle.</i>")
    return "\n\n".join(blocks)


def listino(cfg, con, filt, now):
    f = filt.lower()
    groups = {}
    for ev in db.upcoming_events(con, now, now + 72 * H):
        name = league(ev.sport_key)
        if f and f not in f"{ev.home} {ev.away} {name}".lower():
            continue
        q = db.latest_quotes(con, ev.id, "pinnacle", "h2h")
        h2h = h2h_from(q)
        if not h2h:
            continue
        mins = []
        for i, pr in enumerate(h2h["fair"]):
            fair = 1 / pr
            mins.append(f"{'1X2'[i]} –" if fair > cfg.max_odds else f"{'1X2'[i]} ≥{num(fair * (1 + cfg.ev_min))}")
        age = round((now - q[0]["ts"]) / H)
        groups.setdefault(name, []).append(f"{when(ev.commence)} <b>{esc(match(ev))}</b>\n   {' · '.join(mins)}  <i>(Pinnacle {age}h fa)</i>")
    if not groups:
        return "Nessuna partita con quote Pinnacle nelle prossime 72 ore" + (" per questo filtro." if f else ".")
    out = [f"<b>Quote minime da cercare</b> (prezzo giusto +{num(cfg.ev_min * 100, 0)}%)"]
    out += [f"<b>{esc(n)}</b>\n" + "\n".join(l) for n, l in groups.items()]
    return "\n\n".join(out)


def giocata(cfg, con, args, now):
    m = re.match(r"^([SQ])(\d+)$", (args[0] if args else "").upper())
    if not m:
        return "Uso: <code>/giocata S12 4,10 10</code> (segnale, quota, puntata) oppure <code>/giocata Q5 10</code> (super quota, puntata)"
    rid = int(m.group(2))
    if m.group(1) == "S":
        sig = con.execute("SELECT * FROM signals WHERE id = ?", (rid,)).fetchone()
        if not sig:
            return f"Segnale S{rid} non trovato."
        odds = parse_num(args[1] if len(args) > 1 else None)
        stake = parse_num(args[2] if len(args) > 2 else None)
        if not odds or not stake:
            return "Indica quota e puntata: <code>/giocata S12 4,10 10</code>"
        cur = con.execute("INSERT INTO bets (kind, ref_id, odds, stake, created) VALUES ('signal', ?, ?, ?, ?)", (rid, odds, stake, now))
        con.commit()
        ev = db.get_event(con, sig["event_id"])
        warn = "\n⚠️ Quota sotto il prezzo giusto: questa giocata ha valore negativo." if odds < sig["fair_odds"] else ""
        what = f"{match(ev)} · {outcome_label(ev, sig['outcome'])}" if ev else ""
        return (f"Registrata <b>B{cur.lastrowid}</b>: {esc(what)} a {num(odds)}, {eur(stake)}.{warn}\n"
                "Il risultato lo leggo io a fine partita.")
    boost = con.execute("SELECT * FROM boosts WHERE id = ?", (rid,)).fetchone()
    if not boost:
        return f"Super quota Q{rid} non trovata."
    a1 = parse_num(args[1] if len(args) > 1 else None)
    a2 = parse_num(args[2] if len(args) > 2 else None)
    odds, stake = (a1, a2) if a2 is not None else (boost["odds"], a1)
    if not stake:
        return "Indica la puntata: <code>/giocata Q5 10</code>"
    cur = con.execute("INSERT INTO bets (kind, ref_id, odds, stake, created) VALUES ('boost', ?, ?, ?, ?)", (rid, odds, stake, now))
    con.commit()
    return f"Registrata <b>B{cur.lastrowid}</b>: super quota \"{esc(boost['text'])}\" a {num(odds)}, {eur(stake)}.\nIl risultato lo leggo io a fine partita."


def esito(cfg, con, args):
    m = re.match(r"^B(\d+)$", (args[0] if args else "").upper())
    r = {"v": "won", "p": "lost", "r": "void"}.get((args[1] if len(args) > 1 else " ")[0].lower())
    if not m or not r:
        return "Uso: <code>/esito B3 v</code> (v = vinta, p = persa, r = rimborsata)"
    cur = con.execute("UPDATE bets SET result = ? WHERE id = ?", (r, int(m.group(1))))
    con.commit()
    if not cur.rowcount:
        return f"Giocata B{m.group(1)} non trovata."
    label = {"won": "vinta ✅", "lost": "persa ❌", "void": "rimborsata"}[r]
    return f"B{m.group(1)}: {label}. Bankroll attuale {eur(bankroll(cfg, con))}."


def set_bankroll(cfg, con, v):
    x = parse_num(v)
    if not x or x <= 0:
        return f"Bankroll attuale: <b>{eur(bankroll(cfg, con))}</b>. Per cambiarlo: <code>/bankroll 1000</code>"
    # il bankroll salvato è la base: i risultati registrati vengono sommati sopra
    pnl = bankroll(cfg, con) - float(db.kv_get(con, "bankroll") or cfg.default_bankroll)
    db.kv_set(con, "bankroll", x - pnl)
    return f"Bankroll impostato a <b>{eur(x)}</b>."


def stats(cfg, con):
    sig = con.execute(
        """SELECT kind, COUNT(*) AS n, AVG(CASE WHEN close_prob IS NOT NULL THEN COALESCE(price, min_odds) * close_prob - 1 END) AS clv,
                  SUM(close_prob IS NOT NULL) AS nc FROM signals GROUP BY kind""").fetchall()
    # rendimento teorico dei segnali, dai risultati già letti (nessun credito in più)
    theo = {}
    for g in con.execute("SELECT g.kind, g.outcome, COALESCE(g.price, g.min_odds) AS odds, e.home_score, e.away_score "
                         "FROM signals g JOIN events e ON e.id = g.event_id WHERE e.completed = 1"):
        t = theo.setdefault(g["kind"], [0, 0.0])
        t[0] += 1
        t[1] += g["odds"] - 1 if signal_won(g["outcome"], g["home_score"], g["away_score"]) else -1
    bets = con.execute(
        """SELECT b.*, g.close_prob AS sclose, q.legs AS legs FROM bets b
           LEFT JOIN signals g ON b.kind = 'signal' AND g.id = b.ref_id
           LEFT JOIN boosts q ON b.kind = 'boost' AND q.id = b.ref_id""").fetchall()
    staked = pnl = 0.0
    settled = 0
    clvs = []
    for b in bets:
        if b["result"]:
            settled += 1
            if b["result"] != "void":
                staked += b["stake"]
            pnl += b["stake"] * (b["odds"] - 1) if b["result"] == "won" else -b["stake"] if b["result"] == "lost" else 0
        close = b["sclose"]
        if b["kind"] == "boost" and b["legs"]:
            legs = json.loads(b["legs"])
            close = None
            if all(l.get("closeProb") is not None for l in legs):
                close = 1.0
                for l in legs:
                    close *= l["closeProb"]
        if close:
            clvs.append(b["odds"] * close - 1)
    lines = ["<b>Statistiche</b>", ""]
    for k in sig:
        s = f"Segnali {'automatici' if k['kind'] == 'auto' else 'Pinnacle ha mosso'}: {k['n']}"
        if k["nc"]:
            s += f" · CLV medio {pct(k['clv'] or 0)} su {k['nc']}"
        if k["kind"] in theo:
            n, pl = theo[k["kind"]]
            s += f" · rendimento se giocati tutti: {pct(pl / n)} su {n}"
        lines.append(s)
    lines += [
        "",
        f"Giocate: {len(bets)} (chiuse {settled})",
        f"Profitto: <b>{eur(pnl)}</b>" + (f" · ROI {pct(pnl / staked)}" if staked else ""),
        f"CLV medio delle tue giocate: <b>{pct(sum(clvs) / len(clvs))}</b> su {len(clvs)}" if clvs else "CLV: ancora nessuna giocata arrivata alla chiusura",
        f"Bankroll: {eur(bankroll(cfg, con))}",
        "",
        "<i>Il CLV è l'indicatore che conta: sopra +2% stabile = la falla c'è. Il profitto su poche giocate è soprattutto fortuna.</i>",
    ]
    return "\n".join(lines)
