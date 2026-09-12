# PR: BYOB auf der Website — eigene Bot-Sessions statt geteiltem Öffentlich-Chat (v2.2.0)

## Motivation

Die Website sendet bislang ausschließlich über den geteilten Bot
`@mdtotxt_bot` in den gepinnten `TELEGRAM_CHAT_ID` — auf der öffentlichen
Instanz ein **gemeinsamer Chat**: Alles Gesendete ist für alle Besucher
sichtbar, und jeder, der den Bot auf Telegram hinzufügt, kann den Verlauf
lesen. Der Auftrag: das dezentrale Bot-Konzept (BYOB — *Bring Your Own Bot*)
einfach und benutzerfreundlich auf der Website umsetzen — Nutzer registrieren
ihren eigenen Bot und nutzen ihn in einer ephemeren Session, ohne zentrale
Datenspeicherung.

## Konzept-Entscheidung (bewertete Alternativen)

| Option | Bewertung |
|---|---|
| A. Nur Warnung für den geteilten Bot | Ehrlich, aber ohne privaten Weg im Web (BYOB nur per CLI — zu technisch für Web-Nutzer) |
| B. Geteilten Bot entfernen, nur BYOB | Bricht den Null-Setup-Demo-Pfad; der geteilte Bot soll funktional bleiben |
| **C. Beide Wege (gewählt)** | Geteilter Bot bleibt mit **dominanter Privatsphäre-Warnung** (am Senden-Button + eigene Gefahren-Box); BYOB-Web-Session ist der empfohlene private Weg (~3 Min. Einrichtung). Konvertierung ohne Versand bleibt datenschutzfreundlicher Default |

Umgesetzt wird Betriebsmodus B aus `docs/DECENTRAL_BOT_ARCHITECTURE.md` —
der einzige Modus, der bislang als „nicht implementiert“ galt.

## Was neu ist

**Backend** (`telegram_formatter/app.py`):

- `POST /api/byob/session` — Token-Formatprüfung → `getMe`-Verifikation
  (RAM-Registry) → ephemere `BotSession` (TTL 30 min, Leerlauf 10 min,
  20 Msgs/Min.) → opaker Handle. Token nie gespeichert/geloggt/echoed.
- `POST /api/byob/discover` — Chat-ID-Erkennung per `getUpdates`-Blick
  (kein `offset`, nichts wird bestätigt); nur Chat-Metadaten, nie Texte.
- `POST /api/byob/send` — Versand über die Session; Teilfortschritt,
  429-Backoff, 410 bei Ablauf mit Handlungsanweisung.
- `POST /api/byob/status`, `POST /api/byob/close` — Countdown-Daten bzw.
  sofortiges Verwerfen der Token-Referenz.
- Anti-Missbrauch: separate IP-Rate-Limits (3/3/6 pro Min.), Kappen
  (100 Sessions total, 3 pro IP), `reap_expired`/`prune` vor jedem Öffnen.
- Alle Endpunkte unter den bestehenden Guards (Origin, `X-Auth-Token`,
  Body-Limit, CSP).

**Frontend:**

- Neue Sektion „Versandweg: geteilter Bot oder eigener Bot (BYOB)?“ mit
  roter Warnbox (neue `--danger-*`-Tokens in allen Themes), 3-Schritte-
  Anleitung, Formular (Passwort-Feld ohne Autocomplete, Consent-Checkbox),
  Chat-Erkennungs-Chips, Session-Statuskarte mit Countdown und
  Beenden-Button; `#sendPathNote` am Senden-Button zeigt den aktiven Weg.
- `static/js/byob.js` (neues Modul): Session-Lebenszyklus; stellt
  `window.tfByob` bereit, an das `app.js` den Senden-Button delegiert.
- 4 neue FAQ-Einträge, Howto- und NoScript-Updates.

**Bugfixes aus dem Ganzcode-Review** (Details: `peer-review/2026-09_BYOB-WEB.md`):

1. **Wheel ohne Assets:** package-data-Globs griffen nicht in
   `static/css|js` — `pip install` lieferte ein ungestyltes UI. Korrigiert +
   Vertragstest.
2. **Rate-Limits hinter Proxy wirkungslos:** `ProxyFix(x_for=1)` — vorher
   teilten sich alle Besucher einen Limit-Eimer.
3. Changelog/README-Versionierung aufgeräumt (verwaiste `[Unreleased]`-
   Blöcke → 2.1.0 zugeordnet; stale „2.0.0“-Angabe korrigiert).

## Betrieb

- Sessions leben prozesslokal: Start mit **einem** Gunicorn-Worker +
  `--threads` (`render.yaml` angepasst; `docs/DEPLOYMENT.md` erklärt warum).
- Neue ENV-Vars (alle mit Default, siehe README-Tabelle):
  `TELEGRAM_FORMATTER_BYOB_ENABLED` (Abschalter),
  `…_BYOB_SESSIONS_PER_MINUTE`, `…_BYOB_DISCOVER_PER_MINUTE`,
  `…_BYOB_SENDS_PER_MINUTE`, `…_BYOB_TTL_SECONDS`, `…_BYOB_IDLE_SECONDS`,
  `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE`.

## Tests

- 297 passed (+ jsdom-Suite mit neuem BYOB-Fall: Öffnen, Token-Feld-Leerung,
  Senden-Routing, Chips, 410-Reset); Ruff + Bandit sauber.
- Neu: `tests/test_byob_web.py` (36 Tests) — inkl. „Token in keiner
  Antwort“ und „keine Nachrichtentexte in der Chat-Erkennung“.

## Review

- Peer-Review-Bericht: `peer-review/2026-09_BYOB-WEB.md` (Freigabe ✔).
- Architektur-Doku: `docs/DECENTRAL_BOT_ARCHITECTURE.md` §1.5.1 (Modus B im
  Detail, inkl. Begründung Body-Handle statt Cookie und der
  Review-Gate-Konstruktion).
