# Audit

Dieser Ordner bündelt die **Audit-Sicht** auf das Projekt: welche Prüfungen
wann von wem laufen, wo ihre Ergebnisse liegen und wie sie reproduzierbar
sind. Er ist der Startpunkt für interne und externe Audits.

## Prüf-Matrix

| Prüfung | Werkzeug | Kommando (lokal) | Automatisierung (CI) | Ergebnis liegt in |
|---|---|---|---|---|
| Stil & offensichtliche Fehler | Ruff | `ruff check .` | Job `code-quality` | CI-Log |
| Sicherheitsstatik (Python) | Bandit | `bandit -c pyproject.toml -r telegram_formatter examples/own_bot -ll` | Job `code-quality` | CI-Log / SARIF |
| Secret-Scan | gitleaks | `gitleaks detect --config .gitleaks.toml` | Job `secrets` | PR-Kommentar / SARIF |
| Abhängigkeiten (CVE) | pip-audit | `pip-audit -r requirements.txt -r requirements-dev.txt` | Job `code-quality` (läuft auch wöchentlich via cron) | CI-Log |
| Nutzer-Bot-Statik (BK001–BK012) | `botctl` | `python -m telegram_formatter.botctl review <datei> --bot-id <id> --check` | Job `bot-gate` | `audit/reviews.json` |
| Freigabe-Gate vor Deployment | `botctl` | `python -m telegram_formatter.botctl verify <datei> --bot-id <id>` | Job `bot-gate` | `audit/reviews.json` |
| Unit-Tests | pytest | `pytest -q` | Job `bot-gate` | CI-Log |
| Review-Berichte (menschlich) | Markdown | siehe [`peer-review/`](../peer-review/README.md) | — | `peer-review/` |

## Audit-Trail-Datei

Der maschinenlesbare Review-Vorgang (Tickets, Freigaben, Code-Prüfsummen)
liegt standardmäßig in **`audit/reviews.json`** und wird von
`python -m telegram_formatter.botctl` gepflegt (`review` / `approve` /
`verify`, Option `--ledger`). Er enthält **ausschließlich Metadaten** —
Ticket-IDs, Bot-IDs, SHA-256-Prüfsummen, Regel-IDs, Entscheidungen. Keine
Nachrichteninhalte, keine Tokens (Details:
[`security/README.md`](../security/README.md)).

```bash
# Stand einspielen und prüfen (z. B. im Audit-Interview):
python - <<'PY'
from telegram_formatter.botkit.review import ReviewLedger
trail = ReviewLedger.load("audit/reviews.json").audit_trail()
print(len(trail), "Ticket(s)")
PY
```

## Unveränderlichkeit

`audit/reviews.json` ist Append-orientiert (das Ledger führt alle
Entscheidungen pro Bot+Prüfsumme). Änderungen an bereits freigegebenen
Einträgen sind ein Alarmzeichen: Die Freigabe ist an die SHA-256 des Codes
gebunden — jeder Code-Wechsel erzwingt ein neues Ticket, niemals eine
nachträgliche Editierung alter Entscheidungen.
