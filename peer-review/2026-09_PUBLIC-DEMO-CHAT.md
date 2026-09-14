# Review: Öffentliche Demo als public Supergroup/Channel

- **Datum:** 2026-09-14
- **Reviewende:** arena-agent (Code-Peer-Review)
- **Gegenstand:** Zielchat-Aufbau der gehosteten Demo, Warntexte, Deployment-Doku,
  Versionierung (Branch `arena/01a09ea0-telegram-formatter`)
- **Ergebnis:** ✔ freigegeben

## Kontext

Die gehostete Demo soll den geteilten Bot `@mdtotxt_bot` über einen **öffentlichen**
Zielchat (public Supergroup/Channel) betreiben, damit die Privatsphäre-Warnung auf
der Seite wörtlich zutrifft und das persönliche Postfach des Betreibers ruhig bleibt.
Gleichzeitiger Peer-Review der betroffenen Module auf Korrektheit, Wartbarkeit und
überflüssige Redundanzen; danach Tests, Doku und Versionierung.

## Befunde

### [BLOCKER] Warnung war für öffentliche Chats faktisch falsch

- **Fund:** `telegram_formatter/templates/index.html:124, 321, 575, 808, 825`
- **Kategorie:** correctness (privacy/docs)
- **Begründung:** Die Warnung sagte „jeder, der den Bot auf Telegram hinzufügt,
  kann den bisherigen Verlauf lesen". Für einen **privaten** Chat stimmt das, für
  den empfohlenen **öffentlichen** Chat aber nicht: dort liest jeder, der die
  Gruppe/den Kanal *öffnet*, den kompletten Verlauf — auch ohne Mitglied zu sein.
  Die Warnung war damit im Demo-Szenario ungenau.
- **Fix:** Einheitliche Formulierung „jeder, der die Gruppe oder den Kanal auf
  Telegram öffnet, kann den gesamten Verlauf lesen (auch nachträglich, auch ohne
  Mitglied zu sein)" an allen vier Stellen (Top-Warnung, Sende-Bestätigung,
  Privatsphäre-Sektion, zwei FAQ-Antworten); `telegram_formatter/app.py`-Modul-Docstring
  analog.

### [WARNING] Deployment empfiehlt keinen öffentlichen Zielchat

- **Fund:** `docs/DEPLOYMENT.md` (Schritt 4), `render.yaml`
- **Kategorie:** maintainability (docs)
- **Begründung:** Die Anleitung pinnt `TELEGRAM_CHAT_ID`, sagt aber nicht, dass
  der Chat **öffentlich** sein sollte — die Voraussetzung dafür, dass die Warnung
  wörtlich korrekt ist.
- **Fix:** Neuer Abschnitt „Schritt 4b: Öffentliche Demo — Zielchat als public
  Supergroup/Channel" (3-Schritte: Gruppe/Kanal + Bot mit *Nachrichten senden*,
  Nachricht schreiben → ID `-100…` ablesen, als `TELEGRAM_CHAT_ID` pinnen).
  `render.yaml` erhielt einen Kommentar-Hinweis sowie die deklarierte Variable
  `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` (Standard `@mdtotxt_bot`).

### [WARNING] README inkonsistent zur tatsächlichen Version/Testzahl

- **Fund:** `README.md:13` (Badge), `README.md` „Versionierung" („2.4.0"),
  „Tests & Qualitätssicherung" („297 Tests"), `README.md:76` (Sichtbarkeits-Tabelle)
- **Kategorie:** maintainability (docs)
- **Begründung:** Die Versionierung nannte `2.4.0` bei tatsächlich `2.7.0`
  (`telegram_formatter/__version__`); die Testzahl war hartkodiert statt aktuell;
  die Sichtbarkeits-Tabelle beschrieb den geteilten Chat zu eng („und wer den Bot
  zu Telegram hinzufügt").
- **Fix:** Badge + Versionsangabe auf `2.8.0`; Testzahl auf „360+"; Tabelle
  präzisiert auf den öffentlichen Chat; `TELEGRAM_CHAT_ID`-Beschreibung nennt die
  public-Chat-Empfehlung.

### [INFO] Tote Zusatzprüfung in BYOB-Handlern

- **Fund:** `telegram_formatter/app.py` → `byob_session_send` / `byob_session_status`
  / `byob_session_close` (`if err is not None or sid is None or session_secret is None:`)
- **Kategorie:** maintainability
- **Begründung:** `_byob_session_id` liefert bei Fehler stets `(None, None, err)`
  und bei Erfolg `(sid, secret, None)`. Die Prüfung `sid is None or
  session_secret is None` ist daher nie der Grund für einen Abbruch und toter Code.
- **Fix:** Auf `if err is not None: return err` reduziert — Verhalten identisch,
  Klarheit besser.

### [INFO] Kosmetik: Tippfehler im Kommentar

- **Fund:** `telegram_formatter/botkit/review.py:286` („späät")
- **Fix:** Korrektur auf „spät".

## Angenommen / abgewogen

- Die Warnung nennt bewusst „öffnen" statt „beitreten": für einen *nicht*
  öffentlichen Chat ist „öffnet" etwas weiter gefasst, trifft aber gerade den
  Demo-Fall (public Supergroup) exakt. Eine Fallunterscheidung pro
  Chat-Sichtbarkeit lohnt den Template-Aufwand nicht.
- `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` bleiben `sync: false` im Blueprint —
  Secrets gehören weiterhin nicht ins Git.

## Follow-ups

- [ ] (optional) Bot kann die Sichtbarkeit des Zielchats via `getChat` erkennen
      und die Warnung automatisch auf „öffentlich/privat" zuschärfen.

---

<!-- Konventionen: peer-review/README.md -->
