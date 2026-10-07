# Bot scommesse a valore

Bot Telegram che:
- confronta le quote dei bookmaker italiani con il prezzo giusto di Pinnacle;
- valuta le super quote;
- dà le probabilità di una partita;
- chiude da solo le giocate leggendo i risultati.

Gira gratis su **PythonAnywhere**; lo scheduler è GitHub Actions.

> Nessuna chiave è in questo repository: stanno solo nel file `.env` sul server e nei *secret* di GitHub.

## Cosa fa

| Funzione | Come |
|---|---|
| 🟢 **Occasioni automatiche** | Ogni 15 minuti controlla i campionati con partite nelle prossime 72 ore. Se **Unibet IT** o **Codere IT** pagano almeno il 5% sopra il prezzo giusto di Pinnacle (quote fino a 5), avvisa con la puntata consigliata. |
| 🔔 **Pinnacle ha mosso** | Quando Pinnacle accorcia una quota di almeno il 4%, indica la **quota minima** da cercare sugli altri book (Snai, Sisal, Bet365… li controlli tu). |
| ✅❌ **Super quota** | `Inter vince e over 2,5 @ 3,50` → SÌ / MARGINALE / NO, con prezzo giusto, vantaggio e puntata. |
| 🎯 **Probabilità** | `Inter-Juventus` → 1X2, doppia chance, gol attesi, over/under, gol/nogol, risultati esatti. `Inter vince e over 2,5` → probabilità e quota giusta. |
| 📋 **Esiti automatici** | A fine partita legge il risultato, chiude le giocate e scrive vinta o persa. |
| 📊 **CLV e statistiche** | Registra la chiusura di Pinnacle e calcola se hai battuto il mercato. |

### Come valuta le super quote
- **Mercato singolo quotato da Pinnacle** (1, X, 2, doppia chance, over/under sulla stessa linea): probabilità di Pinnacle senza margine (metodo power). SÌ se il vantaggio è **≥ 2%**.
- **Combinazioni** (1 + over, gol/nogol, risultato esatto, multigol, scarto, gol di una squadra): un modello Dixon-Coles tarato per riprodurre 1X2 e over/under di Pinnacle. SÌ se il vantaggio è **≥ 6%**, oppure ≥ 10% se Pinnacle non ha l'over/under della partita.
- **Multiple**: si moltiplicano le probabilità delle singole partite.
- **Non valutabili**: marcatori, angoli, cartellini, primo tempo.
- **Puntata**: ¼ Kelly con tetto al 2% del bankroll.

### Comandi
```
Inter vince e over 2,5 @ 3,50     super quota (testo con la quota)
Inter-Juventus                    probabilità della partita (testo senza quota)
Inter vince e over 2,5            probabilità e quota giusta di una giocata
/prob ...                         come sopra
/listino [squadra o campionato]   quote minime da cercare nelle prossime 72 ore
/giocata S12 4,10 10              registra una giocata su un segnale (quota, puntata)
/giocata Q5 10                    registra una super quota analizzata (puntata)
/esito B3 v|p|r                   chiusura a mano (di solito non serve)
/bankroll 1000                    imposta il capitale
/stats                            segnali, giocate, profitto, CLV
/crediti                          crediti The Odds API rimasti
```

## Crediti The Odds API (piano gratuito: 500 al mese)
- Il calendario è gratis. Ogni lettura di quote costa 1 credito per campionato, 2 con gli over/under.
- Il bot divide i crediti rimasti per i giorni a fine mese e tiene **60 crediti di riserva** per super quote, probabilità e chiusure.
- **Risultati:**
  - gratis con la chiave di **football-data.org** (Serie A, Premier, Liga, Bundesliga, Ligue 1, Champions);
  - altrimenti, o per Serie B ed Europa League, costano 2 crediti, e solo se c'è una giocata aperta.

## Installazione su PythonAnywhere

1. **Codice**: in una console Bash di PythonAnywhere
   ```sh
   git clone https://github.com/gabrymark06-max/scommesse.git
   ```
2. **Configurazione**: crea `~/scommesse/.env`
   ```
   ODDS_API_KEY=...
   TELEGRAM_TOKEN=...
   TELEGRAM_CHAT_ID=...        # vuoto al primo avvio: /start al bot ti dice il numero
   SECRET=una_stringa_lunga_a_caso
   FOOTBALL_DATA_KEY=          # facoltativa, gratis su football-data.org
   ```
3. **Web app**: scheda *Web* → *Add a new web app* → *Manual configuration* → Python 3.10 o superiore. Nel file WSGI:
   ```python
   import sys
   sys.path.insert(0, "/home/TUONOME/scommesse")
   from app import app as application
   ```
   Poi *Reload*. Flask e requests sono già installati su PythonAnywhere.

   **Se la web app gratuita serve già un altro progetto**, il bot si aggiunge sotto `/scommesse/` senza toccare il resto. In fondo al file WSGI esistente:
   ```python
   import importlib.util
   from werkzeug.middleware.dispatcher import DispatcherMiddleware
   sys.path.append("/home/TUONOME/scommesse")
   _spec = importlib.util.spec_from_file_location("scommesse_app", "/home/TUONOME/scommesse/app.py")
   _mod = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(_mod)
   application = DispatcherMiddleware(application, {"/scommesse": _mod.app})
   ```
   In questo caso gli indirizzi sotto diventano `https://TUONOME.pythonanywhere.com/scommesse/...`.
4. **Collega Telegram**: apri `https://TUONOME.pythonanywhere.com/setup?secret=IL_TUO_SECRET`. Deve comparire `"ok":true`.
5. **Scheduler**: su GitHub, *Settings → Secrets and variables → Actions*, crea il secret `CRON_URL` con valore `https://TUONOME.pythonanywhere.com/cron?secret=IL_TUO_SECRET`. Il workflow `cron` lo chiama ogni 15 minuti.

**Manutenzione:** sul piano gratuito PythonAnywhere chiede di premere *Run until 3 months from today* nella scheda Web ogni 3 mesi; manda un'email prima della scadenza.

## Come usarlo: prima misurare, poi giocare
1. **Prime 6–10 settimane: non giocare i segnali.** Guarda il **CLV medio** in `/stats`.
   - Stabilmente sopra +2% dopo circa 150 segnali: la falla c'è anche su questi book.
   - Sotto +1%: non c'è.
2. **Super quote**: si possono valutare da subito.
3. **Limitazioni**: annota se un bookmaker ti riduce la puntata massima.

## Limiti noti
- Unibet IT e Codere IT non erano nel backtest (Bet365, bwin, William Hill): la fase di misura serve a verificarli.
- Le quote Pinnacle di The Odds API vengono dal sito pubblico e possono arrivare con ritardo.
- Il modello dei gol riproduce Pinnacle su 1X2 e over/under, ma per le combinazioni resta un modello: per questo la soglia è più alta.
- Le scommesse si chiudono sui 90 minuti. Partite ai supplementari senza parziale, o rinviate, vanno chiuse a mano (il bot avvisa).
- I risultati non vengono presi da diretta.it o ESPN, perché le loro condizioni d'uso vietano lo scraping.

## Sviluppo
```sh
pip install -r requirements.txt pytest
python -m pytest -q        # 24 test: modello, parser, prezzi, risultati, flusso completo
```
