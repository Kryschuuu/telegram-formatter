# Changelog

Alle relevanten Änderungen an diesem Projekt, formatiert nach
[Semantic Versioning](https://semver.org/) und
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

## [2.5.0] - 2026-09-13

**Security-Härtung für Web-Versand, BYOB-Session-Zugriff, Limits und CI.**

### Behoben

- **Shared-Chat-Versand geschlossen:** `POST /api/send` ist ohne
  `TELEGRAM_FORMATTER_API_TOKEN` deaktiviert. Mit gesetztem Token wird ein
  zeitkonstanter `X-Auth-Token`-Header verlangt. Der Browser erhält das
  Operator-Secret nicht; Shared-Versand ist API-only.
- **BYOB-Proof-of-Possession:** Jede Session liefert zusätzlich zum opaken
  Handle ein zufälliges `session_secret`. Send, Status und Close verlangen
  beide Werte; serverseitig wird nur ein SHA-256-Digest des Secrets gehalten.
  Das Frontend hält beide Werte ausschließlich im RAM.
- **Atomare BYOB-Kapazitätsprüfung:** Session-Limit, Per-IP-Kappe und
  Session-Erzeugung werden unter einem gemeinsamen Lock serialisiert, sodass
  parallele Requests die Kapazitätsgrenzen nicht überschreiten.
- **Proxy-Vertrauen:** `ProxyFix` ist im Direktbetrieb standardmäßig deaktiviert.
  `TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS` muss explizit gesetzt werden; der
  Render-Blueprint setzt für seine bekannte Proxy-Topologie `1`.
- **CI-Supply-Chain:** GitHub Actions sind auf vollständige Commit-SHAs
  gepinnt; Ruff, Bandit und pip-audit verwenden feste Versionen.

### Geändert

- BYOB-API-Dokumentation, Deployment-Hinweise, README und Sicherheitskommentare
  an das neue Session-Proof- und Shared-Auth-Modell angepasst.
- Version auf `2.5.0` erhöht.

### Tests

- BYOB-Tests senden nun das zusätzliche `session_secret`.
- Bestehende Testfälle für Shared-Versand müssen einen API-Token verwenden.

## [2.4.0] - 2026-09-13

**Security-Delta-Review: Fail-Closed für den Selbstbetrieb und
Review-Gate-Nachschärfung.** Umsetzung der Restbefunde (R-1/R-2) aus dem
Re-Triage des Audits 2.0.0 → 2.3.0 (siehe Status-Tabelle in
[`security/README.md`](security/README.md)): der letzte Relay-Pfad von
`/api/send` wird geschlossen und die AST-Statik fängt die zuvor noch offenen
Umgehungen (dynamische Ziel-URL, Deskriptor-Persistenz, `getattr`-Dispatch).

### Behoben (Sicherheit)

- **R-1 — Fail-Closed im Selbstbetrieb:** Läuft `TELEGRAM_BOT_TOKEN` **ohne**
  `TELEGRAM_CHAT_ID`, ist `TELEGRAM_FORMATTER_API_TOKEN` jetzt **Pflicht** —
  fehlt beides, antwortet `POST /api/send` mit `503` statt anonym beliebige
  Chats zu beliefern (vorher: offener Relay auf den Betreiber-Bot).
  Zusätzlich warnt der Start (Log), wenn die Konstellation ungeschützt ist.
- **R-2 — Review-Gate-Nachschärfung** (`botkit/review.py`):
  - **BK010 ist jetzt ein Blocker** (vorher Warnung): eine nicht statisch
    prüfbare Ziel-URL gilt nicht mehr als „sicher“, sondern als nicht
    verifizierbar — `requests.post(url)` / `f"https://{host}/…"` stoppen den
    Vorgang. Die f-String-Faltung löst bekannte Modul-Konstanten zusätzlich
    auf, damit legitime `f"{API_BASE}/bot{token}/…"`-Muster sauber bleiben.
  - **Deskriptor-Persistenz:** `os.open`/`io.open` mit Schreib-Flags
    (`O_WRONLY`, `O_RDWR`, `O_CREAT`, …) sowie `os.write`/`os.pwrite` lösen
    BK002 aus.
  - **Indirekter Dispatch:** `getattr(os, "system")(…)` (BK007) und
    `getattr(__builtins__, "eval")(…)` (BK003) werden erkannt.
- **R-3 — `cli.py --token` entfernt:** der Parameter legte den Bot-Token im
  Klartext in `ps` und Shell-Historie ab (Audit M-2). Token jetzt
  ausschließlich über `TELEGRAM_BOT_TOKEN` oder interaktiv (`botctl`).
- **R-4 — Client-Vorschau:** `app.js` entfernt NUL-Zeichen vor dem
  Platzhalter-Protokoll (Parität zu `utils.normalize_text`, Audit N-1) —
  Nutzer-NULs können den Vorschau-Restore nicht mehr kollidieren lassen.
- **R-5 — Gitleaks-Allowlist präzisiert:** freigegeben ist nur noch das
  exakte, öffentlich bekannte Negativbeispiel-Literal statt der ganzen
  Klasse `123456789:*` (ein echtes Token mit dieser Bot-ID bliebe sonst in
  README/CHANGELOG/docs unentdeckt).

### Geändert

- **Doku:** `security/README.md` enthält jetzt eine vollständige
  Findings-Status-Tabelle (K-1 … B-14 → behoben in welcher Version);
  `SECURITY_AUDIT.md` trägt einen Status-Banner, der auf diese Tabelle
  verweist. `docs/DECENTRAL_BOT_ARCHITECTURE.md` (BK-Tabelle),
  `.github/pull_request_template.md` (BK010 = Blocker), README und
  `docs/DEPLOYMENT.md` (API-Token-Pflicht im Selbstbetrieb) nachgezogen.
- **Version:** `telegram_formatter.__version__` auf `2.4.0`.

### Tests

- `tests/test_review.py`: BK010-als-Blocker, Deskriptor-Persistenz,
  `getattr`-Dispatch, readonly-`os.open`- und legitime-f-String-Gegentests.
- `tests/test_app.py`: Fail-Closed-Test (503 ohne Zugangsschutz im
  Selbstbetrieb); der Selbstbetrieb-Test setzt jetzt das API-Token voraus.
- `tests/fixtures/insecure_bot.py`: neue R-2-Negativmuster
  (`persist_via_descriptors`, `shell_via_getattr`, `exec_via_getattr`).
- Gesamtsuite: **305 passed**; `ruff check`, `bandit` (Voll) und `pip-audit`
  ohne Befund.

## [2.3.0] - 2026-09-12

**Sende-Bestätigung, Top-Warnung & Privatsphäre-Aufklärung.** Drei
UX-Maßnahmen gegen versehentliche Offenlegung: (1) Vor jedem Versand zeigt
ein **Bestätigungsdialog** den konkreten Absender-Bot, das konkrete Ziel
und eine Nachrichtenvorschau — mit echter Abbrechen-Möglichkeit (Button,
Escape, Backdrop-Klick). (2) Die Warnung zum öffentlichen geteilten Bot
steht jetzt **ganz oben auf der Seite** statt erst im BYOB-Abschnitt. (3)
Ein neuer Abschnitt **„Privatsphäre“** klärt auf, was @BotFather und
Telegram-Bots in Bezug auf Sichtbarkeit bedeuten (Bot-Profile sind öffentlich,
Bot-Chats nicht Ende-zu-Ende-verschlüsselt, Token = Schlüssel).

### Hinzugefügt

- **Sende-Bestätigungsdialog** (`#sendConfirm`, `static/js/app.js`):
  - Öffnet sich bei Klick auf „An Telegram senden“ und bei Strg/Cmd+Enter;
    erst „Jetzt senden“ löst den tatsächlichen Versand aus.
  - Zeigt drei Fakten: **Absender-Bot** (konkret: `@mdtotxt_bot (geteilter
    Bot dieser Seite)` bzw. `@dein_bot (dein eigener Bot)`), **Ziel**
    (gemeinsamer Chat — öffentlich / dein Chat — privat) und eine
    **Nachrichtenvorschau** mit Zeichenzahl (auf 280 Zeichen gekürzt).
  - Drei Hinweis-Varianten per Zustand: öffentliche Warnung (geteilter Bot
    aktiv), privat-Bestätigung (BYOB-Session aktiv), neutraler Hinweis
    (kein Versandweg konfiguriert).
  - Abbrechen per Button, Escape oder Klick auf den Backdrop — es wird
    nichts gesendet; der Fokus kehrt zum Auslöser zurück. Offene Dialoge
    fangen Tab-Fokus (Fokusfalle) und sind als `role="dialog"` mit
    `aria-modal` ausgezeichnet.
- **JS-Vertrag erweitert:** `window.tfByob.describeTarget()` liefert
  `{bot, chat}` der aktiven Session (oder `null`) — `app.js` befüllt daraus
  den Dialog. `<body>` trägt zusätzlich `data-shared-bot` und
  `data-configured`.
- **Top-Warnung** (`.tf-top-warning`): Die „Wichtig — der geteilte Bot ist
  öffentlich!“-Warnung steht als volle Breite direkt unter dem Header, vor
  Hero und Editor (nur gerendert, wenn der geteilte Bot konfiguriert ist);
  der Link zeigt auf den BYOB-Abschnitt. Die bisherige Kopie im
  BYOB-Abschnitt ist entfernt (keine Duplizierung).
- **Privatsphäre-Sektion** (`#privacy`): Vier Erklär-Karten — Was ist
  @BotFather (Verwaltungsbot, sieht nur Metadaten, Bot gehört zum eigenen
  Konto), Was ist ein Bot (Profil öffentlich, Inhalte nur im Chat, keine
  E2E-Verschlüsselung, Token = Lesezugriff), geteilter Bot (gemeinsamer
  Chat, öffentlich) und eigener Bot (BYOB, Token nur im RAM) — plus
  FAQ-Eintrag „Sind Bots und BotFather öffentlich einsehbar?“.
- **CSS-Komponenten** (`components.css`): `.tf-modal*` (Overlay-Dialog mit
  neutralem Backdrop), `.tf-facts` (dt/dd-Faktenliste), `.tf-note--ok`
  (grüne Gegenbox zu `--danger`), `.tf-top-warning`, `.tf-privacy*`.

### Geändert

- Howto-Schritt 5 und Editor-Tipp beschreiben jetzt die Bestätigung vor
  dem Versand („Versandweg prüfen & bestätigen“).
- `tests/test_jsdom_smoke.py` rendert die Seite im *konfigurierten* Zustand,
  damit der geteilte Versandweg (inkl. öffentlicher Warnung im Dialog)
  realistisch getestet wird; die jsdom-Spec prüft neu Dialog-Inhalte,
  Abbrechen- und Escape-Pfad für beide Versandwege.

## [2.2.0] - 2026-09-12

**BYOB auf der Website — dezentrale Bot-Sessions statt geteiltem
Öffentlich-Chat.** Umsetzung des in
[docs/DECENTRAL_BOT_ARCHITECTURE.md](docs/DECENTRAL_BOT_ARCHITECTURE.md)
beschriebenen Betriebsmodus B („gehostete Session“), der bislang als einziger
Modus als *nicht implementiert* galt. Konzept-Entscheidung: Der geteilte
Standard-Bot (`@mdtotxt_bot`) bleibt funktional (null Einrichtungshürde),
bekommt aber eine **dominante Privatsphäre-Warnung** — alles darüber
Gesendete landet im gemeinsamen Chat und ist für alle Besucher (und jeden, der
den Bot auf Telegram hinzufügt) sichtbar. Für private Inhalte ist ab sofort
der eigene Bot in einer ephemeren Web-Session der empfohlene Weg.

### Hinzugefügt (BYOB-Websessions — `botkit`-Betriebsmodus B)

- **Fünf API-Endpunkte** in `telegram_formatter/app.py` (alle POST, unter den
  bestehenden Guards Origin-Check/`X-Auth-Token`/Body-Limit):
  - `POST /api/byob/session` — Token-Formatprüfung, `getMe`-Verifikation über
    die RAM-`BotRegistry`, Öffnen einer ephemeren `BotSession`
    (TTL 30 min, Leerlauf 10 min, 20 Nachrichten/Min., Eingabelimit) und
    Rückgabe eines opaken Session-Handles plus nicht-geheimer Bot-Identität.
    Das Token wird nie gespeichert, geloggt oder in Antworten wiedergegeben.
  - `POST /api/byob/discover` — Chat-ID-Erkennung über `getUpdates` als
    reinen *Blick* (kein `offset`, nichts wird bestätigt/verbraucht);
    zurück kommen ausschließlich Chat-Metadaten (ID, Typ, Name) — nie
    Nachrichtentexte.
  - `POST /api/byob/send` — Versand über die Session (`session.send`):
    Teilfortschritt (`sent_before_error`), Telegram-429-Backoff
    (`retry_after`), Session-Ablauf als 410 mit Handlungsanweisung.
  - `POST /api/byob/status` — Live-Status (Restzeit TTL/Leerlauf, Zähler)
    für den Countdown im UI; `{"active": false}` nach Ablauf.
  - `POST /api/byob/close` — sofortiges Beenden; die Token-Referenz fällt.
- **Review-Gate-Entscheidung dokumentiert:** In der Web-Session läuft *kein*
  Nutzer-Code — nur die geprüften Konverter-/Versand-Module des Projekts.
  `require_review=False` ist deshalb per Konstruktion sicher; das
  Review-Verfahren (Statik BK001–BK012 + Vier-Augen-Prinzip) bleibt für
  eigenen Bot-Code über `botctl`/CI unverändert Pflicht.
- **Anti-Missbrauch:** separate IP-Rate-Limits für Öffnen/Erkennen/Senden,
  harte Kappen für aktive Sessions (100 insgesamt, 3 pro IP) mit
  `reap_expired`/`prune` vor jedem Öffnen; Schließen gibt Kappe sofort frei.
- **Session-Handle im Request-Body statt Cookie** (bewusste Abweichung vom
  Architektur-Entwurf): keine Ambient-Authority ⇒ kein CSRF-Risiko, kein
  Cookie-Flag-Fußabdruck, funktioniert auch in Kontexten mit blockierten
  Third-Party-Cookies. Der Handle lebt nur im JS-Speicher (kein
  `localStorage`), das Token-Feld wird nach dem Session-Start geleert.
- **Oberfläche:** neue Sektion „Versandweg: geteilter Bot oder eigener Bot
  (BYOB)?“ mit roter Gefahren-Warnbox (`.tf-note--danger`, neue
  `--danger-*`-Tokens in allen vier Themes + Auto-Fallback), 3-Schritte-
  Anleitung (BotFather → Chat-ID → Session), Formular (Passwort-Feld ohne
  Autocomplete, Consent-Checkbox), Chat-Erkennungs-Chips, Statuskarte mit
  Countdown/Zählern und „Session beenden“; `#sendPathNote` zeigt am
  Senden-Button den aktiven Weg an. Vier neue FAQ-Einträge (BYOB-Begriff,
  Token-Handling, Session-Ablauf, Review-Pflicht) + Howto/NoScript-Updates.
- **`static/js/byob.js`** — drittes JS-Modul neben `theme.js`/`app.js`:
  Session-Lebenszyklus, Validierungen, Countdown/Status-Poll (30 s),
  Chat-Erkennung; stellt `window.tfByob` bereit, an das `app.js` den
  Senden-Button delegiert (Fallback: geteilter Bot). Button-Label-Vertrag
  über `window.tfSendLabel`.

### Behoben

- **Wheel ohne Assets:** `[tool.setuptools.package-data]` nutzte die Globs
  `static/*.css`/`static/*.js`, die nur direkte Kinder treffen — `pip install`
  lieferte das Paket **ohne** `static/css/*`, `static/js/*` und `favicon.svg`
  (ungestyltes UI ohne Skripte). Korrigiert auf
  `static/css/*.css`/`static/js/*.js`/`static/favicon.svg`; neuer
  Vertragstest `test_package_data_covers_all_assets` vergleicht die Globs
  gegen den tatsächlichen Dateibestand.
- **Rate-Limits hinter Plattform-Proxys wirkungslos:** `request.remote_addr`
  war hinter Render & Co. die Proxy-Adresse — alle Besucher teilten sich
  *einen* Rate-Limit-Eimer (convert/send), und die BYOB-Per-IP-Kappe wäre
  zur Global-Kappe kollabiert. `ProxyFix(x_for=1, x_proto=1)` vertraut genau
  einer Proxy-Ebene; das Trade-off (XFF ist spoofbar ⇒ Limits sind
  Missbrauchs-Heuristik, keine Authentifizierung) ist im Code dokumentiert.
- **`BotSession`-Countdown-Basis:** neue öffentliche Properties
  `ttl_remaining_seconds`/`idle_remaining_seconds` (0 für geschlossene
  Sessions) — vorher war die Restzeit nur über private Interna berechenbar.

### Geändert

- **Betrieb:** BYOB-Sessions leben pro Prozess im RAM — der kanonische
  Start ist jetzt **ein** Gunicorn-Worker mit Threads
  (`gunicorn "telegram_formatter.app:app" --threads 8`, siehe `render.yaml`
  und `docs/DEPLOYMENT.md`). `--workers 2` ohne Sticky-Routing würde
  Sessions im anderen Prozess „verlieren“ (saubere 410-Meldung statt
  stiller Fehlfunktion).
- **`app.py`:** gemeinsame Validierungs-Helfer `_json_body()`/`_valid_text()`
  für alle Sendewege (keine duplizierte Textprüfung); Modul-Docstring
  dokumentiert beide Versand-Wege und die Guard-Abdeckung.
- **Konfiguration (neue Umgebungsvariablen, alle mit Default):**
  `TELEGRAM_FORMATTER_BYOB_ENABLED` (Abschalter für Betreiber),
  `TELEGRAM_FORMATTER_BYOB_SESSIONS_PER_MINUTE` (3),
  `TELEGRAM_FORMATTER_BYOB_DISCOVER_PER_MINUTE` (3),
  `TELEGRAM_FORMATTER_BYOB_SENDS_PER_MINUTE` (6),
  `TELEGRAM_FORMATTER_BYOB_TTL_SECONDS` (1800),
  `TELEGRAM_FORMATTER_BYOB_IDLE_SECONDS` (600),
  `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` (`@mdtotxt_bot`, Anzeige in der
  Warnung).
- **Doku:** README (Abschnitt „Versand-Wege auf der Website“, ENV-Tabelle,
  Badge 2.2.0), `docs/DECENTRAL_BOT_ARCHITECTURE.md` (Modus B jetzt
  implementiert; Endpunkte, Worker-Regel, Body-Handle-Begründung),
  `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `docs/DESIGN.md`,
  `botkit/tokens.py`-Hinweis N-5 aktualisiert; Changelog aufgeräumt — die
  beiden verwaisten `[Unreleased]`-Blöcke sind nun der Version 2.1.0
  zugeordnet, in der sie tatsächlich deployt wurden.
- `render.yaml`: Start-Kommando um `--threads 8` ergänzt (BYOB-tauglich,
  ein Prozess).

### Tests

- **Neu `tests/test_byob_web.py` (36 Tests):** Session-Öffnen (Happy-Path,
  Consent, Token-/Chat-Validierung, Verifikationsfehler ohne Token-Leak,
  Rate-Limit, IP-/Total-Kappe, Kappe-Freigabe nach Schließen, 404 bei
  Deaktiviert), Senden (eigenes Token in Sender-Aufrufen, 410 für
  abgelaufen/unbekannt, Session-Rate-Limit 429, Teilfortschritt 502,
  Telegram-429 mit `retry_after`, Eingabelänge), Status/Schließen
  (Token-Referenz fällt, Idempotenz, Meta-Prune), Chat-Erkennung (nur
  Metadaten — Nachrichtentexte erscheinen nachweislich nicht in der
  Antwort), Guards (Origin 403, Auth-Token 401, Non-JSON 400) und
  Template-Verträge (Panel/Warnung je Konfiguration).
- `tests/test_session.py`: Property-Test für die Restzeit-Berechnung.
- `tests/test_frontend.py`: `byob.js` in die JS-Verträge (IDs, Waisenfreiheit)
  aufgenommen; FAQ-/Hook-Vertrag um BYOB erweitert; neuer
  Paketierungs-Vertragstest (Wheel deckt alle Assets ab).
- `tests/frontend/jsdom_spec.cjs`: neuer Fall 5 — kompletter BYOB-Durchlauf
  im echten DOM (Validierung, Öffnen, aktive Anzeige, Token-Feld-Leerung,
  Senden-Routing, Chat-Chips, 410-Reset, Label-Wechsel).
- Gesamtsuite: **297 passed** (+1 jsdom-Skip ohne Node) — `ruff check` und
  `bandit -c pyproject.toml -r telegram_formatter` sauber.

## [2.1.0] - 2026-09-11

Sicherheitshärtung und Fehlerbehebungen als Umsetzung des externen
Code-Reviews ([`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)); die Nummern (K-*/H-*/
M-*/B-*) verweisen auf die Befunde dort. Enthält zusätzlich die beiden
vormaligen `[Unreleased]`-Blöcke (Design-System „tf“ & Themes sowie
Root-Shim + Render-Blueprint), die mit diesem Stand auf main deployt wurden.

### Behoben (Sicherheit — kritisch/hoch)

- **K-1:** `SendError` enthielt bei Netzwerkfehlern die Requests-Fehlermeldung
  samt URL — **und damit den Bot-Token im Klartext**, den `/api/send` an den
  HTTP-Client zurückgab. Fehlermeldungen nennen jetzt nur noch Statuscode,
  Exception-Klasse und gekürzte API-Description.
- **K-2:** `/api/send` war ein offener Relay: `chat_id` aus dem Request-Body
  überschrieb den konfigurierten Zielchat, ohne Authentifizierung. Jetzt:
  Chat-Pinning auf `TELEGRAM_CHAT_ID`, Validierung gegen `CHAT_ID_PATTERN`,
  optionales API-Token (`X-Auth-Token`), Origin-Bindung, Größen- (413) und
  Rate-Limits.
- **H-1:** Review-Gate-Bypasses geschlossen: Import-Alias-Auflösung
  (`requests as rq`), Modul-Konstanten-/f-String-Faltung für URL-Prüfung,
  Token-Literal-Scan in *allen* String-Konstanten (auch `AnnAssign`, Dicts,
  Call-Argumente), `importlib`/`tempfile`/`shutil`/`builtins` verboten.
- **H-2:** `MAX_CONTENT_LENGTH` gesetzt; Eingabelänge wie im botkit-Layer
  begrenzt; Rate-Limiter pro IP; Doku-Gunicorn-Flags (`--workers/--timeout`).
- **H-3/H-4:** Keine Rohtext- oder Traceback-Leaks mehr: `response.text` wird
  nicht übernommen; der `RedactingFilter` redigiert jetzt auch Exception-Stacks
  (die Handler formatieren nach dem Filtern).
- **H-5:** UI ohne Inline-Skripte (CSP-fähig), CDN-Skript versionsgepinnt
  (SRI beim Release nachziehen); CSP/nosniff/no-referrer/frame-ancestors-Header.
- **M-3/N-2:** `api_base` nur noch HTTPS (localhost ausgenommen);
  Gitleaks-Allowlist auf Pfad+Muster (`matchAll`) verschärft.

### Behoben (Funktional)

- **B-1:** Chunk-Grenzen rissen `<b>/<code>/…`-Tags, `$$`-Formelblöcke und
  ``` fences entzwei → Telegram-400 bzw. kaputtes Rendering. Neu:
  Tag-Balance pro Chunk (mit Nachtrag an den Folgechunk) und atomare
  Rich-Bereiche mit `chunk_markdown_safe`-Artiger Aufteilung.
- **B-2:** `botctl send --local-trust` brach immer mit „Bot ist nicht
  freigegeben" — der Gate wurde dem Manager fälschlich übergeben.
- **B-3:** `ReviewTicket.is_approved()` ignorierte Ablehnungen — ein
  abgelehnter Bot blieb im Local-Modus freigabefähig; Re-Submit erzeugt
  jetzt ein frisches Ticket.
- **B-4:** Preisangaben (`$100 und $200`) wurden als LaTeX fehlgeroutet;
  Inline-Math folgt jetzt GFM-Randregeln (in allen drei Scannern einheitlich).
- **B-5/B-6/B-7/B-8/B-9/B-10/B-11/B-12:** `text`-Typprüfung (400 statt 500),
  Teilversand-Rückmeldung + `retry_after`, `ok`-Flag-Prüfung,
  Verzeichnis-Eingabe für `botctl review`, `RegistrationError`-Fang in
  `approve`, Owner-Fallback `local`, Audit-Trail unter `flock`,
  Fixture-Restoration, Listen-Einrückung.
- **N-1:** NUL-Zeichen aus Nutertext entfernt (Platzhalter-Kollision).

### Geändert

- `chunk_text`/`_group`: inkrementelle Längenführung (O(n²) → O(n)).
- Versand nutzt eine wiederverwendete `requests.Session` (TLS-Pooling).
- `botkit`-Session/`InMemoryTokenVault`: Thread-safe (Locks); Sessions
  entfernen sich beim Schließen selbst aus dem Manager.
- CLI `--token` veraltet (ps/Historie); Environment/`botctl` empfohlen.
- CI: Bandit ohne `-ll`; `botctl verify` als aktiver Schritt (bei
  `vars.BOT_ID`/`vars.BOT_PATH`); Dependabot (pip, actions).
- Dependencies: `gunicorn` 23.0.0 → 26.2.0, `requests` 2.33.0 → 2.34.2.

### Hinzugefügt (Web-Oberfläche — Design-System, aus dem vormaligen [Unreleased]-Block)

> Die folgenden Blöcke standen bis v2.2.0 noch unter `[Unreleased]` — ihr Inhalt
> war jedoch bereits Teil des 2.1.0-Merges und wird hier der Version zugeordnet.

### Behoben (Web-Oberfläche — Design-Bruch)

- **Ungestylter Rohtext:** Die Seite hing am Tailwind-Play-CDN; dessen zur
  Laufzeit injizierten Inline-`<style>`-Regeln blockierte die CSP
  (`style-src` ohne `'unsafe-inline'`) — komplettes Layout fiel aus. Das UI
  ist jetzt **vollständig selbst-gehostet** (4 CSS-Schichten + 2 JS-Module +
  Inline-SVG-Icons unter `static/`), die CSP ist strikt `'self'` und jede
  CDN-Whitelist ist entfallen. Damit ist die Klasse dieses Fehlers strukturell
  ausgeschlossen; `tests/test_app.py` & `tests/test_frontend.py` erzwingen beides.

### Hinzugefügt (Web-Oberfläche — Design-System „tf“ & Themes)

- **Vier Themes + Auto-Modus:** Light (Standard), Dark (Telegram-Nacht),
  Colorful (Verlauf + Glas-Karten), Minimal (monochrom/kantig); „Auto“ folgt
  dem Betriebssystem ohne JavaScript-Anteil (reine `prefers-color-scheme`-
  Media-Query in `tokens.css`).
- **Theme-Switcher** im Sticky-Header (`#themeSwitcher`): `localStorage`
  (`tf-theme`), `aria-pressed`-Status, Zustandsklasse `.is-active`,
  Boot synchron im `<head>` (kein Flash of wrong theme), robust ohne
  `localStorage`/`matchMedia`, No-JS-Fallback aufs Systemtheme.
- **Selbst-gehostetes Design-System:** `static/css/tokens.css|base.css|
  layout.css|components.css` (Token-Schicht, Reset/Typo, Responsive-Grid,
  `.tf-*`-Komponenten) — Farbhartkodierung außerhalb der Tokens ist durch
  Tests verboten; Themewechsel = ein Attribut, kein Markup.
- **UX-Schmuck:** Zeichenzähler (warnend > 4096), Strg/Cmd+Enter = senden,
  Skip-Link, `role="status"`-Live-Region, Noscript-Hinweis, Favicon (SVG),
  reiche Live-Vorschau (Code, Durchstreichen, Links, Formel-Highlight —
  weiterhin escaping-first, kein HTML-Injection-Weg).
- **Dokumentation:** [docs/DESIGN.md](docs/DESIGN.md) (Architektur, Theme-
  Rezept, Switcher-Verhalten, Teststrategie, Erweiterungs-Guide).

### Geändert (Web-Oberfläche)

- `telegram_formatter/templates/index.html` neu geschrieben: semantische
  `.tf-*`-Klassen statt Tailwind-Utilities; **alle Funktions-Hooks bleiben
  contract-getestet erhalten** (`#input`, `#preview`, `#payloads`, `#sendBtn`,
  `#resetBtn`, `#sendStatus`, `data-convert-url`/`data-send-url`, Howto/FAQ/
  Disclaimer/Footer). `static/app.css`/`app.js` → `static/css/*` + `static/js/*`.
- `telegram_formatter/app.py`: CSP vereinfacht (nur `'self'`, `data:` für
  Favicons); Header-Text accordingly. Endpunkte, Limits, Rate-Limits,
  Auth/Guards unverändert.

### Tests

- `tests/test_frontend.py`: 20 Strukturverträge (Token-Vollständigkeit je
  Theme, `var()`-Abdeckung, Asset-Existenz & Waisenfreiheit, JS↔HTML-IDs,
  Switcher-Markup, mobile-first-Breakpoints, Kontrast-Heuristik,
  Browser-Baseline-Verbotsliste, HTML-Wellformedness).
- `tests/frontend/jsdom_spec.cjs` + `tests/test_jsdom_smoke.py`: funktionale
  DOM-Tests (Theme-Boot/Klicks/Persistenz, Debounce+Fetch, Vorschau,
  Sende-/Fehlerpfad, Reset) gegen das echt gerenderte Template — 37 Checks;
  sauberer Skip ohne Node/jsdom.
- Bestehende Suite unverändert grün: **259 passed** (`pytest -q`).

### Behoben (Deploy-Start nach der Paket-Umstellung)

- **`ModuleNotFoundError: No module named 'app'` auf Render.com.** Der dort
  hinterlegte Start-Befehl `gunicorn app:app` — der Python-Default *vor* der
  Umstellung — zeigte nach 2.0.0 auf ein nicht mehr existierendes Root-Modul:
  Build erfolgreich (`== Build successful`), Start abgestürzt. Der kanonische
  Einstieg ist `telegram_formatter.app:app`; für noch nicht umgestellte
  Deployments leitet ab sofort ein Root-Shim weiter, der Blueprint stellt den
  Befehl dauerhaft richtig.

### Hinzugefügt (Deploy-Kompatibilität)

- **`app.py` (Repository-Wurzel) — veralteter Kompatibilitäts-Shim.**
  Einzeilige, rein weiterleitende Adresse (`from telegram_formatter.app import
  app`), damit ein im Hosting-Dashboard hinterlegtes `gunicorn app:app` ohne
  Dashboard-Änderung sofort wieder läuft. **Als veraltet markiert; Entfernung
  mit 3.0.0.** Abgesichert durch zwei Tests in `tests/test_app.py`:
  (1) identisches WSGI-Objekt plus Routen-/HTTP-200-Rauchtest über den Shim,
  (2) AST-Vertrag, der den Shim auf Docstring + einen Re-Export beschränkt
  (keine Funktionen, Klassen, Aufrufe, Control-Flow) — so verkommt er nie zur
  zweiten Logik-Kopie.
- **`render.yaml` — Render-Blueprint.** Deklariert den kanonischen Start
  `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT`,
  `healthCheckPath: /`, `PYTHON_VERSION` `3.11` sowie `TELEGRAM_BOT_TOKEN` und
  `TELEGRAM_CHAT_ID` mit `sync: false` (Secrets bleiben im Dashboard-Store und
  landen nie im Git). Blueprint-Sync gilt nur für aus dem Blueprint erzeugte
  Dienste; Ablauf für bestehende Services: `docs/DEPLOYMENT.md`, Schritt 3a.

### Geändert (Deploy-Kompatibilität)

- **Doku nachgezogen:** `docs/DEPLOYMENT.md` (Blueprint-Alternative,
  Sync-Hinweis für bestehende Services, Troubleshooting-Eintrag zum
  `ModuleNotFoundError`), `MIGRATION.md` §3 (Deployment-Fallback: Root-Shim ↔
  kanonischer Einstieg), README-Strukturbaum, `docs/ARCHITECTURE.md` §5
  (Konfigurationstabellen um Shim und Blueprint ergänzt).
- **CI:** Ruff prüft das Root-`app.py` ausdrücklich mit
  (`ruff check app.py telegram_formatter examples tests`); der
  `pull_request.paths`-Filter löst den Workflow jetzt auch bei Änderungen an
  `app.py`/`render.yaml` aus. `.github/CODEOWNERS` markiert `/app.py` und
  `/render.yaml` als review-pflichtig (beide ändern den laufenden Dienst).

### Verifiziert (Deploy-Kompatibilität)

- `pytest -q` → **180 passed** (178 Baseline aus 2.0.0 + 2 Shim-Tests);
  `ruff check app.py telegram_formatter examples tests` sauber; Bandit `-ll`
  ohne Befund.
- Lokale Replikation des Render-Starts: `gunicorn app:app` (alt, über den Shim)
  und `gunicorn "telegram_formatter.app:app"` (kanonisch) liefern beide
  HTTP 200. Der Footer sagt „GNU GPL v3 · Version 2.0.0" — die Version kommt
  aus `telegram_formatter.__version__` und nicht mehr aus einer hartkodierten
  Angabe (zuvor auf „MIT · 1.2.0" verdriftet).

### Hinweise (bewusst nicht Teil dieses Releases)

- Parser-Ein-Pass-Rewrite (O-1) und Hash-Lockfiles (plattformabhängig) sind
  als separate Änderungen vorgesehen; Details im Audit-Bericht.
## [2.0.0] - 2026-09-11

### Geändert (Repository-Reorganisation — verhaltensneutral)

- **Paketstruktur:** `utils.py`, `sender.py`, `cli.py`, `app.py`, `botctl.py`,
  `botkit/` und `templates/` in das Python-Paket `telegram_formatter/`
  überführt; zentrale Fassade `telegram_formatter/__init__.py` mit
  `__version__` und bequemen Re-Exports (`build_messages`, `send_message`).
  Imports: `from utils import …` → `from telegram_formatter.utils import …`;
  CLI-Aufrufe: `python cli.py …` → `python -m telegram_formatter.cli …`,
  analog `botctl`. Deployment: `gunicorn "telegram_formatter.app:app"`.
  Vollständige Alt→Neu-Mappe: [MIGRATION.md](MIGRATION.md).
- **Doku nach Zweck getrennt:** `docs/` (aktuelle Architektur/Doku),
  `peer-review/` (Review-Berichte + Verlauf inkl. `archive/` für
  abgeschlossene Artefakte), `audit/` (Prüf-Matrix, Audit-Trail), `security/`
  (Schutzziel-Matrix). `docs/BLUEPRINT.md` → `docs/ARCHITECTURE.md`;
  `docs/CODE_REVIEW.md` → `peer-review/CODE_REVIEW.md`; `docs/PROMPT.md` und
  beide `docs/PR_DESCRIPTION*.md` (abgeschlossene Task-Artefakte zu
  v1.1.0/v1.3.0) → `peer-review/archive/`.
- **Konfiguration konsolidiert:** `ruff.toml` und der `conftest.py`-Sys.path-
  Hack aufgelöst in eine `pyproject.toml` (PEP 621: Paket-Metadaten,
  Konsolen-Skripte `telegram-formatter`/`botctl`, Ruff-, pytest- und
  Bandit-Konfiguration; Laufzeit-Deps dynamisch aus `requirements.txt` —
  keine duplizierten Pin-Listen).
- **Audit-Trail umgezogen:** Standardpfad `botctl` von `.botkit/reviews.json`
  → `audit/reviews.json` (versionierbarer, dokumentierter Ort; Verhalten
  identisch).
- **Alle Verlinkungen aktualisiert:** README-Strukturbaum & -kommandos,
  `docs/*`, CI-Workflow (`paths`-Filter, Lint-/Scan-Ziele, `botctl`-Aufrufe),
  `.github/CODEOWNERS`, PR-Template, Docstrings und Beispiel-Code.

### Hinzugefügt

- `LICENSE` — fehlte trotz README-badge „MIT"; im Zuge der Reorganisation
  angelegt und an die Lizenz-Umstellung auf `main` (`a2c3dcb`)
  **angleichen: GNU GPL v3** (`license = "GPL-3.0-or-later"` in
  `pyproject.toml`, README-Badge angepasst).
  `CONTRIBUTING.md`, `MIGRATION.md`, `.github/SECURITY.md`
  (Offenlegungsprozess, von GitHub erkannt), `security/README.md`
  (Schutzziel-Matrix S1–S8 mit Verifikationszuordnung), `audit/README.md`,
  `peer-review/README.md` (Konventionen) + `peer-review/TEMPLATE.md`
  (Review-Berichtsvorlage), `bots/README.md` (dokumentierter Ort für
  Nutzer-Bots — die CI/CODEOWNERS-Pfade `bots/**` existierten bereits ohne
  Ordner).

### Entfernt (toter Code & Redundanz)

- `utils.RICH_MESSAGE_MAX_BLOCKS` — definiert, im gesamten Repo ungenutzt.
- `botkit.review.INFO` — dritter Severity-Wert ohne jede Referenz
  (RULES kennen nur `BLOCKER`/`WARNING`).
- `botkit.__version__` — duplizierte die Projektversion und driftete bereits;
  einzige Quelle ist jetzt `telegram_formatter.__version__`.
- `conftest.py` (17 Zeilen sys.path-Manipulation) — ersetzt durch deklarativen
  `pythonpath`-Eintrag in `pyproject.toml`.
- Veraltete README-Doppelungen (Strukturbaum vs. Doku-Index führten
  verschiedene, teils inkonsistente Dateilisten) — konsolidiert.

### Behoben (Review-Gate-Härtung nach erstem CI-Lauf)

- **CI `bot-gate`:** Die Diff-Schleife zieht jetzt nur noch `*.py`-Dateien
  durch `botctl review` (`grep -E '\.py$'`). Zuvor stürzte das Gate ab, weil
  das neue `bots/README.md` vom `bots/**`-Filter erfasst und in den
  AST-Parser gefüttert wurde.
- **`botctl review`:** nicht-parsbare Eingaben (Markdown, Binärdateien,
  Nullbytes) werden als Eingabefehler gemeldet — Exit-Code 2, klare Meldung,
  **kein** Ticket und **kein** Audit-Trail — statt mit rohem Python-Traceback.
  Regressionstests: `tests/test_botctl.py` (3).

### Verifiziert

- `pytest -q` → **178 passed** (175 aus v1.3.0 unverändert + 3 Regressionstests
  für `botctl review`); `ruff check .` sauber; Bandit `-ll` ohne Befund.
- End-to-End-Rauchtests: CLI-Dry-Run, Flask-Testclient (`/`,
  `/api/convert`, `/api/send`), `botctl`-Umlauf `review → approve×2 →
  verify` inkl. Ledger-Roundtrip gegen `examples/own_bot/minimal_bot.py`.
- Alle internen Markdown-/Quelltext-Links und relativen Pfade skriptgeprüft
  (0 defekte Referenzen nach der Migration).

## [1.3.0] - 2026-09-11

### Hinzugefügt

- **Dezentrales Bot-Konzept (BYOB — Bring Your Own Bot)**: Neues Paket
  `botkit/` als Alternative zum zentralen Bot. Nutzer registrieren ihren
  eigenen Bot, lassen den Code reviewen und nutzen ihn in einer ephemeren
  Session — ohne zentrale Datenspeicherung.
- **`botkit/tokens.py`**: `BotToken`-Umschlag (Klartext nur über `reveal()`,
  `repr` redacted), `InMemoryTokenVault` (RAM + TTL) und
  `PassthroughTokenVault` (strengster Modus, speichert nichts).
- **`botkit/privacy.py`**: `RedactingFilter` für alle Logger (inkl. urllib3),
  `audit()` als einzige Log-Schnittstelle (nur Metadaten), prozesslokale
  HMAC-Fingerprints, `scrub_environment()` gegen Token-Vererbung.
- **`botkit/registry.py`**: Registrierung mit `getMe`-Verifikation
  (`is_bot`, ID-Gegenprobe) — speichert Identität und Status, **niemals** das
  Token; Validierung für `chat_id` und Besitzer-Pseudonym.
- **`botkit/review.py`**: AST-basierte Regeln BK001–BK012 (Persistenz,
  Fremdnetzwerk, dynamische Ausführung, hartkodierte Secrets, Inhalte im
  Log), Checkliste C1–C9, `ReviewLedger` (Append-only, Metadaten) und
  `ReviewGate` mit Vier-Augen-Prinzip (2 Freigaben, ≥1 Maintainer:in,
  Freigabe an die SHA-256-Prüfsumme des Codes gebunden).
- **`botkit/session.py`**: `BotSession`/`SessionManager` mit TTL,
  Leerlauf-Timeout, Rate-Limit-Fenster und Eingabevalidierung; Versand über
  die bestehenden Module `utils.build_messages` + `sender.send_message`.
- **`botkit/telegram_api.py`**: `getMe`, `getUpdates`, `setWebhook`,
  `deleteWebhook` — die einzigen erlaubten API-Aufrufe.
- **`botctl.py`**: CLI mit `register`, `review`, `approve`, `verify`, `send`
  und `checklist`.
- **Referenz-Bot** `examples/own_bot/minimal_bot.py` (besteht alle BK-Regeln)
  und **Negativbeispiel** `tests/fixtures/insecure_bot.py` (löst BK001–BK012
  aus).
- **Review-Infrastruktur**: `.github/workflows/bot-review.yml`
  (Ruff, Bandit, pip-audit, gitleaks, `botctl review`, pytest),
  `.github/CODEOWNERS`, PR-Template mit Pflicht-Checkliste.
- **Doku**: `docs/DECENTRAL_BOT_ARCHITECTURE.md` (Architektur, Nutzerreise,
  Hürden-Tabelle, Security-Durchsetzung, Peer-Review-Prozess,
  Vergleich mit dem zentralen Bot).

### Geändert

- `conftest.py` (neu) sorgt dafür, dass Projekt-Root und `tests/` beim Import
  auflösbar sind.
- Bestehende Module (`utils.py`, `sender.py`, `cli.py`, `app.py`) bleiben
  unverändert — `botkit` ergänzt das Projekt nur.

### Behoben (Audit-Runde CI)

- **pip-audit**: `requests` von 2.32.4 auf **2.33.0** angehoben
  (`PYSEC-2026-2275`), `pytest` von 8.3.5 auf **9.1.1** (`PYSEC-2026-1845`).
  Beide Audits (Laufzeit und Entwicklung) sind damit ohne Befund.
- **gitleaks**: das Beispiel-Token in den Docstrings von
  `examples/own_bot/minimal_bot.py` und `botkit/tokens.py` war ein
  Dummy-Wert, löste aber zu Recht den Secret-Scan aus — ersetzt durch
  Platzhalter (`<token-von-botfather>` bzw. `os.environ[...]`).
- **gitleaks (Negativbeispiel)**: das Token in
  `tests/fixtures/insecure_bot.py` steht jetzt über zwei Zeilen
  (implizite String-Konkatenation). Der AST faltet es zu einer Konstante,
  die BK006-Regel greift weiterhin; der zeilenbasierte Secret-Scanner
  schlägt nicht mehr an.
- **`.gitleaks.toml`** ergänzt: eng begrenzte Freigaben für die
  Negativbeispiele unter `tests/fixtures/` sowie für den historischen
  Dokumentations-Dummy in alten Commits.
- **Workflow `bot-review.yml`**: Berechtigungen für PR-Kommentar und
  SARIF-Upload (`pull-requests: write`, `actions: write`) ergänzt —
  damit entfällt der Fehler „Resource not accessible by integration";
  außerdem wöchentlicher Termin-Check der Abhängigkeiten und Audit der
  Entwicklungs-Abhängigkeiten.

### Tests

- 175 Tests gesamt (vorher 81): 94 neue Tests für Token-Handling, Redaction,
  Registry, Review-Gate (Vier-Augen, Prüfsummen-Bindung) und Sessions
  (TTL, Leerlauf, Rate-Limit, keine Inhalte in Logs).

## [1.2.0] - 2026-09-02

### Hinzugefügt

- **DeepSeek/Gemini-LaTeX-Syntax**: Unterstützung für `\(...\)` (Inline-Math)
  und `\[...\]` (Display-Math) zusätzlich zum klassischen `$...$`/`$$...$$`.
  Diese Syntax wird von DeepSeek Chat, Gemini und anderen KI-Tools verwendet.
  Formeln werden korrekt als LaTeX erkannt und als Rich-Message versendet.
- **Neue Funktion `convert_deepseek_latex_syntax()`** in `utils.py`: normalisiert
  `\(...\)` → `$...$` und `\[...\]` → `$$...$$`. Eingebunden in
  `markdown_to_rich_markdown()` und `markdown_to_html()`.

### Behoben

- **Telegram rendert DeepSeek-Delimiter nicht**: Bisher wurden `\(...\)`/`\[...\]`
  zwar als LaTeX *erkannt* (Rich-Pfad), aber unverändert in die Payload
  geschrieben — Telegram gab sie dadurch als rohen Text mit Backslashes aus.
  Die Delimiter werden jetzt in die von Telegram unterstützte Dollar-Syntax
  übersetzt, während der Formelinhalt 1:1 erhalten bleibt.

### Geändert

- Entfernt: ungenutzte Hilfsfunktion `_find_delimiter_close()` (toter Code, der
  zudem eine `DeprecationWarning` wegen ungültiger Escape-Sequenz auslöste).
- Dokumentation (`README.md`, `templates/index.html`) beschreibt die
  DeepSeek/Gemini-Syntax; Versionsangabe der Web-Oberfläche auf 1.2.0 angehoben.

### Tests

- 81 Tests gesamt (vorher 66), davon 26 rund um die DeepSeek/Gemini-Syntax:
  Erkennung (`split_formulas`, `has_latex`), Konvertierung
  (`convert_deepseek_latex_syntax`) sowie die Payload-Ausgabe über
  `build_messages()`. Abgedeckt sind gemischte Delimiter, mehrzeilige
  Display-Formeln, verschachtelte Klammern, Preisangaben (`$ 20`),
  unvollständige Delimiter, doppelte Backslashes und Code-Schutz.

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
