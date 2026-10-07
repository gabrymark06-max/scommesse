"""Gestione dei crediti di The Odds API (piano gratuito: 500 al mese)."""
import math
from datetime import datetime, timezone

from .db import kv_get, kv_set

FREE_PLAN = 500


def _day(now):
    return datetime.fromtimestamp(now / 1000, timezone.utc).strftime("%Y-%m-%d")


def remaining(con):
    v = kv_get(con, "credits_remaining")
    return FREE_PLAN if v is None else int(float(v))


def used_today(con, now):
    return int(float(kv_get(con, f"used:{_day(now)}") or 0))


def record_usage(con, now, rem, used):
    """Registra il consumo dopo una chiamata a pagamento (dagli header di The Odds API)."""
    if rem is not None:
        kv_set(con, "credits_remaining", rem)
    kv_set(con, f"used:{_day(now)}", used_today(con, now) + (used if used is not None else 1))


def daily_budget(rem, reserve, now):
    """Crediti spendibili oggi: rimasti meno la riserva, divisi per i giorni a fine mese."""
    d = datetime.fromtimestamp(now / 1000, timezone.utc)
    end = datetime(d.year + (d.month == 12), d.month % 12 + 1, 1, tzinfo=timezone.utc)
    days_left = max(1, math.ceil((end.timestamp() * 1000 - now) / 86400_000))
    return max(0, int((rem - reserve) // days_left))
