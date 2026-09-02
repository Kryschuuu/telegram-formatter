# Wiederverwendbarer Prompt — Telegram Formatter v1.1.0 (Web-UI-Features)

> Dieser Prompt ist ein fertig formulierter, unverändert übergebbarer
> Arbeitsauftrag. Er beschreibt die Aufgabe vollständig, die zu Version
> 1.1.0 dieses Repositories geführt hat. Einfach kopieren und an einen
> KI-Agenten (oder Entwickler:in) übergeben.

---

## Prompt

```text
Arbeite im Repository „telegram-formatter" (Flask + Vanilla-JS-Web-UI,
Test-Suite mit pytest). Ziel: Version 1.1.0 — neue Komfort-,
Rechte- und Support-Features für die Weboberfläche in
templates/index.html, passende Tests und Doku/Versionierung.

Sprache der UI: Deutsch. Stil: Tailwind (bereits via CDN eingebunden),
bestehende IDs und Logik (Live-Vorschau, /api/convert, /api/send)
unverändert beibehalten und nur ergänzen.

1) Web-Oberfläche (templates/index.html)

   a) Reset-Button:
      - Direkt neben dem Sende-Button, Beschriftung „Zurücksetzen",
        mit Font-Awesome-Icon (z. B. fa-rotate-left).
      - Klick leert: Eingabe-Textarea, Vorschau (zurück zum Platzhalter
        „Vorschau erscheint hier…"), Payload-Ausgabe (zurück auf „—"),
        Statusmeldung sendStatus.
      - Danach bekommt das Eingabefeld den Fokus (input.focus()).
      - Kein erneuter /api/convert-Aufruf nötig (nur Zustand zurücksetzen).

   b) Buy-me-a-coffee-Button:
      - Link auf https://buymeacoffee.com/rg4free.
      - Im Header, gelb hervorgehoben (z. B. bg-amber-400) mit Icon
        fa-mug-hot und Text „Buy me a coffee".
      - Zusätzlich als Unterstützungs-Link im Footer.
      - Beide Links: target="_blank" und rel="noopener".

   c) Disclaimer:
      - Ausführlicher Haftungsausschluss als Hinweisbox (amber-farbene
        Box mit Warn-Icon) direkt unter dem Editor. Inhalt muss abdecken:
        Nutzung auf eigene Verantwortung; alleinige Verantwortung der
        Nutzenden für den Inhalt der Nachrichten; Akzeptanz der
        Nutzungsbedingungen; keine Verbindung/Kein Sponsorship durch
        Telegram; keine dauerhafte Datenspeicherung (Verarbeitung nur zu
        Konvertierung/Versand); Haftungsausschluss für Schäden.
      - Kurzform derselben Punkte im Footer.

   d) Howto:
      - Abschnitt mit nummerierter Schritt-für-Schritt-Anleitung,
        fünf Schritte: 1) Bot-Token via @BotFather (/newbot,
        Umgebungsvariable TELEGRAM_BOT_TOKEN), 2) Chat-ID ermitteln
        (@userinfobot bzw. getUpdates, TELEGRAM_CHAT_ID), 3) Markdown +
        LaTeX im Editor eingeben, 4) Live-Vorschau und Telegram-Payloads
        prüfen, 5) „An Telegram senden" klicken (Statusmeldung beachten).
      - Abschnitt enthält id="howto".

   e) FAQ:
      - Abschnitt mit id="faq", sieben aufklappbare Akkordeons
        (details/summary ohne externes JS), Themen: Bot-Token
        (Sicherheit/Speicherung), Chat-ID finden, LaTeX-Rendering
        (warum Vorschau vs. echtes Telegram-Rendering), maximale
        Nachrichtenlänge (4096/32768, automatisches Splitting),
        Datenschutz, unterstützte Formatierungen, Fehlerbehebung
        (400/401/429 und Konfigurationsfehler).

   f) Optik:
      - Sticky-Header (klebt beim Scrollen oben), Karten-Layout
        (jede Sektion als abgerundete Karte mit Rand/Schatten),
        mehrspaltiger Footer, Font-Awesome-Icons über die ganze Seite
        (Font-Awesome-CSS via CDN nachladen — fehlte bisher).

2) Tests (tests/test_app.py)
   - Ein neuer Test prüft auf der gerenderten Startseite:
     Reset-Button (id="resetBtn" und Text „Zurücksetzen"),
     Coffee-Link (https://buymeacoffee.com/rg4free, target="_blank",
     rel="noopener"), Disclaimer („Haftungsausschluss",
     „Keine Datenspeicherung"), Howto (id="howto") und FAQ (id="faq",
     mindestens 7 details-Elemente).
   - Die komplette Suite muss grün sein (55 Tests).

3) Doku & Versionierung
   - CHANGELOG.md: neuer Eintrag [1.1.0] nach Keep-a-Changelog-Konvention,
     Minor-Bump wegen neuer Features; Abschnitte „Hinzugefügt"/„Geändert"
     mit allen obigen Punkten.
   - README.md: Versions-Badge und „Aktuelle Version" auf 1.1.0;
     Funktionsübersicht um die neuen UI-Features ergänzen.
   - docs/PROMPT.md: dieser wiederverwendbare Prompt.
   - docs/PR_DESCRIPTION.md: fertiger, sofort verwendbarer PR-Text
     (Übersicht „Was geändert wurde" je Bereich, Teststatus, Hinweise).

4) Abschluss
   - Alles auf einem Feature-Branch committen, pushen und einen
     Pull Request gegen main eröffnen; PR-Body = docs/PR_DESCRIPTION.md.
   - Keine Änderungen an utils.py, sender.py, cli.py oder app.py —
     die API und Konvertierungslogik bleiben unangetastet.
```

---

## Abnahmekriterien

- [ ] `pytest -q` → 55 Tests grün.
- [ ] Startseite zeigt: Reset-Button, gelber Coffee-Button (Header),
      Disclaimer-Box, Howto (5 Schritte), FAQ (7 Akkordeons), Footer.
- [ ] Coffee-Links (Header + Footer) öffnen extern mit
      `target="_blank" rel="noopener"`.
- [ ] Reset leert Eingabe, Vorschau, Payloads und Status und fokussiert
      die Textarea.
- [ ] CHANGELOG-Eintrag `[1.1.0]` und README-Versionsangabe aktualisiert.
