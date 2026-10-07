"""Probabilità "giuste" dalle quote, puntata con Kelly frazionario."""


def devig_power(odds):
    """Toglie il margine col metodo power: p_i = (1/o_i)^k con somma 1.
    Rispetto alla divisione proporzionale corregge il favourite-longshot bias."""
    inv = [1 / o for o in odds]
    lo, hi = 0.5, 3.0
    for _ in range(60):
        k = (lo + hi) / 2
        if sum(x ** k for x in inv) < 1:
            hi = k
        else:
            lo = k
    k = (lo + hi) / 2
    p = [x ** k for x in inv]
    s = sum(p)
    return [x / s for x in p]


def overround(odds):
    return sum(1 / o for o in odds) - 1


def kelly_stake(prob, odds, bankroll, fraction=0.25, cap=0.02):
    """Puntata con Kelly frazionario, limitata a `cap` del bankroll."""
    edge = prob * odds - 1
    if edge <= 0 or odds <= 1:
        return 0.0
    f = min(edge / (odds - 1) * fraction, cap)
    return max(0.0, round(bankroll * f, 2))
