"""Formattazione dei messaggi in italiano."""
import html
from datetime import datetime, timedelta, timezone

from .config import LEAGUE_NAMES

esc = html.escape

_DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]


def num(x, d=2):
    s = f"{x:,.{d}f}"  # 1,234.56
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def pct(x, d=1):
    return f"{'+' if x >= 0 else ''}{num(x * 100, d)}%"


def eur(x):
    return f"{num(x, 2)} €"


def _rome(ms):
    """Ora italiana (CET/CEST) senza dipendere dai dati di fuso orario del server."""
    utc = datetime.fromtimestamp(ms / 1000, timezone.utc)
    y = utc.year
    last_sunday = lambda month: max(datetime(y, month, d, tzinfo=timezone.utc) for d in range(25, 32) if datetime(y, month, d).weekday() == 6)
    start = last_sunday(3).replace(hour=1)
    end = last_sunday(10).replace(hour=1)
    return utc + timedelta(hours=2 if start <= utc < end else 1)


def when(ms):
    t = _rome(ms)
    return f"{_DAYS[t.weekday()]} {t.day} {t:%H:%M}"


def match(ev):
    return f"{ev.home} – {ev.away}"


def league(sport_key):
    return LEAGUE_NAMES.get(sport_key, sport_key)


def outcome_label(ev, o):
    return f"1 ({ev.home})" if o == "1" else f"2 ({ev.away})" if o == "2" else "X (pareggio)"


def parse_num(s):
    """Accetta "4,10" e "4.10"."""
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None
