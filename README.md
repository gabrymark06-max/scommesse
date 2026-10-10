# Bot MondoPengwin → Telegram

Il progetto fa soltanto scraping dei pronostici pubblici di https://www.mondopengwin.it/ e li invia nella chat Telegram configurata. Non calcola pronostici, probabilità, value bet, puntate o risultati.

Ogni 15 minuti GitHub Actions legge le sezioni di tutti i campionati collegate dalla pagina Pronostici, apre le schede attualmente elencate e invia il testo originale dell’analisi e del pronostico di Pengwin o della redazione, incluse le sezioni «La disamina finale». Ogni messaggio contiene partita, campionato, data e ora italiana e link alla fonte. Le partite dei giorni precedenti vengono escluse; al primo avvio vengono incluse tutte quelle di oggi e dei prossimi giorni, anche quelle già iniziate oggi. Gli archivi paginati delle vecchie giornate non vengono scaricati.

GitHub inoltra i pronostici a PythonAnywhere tramite POST sullo stesso `CRON_URL` già configurato. PythonAnywhere usa il token e la chat Telegram presenti nel proprio `.env`; non occorre copiarli nei secret GitHub. SQLite evita di inviare nuovamente lo stesso contenuto. Se un pronostico cambia, viene inviato come aggiornamento. Una partita viene segnata come inviata solo dopo che Telegram conferma tutti i messaggi. Se un testo molto lungo viene diviso e un invio successivo fallisce, al prossimo giro può ripetersi la prima parte. Pagine mancanti o non riconosciute vengono segnalate come errori, senza inventare selezioni. Il confronto usa il testo completo, quindi anche una correzione editoriale genera un aggiornamento.

## Aggiornare l’installazione esistente

Nella console Bash di PythonAnywhere:

```sh
cd ~/scommesse
git pull --ff-only
python -m pip install --user -r requirements.txt
```

Se la Web app utilizza un virtualenv, usare il relativo interprete e omettere `--user`. Nella scheda **Web** premere **Reload**. Il file WSGI, il webhook, il secret GitHub `CRON_URL` e il file `.env` restano compatibili. Non cambiare il database: le nuove tabelle hanno prefisso `pengwin_` e non leggono i dati delle vecchie giocate.

Il file `.env` deve contenere soltanto queste impostazioni necessarie (vedi `.env.example`):

```dotenv
TELEGRAM_TOKEN=...
TELEGRAM_CHAT_ID=...
SECRET=...
```

`DB_PATH` è facoltativo, il valore predefinito è `scommesse.db` nella cartella del progetto. Le vecchie chiavi Odds API e Football Data non vengono più usate. Le credenziali rimangono fuori da GitHub.

MondoPengwin non compare nella allowlist pubblica di PythonAnywhere free: per questo lo scraping viene eseguito su GitHub. Il server riceve le schede già estratte e contatta soltanto Telegram. Non occorre cambiare hosting. Prima di riattivare il cron GitHub, aggiornare e ricaricare la Web app: la vecchia versione non supporta POST `/cron`.

## Endpoint e Telegram

- `POST /cron?secret=...`: riceve il JSON `predictions` estratto dal runner; JSON con schede trovate, invii, duplicati, partite passate, pronostici in preparazione ed errori. Errori anche parziali restituiscono HTTP 502. `GET /cron` esegue lo scraping sullo stesso host, utile solo su hosting che consente il sito.
- `/telegram`: webhook protetto dall’header segreto Telegram; risponde solo alla chat configurata.
- `/setup?secret=...`: collega il webhook, se necessario al primo avvio. Compatibile anche con montaggio sotto `/scommesse`.
- `/start`, `/help`, `/pronostici`: descrivono l’invio automatico, non rilanciano la scansione. Se il chat ID non è configurato, `/start` lo comunica per completare `.env`.

GitHub Actions conserva il cron ogni 15 minuti. Questi orari sono indicativi: GitHub può ritardare le esecuzioni. PythonAnywhere deve essere mantenuto attivo secondo le condizioni del piano.

## Verifica

```sh
python -m pip install -r requirements.txt pytest
python -m pytest -q
```

Una scansione senza inviare a Telegram:

```python
from valuebot.bot import Config, connect, run
report = run(Config("test", "test"), connect(":memory:"), sender=lambda cfg, text: print(text))
print(report)
```
