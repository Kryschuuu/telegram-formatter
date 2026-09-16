# Deployment auf Render.com

Diese Anleitung führt Schritt für Schritt von null zu einer laufenden
Deployment-Instanz auf [Render.com](https://render.com) — ohne tiefes
DevOps-Vorwissen.

## Voraussetzungen

1. Ein kostenloses [Render.com](https://render.com)-Konto (Anmeldung z. B.
   über GitHub).
2. Ein Telegram-Bot-Token von [@BotFather](https://t.me/BotFather)
   (öffne den Chat, sende `/newbot` und folge den Anweisungen — am Ende
   erhältst du einen Token wie `123456:ABC-...`).
3. Die Chat-ID des Ziels (optional — kann auch per Request übergeben werden).
   Deine eigene User-ID erhältst du z. B. über [@userinfobot](https://t.me/userinfobot).

## Schritt 1: Repository auf GitHub bereitstellen

Das Projekt liegt auf GitHub. Notiere dir die Repository-URL
`https://github.com/Kryschuuu/telegram-formatter`. Render kann direkt aus
diesem Repo deployen — du musst nichts lokal installieren.

## Schritt 2: Neuen Web Service anlegen

1. Logge dich bei Render ein und klicke **New + → Web Service**.
2. Verbinde dein GitHub-Konto (einmalig) und wähle das Repository
   `telegram-formatter` aus.
3. Render erkennt Python automatisch.

## Schritt 3: Build & Start konfigurieren

Setze im Formular die folgenden Werte:

| Feld | Wert |
|---|---|
| **Name** | `telegram-formatter` (frei wählbar) |
| **Environment** | `Python` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT --threads 8` |

> **Wichtig — Gunicorn-Worker & BYOB:** Die BYOB-Websessions (eigene Bot-
> Sessions über die Website, seit v2.2.0) leben **pro Prozess** im RAM. Der
> Start-Befehl darf deshalb nur **einen** Worker-Prozess verwenden; Nebenläufigkeit
> liefert `--threads` (gthread). `--workers 2` ohne Sticky-Routing würde Sessions
> im jeweils anderen Prozess „verlieren“ — der Client erhält dann eine klare
> „Session abgelaufen oder unbekannt“-Meldung (410) statt eines Fehlversands.
| **Plan** | Free (oder größer) |

> Der `Start Command` ist wichtig: Die App muss auf `0.0.0.0` und dem von
> Render vorgegebenen `PORT` lauschen, damit sie erreichbar ist.

### Schritt 3a: Alternativ — Blueprint (`render.yaml`) statt Formular

Das Repository enthält einen Render-Blueprint [`render.yaml`](../render.yaml),
der genau die Werte oben deklariert (Start-Kommando, Health-Check `/`,
`PYTHON_VERSION`, Env-Vars). **Neue** Dienste legst du damit an:
**New + → Blueprint** → Repository wählen → die nach `sync: false` gefragten
Secrets eintragen. Danach gleicht Render die Konfiguration des Dienstes bei
jedem Push automatisch an die Datei an (Blueprint-Sync).

Wichtig: Ein **bestehender**, per Hand angelegter Web Service wird von
`render.yaml` **nicht** umgestellt — Blueprint-Sync gilt nur für Dienste, die
aus dem Blueprint entstanden sind. Für den bestehenden Dienst gilt deshalb:

* Start-Kommando einmalig im Dashboard auf
  `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT` setzen
  (Settings → Service → Start Command → *Manual Deploy*), **oder**
* Dienst neu aus dem Blueprint anlegen und den alten ersetzen.

> **Warum läuft der alte Befehl `gunicorn app:app` trotzdem wieder?** In der
> Repository-Wurzel liegt ein veralteter Kompatibilitäts-Shim `app.py`, der
> ausschließlich auf `telegram_formatter.app:app` weiterleitet (enthält keine
> Logik, abgesichert durch `tests/test_app.py`). Er ist als Übergang gedacht
> und entfällt mit **3.0.0** — stelle bis dahin auf den kanonischen Befehl um.

## Schritt 4: Umgebungsvariablen setzen

Unter **Environment → Environment Variables** diese Einträge hinzufügen:

| Variable | Wert |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Dein Token von BotFather, z. B. `123456:ABC-...` |
| `TELEGRAM_CHAT_ID` | **sicherheitsrelevant:** pinnt den Zielchat von `/api/send`; abweichende Request-Werte werden abgewiesen |
| `TELEGRAM_FORMATTER_API_TOKEN` | Operator-Secret für den **authentifizierten** Shared-Versand (`X-Auth-Token`). Nicht gesetzt ⇒ `/api/send` antwortet 503, außer der Browser-Versand ist freigeschaltet (siehe nächste Zeile). Achtung: der Wert pflichtet alle POST-Endpunkte — auf Instanzen, deren Browser-Oberfläche BYOB und Live-Vorschau nutzen soll, bleibt er leer |
| `TELEGRAM_FORMATTER_SHARED_WEB_SEND` | `1` (Standard) ⇒ Besucher können den geteilten Bot im Browser benutzen (`@mdtotxt_bot` im Versandweg-Dialog), `0` ⇒ Shared-Versand nur API-authentifiziert. Wirkt ausschließlich zusammen mit gepinntem `TELEGRAM_CHAT_ID`; Grenzen: `…_SENDS_PER_MINUTE` (4/IP), `…_SENDS_PER_MINUTE_TOTAL` (30/Instanz), `…_MAX_INPUT_CHARS` (**64000**, aufgeteilt in Telegram-Chunks, Codeblöcke bleiben je Chunk wohlgeformt) |
| `TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS` | Vertrauenswürdige Proxy-Hops für Client-IP-Rate-Limits. Standard `0`; im Render-Blueprint `1`. Nur setzen, wenn der Proxy Client-Header überschreibt |
| `TELEGRAM_FORMATTER_BYOB_ENABLED` | (optional, seit v2.2.0) `0` ⇒ BYOB-Websessions & UI aus (Standard: an) |
| `TELEGRAM_FORMATTER_BYOB_TTL_SECONDS` / `…_IDLE_SECONDS` | (optional) Lebensdauer/Leerlauf der Web-Sessions (Standard 1800/600) |
| `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` | (optional) Anzeige-Name des geteilten Bots in der Privatsphäre-Warnung (Standard `@mdtotxt_bot`) |
| `TELEGRAM_FORMATTER_SHARED_CHAT_URL` | (seit v2.9.0) Öffentlicher Link zum Zielchat, z. B. `https://t.me/mdtotxt_bot_web`. Die Seite nennt diesen Kanal an sieben Stellen (Top-Warnung, Kanal-Banner, Senden-Hinweis, Bestätigungsdialog, Privatsphäre, FAQ, Footer) und verlinkt ihn. Akzeptiert: `https://t.me/<handle>`, `t.me/<handle>`, `@<handle>` — alles andere wird verworfen (dann zeigt die UI keinen Link). **Muss** zu `TELEGRAM_CHAT_ID` gehören |
| `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` | (seit v2.9.0) Tage, nach denen der Kanal Beiträge automatisch löscht (Standard `30` = ein Monat). **Muss** der Telegram-Einstellung „Nachrichten automatisch löschen" dieses Kanals entsprechen. `0` ⇒ die Seite macht keine Lösch-Aussage |
| `PYTHON_VERSION` | (optional) z. B. `3.11` |

> **Privacy-Hinweis:** Ist `TELEGRAM_BOT_TOKEN` **und** `TELEGRAM_CHAT_ID`
> gesetzt, sendet der Standard-Button in den gepinnten Chat — auf einer
> öffentlichen Instanz ein **gemeinsamer, für alle sichtbarer Chat**. Die
> Oberfläche warnt entsprechend; private Inhalte gehören in eine BYOB-Session
> (Abschnitt „Versandweg“ auf der Seite).
>
> **Entscheidung für Betreiber:** `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1`
> (Standard) macht den geteilten Bot im Browser benutzbar — bequem für eine
> öffentliche Demo-Instanz, aber jeder Besucher kann in deinen gepinnten Chat
> schreiben (durch die Rate-Limits begrenzt). Wer das nicht will: auf `0`
> setzen. Dann bleibt der Endpunkt API-only (Zustand bis v2.5.0) und die
> Website führt Browser-Nutzer konsequent zu BYOB — der Versandweg-Dialog sagt
> das auch so.

### Schritt 4b: Öffentliche Demo — Zielchat als public Supergroup/Channel

Damit die Privatsphäre-Warnung auf der Seite **wörtlich korrekt** ist („jeder,
der die Gruppe öffnet, kann den gesamten Verlauf lesen“) und dein persönliches
Postfach ruhig bleibt, richte den Demo-Zielchat als **öffentlichen** Chat ein:

1. **Gruppe oder Kanal anlegen** und `@mdtotxt_bot` hinzufügen. Bei einem
   **Kanal** braucht der Bot das Recht *Nachrichten senden* (Admin-Recht
   *Post Messages*). Für die Demo empfiehlt sich eine **public Supergroup**
   oder ein **öffentlicher Kanal** (nicht geheim) — dann ist der Verlauf auch
   für neue Leser einsehbar; bei einem nicht-öffentlichen Chat sieht den
   Verlauf nur, wer Mitglied ist.
2. **Öffentlichen Link festlegen** (Kanal/Gruppe → *Verwalten* → *Öffentlicher
   Link*, z. B. `t.me/mdtotxt_bot_web`). Dieser Link wird als
   `TELEGRAM_FORMATTER_SHARED_CHAT_URL` eingetragen — ohne ihn kann die Seite
   den Ziel-Kanal nur anonym als „öffentlichen Chat" ankündigen.
3. **Automatisches Löschen aktivieren** (Kanal/Gruppe → *Automatisch löschen*
   → z. B. *1 Monat*) und denselben Wert als
   `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` eintragen (`30`). Die Seite
   verspricht Besuchenden genau diese Frist — stimmt der Wert nicht, ist die
   Aussage falsch. Wer keine Löschung einrichtet, setzt `0`: Dann nennt die
   Seite keine Frist und die FAQ sagt ausdrücklich, dass Beiträge dauerhaft
   öffentlich bleiben.
4. **Eine Nachricht dort schreiben** und die Chat-ID ablesen (negativ, meist
   `-100…`) — z. B. über die `/getUpdates`-Antwort oder `@userinfobot`.
5. **`TELEGRAM_CHAT_ID` auf diese ID setzen** (siehe Variable oben) → der
   Dienst deployt neu und pinnt fortan diesen Chat.

Der geteilte Bot `@mdtotxt_bot` (bzw. der in
`TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` konfigurierte) darf **kein** privater
Chat sein: sonst wäre die öffentliche Sichtbarkeit nur vorgetäuscht. Mit einer
public Supergroup ist die Warnung wahr und dein Postfach (DM) bleibt für die
Demo unberührt.

> **Konsistenzpflicht (v2.9.0):** `TELEGRAM_CHAT_ID`,
> `TELEGRAM_FORMATTER_SHARED_CHAT_URL` und
> `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` beschreiben **denselben** Chat.
> Der Code kann das nicht überprüfen (er kennt nur die numerische ID) und
> loggt beim Start lediglich eine Warnung, wenn ein Kanal-Link ohne gepinnten
> Zielchat gesetzt ist. Eine Kanal-Angabe, die nicht stimmt, ist schlimmer als
> gar keine: Besuchende richten ihr Verhalten danach.

Mit **Add Variable** speichern.

## Schritt 5: Deploy starten

Klicke **Create Web Service**. Render führt automatisch den Build aus und
startet die App. Nach kurzer Zeit erscheint eine URL der Form
`https://telegram-formatter.onrender.com`.

> **Hinweis:** Beim kostenlosen Free-Plan „schläft" die Instanz nach
> Inaktivität ein; der erste Aufruf kann dann 30–60 Sekunden dauern.

## Schritt 6: Testen

1. Öffne die bereitgestellte URL im Browser — die Editor-Seite erscheint.
2. Gib z. B. ein: `**fett** und $x^2$` — die gebauten Payloads werden
   angezeigt.
3. Klicke **An Telegram senden** — der Bestätigungsdialog zeigt den
   Versandweg. Ohne eigene Bot-Session ist der geteilte Bot
   (`@mdtotxt_bot`) ausgewählt: die Nachricht landet im **öffentlichen Kanal**
   (`TELEGRAM_FORMATTER_SHARED_CHAT_URL`, z. B. `t.me/mdtotxt_bot_web`) — der
   Dialog nennt ihn als klickbaren Link samt Löschfrist. Prüfe nach dem
   Senden, ob die Nachricht wirklich in diesem Kanal auftaucht. Mit eigener
   Session (BYOB) steht dein Bot oben und sendet privat; beide Wege lassen
   sich im Dialog umschalten.

Alternativ per Kommandozeile (Dry-Run zeigt nur die Payloads):

```bash
python -m telegram_formatter.cli beispiel_input.txt  # Dry-Run
TELEGRAM_BOT_TOKEN=... python -m telegram_formatter.cli beispiel_input.txt --send --chat-id <CHAT_ID>
```

## Lokale Entwicklung (optional)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
flask --app telegram_formatter.app run   # http://127.0.0.1:5000
pytest -q                          # Tests ausführen
```

## Troubleshooting

- **`ModuleNotFoundError: No module named 'app'`** — das Start-Kommando zeigt
  auf das alte Root-Modul, während der Shim `app.py` fehlt (z. B. nach seiner
  Entfernung in 3.0.0). Build und Start sind zwei Schritte: Render meldet
  `== Build successful`, crasht aber im `== Running`-Abschnitt. Abhilfe:
  Start-Kommando auf `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT`
  stellen (siehe Schritt 3/3a) und **Manual Deploy** auslösen.
- **„TELEGRAM_BOT_TOKEN nicht konfiguriert"** — Variable in Schritt 4
  fehlt oder ist falsch; nach dem Setzen **Manual Deploy** auslösen.
- **App startet nicht** — Logs unter **Logs** im Render-Dashboard prüfen.
- **`message is too long`** — sollte nicht auftreten; die App teilt lange
  Nachrichten automatisch auf (4096 bzw. 32768 Zeichen).
- **Port-Konflikt** — immer `$PORT` aus der Umgebung verwenden (wie im
  Start Command oben).
