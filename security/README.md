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
| S8 | Dependency-Register sauber | pip-audit (Laufzeit + Dev) pro PR **und** wöchentlich via cron | CI-Job `code-quality` |

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
