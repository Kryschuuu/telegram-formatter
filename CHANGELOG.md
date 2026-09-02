# Changelog

Alle relevanten Änderungen an diesem Projekt, formatiert nach
[Semantic Versioning](https://semver.org/) und
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

## [1.2.0] - 2026-09-02

### Hinzugefügt

- **DeepSeek/Gemini-LaTeX-Syntax**: Unterstützung für `\(...\)` (Inline-Math)
  und `\[...\]` (Display-Math) zusätzlich zum klassischen `$...$`/`$$...$$`.
  Diese Syntax wird von DeepSeek Chat, Gemini und anderen KI-Tools verwendet.
  Formeln werden korrekt als LaTeX erkannt und als Rich-Message versendet.

### Tests

- 11 neue Tests für die DeepSeek/Gemini-Syntax (`test_split_inline_math_deepseek_syntax`,
  `test_split_display_math_deepseek_syntax`, `test_split_mixed_delimiters`,
  `test_split_preserves_nested_in_deepseek_syntax`, `test_has_latex_deepseek_syntax`,
  `test_has_latex_mixed_syntax`, `test_unterminated_paren_stays_text`,
  `test_unterminated_bracket_stays_text`, `test_deepseek_real_world_example`,
  `test_build_rich_for_deepseek_math`, `test_build_rich_for_deepseek_display_math`).

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
