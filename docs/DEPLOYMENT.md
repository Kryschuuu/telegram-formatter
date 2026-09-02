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
| **Start Command** | `gunicorn app:app --bind 0.0.0.0:$PORT` |
| **Plan** | Free (oder größer) |

> Der `Start Command` ist wichtig: Die App muss auf `0.0.0.0` und dem von
> Render vorgegebenen `PORT` lauschen, damit sie erreichbar ist.

## Schritt 4: Umgebungsvariablen setzen

Unter **Environment → Environment Variables** diese Einträge hinzufügen:

| Key | Wert |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Dein Token von BotFather, z. B. `123456:ABC-...` |
| `TELEGRAM_CHAT_ID` | (optional) Standard-Ziel-Chat, z. B. `-100123456789` |
| `PYTHON_VERSION` | (optional) z. B. `3.11` |

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
3. Klicke **An Telegram senden**, um die Nachricht tatsächlich zu versenden.

Alternativ per Kommandozeile (Dry-Run zeigt nur die Payloads):

```bash
python cli.py beispiel_input.txt                     # Dry-Run
python cli.py beispiel_input.txt --send --token <TOKEN> --chat-id <CHAT_ID>
```

## Lokale Entwicklung (optional)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
flask --app app run                # http://127.0.0.1:5000
pytest -q                          # Tests ausführen
```

## Troubleshooting

- **„TELEGRAM_BOT_TOKEN nicht konfiguriert"** — Variable in Schritt 4
  fehlt oder ist falsch; nach dem Setzen **Manual Deploy** auslösen.
- **App startet nicht** — Logs unter **Logs** im Render-Dashboard prüfen.
- **`message is too long`** — sollte nicht auftreten; die App teilt lange
  Nachrichten automatisch auf (4096 bzw. 32768 Zeichen).
- **Port-Konflikt** — immer `$PORT` aus der Umgebung verwenden (wie im
  Start Command oben).
