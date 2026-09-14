# PR: Geteilter Bot im Browser wählbar — Versandweg statt BYOB-Zwang (v2.6.0)

## Motivation

Die Instanz meldet „Shared-Bot konfiguriert — Browser nutzt BYOB“, aber
Nachrichten an `@mdtotxt_bot` lassen sich nicht senden: man **muss** einen
eigenen Bot konfigurieren. Grund war die Härtung aus v2.5.0 — `POST /api/send`
war ohne `TELEGRAM_FORMATTER_API_TOKEN` pauschal deaktiviert (503), und dieser
Operator-Token darf dem Browser nie ausgehändigt werden. Der geteilte Bot war
damit im UI allgegenwärtig (Warnungen, Hinweise, Statuszeile) und gleichzeitig
unerreichbar; der Bestätigungsdialog zeigte „kein geteilter Bot konfiguriert“,
obwohl einer konfiguriert war.

Anforderung: Beim Klick auf „An Telegram senden“ soll `@mdtotxt_bot` wählbar
sein, **sofern kein eigener Bot initialisiert wurde** — ohne die Sicherheits-
grenzen aus K-2/R-1 (kein Relay, Fail-Closed) zu öffnen.

## Konzept-Entscheidung (bewertete Alternativen)

| Option | Bewertung |
|---|---|
| A. `/api/send` wieder ohne Bedingungen für den Browser öffnen | rückgängig gemachte Härtung: anonyme Long-Text-Flut in den Betreiber-Chat, Rate-Limit nur global — abgelehnt |
| B. Nur Doku/Doku-Hinweis: „Setze den Operator-Token im Browser-Client“ | unmöglich — das Secret darf den Browser nie erreichen (Grundaussage von 2.5.0) |
| **C. Opt-in-Schalter + eigene Grenzen + Weg-Auswahl im Dialog (gewählt)** | Anonymer Versand nur bei **gepinntem Zielchat**, mit Consent-Feld, eigener Längen- und Frequenzkappe (pro IP **und** instanzweit); die UI wählt den sichtbaren Weg, statt einen zu vermuten |
| D. Neuer Browser-Proxy mit Session-Cookie für den geteilten Bot | mehr Zustände/Cookie-Surface (CSRF, Privacy-Versprechen „keine Cookies“) — kein Gewinn gegenüber C |

## Was neu ist

**Backend** (`telegram_formatter/app.py`):

- `_shared_web_send_available()` = `TELEGRAM_FORMATTER_SHARED_WEB_SEND`
  (Standard `1`) **und** `TELEGRAM_BOT_TOKEN` **und** `TELEGRAM_CHAT_ID`.
  Fehlt eine Bedingung: 503 mit Meldung, die beide Zugangswege nennt.
- Anonyme Sendungen brauchen `"confirm_public": true` (sonst 400) und unterliegen
  `SHARED_WEB_MAX_INPUT_CHARS` (8000), `SHARED_WEB_SENDS_PER_MINUTE` (4/IP) und
  `SHARED_WEB_SENDS_PER_MINUTE_TOTAL` (30/Instanz, neuer `per_ip=False`-Bucket).
  Authentizierte `X-Auth-Token`-Aufrufe: unverändert volle Grenzen, kein Consent.
- Zielchat-Pinning bleibt die harte Grenze: `chat_id`-Override → 400 (K-2),
  ohne Pinning kein Browser-Zugang (R-1).
- `_operator_token_required()`: ist der Operator-Token gesetzt **und** der
  Browser-Versand freigeschaltet, ist genau `send` ausgenommen — alle anderen
  POSTs (auch `/api/byob/*`) verlangen weiter `X-Auth-Token`.
- Antwort auf `/api/send` enthält `via: {bot, chat_id, public}` für die UI.
- Validierungs-Helfer entflochten: `_extract_request()` → `_valid_text()`,
  `_resolve_target_chat()`, `_public_consent()`.

**Frontend** (`static/js/app.js`, `static/js/byob.js`, `templates/index.html`):

- **Versandweg-Auswahl** im Bestätigungsdialog (`fieldset#sendConfirmPaths`):
  „@mein_bot — dein eigener Bot (privat · Chat …)“ und
  „@mdtotxt_bot — geteilter Bot dieser Seite (öffentlich · alle Besucher lesen mit)“.
  Voreinstellung ist der private Weg; die Umschaltung zieht Fakten, Hinweisboxen,
  Button-Beschriftung und `#sendPathNote` nach. Kein Weg offen ⇒ „Senden“ ist
  deaktiviert und der Hinweis erklärt den Grund (inkl. Name des Betreiberschalters).
- Entzerrter Vertrag: `app.js` ist alleiniger Autor von Label/Hinweis/Dialog,
  `byob.js` meldiert `tf:botsessionchange`; `window.tfSendLabel` und das
  mehrdeutige `data-configured` sind entfernt. Neu: `data-shared-send`,
  `data-shared-configured`, `data-byob-enabled`.
- Statuszeile, Top-Warnung und Versandweg-Hinweis unterscheiden jetzt drei
  Zustände (nutzbar / nur per API / kein geteilter Bot) statt „nutzt BYOB“.
- `send()` hängt `confirm_public: true` an — **nachdem** der Dialog mit der
  öffentlichen Warnung bestätigt wurde.

**CSS** (`static/css/components.css`): `.tf-send-paths`, `.tf-send-paths__legend`,
`.tf-send-path-option` (+ `__input/__body/__title/__meta`, `--public`,
Zustandsklasse `is-selected`) — ausschließlich `var(--token)`, damit alle vier
Themes + Auto ohne Nacharbeit passen.

## Tests

- `tests/test_app.py`: 13 neue Fälle (Zugangs-Matrix, Consent, Pinning,
  Längen-/Frequenzgrenzen inkl. Instanz-Deckel, Guard-Ausnahme,
  Operator-Token-Pfad, Template-Flags). Der bisherige Fail-Closed-Test prüft
  jetzt explizit `SHARED_WEB_SEND=0`.
- `tests/test_frontend.py`: DOM-Verträge für Fieldset/Radios/Beschriftungen,
  Drei-Zustände-Matrix der `<body>`-Attribute, „BYOB aus“- und
  „nur-per-API“-Meldungen; `data-configured` darf nicht mehr auftauchen.
- `tests/frontend/jsdom_spec.cjs`: Fall 1 auf den echten Demo-Zustand umgestellt
  (geteilter Bot vorausgewählt, POST mit `confirm_public`, `via`-Status), neuer
  Weg-Wechsel-Test bei aktiver Session (Dialog → `/api/send` → zurück →
  `/api/byob/send`), neuer Fall 6 als Regression auf den gemeldeten Bug
  (`data-shared-send="0"`: Auswahl ausgeblendet, „Senden“ deaktiviert, kein POST;
  mit Session wieder privat sendenfähig).
- Ergebnis: **325 passed**, `ruff check`, `bandit` (volles Niveau), `pip-audit`
  ohne Befund.

## Doku & Versionierung

`telegram_formatter.__version__` → **2.6.0**; CHANGELOG (Keep a Changelog,
Abschnitt 2.6.0 mit „Sicherheit“-Block); README (neuer Abschnitt „Zwei
Versandwege“, Env-Tabelle, beide `curl`-Beispiele); `docs/DEPLOYMENT.md`
(Env-Tabelle + Betreiber-Entscheidung + Schritt 6); `docs/ARCHITECTURE.md`
(Routen, Client-Routing); `docs/DESIGN.md` (§8 CSS-Bausteine + JS-/Data-Vertrag);
`security/README.md` (S9 neu gefasst, neuer Abschnitt „Geteilter Bot im Browser“,
K-2-Status); `SECURITY_AUDIT.md` (Nachtrag zu K-2); `MIGRATION.md` §9
(Alt→Neu-Tabelle + Checkliste); `render.yaml` (Variable im Blueprint);
Peer-Review-Bericht `peer-review/2026-09_SHARED-WEB-SEND.md`.

## Hinweis für Betreiber (wichtig)

Der Browser-Versand ist Standard **an**, weil eine Instanz mit Bot + gepinntem
Chat genau diese Demo bedeutet. Wer keinen fremden Schreibzugriff auf den
geteilten Chat will: `TELEGRAM_FORMATTER_SHARED_WEB_SEND=0` — dann gilt wieder
der Zustand aus 2.5.0 (API-only, Browser ausschließlich BYOB). Für bestehende
Render-Dienste ist die Variable einmalig im Dashboard zu setzen (Blueprint
`sync`-Hinweis in `docs/DEPLOYMENT.md`).

## Checkliste

- [x] Nutzer-Bot-Code unverändert (kein `bots/`-, `examples/own_bot/`-Diff) ⇒
      `botctl review` nicht nötig; `botkit/` unverändert
- [x] Bestehende Konvertierung (`utils.py`, `sender.py`) unverändert
- [x] Keine Secrets, keine Persistenz, keine Inhalte in Logs (nur `via`-Metadaten
      in der Antwort, kein Token, keine Log-Zeilen)
- [x] `pytest -q` grün, neue Tests für Verhalten **und** Negativfälle
- [x] Doku, CHANGELOG, MIGRATION, Security-Doku nachgezogen
