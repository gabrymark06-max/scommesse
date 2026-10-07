import pytest

from bot.fair import devig_power, kelly_stake, overround
from bot.markets import joint_prob
from bot.model import fit_model, outcome_probs, over_prob
from bot.parser import extract_odds, parse_boost
from bot.pricing import evaluate_boost, h2h_from, price_leg, totals_from
from bot.sources import find_fd_match, regulation_score
from bot.teams import EventInfo

T0 = 1791659100000  # 10 ottobre 2026 18:45 UTC
EVENTS = [
    EventInfo("e1", "soccer_italy_serie_a", "Inter Milan", "Juventus", T0),
    EventInfo("e2", "soccer_italy_serie_a", "AC Milan", "AS Roma", T0 + 3600_000),
    EventInfo("e3", "soccer_italy_serie_a", "Napoli", "Atalanta BC", T0 + 7200_000),
    EventInfo("e4", "soccer_epl", "Manchester City", "Manchester United", T0 + 86400_000),
    EventInfo("e5", "soccer_italy_serie_a", "Lazio", "Bologna", T0 + 2 * 86400_000),
]


def test_devig():
    p = devig_power([2.1, 3.4, 3.7])
    assert sum(p) == pytest.approx(1)
    assert overround([2.1, 3.4, 3.7]) == pytest.approx(0.0406, abs=1e-3)
    prop = [x / 1.0406 for x in (1 / 2.1, 1 / 3.4, 1 / 3.7)]
    assert p[0] > prop[0] and p[2] < prop[2]


def test_kelly():
    assert kelly_stake(0.26, 4.1, 1000) == pytest.approx((0.26 * 4.1 - 1) / 3.1 * 0.25 * 1000, abs=0.01)
    assert kelly_stake(0.6, 3, 1000) == 20
    assert kelly_stake(0.2, 4, 1000) == 0


def test_model_fit():
    m = fit_model(0.48, 0.26, 0.26, totals=(2.5, 0.55))
    ph, pd, _ = outcome_probs(m.matrix)
    assert ph == pytest.approx(0.48, abs=0.005) and pd == pytest.approx(0.26, abs=0.005)
    assert over_prob(m.matrix, 2.5) == pytest.approx(0.55, abs=0.005)
    assert m.fit_error < 0.005 and m.lambda_home > m.lambda_away
    assert fit_model(0.72, 0.18, 0.10, totals=(2.5, 0.45)).fit_error < 0.01


def test_combo_correlation():
    m = fit_model(0.5, 0.25, 0.25, totals=(2.5, 0.55))
    p1 = joint_prob(m.matrix, [{"t": "1x2", "o": "1"}])
    po = joint_prob(m.matrix, [{"t": "total", "side": "over", "line": 2.5}])
    both = joint_prob(m.matrix, [{"t": "1x2", "o": "1"}, {"t": "total", "side": "over", "line": 2.5}])
    assert p1 * po < both < min(p1, po)


@pytest.mark.parametrize("txt,odds", [
    ("Inter vince e over 2.5 @3.50", 3.5), ("Inter vince e over 2,5 @ 3,50", 3.5), ("Inter vince e over 2.5 a 3,25", 3.25),
    ("Inter vince quota 2.80", 2.8), ("Inter e Milan vincono 4.00", 4.0), ("over 2.5 Inter-Juve", None)])
def test_extract_odds(txt, odds):
    assert extract_odds(txt)[0] == odds


def test_parse_win_over():
    p = parse_boost("Inter vince e over 2,5 @ 3,50", EVENTS)
    assert p.odds == 3.5 and p.unknown == []
    assert p.legs["e1"] == [{"t": "1x2", "o": "1"}, {"t": "total", "side": "over", "line": 2.5}]


def test_parse_multiple_wins():
    p = parse_boost("Inter, Napoli e Milan vincono @ 4.00", EVENTS)
    assert p.unknown == []
    for e in ("e1", "e2", "e3"):
        assert p.legs[e] == [{"t": "1x2", "o": "1"}]


def test_parse_away_and_inter_milan():
    assert parse_boost("Juventus vince @ 3.9", EVENTS).legs["e1"] == [{"t": "1x2", "o": "2"}]
    assert parse_boost("Roma vince + gol @ 5.5", EVENTS).legs["e2"] == [{"t": "1x2", "o": "2"}, {"t": "btts", "yes": True}]


def test_parse_match_then_signs():
    p = parse_boost("Inter - Juventus: 1 + over 2.5 @ 3.40", EVENTS)
    assert p.unknown == [] and p.legs["e1"] == [{"t": "1x2", "o": "1"}, {"t": "total", "side": "over", "line": 2.5}]
    q = parse_boost("Lazio-Bologna: gol + over 2,5 @ 2,90", EVENTS)
    assert q.legs["e5"] == [{"t": "btts", "yes": True}, {"t": "total", "side": "over", "line": 2.5}]


def test_parse_other_markets():
    assert parse_boost("Napoli vince con almeno 2 gol di scarto @ 3.75", EVENTS).legs["e3"] == [{"t": "margin", "team": "home", "min": 2}]
    assert parse_boost("Atalanta o pareggio @ 1.9", EVENTS).legs["e3"] == [{"t": "dc", "o": "X2"}]
    assert parse_boost("Inter over 1.5 gol @ 2.2", EVENTS).legs["e1"] == [{"t": "teamTotal", "team": "home", "side": "over", "line": 1.5}]
    assert parse_boost("Inter-Juventus risultato esatto 2-1 @ 11", EVENTS).legs["e1"] == [{"t": "exact", "h": 2, "a": 1}]
    assert parse_boost("Man City - Man United multigol 2-4 @ 1.95", EVENTS).legs["e4"] == [{"t": "multigol", "lo": 2, "hi": 4}]


def test_parse_unsupported_and_unknown():
    assert len(parse_boost("Lautaro segna e Inter vince @ 3.2", EVENTS).unsupported) == 1
    assert len(parse_boost("Inter vince e più di 9.5 angoli @ 3", EVENTS).unsupported) == 1
    assert len(parse_boost("Inter vince primo tempo @ 2.5", EVENTS).unsupported) == 1
    assert parse_boost("Inter vince e qualcosa di strano @ 3", EVENTS).unknown


H2H = h2h_from([{"outcome": "1", "price": 2.1}, {"outcome": "X", "price": 3.4}, {"outcome": "2", "price": 3.7}])
TOT = totals_from([{"outcome": "over", "point": 2.5, "price": 1.85}, {"outcome": "under", "point": 2.5, "price": 2.02}])


def test_eval_direct():
    sels = [{"t": "1x2", "o": "2"}]
    lp = price_leg(H2H, TOT, sels)
    assert lp.direct
    r = evaluate_boost(4.1, [(lp, sels)], 0.02, 0.06, 1000)
    assert r.ev > 0.05 and r.verdict == "SI" and r.stake > 0


def test_eval_model_thresholds():
    sels = [{"t": "1x2", "o": "1"}, {"t": "total", "side": "over", "line": 2.5}]
    lp = price_leg(H2H, TOT, sels)
    fair = 1 / lp.prob
    assert not lp.direct
    assert evaluate_boost(fair * 0.95, [(lp, sels)], 0.02, 0.06, 1000).verdict == "NO"
    assert evaluate_boost(fair * 1.03, [(lp, sels)], 0.02, 0.06, 1000).verdict == "MARGINALE"
    assert evaluate_boost(fair * 1.10, [(lp, sels)], 0.02, 0.06, 1000).verdict == "SI"
    btts = [{"t": "btts", "yes": True}]
    lp2 = price_leg(H2H, None, btts)
    r = evaluate_boost(1 / lp2.prob * 1.08, [(lp2, btts)], 0.02, 0.06, 1000)
    assert r.weak_model and r.verdict == "MARGINALE"


def _fd(home, away, utc_ms, ft, **score):
    from datetime import datetime, timezone
    return {"utcDate": datetime.fromtimestamp(utc_ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "FINISHED",
            "homeTeam": {"name": home}, "awayTeam": {"name": away},
            "score": {"duration": "REGULAR", "fullTime": {"home": ft[0], "away": ft[1]}, **score}}


def test_football_data_matching():
    day = [_fd("FC Internazionale Milano", "Juventus FC", T0, (2, 1)), _fd("AC Milan", "AS Roma", T0 + 3 * 3600_000, (0, 0)),
           _fd("Manchester United FC", "Manchester City FC", T0, (1, 3))]
    assert find_fd_match(EventInfo("a", "x", "Inter Milan", "Juventus", T0), day) is day[0]
    assert find_fd_match(EventInfo("b", "x", "AC Milan", "AS Roma", T0 + 3 * 3600_000), day) is day[1]
    assert find_fd_match(EventInfo("c", "x", "Manchester United", "Manchester City", T0), day) is day[2]
    assert find_fd_match(EventInfo("d", "x", "Arsenal", "Chelsea", T0), day) is None
    assert find_fd_match(EventInfo("a", "x", "Inter Milan", "Juventus", T0 + 26 * 3600_000), day) is None


def test_regulation_score():
    assert regulation_score(_fd("A", "B", T0, (2, 1))) == (2, 1)
    assert regulation_score(_fd("A", "B", T0, (2, 1), duration="EXTRA_TIME", regularTime={"home": 1, "away": 1})) == (1, 1)
    assert regulation_score(_fd("A", "B", T0, (2, 1), duration="PENALTY_SHOOTOUT")) is None
    assert regulation_score({**_fd("A", "B", T0, (0, 0)), "status": "POSTPONED"}) is None
