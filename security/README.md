# Security

Sicherheitskonzept, Bedrohungsmodell und die harten Regeln, die dieses
Projekt **strukturell** durchsetzt (nicht nur dokumentarisch).

> Vertrauensverletzung gefunden? → [SECURITY.md](../.github/SECURITY.md)
> (Offenlegungsprozess, von GitHub als Security-Policy erkannt).

## Schutzziele & Durchsetzung

| # | Schutzziel | Durchsetzung im Code | Testbar über |
|---|---|---|---|
| S1 | Bot-Tokens werden **nie** persistiert | `BotToken`-Umschlag; Vaults sind RAM-only (`InMemoryTokenVault` TTL, `PassthroughTokenVault` verweigert `store()` bewusst) | `pytest tests/test_tokens.py` |
| S2 | Keine Nachrichteninhalte in Logs | `RedactingFilter` auf Root-/Handler-/Fremd-Loggern; `audit()` redaktioniert sensible Felder zu `len=… fp=…` | `tests/test_privacy.py`, `tests/test_session.py` |
| S3 | Secrets im Quelltext | gitleaks im CI; BK006 (AST) für Nutzer-Bots; `.gitleaks.toml` eng begrenzte Allowlist nur für Negativbeispiele | CI-Job `secrets`, `botctl review` |
| S4 | Nutzer-Bots können keine Daten abfließen/zerschreiben | AST-Regeln BK001–BK012 (`telegram_formatter/botkit/review.py`): verbotene Imports, Fremd-Hosts, Schreibzugriffe, `eval`/`exec`, Shell | `tests/test_review.py` (positiv + `tests/fixtures/insecure_bot.py`) |
| S5 | Freigaben sind an Code gebunden | `ReviewGate` prüft SHA-256 der reviewten Datei bei **jeder** Session-Öffnung; eine Zeile Änderung ⇒ kein Access | `tests/test_session.py` |
| S6 | Vier-Augen-Prinzip | `ReviewTicket.is_approved`: ≥2 unabhängige Handles, ≥1 Maintainer; `CODEOWNERS` + Branch-Protection im Git-Flow | `tests/test_review.py`, GitHub-Settings |
| S7 | Session-Ende hinterlässt keinen Zustand | `BotSession.close()` verwirft Token-Referenz; `deleteWebhook(drop_pending_updates=True)` als Pflicht im Referenz-Bot | `tests/test_session.py` |
| S8 | Dependency-Register sauber | pip-audit (Laufzeit + Dev) pro PR **und** wöchentlich via cron; CI-Actions und Security-Tools sind commit-/versionsgepinnt | CI-Job `code-quality` |
| S9 | Web-Versand nur über einen expliziten Zugang | `/api/send` braucht entweder den Operator-Token (`X-Auth-Token`) **oder** den freigeschalteten Browser-Versand (`TELEGRAM_FORMATTER_SHARED_WEB_SEND=1` + gepinnter Zielchat + `confirm_public`); ohne beides 503. BYOB verlangt Handle plus `session_secret` | `tests/test_app.py` (Zugangs-Matrix), `tests/test_byob_web.py` |
| S10 | Kein Klickziel aus Betreiber-Konfiguration | `normalize_public_chat_url` akzeptiert ausschließlich `https://t.me/<handle>`, `t.me/<handle>`, `@<handle>` (Handle 5–32 Zeichen, keine Ziffer am Anfang); fremde Domains, andere Schemata (`javascript:`, `data:`), private Einladelinks (`t.me/+…`, `t.me/c/…`), Deep-Links und Query/Fragment ergeben `None` ⇒ die UI rendert keinen Link. Jeder Kanal-Link trägt `rel="noopener noreferrer"` | `tests/test_shared_channel.py` |
| S11 | Keine erfundene Zusage an Besuchende | Ohne gepinnten `TELEGRAM_CHAT_ID` wird kein Kanal behauptet (`_shared_channel` ⇒ `url=None`); `SHARED_RETENTION_DAYS=0` ⇒ keine Lösch-Aussage (FAQ sagt dann „dauerhaft öffentlich"); Kanal-Link ohne Pinning wird beim Start geloggt (`app.shared_channel_unpinned`) | `tests/test_shared_channel.py` |
| S12 | Eine Wahrheit für UI und API | Template-Makros, `<body>`-Attribute und das `via`-Feld von `/api/send` lesen alle `_shared_channel()` — `app.js` erfindet weder Kanalnamen noch Fristen | `tests/test_shared_channel.py`, `tests/frontend/jsdom_spec.cjs` |

## Token-Bedarfsminimierung

- `BotToken.reveal()` ist der einzige dokumentierte Ausgang; der Zähler
  `reveal_count` macht übermäßigen Zugriff bei Reviews sichtbar.
- `repr`/`str`/Exceptions geben nur `bot_id` + prozesslokalen HMAC-Fingerprint
  heraus; zeitkonstanter `==`-Vergleich gegen Timing-Seitenkanäle.
- `scrub_environment()` entfernt die Token-Env-Variable nach dem Einlesen —
  keine Vererbung an Kindprozesse, kein `/proc/<pid>/environ`-Eintrag.
- Einmal-Fingerprint (HMAC mit Prozesszufall): Logs sind innerhalb eines
  Prozesses korrelierbar, über Deployments hinweg wertlos.

## Injection-Hygiene

- `chat_id` wird gegen `^-?\d{1,32}$` validiert, bevor es in einen
  API-Payload gelangt (`telegram_formatter/botkit/registry.py`).
- HTML-Escaping vor Markdown-Konvertierung (`_escape_html`), `&`, `<`, `>`
  im Regular-Pfad; Formeln werden 1:1 übernommen, aber geklammert-balanciert
  geprüft (`validate_latex_braces`).
- Redirect-Unwrap (v2.7.0): extrahierte Ziel-URLs gelangen nur in Link-Ziele,
  wenn sie selbst absolute `http(s)`-URLs sind (`_looks_like_http_url`) —
  `javascript:`/`data:` können über Redirect-Parameter nicht eingeschleust
  werden; Host-Regeln sind `fullmatch`-verankert (kein Suffix-Phishing à la
  `evilgoogle.com`). Negativtests: `tests/test_utils.py::TestUnwrapRedirectUrl`.
- Web-Endpunkte nehmen nur `text`/`chat_id` entgegen; kein Pfad, keine
  URL, kein Template aus Nutkereingabe.

## Geteilter Bot im Browser (seit 2.6.0)

Der Browser-Versand ist ein **bewusster Opt-in des Betreibers**
(`TELEGRAM_FORMATTER_SHARED_WEB_SEND`, Standard `1`) und kein Relais:

* Zielchat ist immer der gepinnte `TELEGRAM_CHAT_ID`; ein `chat_id`-Override
  im Request wird mit 400 abgewiesen (K-2 bleibt damit geschlossen).
* Anonyme Aufrufe brauchen `"confirm_public": true` (öffentliche Sichtbarkeit
  bestätigt), dürfen höchstens `SHARED_WEB_MAX_INPUT_CHARS` Zeichen senden und
  werden pro IP (Standard 4/min) **und** instanzweit (Standard 30/min)
  gedeckelt — der Bot soll nicht gegen die Telegram-Limits gelaufen werden.
* Kein Pfad ohne Origin-Bindung: fremde `Origin`-Header bleiben 403; der
  Operator-Token befreit `/api/send` aus der Pflicht, alle übrigen POSTs
  bleiben pflichtig.
* Restrisiko (vom Betreiber getragen): fremde Besucher schreiben in den
  eigenen, öffentlichen Kanal. Wer das nicht will, setzt den Schalter auf `0`.

## Offenlegung des Ziel-Kanals (seit 2.9.0)

Die Einwilligung in den öffentlichen Versand (`confirm_public`) ist nur dann
informiert, wenn Besuchende das Ziel kennen. Deshalb benennt die UI den
konkreten Kanal (`TELEGRAM_FORMATTER_SHARED_CHAT_URL`, Demo:
`https://t.me/mdtotxt_bot_web`) samt Aufbewahrungsdauer
(`TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS`, Demo: 30 Tage) an sieben Stellen
und im `via`-Feld von `POST /api/send`. Die strukturellen Regeln dafür sind
die Schutzziele **S10–S12** in der Tabelle oben; die Stellen im Einzelnen:
Top-Warnung, Kanal-Banner im Hero, Hinweis am Senden-Button,
Bestätigungsdialog (Faktenzeile „Kanal (öffentlich)" mit Link + Löschfrist),
Privatsphäre-Sektion, FAQ (zwei Einträge) und Footer/Howto.

Betreiberpflicht (nicht maschinell prüfbar): `TELEGRAM_CHAT_ID`,
`…_SHARED_CHAT_URL` und `…_SHARED_RETENTION_DAYS` müssen denselben Chat
beschreiben — siehe `docs/DEPLOYMENT.md`, Schritt 4b.

## Bekannte Grenzen (bewusst hingenommen)

- Python-Strings sind unveränderlich — „Secure Wipe“ von Token-Resten aus dem
  RAM ist auf CPython nicht garantiert; Designziel ist daher *keine
  Persistenz auf dauerhaften Medien*, nicht „keine Spur im RAM“.
- Der HTTP-Transport zur Telegram-API ist TLS; Zertifikatsprüfung obliegt
  `requests`/System-CA (kein Patching, kein `verify=False` im gesamten Repo —
  gitleaks/Bandit alarmieren bei Verstößen).
- `--local-trust` in `botctl send` überspringt das Review **nur** für privat
  betriebene Bots des Nutzenden selbst; im gehosteten Modus ist es die
  fail-closed-Standardabweichung und im Audit-Trail sichtbar.

## Audit-Findings-Status (SECURITY_AUDIT.md v2.0.0)

Zuordnung der Befunde aus dem Audit v2.0.0 (Commit `f955550`) zu den
Versionen, in denen sie behoben wurden. Befund-IDs wie im Bericht
(`K-*` Kritisch, `H-*` Hoch, `M-*` Mittel, `N-*` Niedrig, `B-*` Bugs).

| Befund | Status | Behoben in | Anmerkung |
|---|---|---|---|
| K-1 · Token-Leak im Fehlerpfad | ✅ behoben | 2.1.0 | Fehlermeldungen ohne URL/Token |
| K-2 · Open Relay (`/api/send`) | ✅ behoben | 2.1.0 + 2.4.0 + 2.5.0 | Chat-Pinning (2.1.0); Selbstbetrieb Fail-Closed (2.4.0); Shared-Versand ohne API-Token deaktiviert (2.5.0); seit 2.6.0 optionaler Browser-Versand — **nur** bei gepinntem Chat, mit Consent, Längen- und Frequenzgrenzen; `0` schaltet wieder komplett ab |
| H-1 · Review-Gate-Umgehungen | ✅ behoben | 2.1.0 + 2.4.0 | Alias/Konstanten-Faltung (2.1.0); BK010=Blocker, `os.open`/`os.write`, `getattr` (2.4.0, R-2) |
| H-2 · DoS ohne Limits | ✅ behoben | 2.1.0 | Body-/Input-/Rate-Limits |
| H-3 · `response.text`-Leak | ✅ behoben | 2.1.0 | gekürzte Description |
| H-4 · Traceback-Leak | ✅ behoben | 2.1.0 | `exc_info`-Redaction |
| H-5 · CDN/CSP | ✅ behoben | 2.1.0/2.2.0 | Self-Hosting, CSP `'self'` |
| M-1 · `"`-Attribut-Injection | ✅ behoben | 2.1.0 | `&quot;`, Fence-Allowlist |
| M-2 · `--token` in ps/Historie | ✅ behoben | 2.4.0 | Parameter entfernt (R-3) |
| M-3 · `api_base` http:// | ✅ behoben | 2.1.0 | HTTPS-Pflicht |
| M-4 · Audit-Trail-Trust | 🟡 teilweise | 2.1.0/2.2.0 | CI-`verify` aktiv, `flock`; Rollen-Selbstattestation + 1 CODEOWNER offen (Governance) |
| M-5 · gunicorn veraltet | ✅ behoben | 2.1.0 | 26.2.0; Hash-Lockfile offen (Roadmap) |
| M-6 · CSRF/Origin | ✅ behoben | 2.1.0/2.2.0/2.5.0 | Origin-Check, Body-Handle; BYOB-Proof-of-Possession mit `session_secret` |
| M-7 · Thread-Safety | ✅ behoben | 2.1.0 | Locks |
| N-1 · NUL-Kollision | ✅ behoben | 2.1.0 + 2.4.0 | Server (2.1.0), Client-Vorschau (2.4.0, R-4) |
| N-2 · Gitleaks-Allowlist | ✅ behoben | 2.1.0 + 2.4.0 | `matchAll` (2.1.0), Exakt-Literal (2.4.0, R-5) |
| N-3 · Dev-Server-Debug | ✅ behoben | 2.1.0 | `debug=False` |
| N-4 · Registry-Wanduhr | 🟡 akzeptiert | — | bewusst dokumentiert |
| N-5 · Doku-Drift (Vault) | ✅ behoben | 2.2.0 | Vault als Baustein markiert |
| B-1 · Chunking zerreißt Tags | ✅ behoben | 2.1.0 | Tag-Balance + atomare Bereiche |
| B-2 · `--local-trust` defekt | ✅ behoben | 2.1.0 | |
| B-3 · Reject invalidiert nicht | ✅ behoben | 2.1.0 | |
| B-4 · Preis-`$` als LaTeX | ✅ behoben | 2.1.0 | GFM-Randregeln |
| B-5 · `text:int` → 500 | ✅ behoben | 2.1.0 | 400 |
| B-6 · Teilversand | ✅ behoben | 2.1.0 | `sent_before_error` |
| B-7 · `retry_after`/`ok` | ✅ behoben | 2.1.0 | |
| B-8/B-9/B-10 · botctl/Lost Update | ✅ behoben | 2.1.0 | `flock` |
| B-11 · Test-Isolation | ✅ behoben | 2.1.0 | Fixture-Restore |
| B-12 · Listen-Einrückung | ✅ behoben | 2.1.0 | |
| B-13 · `repr` geschlossener Sessions | ✅ behoben | 2.1.0 | kosmetisch |
| B-14 · `has_table`-Toleranz | 🟡 akzeptiert | — | dokumentiert |

> Offene Punkte sind **Governance/Deployment-Roadmap**, keine offenen Findings
> aus diesem Review: zweiter CODEOWNER-Maintainer (M-4), Hash-Lockfile für
> PyPI (M-5) und ein verteilter Rate-Limit-Store für Multi-Instance-Betrieb.
> Der direkte Betrieb vertraut standardmäßig keinen Forwarding-Headern; eine
> Proxy-Topologie muss `TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS` explizit setzen.
