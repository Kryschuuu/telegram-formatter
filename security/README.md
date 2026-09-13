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
| S9 | Web-Versand autorisiert | `/api/send` ist ohne `TELEGRAM_FORMATTER_API_TOKEN` deaktiviert; BYOB verlangt Handle plus `session_secret` | `tests/test_app.py`, `tests/test_byob_web.py` |

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
- Web-Endpunkte nehmen nur `text`/`chat_id` entgegen; kein Pfad, keine
  URL, kein Template aus Nutkereingabe.

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
| K-2 · Open Relay (`/api/send`) | ✅ behoben | 2.1.0 + 2.4.0 + 2.5.0 | Chat-Pinning (2.1.0); Selbstbetrieb Fail-Closed (2.4.0); Shared-Versand ohne API-Token deaktiviert (2.5.0) |
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
