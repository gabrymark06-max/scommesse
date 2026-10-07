"""Formattazione dei messaggi in italiano."""
import html
from datetime import datetime, timedelta, timezone

from .config import LEAGUE_NAMES

esc = html.escape

_DAYS = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
_MONTHS = ["gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic"]
SEP = "━━━━━━━━━━━━━━━━━━"


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
    return f"{_DAYS[t.weekday()]} {t.day} {_MONTHS[t.month - 1]} · {t:%H:%M}"


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


def bar(p, width=10):
    """Barra di probabilità: ███░░░░░░░"""
    n = max(0, min(width, round(p * width)))
    return "█" * n + "░" * (width - n)


def head(ev):
    """Intestazione standard di una partita."""
    return f"⚽ <b>{esc(match(ev))}</b>\n🏆 {esc(league(ev.sport_key))}  ·  🗓 {when(ev.commence)}"


def short(name, n=12):
    return name if len(name) <= n else name[: n - 1] + "…"


def table(rows, align=None):
    """Tabella allineata in un blocco monospazio. align: stringa di 'l'/'r' per colonna."""
    rows = [[str(c) for c in r] for r in rows]
    cols = max(len(r) for r in rows)
    align = (align or "l" * cols).ljust(cols, "l")
    width = [max(len(r[i]) for r in rows if i < len(r)) for i in range(cols)]
    lines = []
    for r in rows:
        cells = [(c.rjust(width[i]) if align[i] == "r" else c.ljust(width[i])) for i, c in enumerate(r)]
        lines.append("  ".join(cells).rstrip())
    return "<pre>" + esc("\n".join(lines)) + "</pre>"
