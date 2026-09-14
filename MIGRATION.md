# Migration — Repository-Reorganisation (1.3.0 → 2.0.0)

Dieses Dokument protokolliert die umfassende Struktur- und Bereinigungs-
aktion von 2026-09-11. Es ist die Nachschlage-Tabelle „alt → neu“ für alle,
die mit der vorherigen Struktur arbeiten.

## 1. Warum Version 2.0.0?

Der **Importpfad** der Laufzeitmodule hat sich geändert (z. B.
`from utils import …` → `from telegram_formatter.utils import …`) und die
CLI-Aufrufe lauten jetzt `python -m telegram_formatter.cli` bzw.
`python -m telegram_formatter.botctl`. Nach Semantic Versioning ist das ein
BREAKING CHANGE für Bibliotheks-Nutzer → Major. **Funktionale Änderungen an
Konvertierung, Versand oder Review-Logik wurden nicht vorgenommen** —
Verhalten, Payloads und API der Telegram-Schnittstelle sind unverändert.

## 2. Dateimappe (alt → neu)

### Code

| Alt | Neu | Anmerkung |
|---|---|---|
| `utils.py` | `telegram_formatter/utils.py` | Modulname bewusst unverändert (öffentliche Referenzen in Doku/CI) |
| `sender.py` | `telegram_formatter/sender.py` | |
| `cli.py` | `telegram_formatter/cli.py` | Einstieg: `python -m telegram_formatter.cli` |
| `app.py` | `telegram_formatter/app.py` | WSGI: `gunicorn "telegram_formatter.app:app"`; Root-`app.py` bleibt als veralteter Forwarding-Shim (entfällt 3.0.0) |
| `botctl.py` | `telegram_formatter/botctl.py` | Einstieg: `python -m telegram_formatter.botctl` |
| `botkit/` | `telegram_formatter/botkit/` | Paketinhalt unverändert |
| `templates/index.html` | `telegram_formatter/templates/index.html` | Flask löst Templates jetzt im Paket auf |
| — | `telegram_formatter/__init__.py` | Neu: Fassade (`__version__`, `build_messages`, `send_message`) |

### Dokumentation & Audits

| Alt | Neu | Anmerkung |
|---|---|---|
| `docs/BLUEPRINT.md` | `docs/ARCHITECTURE.md` | Geläufiger Standardname; Inhalt auf neue Struktur aktualisiert |
| `docs/CODE_REVIEW.md` | `peer-review/CODE_REVIEW.md` | Review-Ergebnisse gehören ins Review-Verzeichnis |
| `docs/PROMPT.md` | `peer-review/archive/PROMPT-v1.1.0.md` | Abgeschlossenes Arbeits-Artefakt (v1.1.0), archiviert |
| `docs/PR_DESCRIPTION.md` | `peer-review/archive/PR-DESCRIPTION-v1.1.0.md` | Historische PR-Beschreibung, archiviert |
| `docs/PR_DESCRIPTION_BYOB.md` | `peer-review/archive/PR-DESCRIPTION-v1.3.0-BYOB.md` | Historische PR-Beschreibung, archiviert |
| `docs/DECENTRAL_BOT_ARCHITECTURE.md` | *(unverändert)* | Pfade/Kommandos im Dokument aktualisiert |
| `docs/DEPLOYMENT.md` | *(unverändert)* | Start-Kommando aktualisiert |
| — | `peer-review/README.md`, `peer-review/TEMPLATE.md` | Neu: Review-Konvention + Berichtsvorlage |
| — | `audit/README.md` | Neu: Audit-Sicht (Prüf-Matrix, Trail-Ablage) |
| — | `security/README.md` | Neu: Schutzziel-Matrix, Injection-Hygiene, bekannte Grenzen |
| — | `.github/SECURITY.md` | Neu: Offenlegungsprozess (von GitHub erkannt) |
| — | `bots/README.md` | Neu: dokumentierter Ort für Nutzer-Bots (CI-Pfade existierten schon) |
| — | `LICENSE` | Neu; Inhalt gemäß main-Umstellung auf **GNU GPL v3** (der alte MIT-Badge behauptete eine Lizenz, ohne dass je eine Datei existierte; Badge/pyproject angepasst) |
| — | `CONTRIBUTING.md` | Neu: Setup, Schichtregeln, PR-Prozess |
| — | `MIGRATION.md` | Neu: dieses Dokument |

### Konfiguration

| Alt | Neu | Anmerkung |
|---|---|---|
| `ruff.toml` | `pyproject.toml` (`[tool.ruff]`) | Konsolidiert — eine Konfigurationsdatei weniger |
| `conftest.py` | `pyproject.toml` (`[tool.pytest.ini_options] pythonpath`) | pytest-Boardmittel ersetzt den Sys.path-Handstand |
| — | `pyproject.toml` (`[project]`, `[tool.bandit]`) | PEP-621-Metadaten, `pip install -e .`, Konsolen-Skripte |
| `requirements*.txt` | *(bleiben)* | Render/CI-Build-Kompatibilität; Deps werden dynamisch referenziert, nicht dupliziert |
| `.gitleaks.toml` | *(bleibt im Root)* | Tool-Standardabruf + CI-Env-Var; Erklärung in `security/README.md` |

## 3. Import- & Aufruf-Migration

```python
# alt (≤ 1.3.0)
from utils import build_messages
from sender import send_message, SendError
from botkit.tokens import BotToken
from botkit.session import BotSession

# neu (≥ 2.0.0)
from telegram_formatter import build_messages, send_message, SendError
from telegram_formatter.utils import build_messages   # oder gezielt
from telegram_formatter.sender import send_message
from telegram_formatter.botkit.tokens import BotToken
from telegram_formatter.botkit.session import BotSession
```

| alt | neu |
|---|---|
| `python cli.py datei.txt` | `python -m telegram_formatter.cli datei.txt` |
| `python botctl.py <befehl>` | `python -m telegram_formatter.botctl <befehl>` (nach `pip install -e .` auch `botctl <befehl>`) |
| `flask --app app run` | `flask --app telegram_formatter.app run` |
| `gunicorn app:app --bind 0.0.0.0:$PORT` | `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT` |
| `python examples/own_bot/minimal_bot.py` | *(unverändert)* — Imports zeigen jetzt auf das Paket; ausführbar aus Repo-Wurzel |

### Deployment-Fallback (Start-Kommando)

Der rechte Wert oben ist der **kanonische** Einstieg. Hosting-Plattformen
übernehmen ihr Start-Kommando jedoch aus dem Dashboard (nicht aus dem Git),
sodass nach dem Merge von 2.0.0 der alte Befehl `gunicorn app:app` gegen ein
nicht mehr existierendes Root-Modul lief — Build erfolgreich, Start mit
`ModuleNotFoundError: No module named 'app'`. Deshalb gilt:

| Ebene | Zustand ab 2.0.x |
|---|---|
| `app.py` (Wurzel) | **Veralteter Kompatibilitäts-Shim**: einzeilige, rein weiterleitende Adresse für den alten Befehl; **Entfernung mit 3.0.0**. Keine Logik — erzwungen durch `tests/test_app.py` (identisches WSGI-Objekt + AST-Prüfung). |
| `render.yaml` (Wurzel) | Render-Blueprint mit dem kanonischen Start-Kommando, `healthCheckPath: /`, `PYTHON_VERSION` und den Secrets `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` (`sync: false` → bleiben im Dashboard-Store). Gilt automatisch nur für **aus dem Blueprint erzeugte** Dienste; bestehende Services einmalig umstellen (docs/DEPLOYMENT.md, Schritt 3a). |

Empfehlung: Start-Kommando im Dashboard auf den kanonischen Befehl setzen und
den Shim wegwerfen, sobald 3.0.0 ansteht — er ist eine Überbrückung, keine
zweite Einstiegsschicht.

## 4. Verhaltensneutrale Bereinigungen (toter Code)

| Fundstelle | Maßnahme |
|---|---|
| `utils.py`: `RICH_MESSAGE_MAX_BLOCKS = 500` | **Entfernt** — definiert, aber im gesamten Repo nie verwendet (weder Code noch Tests) |
| `botkit/review.py`: `INFO = "info"` | **Entfernt** — RULES nutzen nur `BLOCKER`/`WARNING`; keine Referenz im Repo |
| `botkit/__init__.py`: `__version__ = "1.3.0"` | **Entfernt** — duplizierte die Projektversion (driftete bereits); zentrale Quelle ist jetzt `telegram_formatter.__version__` |
| `conftest.py` (sys.path-Manipulation) | **Entfernt** — ersetzt durch deklarativen `pythonpath`-Eintrag in `pyproject.toml` |
| `botctl.py`: `DEFAULT_LEDGER` `.botkit/reviews.json` | **Umgezogen** → `audit/reviews.json` (Audit-Trail gehört in den dafür vorgesehenen, versionierten Ordner statt in einen versteckten Dot-Ordner) |

## 5. Dokumentation: bereinigte Redundanz

- Doppelte Struktur- und Verlinkungsabschnitte im README wurden auf einen
  aktuellen Stand geführt (ein Strukturbaum, ein Doku-Index).
- Die drei Task-Artefakte (`PROMPT.md`, zwei `PR_DESCRIPTION*.md`) standen
  als „aktuelle Doku“ neben Architektur-Dokumenten und enthielten
  Anweisungen für **abgeschlossene** Arbeiten (v1.1.0/v1.3.0) → archiviert
  unter `peer-review/archive/` statt entfernt (Verlauf bleibt Auditierbar).
- `docs/CODE_REVIEW.md` (Findings + Fixes) war Review-Dokument, nicht
  Projekt-Doku → `peer-review/`.
- Veraltete Pfadangaben in allen Dokumenten (`botkit/…`, `python botctl.py …`,
  `.botkit/reviews.json`, `gunicorn app:app`) auf die neue Struktur gezogen.

## 6. CI/CD- & Metadatei-Anpassungen

- **`.github/workflows/bot-review.yml`:** `paths`-Filter, Ruff-/Bandit-Ziele
  und `botctl`-Aufrufe auf `telegram_formatter/…` bzw. `python -m …`
  aktualisiert; Audit-Trail-Pfad `.botkit/**` → `audit/**`.
- **`.github/CODEOWNERS`:** neue Paket-/Ordnerpfade (inkl. `audit/`,
  `security/`, `peer-review/`, `bots/`, `pyproject.toml`).
- **`.github/pull_request_template.md`:** Kommandos + Trail-Pfad aktualisiert.
- **`.gitignore`:** Standard-Artefakte (Build/Cache/Coverage) ergänzt,
  redundanten Eintrag bereinigt.
- **`pyproject.toml`:** `[tool.bandit] exclude_dirs` — Bandit ignoriert
  `tests/`/`examples/` (dort liegen absichtliche Negativbeispiele; das war
  bisher nur über die Aufrufzeile in CI gelöst).

## 7. Verifikation der Aktion

Ausgeführt am 2026-09-11 unter `python -m venv` (Python 3.11.2):

- `pytest -q` → **178 passed** — 175 Baseline-Tests unverändert grün (kein
  Test verloren, keiner angepasst außer den Importpfaden) plus 3
  Regressionstests für das gehärtete Review-Tor (`tests/test_botctl.py`)
- `ruff check .` → All checks passed
- CLI-Dry-Run, Flask-Routen (`/`, `/api/convert`, `/api/send`),
  `botctl`-Durchlauf `review → approve×2 → verify → ledger` gegen
  `examples/own_bot/minimal_bot.py` → fehlerfrei
- Alle internen Markdown-/Dokumentlinks auf Existenz geprüft (Skript siehe
  `CHANGELOG`-Eintrag 2.0.0)

## 8. Kurz-Checkliste für Nutzer der alten Struktur

- [ ] Imports auf `telegram_formatter…` umstellen (oben, Abschnitt 3)
- [ ] `pip install -e .` (optional) oder Repo-Wurzel im `PYTHONPATH`
- [ ] Deployment: Start-Kommando auf `gunicorn "telegram_formatter.app:app"` stellen
      (der Root-Shim `app.py` überbrückt den alten Befehl nur, bis 3.0.0;
      neu angelegte Render-Dienste übernehmen ihn aus `render.yaml`)
- [ ] Eigenen Audit-Trail von `.botkit/reviews.json` nach `audit/reviews.json` übernehmen:
      `git mv .botkit/reviews.json audit/reviews.json` (bzw. Datei verschieben)
- [ ] README/Abschnitt „Struktur“ lesen — neue Ordner `docs/`, `peer-review/`,
      `audit/`, `security/`, `bots/`

---

## 9. Nachzug: v2.6.0 — Versandweg-Verträge (Browser ↔ API)

Der geteilte Bot ist im Browser wählbar. Für Nutzer von `app.py` (als own
Blueprint) und für Skripte, die gegen eine Instanz laufen, ändern sich drei
Verträge — alle abwärtskompatibel für den **authentifizierten** API-Weg:

| Alt (≤ 2.5.0) | Neu (≥ 2.6.0) | Auswirkung |
|---|---|---|
| `<body data-configured="1\|0">` (= „Shared-Versand aus dem Browser möglich“) | `<body data-shared-send>` + `<body data-shared-configured>` (+ `data-byob-enabled`) | Getrennte Aussagen: „Bot existiert“ vs. „Bot ist im Browser nutzbar“. Eigenes Template-Overlay muss die Attribute umbenennen |
| `POST /api/send` ohne `X-Auth-Token` ⇒ immer 503 | offen, wenn `TELEGRAM_FORMATTER_SHARED_WEB_SEND=1` (Standard) **und** `TELEGRAM_CHAT_ID` gepinnt | Anonyme Aufrufe brauchen `"confirm_public": true` und unterliegen `…_MAX_INPUT_CHARS` (8000) statt 100 000 |
| `window.tfSendLabel` (von `byob.js` gesetzt, von `app.js` gelesen) | entfällt — `app.js` leitet die Beschriftung aus `tfByob.isActive()` ab; `byob.js` dispatcht `tf:botsessionchange` am `document` | Eigene Skripte, die das Label überschrieben haben, stattdessen `window.tfByob.describeTarget()` benutzen |
| `telegram_formatter.app._extract_request()` | `_valid_text(data, max_chars=None)`, `_resolve_target_chat(data, require_chat=…)`, `_public_consent(data)` | Nur internal (Unterstrich-Präfix), aber Referenzen in Forks bitte anpassen |

**Keine Änderung** an: `TELEGRAM_FORMATTER_API_TOKEN` + `X-Auth-Token`
(authentifizierte Aufrufe senden weiterhin ohne `confirm_public` mit voller
Längengrenze), an der Payload-Form von `/api/convert` und an allen
BYOB-Endpunkten (`/api/byob/*` verlangen weiterhin Session-Handle +
`session_secret`, und bei gesetztem Operator-Token den Header).

**Umstellungs-Checkliste**

- [ ] Eigenes Template / eigener Client: `data-configured` → `data-shared-send`
      (+ `data-shared-configured`) ersetzen
- [ ] Skripte, die anonym `POST /api/send` nutzen: `"confirm_public": true`
      ergänzen (sonst 400)
- [ ] Betreiber ohne öffentlichen Demo-Chat: `TELEGRAM_FORMATTER_SHARED_WEB_SEND=0`
      setzen (verhält sich dann wieder wie 2.5.0)
- [ ] Bestehende Render-Dienste: neue Variable einmalig im Dashboard pflegen
      (Blueprint-Wirksamkeit siehe Abschnitt 3, „Deployment-Fallback“)
