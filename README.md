# telegram-formatter

Konvertiert **Markdown mit LaTeX-Formeln und Tabellen** in sendefertige
Telegram-Nachrichten — mit korrektem LaTeX-Rendering, Telegram-Formatierung
(Fett, Kursiv, Unterstrichen, Code, …) und automatischer Aufteilung langer
Nachrichten.

![Version](https://img.shields.io/badge/version-1.2.0-blue)
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
pip install -r requirements.txt
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
python cli.py beispiel_input.txt

# Aus STDIN lesen
echo "**fett** und $x^2$" | python cli.py

# Wirklich senden
python cli.py beispiel_input.txt --send --token 123456:ABC --chat-id -100123456789
```

### Web-Oberfläche

```bash
flask --app app run            # http://127.0.0.1:5000
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
from utils import build_messages
from sender import send_message

for msg in build_messages("**fett** und $x^2$", chat_id="-100123456789"):
    send_message(msg, bot_token="123456:ABC")
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

## Projektstruktur

```
telegram-formatter/
├── app.py                  # Flask-Weboberfläche
├── cli.py                  # Kommandozeilen-Einstieg
├── utils.py                # Konvertierungs- & Splitting-Logik (pure)
├── sender.py               # HTTP-Versand an die Telegram-API
├── templates/index.html    # Editor-Seite
├── tests/                  # Unit-Tests (utils, sender, app)
├── docs/
│   ├── BLUEPRINT.md        # Architektur & Datenflüsse
│   ├── DEPLOYMENT.md       # Render.com-Anleitung
│   ├── CODE_REVIEW.md      # Review-Ergebnisse & Fixes
│   ├── PROMPT.md           # Wiederverwendbarer Arbeitsauftrag (KI-Agent)
│   └── PR_DESCRIPTION.md   # PR-Text für die 1.1.0-Änderungen
└── requirements.txt
```

## Deployment

Schritt-für-Schritt-Anleitung für [Render.com](https://render.com) in
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## Dokumentation

- [Technischer Blueprint](docs/BLUEPRINT.md) — Architektur, Komponenten,
  Datenflüsse, Abhängigkeiten.
- [Code-Peer-Review](docs/CODE_REVIEW.md) — gefundene Probleme und Fixes.
- [Changelog](CHANGELOG.md) — Versionshistorie (Semantic Versioning).

## Versionierung

Das Projekt folgt [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`). Aktuelle Version: **1.2.0**.
