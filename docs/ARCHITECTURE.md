# Architektur

> Diese Datei beschreibt die Struktur seit v2.0.0 (Paket layout +
> Audit-Ordner). Die Alt→Neu-Mappe jeder Datei findet sich in
> [MIGRATION.md](../MIGRATION.md); früherer Name: `docs/BLUEPRINT.md`.

## 1. Überblick

`telegram-formatter` konvertiert **Markdown mit LaTeX-Formeln und Tabellen**
in sendefertige Telegram-Nachrichten. Kernentscheidung: Inhalte mit LaTeX
oder Tabellen werden als **Rich Messages** (`sendRichMessage`, Feld
`markdown`) verschickt, weil klassische Nachrichten (`sendMessage`) weder
LaTeX noch Tabellen unterstützen. Reiner Formatierungstext läuft über den
klassischen Pfad mit Telegram-HTML.

Die Logik ist strikt in Schichten getrennt, sodass die
Konvertierungsfunktionen ohne Netzwerkzugriff und ohne Flask testbar sind.
Der komplette Laufzeitcode lebt im Paket `telegram_formatter/`; `botkit/`
darin ist ein eigenständiger Sub-Layer für das dezentrale BYOB-Modell.

## 2. Architektur / Komponenten

```
  Eingabe
 (Markdown+LaTeX)
      |
      v
+-----------------------------------------------------+
| telegram_formatter/utils.py                         |
|   reine Konvertierung & Aufteilung (I/O-frei)       |
|   build_messages()  ->  [TelegramMessage]           |
+--------------------------+--------------------------+
                           |
           +---------------+----------------+
           |                                |
           v                                v
+---------------------------+   +--------------------------+
| telegram_formatter/       |   | Einstiegspunkte          |
|   sender.py (HTTP,        |   |   cli.py  (Dry-Run/Send) |
|   requests lazy)          |   |   app.py  (Flask, WSGI)  |
+---------------------------+   |   botctl.py (BYOB-CLI)   |
                                +-------------+------------+
                                              |
                                              v
                                +---------------------------+
                                | telegram_formatter/botkit |  BYOB-Layer:
                                | tokens · privacy ·        |  Registrierung,
                                | registry · review ·       |  Review-Gate,
                                | session · telegram_api    |  ephemere Sessions
                                +---------------------------+
```

| Modul | Verantwortung |
|---|---|
| `telegram_formatter/__init__.py` | Fassade: `__version__`, Re-Exports (`build_messages`, `send_message`) |
| `telegram_formatter/utils.py` | Reine, I/O-freie Konvertierungs- und Aufteilungslogik. Enthält `normalize_text`, `split_formulas`, `validate_latex_braces`, `parse_pipe_table`, `markdown_to_html`, `markdown_to_rich_markdown`, `build_messages`, `chunk_text`. |
| `telegram_formatter/sender.py` | Versand einzelner `TelegramMessage`-Objekte via HTTP (`sendMessage`/`sendRichMessage`). Lazy-Import von `requests`. |
| `telegram_formatter/cli.py` | Kommandozeilen-Einstieg (Datei/STDIN → Payloads anzeigen oder senden). |
| `telegram_formatter/app.py` | Flask-Weboberfläche mit Editor, Live-Vorschau und den Routen `/api/convert` und `/api/send`. Templates liegen in `telegram_formatter/templates/`. |
| `telegram_formatter/static/` + `templates/` | Selbst-gehostetes UI („Design-System tf", seit Redesign 2026-09): vier CSS-Schichten (tokens → base → layout → components), `js/theme.js` (Theme-Switcher) + `js/app.js` (Editor), Inline-SVG-Icons — **keine CDNs**, CSP strikt `'self'`. Architektur & Theme-Anleitung: [DESIGN.md](DESIGN.md). |
| `telegram_formatter/botctl.py` | CLI für eigene Bots: `register` · `review` · `approve` · `verify` · `send` · `checklist`; pflegt den Audit-Trail `audit/reviews.json`. |
| `telegram_formatter/botkit/` | BYOB-Baukasten (Tokens/Vaults, Privacy/Redaction, Registry, Review-Statik BK001–BK012, ephemere Sessions, Telegram-Aufrufe). Details: [DECENTRAL_BOT_ARCHITECTURE.md](DECENTRAL_BOT_ARCHITECTURE.md). |

**Importrichtung (Schichtregel):** `botkit → utils/sender` ist erlaubt, nie
umgekehrt; Einstiegspunkte (`cli`, `app`, `botctl`) liegen oben und fangen
Parameter ein. `utils.py` importiert weder `flask` noch `requests` noch
`botkit`.

## 3. Datenfluss

1. **Normalisierung** (`normalize_text`): Zeilenumbrüche vereinheitlichen,
   Unicode nach NFC vorkomponieren (Diakritika wie `ì` werden EIN Codepoint).
2. **Pfadentscheidung** (`needs_rich_message`): Enthält der Text eine gültige
   `$…$`/`$$…$$`-Formel (`has_latex`) oder eine Pipe-Tabelle (`has_table`)?
   - **Ja → Rich-Pfad:** `markdown_to_rich_markdown` (GFM + nativem LaTeX,
     `__x__` → `<u>x</u>`, Tabellen normalisiert) → `chunk_text(…, 32768)`
     → Payload `rich_message.markdown`.
   - **Nein → Regular-Pfad:** `markdown_to_html` (Telegram-HTML, mit
     Platzhalter-Schutz für Code/Formeln und vollständigem Escaping) →
     `chunk_text(…, 4096)` → Payload `text` + `parse_mode="HTML"`.
3. **Versand** (`telegram_formatter/sender.py::send_message`): wählt
   `sendRichMessage` bzw. `sendMessage` anhand von `message.kind`.

## 4. Zentrale Datenstrukturen

- `Segment(kind, content)` — `"text" | "inline_math" | "display_math"`.
- `TelegramMessage(kind, payload)` — `"rich" | "regular"` plus API-Payload.
- `_PlaceholderStore` — schützt Code/Formeln vor Regex-Ersetzungen über
  NUL-basierte Marker.

## 5. Konfiguration & Werkzeug-Ebenen

| Datei/Ebene | Inhalt |
|---|---|
| `pyproject.toml` | PEP-621-Metadaten, Konsolen-Skripte, Ruff-, pytest- und Bandit-Konfiguration; Deps dynamisch aus `requirements.txt` |
| `render.yaml` | Render-Blueprint: kanonischer Start `gunicorn "telegram_formatter.app:app"`, `healthCheckPath: /`, `PYTHON_VERSION`, Secrets mit `sync: false` |
| `app.py` (Wurzel) | **Veraltet (entfällt 3.0.0):** reiner Forwarding-Shim für Deployments mit altem Start-Befehl `gunicorn app:app`; keine Logik, erzwungen durch `tests/test_app.py` |
| `requirements.txt` / `requirements-dev.txt` | gepinnte Laufzeit-/Dev-Abhängigkeiten (Build-Kompatibilität zu Render.com) |
| `.gitleaks.toml` | Secret-Scan-Freigaben (nur Negativbeispiele, eng begrenzt) |
| `docs/` | aktuelle Projekt-Dokumentation |
| `peer-review/` | Review-Berichte und deren Verlauf (Konventionen: `peer-review/README.md`) |
| `audit/` | Prüf-Matrix und versionierter Review-Audit-Trail (`audit/reviews.json`) |
| `security/` | Schutzziel-Matrix und bekannte Grenzen; Meldeprozess: `.github/SECURITY.md` |
| `bots/` | Ablage für Nutzer-Bots (CI-gated, `CODEOWNERS`-geschützt) |

Laufzeit-Abhängigkeiten:

| Paket | Zweck |
|---|---|
| `Flask` | Web-Oberfläche und API-Routen |
| `gunicorn` | WSGI-Produktionsserver (Render.com): `gunicorn "telegram_formatter.app:app"` |
| `requests` | HTTP-Versand an die Telegram-Bot-API (lazy importiert) |

Entwicklung: `pytest` (siehe `requirements-dev.txt`).

## 6. Externe Schnittstellen

- **Telegram Bot API** — `POST /bot<TOKEN>/sendMessage` und
  `/sendRichMessage`. Limits: 4096 Zeichen (sendMessage) bzw. 32768 Zeichen
  und 500 Blöcke (sendRichMessage).
- **Umgebungsvariablen:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `PORT`.

## 7. Teststrategie

- `tests/test_utils.py` — Konvertierung, LaTeX-Erkennung (inkl. verschachtelte
  `\binom`), Tabellen, Splitting (4096/32768, Absatz-/Wort-/Hard-Splits).
- `tests/test_sender.py` — Versand mit gemocktem `requests` (Methodenwahl,
  Fehlerpfade).
- `tests/test_app.py` — Flask-Routen über Testclient.
- `tests/test_tokens.py`, `test_privacy.py`, `test_registry.py`,
  `test_review.py`, `test_session.py` — BYOB-Layer (Vault-TTL, Redaction,
  getMe-Gegenprobe, BK-Regeln + Vier-Augen-Gate, Session-Grenzen).
- `tests/test_botctl.py` — CLI-Einstieg: Eingabe-Guard des Review-Tors
  (Nicht-Code-Datei → sauberer Fehler, Exit 2, kein Ticket/Audit-Trail).
- `tests/fixtures/insecure_bot.py` — absichtliches Negativbeispiel; muss von
  BK001–BK012 erkannt werden (Regressionstest des Regelwerks).

Tests laufen ohne Installation aus jeder Richtung (`pythonpath = ["."]` in
`pyproject.toml`), Start: `pytest -q`.
