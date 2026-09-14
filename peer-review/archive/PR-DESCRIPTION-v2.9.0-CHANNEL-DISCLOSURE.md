# PR-Beschreibung — v2.9.0: Ziel-Kanal wird offen benannt

> Archiviert aus `peer-review/2026-09_CHANNEL-DISCLOSURE.md`. Branch:
> `arena/01a09f6a-telegram-formatter`.

## Zusammenfassung

Meldung aus dem Betrieb: *„nirgends steht der Kanal, in dem die Nachrichten
gepostet werden, sofern man keinen eigenen Bot verwendet."* Die Seite warnte
zwar mehrfach vor „einem öffentlichen, gemeinsamen Chat", nannte aber nie
**welchen**. Jetzt ist der Ziel-Kanal Konfiguration
(`TELEGRAM_FORMATTER_SHARED_CHAT_URL`, Standard
`https://t.me/mdtotxt_bot_web`) und wird an **sieben** Stellen benannt und
verlinkt — zusammen mit der Aussage, dass neue Nachrichten automatisch nach
einem Monat gelöscht werden (`TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS`,
Standard `30`).

## Änderungen

- **Kanal-Offenlegung im UI (7 Stellen):** Top-Warnung · neues Kanal-Banner
  `.tf-channel` direkt unter der Hero-Überschrift · Hinweis am Senden-Button ·
  Bestätigungsdialog (neue Faktenzeile „Kanal (öffentlich)" mit klickbarem
  Link + Löschfrist, Kanal im Warnbox-Titel und im Bestätigungs-Knopf „In den
  Kanal t.me/… senden") · Privatsphäre-Sektion · zwei neue FAQ-Einträge ·
  Footer + Howto. Die Erfolgsstatusmeldung nach dem Senden wiederholt Bot
  **und** Kanal.
- **Backend:** `normalize_public_chat_url` (nur `https://t.me/<handle>`,
  `t.me/<handle>`, `@<handle>` — kein Klickziel aus Konfiguration),
  `_shared_channel()` als **eine** Quelle für Template, `<body>`-Attribute und
  das `via`-Feld von `POST /api/send` (neu: `chat_url`, `retention_days`).
  Ohne gepinnten `TELEGRAM_CHAT_ID` wird kein Kanal behauptet.
- **Bugfixes aus dem Peer-Review:**
  1. `HTTPException` wurde vom allgemeinen `Exception`-Handler zu **500**
     geflattet (`abort(400)` → 500 + irreführende Log-Warnung) → neuer
     `@app.errorhandler(HTTPException)` mit HTML-freien Meldungen.
  2. `_RATE_HITS` behielt für jede jemals gesehene IP einen leeren Eintrag
     (**Speicherleck** im langlebigen Prozess) → `_prune_rate_buckets()`,
     amortisiert ab 4096 Einträgen, `_RATE_LOCK` jetzt `RLock`.
  3. Nackte `int()/float()`-Aufrufe auf ENV-Werte: Tippfehler ⇒ Start-Crash
     oder **stillschweigend abgeschaltetes Limit** → `_env_int()` mit
     Grenzen und lautem Fallback (alle 13 Aufrufstellen).
  4. `SHARED_BOT_HANDLE` lag im BYOB-Block → in den Shared-Abschnitt umgezogen.
- **Redundanz entfernt:** Template-Makros `channel_link()`/`channel_noun()`/
  `retention_note()` ersetzen sieben handgeschriebene Fassungen derselben
  Aussage; das im FAQ hartkodierte `<code>@mdtotxt_bot</code>` nutzt jetzt
  `channel.bot`.
- **Doku:** `README.md` (Feature, Versandweg-Tabelle, ENV-Tabelle,
  Privatsphäre-Abschnitt), `docs/DEPLOYMENT.md` (Schritt 4b: öffentlicher
  Link + Auto-Löschen + Konsistenzpflicht), `docs/DESIGN.md` (visuelle
  Hierarchie, neue Komponenten, `<body>`-Vertrag), `docs/ARCHITECTURE.md`,
  `security/README.md` (Schutzziele S10–S12), `render.yaml` (beide Variablen).
- **Tests:** neu `tests/test_shared_channel.py`; ergänzt
  `tests/test_frontend.py` (DOM-/CSS-Verträge), `tests/frontend/jsdom_spec.cjs`
  (Dialog-Kanalzeile, Erfolgsstatus, BYOB-Gegenprobe, **Fall 7** für den
  degradierten Zustand ohne Kanal-Link), `tests/test_app.py` +
  `tests/test_byob_web.py` + `tests/test_public_demo_chat.py`
  (Wording/`via`/Version). Die Grammatik des Fallbacks ist bewusst doppelt
  abgesichert: dieselbe Regel steckt im Template-Makro `channel_noun()` und in
  den JS-Helfern `sharedTargetNoun()`/`sharedTargetHeading()`.

## Testplan

- [x] `pytest -q` — **422 passed** (vorher 366)
- [x] `ruff check .` — sauber
- [x] `bandit -c pyproject.toml -r telegram_formatter -ll` — keine Befunde
- [x] jsdom-Spec läuft real (Node + `node_modules` vorhanden) und ist grün
- [x] Mutationstest: Fallback-Grammatik im JS künstlich gebrochen ⇒ Spec fällt
      (`JSDOM_SPEC_FAILED (1)`), zurückgebaut ⇒ grün. Fall 7 hat also Zähne.
- [x] Manuell: Seite mit gesetztem `TELEGRAM_CHAT_ID` + Kanal-URL gerendert;
      Gegenprobe ohne Pinning und mit `SHARED_RETENTION_DAYS=0` (dann keine
      Lösch-Aussage, keine Kanal-Behauptung)

## Risiko

Niedrig. Keine Änderung am Konvertierungs- oder Versandpfad, keine neuen
Abhängigkeiten. `/api/send` erweitert das `via`-Feld **additiv**
(`chat_url`, `retention_days`); Bestandsclients, die nur `bot`/`public` lesen,
bleiben kompatibel. Die beiden Robustheits-Fixes ändern Verhalten nur in
Fehlerfällen, die vorher falsch beantwortet wurden (4xx statt 500).
