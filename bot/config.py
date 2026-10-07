"""Impostazioni: variabili d'ambiente, oppure file .env nella cartella del progetto (mai su GitHub)."""
import os
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(os.path.join(ROOT, ".env"))


def _list(name, default):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


def _num(name, default):
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return float(default)


@dataclass
class Config:
    odds_api_key: str = ""
    telegram_token: str = ""
    telegram_chat_id: str = ""
    secret: str = ""  # protegge /cron, /setup e l'indirizzo del webhook
    football_data_key: str = ""
    db_path: str = ""
    leagues: list = field(default_factory=list)
    bookmakers: list = field(default_factory=list)
    soft_books: list = field(default_factory=list)
    ev_min: float = 0.05  # vantaggio minimo per i segnali (come nel backtest)
    move_min: float = 0.04  # movimento di Pinnacle che fa scattare l'avviso
    max_odds: float = 5.0  # quote più alte escluse (come nel backtest)
    scan_every_h: float = 8.0  # ore minime tra due scansioni dello stesso campionato
    credit_reserve: float = 60  # crediti tenuti da parte per super quote, probabilità e chiusure
    boost_min_direct: float = 0.02  # super quota su un mercato quotato da Pinnacle
    boost_min_model: float = 0.06  # super quota combinata (modello dei gol)
    default_bankroll: float = 1000


def load():
    e = os.environ.get
    return Config(
        odds_api_key=e("ODDS_API_KEY", ""),
        telegram_token=e("TELEGRAM_TOKEN", ""),
        telegram_chat_id=e("TELEGRAM_CHAT_ID", ""),
        secret=e("SECRET", ""),
        football_data_key=e("FOOTBALL_DATA_KEY", ""),
        db_path=e("DB_PATH", os.path.join(ROOT, "scommesse.db")),
        leagues=_list("LEAGUES", "soccer_italy_serie_a,soccer_epl,soccer_spain_la_liga,soccer_germany_bundesliga,"
                                 "soccer_france_ligue_one,soccer_uefa_champs_league,soccer_italy_serie_b,soccer_uefa_europa_league"),
        bookmakers=_list("BOOKMAKERS", "pinnacle,unibet_it,codere_it"),
        soft_books=_list("SOFT_BOOKS", "unibet_it,codere_it"),
        ev_min=_num("EV_MIN", 0.05),
        move_min=_num("MOVE_MIN", 0.04),
        max_odds=_num("MAX_ODDS", 5),
        scan_every_h=_num("SCAN_EVERY_H", 8),
        credit_reserve=_num("CREDIT_RESERVE", 60),
        boost_min_direct=_num("BOOST_MIN_DIRECT", 0.02),
        boost_min_model=_num("BOOST_MIN_MODEL", 0.06),
        default_bankroll=_num("DEFAULT_BANKROLL", 1000),
    )


LEAGUE_NAMES = {
    "soccer_italy_serie_a": "Serie A",
    "soccer_italy_serie_b": "Serie B",
    "soccer_epl": "Premier League",
    "soccer_spain_la_liga": "Liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_france_ligue_one": "Ligue 1",
    "soccer_uefa_champs_league": "Champions League",
    "soccer_uefa_europa_league": "Europa League",
    "soccer_uefa_europa_conference_league": "Conference League",
    "soccer_netherlands_eredivisie": "Eredivisie",
    "soccer_portugal_primeira_liga": "Primeira Liga",
    "soccer_efl_champ": "Championship",
}

BOOK_NAMES = {"pinnacle": "Pinnacle", "unibet_it": "Unibet", "codere_it": "Codere", "williamhill": "William Hill"}
