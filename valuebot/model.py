"""Modello dei gol Dixon-Coles tarato sulle quote di Pinnacle.

Non prevede nulla da solo: sceglie i gol attesi delle due squadre (e la correzione rho sui
risultati bassi) per riprodurre 1X2 e over/under di Pinnacle senza margine. Serve a dare un
prezzo giusto alle combinazioni che Pinnacle non quota (es. "1 + over 2.5").
"""
import math
from dataclasses import dataclass

MAX_GOALS = 10


@dataclass
class GoalModel:
    lambda_home: float
    lambda_away: float
    rho: float
    matrix: list  # matrix[h][a] = probabilità che finisca h-a
    fit_error: float  # scarto massimo dalle probabilità di Pinnacle


def _pmf(lam):
    p = [math.exp(-lam)]
    for k in range(1, MAX_GOALS + 1):
        p.append(p[-1] * lam / k)
    return p


def score_matrix(lh, la, rho):
    ph, pa = _pmf(lh), _pmf(la)
    m, total = [], 0.0
    for h in range(MAX_GOALS + 1):
        row = []
        for a in range(MAX_GOALS + 1):
            tau = 1.0
            if h == 0 and a == 0:
                tau = 1 - lh * la * rho
            elif h == 0 and a == 1:
                tau = 1 + lh * rho
            elif h == 1 and a == 0:
                tau = 1 + la * rho
            elif h == 1 and a == 1:
                tau = 1 - rho
            v = max(0.0, ph[h] * pa[a] * tau)
            row.append(v)
            total += v
        m.append(row)
    return [[v / total for v in row] for row in m]


def outcome_probs(m):
    ph = pd = pa = 0.0
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            if h > a:
                ph += m[h][a]
            elif h == a:
                pd += m[h][a]
            else:
                pa += m[h][a]
    return ph, pd, pa


def over_prob(m, line):
    return sum(m[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h + a > line)


def _over_for_line(m, line):
    """Linee asiatiche intere (es. 3.0): probabilità condizionata escludendo il rimborso."""
    if float(line).is_integer():
        over = sum(m[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h + a > line)
        under = sum(m[h][a] for h in range(MAX_GOALS + 1) for a in range(MAX_GOALS + 1) if h + a < line)
        return over / (over + under)
    return over_prob(m, line)


def _objective(x, t):
    lh, la, rho = math.exp(x[0]), math.exp(x[1]), x[2]
    if not -0.35 <= rho <= 0.35 or 1 - lh * la * rho <= 0 or 1 + lh * rho <= 0 or 1 + la * rho <= 0:
        return 1e9
    m = score_matrix(lh, la, rho)
    ph, pd, pa = outcome_probs(m)
    err = (ph - t["pH"]) ** 2 + (pd - t["pD"]) ** 2 + (pa - t["pA"]) ** 2
    if t.get("totals"):
        line, p_over = t["totals"]
        err += 2 * (_over_for_line(m, line) - p_over) ** 2
    else:
        # senza over/under fissiamo il grado di libertà in più con valori tipici
        # del calcio europeo (circa 2,6 gol a partita, rho -0,1)
        err += 0.0005 * (lh + la - 2.6) ** 2 + 0.002 * (rho + 0.1) ** 2
    return err


def _nelder_mead(f, x0, step=0.25, iters=400):
    n = len(x0)
    simplex = [list(x0)]
    for i in range(n):
        x = list(x0)
        x[i] += 0.05 if i == 2 else step
        simplex.append(x)
    values = [f(x) for x in simplex]
    for _ in range(iters):
        order = sorted(range(n + 1), key=lambda i: values[i])
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if values[n] - values[0] < 1e-14:
            break
        centroid = [sum(simplex[i][j] for i in range(n)) / n for j in range(n)]
        worst = simplex[n]
        refl = [c + (c - w) for c, w in zip(centroid, worst)]
        fr = f(refl)
        if fr < values[0]:
            exp = [c + 2 * (c - w) for c, w in zip(centroid, worst)]
            fe = f(exp)
            simplex[n], values[n] = (exp, fe) if fe < fr else (refl, fr)
        elif fr < values[n - 1]:
            simplex[n], values[n] = refl, fr
        else:
            contr = [c + 0.5 * (w - c) for c, w in zip(centroid, worst)]
            fc = f(contr)
            if fc < values[n]:
                simplex[n], values[n] = contr, fc
            else:
                for i in range(1, n + 1):
                    simplex[i] = [simplex[0][j] + 0.5 * (simplex[i][j] - simplex[0][j]) for j in range(n)]
                    values[i] = f(simplex[i])
    best = min(range(n + 1), key=lambda i: values[i])
    return simplex[best]


def fit_model(p_home, p_draw, p_away, totals=None):
    """totals = (linea, probabilità over senza margine) oppure None."""
    t = {"pH": p_home, "pD": p_draw, "pA": p_away, "totals": totals}
    total = max(1.2, totals[0] + (totals[1] - 0.5) * 2) if totals else 2.6
    share = 0.5 + (p_home - p_away) * 0.45
    x = _nelder_mead(lambda v: _objective(v, t), [math.log(total * share), math.log(total * (1 - share)), -0.08])
    lh, la = math.exp(x[0]), math.exp(x[1])
    m = score_matrix(lh, la, x[2])
    ph, pd, pa = outcome_probs(m)
    err = max(abs(ph - p_home), abs(pd - p_draw), abs(pa - p_away))
    if totals:
        err = max(err, abs(_over_for_line(m, totals[0]) - totals[1]))
    return GoalModel(lh, la, x[2], m, err)
