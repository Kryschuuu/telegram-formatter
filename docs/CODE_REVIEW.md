# Code-Peer-Review

Dokumentiert die im Rahmen der Überarbeitung gefundenen Probleme der
ursprünglichen Codebasis, jeweils mit Begründung und konkreter Lösung. Der
Ausgangszustand bestand aus einer Flask-App mit einer großen, teils toten
Web-Oberfläche (`templates/index.html`) sowie einer Konvertierungslogik in
`utils.py`/`app.py`, die mehrere schwerwiegende Fehler enthielt.

## Kritische Fehler (Bugs)

### 1. Ungültiges Rich-Message-Payload (`format`/`text` statt `markdown`)
**Fund:** `app.py` baute das `rich_message`-Objekt als
`{"format": "markdown", "text": chunk}`.

**Begründung:** Die Telegram Bot API (10.1+, 2026) definiert
`InputRichMessage` mit genau **einem** der Felder `html`, `markdown` oder
`blocks`. Die Felder `format` und `text` existieren dort nicht — jede so
gesendete Nachricht würde mit `400 Bad Request` abgelehnt.

**Lösung:** Payload korrekt als `{"chat_id": ..., "rich_message": {"markdown": chunk}}`
aufgebaut (siehe `utils.build_messages`).

### 2. LaTeX-Zerstörung durch Regex
**Fund:** Verschachtelte Formeln wie `$\binom{\binom{70}{6}}{33}$` wurden
durch non-greedy Regex-Matching (z. B. `\$([^$]+?)\$` oder
`\\binom\{(.*?)\}\{(.*?)\}`) mittendrin zerschnitten.

**Begründung:** Reguläre Ausdrücke können beliebig tief verschachtelte,
geklammerte Strukturen nicht erkennen („balanced matching" ist mit regulären
Sprachen nicht lösbar).

**Lösung:** `split_formulas()` scannt zeichenweise und findet nur die
schließende `$`-Marke; der Formelinhalt wird 1:1 übernommen. Zusätzlich prüft
`validate_latex_braces()` die Klammerbilanz. Unbalancierte Formeln bleiben
als Klartext erhalten, statt das Dokument zu beschädigen.

### 3. Fehlendes Nachrichten-Splitting bei 4096 Zeichen
**Fund:** Reiner Fließtext (Regular-Pfad) wurde ohne Längenprüfung verschickt.

**Begründung:** `sendMessage` begrenzt Text auf **4096** Zeichen; längere
Nachrichten scheitern mit `message is too long`.

**Lösung:** `chunk_text()` teilt an Absatz- → Zeilen- → Wortgrenzen auf und
respektiert dabei Blockstrukturen (Tabellen/Formeln bleiben intakt).

### 4. Bug im Rich-Chunking (Überlange Chunks)
**Fund:** `chunk_rich_markdown()` fügte einen Absatz auch dann an den
aktuellen Chunk an, wenn dieser dadurch das Limit überschritt; das Ergebnis
konnte länger als `max_chars` sein.

**Lösung:** Neu implementierte `chunk_text()` garantiert `len(chunk) <= max_chars`
in jedem Fall (inkl. harter Wort-Teilung als letztem Ausweg).

### 5. Unterstreichen-Semantik in Rich Markdown
**Fund:** `__text__` wurde als Unterstreichen interpretiert.

**Begründung:** In Telegrams „Rich Markdown" bedeutet `__text__` **Fett**;
Unterstreichen wird über `<u>…</u>` ausgedrückt.

**Lösung:** `markdown_to_rich_markdown()` übersetzt `__x__` → `<u>x</u>`.

## Sicherheit

### 6. Fehlende HTML-Escaping-Reihenfolge
**Fund:** Im Alt-Code wurde teils vor, teils nach dem Escaping ersetzt,
sodass Nutzerinhalte (z. B. `<script>`) in den HTML-Payload gelangen konnten.

**Begründung:** Telegram-HTML parst nur eine Teilmenge von Tags, aber
beliebiger, nicht escapter HTML-Inhalt ist ein XSS-Vektor in der Vorschau
und erzeugt ggf. `Unmatched end tag`-Fehler.

**Lösung:** `markdown_to_html()` schützt Code/Formeln per Platzhalter,
escaped dann **alles** restliche (`&`, `<`, `>`) und stellt Geschütztes
zuletzt wieder her. Eingaben werden nie als rohes HTML durchgereicht.

### 7. Bot-Token im Code/Log
**Fund:** Token wurde nur aus der Umgebung gelesen (gut), aber der Dry-Run
druckte Payloads inkl. `chat_id`. Kein Token im Code.

**Begründung:** Tokens dürfen nie hartkodiert oder in Logs landen.

**Lösung:** Beibehalten: Token ausschließlich über `TELEGRAM_BOT_TOKEN`;
Logs enthalten keine Secrets.

## Strukturelle Schwächen / Toter Code

### 8. Verwaiste Web-Oberfläche
**Fund:** `templates/index.html` („PostMaster Pro") referenzierte Routen
(`/api/auth`, `/api/channels`, `/api/posts` …), die im Backend gar nicht
existierten, sowie eine JS-Funktion `onTelegramAuth` ohne Gegenstück.

**Lösung:** Durch eine schlanke, funktionierende Editor-Seite ersetzt, die
exakt die vorhandenen Routen `/api/convert` und `/api/send` nutzt.

### 9. Ungenutzte Abhängigkeiten
**Fund:** `requirements.txt` enthielt `pyTelegramBotAPI`, `telegramify-markdown`,
`APScheduler` sowie doppeltes `Flask==3.0.2` — nichts davon wurde importiert.

**Lösung:** Auf tatsächlich benötigte Pakete reduziert (`Flask`, `gunicorn`,
`requests`) und versioniert.

### 10. Gemischte Verantwortlichkeiten
**Fund:** Konvertierung, Versand und Web-Routing lagen in `app.py` zusammen.

**Lösung:** Aufteilung in `utils` (Konvertierung, testbar), `sender`
(Versand), `cli` (Kommandozeile) und `app` (Web). Klare Namensgebung und
durchgängige Typannotationen/Docstrings.

## Konsistenz / Qualität

- Einheitliche deutsche Kommentare und Docstrings in allen Modulen.
- `from __future__ import annotations` überall; Python-3.10+-Syntax.
- Konstanten für Telegram-Limits zentral in `utils` definiert.
- Fehlerbehandlung im Versand über eine eigene `SendError`-Exception.
