"""Database SQLite: un file sul server."""
import sqlite3

from .teams import EventInfo

SCHEMA = """
-- Partite in calendario (endpoint gratuito /events) e risultato finale dei 90 minuti
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  sport_key TEXT NOT NULL,
  home TEXT NOT NULL,
  away TEXT NOT NULL,
  commence INTEGER NOT NULL,
  home_score INTEGER,
  away_score INTEGER,
  completed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_commence ON events(commence);

-- Ogni quota letta. market: h2h | totals. outcome: 1 X 2 | over under
CREATE TABLE IF NOT EXISTS quotes (
  event_id TEXT NOT NULL, ts INTEGER NOT NULL, book TEXT NOT NULL, market TEXT NOT NULL,
  outcome TEXT NOT NULL, point REAL, price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS quotes_lookup ON quotes(event_id, book, market, ts);

CREATE TABLE IF NOT EXISTS leagues (
  sport_key TEXT PRIMARY KEY,
  last_events INTEGER NOT NULL DEFAULT 0,
  last_scan INTEGER NOT NULL DEFAULT 0,
  last_scores INTEGER NOT NULL DEFAULT 0
);

-- Segnali. kind: auto (un book italiano paga sopra il prezzo giusto) | move (Pinnacle ha accorciato)
CREATE TABLE IF NOT EXISTS signals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id TEXT NOT NULL, outcome TEXT NOT NULL, kind TEXT NOT NULL, book TEXT, price REAL,
  fair_odds REAL NOT NULL, min_odds REAL NOT NULL, created INTEGER NOT NULL, close_prob REAL
);
CREATE INDEX IF NOT EXISTS signals_event ON signals(event_id, outcome, kind);

-- Super quote analizzate. legs = JSON [{eventId, sels, prob, closeProb}]
CREATE TABLE IF NOT EXISTS boosts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  text TEXT NOT NULL, odds REAL NOT NULL, prob REAL NOT NULL, ev REAL NOT NULL,
  verdict TEXT NOT NULL, legs TEXT NOT NULL, created INTEGER NOT NULL
);

-- Giocate reali. kind: signal | boost. ref_id = id del segnale o della super quota
CREATE TABLE IF NOT EXISTS bets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL, ref_id INTEGER NOT NULL, odds REAL NOT NULL, stake REAL NOT NULL,
  created INTEGER NOT NULL, result TEXT
);

CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT NOT NULL);
"""


def connect(path):
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def kv_get(con, k):
    r = con.execute("SELECT v FROM kv WHERE k = ?", (k,)).fetchone()
    return r["v"] if r else None


def kv_set(con, k, v):
    con.execute("INSERT INTO kv (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (k, str(v)))
    con.commit()


def upsert_events(con, events):
    con.executemany(
        "INSERT INTO events (id, sport_key, home, away, commence) VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET commence = excluded.commence, home = excluded.home, away = excluded.away",
        [(e.id, e.sport_key, e.home, e.away, e.commence) for e in events],
    )
    con.commit()


def insert_quotes(con, ts, quotes):
    con.executemany(
        "INSERT INTO quotes (event_id, ts, book, market, outcome, point, price) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(q["event_id"], ts, q["book"], q["market"], q["outcome"], q["point"], q["price"]) for q in quotes],
    )
    con.commit()


def _quotes_at(con, event_id, book, market, agg):
    rows = con.execute(
        f"""SELECT ts, outcome, point, price FROM quotes WHERE event_id = ?1 AND book = ?2 AND market = ?3
            AND ts = (SELECT {agg}(ts) FROM quotes WHERE event_id = ?1 AND book = ?2 AND market = ?3)""",
        (event_id, book, market),
    ).fetchall()
    return [dict(r) for r in rows]


def latest_quotes(con, event_id, book, market):
    return _quotes_at(con, event_id, book, market, "MAX")


def first_quotes(con, event_id, book, market):
    return _quotes_at(con, event_id, book, market, "MIN")


def _ev(r):
    return EventInfo(r["id"], r["sport_key"], r["home"], r["away"], r["commence"])


def upcoming_events(con, start, end):
    rows = con.execute("SELECT * FROM events WHERE commence BETWEEN ? AND ? ORDER BY commence", (start, end)).fetchall()
    return [_ev(r) for r in rows]


def get_event(con, event_id):
    r = con.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _ev(r) if r else None


def prune(con, now):
    """Toglie le quote più vecchie di 45 giorni."""
    cutoff = now - 45 * 86400_000
    con.execute("DELETE FROM quotes WHERE ts < ?", (cutoff,))
    con.commit()
