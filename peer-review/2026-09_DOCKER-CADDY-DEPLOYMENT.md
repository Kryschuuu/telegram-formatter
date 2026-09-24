# Review: Produktionsreife für lokales Docker-Deployment mit Caddy (v2.12.0)

- **Datum:** 2026-09-24
- **Reviewende:** Backend-Engineer (Agent) — Rolle: Docker/Caddy/Telegram-Bots
- **Gegenstand:** Branch `arena/01a0d4fe-telegram-formatter` (Basis: `4dbacec`, v2.11.1);
  Voll-Review der Codebasis (`telegram_formatter/**`, Templates/Assets, Tests,
  Doku) plus neue Deployments-Dateien (`Dockerfile`, `docker-compose.yml`,
  `Caddyfile`, `.env.example`, `.dockerignore`, `docs/DOCKER.md`, `docs/API.md`).
- **Ergebnis:** ✔ freigegeben (mit dokumentierten Follow-ups, s. u.)

## Kontext

Das Tool soll produktionsreif in ein lokales Netz (Server `192.168.0.10`,
Client `192.168.0.20`, Kanal `t.me/mdtotxt_bot_web`). Dazu wurde die gesamte
Codebasis erneut geprüft (Stand nach Security-Audit 2026-09 + Review v2.11.1),
die Lücke „kein Container-/Proxy-Betrieb" geschlossen und alles mit Tests und
Doku abgesichert. Keine API-Änderung, keine ENV-Umbenennung.

## Befunde

### [WARNING] W-1: Keine Liveness-Probe für Orchestrierung

- **Fund:** `telegram_formatter/app.py` (Routen); `render.yaml` (`healthCheckPath: /`)
- **Kategorie:** maintainability
- **Begründung:** Health-Checks mussten `/` aufrufen — volles
  Template-Rendering pro Probe, an Rate-Limits und Seitendarstellung gekoppelt.
  Container-Runtime (Restart-Politik, `depends_on`) und Render prüften damit
  teuer und indirekt.
- **Fix:** Neue Route `GET /healthz` → `{"status":"ok","version"}` (kein
  Rendering, kein Rate-Limit, nur GET ⇒ keine Auth nötig, keine Secrets in
  der Antwort). `Dockerfile`-`HEALTHCHECK` und `render.yaml` zeigen dorthin.
  Tests: 5 Fälle in `tests/test_app.py` + Routen-Vertrag des Root-Shims
  nachgezogen.

### [WARNING] W-2: Kein reproduzierbarer Produktions-Betrieb

- **Fund:** Repository-Wurzel (kein `Dockerfile`, keine Compose-/Proxy-Konfig)
- **Kategorie:** maintainability
- **Begründung:** Produktion hieß bisher „venv + Gunicorn + Hand-Proxy" —
  nicht reproduzierbar, kein TLS-/ACL-Standard, Single-Worker-Gebot für
  BYOB-RAM-Sessions nur als Doku-Satz.
- **Fix:** `Dockerfile` (Non-Root, `--workers 1 --threads 8`, `HEALTHCHECK`),
  `docker-compose.yml` (App ohne Host-Ports, internes Netz, Caddy wartet auf
  gesunde App, `TRUSTED_PROXY_HOPS=1` erzwungen), `Caddyfile`
  (`tls internal`, `remote_ip`-ACL nur für `192.168.0.20`, sonst 403,
  `admin off`), `.env.example` (alle Variablen, Demo-Kanal gepinnt),
  `.dockerignore`. Tests: 21 Vertragstests in `tests/test_deployment.py`.

### [INFO] I-1: README-Umgebungstabelle unvollständig

- **Fund:** `README.md` (ENV-Tabelle) vs. `telegram_formatter/app.py`
  (`CONVERTS_PER_MINUTE`, Standard 60)
- **Kategorie:** maintainability
- **Begründung:** Die Variable existierte im Code, fehlte aber in der Doku —
  Doku/Code-Drift, Betreiber finden das Convert-Limit nicht.
- **Fix:** Tabellenzeile ergänzt; `.env.example` + Vertragstest
  (`test_env_example_documents_every_app_variable`) verhindern künftig Drift.

### [INFO] I-2: README-Projektstruktur veraltet

- **Fund:** `README.md` („Projektstruktur")
- **Kategorie:** maintainability
- **Begründung:** Der Baum listete nur 3 der 5 `docs/`-Dateien
  (`DESIGN.md`, `FORMATTING.md` fehlten) und kannte die neuen
  Deployments-Dateien nicht.
- **Fix:** Baum synchronisiert (alle `docs/`-Dateien + `Dockerfile`,
  `docker-compose.yml`, `Caddyfile`, `.env.example`, Dev-Deps).

### [INFO] I-3: Render-Health-Check auf teurer Route

- **Fund:** `render.yaml` (`healthCheckPath: /`)
- **Kategorie:** maintainability
- **Begründung:** Folgt aus W-1 — Render prüfte die Vollseite statt Liveness.
- **Fix:** Auf `/healthz` umgestellt (per Blueprint-Sync wirksam).

## Geprüft ohne Befund (Stichprobe mit Werkzeug)

- Keine `TODO`/`FIXME`/`XXX`/`HACK`-Rückstände in Paket und Tests.
- Keine Risikomuster im Laufzeitcode (`shell=True`, `eval`/`exec`, `pickle`,
  `os.system`, `subprocess` — Treffer nur im *Regelwerk* von `botkit/review.py`,
  das diese Muster per Design erkennt).
- Alle `requests`-Aufrufe (`sender.py`, `botkit/telegram_api.py`) mit Timeout;
  Fehlermeldungen token-/URL-frei (K-1-Regel eingehalten).
- Token-Hygiene intakt: `BotToken`-Umschlag, kein `--token`-CLI-Flag,
  `.env.example` ohne tokenförmige Werte (per Regex-Vertragstest erzwungen,
  Gitleaks-CI bleibt grün); `.env` git-ignoriert, `.env.example` committet.
- Rate-Limits, BYOB-Kappen (100/3), Session-TTL/Idle, Chat-Pinning,
  Fail-Closed-Pfade und Security-Header unverändert wirksam (522 Tests grün,
  Ruff sauber, Bandit Exit 0).
- Neue Doku gegen Code verifiziert (`docs/API.md`: Response-Formen, Status-
  codes, Defaults, Limits — jeweils an `app.py` abgeglichen).

## Angenommen / abgewogen

- **Kein `read_only`-Container:** Wünschenswert, aber ohne Docker-Host nicht
  verifizierbar — ein ungeprüftes Read-only könnte den Start brechen.
  Follow-up statt Rateversuch.
- **Caddyfile nicht maschinell validiert:** Kein Caddy-Binary in der Sandbox
  (Download blockiert). Syntax sorgfältig gegen Caddy-v2-Dokumentation
  geprüft (`not remote_ip`-Matcher, `handle`-Reihenfolge, `tls internal` bei
  IP-Site, `admin off`); CI-Validierung als Follow-up.
- **`docker compose up` (E2E) nicht ausgeführt:** Kein Container-Runtime in
  der Sandbox. Abgesichert durch PyYAML-Parse + 21 Vertragstests sowie
  Gunicorn-Smoke-Test mit den exakten Dockerfile-Flags (`/healthz`, `/`,
  `/api/convert`, 404-JSON, Header — alle grün). E2E-Schritte für den
  Ziel-Host in `docs/DOCKER.md` §4 dokumentiert.
- **Bandit-Hinweis `nosec B104` (pre-existing):** Warnung ohne Fehlertest,
  Exit 0, bereits auf Basis-Commit vorhanden (per `git stash` verifiziert) —
  unberührt gelassen, kein Bezug zu dieser Änderung.
- **Root-Shim `app.py` unberührt:** Veraltet, Entfernung mit 3.0.0 wie geplant;
  Routen-Vertrag deckt `/healthz` mit ab.
- **Keine `.env` im Repo:** Ausschließlich `.env.example` committet; echte
  Secrets entstehen erst per `cp` auf dem Ziel-Host.

## Follow-ups

- [ ] E2E auf dem Ziel-Host: `docker compose up --build -d`, dann
      Verifikation nach `docs/DOCKER.md` §4 (200 vom Client, 403 von fremder
      Adresse, Versand in `t.me/mdtotxt_bot_web`).
- [ ] CI-Job `caddy validate` (Caddyfile gegen Regressionen pinnen).
- [ ] `read_only` + `tmpfs: /tmp` evaluieren, sobald ein Docker-Host zum
      Testen bereitsteht.
- [ ] Optional: Image-Scan (z. B. Trivy) für `Dockerfile`-Basis in CI.
