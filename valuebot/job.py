"""Scraping sul runner GitHub, inoltro al server con CRON_URL esistente."""
import os
from dataclasses import asdict
from datetime import datetime

import requests

from .bot import ROME, Scraper, parse_prediction


def collect(scraper=None, now=None):
    scraper = scraper or Scraper()
    today = (now or datetime.now(ROME)).astimezone(ROME).date()
    urls, errors = scraper.discover()
    predictions = []
    past = pending = 0
    for url in urls:
        try:
            prediction = parse_prediction(scraper.get(url), url)
            if prediction is None:
                pending += 1
            elif prediction.kickoff.date() < today:
                past += 1
            else:
                data = asdict(prediction)
                data['kickoff'] = prediction.kickoff.isoformat()
                predictions.append(data)
        except (RuntimeError, ValueError) as exc:
            errors.append(f'{url}: {exc}')
    return predictions, {'found': len(urls), 'past': past, 'pending': pending, 'errors': errors}


def main():
    url = os.getenv('CRON_URL', '')
    if not url.startswith('https://'):
        raise SystemExit('CRON_URL mancante o non valido')
    predictions, report = collect()
    print('Scraping:', report)
    # Invia anche in caso di errore parziale, poi segnala il fallimento alla CI.
    try:
        response = requests.post(url, json={'predictions': predictions}, timeout=(10, 900))
        result = response.json()
    except (requests.RequestException, ValueError):
        raise SystemExit('Server non raggiungibile o risposta non valida') from None
    print('Consegna:', result)
    if not response.ok or report['errors']:
        raise SystemExit('Scansione o consegna incompleta: vedere gli errori sopra')


if __name__ == '__main__':
    main()
