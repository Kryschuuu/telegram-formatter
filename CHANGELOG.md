# Changelog

Alle relevanten Änderungen an diesem Projekt, formatiert nach
[Semantic Versioning](https://semver.org/) und
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

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
