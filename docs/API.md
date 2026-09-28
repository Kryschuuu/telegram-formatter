# API-Referenz

Alle Endpunkte der App — für Skripte, Integrationen und den
[Docker-Betrieb](DOCKER.md). Basis-URL im LAN-Beispiel:
`https://192.168.0.10` (bis zum CA-Import mit `curl -k` aufrufen).

Konventionen:

* Request/Response-Format ist **JSON** (`Content-Type: application/json`).
* **Fehler sind immer JSON**: `{"error": "…", …}` — nie HTML, nie Tracebacks,
  nie Tokens/URLs/Inhalte in Meldungen.
* Request-Bodys sind auf **512 KiB** begrenzt (darüber `413`).
* POSTs mit fremdem `Origin`-Header werden mit `403` abgewiesen. Auch
  `Origin: null` (Sandboxed-iframe, `file://`, Redirect-Verläufe) zählt als
  fremd — seit v2.13.0; vorher passierte der Fall durch, weil `urlparse("null")`
  keinen `netloc` liefert.
* Ist `TELEGRAM_FORMATTER_API_TOKEN` gesetzt, verlangen alle POSTs den Header
  `X-Auth-Token` (`401` ohne) — **außer** `POST /api/send` bei freigegebenem
  Browser-Versand (der Browser erhält das Secret nie).

## Übersicht

| Methode & Pfad | Zweck | Auth |
|---|---|---|
| `GET /` | Editor-Seite (HTML) | – |
| `GET /healthz` | Liveness-Probe `{"status":"ok","version"}` | – |
| `POST /api/convert` | Markdown → Telegram-Payloads (sendet nichts) | ggf. `X-Auth-Token` |
| `POST /api/send` | Senden über den geteilten Bot (öffentlich!) | anonym + `confirm_public` **oder** `X-Auth-Token` |
| `POST /api/byob/session` | Eigene Bot-Session öffnen (201) | Token im Body + `consent` |
| `POST /api/byob/discover` | Chat-IDs des eigenen Bots finden | Token im Body |
| `POST /api/byob/send` | Senden über die eigene Session (privat) | `session_id` + `session_secret` |
| `POST /api/byob/status` | Session-Status (Countdown/Zähler) | `session_id` + `session_secret` |
| `POST /api/byob/close` | Session sofort beenden | `session_id` + `session_secret` |

Frequenzlimits (pro Minute und IP, sofern nicht anders genannt):

| Endpunkt | Standard | Variable |
|---|---|---|
| `/api/convert` | 60 | `TELEGRAM_FORMATTER_CONVERTS_PER_MINUTE` |
| `/api/send` (authentifiziert) | 6 | `TELEGRAM_FORMATTER_SENDS_PER_MINUTE` |
| `/api/send` (anonym) | 4 + 30 instanzweit | `…_SHARED_WEB_SENDS_PER_MINUTE[_TOTAL]` |
| `/api/byob/session` | 3 | `…_BYOB_SESSIONS_PER_MINUTE` |
| `/api/byob/discover` | 3 | `…_BYOB_DISCOVER_PER_MINUTE` |
| `/api/byob/send` | 6 | `…_BYOB_SENDS_PER_MINUTE` |

Bei `429` antwortet die API mit `{"error": "…", "retry_after": 60}` (anonyme
Sendungen) bzw. ohne `retry_after` (übrige) — bitte warten statt hämmern.

## GET /healthz

Liveness-Probe für Docker-`HEALTHCHECK`, Load-Balancer und Deploy-Checks.
Billig (kein Template), nie ratenlimitiert, ohne Konfigurationsdetails:

```bash
curl -k https://192.168.0.10/healthz
# {"status":"ok","version":"2.14.1"}
```

## POST /api/convert

Wandelt Markdown (+ LaTeX `$…$`/`$$…$$`, Tabellen) in sendefertige
Telegram-Nachrichten um — **rein lesend**, es wird nichts versendet.
`chat_id` ist optional (wird nur ins Payload übernommen; bei gepinntem
`TELEGRAM_CHAT_ID` muss ein mitgesendeter Wert exakt passen, sonst 400).

```bash
curl -k -X POST https://192.168.0.10/api/convert \
  -H 'Content-Type: application/json' \
  --data '{"text":"**fett** und $x^2$"}'
```

```json
{
  "count": 1,
  "messages": [
    {"kind": "rich", "payload": {"chat_id": "0", "rich_message": {"markdown": "**fett** und $x^2$"}}}
  ]
}
```

`kind` ist `rich` (→ `sendRichMessage`, LaTeX/Tabellen, ≤ 32768 Zeichen) oder
`regular` (→ `sendMessage` + `parse_mode="HTML"`, ≤ 4096 Zeichen). Lange
Eingaben kommen als **mehrere** Nachrichten zurück (Aufteilung an Absatz-,
Zeilen- und Wortgrenzen, Formatierungen bleiben je Chunk wohlgeformt).

### Feld `preview` (seit v2.14.0)

Dieselben Nachrichten, **fertig als Anzeige-HTML** für die Live-Vorschau.
Damit zeigt die Vorschau exakt das, was Telegram bekommt — statt es ein
zweites Mal zu rechnen (was bis v2.13.0 in `app.js` geschah und sofort
veraltete: keine Emoji-Überschriften, Pipe-Tabellen als Rohtext, LaTeX
ungesetzt, keine Nachrichtenteilung).

```json
{
  "count": 2,
  "messages": [ … ],
  "preview": {
    "path": "rich",
    "count": 2,
    "messages": [
      {
        "index": 1,
        "kind": "rich",
        "html": "<h1 class=\"tf-heading tf-heading--1\">Titel</h1><table class=\"tf-table\">…</table>",
        "utf16": 1820,
        "limit": 32768
      }
    ]
  }
}
```

| Feld | Bedeutung |
|---|---|
| `path` | `regular` \| `rich` — der Anzeigedialekt der **ersten** Nachricht. `build_messages` entscheidet je Nachricht, die Vorschau daher ebenfalls (`messages[].kind`) |
| `count` | Anzahl Nachrichten = Anzahl Sprechblasen in der Vorschau |
| `messages[].index` | 1-basiert, für die Nummerierung |
| `messages[].html` | Anzeige-HTML, **bereits sanitisiert** (Allowlist). Nur die von Telegram unterstützten Tags; LaTeX steht als `<span class="tf-math" data-tex="…">` und wird vom Client mit KaTeX gesetzt |
| `messages[].utf16` / `.limit` | Zeichen in UTF-16-Einheiten (so zählt Telegram) und das jeweilige Limit |

**Sicherheit.** `html` ist per Konstruktion sicher (jedes Textfragment wird vor
einem Tag escaped) und wird zusätzlich gegen eine Allowlist gefiltert:
`b i u s code pre a blockquote br tg-spoiler details tg-emoji` samt `href`,
`language`, `emoji-id`. Event-Handler, `style`, `class` und `javascript:`-URLs
fliegen raus. Wer `html` in eine eigene Oberfläche einsetzt, bekommt
denselben Schutz — der Client muss es nur noch einsetzen.

**Zwei Dialekte, zwei Renderer.** `regular` liefert bereits Telegram-HTML
(einschließlich der Emoji-Präfixe `🚀 📍 🔹 🔸`, die aus `utils` stammen) und
wird nur sanitisiert. `rich` ist GFM-Markdown und wird vom Server in das
Anzeige-Subset übersetzt — dort gibt es **keine** Emoji-Präfixe, weil
`sendRichMessage` sie nicht erzeugt.

Fehler: `400` (kein JSON-Objekt, `text` kein String, zu lang — max.
`TELEGRAM_FORMATTER_MAX_INPUT_CHARS`, ungültige `chat_id`, unvollständiges
Unicode-Zeichen, s. u.), `401` (s. o.), `429`, `413`.

**Unvollständiges Unicode (seit v2.13.0).** `{"text": "𝓀"}` ist syntaktisch
gültiges JSON — der `json`-Decoder erzeugt daraus einen *ungepaarten*
UTF-16-Surrogate, der in UTF-8 gar nicht darstellbar und also von keiner
Validierung zu beanstanden ist. Er scheitert erst beim UTF-16-Encoding
(`UnicodeEncodeError`). Statt eines undurchsichtigen `500` wird er hier
abgelehnt:

```json
{"error": "'text' enthält ungültige Zeichen (unvollständiges Unicode)."}
```

Status `400`. Gilt für **alle** Endpunkte mit `text`-Feld, auch für die
Sendewege — dort hätte derselbe String nicht nur einen 500, sondern einen
halb zugestellten Versand ohne `sent_before_error` verursacht.

## POST /api/send — geteilter Bot (öffentlich!)

Sendet über den zentral konfigurierten Bot in den **gepinnten**
`TELEGRAM_CHAT_ID` — auf der Demo-Instanz der **öffentliche** Kanal
[`t.me/mdtotxt_bot_web`](https://t.me/mdtotxt_bot_web). Jede Nachricht ist
für alle Kanalbesucher sichtbar (auch nachträglich) und wird nach
`TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` (30) automatisch gelöscht.

Zwei Zugangsarten — sonst `503` (fail-closed):

1. **Anonym aus dem Browser** (nur wenn `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1`
   und Zielchat gepinnt): Body braucht `"confirm_public": true` als
   Bestätigung der öffentlichen Sichtbarkeit; kürzere Längenkappe
   (`TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS`, 64000) und engere Limits.
2. **Authentifiziert** (serverseitig, z. B. aus Skripten): Header
   `X-Auth-Token: <operator-secret>`, volle Textlänge, Limit
   `TELEGRAM_FORMATTER_SENDS_PER_MINUTE`.

```bash
# Anonym (Browser-Fall):
curl -k -X POST https://192.168.0.10/api/send \
  -H 'Content-Type: application/json' \
  --data '{"text":"Hallo Kanal!","confirm_public":true}'

# Authentifiziert (Server-Fall):
curl -k -X POST https://192.168.0.10/api/send \
  -H 'Content-Type: application/json' -H 'X-Auth-Token: <operator-secret>' \
  --data '{"text":"Hallo Kanal!"}'
```

**Obergrenze pro Sendung (seit v2.13.0).** Jeder Telegram-Chunk ist ein
eigener API-Aufruf mit `timeout=15.0`, sequenziell im Request-Handler — also
in einem von nur 8 Gunicorn-Threads. Ohne Kappung bindet eine Anfrage bis zu
23 × 15 s = 345 s einen Thread, und zwei bis drei gleichzeitige anonyme
Anfragen erschöpfen den Pool (die Instanz antwortet dann auch auf `/` nicht
mehr).

`TELEGRAM_FORMATTER_MAX_CHUNKS_PER_REQUEST` (Default **25**) begrenzt die
Chunkzahl. Der Default entspricht genau der Chunkzahl der dokumentierten
Eingabelimits (17 für 64 000 Zeichen, 23 für 100 000) und verkleinert damit
**keine** dokumentierte Fähigkeit. Wird er gesenkt, muss
`TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS` mitgesenkt werden, sonst
lehnt die App gültige Eingaben ab:

```json
{
  "error": "Zu viele Teile (23) — bitte kürzen (max. 10 Teile pro Sendung)."
}
```

Status `400`. Es wird **kein** einziger API-Aufruf abgesetzt.

Erfolg:

```json
{
  "sent": 1,
  "results": [{"kind": "regular", "status": "ok"}],
  "via": {
    "bot": "@mdtotxt_bot",
    "chat_id": "-1001234567890",
    "chat_url": "https://t.me/mdtotxt_bot_web",
    "retention_days": 30,
    "public": true
  }
}
```

Teilfehler (Chunk 3 von 5 scheitert): `502` (bzw. `429` bei Telegram-Limit)
mit `{"error": "…", "sent_before_error": 2, "results": […], "note": "…",
"retry_after?": n}` — **nicht** blind komplett neu senden (Duplikate!).

Weitere Fehler: `400` (`confirm_public` fehlt, Eingabe zu lang, `chat_id`
abweichend vom Pin, Token nicht konfiguriert), `401`, `429`, `503`
(Shared-Versand deaktiviert), `413`.

## POST /api/byob/session — eigene Bot-Session öffnen

Öffnet eine **ephemere** Session mit dem eigenen Bot (BYOB): Token wird per
`getMe` verifiziert, lebt nur im RAM (TTL 30 min, Leerlauf 10 min) und wird
beim Ende verworfen — keine Datei, kein Log, kein Cookie. Antwortet mit
`201`. Ist `TELEGRAM_FORMATTER_BYOB_ENABLED=0`, antworten alle
`/api/byob/*`-Endpunkte mit `404`.

```bash
curl -k -X POST https://192.168.0.10/api/byob/session \
  -H 'Content-Type: application/json' \
  --data '{"token":"<dein-bot-token>","chat_id":"-1001234567890","consent":true}'
```

**Body-Format (seit v2.13.0).** JSON ist der Normalfall und hat Vorrang.
Zusätzlich wird `application/x-www-form-urlencoded` akzeptiert, damit das
HTML-Formular der Seite auch ohne JavaScript funktioniert:

```bash
# Ohne JavaScript (der Fall, für den es das gibt):
curl -k -X POST https://192.168.0.10/api/byob/session \
  -d 'token=<dein-bot-token>' -d 'chat_id=-1001234567890' -d 'consent=on'
```

**Warum es den Formular-Weg braucht:** das `<form>` auf der Seite trägt
bewusst `method="post"`. Ohne diese Angabe hätte ein Browser per HTML-Default
GET verwendet und den Bot-Token als Query-String an die URL gehängt — in die
Adressleiste, die Browser-Historie und das Caddy-Access-Log. Bei einem
Formular-Body kommt `consent` als **String** (`"on"` bei angehaktem
Kontrollkästchen), im JSON als Boolean `true`; `"false"`, `"0"` und `""`
sind in beiden Fällen **keine** Zustimmung.

```json
{
  "session_id": "…",
  "session_secret": "…",
  "bot": {"id": 123456789, "username": "mein_bot", "display_name": "Mein Bot", "handle": "@mein_bot"},
  "chat_id": "-1001234567890",
  "limits": {"ttl_seconds": 1800, "idle_timeout_seconds": 600,
             "max_messages_per_minute": 20, "max_input_chars": 100000}
}
```

`session_secret` ist der zweite Zugangsnachweis (Proof-of-Possession) für
`send`/`status`/`close` — wie die `session_id` nur flüchtig halten (RAM),
niemals loggen oder persistieren. Fehler: `400` (`consent` fehlt, Token- oder
Chat-ID-Format ungültig, Token von Telegram abgelehnt), `429` (Frequenz- oder
Session-Kappen: max. 100 instanzweit, 3 pro IP), `401`, `404` (BYOB aus).

## POST /api/byob/discover — Chat-IDs finden

Blickt in die `getUpdates`-Warteschlange des eigenen Bots (verbraucht nichts,
bestätigt nichts) und listet bekannte Chats — Antwort enthält nur Metadaten,
niemals Nachrichteninhalte:

```bash
curl -k -X POST https://192.168.0.10/api/byob/discover \
  -H 'Content-Type: application/json' \
  --data '{"token":"<dein-bot-token>"}'
# {"chats": [{"id": -1001234567890, "type": "supergroup", "name": "…"}]}
```

Fehler: `400` (Token-Format), `502` (`Chat-Erkennung fehlgeschlagen: …` —
z. B. Webhook-Konflikt 409 oder ungültiges Token), `429`, `401`, `404`.

## POST /api/byob/send — über die eigene Session senden (privat)

```bash
curl -k -X POST https://192.168.0.10/api/byob/send \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"…","session_secret":"…","text":"**privat** und $x^2$"}'
# {"sent": 1, "session": {"ttl_remaining_seconds": 1790, "idle_remaining_seconds": 595}}
```

Fehler: `401` (Zugangsdaten ungültig), `410` (Session abgelaufen/unbekannt —
neu öffnen), `400` (Text-/Session-Fehler), `429` (Limit), `502`/`429`
(Telegram-Fehler mit `sent_before_error`, siehe `/api/send`), `404` (BYOB aus).

## POST /api/byob/status und POST /api/byob/close

```bash
curl -k -X POST https://192.168.0.10/api/byob/status \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"…","session_secret":"…"}'
# aktiv:   {"active":true,"bot":{…},"chat_id":"…",
#           "ttl_remaining_seconds":1790,"idle_remaining_seconds":595,
#           "messages_sent":1,"chunks_sent":1}
# beendet: {"active":false}

curl -k -X POST https://192.168.0.10/api/byob/close \
  -H 'Content-Type: application/json' \
  --data '{"session_id":"…","session_secret":"…"}'
# {"closed":true}   — Token-Referenz fällt sofort, kein Warten auf TTL nötig.
```

Fehler: `401` (Zugangsdaten ungültig), `400` (Handle-Format), `404` (BYOB aus).
