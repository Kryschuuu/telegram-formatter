# Code-Peer-Review — v2.15.0

**Datum:** 2026-09-28
**Geprüfter Stand:** `main` @ `e528269` (v2.14.1) + Deployment-Fixes
**Umfang:** Vollständiger Produktionscode (7 835 Zeilen), 9 Doku-Dateien,
CHANGELOG, MIGRATION, SECURITY_AUDIT, `peer-review/`, Deployment-Dateien,
CI-Workflow, `pyproject.toml`
**Methodik:** Manueller Review aller Module, statische Analyse (Ruff 0.16.9,
Bandit 1.9.4 — beide clean), 690 grüne Tests als Baseline

---

## Zusammenfassung

Der Code macht einen **sehr reifen** Eindruck: saubere Schichtarchitektur
(I/O-freie Konvertierung vs. Netzwerklayer), durchdachtes Token-Handling
(`BotToken`-Umschlag mit Redaction, `compare_digest`, RAM-only-Vaults),
Fail-Closed-Defaults im Session-Layer, 690 grüne Tests, gepinnte
Dependencies. Ruff und Bandit melden **null** Befunde — die gefundenen
Probleme liegen tiefer: in Deployment-Konfiguration, Doku-Drift und
Redundanz.

**Acht Befunde**, davon zwei mit Produktionsauswirkung (F1, F5).

---

## Befunde

### F1 · Versions-Drift im Deployment 🟠

**Fundstelle:** `Dockerfile:11-12`, `docker-compose.yml:28`

**Befund:** Das Image-Tag war auf `telegram-formatter:2.12.0` hartkodiert,
während die Quelle `2.14.1` war. Ein `docker compose up` im Repo baute und
startete still eine veraltete Version.

**Begründung:** Genau das passierte am 2026-09-28: Ein `docker compose up`
im Repo startete einen **zweiten Stack** neben dem NAS-Betrieb — zwei
Instanzen mit demselben Bot-Token, beide sendefähig. Der Duplikat-Stack war
vom Host aus direkt erreichbar (`http://172.24.0.2:5000/healthz` → 200).

**Fix:** Tag auf `2.15.0` gesetzt. Vertragstest
`test_compose_image_tag_matches_version` in `tests/test_deployment.py` macht
künftige Drift unmöglich.

---

### F2 · Versions-Gate an zwei Stellen, davon eine hohle Hülle 🟡

**Fundstelle:** `tests/test_public_demo_chat.py:117`,
`tests/test_split_64000.py:258`

**Befund:** Zwei hartkodierte `assert __version__ == "2.14.1"`. Der zweite
saß in `test_build_messages_rejects_over_64000_via_shared_limit` — einem
Test **ohne jede Verhaltens-Assertion** (Parameter `monkeypatch=None`, wird
nie als Fixture benutzt). Beide brechen bei jedem Bump.

**Begründung:** Ein Test ohne Verhaltens-Assertion ist keine Absicherung,
nur ein Hindernis bei Releases. Die doppelte Version bedeutet doppelte
Pflicht bei jedem Bump.

**Fix:** Gate an einer Stelle konsolidiert
(`test_version_matches_release` in `test_public_demo_chat.py`), hohler
Test entfernt.

---

### F3 · Falscher Service-Name in der Doku 🟡

**Fundstelle:** `docs/DOCKER.md:261-312`

**Befund:** `docker compose exec proxy …` — im Solo-Stack heißt der
Service `caddy`. Die Zeilen sind aus dem NAS-Runbook kopiert und dort
schlicht falsch.

**Begründung:** Ein Betreiber, der der Doku folgt, erhält
`no service named proxy` — und vermutet einen kaputten Stack.

**Fix:** `proxy` → `caddy`. Alle 19 `docker compose`-Kommandos in
`docs/DOCKER.md` zusätzlich mit `-f docker-compose.standalone.yml`
ergänzt (siehe F-Änderung der Compose-Datei).

---

### F4 · ruff-Pin-Drift 🟢

**Fundstelle:** `.github/workflows/bot-review.yml:48`

**Befund:** CI pinnt `ruff==0.16.7`, lokal lief `0.16.9`.

**Begründung:** Gate-Ergebnisse sind nicht reproduzierbar — ein lokal
sauberer Build kann in CI rot sein und umgekehrt.

**Fix:** Pin auf `0.16.9` aktualisiert.

---

### F5 · Gunicorn-26-Control-Socket 🟠

**Fundstelle:** `Dockerfile:40`

**Befund:** `useradd --system --no-create-home` vs. Gunicorn 26, das
standardmäßig ein Control-Socket unter `$HOME/.gunicorn/gunicorn.ctl`
anlegt. Der Start scheiterte mit:

```
[ERROR] Control server error: [Errno 13] Permission denied: '/home/appuser'
```

**Begründung:** Der Fehler trat in **jedem** Deployment auf — auch im
Solo-Stack. Er ist harmlos (der Container läuft), aber er legt bei jeder
Log-Sichtung eine Fehlalarm-Spur und könnte ein echtes Problem
verschleiern.

**Fix:** `--create-home`, damit `$HOME` existiert und `appuser` es
beschreiben kann. Der Socket wird mit 0700 angelegt; im Container ist das
Interface ohnehin unbenutzbar (Verwaltung läuft über
`docker compose restart`).

---

### F6 · DRY-Verstoß `_error_detail` 🟡

**Fundstelle:** `sender.py:124-136`, `telegram_api.py:51-68`

**Befund:** Fast identische Implementierung in zwei Modulen.

**Begründung:** Zwei Quellen für dieselbe Logik — eine künftige Änderung
muss an zwei Stellen gemacht werden.

**Fix:** `error_detail()` in `sender.py` öffentlich gemacht,
`telegram_api.py` nutzt sie.

---

### F7 · Verwirrender `chat = "0"`-Fallback 🟢

**Fundstelle:** `app.py:1088`

**Befund:** Selbstbetrieb ohne ENV-Chat und ohne Body-chat_id verwendete
`"0"` als Zielchat — ein Wert, der nie eine gültige Chat-ID ist.

**Begründung:** Der Fallback ist für Dry-Run und `/api/convert` korrekt
(kein chat_id nötig), aber die Absicht war nicht erkennbar.

**Fix:** Fallback beibehalten, aber ausdrücklich dokumentiert.

---

### F8 · `chat_id or 0` in der CLI 🟢

**Fundstelle:** `cli.py:81`

**Befund:** Gleiche Fallback-Problematik wie F7.

**Begründung:** Siehe F7.

**Fix:** Dry-Run ohne chat_id erlaubt, echte Senden nur mit chat_id.

---

## Nicht als Befund gewertet

Geprüft und **bewusst nicht** geändert. Damit sie beim nächsten Review
nicht erneut untersucht werden:

| Punkt | Warum kein Befund |
|---|---|
| **`requests.Session` prozessweit geteilt** | Laut Dokumentation nicht thread-sicher (mutiert `cookies` je Antwort). Die beteiligten Dikt-Operationen sind GIL-atomar; es wurde **kein** Fehler reproduziert. |
| **Kein `parse_mode`-Fallback** | Telegram lehnt `can't parse entities` ab. Ein zweiter Versuch ohne `parse_mode` stellt Text **ohne Formatierung** zu — stiller Inhaltsverlust. |
| **Keine Retries auf Netzwerkfehler/5xx** | Automatische Retries erzeugen Duplikate, wenn der erste Versuch doch zugestellt hat. Nur 429 wird wiederholt, weil Telegram die Wartezeit liefert. |
| **`_error_detail` in `telegram_api.py`** | War bewusst dokumentiert ("v2.13.0: retry_after mitgeben (wie sender.py)"). Jetzt durch F6 behoben. |

---

## Testabdeckung

690 Tests (Baseline) + 1 neuer Vertragstest. Jeder Befund hat mindestens
einen Test mit dem konkreten Fehlerbild oder dem Messwert.

| Bereich | Tests | Was geprüft wird |
|---|---|---|
| Versions-Gate | 1 | `test_compose_image_tag_matches_version` — Tag muss `__version__` entsprechen |
| Deployment | 43 | `tests/test_deployment.py` — alle Vertragstests grün |
| Gesamt | 690 | Vollständige Suite |

---

## Deployment-Änderungen

- **Solo-Compose umbenannt** (`docker-compose.yml` →
  `docker-compose.standalone.yml`): Ein `docker compose up` im Repo bricht
  mit `no configuration file provided: not found` ab — bewusst. Der
  LAN-Betrieb läuft über `/home/kris/nas-server`, und ein versehentlich
  gestarteter zweiter Stack würde dieselbe Bot-Token-Konfiguration tragen.
- **Dockerfile:** `--create-home` für `appuser` (F5).
- **nas-server-Compose:** `env_file` ergänzt, `TRUSTED_PROXY_HOPS=1`
  erzwungen, Log-Rotation gesetzt (aus früherer Session, in diesem Release
  mit aufgenommen).
