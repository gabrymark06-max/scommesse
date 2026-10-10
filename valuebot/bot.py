import hashlib
import os
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parent.parent
BASE = 'https://www.mondopengwin.it'
INDEX = BASE + '/pronostici/calcio/'
ROME = ZoneInfo('Europe/Rome')
MONTHS = dict(zip('GEN FEB MAR APR MAG GIU LUG AGO SET OTT NOV DIC'.split(), range(1, 13)))
_send_lock = threading.Lock()
_last_send = {}


@dataclass
class Config:
    telegram_token: str = ''
    telegram_chat_id: str = ''
    secret: str = ''
    db_path: str = str(ROOT / 'scommesse.db')


def load():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ.setdefault(key.strip(), value.strip().strip('\"').strip("'"))
    return Config(os.getenv('TELEGRAM_TOKEN', ''), os.getenv('TELEGRAM_CHAT_ID', ''),
                  os.getenv('SECRET', ''), os.getenv('DB_PATH', str(ROOT / 'scommesse.db')))


def telegram_api(cfg, method, payload):
    if not cfg.telegram_token:
        raise RuntimeError('TELEGRAM_TOKEN mancante')
    for attempt in range(3):
        if method == 'sendMessage':
            target = str(payload['chat_id'])
            interval = 3.1 if target.startswith('-') else 1.05
            with _send_lock:
                delay = interval - (time.monotonic() - _last_send.get(target, 0))
                if delay > 0:
                    time.sleep(delay)
                _last_send[target] = time.monotonic()
        try:
            response = requests.post(f'https://api.telegram.org/bot{cfg.telegram_token}/{method}',
                                     json=payload, timeout=20)
            data = response.json()
        except (requests.RequestException, ValueError):
            # L'eccezione requests può contenere il token nell'URL.
            raise RuntimeError('Telegram non raggiungibile') from None
        if response.status_code == 429 and attempt < 2:
            delay = data.get('parameters', {}).get('retry_after', 1)
            if isinstance(delay, (int, float)) and 0 < delay <= 60:
                time.sleep(delay + 1)
                continue
        break
    if not response.ok or not data.get('ok'):
        raise RuntimeError(f'Telegram: richiesta fallita (HTTP {response.status_code})')
    return data


def send(cfg, text, chat_id=None):
    target = chat_id or cfg.telegram_chat_id
    if not target:
        raise RuntimeError('TELEGRAM_CHAT_ID mancante')
    # Testo semplice: niente errori HTML sui simboli del sito.
    # 1800 code point restano entro 4096 unità UTF-16 anche con emoji.
    parts, current = [], ''
    for block in text.split('\n'):
        while len(block) > 1800:
            if current:
                parts.append(current)
                current = ''
            parts.append(block[:1800])
            block = block[1800:]
        if len(current) + len(block) + 1 > 1800:
            parts.append(current)
            current = ''
        current = f'{current}\n{block}' if current else block
    if current:
        parts.append(current)
    for part in parts:
        telegram_api(cfg, 'sendMessage', {'chat_id': target, 'text': part,
                                         'disable_web_page_preview': True})


def clean(text):
    return re.sub(r'\s+', ' ', text).strip()


def links(html, categories=False):
    from bs4 import BeautifulSoup
    found = set()
    for anchor in BeautifulSoup(html, 'html.parser').select('a[href]'):
        parsed = urlparse(urljoin(INDEX, anchor['href']))
        if parsed.scheme != 'https' or parsed.netloc != 'www.mondopengwin.it':
            continue
        segments = parsed.path.strip('/').split('/')
        if (segments[:2] == ['pronostici', 'calcio']
                and len(segments) == (3 if categories else 4)
                and not segments[-1].startswith('pagina-')):
            found.add(BASE + parsed.path.rstrip('/') + ('/' if categories else ''))
    return sorted(found)


@dataclass
class Prediction:
    url: str
    match: str
    league: str
    kickoff: datetime
    text: str

    def message(self, updated=False):
        label = 'Pronostico aggiornato' if updated else 'Pronostico MondoPengwin'
        return (f'{label}\n{self.match}\n{self.league} · '
                f'{self.kickoff:%d/%m/%Y %H:%M} (Italia)\n\n'
                f'{self.text}\n\nFonte: {self.url}')


def parse_prediction(html, url):
    from bs4 import BeautifulSoup
    content = BeautifulSoup(html, 'html.parser').select_one('#pengwincontent')
    if content is None:
        raise ValueError('Scheda partita assente')
    title, info = content.select_one('h1'), content.select_one('.info_partita')
    if title is None or info is None or info.select_one('span') is None:
        raise ValueError('Partita o data assente')
    match = clean(title.get_text(' ', strip=True)).split(':')[0]
    league = clean(info.select_one('span').get_text())
    date = re.search(r'(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\s+(\d{1,2}):(\d{2})',
                     clean(info.get_text(' ', strip=True)).upper())
    if not date or date[2] not in MONTHS:
        raise ValueError('Data della partita non riconosciuta')
    kickoff = datetime(int(date[3]), MONTHS[date[2]], int(date[1]),
                       int(date[4]), int(date[5]), tzinfo=ROME)
    heading = next((h for h in content.select('h2,h3')
                    if 'PRONOSTIC' in h.get_text().upper()
                    and 'CONSIGLI' in h.get_text().upper()), None)
    if heading is None:
        raise ValueError('Sezione pronostico assente')
    blocks = []
    for node in heading.next_siblings:
        name = getattr(node, 'name', None)
        if name in ('h2', 'h3', 'div'):
            break
        if name in ('p', 'ul', 'ol'):
            text = clean(node.get_text(' ', strip=True))
            if text.lower().startswith(('per non perderti', 'se vuoi scoprire il pronostico')):
                break
            if text:
                blocks.append(text)
    text = '\n\n'.join(blocks)
    marker = re.search(r'(?:Analisi e pronostico (?:di Kristian Pengwin|della redazione)|La disamina finale)\s*[.:]?', text, re.I)
    if marker:
        text = text[marker.end():].strip()
    # Non trasmettere curiosità o una pagina ancora in preparazione come pronostico.
    if not text or re.search(r'(in aggiornamento|pronostico disponibile a breve)', text, re.I):
        return None
    if not marker:
        raise ValueError('Testo del pronostico non riconosciuto')
    return Prediction(url, match, league, kickoff, text)


class Scraper:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': 'Mozilla/5.0', 'Referer': BASE + '/'})
        retries = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount('https://', HTTPAdapter(max_retries=retries))

    def get(self, url):
        try:
            response = self.session.get(url, timeout=(5, 15))
            response.raise_for_status()
        except requests.RequestException:
            raise RuntimeError(f'Pagina MondoPengwin non raggiungibile: {url}') from None
        response.encoding = 'utf-8'
        return response.text

    def discover(self):
        # Il sito può restituire temporaneamente una pagina vuota/intermedia
        # anche con HTTP 200. La home contiene lo stesso menu dei campionati.
        categories = []
        articles = set()
        errors = []
        for attempt in range(3):
            for url in (INDEX, BASE + '/'):
                try:
                    index = self.get(url)
                    categories = links(index, categories=True)
                    articles.update(links(index))
                except RuntimeError:
                    continue
                if categories:
                    break
            if categories:
                break
            time.sleep(2 * (attempt + 1))
        if not categories:
            raise ValueError('Nessun campionato leggibile: sito non disponibile o struttura cambiata')
        for category in categories:
            try:
                articles.update(links(self.get(category)))
            except (RuntimeError, ValueError) as exc:
                errors.append(str(exc))
        if not articles:
            raise ValueError('Nessuna scheda partita trovata')
        return sorted(articles), errors


def connect(path):
    con = sqlite3.connect(path, timeout=10)
    con.execute('''CREATE TABLE IF NOT EXISTS pengwin_sent (
        url TEXT PRIMARY KEY, digest TEXT NOT NULL, kickoff TEXT NOT NULL,
        sent_at TEXT NOT NULL)''')
    con.execute('CREATE TABLE IF NOT EXISTS pengwin_lock (id INTEGER PRIMARY KEY, until REAL)')
    con.execute('INSERT OR IGNORE INTO pengwin_lock VALUES (1,0)')
    con.commit()
    return con


def run(cfg, con, now=None, scraper=None, sender=send):
    if not cfg.telegram_token or not cfg.telegram_chat_id:
        raise RuntimeError('Configura TELEGRAM_TOKEN e TELEGRAM_CHAT_ID')
    now = now or datetime.now(ROME)
    scraper = scraper or Scraper()
    urls, errors = scraper.discover()
    report = {'found': len(urls), 'sent': 0, 'unchanged': 0, 'past': 0,
              'pending': 0, 'errors': errors}
    for url in urls:
        try:
            page = scraper.get(url)
            prediction = page if isinstance(page, Prediction) else parse_prediction(page, url)
            if prediction is None:
                report['pending'] += 1
                continue
            if prediction.kickoff.date() < now.astimezone(ROME).date():
                report['past'] += 1
                continue
            # Al primo avvio include tutte le partite di oggi, anche già iniziate.
            digest = hashlib.sha256(prediction.message().encode()).hexdigest()
            previous = con.execute('SELECT digest FROM pengwin_sent WHERE url=?', (url,)).fetchone()
            if previous and previous[0] == digest:
                report['unchanged'] += 1
                continue
            sender(cfg, prediction.message(updated=bool(previous)))
            # Registrazione soltanto dopo la conferma di Telegram.
            con.execute('INSERT OR REPLACE INTO pengwin_sent VALUES (?,?,?,?)',
                        (url, digest, prediction.kickoff.isoformat(), now.isoformat()))
            con.commit()
            report['sent'] += 1
        except (RuntimeError, ValueError) as exc:
            report['errors'].append(f'{url}: {exc}')
    return report


def deliver(cfg, con, predictions, now=None, sender=send):
    """Riceve da GitHub le schede già estratte e usa lo stesso registro invii."""
    if not isinstance(predictions, list) or len(predictions) > 300:
        raise ValueError('Elenco pronostici non valido')
    pages = {}
    for data in predictions:
        if not isinstance(data, dict):
            raise ValueError('Scheda non valida')
        url = data.get('url', '')
        if not isinstance(url, str):
            raise ValueError('Fonte non valida')
        parsed = urlparse(url)
        segments = parsed.path.strip('/').split('/')
        if (parsed.scheme != 'https' or parsed.netloc != 'www.mondopengwin.it'
                or len(segments) != 4 or segments[:2] != ['pronostici', 'calcio']
                or segments[-1].startswith('pagina-') or parsed.query or parsed.fragment):
            raise ValueError('Fonte non valida')
        values = [data.get(k) for k in ('match', 'league', 'kickoff', 'text')]
        if not all(isinstance(v, str) and v.strip() and len(v) < 30000 for v in values):
            raise ValueError('Contenuto non valido')
        match, league, date, text = values
        kickoff = datetime.fromisoformat(date)
        if kickoff.tzinfo is None:
            raise ValueError('Fuso orario assente')
        pages[url] = Prediction(url, match, league, kickoff.astimezone(ROME), text)
    # Riutilizza deduplicazione e invio, senza richieste al sito dal server.
    class Received:
        def discover(self):
            return list(pages), []
        def get(self, url):
            return pages[url]
    return run(cfg, con, now, Received(), sender)
