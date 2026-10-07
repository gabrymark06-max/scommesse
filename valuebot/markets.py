"""Selezioni su una partita (risultato dei 90 minuti) come dizionari semplici.

Tipi:
  {"t": "1x2", "o": "1"|"X"|"2"}
  {"t": "dc", "o": "1X"|"X2"|"12"}
  {"t": "total", "side": "over"|"under", "line": 2.5}
  {"t": "teamTotal", "team": "home"|"away", "side": ..., "line": ...}
  {"t": "btts", "yes": True|False}
  {"t": "margin", "team": "home"|"away", "min": 2}
  {"t": "exact", "h": 2, "a": 1}
  {"t": "multigol", "lo": 2, "hi": 4}
"""
from .model import MAX_GOALS


def holds(s, h, a):
    t = s["t"]
    if t == "1x2":
        return h > a if s["o"] == "1" else h == a if s["o"] == "X" else h < a
    if t == "dc":
        return h >= a if s["o"] == "1X" else h <= a if s["o"] == "X2" else h != a
    if t == "total":
        return h + a > s["line"] if s["side"] == "over" else h + a < s["line"]
    if t == "teamTotal":
        g = h if s["team"] == "home" else a
        return g > s["line"] if s["side"] == "over" else g < s["line"]
    if t == "btts":
        return (h > 0 and a > 0) == s["yes"]
    if t == "margin":
        return (h - a if s["team"] == "home" else a - h) >= s["min"]
    if t == "exact":
        return h == s["h"] and a == s["a"]
    if t == "multigol":
        return s["lo"] <= h + a <= s["hi"]
    raise ValueError(t)


def joint_prob(matrix, sels):
    """Probabilità che tutte le selezioni (sulla stessa partita) siano vere."""
    return sum(
        matrix[h][a]
        for h in range(MAX_GOALS + 1)
        for a in range(MAX_GOALS + 1)
        if all(holds(s, h, a) for s in sels)
    )


def _line(l):
    return f"{l:.1f}".replace(".", ",")


def describe(s, home, away):
    team = lambda t: home if t == "home" else away
    t = s["t"]
    if t == "1x2":
        return f"vince {home}" if s["o"] == "1" else f"vince {away}" if s["o"] == "2" else "pareggio"
    if t == "dc":
        return f"{home} o pareggio" if s["o"] == "1X" else f"{away} o pareggio" if s["o"] == "X2" else "non finisce pari"
    if t == "total":
        return f"{s['side']} {_line(s['line'])}"
    if t == "teamTotal":
        return f"{team(s['team'])} {s['side']} {_line(s['line'])} gol"
    if t == "btts":
        return "entrambe segnano (gol)" if s["yes"] else "almeno una non segna (nogol)"
    if t == "margin":
        return f"vince {team(s['team'])}" if s["min"] <= 1 else f"{team(s['team'])} vince con {s['min']}+ gol di scarto"
    if t == "exact":
        return f"risultato esatto {s['h']}-{s['a']}"
    if t == "multigol":
        return f"multigol {s['lo']}-{s['hi']}"
    return t
