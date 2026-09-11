# telegram-formatter

Konvertiert **Markdown mit LaTeX-Formeln und Tabellen** in sendefertige
Telegram-Nachrichten — mit korrektem LaTeX-Rendering, Telegram-Formatierung
(Fett, Kursiv, Unterstrichen, Code, …) und automatischer Aufteilung langer
Nachrichten.

![Version](https://img.shields.io/badge/version-2.0.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

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
  Codeblöcke, Links, Überschriften, Listen und Blockquotes.
- **Tabellen** — Pipe-Tabellen werden in native Rich-Markdown-Tabellen
  übersetzt (GFM).
- **Automatisches Splitting** — Nachrichten werden an Absatz-, Zeilen- und
  Wortgrenzen aufgeteilt (4096 Zeichen für klassische, 32768 für Rich
  Messages), ohne Formatierungen oder Tabellen zu zerreißen.
- **Eigener Bot (dezentral, v1.3.0)** — BYOB über `telegram_formatter/botkit`
  und das `botctl`-CLI:
  eigener Bot registrieren, reviewen und in einer ephemeren Session
  nutzen — ohne zentrale Datenspeicherung.
- **Unicode-sicher** — NFC-Normalisierung für Diakritika wie `ì`.
- **Komfortable Web-Oberfläche** (v1.1.0) — Live-Vorschau und Payloads in
  Echtzeit, Reset-Button („Zurücksetzen"), Sticky-Header im Karten-Layout,
  Schritt-für-Schritt-Howto, aufklappbares FAQ, Disclaimer-Hinweisbox sowie
  Buy-me-a-coffee-Unterstützungs-Link (Header & Footer).

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

Setze die Umgebungsvariablen:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-..."
export TELEGRAM_CHAT_ID="-100123456789"   # optional
```

## Nutzungsbeispiele

### Kommandozeile

```bash
# Dry-Run: zeigt nur die gebauten API-Payloads (kein Token nötig)
python -m telegram_formatter.cli beispiel_input.txt

# Aus STDIN lesen
echo "**fett** und $x^2$" | python -m telegram_formatter.cli

# Wirklich senden
python -m telegram_formatter.cli beispiel_input.txt --send --token 123456:ABC --chat-id -100123456789
```

*(Nach `pip install -e .` stehen zusätzlich die Befehle `telegram-formatter`
und `botctl` zur Verfügung.)*

### Web-Oberfläche

```bash
flask --app telegram_formatter.app run   # http://127.0.0.1:5000
```

Im Browser Markdown/LaTeX eingeben, die Payloads in Echtzeit prüfen und
optional direkt senden.

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


## Eigener Bot statt Zentral-Bot (dezentral, ab v1.3.0)

Standardmäßig sendet diese Anwendung über **einen** konfigurierten Bot
(`TELEGRAM_BOT_TOKEN`). Wer seine Nachrichten nicht über fremde Infrastruktur
laufen lassen will, nutzt stattdessen den eigenen Bot: Registrierung, Review
und Session laufen lokal bzw. in einer ephemeren Session — **ohne
Datenspeicherung**.

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
pytest -q                                  # 175 Tests
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
├── telegram_formatter/         # ← gesamter Laufzeitcode als Python-Paket
│   ├── app.py                  #   Flask-Weboberfläche (WSGI-Einstieg)
│   ├── cli.py                  #   Kommandozeilen-Einstieg
│   ├── utils.py                #   Konvertierungs- & Splitting-Logik (pure)
│   ├── sender.py               #   HTTP-Versand an die Telegram-API
│   ├── botctl.py               #   CLI für eigene Bots (register/review/approve/send)
│   ├── templates/index.html    #   Editor-Seite
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
├── requirements.txt            # Laufzeit-Deps (gepinnt; pyproject liest sie dynamisch)
├── requirements-dev.txt        # Dev-Deps (pytest)
├── CHANGELOG.md                # Versionierung (Keep a Changelog)
├── CONTRIBUTING.md             # Setup, Schichtregeln, PR-Prozess
├── MIGRATION.md                # Struktur-Migration 1.3.0 → 2.0.0 (alt → neu)
└── LICENSE                     # MIT
```

**So nutzt du die Struktur bei Reviews/Audits:** Doku-Frage → `docs/`.
„Wie wurde geprüft?“ → `peer-review/`. „Welche Gates laufen wann und wo
liegt der Trail?“ → `audit/README.md`. „Welche Sicherheitsanforderungen
erzwingt der Code?“ → `security/README.md`.

## Deployment

Schritt-für-Schritt-Anleitung für [Render.com](https://render.com) in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Dokumentation

- [Architektur](docs/ARCHITECTURE.md) — Schichten, Komponenten, Datenflüsse,
  Abhängigkeiten.
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
(`MAJOR.MINOR.PATCH`). Aktuelle Version: **2.0.0** — die Struktur-Reorganisation
(Importpfade/CLI-Aufrufe) ist dokumentiert in [MIGRATION.md](MIGRATION.md).
