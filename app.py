"""App web per PythonAnywhere.

/telegram  webhook di Telegram (protetto dall'header segreto)
/cron      lavoro programmato, da chiamare ogni 15 minuti (es. cron-job.org) con ?secret=...
/setup     collega il bot Telegram a questo indirizzo, una volta sola, con ?secret=...
"""
import hmac
import threading
import time
import traceback

from flask import Flask, abort, request

from valuebot import config, db
from valuebot.commands import handle_message
from valuebot.scan import run_scheduled
from valuebot.telegram import set_webhook

app = Flask(__name__)
cfg = config.load()
_cron_lock = threading.Lock()


def _check(secret):
    if not cfg.secret or not hmac.compare_digest(secret or "", cfg.secret):
        abort(403)


def _now():
    return int(time.time() * 1000)


@app.post("/telegram")
def telegram():
    _check(request.headers.get("X-Telegram-Bot-Api-Secret-Token"))
    update = request.get_json(silent=True) or {}
    if "message" in update:
        con = db.connect(cfg.db_path)
        try:
            handle_message(cfg, con, update["message"], _now())
        except Exception:
            traceback.print_exc()
        finally:
            con.close()
    return "ok"


@app.get("/cron")
def cron():
    _check(request.args.get("secret"))
    if not _cron_lock.acquire(blocking=False):
        return "già in esecuzione"
    con = db.connect(cfg.db_path)
    try:
        alerts = run_scheduled(cfg, con, _now())
        return f"ok, {len(alerts)} messaggi"
    finally:
        con.close()
        _cron_lock.release()


@app.get("/setup")
def setup():
    _check(request.args.get("secret"))
    url = request.url_root.replace("http://", "https://").rstrip("/") + "/telegram"
    return set_webhook(cfg, url)


@app.get("/")
def home():
    return "bot scommesse attivo"
