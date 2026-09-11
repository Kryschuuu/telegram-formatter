# Changelog

Alle relevanten Änderungen an diesem Projekt, formatiert nach
[Semantic Versioning](https://semver.org/) und
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

## [1.3.0] - 2026-09-11

### Hinzugefügt

- **Dezentrales Bot-Konzept (BYOB — Bring Your Own Bot)**: Neues Paket
  `botkit/` als Alternative zum zentralen Bot. Nutzer registrieren ihren
  eigenen Bot, lassen den Code reviewen und nutzen ihn in einer ephemeren
  Session — ohne zentrale Datenspeicherung.
- **`botkit/tokens.py`**: `BotToken`-Umschlag (Klartext nur über `reveal()`,
  `repr` redacted), `InMemoryTokenVault` (RAM + TTL) und
  `PassthroughTokenVault` (strengster Modus, speichert nichts).
- **`botkit/privacy.py`**: `RedactingFilter` für alle Logger (inkl. urllib3),
  `audit()` als einzige Log-Schnittstelle (nur Metadaten), prozesslokale
  HMAC-Fingerprints, `scrub_environment()` gegen Token-Vererbung.
- **`botkit/registry.py`**: Registrierung mit `getMe`-Verifikation
  (`is_bot`, ID-Gegenprobe) — speichert Identität und Status, **niemals** das
  Token; Validierung für `chat_id` und Besitzer-Pseudonym.
- **`botkit/review.py`**: AST-basierte Regeln BK001–BK012 (Persistenz,
  Fremdnetzwerk, dynamische Ausführung, hartkodierte Secrets, Inhalte im
  Log), Checkliste C1–C9, `ReviewLedger` (Append-only, Metadaten) und
  `ReviewGate` mit Vier-Augen-Prinzip (2 Freigaben, ≥1 Maintainer:in,
  Freigabe an die SHA-256-Prüfsumme des Codes gebunden).
- **`botkit/session.py`**: `BotSession`/`SessionManager` mit TTL,
  Leerlauf-Timeout, Rate-Limit-Fenster und Eingabevalidierung; Versand über
  die bestehenden Module `utils.build_messages` + `sender.send_message`.
- **`botkit/telegram_api.py`**: `getMe`, `getUpdates`, `setWebhook`,
  `deleteWebhook` — die einzigen erlaubten API-Aufrufe.
- **`botctl.py`**: CLI mit `register`, `review`, `approve`, `verify`, `send`
  und `checklist`.
- **Referenz-Bot** `examples/own_bot/minimal_bot.py` (besteht alle BK-Regeln)
  und **Negativbeispiel** `tests/fixtures/insecure_bot.py` (löst BK001–BK012
  aus).
- **Review-Infrastruktur**: `.github/workflows/bot-review.yml`
  (Ruff, Bandit, pip-audit, gitleaks, `botctl review`, pytest),
  `.github/CODEOWNERS`, PR-Template mit Pflicht-Checkliste.
- **Doku**: `docs/DECENTRAL_BOT_ARCHITECTURE.md` (Architektur, Nutzerreise,
  Hürden-Tabelle, Security-Durchsetzung, Peer-Review-Prozess,
  Vergleich mit dem zentralen Bot).

### Geändert

- `conftest.py` (neu) sorgt dafür, dass Projekt-Root und `tests/` beim Import
  auflösbar sind.
- Bestehende Module (`utils.py`, `sender.py`, `cli.py`, `app.py`) bleiben
  unverändert — `botkit` ergänzt das Projekt nur.

### Behoben (Audit-Runde CI)

- **pip-audit**: `requests` von 2.32.4 auf **2.33.0** angehoben
  (`PYSEC-2026-2275`), `pytest` von 8.3.5 auf **9.1.1** (`PYSEC-2026-1845`).
  Beide Audits (Laufzeit und Entwicklung) sind damit ohne Befund.
- **gitleaks**: das Beispiel-Token in den Docstrings von
  `examples/own_bot/minimal_bot.py` und `botkit/tokens.py` war ein
  Dummy-Wert, löste aber zu Recht den Secret-Scan aus — ersetzt durch
  Platzhalter (`<token-von-botfather>` bzw. `os.environ[...]`).
- **gitleaks (Negativbeispiel)**: das Token in
  `tests/fixtures/insecure_bot.py` steht jetzt über zwei Zeilen
  (implizite String-Konkatenation). Der AST faltet es zu einer Konstante,
  die BK006-Regel greift weiterhin; der zeilenbasierte Secret-Scanner
  schlägt nicht mehr an.
- **`.gitleaks.toml`** ergänzt: eng begrenzte Freigaben für die
  Negativbeispiele unter `tests/fixtures/` sowie für den historischen
  Dokumentations-Dummy in alten Commits.
- **Workflow `bot-review.yml`**: Berechtigungen für PR-Kommentar und
  SARIF-Upload (`pull-requests: write`, `actions: write`) ergänzt —
  damit entfällt der Fehler „Resource not accessible by integration";
  außerdem wöchentlicher Termin-Check der Abhängigkeiten und Audit der
  Entwicklungs-Abhängigkeiten.

### Tests

- 175 Tests gesamt (vorher 81): 94 neue Tests für Token-Handling, Redaction,
  Registry, Review-Gate (Vier-Augen, Prüfsummen-Bindung) und Sessions
  (TTL, Leerlauf, Rate-Limit, keine Inhalte in Logs).

## [1.2.0] - 2026-09-02

### Hinzugefügt

- **DeepSeek/Gemini-LaTeX-Syntax**: Unterstützung für `\(...\)` (Inline-Math)
  und `\[...\]` (Display-Math) zusätzlich zum klassischen `$...$`/`$$...$$`.
  Diese Syntax wird von DeepSeek Chat, Gemini und anderen KI-Tools verwendet.
  Formeln werden korrekt als LaTeX erkannt und als Rich-Message versendet.
- **Neue Funktion `convert_deepseek_latex_syntax()`** in `utils.py`: normalisiert
  `\(...\)` → `$...$` und `\[...\]` → `$$...$$`. Eingebunden in
  `markdown_to_rich_markdown()` und `markdown_to_html()`.

### Behoben

- **Telegram rendert DeepSeek-Delimiter nicht**: Bisher wurden `\(...\)`/`\[...\]`
  zwar als LaTeX *erkannt* (Rich-Pfad), aber unverändert in die Payload
  geschrieben — Telegram gab sie dadurch als rohen Text mit Backslashes aus.
  Die Delimiter werden jetzt in die von Telegram unterstützte Dollar-Syntax
  übersetzt, während der Formelinhalt 1:1 erhalten bleibt.

### Geändert

- Entfernt: ungenutzte Hilfsfunktion `_find_delimiter_close()` (toter Code, der
  zudem eine `DeprecationWarning` wegen ungültiger Escape-Sequenz auslöste).
- Dokumentation (`README.md`, `templates/index.html`) beschreibt die
  DeepSeek/Gemini-Syntax; Versionsangabe der Web-Oberfläche auf 1.2.0 angehoben.

### Tests

- 81 Tests gesamt (vorher 66), davon 26 rund um die DeepSeek/Gemini-Syntax:
  Erkennung (`split_formulas`, `has_latex`), Konvertierung
  (`convert_deepseek_latex_syntax`) sowie die Payload-Ausgabe über
  `build_messages()`. Abgedeckt sind gemischte Delimiter, mehrzeilige
  Display-Formeln, verschachtelte Klammern, Preisangaben (`$ 20`),
  unvollständige Delimiter, doppelte Backslashes und Code-Schutz.

## [1.1.0] - 2026-09-02

Neue Web-UI-Features (Minor-Bump nach Semantic Versioning).

### Hinzugefügt
- **Reset-Button** „Zurücksetzen" in der Web-Oberfläche: leert Eingabe,
  Vorschau, Payload-Ausgabe und Statusmeldung und setzt den Fokus zurück
  ins Eingabefeld.
- **Buy-me-a-coffee-Button** (`https://buymeacoffee.com/rg4free`) im Header
  (gelb hervorgehoben) sowie als Unterstützungs-Link im Footer
  (`target="_blank"`, `rel="noopener"`).
- **Disclaimer**: ausführlicher Haftungsausschluss als Hinweisbox unter dem
  Editor (eigene Verantwortung, Akzeptanz der Nutzungsbedingungen, keine
  Verbindung zu Telegram, keine Datenspeicherung, Haftungsausschluss) plus
  Kurzform im Footer.
- **Howto**: nummerierte Schritt-für-Schritt-Anleitung direkt auf der Seite
  (Token via @BotFather, Chat-ID ermitteln, Eingabe, Vorschau prüfen,
  Versand).
- **FAQ**: sieben aufklappbare Akkordeons (Bot-Token, Chat-ID,
  LaTeX-Rendering, Nachrichtenlänge, Datenschutz, Formatierung,
  Fehlerbehebung).
- Font-Awesome-Icons für Buttons, Abschnitte und Statusanzeigen.
- Tests: `tests/test_app.py` prüft Reset-Button, Coffee-Link, Disclaimer
  und Howto/FAQ (55 Tests gesamt).
- `docs/PROMPT.md` (wiederverwendbarer Arbeitsauftrag) und
  `docs/PR_DESCRIPTION.md` (fertiger PR-Text).

### Geändert
- **Optik**: Sticky-Header, Karten-Layout für alle Sektionen, neuer
  mehrspaltiger Footer; Konfigurationsstatus („Bot konfiguriert" /
  „Kein Bot-Token gesetzt") im Header sichtbar.
- README: Versions-Badge und Versionsangabe auf **1.1.0**,
  Funktionsübersicht um die neuen UI-Features ergänzt.

## [1.0.0] - 2026-09-02

Komplette Überarbeitung: Code-Review, Bugfixing, Tests, Dokumentation.

### Hinzugefügt
- Automatisches Nachrichten-Splitting an Absatz-, Zeilen- und Wortgrenzen
  (`chunk_text`) für beide Telegram-Limits (4096 / 32768 Zeichen).
- Separater Versand-Layer (`sender.py`) mit eigener `SendError`-Exception und
  lazy `requests`-Import.
- Kommandozeilen-Einstieg (`cli.py`) mit Datei-/STDIN-Eingabe und `--send`.
- Flask-Weboberfläche (`app.py`) mit den Routen `/api/convert` und `/api/send`
  und einer passenden, schlanken Editor-Seite.
- Vollständige Test-Suite (`tests/`): 54 Tests für Konvertierung, LaTeX,
  Tabellen, Splitting, Versand (gemockt) und Flask-Routen.
- Dokumentation: `docs/BLUEPRINT.md`, `docs/DEPLOYMENT.md`,
  `docs/CODE_REVIEW.md` sowie dieses Changelog.

### Behoben
- **LaTeX-Zerstörung** durch naive Regex bei verschachtelten Formeln
  (`\binom{\binom{70}{6}}{33}`). Ersetzt durch zeichenbasiertes Parsing mit
  Klammerbilanz-Prüfung.
- **Ungültiges Rich-Message-Payload** (`format`/`text` statt korrektem
  `markdown`-Feld), das `400 Bad Request` ausgelöst hätte.
- **Fehlendes Splitting** langer klassischer Nachrichten (4096-Zeichen-Limit).
- **Rich-Chunking-Bug**, der Chunks länger als das Limit erzeugen konnte.
- **Unterstreichen-Semantik** in Rich Markdown (`__x__` → `<u>x</u>`).
- **Unicode/Diakritika** (z. B. `ì`) durch NFC-Normalisierung und explizites
  UTF-8-Einlesen.

### Geändert
- Abhängigkeiten bereinigt und versioniert: `pyTelegramBotAPI`,
  `telegramify-markdown`, `APScheduler` und doppeltes `Flask` entfernt.
- Verwaiste Web-Oberfläche („PostMaster Pro") durch funktionierende
  Editor-Seite ersetzt.
- Code strukturell getrennt (utils / sender / cli / app) und durchgängig
  kommentiert, typannotiert und dokumentiert.
- `requirements-dev.txt` für Entwicklungswerkzeuge ergänzt.

### Entfernt
- Toter Code: ungenutzte Routen, ungenutzte JS-Handler, ungenutzte
  Abhängigkeiten.
