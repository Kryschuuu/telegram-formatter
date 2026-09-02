# Technischer Blueprint

## 1. Überblick

`telegram-formatter` konvertiert **Markdown mit LaTeX-Formeln und Tabellen**
in sendefertige Telegram-Nachrichten. Kernentscheidung: Inhalte mit LaTeX
oder Tabellen werden als **Rich Messages** (`sendRichMessage`, Feld
`markdown`) verschickt, weil klassische Nachrichten (`sendMessage`) weder
LaTeX noch Tabellen unterstützen. Reiner Formatierungstext läuft über den
klassischen Pfad mit Telegram-HTML.

Die Logik ist strikt in vier Schichten getrennt, sodass die
Konvertierungsfunktionen ohne Netzwerkzugriff und ohne Flask testbar sind.

## 2. Architektur / Komponenten

```
                 +-----------------------+
  Eingabe        |   utils.py (pure)     |
 (Markdown+LaTeX)|  Konvertierung &      |
  ------------>  |  Aufteilung           |
                 +----------+------------+
                            |
                            v
                 +-----------------------+
                 |   build_messages()    |
                 |  -> [TelegramMessage] |
                 +----------+------------+
                            |
              +-------------+--------------+
              |                            |
              v                            v
    +------------------+        +---------------------+
    |  sender.py       |        |  cli.py / app.py    |
    |  HTTP-Versand    |        |  Einstiegspunkte     |
    |  (requests)      |        |  (CLI / Flask)       |
    +------------------+        +---------------------+
```

| Datei | Verantwortung |
|---|---|
| `utils.py` | Reine, I/O-freie Konvertierungs- und Aufteilungslogik. Enthält `normalize_text`, `split_formulas`, `validate_latex_braces`, `parse_pipe_table`, `markdown_to_html`, `markdown_to_rich_markdown`, `build_messages`, `chunk_text`. |
| `sender.py` | Versand einzelner `TelegramMessage`-Objekte via HTTP (`sendMessage`/`sendRichMessage`). Lazy-Import von `requests`. |
| `cli.py` | Kommandozeilen-Einstieg (Datei/STDIN → Payloads anzeigen oder senden). |
| `app.py` | Flask-Weboberfläche mit Editor, Live-Vorschau und den Routen `/api/convert` und `/api/send`. |
| `templates/index.html` | Editor-Seite (Tailwind CDN), ruft die beiden API-Routen auf. |
| `tests/` | Unit-Tests für `utils`, `sender` (mit gemocktem `requests`) und `app` (Flask-Testclient). |

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
3. **Versand** (`sender.send_message`): wählt `sendRichMessage` bzw.
   `sendMessage` anhand von `message.kind`.

## 4. Zentrale Datenstrukturen

- `Segment(kind, content)` — `"text" | "inline_math" | "display_math"`.
- `TelegramMessage(kind, payload)` — `"rich" | "regular"` plus API-Payload.
- `_PlaceholderStore` — schützt Code/Formeln vor Regex-Ersetzungen über
  NUL-basierte Marker.

## 5. Abhängigkeiten (Laufzeit)

| Paket | Zweck |
|---|---|
| `Flask` | Web-Oberfläche und API-Routen |
| `gunicorn` | WSGI-Produktionsserver (Render.com) |
| `requests` | HTTP-Versand an die Telegram-Bot-API |

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
