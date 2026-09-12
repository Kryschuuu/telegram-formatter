# Review: BYOB-Websessions (Modus B) + Ganzcode-Review v2.2.0

- **Datum:** 2026-09-12
- **Reviewende:** Arena-Agent (Pair: Kryschuuu), Rollen: Maintainer-Review + Kern-Code
- **Gegenstand:** Branch `arena/01a09542-telegram-formatter` (gegen `main` @ `a917c62`),
  Bereich: `telegram_formatter/app.py`, `botkit/session.py`, `static/js/byob.js` (neu),
  `templates/index.html`, `static/css/*`, `pyproject.toml`, Doku & Tests
- **Ergebnis:** ✔ freigegeben (297 Tests grün, Ruff/Bandit sauber)

## Kontext

Umsetzung des dezentralen Bot-Konzepts (BYOB) auf der Website: Der geteilte
Bot (`@mdtotxt_bot`) sendet in einen gemeinsamen, öffentlich einsehbaren
Chat — Nutzer brauchen einen privaten Versandweg über den eigenen Bot in
einer ephemeren Session (botkit-Betriebsmodus B, bislang als „nicht
implementiert“ geführt). Vor der Implementierung wurde der Gesamtcode einem
Peer-Review unterzogen; alle Blocker sind mit diesem Release behoben.

## Befunde (Ganzcode-Review, vor/new in diesem PR)

### [BLOCKER] Wheel ohne UI-Assets ausgeliefert

- **Fund:** `pyproject.toml` — `[tool.setuptools.package-data]` mit den
  Globs `static/*.css`/`static/*.js`
- **Kategorie:** correctness / packaging
- **Begründung:** Die Assets liegen in `static/css/` und `static/js/`;
  `static/*.css` matcht nur *direkte* Kinder. `pip install telegram-formatter`
  lieferte ein Paket ohne alle Styles, Skripte und Favicon — Web-Betrieb aus
  dem Wheel war ungestylt und ohne Funktion (nur der Repo-Checkout funktionierte).
- **Fix:** Globs korrigiert (`static/css/*.css`, `static/js/*.js`,
  `static/favicon.svg`); neuer Vertragstest
  `tests/test_frontend.py::test_package_data_covers_all_assets` vergleicht
  die Globs gegen den tatsächlichen Dateibestand; Wheel-Bau verifiziert
  (9 Assets enthalten).

### [BLOCKER] IP-Rate-Limits hinter Plattform-Proxy wirkungslos

- **Fund:** `telegram_formatter/app.py` — `_rate_limited()` keyed auf
  `request.remote_addr`
- **Kategorie:** security / availability
- **Begründung:** Hinter dem Render-Proxy ist `remote_addr` die Proxy-Adresse:
  **alle Besucher** teilten sich einen Rate-Limit-Eimer (`/api/convert`
  60/min global!) — ein einzeler Nutzer konnte die Instanz für alle
  drosseln. Die neuen BYOB-Per-IP-Kappen wären ohne Fix zur Global-Kappe
  kollabiert.
- **Fix:** `ProxyFix(app.wsgi_app, x_for=1, x_proto=1)` — vertraut genau
  einer Proxy-Ebene (Plattform-Topologie). Trade-off dokumentiert: XFF ist
  spoofbar ⇒ Rate-Limits bleiben Missbrauchs-Heuristik, keine
  Authentifizierungsschicht (die Guards Origin/Auth-Token bleiben
  unabhängig davon wirksam).

### [WARNING] BYOB-Sessions sind prozesslokal (Multi-Worker-Falle)

- **Fund:** `telegram_formatter/app.py` — `_ByobRuntime` hält Registry +
  SessionManager im Prozess-RAM
- **Kategorie:** correctness / operations
- **Begründung:** Mit `--workers N > 1` ohne Sticky-Routing landet
  `/api/byob/send` auf einem anderen Worker als der Session-Start ⇒ Handle
  unbekannt. Kein Datenverlust (Token bleibt im öffnenden Prozess bis TTL),
  aber Verwirrung.
- **Fix:** Kanonischer Start auf **einen** Worker + `--threads 8` umgestellt
  (`render.yaml`, `docs/DEPLOYMENT.md`, Modul-Docstring); unbekannte Handles
  antworten mit eindeutiger 410-Meldung („bitte erneut öffnen“) statt
  stiller Fehlfunktion.

### [WARNING] Review-Gate im Web-Modus — Konstruktion statt Ausnahme

- **Fund:** `_build_byob()` nutzt `SessionConfig(require_review=False)`
- **Kategorie:** security
- **Begründung:** Könnte als Umgehung des Review-Gates gelesen werden.
- **Fix (Abwägung, dokumentiert):** In der Web-Session läuft **kein
  Nutzer-Code** — der Server verwendet ausschließlich die geprüften
  Projekt-Module (`utils.build_messages`, `sender.send_message`; Projekt-
  Review + CI). Es gibt schlicht nichts Ungeprüftes zur Ausführung; das
  Gate bleibt für Modus A (`botctl`) und die CI-Pipeline Pflicht.
  Begründung im Code-Docstring, in
  `docs/DECENTRAL_BOT_ARCHITECTURE.md` §1.5.1 und im UI-FAQ verankert.

### [INFO] Verwaiste Changelog-Blöcke & stale Versionsangabe

- **Fund:** `CHANGELOG.md` (zwei `[Unreleased]`-Blöcke, deren Inhalt längst
  auf main deployt war), `README.md` („Aktuelle Version: 2.0.0“ bei
  tatsächlich 2.1.0)
- **Kategorie:** maintainability
- **Fix:** Beide Blöcke der Version 2.1.0 zugeordnet (in der sie deployt
  wurden), frischer `[2.2.0]`-Eintrag; Versionsangabe korrigiert.

### Geprüft und sauber befunden (Auszug)

- `sender.py`: Fehlermeldungen frei von Token/URL (K-1-Konstruktion) — auch
  die neuen BYOB-Fehlerpfade erben dies (Tests bestätigen Token-Freiheit).
- `botkit/session.py`: Thread-Safety (Locks), Token-Verwurf bei
  `close()`/TTL, Rate-Limit-Fenster — Basis der Web-Sessions, unverändert
  übernommen; nur zwei neue read-only Properties ergänzt.
- `app.py` Guards: Origin-Check, optionales `X-Auth-Token`, Body-Limit und
  CSP greifen für **alle** neuen Endpunkte (Tests: 403/401/400/413-Pfade).
- `getUpdates`-Chat-Erkennung: reiner Blick ohne `offset` (nichts wird
  bestätigt/verbraucht); Antwort enthält nachweislich keine Nachrichtentexte
  (`test_discover_returns_chat_metadata_only`).
- Kein Token in irgendeiner JSON-Antwort; Session-Handle nur im Request-Body
  (kein Cookie ⇒ kein CSRF-Vektor, kein `localStorage` clientseitig).

## Angenommen / abgewogen

- **XFF-Spoofing** kann Per-IP-Limits aufblähen — akzeptiert (s. o.); harte
  Totalkappe (100 Sessions) und Telegram-eigene Limits begrenzen den Schaden.
- **Kein Sign-in/Account-System** für BYOB-Sessions — bewusst: Ephemeralität
  ist das Datenschutzversprechen; Kappe + TTL sind der Ersatz für Identität.
- **Session-Fortsetzung nach Seiten-Reload** (Handle nur im JS-Speicher) —
  bewusst nicht eingebaut: Persistenz im Browser widerspricht dem
  RAM-only-Modell; UI erklärt den Ablauf.

## Follow-ups

- [ ] Signierte Review-Entscheidungen (GPG/Sigstore) für den Audit-Trail
      (übernommen aus der vormaligen Roadmap).
- [ ] Beobachten: Rate-Limit-Trefferverhalten hinter dem Proxy nach dem
      ProxyFix-Rollout (Metriken vergleichen).
