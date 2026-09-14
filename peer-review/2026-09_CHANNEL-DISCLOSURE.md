# Review: Ziel-Kanal-Offenlegung & Web-Schicht (v2.9.0)

- **Datum:** 2026-09-14
- **Reviewende:** Arena-Agent (Peer-Review im Auftrag des Maintainers)
- **Gegenstand:** `389a25b` (Branch-Basis) → v2.9.0; Dateibereich
  `telegram_formatter/app.py`, `telegram_formatter/templates/index.html`,
  `telegram_formatter/static/js/app.js`, `telegram_formatter/static/css/components.css`,
  `render.yaml`, `docs/`, `README.md`
- **Ergebnis:** ✔ freigegeben (alle BLOCKER/WARNING behoben, Tests grün)

## Kontext

Meldung aus dem Betrieb: *„nirgends steht der Kanal, in dem die Nachrichten
gepostet werden, sofern man keinen eigenen Bot verwendet."* Die Seite warnte
zwar mehrfach vor „einem öffentlichen, gemeinsamen Chat", nannte aber nie,
**welcher** Chat das ist (`https://t.me/mdtotxt_bot_web`), und schwieg zur
automatischen Löschung nach einem Monat. Im Zuge der Umsetzung wurde die
gesamte Web-Schicht (Backend-Routen, Template, `app.js`, `byob.js`, CSS,
Blueprint, Doku) erneut gegen die Kategorien `security`, `correctness`,
`privacy`, `maintainability`, `tests` geprüft.

## Befunde

### [BLOCKER] Ziel der Shared-Sendungen wird nicht benannt

- **Fund:** `telegram_formatter/templates/index.html:124`, `:190`, `:390`,
  `:572`, `:825` (Stand `389a25b`) — privacy / correctness
- **Begründung:** Alle fünf Warnstellen formulierten anonym („ein
  öffentlicher, gemeinsamer Chat", „die Gruppe oder den Kanal"). Damit war die
  Einwilligung, die der Bestätigungsdialog über `confirm_public: true`
  einholt, **nicht informiert**: Besuchende konnten weder prüfen, wo die
  Nachricht landet, noch den Kanal vorab lesen, noch ihre Inhalte danach
  wiederfinden. Auch die Aufbewahrungsdauer (Telegram-Auto-Löschen: 1 Monat)
  kam nirgends vor — weder im UI noch in der Doku. Für einen Dienst, dessen
  einziger Default-Versandweg öffentlich ist, ist das die zentrale
  Aufklärungslücke.
- **Fix:** Kanal wird Konfiguration
  (`TELEGRAM_FORMATTER_SHARED_CHAT_URL`, Standard
  `https://t.me/mdtotxt_bot_web`; `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS`,
  Standard `30`) und aus **einer** Quelle (`app.py::_shared_channel`) in
  Template, `<body>`-Attribute und `via`-Feld von `/api/send` gespeist.
  Sieben Stellen im UI: Top-Warnung, neues Kanal-Banner `.tf-channel` im Hero,
  Hinweis am Senden-Button, Bestätigungsdialog (eigene Faktenzeile „Kanal
  (öffentlich)" mit klickbarem Link + Löschfrist, Kanal im Warnbox-Titel und
  im Bestätigungs-Knopf), Privatsphäre-Sektion, zwei neue FAQ-Einträge,
  Footer/Howto; die Erfolgsstatusmeldung nach dem Senden wiederholt Bot und
  Kanal. Absicherung: `tests/test_shared_channel.py`.

### [WARNING] `HTTPException` wird zu 500 geflattet

- **Fund:** `telegram_formatter/app.py:605` (`@app.errorhandler(Exception)`) —
  correctness
- **Begründung:** Flask sucht den Fehlerhandler über die MRO der Exception.
  Eine `HTTPException` ohne eigenen Code-Handler (z. B. `abort(400)`, 408,
  414, 503) trifft deshalb den allgemeinen `Exception`-Handler: aus einem
  Client-Fehler 4. Ordnung wurde `500 {"error": "Interner Fehler"}` — plus
  `app.unhandled_error`-Warnung im Log, die einen Serverdefekt vortäuscht.
  Reproduziert: `abort(400)` → `500`, `abort(418)` → `500`.
  Erreichbar ist der Pfad heute über Werkzeug-Ausnahmen ohne eigenen Handler
  (z. B. `RequestURITooLarge`) und über jeden künftigen `abort()`-Aufruf; die
  503-/500-Semantik des Shared-Versands war dadurch latent falsch.
- **Fix:** Neuer `@app.errorhandler(HTTPException)` erhält den Status und
  liefert eine feste, HTML-freie JSON-Meldung (`_HTTP_ERROR_TEXTS`); die
  code-spezifischen Handler (404/405/413) bleiben laut Flask-Lookup
  vorrangig. Test: `test_http_exception_handler_keeps_the_status_code`.

### [WARNING] `_RATE_HITS` wächst unbegrenzt (Speicherleck)

- **Fund:** `telegram_formatter/app.py:305`, `:329` — maintainability / DoS
- **Begründung:** `_RATE_HITS` ist ein `defaultdict(deque)` mit Schlüssel
  `(bucket, client_ip)`. `_rate_limited` leert abgelaufene Treffer per
  `popleft`, **löscht aber nie den Schlüssel**. Damit bleibt für jede jemals
  gesehene Adresse und jeden Bucket ein Eintrag stehen — bei einem
  langlebigen Gunicorn-Prozess mit öffentlichem Traffic wächst das Dict
  monoton (5 Buckets × Millionen Adressen). Der BYOB-Runtime hat dasselbe
  Problem nicht: dort räumt `prune()`/`reap_expired()` auf.
- **Fix:** `_prune_rate_buckets()` entfernt leere Eimer; `_rate_limited` ruft
  die Funktion amortisiert ab `_RATE_PRUNE_THRESHOLD` (4096) Einträgen unter
  derselben Sperre auf (`_RATE_LOCK` dafür `Lock` → `RLock`, sonst
  Selbst-Deadlock). Tests: `test_prune_rate_buckets_removes_empty_entries`,
  `test_rate_limited_prunes_when_buckets_pile_up`.

### [WARNING] ENV-Zahlen ohne Validierung — Start-Crash und stille Limit-Abschaltung

- **Fund:** `telegram_formatter/app.py:155`, `:186`, `:251`, `:254` (nackte
  `int(...)`/`float(...)` auf `os.environ.get`) — correctness / security
- **Begründung:** Zwei Fehlerbilder aus einem Tippfehler im Hosting-Dashboard:
  (1) `TELEGRAM_FORMATTER_SENDS_PER_MINUTE=6/min` lässt den Dienst mit einem
  nackten `ValueError`-Traceback beim Import sterben — kein Health-Check,
  keine Meldung, die auf die Variable zeigt; (2) `…=-1` ist *parsbar* und
  schaltet das Limit über `if limit <= 0: return False` lautlos ab. Gleiches
  Muster bei `BYOB_TTL_SECONDS`: `0` oder `-5` ergeben eine Session, die
  sofort abgelaufen ist.
- **Fix:** `_env_int(name, default, *, minimum, maximum)` ist jetzt die
  einzige Stelle, die ENV-Zahlen parst: unparsbar oder außerhalb des
  Bereichs ⇒ Log-Warnung + dokumentierter Standardwert. `0` bleibt dort ein
  gültiger Wert, wo er Bedeutung hat (Limit aus / „keine Aussage").
  `BYOB_TTL_SECONDS`/`BYOB_IDLE_SECONDS` mit Untergrenzen 60/30 s. Tests:
  `test_env_int_falls_back_loudly`, `test_env_int_rejects_out_of_range_and_caps`.

### [INFO] `SHARED_BOT_HANDLE` im BYOB-Konfigurationsblock

- **Fund:** `telegram_formatter/app.py:258` — maintainability
- **Begründung:** Die Anzeige-Konstante des *geteilten* Bots stand zwischen
  `BYOB_IDLE_SECONDS` und `BYOB_MAX_SESSIONS_TOTAL` — wer die
  Shared-Konfiguration suchte, fand sie nicht; wer BYOB-Grenzen änderte,
  stolperte über eine fremde Konstante.
- **Fix:** Umgezogen in den Shared-Abschnitt, direkt neben
  `SHARED_CHAT_URL`/`SHARED_RETENTION_DAYS` (reine Ordnungsänderung, kein
  Verhaltensunterschied — Modul-Global bleibt monkeypatchbar).

### [INFO] Bot-Handle im FAQ hartkodiert

- **Fund:** `telegram_formatter/templates/index.html:824`
  (`<code>@mdtotxt_bot</code>`) — maintainability
- **Begründung:** Sieben Stellen nutzten `{{ shared_bot_handle }}`, diese eine
  den literalen String. Ein Betreiber mit eigenem Shared-Bot
  (`TELEGRAM_FORMATTER_SHARED_BOT_HANDLE`) sah im FAQ einen fremden Bot —
  dieselbe Drift-Klasse wie der befundene BLOCKER.
- **Fix:** Durch `{{ channel.bot }}` ersetzt; zusätzlich ersetzen die Makros
  `channel_link()`/`channel_noun()`/`retention_note()` die sieben
  handgeschriebenen Fassungen derselben Aussage durch eine Definition.

### [INFO] Kontext-Variable `shared_bot_handle` vs. View-Model

- **Fund:** `telegram_formatter/app.py:690` (`index()`) — maintainability
- **Begründung:** Mit Kanal-Link, Kurzname und Löschsatz wären vier einzelne
  Template-Variablen entstanden, die dieselbe Konfiguration doppelt abbilden
  (und die JS-Attribute ein drittes Mal).
- **Fix:** `index()` übergibt **ein** Dict `channel` (gebaut von
  `_shared_channel()`); Template-Makros und `app.js` lesen dieselben Felder.
  `/api/send` nutzt dieselbe Funktion für `via` — API und UI können dadurch
  nicht mehr unterschiedliche Ziele behaupten.

### [INFO] Klickziel aus Betreiber-Konfiguration

- **Fund:** neu eingeführt (`channel_link()` rendert `href="{{ channel.url }}"`)
  — security
- **Begründung:** Ein `href`, der aus einer ENV-Variablen kommt, ist ein
  potenzielles Einschleusungsziel (`javascript:`, Fremd-Domain, Phishing via
  `https://t.me.evil.example/…`). Die CSP blockiert `javascript:`-Navigation
  nicht zuverlässig in allen Browsern, und `form-action`/`navigate-to` greifen
  hier nicht.
- **Fix:** `normalize_public_chat_url` akzeptiert ausschließlich
  `https://t.me/<handle>`, `t.me/<handle>`, `@<handle>` und prüft Handle-Länge
  (5–32), Anfangszeichen und Abwesenheit von Query/Fragment/zusätzlichen
  Pfadsegmenten; `http://` wird bewusst **nicht** auf `https` angehoben,
  sondern abgelehnt. Alles andere ⇒ `None` ⇒ kein Link im UI. Jeder
  gerenderte Kanal-Link trägt `rel="noopener noreferrer"`. 17 Negativfälle in
  `test_normalize_rejects_unsafe_or_private_targets`.

## Angenommen / abgewogen

1. **Kanal-Link und Löschfrist werden nicht zur Laufzeit gegen Telegram
   verifiziert** (`getChat`). Begründung: ein Netzwerk-Call im Start-Pfad bzw.
   pro Request wäre eine neue Fehlerquelle (Timeout, Rechte, Caching) für eine
   reine Anzeige-Aussage. Stattdessen: Fail-Log beim Start, wenn ein
   Kanal-Link ohne gepinnten `TELEGRAM_CHAT_ID` gesetzt ist
   (`app.shared_channel_unpinned`), keine Kanal-Aussage ohne Pinning
   (`_shared_channel` liefert dann `url=None`), und eine dokumentierte
   Konsistenzpflicht in `docs/DEPLOYMENT.md` (Schritt 4b) + `render.yaml`.
2. **`via.chat_id` bleibt in der Antwort von `/api/send`.** Die numerische ID
   eines *öffentlichen* Kanals ist kein Geheimnis (sie ist über den Kanal
   selbst ermittelbar) und hilft bei der Fehlersuche; `chat_url` wurde
   ergänzt, nicht ersetzt. Für Instanzen mit privatem Zielchat ist der
   anonyme Weg ohnehin geschlossen (`confirm_public` + Pinning).
3. **`SHARED_RETENTION_DAYS = 0` bedeutet „keine Aussage", nicht „sofort
   gelöscht".** Ein Betreiber ohne Auto-Löschen soll nicht gezwungen sein,
   eine falsche Frist anzuzeigen; die FAQ formuliert für diesen Fall
   ausdrücklich, dass Beiträge dauerhaft öffentlich bleiben.
4. **`#sendConfirmChannelLink` behält `href="#"` im Markup.** Die Zeile ist
   `hidden` und wird nur eingeblendet, wenn `data-shared-chat-url` nicht leer
   ist (`renderChannelFact`). Ein leeres `href` wäre ebenfalls ein
   No-Op-Link; der Test `test_missing_channel_url_degrades_to_text_only`
   pinnt beides (genau ein `href="#"`, Zeile versteckt).
5. **`sendPathNote` bleibt reiner Text (kein Link).** `app.js` schreibt dort
   `textContent`; ein serverseitig gerendertes `<a>` wäre nach dem ersten
   Weg-Wechsel verschwunden — Anzeige-Drift, genau die Klasse Bug, die 2.6.0
   beseitigt hat. Klickbare Links stehen an den sechs anderen Stellen.

## Follow-ups

- [ ] Gehostete Instanz: `TELEGRAM_CHAT_ID`,
      `TELEGRAM_FORMATTER_SHARED_CHAT_URL` und
      `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` im Render-Dashboard auf
      denselben Kanal setzen (Auto-Löschen im Kanal = 1 Monat) und die Seite
      gegenprüfen.
- [ ] Optional (eigenes Ticket): Startzeit-Plausibilitätscheck per `getChat`
      hinter einem Feature-Flag, falls Betreiber die Konsistenzpflicht
      wiederholt verletzen.
