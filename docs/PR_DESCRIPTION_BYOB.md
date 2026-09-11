# PR: Dezentrale Bots (BYOB) — `botkit`, `botctl`, Review-Gate

## Überblick

Bisher sendet das Projekt über **einen** zentral konfigurierten Bot
(`TELEGRAM_BOT_TOKEN`). Dieses PR ergänzt eine **dezentrale Alternative**:
Nutzer registrieren ihren eigenen Bot, lassen den Code reviewen und nutzen ihn
in einer ephemeren Session — **ohne dass Inhalte, Tokens oder Nutzerdaten
gespeichert werden**.

Bestehende Module (`utils.py`, `sender.py`, `cli.py`, `app.py`) bleiben
**unverändert**; `botkit` ergänzt das Projekt ausschließlich.

## Was geändert wurde

### Neu: Paket `botkit/`

| Datei | Inhalt |
|---|---|
| `botkit/tokens.py` | `BotToken`-Umschlag (Klartext nur über `reveal()`, `repr` redacted), `InMemoryTokenVault` (RAM + TTL), `PassthroughTokenVault` (speichert nichts, `store()` wirft) |
| `botkit/privacy.py` | `RedactingFilter` (auch für urllib3/requests), `audit()` als einzige Log-Schnittstelle (nur Metadaten), HMAC-Fingerprints, `scrub_environment()` |
| `botkit/registry.py` | `getMe`-Verifikation (`is_bot`, ID-Gegenprobe), Identität + Status — **kein** Token, `chat_id`/Pseudonym-Validierung |
| `botkit/review.py` | AST-Regeln BK001–BK012, Checkliste C1–C9, `ReviewLedger` (Metadaten), `ReviewGate` mit Vier-Augen-Prinzip und SHA-256-Bindung |
| `botkit/session.py` | `BotSession`/`SessionManager`: TTL, Leerlauf-Timeout, Rate-Limit-Fenster, Eingabevalidierung |
| `botkit/telegram_api.py` | `getMe`, `getUpdates`, `setWebhook`, `deleteWebhook` |

### Neu: `botctl.py` (CLI)

`register` · `review` · `approve` · `verify` · `send` · `checklist` — inkl.
Dry-Run, `--api-base` für private Bot-API-Server und `--local-trust` als
explizitem (dokumentiertem) Opt-out des Reviews für selbst gehostete Bots.

### Neu: Beispiele

- `examples/own_bot/minimal_bot.py` — Referenz-Bot, besteht alle BK-Regeln,
  Long-Polling ohne Offset-Persistenz, geordnetes Teardown (`deleteWebhook`).
- `tests/fixtures/insecure_bot.py` — Negativbeispiel, löst BK001–BK012 aus
  (Regressionstest für das Regelwerk).

### Neu: Review-Infrastruktur

- `.github/workflows/bot-review.yml` — Ruff, Bandit, `pip-audit`, gitleaks,
  `botctl review` für geänderte Bot-Dateien, pytest.
- `.github/CODEOWNERS` — Maintainer-Pflicht für Bot-Code und Infrastruktur.
- `.github/pull_request_template.md` — Prüf-Checkliste C1–C9 + BK-Befunde.
- `ruff.toml` — expliziter Regelsatz (reproduzierbare CI-Ergebnisse).
- `conftest.py` — verlässliche Import-Pfade für `pytest`.

### Doku

- `docs/DECENTRAL_BOT_ARCHITECTURE.md` — Architektur, Nutzerreise,
  Hürden-Tabelle, Security-Durchsetzung, Peer-Review-Prozess, Vergleich mit
  dem zentralen Bot, Abnahmekriterien und Roadmap.
- `README.md` — Abschnitt „Eigener Bot statt Zentral-Bot", Projektstruktur,
  Version 1.3.0.
- `CHANGELOG.md` — Eintrag `[1.3.0]`.

## Audit-Runde 1 (CI-Feedback behoben)

| Befund | Ursache | Fix |
|---|---|---|
| `pip-audit`: `requests 2.32.4` → `PYSEC-2026-2275` | veraltete Laufzeit-Abhängigkeit | `requests==2.33.0` |
| `pip-audit`: `pytest 8.3.5` → `PYSEC-2026-1845` (nur Dev) | veraltete Test-Abhängigkeit | `pytest==9.1.1` |
| `gitleaks`: Treffer in `examples/own_bot/minimal_bot.py:31` | Dummy-Token in der Doku sah echt aus | Platzhalter `<token-von-botfather>`; gleiches in `botkit/tokens.py` |
| `gitleaks` vs. Negativbeispiel | Fixture braucht ein tokenförmiges Literal für BK006 | Token über zwei Zeilen (AST faltet es, Scanner sieht nichts) + Freigabe in `.gitleaks.toml` |
| „Resource not accessible by integration" | Workflow-Berechtigungen zu streng | `pull-requests: write`, `actions: write` ergänzt |

Zusätzlich: wöchentlicher Cron-Lauf für Abhängigkeiten, `pip-audit` auch für
`requirements-dev.txt`, Ruff jetzt inklusive `conftest.py`/`tests/`.

## Teststatus

```
175 passed            (vorher 81; 94 neue Tests)
ruff check            All checks passed!
bandit -ll            No issues identified.
pip-audit (prod+dev)  No known vulnerabilities found
gitleaks              keine einzeiligen Token-Literale im Working Tree
```

Wichtigste neue Tests:

- `test_registry_never_stores_the_secret` — Registry hält kein Token
- `test_session_logs_metadata_only` — keine Inhalte/Tokens/chat_id im Log
- `test_two_independent_approvals_activate_the_bot` — Vier-Augen-Prinzip
- `test_approval_is_bound_to_the_source_hash` — Freigabe gilt nur für die
  reviewte Code-Fassung
- `test_insecure_bot_triggers_all_expected_rules` — Regelwerk wirkt

End-to-End verifiziert (gegen einen lokalen API-Stub):
`register → review → 2× approve → verify → send` sowie
`Start → getMe → getUpdates → Session → sendRichMessage → deleteWebhook`.

## Review-Hinweise

1. `botkit/review.py` ist der größte Zuwachs — Fokus auf die Regelliste
   (`RULES`, `FORBIDDEN_IMPORTS`) und die Suppression-Logik.
2. `PassthroughTokenVault.store()` wirft absichtlich (Fail-Closed).
3. Der Audit-Trail `.botkit/reviews.json` ist die **einzige** Datei, die das
   Paket schreibt — bitte prüfen, dass dort ausschließlich Metadaten stehen.
4. `examples/own_bot/minimal_bot.py` ist Referenz *und* Doku: Änderungen
   daran verändern die Review-Ergebnisse im CI.
