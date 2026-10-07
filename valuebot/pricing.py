"""Prezzo giusto di una giocata a partire dalle quote di Pinnacle."""
from dataclasses import dataclass

from .fair import devig_power, kelly_stake
from .markets import joint_prob
from .model import fit_model


def h2h_from(rows):
    """rows: lista di dict con outcome/price. Restituisce (quote, probabilità giuste) di 1, X, 2."""
    get = lambda o: next((r["price"] for r in rows if r["outcome"] == o), None)
    o = [get("1"), get("X"), get("2")]
    if not all(o):
        return None
    return {"odds": o, "fair": devig_power(o)}


def totals_from(rows):
    """Linea più equilibrata dell'over/under: (linea, probabilità over senza margine)."""
    lines = {}
    for r in rows:
        if r.get("point") is None:
            continue
        lines.setdefault(r["point"], {})[r["outcome"]] = r["price"]
    best, gap = None, float("inf")
    for line, l in lines.items():
        if "over" in l and "under" in l and abs(l["over"] - l["under"]) < gap:
            gap = abs(l["over"] - l["under"])
            best = (line, devig_power([l["over"], l["under"]])[0])
    return best


@dataclass
class LegPrice:
    prob: float
    direct: bool  # mercato quotato da Pinnacle così com'è, nessun modello
    has_totals: bool
    model: object = None


def price_leg(h2h, totals, sels):
    if len(sels) == 1:
        s = sels[0]
        p1, px, p2 = h2h["fair"]
        if s["t"] == "1x2":
            return LegPrice({"1": p1, "X": px, "2": p2}[s["o"]], True, bool(totals))
        if s["t"] == "dc":
            return LegPrice({"1X": p1 + px, "X2": px + p2, "12": p1 + p2}[s["o"]], True, bool(totals))
        if s["t"] == "total" and totals and s["line"] == totals[0] and not float(s["line"]).is_integer():
            return LegPrice(totals[1] if s["side"] == "over" else 1 - totals[1], True, True)
    m = fit_model(*h2h["fair"], totals=totals)
    return LegPrice(joint_prob(m.matrix, sels), False, bool(totals), m)


def needs_goals(sels):
    return any(s["t"] not in ("1x2", "dc") for s in sels)


@dataclass
class BoostEval:
    prob: float
    fair_odds: float
    ev: float
    threshold: float
    verdict: str  # SI | MARGINALE | NO
    stake: float
    model_used: bool
    weak_model: bool


def evaluate_boost(odds, legs, min_direct, min_model, bankroll):
    """legs: lista di (LegPrice, selezioni)."""
    prob = 1.0
    for lp, _ in legs:
        prob *= lp.prob
    model_used = any(not lp.direct for lp, _ in legs)
    # senza over/under i gol attesi sono stimati con valori medi: serve più margine
    weak = any(not lp.direct and not lp.has_totals and needs_goals(sels) for lp, sels in legs)
    threshold = min_direct if not model_used else (min_model + 0.04 if weak else min_model)
    ev = prob * odds - 1
    verdict = "SI" if ev >= threshold else "MARGINALE" if ev >= 0 else "NO"
    stake = kelly_stake(prob, odds, bankroll) if verdict == "SI" else 0.0
    return BoostEval(prob, 1 / prob, ev, threshold, verdict, stake, model_used, weak)
