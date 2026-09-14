# Review: Shared-Versand im Browser + Versandweg-Auswahl (v2.6.0)

- **Datum:** 2026-09-13
- **Reviewende:** Arena-Agent (Selbst-Review vor PR), Rollen: Kern-Code + Security
- **Gegenstand:** Branch `arena/01a09ce4-telegram-formatter` gegen `main` @ `0157aba`,
  Bereich: `telegram_formatter/app.py`, `static/js/app.js`, `static/js/byob.js`,
  `static/css/components.css`, `templates/index.html`, `tests/*`, Doku & `render.yaml`
- **Ergebnis:** ✔ freigegeben (325 Tests grün, Ruff/Bandit sauber, 2 Auflagen als
  Follow-up notiert)

## Kontext

Gemeldeter Fehler: Die Seite zeigt „Shared-Bot konfiguriert — Browser nutzt BYOB“,
aber eine Nachricht an `@mdtotxt_bot` lässt sich nicht absenden — man *muss*
einen eigenen Bot konfigurieren. Ursache war die Härtung aus 2.5.0: `POST /api/send`
war kompromisslos an den Operator-Token (`X-Auth-Token`) gebunden, den der Browser
aus gutem Grund nie erhält. Der geteilte Bot war damit im UI allgegenwärtig und
gleichzeitig unerreichbar.

Der Fix macht den Weg konfigurierbar statt ihn rückzubauen: `/api/send` ist aus dem
Browser erreichbar, wenn `TELEGRAM_FORMATTER_SHARED_WEB_SEND` (Standard `1`) **und**
ein gepinnter `TELEGRAM_CHAT_ID` gesetzt sind; der Bestätigungsdialog stellt beide
Wege als Radio bereit (eigener Bot = privat, geteilter Bot = öffentlich).

## Befunde (am neuen Code)

### [BLOCKER] Anonymer Versand darf kein Relay werden — Pinning ist Pflicht

- **Fund:** `telegram_formatter/app.py` — `_shared_web_send_available()`,
  `_resolve_target_chat()`
- **Kategorie:** security (Audit-Regelfall K-2)
- **Begründung:** Ein Browser-Endpunkt, der Text an Telegram weiterreicht, ist ohne
  Zielbindung ein offener Relay: jeder Besucher schreibt über den Betreiber-Bot an
  beliebige Dritte (Spam/Phishing → Bot-Sperre).
- **Fix / Verifikation:** Die Freigabe greift **nur** bei gesetztem
  `TELEGRAM_BOT_TOKEN` *und* `TELEGRAM_CHAT_ID`; `chat_id` aus dem Request wird bei
  gepinntem Chat mit 400 abgewiesen, ohne Pinning bleibt der Endpunkt bei 503.
  Abgedeckt durch `tests/test_app.py::test_shared_web_send_never_reaches_foreign_chat`
  und `::test_shared_web_send_needs_pinned_chat`.

### [WARNING] Öffentlicher Chat = Spamfläche — Limits müssen an den anonymen Weg gekoppelt sein

- **Fund:** `telegram_formatter/app.py` — `send()`, `SHARED_WEB_*`
- **Kategorie:** security / availability
- **Begründung:** 100 000 Zeichen × 4/min pro IP sind im geteilten Chat eine
  Zumutung für alle Leser, und viele IPs parallel laufen den geteilten Bot gegen die
  Telegram-Rate-Limits (Bot-Sperre trifft alle Nutzer).
- **Fix:** Anonyme Sendungen: `SHARED_WEB_MAX_INPUT_CHARS` (8000),
  `SHARED_WEB_SENDS_PER_MINUTE` (4/IP) und `SHARED_WEB_SENDS_PER_MINUTE_TOTAL`
  (30/Instanz, Bucket mit `per_ip=False`). Authentizierte API-Aufrufe bleiben bei
  `MAX_INPUT_CHARS`/`SENDS_PER_MINUTE` (Test:
  `test_operator_token_path_keeps_full_input_limit`).

### [WARNING] Zweite Bestätigungsinstanz: Consent im Body, nicht nur im Dialog

- **Fund:** `telegram_formatter/app.py::_public_consent`,
  `telegram_formatter/static/js/app.js::send()`
- **Kategorie:** privacy / UX
- **Begründung:** Die UI warnt vor der Öffentlichkeit — ein Skript (oder ein
  Browser-Plugin, das den Button auslöst) umgeht den Dialog. Die Hürde ist keine
  Authentifizierung, sondern ein Nachweis, dass der Aufrufer die Öffentlichkeit
  kennt: `confirm_public: true` wird sonst mit 400 abgelehnt.
- **Fix:** `send()` setzt das Feld erst, *nachdem* der Bestätigungsdialog mit der
  Warnbox bestätigt wurde (jsdom-Vertrag:
  `tests/frontend/jsdom_spec.cjs`, Fall „Senden: öffentliche Sichtbarkeit wird
  bestätigt mitgeschickt“).

### [WARNING] Anzeige-Drift: zwei Skripte am selben Text

- **Fund:** `telegram_formatter/static/js/byob.js` (vorher `updateSendPath()`),
  `telegram_formatter/static/js/app.js`
- **Kategorie:** maintainability
- **Begründung:** `byob.js` schrieb an `#sendBtnLabel`, `#sendPathNote` und
  `window.tfSendLabel`, `app.js` las davon nur einen Teil und baute den Dialog aus
  einem einzigen `sharedConfigured`-Flag — genau diese Mehrdeutigkeit
  („configured“ = Bot da? = senden erlaubt?) war der Ursprung des gemeldeten Fehlers.
- **Fix:** Ein Owner (`app.js`) für Beschriftung, Hinweis und Dialog; `byob.js`
  meldiert nur noch `tf:botsessionchange`. Die Flags sind getrennt
  (`data-shared-send` vs. `data-shared-configured`), `window.tfSendLabel` ist
  entfernt (Migration: `MIGRATION.md` §9).

### [INFO] Guard-Ausnahme bleibt auf genau einen Endpunkt beschränkt

- **Fund:** `telegram_formatter/app.py` — `_operator_token_required()`,
  `SHARED_SEND_ENDPOINT`
- **Kategorie:** security
- **Begründung:** Mit Operator-Token *und* Browser-Versand wäre `/api/send` durch
  den Guard trotzdem gesperrt — widersprüchlich, weil der Browser das Secret nie
  bekommen darf. Die Ausnahme ist deshalb explizit endpunktgebunden (kein
  Endpoint-Set, kein Präfix-Matching).
- **Fix / Verifikation:** `test_operator_token_exempts_only_the_shared_send_path`
  prüft beide Richtungen (`/api/send` ohne Header durch, `/api/convert` ohne Header
  401). Die Origin-Bindung gilt ausnahmslos für alle POSTs.

### [INFO] Zugänglichkeit des Dialogs

- **Fund:** `templates/index.html` (`fieldset`/`legend` + Radios),
  `static/js/app.js::trapFocus`
- **Kategorie:** correctness / a11y
- **Begründung:** Neue interaktive Elemente im Modal müssen in die Tab-Falle, und
  ein deaktivierter „Senden nicht möglich“-Knopf darf den Fokuszyklus nicht
  blockieren.
- **Fix:** Selektor auf `button:not([disabled])` erweitert; Radios sind echte
  Form-Controls (`name="sendPath"`), der Hinweis-Text hängt als `role="note"` an.
  Bewusst *keine* Vorauswahl des geteilten Bots, wenn eine eigene Session läuft —
  privacy-first, im Dialog aber umschaltbar.

## Angenommen / abgewogen

- **Standard `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1`:** bewusste Produktentscheidung —
  eine Instanz, die Bot *und* Zielchat pinnt, betreibt eine Demo mit öffentlichem
  Chat; der gemeldete Fehler war die unerreichbare Zusage. Wer den öffentlichen
  Chat nicht will, setzt `0` (Drei-Zeilen-Änderung im Dashboard) und hat den
  2.5.0-Zustand exakt wieder. Restrisiko (fremde Besucher im eigenen Chat) ist in
  `security/README.md` dokumentiert.
- **Kein CAPTCHA, keine Nutzer-Identität für anonyme Sendungen:** Rate-Limit +
 instanzweiter Deckel + Consent reichen für eine Demo-Instanz; echte
  Missbrauchsbekämpfung (Gerätefingerprint, Mail-Verifizierung) würde das
  Privacy-Versprechen des Projekts (keine Personendaten) unterlaufen.
- **Kein Multi-User-Audit-Trail pro Nachricht:** der geteilte Chat selbst ist der
  Beleg; serverseitig wird nichts protokolliert (Schutzziel S2).
- **`byob_enabled` wird im Dialog nur für Textherkunft gebraucht:** kein eigenes
  UI-Element, sonst müsste `app.js` die BYOB-Formularlogik kennen (Schichtbruch).

## Follow-ups

- [ ] Optional: `TELEGRAM_FORMATTER_SHARED_WEB_ALLOW_RICH` (anonymer Weg nur für
      Regular-Messages) — falls LaTeX-Flut im Demo-Chat je stört.
- [ ] Optional: `/api/send`-Antwort um `chat_title` ergänzen, damit die UI den
      Zielchat beim geteilten Weg benennen kann (heute: „gemeinsamer Chat dieser
      Seite“).
- [ ] Beobachten: ob Betreiber die Variable im Dashboard pflegen oder im Blueprint
      erwarten (docs/DEPLOYMENT.md, Schritt 3a abgleichen).
