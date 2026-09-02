# PR-Beschreibung — Telegram Formatter v1.1.0

> Bereits verwendbarer Text für den Pull Request (Titel + Body unten
> ab der Markierung kopieren).

---

## Titel

```text
feat(web-ui): Reset-Button, Coffee-Link, Disclaimer, Howto & FAQ — v1.1.0
```

## Body

````markdown
## Summary

Version 1.1.0: Komfort-, Rechts- und Support-Features für die
Web-Oberfläche — ohne Änderungen an Konvertierungslogik oder API.

## Was geändert wurde

### Web-Oberfläche (`templates/index.html`)

- **Reset-Button** „Zurücksetzen" (mit Icon) — leert Eingabe, Vorschau,
  Payload-Ausgabe und Statusmeldung und setzt den Fokus zurück ins
  Eingabefeld.
- **Buy-me-a-coffee-Button** → `https://buymeacoffee.com/rg4free`, im
  Header hervorgehoben (gelb) und als Unterstützungs-Link im Footer
  (`target="_blank"`, `rel="noopener"`).
- **Disclaimer** — aussagekräftiger Haftungsausschluss als Hinweisbox
  unter dem Editor plus Kurzform im Footer (Verantwortung,
  Nutzungsbedingungen, keine Verbindung zu Telegram, keine
  Datenspeicherung, keine Haftung).
- **Howto** — nummerierte Schritt-für-Schritt-Anleitung (Token via
  @BotFather, Chat-ID, Eingabe, Vorschau, Versand).
- **FAQ** — aufklappbare Akkordeons (Token, Chat-ID, LaTeX-Rendering,
  Nachrichtenlänge, Datenschutz, Formatierung, Fehlerbehebung).
- **Optik** — Sticky-Header, Karten-Layout, Footer, Font-Awesome-Icons.

### Doku & Versionierung

- `CHANGELOG.md`: neuer Eintrag `[1.1.0]` (Keep a Changelog, Minor-Bump
  wegen neuer Features).
- `README.md`: Versions-Badge und Versionsangabe auf **1.1.0**,
  Funktionsübersicht ergänzt.
- `docs/PROMPT.md`: wiederverwendbarer Prompt (vollständiger,
  unverändert übergebbarer Arbeitsauftrag zu dieser Aufgabe).
- `docs/PR_DESCRIPTION.md`: dieser PR-Text.

### Tests

- `tests/test_app.py` prüft jetzt, dass Reset-Button, Coffee-Link,
  Disclaimer sowie Howto/FAQ auf der Startseite vorhanden sind.
- **55 Tests grün** (`pytest -q`).

## Screenshots / Testing

```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q                      # 55 passed
flask --app app run            # http://127.0.0.1:5000
```

Manuell prüfen: Reset leert alles und fokussiert die Textarea; beide
Coffee-Links öffnen extern (noopener); FAQ-Akkordeons klappen auf;
Header bleibt beim Scrollen sticky.

## Breaking Changes

Keine. API (`/api/convert`, `/api/send`), `utils.py`, `sender.py`,
`cli.py` und `app.py` sind unverändert.
````
