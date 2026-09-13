# Pull Request

## Was ändert sich?

<!-- Kurz: 1–3 Sätze. Bei Bot-Code: Welcher Bot, welches Verhalten? -->

## Typ

- [ ] Nutzer-Bot (neu/geändert) → **Review-Pflicht, siehe unten**
- [ ] `telegram_formatter/botkit`-Baukasten
- [ ] Doku / CI
- [ ] Bestehende Konvertierung (in `telegram_formatter/`: `utils.py`, `sender.py`, `app.py`, `cli.py`)

---

## Pflichtteil für Nutzer-Bots (vor dem Anhaken ausführen!)

```bash
python -m telegram_formatter.botctl review <bot-datei> --bot-id <id>        # Statik + Ticket
python -m telegram_formatter.botctl approve <TICKET> --reviewer <handle> --role maintainer \
    --checks C1,C2,C3,C4,C5,C6,C7,C8,C9
python -m telegram_formatter.botctl verify <bot-datei> --bot-id <id>        # Tor vor dem Deployment
```

### Review-Checkliste (C1–C9)

- [ ] **C1** Token nur über `BotToken`/Environment, nie geloggt oder gespeichert
- [ ] **C2** Kein Schreibzugriff auf Dateisystem, Datenbank oder Cache
- [ ] **C3** Alle Eingaben validiert (Typ, Länge, Format, `chat_id`, Callback-Daten)
- [ ] **C4** Nur erlaubte Telegram-Methoden, keine Fremd-APIs
- [ ] **C5** Fehler klassifiziert geloggt — ohne Inhalte, Token oder `chat_id`
- [ ] **C6** Rate-Limits/429 mit Backoff, Retry-Budget begrenzt
- [ ] **C7** Session-Ende räumt auf (`deleteWebhook`, `drop_pending_updates`, kein Offset)
- [ ] **C8** Keine neuen Abhängigkeiten ohne Begründung, Versionen gepinnt
- [ ] **C9** Tests für Konvertierung, Fehlerpfad und „keine Persistenz"

### Statische Befunde

- [ ] `botctl review --check` ist grün (keine Blocker BK001–BK008, BK010)
- [ ] Warnungen (BK011–BK012) sind behoben oder begründet
- [ ] Suppressions (`# botkit:allow …`) sind aufgeführt **und** begründet:

<!-- Regel-ID, Zeile, Grund -->

### Datenschutz (Kernanforderung)

- [ ] Keine Personendaten, Inhalte oder Tokens im Log/Code/Commit
- [ ] Keine Persistenz (auch keine „temporären" Dateien/Caches)
- [ ] Audit-Trail (`audit/reviews.json`) enthält nur Metadaten

## Tests

- [ ] `pytest -q` grün
- [ ] Neue Tests für das neue Verhalten (inkl. Negativfall)

## Reviewer

- [ ] Zwei Freigaben, mindestens eine von Maintainer:in (Vier-Augen-Prinzip)
