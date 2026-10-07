"""Settimane di funzionamento simulate attraverso l'app web, con The Odds API, football-data.org e Telegram finti."""
import json
from datetime import datetime, timezone

import pytest
import requests

import app as webapp
from bot import config, db

H = 3600_000
T0 = int(datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc).timestamp() * 1000)
KICK = T0 + 30 * H


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeResp:
    def __init__(self, data, status=200, headers=None):
        self._d, self.status_code, self.headers = data, status, headers or {}
        self.ok = status < 400
        self.text = json.dumps(data)

    def json(self):
        return self._d

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(self.status_code)


class World:
    def __init__(self):
        self.books, self.sent, self.odds_calls, self.credits = [], [], [], 500
        self.final, self.score_calls, self.fd_calls, self.now = None, 0, 0, T0

    def event(self, markets):
        bms = []
        for b in self.books:
            ms = [{"key": "h2h", "outcomes": [{"name": "Inter Milan", "price": b["h2h"][0]}, {"name": "Draw", "price": b["h2h"][1]},
                                              {"name": "Juventus", "price": b["h2h"][2]}]}]
            if "totals" in markets and b.get("totals"):
                l, o, u = b["totals"]
                ms.append({"key": "totals", "outcomes": [{"name": "Over", "price": o, "point": l}, {"name": "Under", "price": u, "point": l}]})
            bms.append({"key": b["key"], "markets": ms})
        return {"id": "ev1", "sport_key": "soccer_italy_serie_a", "home_team": "Inter Milan", "away_team": "Juventus",
                "commence_time": iso(KICK), "bookmakers": bms}

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        if "football-data.org" in url:
            self.fd_calls += 1
            ms = []
            if self.final:
                ms = [{"utcDate": iso(KICK), "status": "FINISHED",
                       "homeTeam": {"name": "FC Internazionale Milano", "shortName": "Inter"}, "awayTeam": {"name": "Juventus FC"},
                       "score": {"duration": "REGULAR", "fullTime": {"home": self.final[0], "away": self.final[1]}}}]
            return FakeResp({"matches": ms})
        serie_a = "serie_a" in url
        if url.endswith("/scores"):
            self.score_calls += 1
            self.credits -= 2
            body = []
            if serie_a:
                sc = [{"name": "Inter Milan", "score": str(self.final[0])}, {"name": "Juventus", "score": str(self.final[1])}] if self.final else None
                body = [{"id": "ev1", "completed": bool(self.final), "home_team": "Inter Milan", "away_team": "Juventus", "scores": sc}]
            return FakeResp(body, headers={"x-requests-remaining": str(self.credits), "x-requests-last": "2"})
        if url.endswith("/events"):
            return FakeResp([{**self.event([]), "bookmakers": None}] if serie_a else [])
        if url.endswith("/odds"):
            if not serie_a:
                return FakeResp([], headers={"x-requests-remaining": str(self.credits), "x-requests-last": "0"})
            markets = params["markets"].split(",")
            self.credits -= len(markets)
            self.odds_calls.append("+".join(markets))
            return FakeResp([self.event(markets)], headers={"x-requests-remaining": str(self.credits), "x-requests-last": str(len(markets))})
        return FakeResp({}, 404)

    def post(self, url, json=None, timeout=None):
        if "sendMessage" in url:
            self.sent.append(json["text"])
        return FakeResp({"ok": True})


@pytest.fixture
def w(monkeypatch, tmp_path):
    world = World()
    monkeypatch.setattr(requests, "get", world.get)
    monkeypatch.setattr(requests, "post", world.post)
    monkeypatch.setattr(webapp, "_now", lambda: world.now)
    cfg = config.load()
    cfg.odds_api_key, cfg.telegram_token, cfg.telegram_chat_id, cfg.secret = "k", "t", "42", "s"
    cfg.football_data_key = ""
    cfg.db_path = str(tmp_path / "test.db")
    cfg.leagues = ["soccer_italy_serie_a", "soccer_epl"]
    monkeypatch.setattr(webapp, "cfg", cfg)
    world.client = webapp.app.test_client()
    return world


def cron(w, at):
    w.now = at
    r = w.client.get("/cron?secret=s")
    assert r.status_code == 200, r.data


def say(w, text, at, chat=42):
    w.now = at
    r = w.client.post("/telegram", json={"message": {"chat": {"id": chat}, "text": text}},
                      headers={"X-Telegram-Bot-Api-Secret-Token": "s"})
    assert r.status_code == 200


def test_security(w):
    assert w.client.get("/cron?secret=sbagliato").status_code == 403
    assert w.client.post("/telegram", json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "x"}).status_code == 403
    say(w, "/start", T0, chat=99)
    assert w.sent == []  # risponde solo al proprietario


def test_full_flow(w):
    # 1. Unibet paga la Juve 4,15 contro un prezzo giusto ~3,85
    w.books = [{"key": "pinnacle", "h2h": [2.1, 3.4, 3.7], "totals": (2.5, 1.85, 2.02)},
               {"key": "unibet_it", "h2h": [2.0, 3.3, 4.15]}, {"key": "codere_it", "h2h": [2.05, 3.25, 3.6]}]
    cron(w, T0)
    assert w.odds_calls == ["h2h"]
    assert len(w.sent) == 1 and "OCCASIONE S1" in w.sent[0] and "Unibet paga <b>4,15</b>" in w.sent[0] and "2 (Juventus)" in w.sent[0]

    # 2. 15 minuti dopo: niente nuova scansione né doppio avviso
    w.sent = []
    cron(w, T0 + H // 4)
    assert len(w.odds_calls) == 1 and w.sent == []

    # 3. dopo 9 ore Pinnacle accorcia l'Inter -> avviso "ha mosso"
    w.books[0] = {"key": "pinnacle", "h2h": [1.95, 3.6, 4.1], "totals": (2.5, 1.8, 2.08)}
    w.books[1] = {"key": "unibet_it", "h2h": [2.0, 3.3, 3.9]}
    cron(w, T0 + 9 * H)
    assert "PINNACLE HA MOSSO" in "\n".join(w.sent) and "1 (Inter Milan)" in "\n".join(w.sent)

    # 4. super quote
    w.sent = []
    say(w, "Inter vince e over 2,5 @ 4,50", T0 + 10 * H)
    assert w.odds_calls[-1] == "h2h+totals" and "SÌ, conviene" in w.sent[0] and "[modello]" in w.sent[0]
    w.sent = []
    say(w, "Inter vince e over 2.5 @ 2.50", T0 + 10 * H + 360_000)
    assert "NO, non conviene" in w.sent[0]
    assert w.odds_calls.count("h2h+totals") == 1  # quote ancora fresche: nessun credito
    w.sent = []
    say(w, "Lautaro segna e Inter vince @ 3", T0 + 10 * H)
    assert "Non valutabile" in w.sent[0]

    # 5. probabilità
    w.sent = []
    say(w, "Inter-Juventus", T0 + 10 * H)
    assert "Gol attesi" in w.sent[0] and "Risultati più probabili" in w.sent[0]
    w.sent = []
    say(w, "/prob Juventus vince", T0 + 10 * H)
    assert "Probabilità <b>2" in w.sent[0] and "[modello]" not in w.sent[0]

    # 6. giocate e listino
    w.sent = []
    say(w, "/giocata S1 4,15 10", T0 + 10 * H)
    say(w, "/giocata Q1 12", T0 + 10 * H)
    assert "Registrata <b>B1</b>" in w.sent[0] and "Registrata <b>B2</b>" in w.sent[1]
    w.sent = []
    say(w, "/listino inter", T0 + 10 * H)
    assert "Quote minime da cercare" in w.sent[0] and "Inter Milan – Juventus" in w.sent[0]

    # 7. chiusura 15 minuti prima: una sola chiamata, con gli over/under per la super quota giocata
    w.books[0] = {"key": "pinnacle", "h2h": [2.0, 3.55, 3.5], "totals": (2.5, 1.75, 2.15)}
    before = len(w.odds_calls)
    cron(w, KICK - 15 * 60_000)
    assert len(w.odds_calls) == before + 1 and w.odds_calls[-1] == "h2h+totals"

    # 8. risultato: durante la partita niente, a fine partita (2-1) chiusura automatica
    cron(w, KICK + H)
    assert w.score_calls == 0
    w.final, w.sent = (2, 1), []
    cron(w, KICK + 3 * H)
    out = "\n".join(w.sent)
    assert w.score_calls == 1 and "B1 persa" in out and "B2 vinta" in out and "Inter Milan 2-1 Juventus" in out
    cron(w, KICK + 7 * H)
    assert w.score_calls == 1  # nessuna giocata aperta: nessuna lettura

    w.sent = []
    say(w, "/stats", KICK + 8 * H)
    st = w.sent[-1]
    assert "Giocate: 2 (chiuse 2)" in st and "Profitto: <b>32,00 €</b>" in st  # -10 + 12 x 3,50
    assert "CLV medio delle tue giocate: <b>+" in st and "rendimento se giocati tutti" in st


def test_football_data_is_free(w):
    webapp.cfg.football_data_key = "fd"
    w.books = [{"key": "pinnacle", "h2h": [2.1, 3.4, 3.7], "totals": (2.5, 1.85, 2.02)}, {"key": "unibet_it", "h2h": [2.0, 3.3, 4.15]}]
    cron(w, T0)
    say(w, "/giocata S1 4,15 10", T0 + H)
    w.final, w.sent = (0, 1), []
    before = w.credits
    cron(w, KICK + 3 * H)
    assert w.fd_calls == 1 and w.score_calls == 0 and w.credits == before
    assert "B1 vinta" in "\n".join(w.sent) and "+31,50 €" in "\n".join(w.sent)


def test_credit_budget(w):
    w.books = [{"key": "pinnacle", "h2h": [2.1, 3.4, 3.7]}]
    con = db.connect(webapp.cfg.db_path)
    db.kv_set(con, "credits_remaining", 70)
    con.close()
    cron(w, T0)
    assert w.odds_calls == []  # 70 - riserva 60 = 10 crediti in 25 giorni -> 0 al giorno
