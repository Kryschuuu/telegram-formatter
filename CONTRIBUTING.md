# Mitwirken

Willkommen! Dieser Leitfaden beschreibt Setup, Qualitätsmaßstäbe und den
Review-Prozess. Kurzfassung der Regeln, nach denen reviewed wird: die
Checkliste C1–C9 (`python -m telegram_formatter.botctl checklist`).

## Entwicklungsumgebung

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements-dev.txt
# optional (Komfort: `telegram-formatter`/`botctl` als Befehle):
pip install -e .
```

| Aufgabe | Kommando |
|---|---|
| Tests | `pytest -q` |
| UI-Design-Verträge | `pytest tests/test_frontend.py` (Struktur) — für funktionale DOM-Tests zusätzlich `npm install` (jsdom gepinnt in `package.json`), dann `pytest tests/test_jsdom_smoke.py` (skippt ohne Node/jsdom sauber) |
| Lint | `ruff check .` |
| Sicherheits-Scan | `bandit -c pyproject.toml -r telegram_formatter examples/own_bot -ll` |
| Alle Prüfungen wie CI | siehe [`audit/README.md`](audit/README.md) |

## Arbeitsweise im Code

- **Schichten einhalten:** `telegram_formatter/utils.py` bleibt rein (keine
  I/O-, keine Flask-Abhängigkeiten). Netzwerk nur in `sender.py` bzw.
  `botkit/telegram_api.py`. Einstiegspunkte (`cli`, `app`, `botctl`) bleiben
  dünn.
- **Keine Secrets, keine Persistenz, keine Inhalte in Logs** — Details und
  Durchsetzung: [`security/README.md`](security/README.md).
- **Typannotationen & Deutsch als Doku-Sprache** im Projekt bestehen lassen;
  Docstring-Überschriften tragen den Pfad des Moduls (z. B.
  `telegram_formatter/botkit/tokens.py`).
- Abhängigkeiten werden in `requirements*.txt` gepinnt (PEP 668-kompatible
  Reproduzierbarkeit); `pyproject.toml` leitet die Laufzeit-Abhängigkeiten
  dynamisch daraus ab — **keine Pin-Liste duplizieren**.

## Änderungen dokumentieren

1. [CHANGELOG.md](CHANGELOG.md) — neuer Abschnitt nach *Keep a Changelog*
   (Hinzugefügt / Geändert / Behoben / Entfernt).
2. Bei Struktur- oder API-Änderungen: [MIGRATION.md](MIGRATION.md) erweitern
   (Alt → Neu-Tabelle).
3. Bei inhaltlichen Reviews: neuer Bericht in
   [`peer-review/`](peer-review/README.md) nach `TEMPLATE.md`.

## Pull-Request-Prozess

1. `pull_request_template.md` vollständig ausfüllen (er erscheint automatisch).
2. Bei Dateien unter `bots/` oder `examples/own_bot/`: `botctl review` +
   zwei Freigaben (≥1 Maintainer) — Ablauf siehe [`bots/README.md`](bots/README.md).
3. `CODEOWNERS` zieht Maintainer-Review für sicherheitsrelevante Pfade
   automatisch nach; Branch-Protection verhindert Merges ohne Freigaben.

## Testkonvention

- Jeder Fix bekommt einen Regressionstest (vorher fehlschlagend).
- Negativbeispiele (z. B. `tests/fixtures/insecure_bot.py`) bleiben
  absichtlich „schlecht“ und sind via `.gitleaks.toml`/`tests/fixtures`-Ausnahme
  aus Lint/Secret-Scan ausgenommen — dort nicht „aufräumen“.
