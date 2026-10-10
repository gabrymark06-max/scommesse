"""Bot MondoPengwin, compatibile con il precedente WSGI e CRON_URL."""
import hmac
import logging
import threading
import time

from flask import Flask, abort, request
from valuebot.bot import connect, deliver, load, run, send, telegram_api

app = Flask(__name__)
cfg = load()
_cron_lock = threading.Lock()


def check(secret):
    if not cfg.secret or not hmac.compare_digest(secret or '', cfg.secret):
        abort(403)


@app.get('/cron')
@app.post('/cron')
def cron():
    check(request.args.get('secret'))
    if not _cron_lock.acquire(blocking=False):
        return {'status': 'already_running'}, 409
    con = None
    try:
        con = connect(cfg.db_path)
        # Lease SQLite: evita doppioni anche con più processi WSGI.
        with con:
            acquired = con.execute('UPDATE pengwin_lock SET until=? WHERE id=1 AND until<?',
                                   (time.time() + 1800, time.time())).rowcount
        if not acquired:
            return {'status': 'already_running'}, 409
        try:
            if request.method == 'POST':
                payload = request.get_json(silent=True)
                if not isinstance(payload, dict) or 'predictions' not in payload:
                    raise ValueError('Payload non valido')
                report = deliver(cfg, con, payload['predictions'])
            else:
                report = run(cfg, con)
            return report, (502 if report['errors'] else 200)
        finally:
            with con:
                con.execute('UPDATE pengwin_lock SET until=0 WHERE id=1')
    except (RuntimeError, ValueError) as exc:
        logging.error('Scansione fallita: %s', exc)
        return {'error': str(exc)}, 502
    finally:
        if con is not None:
            con.close()
        _cron_lock.release()


@app.post('/telegram')
def telegram():
    check(request.headers.get('X-Telegram-Bot-Api-Secret-Token'))
    message = (request.get_json(silent=True) or {}).get('message', {})
    chat_id = str(message.get('chat', {}).get('id', ''))
    if not chat_id or (cfg.telegram_chat_id and chat_id != cfg.telegram_chat_id):
        return 'ok'
    # Risponde solo ai comandi, senza interpretare giocate o quote.
    if message.get('text', '').split('@')[0].strip() not in ('/start', '/help', '/pronostici'):
        return 'ok'
    if not cfg.telegram_chat_id:
        text = f'Il tuo TELEGRAM_CHAT_ID è {chat_id}. Inseriscilo nel file .env sul server.'
    else:
        text = ('Bot MondoPengwin attivo. Riceverai automaticamente i pronostici '
                'per ogni partita di oggi e dei prossimi giorni, con testo originale '
                'e link alla fonte. Controllo ogni 15 minuti.')
    send(cfg, text, chat_id)
    return 'ok'


@app.get('/setup')
def setup():
    check(request.args.get('secret'))
    url = request.url_root.replace('http://', 'https://').rstrip('/') + '/telegram'
    return telegram_api(cfg, 'setWebhook', {
        'url': url, 'secret_token': cfg.secret, 'allowed_updates': ['message'],
    })


@app.get('/')
def home():
    return 'Bot MondoPengwin attivo'
