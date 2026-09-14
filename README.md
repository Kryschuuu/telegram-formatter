# telegram-formatter

Konvertiert **Markdown mit LaTeX-Formeln und Tabellen** in sendefertige
Telegram-Nachrichten — mit korrektem LaTeX-Rendering, Telegram-Formatierung
(Fett, Kursiv, Unterstrichen, Code, …) und automatischer Aufteilung langer
Nachrichten.

![Version](https://img.shields.io/badge/version-2.8.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license/GPLv3-lightgrey)

## Funktionsübersicht

- **LaTeX-Konvertierung** — Unterstützt mehrere Delimiter-Syntaxe:
  - Klassisch: `$…$` (Inline) und `$$…$$` (Display)
  - DeepSeek/Gemini: `\(…\)` und `\[…\]` — werden automatisch in die
    Telegram-Syntax `$…$`/`$$…$$` umgewandelt
  
  Verschachtelte Strukturen wie `$\binom{\binom{70}{6}}{33}$` und
  Spezialsymbole (`\sum`, `\int`, `\alpha`, …) bleiben intakt. Kein
  Zerschneiden durch naive Regex.
- **Telegram-Formatierung** — Fett `**x**`, Kursiv `*x*`/`_x_`,
  Unterstreichen `__x__`, Durchgestrichen `~~x~~`, Inline-Code `` `x` ``,
  Codeblöcke, Links, Überschriften, Listen und Blockquotes. Vollständige
  Gegenüberstellung von Telegram-Features, LLM-Ausgabeformaten und
  Implementierungsstand: [docs/FORMATTING.md](docs/FORMATTING.md).
- **Redirect-URLs werden entpackt (seit v2.7.0)** — Links aus Such-KIs und
  sozialen Netzwerken führen oft über Tracking-Adressen
  (`google.com/search?q=<percent-kodiertes Ziel>`, `youtube.com/redirect?q=…`,
  `l.facebook.com/l.php?u=…`, …). Der Parser extrahiert die eigentliche
  Ziel-URL (rekursiv, schema-geschützt); Link-Artefakte wie
  `[text]([url](url))` werden geglättet.
- **Tabellen** — Pipe-Tabellen werden in native Rich-Markdown-Tabellen
  übersetzt (GFM).
- **Automatisches Splitting** — Nachrichten werden an Absatz-, Zeilen- und
  Wortgrenzen aufgeteilt (4096 Zeichen für klassische, 32768 für Rich
  Messages), ohne Formatierungen oder Tabellen zu zerreißen.
- **Zwei Versandwege, frei wählbar (seit v2.6.0)** — der Bestätigungsdialog
  stellt den Weg aus, der tatsächlich offen ist: **geteilter Bot**
  `@mdtotxt_bot` in den öffentlichen Demo-Chat (ohne Einrichtung, nur für
  öffentliche Inhalte) oder **eigener Bot** in den privaten Ziel-Chat. Ist
  eine eigene Session aktiv, steht sie vorausgewählt ganz oben; ohne Session
  ist `@mdtotxt_bot` wählbar.
- **Eigener Bot (dezentral, v1.3.0 / Web-Session seit v2.2.0)** — BYOB über
  `telegram_formatter/botkit` direkt **auf der Website** (Token + Chat-ID im
  Formular → ephemere RAM-Session) oder das `botctl`-CLI:
  eigenen Bot registrieren, reviewen und in einer ephemeren Session
  nutzen — ohne zentrale Datenspeicherung.
- **Unicode-sicher** — NFC-Normalisierung für Diakritika wie `ì`.
- **Komfortable Web-Oberfläche** (v1.1.0, Redesign 2026-09) — Live-Vorschau
  und Payloads in Echtzeit, Zeichenzähler, Reset-Button („Zurücksetzen“),
  Sticky-Header, Schritt-für-Schritt-Howto, aufklappbares FAQ,
  Disclaimer-Hinweisbox und Buy-me-a-coffee-Link (Header & Footer).
  Vollständig selbst-gehostetes Design-System (keine CDNs) mit **vier Themes
  plus Auto-Modus** (Light · Dark · Colorful · Minimal) und
  Theme-Switcher mit localStorage-Persistenz. Details:
  [docs/DESIGN.md](docs/DESIGN.md).

## Warum zwei Pfade?

Klassische Telegram-Nachrichten (`sendMessage`) unterstützen **kein LaTeX und
keine Tabellen** und sind auf 4096 Zeichen begrenzt. Telegram bietet seit der
Bot API 10.1 (2026) **Rich Messages** (`sendRichMessage`), die LaTeX,
Tabellen und bis zu 32768 Zeichen nativ unterstützen. Das Projekt wählt den
Pfad automatisch anhand des Inhalts:

| Inhalt | Pfad | Methode |
|---|---|---|
| Reiner Formatierungstext | Regular | `sendMessage` + `parse_mode="HTML"` |
| Enthält LaTeX oder Tabelle | Rich | `sendRichMessage` + Feld `markdown` |

## Zwei Versandwege — wer sendet, wer liest mit?

| Weg | Endpoint | Ziel | Sichtbarkeit | Einrichtung |
|---|---|---|---|---|
| **Geteilter Bot** `@mdtotxt_bot` | `POST /api/send` | der auf `TELEGRAM_CHAT_ID` **gepinnte**, öffentliche Chat des Betreibers (public Supergroup/Channel) | öffentlich — alle Besucher der Instanz und jeder, der die Gruppe/den Kanal auf Telegram öffnet, lesen den **gesamten** Verlauf mit (auch nachträglich) | keine |
| **Eigener Bot (BYOB)** | `POST /api/byob/send` | dein eigener Ziel-Chat | privat — nur Mitglieder deines Chats | Token + Chat-ID, Session max. 30 min |

Der Sende-Knopf öffnet zuerst den Bestätigungsdialog: dort wird der Weg
angezeigt (und bei aktivierter Session umgeschaltet), dann erst wird gesendet.
Der geteilte Weg ist an drei Bedingungen geknüpft — sonst bleibt er zu:

1. `TELEGRAM_BOT_TOKEN` **und** `TELEGRAM_CHAT_ID` sind gesetzt (der Zielchat
   ist gepinnt, `chat_id`-Overrides aus dem Request werden abgewiesen),
2. `TELEGRAM_FORMATTER_SHARED_WEB_SEND` ist nicht auf `0` gestellt,
3. der Request bestätigt die öffentliche Sichtbarkeit mit
   `"confirm_public": true` (im Browser holt der Dialog diese Bestätigung ein).

## Setup

### Voraussetzungen

- Python 3.10+
- Ein Telegram-Bot-Token von [@BotFather](https://t.me/BotFather)

### Installation

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt  # oder: pip install -e . (dann inkl. CLI-Befehle)
```

### Konfiguration

Setze die Umgebungsvariablen (Token am besten ohne Shell-Historie eintragen):

```bash
read -rsp 'TELEGRAM_BOT_TOKEN: ' TELEGRAM_BOT_TOKEN && echo && export TELEGRAM_BOT_TOKEN
export TELEGRAM_CHAT_ID="-100123456789"   # optional
```

Für den Web-Betrieb zusätzlich möglich (seit v2.1.0):

| Variable | Wirkung |
|---|---|
| `TELEGRAM_CHAT_ID` | **pinnt** den Zielchat von `/api/send`; ein abweichender Request-Wert wird abgewiesen. Für die öffentliche Demo empfiehlt sich eine **public Supergroup** oder ein **Channel** mit dem Bot als Mitglied (Recht *Nachrichten senden*) — so ist die Warnung auf der Seite wörtlich korrekt und jede Nachricht für neue Leser im Verlauf einsehbar |
| `TELEGRAM_FORMATTER_API_TOKEN` | Operator-Secret für den **authentifizierten** Shared-Versand. Bei gesetztem Wert verlangen alle POST-Endpunkte den Header `X-Auth-Token` — nur `POST /api/send` bleibt offen, wenn zusätzlich `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1` gilt (sonst wäre der Browser-Weg widersprüchlich: er darf das Secret nie erhalten). Ohne dieses Secret ist `/api/send` deaktiviert (503) |
| `TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS` | Anzahl vertrauenswürdiger Proxy-Hops für Client-IP-Erkennung; Standard `0` (sicherer Direktbetrieb), Render-Blueprint setzt `1` |
| `TELEGRAM_FORMATTER_MAX_INPUT_CHARS` | Eingabelimit (Standard 100000) |
| `TELEGRAM_FORMATTER_SENDS_PER_MINUTE` | Rate-Limit pro IP für `/api/send` (Standard 6) |
| `TELEGRAM_FORMATTER_BYOB_ENABLED` | BYOB-Websessions aktiv (Standard `1`; `0` blendet UI + API aus) |
| `TELEGRAM_FORMATTER_BYOB_SESSIONS_PER_MINUTE` | Session-Öffnungen pro IP (Standard 3) |
| `TELEGRAM_FORMATTER_BYOB_DISCOVER_PER_MINUTE` | Chat-ID-Erkennungen pro IP (Standard 3) |
| `TELEGRAM_FORMATTER_BYOB_SENDS_PER_MINUTE` | Sendungen über eigene Sessions pro IP (Standard 6) |
| `TELEGRAM_FORMATTER_BYOB_TTL_SECONDS` | Lebensdauer einer Web-Session (Standard 1800) |
| `TELEGRAM_FORMATTER_BYOB_IDLE_SECONDS` | Leerlauf-Timeout einer Web-Session (Standard 600) |
| `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` | Anzeige-Name des geteilten Bots in der Warnung (Standard `@mdtotxt_bot`) |
| `TELEGRAM_FORMATTER_SHARED_WEB_SEND` | Geteilter Bot im Browser nutzbar (Standard `1`). Wirkt nur zusammen mit gepinntem `TELEGRAM_CHAT_ID`; `0` = API-only wie in 2.5.0, der Browser sendet dann ausschließlich über BYOB |
| `TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE` | Anonyme Browser-Sendungen pro IP (Standard 4) |
| `TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE_TOTAL` | Anonyme Browser-Sendungen pro Minute **instanzweit** (Standard 30) |
| `TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS` | Längenkappe für anonyme Browser-Sendungen (Standard 8000; authentifizierte Aufrufe nutzen `TELEGRAM_FORMATTER_MAX_INPUT_CHARS`) |

Hintergrund der Härtungen: [SECURITY_AUDIT.md](SECURITY_AUDIT.md).

**Shared-Versand aus dem Browser** (Standardfall der gehosteten Instanz):
`POST /api/send` nimmt den Text anonym an und legt ihn in den gepinnten Chat —
öffentliche Sichtbarkeit mit `confirm_public` bestätigt, kürzere Längenkappe
und engere Frequenzlimits als der API-Weg:

```bash
curl -X POST https://example.invalid/api/send \
  -H 'Content-Type: application/json' \
  --data '{"text":"öffentliche Testnachricht","confirm_public":true}'
```

**Shared-Versand serverseitig** (z. B. aus Skripten oder wenn
`TELEGRAM_FORMATTER_SHARED_WEB_SEND=0` gilt): `TELEGRAM_CHAT_ID` und
`TELEGRAM_FORMATTER_API_TOKEN` setzen, Token ausschließlich über `X-Auth-Token`
übermitteln — der Browser erhält dieses Secret nie:

```bash
curl -X POST https://example.invalid/api/send \
  -H 'Content-Type: application/json' \
  -H 'X-Auth-Token: <operator-secret>' \
  --data '{"text":"öffentliche Testnachricht"}'
```

Der Wert `session_secret` aus `POST /api/byob/session` ist ein zusätzlicher
Proof-of-Possession-Wert für `/api/byob/send`, `/api/byob/status` und
`/api/byob/close`. Er wird wie die Session-ID nur flüchtig im Browser gehalten
und darf nicht geloggt oder persistiert werden.

## Nutzungsbeispiele

### Kommandozeile

```bash
# Dry-Run: zeigt nur die gebauten API-Payloads (kein Token nötig)
python -m telegram_formatter.cli beispiel_input.txt

# Aus STDIN lesen
echo "**fett** und $x^2$" | python -m telegram_formatter.cli

# Wirklich senden (Token ausschließlich aus der Umgebungsvariable —
# der frühere --token-Parameter ist entfernt, er lag in ps & Shell-Historie)
TELEGRAM_BOT_TOKEN=... python -m telegram_formatter.cli beispiel_input.txt --send --chat-id -100123456789
```

*(Nach `pip install -e .` stehen zusätzlich die Befehle `telegram-formatter`
und `botctl` zur Verfügung.)*

### Web-Oberfläche

```bash
flask --app telegram_formatter.app run   # http://127.0.0.1:5000
```

Im Browser Markdown/LaTeX eingeben, die Payloads in Echtzeit prüfen und
optional direkt senden — wahlweise über eine eigene Bot-Session (BYOB,
empfohlen) oder den geteilten Bot (öffentlich, siehe unten). Vor jedem
Versand erscheint eine **Bestätigung**, die den konkreten Bot und das Ziel
anzeigt und sich abbrechen lässt.

### Beispiel-Eingabe

```markdown
# Überschrift

**Fett**, *kursiv*, __unterstrichen__ und `code`.

Formel: $E = mc^2$ und $\binom{\binom{70}{6}}{33}$.

## Tabelle

n | L(n,6,6,2) | Quelle
---|-----------|--------
20 | 10 | [Thm 3.1]
```

### DeepSeek/Gemini-Syntax

KI-Tools wie DeepSeek und Gemini verwenden eine andere LaTeX-Syntax.
telegram-formatter erkennt sie und wandelt sie automatisch um, denn Telegram
rendert ausschließlich `$…$` und `$$…$$`:

```markdown
Die gespeicherte Energie \(E\) eines Kondensators beträgt:

\[
E = \frac{1}{2} C U^2
\]
```

wird zu:

```markdown
Die gespeicherte Energie $E$ eines Kondensators beträgt:

$$
E = \frac{1}{2} C U^2
$$
```

Beide Syntaxformen (`$...$`/`$$...$$` und `\(...\)`/`\[...\]`) werden erkannt
und als Rich-Message versendet. Der Formelinhalt bleibt dabei unverändert;
Formeln in Code-Blöcken werden nicht angefasst.

### Als Bibliothek

```python
from telegram_formatter import build_messages, send_message

for msg in build_messages("**fett** und $x^2$", chat_id="-100123456789"):
    send_message(msg, bot_token="123456:ABC")
```


## Versand-Wege auf der Website (ab v2.2.0)

Die Website kennt zwei Versand-Wege — der Editor wählt automatisch den
aktiven:

| | Geteilter Bot (Standard) | Eigener Bot — BYOB (empfohlen) |
|---|---|---|
| Einrichtung | keine | @BotFather → Token + Chat-ID (ca. 3 Min.) |
| Absender | `@mdtotxt_bot` (bzw. konfigurierter Bot) | dein eigener Bot |
| Sichtbarkeit | ⚠ **gemeinsamer Chat — alle Besucher sehen alles**, auch nachträglich | nur dein Ziel-Chat |
| Token-Lagerung | Server-Environment | **nur RAM der Session** (max. 30 Min., dann verworfen) |
| Rate-Limits | geteilt mit allen | eigenes Session-Limit (20/Min.) + IP-Limits |
| Chat-ID finden | entfällt | „Chat-ID erkennen“ (`getUpdates`-Blick, nur Metadaten) |

**Wichtig (Privatsphäre):** Solange keine eigene Session läuft, sendet der
Senden-Button über den geteilten Bot in den gemeinsamen Chat — die
Oberfläche warnt mehrfach: als **Top-Warnung ganz oben auf der Seite**, am
Button selbst und **im Bestätigungsdialog vor jedem Versand** (der den
konkreten Bot, das Ziel und eine Vorschau zeigt und abgebrochen werden
kann). Ein eigener Aufklärungs-Abschnitt („Privatsphäre“) erklärt außerdem,
was @BotFather und Bots in Bezug auf Sichtbarkeit bedeuten. Für private
Inhalte: eigene Bot-Session starten. Der Ablauf:

1. **Bot anlegen:** [@BotFather](https://t.me/BotFather) → `/newbot` → Token kopieren.
2. **Chat-ID ermitteln:** [@userinfobot](https://t.me/userinfobot) für die eigene ID
   oder „Chat-ID erkennen“ (deinem Bot kurz eine Nachricht senden, dann
   klicken — der Server liest nur Chat-Metadaten aus `getUpdates`).
3. **Session starten:** Token + Chat-ID ins Formular, Hinweis bestätigen —
   der Senden-Button versendet danach über deinen Bot. Statusanzeige zeigt
   Bot, Chat und Restzeit; „Session beenden“ verwirft das Token sofort.

API-seitig: `POST /api/byob/session` · `POST /api/byob/discover` ·
`POST /api/byob/send` · `POST /api/byob/status` · `POST /api/byob/close`
(Details im Modul-Docstring `telegram_formatter/app.py` und in
[docs/DECENTRAL_BOT_ARCHITECTURE.md](docs/DECENTRAL_BOT_ARCHITECTURE.md)).
Betriebshinweis: Sessions leben prozesslokal im RAM — Deployment mit **einem**
Gunicorn-Worker plus `--threads` betreiben (siehe `render.yaml`).

## Eigener Bot statt Zentral-Bot (dezentral, ab v1.3.0)

Standardmäßig sendet diese Anwendung über **einen** konfigurierten Bot
(`TELEGRAM_BOT_TOKEN`). Wer seine Nachrichten nicht über fremde Infrastruktur
laufen lassen will, nutzt stattdessen den eigenen Bot: **auf der Website**
(Sektion „Versandweg“, siehe oben) oder über die CLI — Registrierung, Review
und Session laufen lokal bzw. in einer ephemeren Session, **ohne
Datenspeicherung**. Für eigenen Bot-Code, der tatsächlich ausgeführt wird,
bleibt das Review-Gate Pflicht (Statik + Vier-Augen-Prinzip); in der
Web-Session läuft ausschließlich der geprüfte Code dieses Projekts.

```bash
# 1) Eigener Bot (Token von @BotFather) verifizieren — Token wird nicht gespeichert
python -m telegram_formatter.botctl register --owner <dein-handle>

# 2) Bot-Code statisch prüfen und Review-Ticket anlegen
python -m telegram_formatter.botctl review examples/own_bot/minimal_bot.py --bot-id <deine-bot-id>

# 3) Zwei Freigaben (Vier-Augen-Prinzip, mindestens eine Maintainer:in)
python -m telegram_formatter.botctl approve <TICKET> --reviewer alice --role maintainer \
    --checks C1,C2,C3,C4,C5,C6,C7,C8,C9
python -m telegram_formatter.botctl approve <TICKET> --reviewer bob --role contributor \
    --checks C1,C2,C3,C4,C5,C6,C7,C8,C9

# 4) In einer Session über den eigenen Bot senden (Dry-Run ohne --send)
python -m telegram_formatter.botctl send --chat-id -1001234567890 --file eingabe.md \
    --bot-source examples/own_bot/minimal_bot.py --send
```

Kernpunkte des Designs:

- **Keine Persistenz** — Tokens leben nur im RAM (`BotToken` + Vault mit TTL),
  Sessions enden nach TTL/Leerlauf; es wird nichts auf die Platte geschrieben.
- **Keine Inhalte in Logs** — geloggt werden ausschließlich Metadaten
  (Anzahl Chunks, Zeichenlänge, Fingerprints); ein `RedactingFilter` bereinigt
  auch Fremdlogger wie `urllib3`.
- **Review vor Deployment** — AST-Regeln (BK001–BK012), Checkliste (C1–C9),
  zwei Freigaben, Freigabe gebunden an die SHA-256-Prüfsumme des Codes.

Details: [Dezentrale Bot-Architektur](docs/DECENTRAL_BOT_ARCHITECTURE.md) ·
Review-Checkliste: `python -m telegram_formatter.botctl checklist`

## Tests & Qualitätssicherung

```bash
pip install -r requirements-dev.txt
pytest -q                                  # 360+ Tests
ruff check .                               # Stil & offensichtliche Fehler
bandit -c pyproject.toml -r telegram_formatter -ll   # Sicherheits-Scan
```

Der komplette Prüf-Matrix (inkl. Secret-Scan, pip-audit, Review-Gate) ist in
[audit/README.md](audit/README.md) dokumentiert und als CI-Workflow
[`.github/workflows/bot-review.yml`](.github/workflows/bot-review.yml) automatisiert.

## Projektstruktur

Seit v2.0.0 ist das Repository nach **Zweck** geschichtet: Laufzeitcode als
Paket, Doku unter `docs/`, Prüf- und Audit-Artefakte in `peer-review/`,
`audit/` und `security/` (Details: [MIGRATION.md](MIGRATION.md)).

```
telegram-formatter/
├── app.py                      # ⚠ veralteter Deploy-Shim: reine Weiterleitung an
│                               #   telegram_formatter.app (entfällt mit 3.0.0)
├── telegram_formatter/         # ← gesamter Laufzeitcode als Python-Paket
│   ├── app.py                  #   Flask-Weboberfläche (WSGI-Einstieg)
│   ├── cli.py                  #   Kommandozeilen-Einstieg
│   ├── utils.py                #   Konvertierungs- & Splitting-Logik (pure)
│   ├── sender.py               #   HTTP-Versand an die Telegram-API
│   ├── botctl.py               #   CLI für eigene Bots (register/review/approve/send)
│   ├── templates/index.html    #   Editor-Seite (inkl. BYOB-Sektion)
│   ├── static/                 #   selbst-gehostetes UI (keine CDNs, CSP 'self')
│   │   ├── css/                #     tokens → base → layout → components
│   │   └── js/                 #     theme.js · app.js · byob.js (BYOB-Session)
│   └── botkit/                 #   Dezentrale Bots (BYOB), seit v1.3.0
│       ├── tokens.py           #     BotToken, RAM-Vaults, Formatvalidierung
│       ├── privacy.py          #     Redaction, Fingerprints, audit()
│       ├── registry.py         #     Registrierung ohne Token-Speicherung
│       ├── review.py           #     BK-Regeln, Checkliste, Review-Gate
│       ├── session.py          #     ephemere Bot-Sessions (TTL, Rate-Limit)
│       └── telegram_api.py     #     getMe/getUpdates/setWebhook/deleteWebhook
├── bots/                       # eigene Nutzer-Bots (reviewpflichtig, CI-Gate)
├── examples/own_bot/           # Referenz-Bot (besteht alle Review-Regeln)
├── tests/                      # Unit-Tests (utils, sender, app, botkit)
├── docs/                       # aktuelle Projekt-Dokumentation
│   ├── ARCHITECTURE.md                     # Architektur & Datenflüsse
│   ├── DECENTRAL_BOT_ARCHITECTURE.md       # BYOB-Architektur & Review-Prozess
│   └── DEPLOYMENT.md                       # Render.com-Anleitung
├── peer-review/                # Review-Berichte & deren Verlauf
│   ├── README.md                           # Konventionen
│   ├── TEMPLATE.md                         # Berichtsvorlage
│   ├── CODE_REVIEW.md                      # aktuellster Review-Bericht
│   └── archive/                            # abgeschlossene Artefakte (Prompts, PR-Texte)
├── audit/                      # Audit-Einstieg: Prüf-Matrix, reviews.json
├── security/                   # Schutzziel-Matrix, Threat-Model, Grenzen
├── .github/                    # CODEOWNERS, PR-Template, SECURITY.md, CI-Workflow
├── pyproject.toml              # Paketierung + Ruff/pytest/Bandit-Konfiguration
├── render.yaml                 # Render-Blueprint: kanonischer Start, Health-Check, Env-Vars
├── requirements.txt            # Laufzeit-Deps (gepinnt; pyproject liest sie dynamisch)
├── requirements-dev.txt        # Dev-Deps (pytest)
├── CHANGELOG.md                # Versionierung (Keep a Changelog)
├── CONTRIBUTING.md             # Setup, Schichtregeln, PR-Prozess
├── MIGRATION.md                # Struktur-Migration 1.3.0 → 2.0.0 (alt → neu)
└── LICENSE                     # GNU GPL v3
```

**So nutzt du die Struktur bei Reviews/Audits:** Doku-Frage → `docs/`.
„Wie wurde geprüft?“ → `peer-review/`. „Welche Gates laufen wann und wo
liegt der Trail?“ → `audit/README.md`. „Welche Sicherheitsanforderungen
erzwingt der Code?“ → `security/README.md`.

## Deployment

Schritt-für-Schritt-Anleitung für [Render.com](https://render.com) in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). Der kanonische Startbefehl ist
zusätzlich als Render-Blueprint in [`render.yaml`](render.yaml) deklariert und
übernimmt dort die Konfiguration neu aus dem Blueprint angelegter Dienste.

> **Gestörter Alt-Start?** Leitet das Dashboard noch `gunicorn app:app` weiter
> — die Adresse zeigt über den veralteten Root-Shim `app.py` auf dieselbe App.
> Der Shim entfällt mit 3.0.0; bis umstellen.

## Dokumentation

- [Architektur](docs/ARCHITECTURE.md) — Schichten, Komponenten, Datenflüsse,
  Abhängigkeiten.
- [Formatierung](docs/FORMATTING.md) — Telegram-Features vs. LLM-Ausgabe vs.
  Implementierungsstand (inkl. Redirect-Unwrap).
- [Dezentrale Bot-Architektur](docs/DECENTRAL_BOT_ARCHITECTURE.md) — eigene
  Bots registrieren, reviewen und in Sessions nutzen; Peer-Review-Prozess.
- [Deployment](docs/DEPLOYMENT.md) — Schritt-für-Schritt für Render.com.
- [Code-Peer-Review](peer-review/CODE_REVIEW.md) — gefundene Probleme und
  Fixes; Vorlage & Konventionen: [peer-review/](peer-review/README.md).
- [Security-Konzept](security/README.md) — Schutzziele & Durchsetzung;
  Meldeprozess: [.github/SECURITY.md](.github/SECURITY.md).
- [Audit-Einstieg](audit/README.md) — Prüf-Matrix und Ablage des
  Review-Audit-Trails (`audit/reviews.json`).
- [Mitwirken](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) ·
  [Migrationsnotizen 2.0.0](MIGRATION.md)

## Versionierung

Das Projekt folgt [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`). Aktuelle Version: **2.8.0** — Änderungen je Version im
[CHANGELOG.md](CHANGELOG.md); die Struktur-Reorganisation (Importpfade/
CLI-Aufrufe) aus 2.0.0 ist dokumentiert in [MIGRATION.md](MIGRATION.md).
