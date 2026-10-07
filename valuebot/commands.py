"""Messaggi e comandi Telegram."""
import json
import re
import traceback

from . import credits, db, sources
from .fmt import SEP, bar, esc, eur, head, league, match, num, outcome_label, parse_num, pct, short, table, when
from .markets import describe, joint_prob
from .model import MAX_GOALS, fit_model, over_prob
from .parser import parse_boost
from .pricing import evaluate_boost, h2h_from, price_leg, totals_from
from .scan import bankroll
from .settle import signal_won
from .telegram import send

H = 3600_000

HELP = f"""🤖 <b>COME USARMI</b>
{SEP}
💥 <b>Super quota</b> — scrivi il testo con la quota
<code>Inter vince e over 2,5 @ 3,50</code>
<code>Inter, Napoli e Milan vincono @ 4.00</code>
<code>Roma-Lazio: gol + over 2.5 @ 2,90</code>
→ ti dico se conviene, il prezzo giusto e quanto puntare

🎯 <b>Probabilità</b> — scrivi senza quota
<code>Como-Roma</code> → scheda completa della partita
<code>Inter vince e over 2,5</code> → probabilità di quella giocata

<i>Capisco: 1X2, doppia chance, over/under (anche di una squadra), gol/nogol, risultato esatto, multigol, vittoria con scarto. Non valuto marcatori, angoli, cartellini, primo tempo.</i>

📋 <b>Comandi</b>
/listino — quote minime da cercare (anche <code>/listino inter</code>)
/giocata — registra una giocata: <code>/giocata S12 4,10 10</code> · <code>/giocata Q5 10</code>
/stats — risultati, profitto e CLV
/bankroll — vedi o imposta il capitale
/crediti — crediti The Odds API rimasti
/esito — chiudi a mano una giocata (di solito lo faccio io)"""


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
            return send(cfg, f"🔋 <b>Crediti The Odds API:</b> {credits.remaining(con)} su 500\n<i>Si ricaricano ogni mese.</i>")
        if c == "/prob":
            return send(cfg, analyze(cfg, con, " ".join(args), now, prob_only=True))
        if c.startswith("/") and c != "/sq":
            return send(cfg, "Comando sconosciuto. Scrivi /aiuto per l'elenco.")
        return send(cfg, analyze(cfg, con, " ".join(args) if c == "/sq" else text, now))
    except Exception as e:
        traceback.print_exc()
        return send(cfg, f"⚠️ Errore: {esc(str(e))}")


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


def _leg_line(ev, sels, lp, bold=False):
    what = esc(" + ".join(describe(x, ev.home, ev.away) for x in sels))
    prob = f"<b>{_pc(lp.prob)}</b>" if bold else _pc(lp.prob)
    return f"• <b>{esc(match(ev))}</b> · {when(ev.commence)}\n   {what} → {prob}{'' if lp.direct else ' 🧮'}"


def analyze(cfg, con, text, now, prob_only=False):
    """Super quota (testo con quota) oppure probabilità (testo senza quota)."""
    events = db.upcoming_events(con, now - 2 * H, now + 10 * 24 * H)
    p = parse_boost(text, events)
    not_found = ("🔍 Non trovo la partita. Controlla i nomi delle squadre.\n<i>Campionati seguiti: "
                 + ", ".join(league(l) for l in cfg.leagues) + "</i>")

    if p.unsupported:
        return ("⚪ <b>NON VALUTABILE</b>\n" + SEP + "\nContiene mercati che Pinnacle non quota:\n"
                f"<i>{esc('; '.join(p.unsupported))}</i>\n\nPosso valutare solo giocate sul risultato e sui gol "
                "(niente marcatori, angoli, cartellini, tempi).")
    if p.unknown:
        return (f"🤔 Non ho capito: <i>{esc('; '.join(p.unknown))}</i>\n"
                "Riscrivilo più semplice, per esempio <code>Inter vince</code>, <code>over 2,5</code>, <code>gol</code>.")
    if prob_only or p.odds is None or p.odds <= 1:
        return probabilities(cfg, con, p, events, now)
    if not p.legs:
        return "Manca la giocata. Per esempio: <code>Inter vince e over 2,5 @ 3,50</code>" if p.events else not_found
    if p.lone_teams:
        return "Una squadra è citata senza giocata. Per esempio: <code>Inter vince e Milan vince @ 3,20</code>"

    evs = [next((e for e in events if e.id == eid), None) or db.get_event(con, eid) for eid in p.legs]
    evs = [e for e in evs if e]
    need_totals = any(len(s) > 1 or any(x["t"] not in ("1x2", "dc") for x in s) for s in p.legs.values())
    _fresh_quotes(cfg, con, evs, now, need_totals)

    legs = []
    for ev in evs:
        h2h, totals, _ = _pinnacle(con, ev)
        if not h2h:
            return f"⏳ Pinnacle non quota ancora {esc(match(ev))}: non posso calcolare il prezzo giusto."
        sels = p.legs[ev.id]
        legs.append((ev, sels, price_leg(h2h, totals, sels)))

    bank = bankroll(cfg, con)
    r = evaluate_boost(p.odds, [(lp, sels) for _, sels, lp in legs], cfg.boost_min_direct, cfg.boost_min_model, bank)
    cur = con.execute(
        "INSERT INTO boosts (text, odds, prob, ev, verdict, legs, created) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (text, p.odds, r.prob, r.ev, r.verdict, json.dumps([{"eventId": ev.id, "sels": sels, "prob": lp.prob} for ev, sels, lp in legs]), now))
    con.commit()
    qid = cur.lastrowid

    title = {"SI": "✅ <b>SUPER QUOTA: GIOCALA</b>", "MARGINALE": "🟡 <b>SUPER QUOTA: LASCIA PERDERE</b>",
             "NO": "❌ <b>SUPER QUOTA: NON CONVIENE</b>"}[r.verdict]
    sub = {"SI": "Paga più di quanto vale davvero.", "MARGINALE": "Vantaggio troppo piccolo per essere sicuro.",
           "NO": "Paga meno di quanto vale: il banco è avanti."}[r.verdict]
    lines = [
        f"{title}  <i>Q{qid}</i>",
        f"<i>{sub}</i>",
        SEP,
        table([["Quota offerta", num(p.odds)], ["Quota giusta", num(r.fair_odds)], ["Probabilità", _pc(r.prob)],
               ["Vantaggio", pct(r.ev)], ["Soglia minima", pct(r.threshold, 0)]], "lr"),
    ]
    if r.verdict == "SI":
        lines.append(f"💶 <b>Punta {eur(r.stake)}</b>  <i>(¼ Kelly su {eur(bank)})</i>\n"
                     "<i>Se la super quota ha un massimo più basso, gioca il massimo.</i>")
    lines += ["", "📋 <b>Giocata</b>", *(_leg_line(ev, sels, lp) for ev, sels, lp in legs)]
    if r.model_used:
        lines += ["", "<i>🧮 Pinnacle non ha l'over/under di questa partita: gol stimati con valori medi, soglia alzata.</i>"
                  if r.weak_model else "<i>🧮 combinazione calcolata col modello dei gol tarato su Pinnacle</i>"]
    if r.verdict == "SI":
        lines += ["", f"➡️ Se la giochi: <code>/giocata Q{qid} {num(r.stake)}</code>"]
    return "\n".join(lines)


def probabilities(cfg, con, p, events, now):
    ids = list(p.legs) if p.legs else p.events[:3]
    if not ids:
        return "🔍 Non trovo la partita. Scrivi le squadre, per esempio <code>Como-Roma</code> oppure <code>Inter vince e over 2,5</code>."
    evs = [e for e in (next((x for x in events if x.id == i), None) or db.get_event(con, i) for i in ids) if e]
    _fresh_quotes(cfg, con, evs, now, True)

    # una giocata precisa: probabilità e quota giusta
    if p.legs:
        lines, prob, model = [], 1.0, False
        for ev in evs:
            h2h, totals, _ = _pinnacle(con, ev)
            if not h2h:
                return f"⏳ Pinnacle non quota ancora {esc(match(ev))}."
            sels = p.legs[ev.id]
            lp = price_leg(h2h, totals, sels)
            prob *= lp.prob
            model = model or not lp.direct
            lines.append(_leg_line(ev, sels, lp, bold=True))
        threshold = cfg.boost_min_model if model else cfg.boost_min_direct
        out = ["🎯 <b>PROBABILITÀ DELLA GIOCATA</b>", SEP,
               table([["Probabilità", _pc(prob)], ["", bar(prob, 12)], ["Quota giusta", num(1 / prob)],
                      ["Conviene da", num(1 / prob * (1 + threshold))]], "lr"),
               "", *lines]
        if model:
            out += ["", "<i>🧮 calcolata col modello dei gol tarato su Pinnacle</i>"]
        return "\n".join(out)

    # solo la partita: scheda completa
    blocks = []
    for ev in evs:
        h2h, totals, ts = _pinnacle(con, ev)
        if not h2h:
            blocks.append(f"{head(ev)}\n⏳ Pinnacle non la quota ancora.")
            continue
        p1, px, p2 = h2h["fair"]
        m = fit_model(p1, px, p2, totals=totals)
        scores = sorted(((f"{h}-{a}", m.matrix[h][a]) for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1)), key=lambda x: -x[1])
        btts = joint_prob(m.matrix, [{"t": "btts", "yes": True}])
        ou = []
        for l in (1.5, 2.5, 3.5):
            po = totals[1] if totals and totals[0] == l else over_prob(m.matrix, l)
            ou.append([num(l, 1), _pc(po), _pc(1 - po)])
        age = max(0, round((now - ts) / 60_000))
        b = [
            head(ev),
            SEP,
            "🏁 <b>Esito finale</b>  <i>(quota giusta)</i>",
            table([["1", short(ev.home), bar(p1), _pc(p1), num(1 / p1)],
                   ["X", "Pareggio", bar(px), _pc(px), num(1 / px)],
                   ["2", short(ev.away), bar(p2), _pc(p2), num(1 / p2)]], "lllrr"),
            f"🔀 <b>Doppia chance</b>  1X {_pc(p1 + px)} · X2 {_pc(px + p2)} · 12 {_pc(p1 + p2)}",
            "",
            f"⚡ <b>Gol attesi</b>  {esc(ev.home)} <b>{num(m.lambda_home)}</b> · {esc(ev.away)} <b>{num(m.lambda_away)}</b>",
            table([["Linea", "Over", "Under"], *ou], "lrr"),
            f"🥅 <b>Gol</b> {_pc(btts)}  ·  <b>NoGol</b> {_pc(1 - btts)}",
            "",
            "🔢 <b>Risultati più probabili</b>",
            table([[k, bar(v / scores[0][1] * 0.8, 8), _pc(v)] for k, v in scores[:5]], "llr"),
        ]
        if not totals:
            b.append("<i>⚠️ Pinnacle non ha l'over/under: gol stimati con valori medi.</i>")
        b.append(f"<i>📡 Pinnacle senza margine · aggiornate {age} min fa\n🧮 gol, over/under e risultati dal modello tarato su Pinnacle</i>")
        blocks.append("\n".join(b))
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
            mins.append(f"{'1X2'[i]} " + ("  –  " if fair > cfg.max_odds else f"≥{num(fair * (1 + cfg.ev_min))}"))
        groups.setdefault(name, []).append(f"🗓 {when(ev.commence)}\n<b>{esc(match(ev))}</b>\n<code>{esc('   '.join(mins))}</code>")
    if not groups:
        return "📭 Nessuna partita con quote Pinnacle nelle prossime 72 ore" + (" per questo filtro." if f else ".")
    out = [f"📋 <b>QUOTE MINIME DA CERCARE</b>\n<i>Gioca solo se un book italiano paga almeno così (prezzo giusto +{num(cfg.ev_min * 100, 0)}%).</i>"]
    out += [f"🏆 <b>{esc(n)}</b>\n{SEP}\n" + "\n\n".join(l) for n, l in groups.items()]
    return "\n\n".join(out)


def giocata(cfg, con, args, now):
    m = re.match(r"^([SQ])(\d+)$", (args[0] if args else "").upper())
    if not m:
        return "Uso: <code>/giocata S12 4,10 10</code> (segnale, quota, puntata)\noppure <code>/giocata Q5 10</code> (super quota, puntata)"
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
        warn = "\n⚠️ <b>Quota sotto il prezzo giusto</b>: questa giocata ha valore negativo." if odds < sig["fair_odds"] else ""
        what = f"\n{head(ev)}\n🎯 {esc(outcome_label(ev, sig['outcome']))}" if ev else ""
        return (f"📝 <b>Giocata registrata · B{cur.lastrowid}</b>{what}\n"
                + table([["Quota", num(odds)], ["Puntata", eur(stake)], ["Vincita", eur(stake * odds)]], "lr")
                + f"{warn}\n<i>Il risultato lo leggo io a fine partita.</i>")
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
    return (f"📝 <b>Giocata registrata · B{cur.lastrowid}</b>\n💥 {esc(boost['text'])}\n"
            + table([["Quota", num(odds)], ["Puntata", eur(stake)], ["Vincita", eur(stake * odds)]], "lr")
            + "\n<i>Il risultato lo leggo io a fine partita.</i>")


def esito(cfg, con, args):
    m = re.match(r"^B(\d+)$", (args[0] if args else "").upper())
    r = {"v": "won", "p": "lost", "r": "void"}.get((args[1] if len(args) > 1 else " ")[0].lower())
    if not m or not r:
        return "Uso: <code>/esito B3 v</code> (v = vinta, p = persa, r = rimborsata)"
    cur = con.execute("UPDATE bets SET result = ? WHERE id = ?", (r, int(m.group(1))))
    con.commit()
    if not cur.rowcount:
        return f"Giocata B{m.group(1)} non trovata."
    label = {"won": "✅ vinta", "lost": "❌ persa", "void": "↩️ rimborsata"}[r]
    return f"<b>B{m.group(1)}</b> {label}\n💰 Bankroll: <b>{eur(bankroll(cfg, con))}</b>"


def set_bankroll(cfg, con, v):
    x = parse_num(v)
    if not x or x <= 0:
        return f"💰 Bankroll: <b>{eur(bankroll(cfg, con))}</b>\nPer cambiarlo: <code>/bankroll 1000</code>"
    # il bankroll salvato è la base: i risultati registrati vengono sommati sopra
    pnl = bankroll(cfg, con) - float(db.kv_get(con, "bankroll") or cfg.default_bankroll)
    db.kv_set(con, "bankroll", x - pnl)
    return f"💰 Bankroll impostato a <b>{eur(x)}</b>"


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
    settled = won = 0
    clvs = []
    for b in bets:
        if b["result"]:
            settled += 1
            won += b["result"] == "won"
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

    lines = ["📊 <b>STATISTICHE</b>", SEP, "📡 <b>Segnali</b>"]
    rows = [["", "N.", "CLV", "Resa"]]
    for k in sig:
        n, pl = theo.get(k["kind"], (0, 0))
        rows.append(["Automatici" if k["kind"] == "auto" else "Pinnacle mosso", str(k["n"]),
                     pct(k["clv"] or 0) if k["nc"] else "–", pct(pl / n) if n else "–"])
    lines.append(table(rows, "lrrr") if len(rows) > 1 else "<i>Nessun segnale ancora.</i>")
    lines += [
        "",
        "🎯 <b>Le tue giocate</b>",
        table([["Giocate", f"{len(bets)} (chiuse {settled})"], ["Vinte", f"{won} su {settled}" if settled else "–"],
               ["Profitto", eur(pnl)], ["ROI", pct(pnl / staked) if staked else "–"],
               ["CLV medio", f"{pct(sum(clvs) / len(clvs))} su {len(clvs)}" if clvs else "–"],
               ["Bankroll", eur(bankroll(cfg, con))]], "lr"),
        "",
        "<i>💡 Il CLV è ciò che conta: stabilmente sopra +2% = la falla c'è. "
        "Il profitto su poche giocate è soprattutto fortuna. «Resa» = rendimento se avessi giocato tutti i segnali.</i>",
    ]
    return "\n".join(lines)
