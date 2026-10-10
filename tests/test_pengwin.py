from datetime import datetime

import pytest

import app as web
from valuebot import bot

URL = bot.INDEX + 'serie-a/inter-parma-statistiche-quote-e-pronostico'


def page(text='Scelgo 2 Handicap (0:4), quota 1.35.', date='10 OTT 2026'):
    return f'''<div id="pengwincontent">
    <h1>INTER-PARMA: STATISTICHE, QUOTE E PRONOSTICO</h1>
    <div class="info_partita"><span>SERIE A</span> {date} <strong>18:00</strong></div>
    <h2>COMPARAZIONE QUOTE</h2><p>1: 1.10, X: 7.50</p>
    <h2>ECCO I PRONOSTICI E I CONSIGLI PIU' INTERESSANTI</h2>
    <p><p>Numeri e curiosità: statistiche</p>
    <p><strong>Analisi e pronostico di Kristian Pengwin:</strong></p>
    <p>{text}</p></p>
    <p>Per non perderti tutti i consigli iscriviti a Telegram</p>
    <div><h2>Comparatore bonus</h2><p>Promozioni</p></div></div>'''


class FakeScraper:
    html = page()

    def discover(self):
        return [URL], []

    def get(self, url):
        return self.html


def test_extracts_original_only():
    p = bot.parse_prediction(page(), URL)
    assert p.match == 'INTER-PARMA'
    assert p.text == 'Scelgo 2 Handicap (0:4), quota 1.35.'
    assert p.kickoff == datetime(2026, 10, 10, 18, tzinfo=bot.ROME)
    assert 'Fonte: ' + URL in p.message()


@pytest.mark.parametrize('marker', ['La disamina finale', 'Analisi e pronostico della redazione'])
def test_editorial_prediction(marker):
    html = page().replace('Analisi e pronostico di Kristian Pengwin', marker)
    assert bot.parse_prediction(html, URL).text == 'Scelgo 2 Handicap (0:4), quota 1.35.'


def test_links_ignore_pagination_and_external():
    html = f'<a href="{URL}"></a><a href="{URL}"></a>'
    html += f'<a href="{bot.INDEX}serie-a/pagina-2"></a>'
    html += '<a href="https://evil.example/pronostici/calcio/a/b"></a>'
    assert bot.links(html) == [URL]


def test_missing_and_pending():
    with pytest.raises(ValueError):
        bot.parse_prediction('<h1>challenge</h1>', URL)
    assert bot.parse_prediction(page('In aggiornamento'), URL) is None


def test_duplicate_update_and_failed_send(tmp_path):
    cfg = bot.Config('token', 'chat', 'secret', str(tmp_path / 'bot.db'))
    con = bot.connect(cfg.db_path)
    s = FakeScraper()
    now = datetime(2026, 10, 10, 20, tzinfo=bot.ROME)
    sent = []
    sender = lambda cfg, text: sent.append(text)
    assert bot.run(cfg, con, now, s, sender)['sent'] == 1
    assert bot.run(cfg, con, now, s, sender)['unchanged'] == 1
    s.html = page('Aggiornato: 1X a quota 1.51.')
    def failure(cfg, text):
        raise RuntimeError('Telegram offline')
    assert bot.run(cfg, con, now, s, failure)['errors']
    assert bot.run(cfg, con, now, s, sender)['sent'] == 1
    assert sent[-1].startswith('Pronostico aggiornato')
    s.html = page(date='09 OTT 2026')
    assert bot.run(cfg, con, now, s, sender)['past'] == 1
    con.close()


def test_telegram_splits_large_blocks_and_checks_ok(monkeypatch):
    calls = []
    monkeypatch.setattr(bot, 'telegram_api', lambda cfg, method, data: calls.append(data))
    bot.send(bot.Config('token', 'chat'), '😀' * 5000 + '\nA & B < 3')
    assert len(calls) >= 3
    assert all(len(c['text'].encode('utf-16-le')) // 2 <= 4096 for c in calls)
    assert all('parse_mode' not in c for c in calls)


def test_telegram_api_rejects_unsuccessful_200(monkeypatch):
    class Response:
        ok = True
        status_code = 200
        def json(self):
            return {'ok': False}
    monkeypatch.setattr(bot.requests, 'post', lambda *a, **kw: Response())
    with pytest.raises(RuntimeError):
        bot.telegram_api(bot.Config('token'), 'sendMessage', {})


def test_web_auth_cron_and_foreign_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(web, 'cfg', bot.Config('token', '123', 'secret', str(tmp_path / 'web.db')))
    calls = []
    monkeypatch.setattr(web, 'send', lambda *a: calls.append(a))
    monkeypatch.setattr(web, 'run', lambda cfg, con: {'sent': 2, 'errors': []})
    c = web.app.test_client()
    assert c.get('/cron').status_code == 403
    assert c.get('/cron?secret=secret').json['sent'] == 2
    assert c.post('/telegram', json={}).status_code == 403
    c.post('/telegram', headers={'X-Telegram-Bot-Api-Secret-Token': 'secret'},
           json={'message': {'chat': {'id': 999}, 'text': '/start'}})
    assert not calls
    monkeypatch.setattr(web, 'run', lambda cfg, con: {'sent': 1, 'errors': ['page failed']})
    assert c.get('/cron?secret=secret').status_code == 502


def test_delivery_from_github_and_invalid_source(tmp_path):
    from dataclasses import asdict
    cfg = bot.Config('token', 'chat', 'secret', str(tmp_path / 'delivery.db'))
    con = bot.connect(cfg.db_path)
    data = asdict(bot.parse_prediction(page(), URL))
    data['kickoff'] = data['kickoff'].isoformat()
    sent = []
    now = datetime(2026, 10, 10, 20, tzinfo=bot.ROME)
    sender = lambda cfg, text: sent.append(text)
    assert bot.deliver(cfg, con, [data], now, sender)['sent'] == 1
    assert bot.deliver(cfg, con, [data], now, sender)['unchanged'] == 1
    with pytest.raises(ValueError):
        bot.deliver(cfg, con, [{**data, 'url': 'https://evil.example/a'}], now, sender)
    assert len(sent) == 1
    con.close()


def test_collect_on_runner():
    from valuebot.job import collect
    predictions, report = collect(FakeScraper(), datetime(2026, 10, 10, tzinfo=bot.ROME))
    assert not report['errors']
    assert predictions[0]['text'] == 'Scelgo 2 Handicap (0:4), quota 1.35.'
    assert predictions[0]['kickoff'].endswith('+02:00')
