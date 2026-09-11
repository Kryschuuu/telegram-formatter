# Changelog

Alle relevanten Änderungen an diesem Projekt, formatiert nach
[Semantic Versioning](https://semver.org/) und
[Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

## [Unreleased]

### Behoben (Web-Oberfläche — Design-Bruch)

- **Ungestylter Rohtext:** Die Seite hing am Tailwind-Play-CDN; dessen zur
  Laufzeit injizierten Inline-`<style>`-Regeln blockierte die CSP
  (`style-src` ohne `'unsafe-inline'`) — komplettes Layout fiel aus. Das UI
  ist jetzt **vollständig selbst-gehostet** (4 CSS-Schichten + 2 JS-Module +
  Inline-SVG-Icons unter `static/`), die CSP ist strikt `'self'` und jede
  CDN-Whitelist ist entfallen. Damit ist die Klasse dieses Fehlers strukturell
  ausgeschlossen; `tests/test_app.py` & `tests/test_frontend.py` erzwingen beides.

### Hinzugefügt (Web-Oberfläche — Design-System „tf“ & Themes)

- **Vier Themes + Auto-Modus:** Light (Standard), Dark (Telegram-Nacht),
  Colorful (Verlauf + Glas-Karten), Minimal (monochrom/kantig); „Auto“ folgt
  dem Betriebssystem ohne JavaScript-Anteil (reine `prefers-color-scheme`-
  Media-Query in `tokens.css`).
- **Theme-Switcher** im Sticky-Header (`#themeSwitcher`): `localStorage`
  (`tf-theme`), `aria-pressed`-Status, Zustandsklasse `.is-active`,
  Boot synchron im `<head>` (kein Flash of wrong theme), robust ohne
  `localStorage`/`matchMedia`, No-JS-Fallback aufs Systemtheme.
- **Selbst-gehostetes Design-System:** `static/css/tokens.css|base.css|
  layout.css|components.css` (Token-Schicht, Reset/Typo, Responsive-Grid,
  `.tf-*`-Komponenten) — Farbhartkodierung außerhalb der Tokens ist durch
  Tests verboten; Themewechsel = ein Attribut, kein Markup.
- **UX-Schmuck:** Zeichenzähler (warnend > 4096), Strg/Cmd+Enter = senden,
  Skip-Link, `role="status"`-Live-Region, Noscript-Hinweis, Favicon (SVG),
  reiche Live-Vorschau (Code, Durchstreichen, Links, Formel-Highlight —
  weiterhin escaping-first, kein HTML-Injection-Weg).
- **Dokumentation:** [docs/DESIGN.md](docs/DESIGN.md) (Architektur, Theme-
  Rezept, Switcher-Verhalten, Teststrategie, Erweiterungs-Guide).

### Geändert (Web-Oberfläche)

- `telegram_formatter/templates/index.html` neu geschrieben: semantische
  `.tf-*`-Klassen statt Tailwind-Utilities; **alle Funktions-Hooks bleiben
  contract-getestet erhalten** (`#input`, `#preview`, `#payloads`, `#sendBtn`,
  `#resetBtn`, `#sendStatus`, `data-convert-url`/`data-send-url`, Howto/FAQ/
  Disclaimer/Footer). `static/app.css`/`app.js` → `static/css/*` + `static/js/*`.
- `telegram_formatter/app.py`: CSP vereinfacht (nur `'self'`, `data:` für
  Favicons); Header-Text accordingly. Endpunkte, Limits, Rate-Limits,
  Auth/Guards unverändert.

### Tests

- `tests/test_frontend.py`: 20 Strukturverträge (Token-Vollständigkeit je
  Theme, `var()`-Abdeckung, Asset-Existenz & Waisenfreiheit, JS↔HTML-IDs,
  Switcher-Markup, mobile-first-Breakpoints, Kontrast-Heuristik,
  Browser-Baseline-Verbotsliste, HTML-Wellformedness).
- `tests/frontend/jsdom_spec.cjs` + `tests/test_jsdom_smoke.py`: funktionale
  DOM-Tests (Theme-Boot/Klicks/Persistenz, Debounce+Fetch, Vorschau,
  Sende-/Fehlerpfad, Reset) gegen das echt gerenderte Template — 37 Checks;
  sauberer Skip ohne Node/jsdom.
- Bestehende Suite unverändert grün: **259 passed** (`pytest -q`).

## [2.1.0] - 2026-09-11

Sicherheitshärtung und Fehlerbehebungen als Umsetzung des externen
Code-Reviews (baut auf dem Stand des [Unreleased]-Blocks: Root-Shim + render.yaml) ([`SECURITY_AUDIT.md`](SECURITY_AUDIT.md)); die Nummern (K-*/H-*/
M-*/B-*) verweisen auf die Befunde dort.

### Behoben (Sicherheit — kritisch/hoch)

- **K-1:** `SendError` enthielt bei Netzwerkfehlern die Requests-Fehlermeldung
  samt URL — **und damit den Bot-Token im Klartext**, den `/api/send` an den
  HTTP-Client zurückgab. Fehlermeldungen nennen jetzt nur noch Statuscode,
  Exception-Klasse und gekürzte API-Description.
- **K-2:** `/api/send` war ein offener Relay: `chat_id` aus dem Request-Body
  überschrieb den konfigurierten Zielchat, ohne Authentifizierung. Jetzt:
  Chat-Pinning auf `TELEGRAM_CHAT_ID`, Validierung gegen `CHAT_ID_PATTERN`,
  optionales API-Token (`X-Auth-Token`), Origin-Bindung, Größen- (413) und
  Rate-Limits.
- **H-1:** Review-Gate-Bypasses geschlossen: Import-Alias-Auflösung
  (`requests as rq`), Modul-Konstanten-/f-String-Faltung für URL-Prüfung,
  Token-Literal-Scan in *allen* String-Konstanten (auch `AnnAssign`, Dicts,
  Call-Argumente), `importlib`/`tempfile`/`shutil`/`builtins` verboten.
- **H-2:** `MAX_CONTENT_LENGTH` gesetzt; Eingabelänge wie im botkit-Layer
  begrenzt; Rate-Limiter pro IP; Doku-Gunicorn-Flags (`--workers/--timeout`).
- **H-3/H-4:** Keine Rohtext- oder Traceback-Leaks mehr: `response.text` wird
  nicht übernommen; der `RedactingFilter` redigiert jetzt auch Exception-Stacks
  (die Handler formatieren nach dem Filtern).
- **H-5:** UI ohne Inline-Skripte (CSP-fähig), CDN-Skript versionsgepinnt
  (SRI beim Release nachziehen); CSP/nosniff/no-referrer/frame-ancestors-Header.
- **M-3/N-2:** `api_base` nur noch HTTPS (localhost ausgenommen);
  Gitleaks-Allowlist auf Pfad+Muster (`matchAll`) verschärft.

### Behoben (Funktional)

- **B-1:** Chunk-Grenzen rissen `<b>/<code>/…`-Tags, `$$`-Formelblöcke und
  ``` fences entzwei → Telegram-400 bzw. kaputtes Rendering. Neu:
  Tag-Balance pro Chunk (mit Nachtrag an den Folgechunk) und atomare
  Rich-Bereiche mit `chunk_markdown_safe`-Artiger Aufteilung.
- **B-2:** `botctl send --local-trust` brach immer mit „Bot ist nicht
  freigegeben" — der Gate wurde dem Manager fälschlich übergeben.
- **B-3:** `ReviewTicket.is_approved()` ignorierte Ablehnungen — ein
  abgelehnter Bot blieb im Local-Modus freigabefähig; Re-Submit erzeugt
  jetzt ein frisches Ticket.
- **B-4:** Preisangaben (`$100 und $200`) wurden als LaTeX fehlgeroutet;
  Inline-Math folgt jetzt GFM-Randregeln (in allen drei Scannern einheitlich).
- **B-5/B-6/B-7/B-8/B-9/B-10/B-11/B-12:** `text`-Typprüfung (400 statt 500),
  Teilversand-Rückmeldung + `retry_after`, `ok`-Flag-Prüfung,
  Verzeichnis-Eingabe für `botctl review`, `RegistrationError`-Fang in
  `approve`, Owner-Fallback `local`, Audit-Trail unter `flock`,
  Fixture-Restoration, Listen-Einrückung.
- **N-1:** NUL-Zeichen aus Nutertext entfernt (Platzhalter-Kollision).

### Geändert

- `chunk_text`/`_group`: inkrementelle Längenführung (O(n²) → O(n)).
- Versand nutzt eine wiederverwendete `requests.Session` (TLS-Pooling).
- `botkit`-Session/`InMemoryTokenVault`: Thread-safe (Locks); Sessions
  entfernen sich beim Schließen selbst aus dem Manager.
- CLI `--token` veraltet (ps/Historie); Environment/`botctl` empfohlen.
- CI: Bandit ohne `-ll`; `botctl verify` als aktiver Schritt (bei
  `vars.BOT_ID`/`vars.BOT_PATH`); Dependabot (pip, actions).
- Dependencies: `gunicorn` 23.0.0 → 26.2.0, `requests` 2.33.0 → 2.34.2.

### Hinweise (bewusst nicht Teil dieses Releases)

- Parser-Ein-Pass-Rewrite (O-1) und Hash-Lockfiles (plattformabhängig) sind
  als separate Änderungen vorgesehen; Details im Audit-Bericht.
## [Unreleased]

### Behoben (Deploy-Start nach der Paket-Umstellung)

- **`ModuleNotFoundError: No module named 'app'` auf Render.com.** Der dort
  hinterlegte Start-Befehl `gunicorn app:app` — der Python-Default *vor* der
  Umstellung — zeigte nach 2.0.0 auf ein nicht mehr existierendes Root-Modul:
  Build erfolgreich (`== Build successful`), Start abgestürzt. Der kanonische
  Einstieg ist `telegram_formatter.app:app`; für noch nicht umgestellte
  Deployments leitet ab sofort ein Root-Shim weiter, der Blueprint stellt den
  Befehl dauerhaft richtig.

### Hinzugefügt

- **`app.py` (Repository-Wurzel) — veralteter Kompatibilitäts-Shim.**
  Einzeilige, rein weiterleitende Adresse (`from telegram_formatter.app import
  app`), damit ein im Hosting-Dashboard hinterlegtes `gunicorn app:app` ohne
  Dashboard-Änderung sofort wieder läuft. **Als veraltet markiert; Entfernung
  mit 3.0.0.** Abgesichert durch zwei Tests in `tests/test_app.py`:
  (1) identisches WSGI-Objekt plus Routen-/HTTP-200-Rauchtest über den Shim,
  (2) AST-Vertrag, der den Shim auf Docstring + einen Re-Export beschränkt
  (keine Funktionen, Klassen, Aufrufe, Control-Flow) — so verkommt er nie zur
  zweiten Logik-Kopie.
- **`render.yaml` — Render-Blueprint.** Deklariert den kanonischen Start
  `gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT`,
  `healthCheckPath: /`, `PYTHON_VERSION` `3.11` sowie `TELEGRAM_BOT_TOKEN` und
  `TELEGRAM_CHAT_ID` mit `sync: false` (Secrets bleiben im Dashboard-Store und
  landen nie im Git). Blueprint-Sync gilt nur für aus dem Blueprint erzeugte
  Dienste; Ablauf für bestehende Services: `docs/DEPLOYMENT.md`, Schritt 3a.

### Geändert

- **Doku nachgezogen:** `docs/DEPLOYMENT.md` (Blueprint-Alternative,
  Sync-Hinweis für bestehende Services, Troubleshooting-Eintrag zum
  `ModuleNotFoundError`), `MIGRATION.md` §3 (Deployment-Fallback: Root-Shim ↔
  kanonischer Einstieg), README-Strukturbaum, `docs/ARCHITECTURE.md` §5
  (Konfigurationstabellen um Shim und Blueprint ergänzt).
- **CI:** Ruff prüft das Root-`app.py` ausdrücklich mit
  (`ruff check app.py telegram_formatter examples tests`); der
  `pull_request.paths`-Filter löst den Workflow jetzt auch bei Änderungen an
  `app.py`/`render.yaml` aus. `.github/CODEOWNERS` markiert `/app.py` und
  `/render.yaml` als review-pflichtig (beide ändern den laufenden Dienst).

### Verifiziert

- `pytest -q` → **180 passed** (178 Baseline aus 2.0.0 + 2 Shim-Tests);
  `ruff check app.py telegram_formatter examples tests` sauber; Bandit `-ll`
  ohne Befund.
- Lokale Replikation des Render-Starts: `gunicorn app:app` (alt, über den Shim)
  und `gunicorn "telegram_formatter.app:app"` (kanonisch) liefern beide
  HTTP 200. Der Footer sagt „GNU GPL v3 · Version 2.0.0" — die Version kommt
  aus `telegram_formatter.__version__` und nicht mehr aus einer hartkodierten
  Angabe (zuvor auf „MIT · 1.2.0" verdriftet).

## [2.0.0] - 2026-09-11

### Geändert (Repository-Reorganisation — verhaltensneutral)

- **Paketstruktur:** `utils.py`, `sender.py`, `cli.py`, `app.py`, `botctl.py`,
  `botkit/` und `templates/` in das Python-Paket `telegram_formatter/`
  überführt; zentrale Fassade `telegram_formatter/__init__.py` mit
  `__version__` und bequemen Re-Exports (`build_messages`, `send_message`).
  Imports: `from utils import …` → `from telegram_formatter.utils import …`;
  CLI-Aufrufe: `python cli.py …` → `python -m telegram_formatter.cli …`,
  analog `botctl`. Deployment: `gunicorn "telegram_formatter.app:app"`.
  Vollständige Alt→Neu-Mappe: [MIGRATION.md](MIGRATION.md).
- **Doku nach Zweck getrennt:** `docs/` (aktuelle Architektur/Doku),
  `peer-review/` (Review-Berichte + Verlauf inkl. `archive/` für
  abgeschlossene Artefakte), `audit/` (Prüf-Matrix, Audit-Trail), `security/`
  (Schutzziel-Matrix). `docs/BLUEPRINT.md` → `docs/ARCHITECTURE.md`;
  `docs/CODE_REVIEW.md` → `peer-review/CODE_REVIEW.md`; `docs/PROMPT.md` und
  beide `docs/PR_DESCRIPTION*.md` (abgeschlossene Task-Artefakte zu
  v1.1.0/v1.3.0) → `peer-review/archive/`.
- **Konfiguration konsolidiert:** `ruff.toml` und der `conftest.py`-Sys.path-
  Hack aufgelöst in eine `pyproject.toml` (PEP 621: Paket-Metadaten,
  Konsolen-Skripte `telegram-formatter`/`botctl`, Ruff-, pytest- und
  Bandit-Konfiguration; Laufzeit-Deps dynamisch aus `requirements.txt` —
  keine duplizierten Pin-Listen).
- **Audit-Trail umgezogen:** Standardpfad `botctl` von `.botkit/reviews.json`
  → `audit/reviews.json` (versionierbarer, dokumentierter Ort; Verhalten
  identisch).
- **Alle Verlinkungen aktualisiert:** README-Strukturbaum & -kommandos,
  `docs/*`, CI-Workflow (`paths`-Filter, Lint-/Scan-Ziele, `botctl`-Aufrufe),
  `.github/CODEOWNERS`, PR-Template, Docstrings und Beispiel-Code.

### Hinzugefügt

- `LICENSE` — fehlte trotz README-badge „MIT"; im Zuge der Reorganisation
  angelegt und an die Lizenz-Umstellung auf `main` (`a2c3dcb`)
  **angleichen: GNU GPL v3** (`license = "GPL-3.0-or-later"` in
  `pyproject.toml`, README-Badge angepasst).
  `CONTRIBUTING.md`, `MIGRATION.md`, `.github/SECURITY.md`
  (Offenlegungsprozess, von GitHub erkannt), `security/README.md`
  (Schutzziel-Matrix S1–S8 mit Verifikationszuordnung), `audit/README.md`,
  `peer-review/README.md` (Konventionen) + `peer-review/TEMPLATE.md`
  (Review-Berichtsvorlage), `bots/README.md` (dokumentierter Ort für
  Nutzer-Bots — die CI/CODEOWNERS-Pfade `bots/**` existierten bereits ohne
  Ordner).

### Entfernt (toter Code & Redundanz)

- `utils.RICH_MESSAGE_MAX_BLOCKS` — definiert, im gesamten Repo ungenutzt.
- `botkit.review.INFO` — dritter Severity-Wert ohne jede Referenz
  (RULES kennen nur `BLOCKER`/`WARNING`).
- `botkit.__version__` — duplizierte die Projektversion und driftete bereits;
  einzige Quelle ist jetzt `telegram_formatter.__version__`.
- `conftest.py` (17 Zeilen sys.path-Manipulation) — ersetzt durch deklarativen
  `pythonpath`-Eintrag in `pyproject.toml`.
- Veraltete README-Doppelungen (Strukturbaum vs. Doku-Index führten
  verschiedene, teils inkonsistente Dateilisten) — konsolidiert.

### Behoben (Review-Gate-Härtung nach erstem CI-Lauf)

- **CI `bot-gate`:** Die Diff-Schleife zieht jetzt nur noch `*.py`-Dateien
  durch `botctl review` (`grep -E '\.py$'`). Zuvor stürzte das Gate ab, weil
  das neue `bots/README.md` vom `bots/**`-Filter erfasst und in den
  AST-Parser gefüttert wurde.
- **`botctl review`:** nicht-parsbare Eingaben (Markdown, Binärdateien,
  Nullbytes) werden als Eingabefehler gemeldet — Exit-Code 2, klare Meldung,
  **kein** Ticket und **kein** Audit-Trail — statt mit rohem Python-Traceback.
  Regressionstests: `tests/test_botctl.py` (3).

### Verifiziert

- `pytest -q` → **178 passed** (175 aus v1.3.0 unverändert + 3 Regressionstests
  für `botctl review`); `ruff check .` sauber; Bandit `-ll` ohne Befund.
- End-to-End-Rauchtests: CLI-Dry-Run, Flask-Testclient (`/`,
  `/api/convert`, `/api/send`), `botctl`-Umlauf `review → approve×2 →
  verify` inkl. Ledger-Roundtrip gegen `examples/own_bot/minimal_bot.py`.
- Alle internen Markdown-/Quelltext-Links und relativen Pfade skriptgeprüft
  (0 defekte Referenzen nach der Migration).

## [1.3.0] - 2026-09-11

### Hinzugefügt

- **Dezentrales Bot-Konzept (BYOB — Bring Your Own Bot)**: Neues Paket
  `botkit/` als Alternative zum zentralen Bot. Nutzer registrieren ihren
  eigenen Bot, lassen den Code reviewen und nutzen ihn in einer ephemeren
  Session — ohne zentrale Datenspeicherung.
- **`botkit/tokens.py`**: `BotToken`-Umschlag (Klartext nur über `reveal()`,
  `repr` redacted), `InMemoryTokenVault` (RAM + TTL) und
  `PassthroughTokenVault` (strengster Modus, speichert nichts).
- **`botkit/privacy.py`**: `RedactingFilter` für alle Logger (inkl. urllib3),
  `audit()` als einzige Log-Schnittstelle (nur Metadaten), prozesslokale
  HMAC-Fingerprints, `scrub_environment()` gegen Token-Vererbung.
- **`botkit/registry.py`**: Registrierung mit `getMe`-Verifikation
  (`is_bot`, ID-Gegenprobe) — speichert Identität und Status, **niemals** das
  Token; Validierung für `chat_id` und Besitzer-Pseudonym.
- **`botkit/review.py`**: AST-basierte Regeln BK001–BK012 (Persistenz,
  Fremdnetzwerk, dynamische Ausführung, hartkodierte Secrets, Inhalte im
  Log), Checkliste C1–C9, `ReviewLedger` (Append-only, Metadaten) und
  `ReviewGate` mit Vier-Augen-Prinzip (2 Freigaben, ≥1 Maintainer:in,
  Freigabe an die SHA-256-Prüfsumme des Codes gebunden).
- **`botkit/session.py`**: `BotSession`/`SessionManager` mit TTL,
  Leerlauf-Timeout, Rate-Limit-Fenster und Eingabevalidierung; Versand über
  die bestehenden Module `utils.build_messages` + `sender.send_message`.
- **`botkit/telegram_api.py`**: `getMe`, `getUpdates`, `setWebhook`,
  `deleteWebhook` — die einzigen erlaubten API-Aufrufe.
- **`botctl.py`**: CLI mit `register`, `review`, `approve`, `verify`, `send`
  und `checklist`.
- **Referenz-Bot** `examples/own_bot/minimal_bot.py` (besteht alle BK-Regeln)
  und **Negativbeispiel** `tests/fixtures/insecure_bot.py` (löst BK001–BK012
  aus).
- **Review-Infrastruktur**: `.github/workflows/bot-review.yml`
  (Ruff, Bandit, pip-audit, gitleaks, `botctl review`, pytest),
  `.github/CODEOWNERS`, PR-Template mit Pflicht-Checkliste.
- **Doku**: `docs/DECENTRAL_BOT_ARCHITECTURE.md` (Architektur, Nutzerreise,
  Hürden-Tabelle, Security-Durchsetzung, Peer-Review-Prozess,
  Vergleich mit dem zentralen Bot).

### Geändert

- `conftest.py` (neu) sorgt dafür, dass Projekt-Root und `tests/` beim Import
  auflösbar sind.
- Bestehende Module (`utils.py`, `sender.py`, `cli.py`, `app.py`) bleiben
  unverändert — `botkit` ergänzt das Projekt nur.

### Behoben (Audit-Runde CI)

- **pip-audit**: `requests` von 2.32.4 auf **2.33.0** angehoben
  (`PYSEC-2026-2275`), `pytest` von 8.3.5 auf **9.1.1** (`PYSEC-2026-1845`).
  Beide Audits (Laufzeit und Entwicklung) sind damit ohne Befund.
- **gitleaks**: das Beispiel-Token in den Docstrings von
  `examples/own_bot/minimal_bot.py` und `botkit/tokens.py` war ein
  Dummy-Wert, löste aber zu Recht den Secret-Scan aus — ersetzt durch
  Platzhalter (`<token-von-botfather>` bzw. `os.environ[...]`).
- **gitleaks (Negativbeispiel)**: das Token in
  `tests/fixtures/insecure_bot.py` steht jetzt über zwei Zeilen
  (implizite String-Konkatenation). Der AST faltet es zu einer Konstante,
  die BK006-Regel greift weiterhin; der zeilenbasierte Secret-Scanner
  schlägt nicht mehr an.
- **`.gitleaks.toml`** ergänzt: eng begrenzte Freigaben für die
  Negativbeispiele unter `tests/fixtures/` sowie für den historischen
  Dokumentations-Dummy in alten Commits.
- **Workflow `bot-review.yml`**: Berechtigungen für PR-Kommentar und
  SARIF-Upload (`pull-requests: write`, `actions: write`) ergänzt —
  damit entfällt der Fehler „Resource not accessible by integration";
  außerdem wöchentlicher Termin-Check der Abhängigkeiten und Audit der
  Entwicklungs-Abhängigkeiten.

### Tests

- 175 Tests gesamt (vorher 81): 94 neue Tests für Token-Handling, Redaction,
  Registry, Review-Gate (Vier-Augen, Prüfsummen-Bindung) und Sessions
  (TTL, Leerlauf, Rate-Limit, keine Inhalte in Logs).

## [1.2.0] - 2026-09-02

### Hinzugefügt

- **DeepSeek/Gemini-LaTeX-Syntax**: Unterstützung für `\(...\)` (Inline-Math)
  und `\[...\]` (Display-Math) zusätzlich zum klassischen `$...$`/`$$...$$`.
  Diese Syntax wird von DeepSeek Chat, Gemini und anderen KI-Tools verwendet.
  Formeln werden korrekt als LaTeX erkannt und als Rich-Message versendet.
- **Neue Funktion `convert_deepseek_latex_syntax()`** in `utils.py`: normalisiert
  `\(...\)` → `$...$` und `\[...\]` → `$$...$$`. Eingebunden in
  `markdown_to_rich_markdown()` und `markdown_to_html()`.

### Behoben

- **Telegram rendert DeepSeek-Delimiter nicht**: Bisher wurden `\(...\)`/`\[...\]`
  zwar als LaTeX *erkannt* (Rich-Pfad), aber unverändert in die Payload
  geschrieben — Telegram gab sie dadurch als rohen Text mit Backslashes aus.
  Die Delimiter werden jetzt in die von Telegram unterstützte Dollar-Syntax
  übersetzt, während der Formelinhalt 1:1 erhalten bleibt.

### Geändert

- Entfernt: ungenutzte Hilfsfunktion `_find_delimiter_close()` (toter Code, der
  zudem eine `DeprecationWarning` wegen ungültiger Escape-Sequenz auslöste).
- Dokumentation (`README.md`, `templates/index.html`) beschreibt die
  DeepSeek/Gemini-Syntax; Versionsangabe der Web-Oberfläche auf 1.2.0 angehoben.

### Tests

- 81 Tests gesamt (vorher 66), davon 26 rund um die DeepSeek/Gemini-Syntax:
  Erkennung (`split_formulas`, `has_latex`), Konvertierung
  (`convert_deepseek_latex_syntax`) sowie die Payload-Ausgabe über
  `build_messages()`. Abgedeckt sind gemischte Delimiter, mehrzeilige
  Display-Formeln, verschachtelte Klammern, Preisangaben (`$ 20`),
  unvollständige Delimiter, doppelte Backslashes und Code-Schutz.

## [1.1.0] - 2026-09-02

Neue Web-UI-Features (Minor-Bump nach Semantic Versioning).

### Hinzugefügt
- **Reset-Button** „Zurücksetzen" in der Web-Oberfläche: leert Eingabe,
  Vorschau, Payload-Ausgabe und Statusmeldung und setzt den Fokus zurück
  ins Eingabefeld.
- **Buy-me-a-coffee-Button** (`https://buymeacoffee.com/rg4free`) im Header
  (gelb hervorgehoben) sowie als Unterstützungs-Link im Footer
  (`target="_blank"`, `rel="noopener"`).
- **Disclaimer**: ausführlicher Haftungsausschluss als Hinweisbox unter dem
  Editor (eigene Verantwortung, Akzeptanz der Nutzungsbedingungen, keine
  Verbindung zu Telegram, keine Datenspeicherung, Haftungsausschluss) plus
  Kurzform im Footer.
- **Howto**: nummerierte Schritt-für-Schritt-Anleitung direkt auf der Seite
  (Token via @BotFather, Chat-ID ermitteln, Eingabe, Vorschau prüfen,
  Versand).
- **FAQ**: sieben aufklappbare Akkordeons (Bot-Token, Chat-ID,
  LaTeX-Rendering, Nachrichtenlänge, Datenschutz, Formatierung,
  Fehlerbehebung).
- Font-Awesome-Icons für Buttons, Abschnitte und Statusanzeigen.
- Tests: `tests/test_app.py` prüft Reset-Button, Coffee-Link, Disclaimer
  und Howto/FAQ (55 Tests gesamt).
- `docs/PROMPT.md` (wiederverwendbarer Arbeitsauftrag) und
  `docs/PR_DESCRIPTION.md` (fertiger PR-Text).

### Geändert
- **Optik**: Sticky-Header, Karten-Layout für alle Sektionen, neuer
  mehrspaltiger Footer; Konfigurationsstatus („Bot konfiguriert" /
  „Kein Bot-Token gesetzt") im Header sichtbar.
- README: Versions-Badge und Versionsangabe auf **1.1.0**,
  Funktionsübersicht um die neuen UI-Features ergänzt.

## [1.0.0] - 2026-09-02

Komplette Überarbeitung: Code-Review, Bugfixing, Tests, Dokumentation.

### Hinzugefügt
- Automatisches Nachrichten-Splitting an Absatz-, Zeilen- und Wortgrenzen
  (`chunk_text`) für beide Telegram-Limits (4096 / 32768 Zeichen).
- Separater Versand-Layer (`sender.py`) mit eigener `SendError`-Exception und
  lazy `requests`-Import.
- Kommandozeilen-Einstieg (`cli.py`) mit Datei-/STDIN-Eingabe und `--send`.
- Flask-Weboberfläche (`app.py`) mit den Routen `/api/convert` und `/api/send`
  und einer passenden, schlanken Editor-Seite.
- Vollständige Test-Suite (`tests/`): 54 Tests für Konvertierung, LaTeX,
  Tabellen, Splitting, Versand (gemockt) und Flask-Routen.
- Dokumentation: `docs/BLUEPRINT.md`, `docs/DEPLOYMENT.md`,
  `docs/CODE_REVIEW.md` sowie dieses Changelog.

### Behoben
- **LaTeX-Zerstörung** durch naive Regex bei verschachtelten Formeln
  (`\binom{\binom{70}{6}}{33}`). Ersetzt durch zeichenbasiertes Parsing mit
  Klammerbilanz-Prüfung.
- **Ungültiges Rich-Message-Payload** (`format`/`text` statt korrektem
  `markdown`-Feld), das `400 Bad Request` ausgelöst hätte.
- **Fehlendes Splitting** langer klassischer Nachrichten (4096-Zeichen-Limit).
- **Rich-Chunking-Bug**, der Chunks länger als das Limit erzeugen konnte.
- **Unterstreichen-Semantik** in Rich Markdown (`__x__` → `<u>x</u>`).
- **Unicode/Diakritika** (z. B. `ì`) durch NFC-Normalisierung und explizites
  UTF-8-Einlesen.

### Geändert
- Abhängigkeiten bereinigt und versioniert: `pyTelegramBotAPI`,
  `telegramify-markdown`, `APScheduler` und doppeltes `Flask` entfernt.
- Verwaiste Web-Oberfläche („PostMaster Pro") durch funktionierende
  Editor-Seite ersetzt.
- Code strukturell getrennt (utils / sender / cli / app) und durchgängig
  kommentiert, typannotiert und dokumentiert.
- `requirements-dev.txt` für Entwicklungswerkzeuge ergänzt.

### Entfernt
- Toter Code: ungenutzte Routen, ungenutzte JS-Handler, ungenutzte
  Abhängigkeiten.
