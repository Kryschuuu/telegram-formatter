# Dezentrale Bot-Architektur (BYOB — *Bring Your Own Bot*)

> **Ziel:** Jeder Nutzer kann seinen eigenen Telegram-Bot erstellen, reviewen
> lassen und in einer Session nutzen — ohne dass Nachrichteninhalte,
> Tokens oder Nutzerdaten zentral gespeichert werden.
> **Status:** Design + lauffähiges Referenz-Paket
> `telegram_formatter/botkit/` (eingeführt in v1.3.0; Pfade mit v2.0.0 an die
> Paketstruktur angepasst, 175 Unit-Tests grün). **Seit v2.2.0 ist auch
> Betriebsmodus B umgesetzt** — BYOB-Websessions auf der gehosteten
> Oberfläche (Abschnitt 1.5.1), 297 Unit-Tests grün.

---

## 1. Architektur-Überblick

### 1.1 Status quo vs. Zielbild

| Aspekt | Zentral (`@mdtotxt_bot`) | Dezentral (`botkit`) |
|---|---|---|
| Betreiber des Bots | Fremder Dritter | Nutzer selbst |
| Bot-Token | liegt beim Betreiber | liegt ausschließlich beim Nutzer |
| Nachrichteninhalte | laufen über fremde Infrastruktur | laufen nur über `api.telegram.org` mit dem eigenen Token |
| Datenspeicherung | beim Betreiber (Nachrichten werden gespeichert) | **keine** — RAM-only, endet mit der Session |
| Identität/Absender | immer derselbe Bot | eigener Bot-Name, eigenes Avatar |
| Rate-Limits | geteilt mit allen Nutzern | exklusiv pro Nutzer-Bot |
| Ausfallsicherheit | Single Point of Failure | kein zentraler Dienst nötig |
| Erweiterbarkeit | Feature-Wünsche beim Betreiber | eigener Code, eigenes Review |
| Einstiegshürde | niedrig (einfach hinzufügen) | mittel (BotFather + Token) → durch `botctl`/Wizard gesenkt |

### 1.2 Komponenten

```
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  Nutzer-Ebene (Browser / Terminal)                                        │
 │  ┌──────────────┐   Token-Eingabe    ┌────────────────────────────────┐   │
 │  │ @BotFather   │ ──────────────────▶│ botctl / Web-Wizard            │   │
 │  │ /newbot      │   (nie im Chat!)   │ (Token nie in URL/Log/History) │   │
 │  └──────────────┘                    └───────────────┬────────────────┘   │
 └──────────────────────────────────────────────────────┼───────────────────┘
                                                         │ HTTPS
 ┌───────────────────────────────────────────────────────▼───────────────────┐
 │  botkit (Prozess des Nutzers oder gehosteter Session-Runner)              │
 │                                                                           │
 │  ┌─────────────┐   ┌──────────────┐   ┌─────────────┐   ┌─────────────┐  │
 │  │ tokens.py   │   │ registry.py  │   │ review.py   │   │ session.py  │  │
 │  │ BotToken    │──▶│ Identität +  │◀──│ Statik +    │──▶│ BotSession  │  │
 │  │ Vault (RAM) │   │ Status       │   │ Ledger+Gate │   │ (TTL, Rate) │  │
 │  └─────────────┘   └──────────────┘   └─────────────┘   └──────┬──────┘  │
 │         │                                                       │         │
 │  ┌──────▼───────────────────────────────────────────────────────▼──────┐ │
 │  │ privacy.py — RedactingFilter, audit(), Fingerprints (kein Inhalt!)   │ │
 │  └─────────────────────────────────────────────────────────────────────┘ │
 │  ┌─────────────────────────────────────────────────────────────────────┐ │
 │  │ telegram_api.py — getMe · getUpdates · setWebhook · deleteWebhook     │ │
 │  └─────────────────────────────────────────────────────────────────────┘ │
 └───────────────────────────────────┬───────────────────────────────────────┘
                                     │  bestehende Projekt-Module (unverändert)
                    ┌────────────────▼─────────────────┐
                    │ utils.build_messages → sender.   │
                    │ send_message/sendRichMessage     │
                    └────────────────┬─────────────────┘
                                     ▼
                          api.telegram.org (einzige Gegenstelle)
```

### 1.3 Verantwortlichkeiten

| Modul | Verantwortung | Speichert |
|---|---|---|
| `telegram_formatter/botkit/tokens.py` | `BotToken`-Umschlag, Formatvalidierung, RAM-Vaults mit TTL | Token (RAM, TTL) — oder nichts (`PassthroughTokenVault`) |
| `telegram_formatter/botkit/privacy.py` | Redaction aller Logzeilen, `audit()` (nur Metadaten), Fingerprints, Environment-Scrubbing | nichts |
| `telegram_formatter/botkit/registry.py` | `getMe`-Verifikation, Identität + Status, Chat-ID-Validierung | Identität, Pseudonym-Fingerprint, Freigabe-Prüfsumme (RAM) |
| `telegram_formatter/botkit/review.py` | AST-Regeln BK001–BK012, Checkliste C1–C9, Ledger, Vier-Augen-Gate | Metadaten (Prüfsummen, Entscheidungen) |
| `telegram_formatter/botkit/session.py` | `BotSession`/`SessionManager`: TTL, Leerlauf, Rate-Limit, Versand | Token-Referenz (RAM, bis `close()`) |
| `telegram_formatter/botkit/telegram_api.py` | Die vier erlaubten API-Aufrufe | nichts |
| `telegram_formatter/botctl.py` | CLI: `register`, `review`, `approve`, `verify`, `send`, `checklist` | Audit-Trail-Datei (Metadaten) |

### 1.4 Datenfluss — Registrierung und Session

```
Nutzer                     botkit                          Telegram
  │  Token (Prompt/Env)       │                               │
  ├─ 1 ──────────────────────▶│ BotToken.parse()              │
  │                           │  └─ Formatprüfung             │
  │                           │                               │
  │                           │ getMe(Secret) ──── 2 ────────▶│
  │                           │◀─── {id, username, is_bot} ───┤
  │                           │  └─ Plausibilität: is_bot?    │
  │                           │     id == Token-Präfix?       │
  │                           │                               │
  │                           │ Registry: Identität + Status  │
  │                           │  ✗ kein Secret, keine E-Mail  │
  │◀─ 3 „@mein_bot bereit" ───┤                               │
  │                           │                               │
  │  Bot-Code (PR)            │                               │
  ├─ 4 ──────────────────────▶│ analyze_source() → BK-Regeln  │
  │                           │ Ticket RV-XXXX (sha256)       │
  │                           │ 2 Freigaben (≥1 Maintainer)   │
  │                           │ → mark_approved(bot, sha256)  │
  │                           │                               │
  │  Nachricht senden         │                               │
  ├─ 5 ──────────────────────▶│ SessionManager.open()         │
  │                           │  ├─ Registry: bekannt?        │
  │                           │  ├─ Review: sha256 freigegeben?
  │                           │  └─ BotSession(TTL, Idle)     │
  │                           │ build_messages() → Chunks     │
  │                           │ sendMessage/sendRichMessage ▇─▶│
  │◀─ 6 „2 Chunks gesendet" ──┤ close(): Token-Referenz weg   │
```

### 1.5 Betriebsmodi (Trust-Modell)

| Modus | Wer betreibt | Token-Ablage | Review-Pflicht |
|---|---|---|---|
| **A — Lokal / Self-Host** (`botctl`, eigener Server) | Nutzer | `PassthroughTokenVault` (nur im Session-Objekt) | Selbstreview oder PR (`--local-trust` macht den Verzicht explizit) |
| **B — Gehostete Session** (Web, **seit v2.2.0 implementiert**) | Projekt-Infrastruktur | nur im RAM der `BotSession` (TTL 30 min / Leerlauf 10 min); Handle plus `session_secret` im Request-Body | per Konstruktion erfüllt — es läuft **kein Nutzer-Code** auf dem Server, nur die geprüften Projekt-Module (`require_review=False`); für eigenen Bot-Code bleibt das Gate Pflicht (Modus A/CI) |
| **C — Nur-Konvertierung** (per `telegram_formatter/cli.py`) | Nutzer | Environment | nein (kein Bot nötig) |

Modus B ist der einzige, in dem fremde Infrastruktur das Token sieht. Deshalb
gilt dort: Token wird pro Session nur im RAM gehalten, keine Persistenz, und
`--api-base` erlaubt den Betrieb gegen einen privaten Bot-API-Server.

### 1.5.1 Modus B im Detail — die BYOB-Websessions (v2.2.0)

```
Browser                          Flask (telegram_formatter/app.py)         Telegram
  │  1 Token + chat_id + Consent   │                                        │
  ├─ POST /api/byob/session ──────▶│ BotToken.parse → BotRegistry.register  │
  │                                │  └─ getMe ────────────────────────────▶│
  │                                │ SessionManager.open (RAM, TTL/Idle)    │
  │◀─ 201 {session_id, bot, …} ────┤  (Token NUR im BotSession-Objekt)      │
  │                                │                                        │
  │  2 „Chat-ID erkennen“ (optional)│                                       │
  ├─ POST /api/byob/discover ─────▶│ getUpdates (Blick ohne offset/bestätigt)│
  │◀─ {chats: [{id, type, name}]} ─┤  (nur Metadaten, keine Texte)          │
  │                                │                                        │
  │  3 text senden                 │                                        │
  ├─ POST /api/byob/send ─────────▶│ session.send → build_messages → sender │
  │◀─ {sent, session{Restzeit}} ───┤  └─ sendMessage/sendRichMessage ──────▶│
  │                                │                                        │
  │  4 Ende (Button oder Timeout)  │                                        │
  ├─ POST /api/byob/close ────────▶│ close(): Token-Referenz fällt          │
  └────────────────────────────────┴────────────────────────────────────────┘
```

Sicherheitsentscheidungen (Abweichungen vom ursprünglichen Entwurf sind
begründet):

* **Session-Handle plus `session_secret` im Request-Body statt HttpOnly-Cookie:**
  kein Cookie ⇒ keine Ambient-Authority (CSRF-konstruktiv ausgeschlossen),
  kein Cookie-Flag-Footprint, funktioniert in Third-Party-Kontexten mit
  blockierten Cookies. Handle und zufälliges Session-Secret müssen gemeinsam
  vorgelegt werden; serverseitig wird nur der SHA-256-Digest des Secrets
  gespeichert. Clientseitig leben beide Werte nur im JS-Speicher (kein
  `localStorage`).
* **`InMemoryTokenVault` wird nicht benötigt:** die `BotSession` *ist* die
  flüchtige Ablage — sie hält das Token und verwirft die Referenz bei
  `close()`/TTL/Leerlauf. Ein zusätzlicher Vault wäre reine Redundanz.
* **Review-Gate:** Für die Web-Session existiert kein ausführbarer
  Nutzer-Code — der Server verwendet ausschließlich `utils.build_messages`
  und `sender.send_message` (dieser Code durchläuft das reguläre
  Projekt-Review + CI). `SessionConfig(require_review=False)` dokumentiert
  genau diese Konstruktion; das Gate selbst bleibt für Modus A und die
  CI-Pipeline unverändert Pflicht.
* **Anti-Missbrauch:** IP-Rate-Limits (Öffnen 3/min, Erkennen 3/min, Senden
  6/min), Kappen (100 Sessions insgesamt, 3 pro IP), `reap_expired` +
  Metadaten-Prune vor jedem Öffnen; Schließen gibt den Platz sofort frei.
* **Deployment-Regel:** Sessions leben prozesslokal — der Dienst muss mit
  **einem** Gunicorn-Worker (plus `--threads`) laufen. Ein unbekannter
  Session-Handle antwortet mit einer eindeutigen 410-Meldung statt stiller
  Fehlfunktion.

### 1.6 Unterschied zum zentralen Bot — auf einen Blick

Der zentrale Bot ist ein **Dienst**; `botkit` ist ein **Baukasten**. Statt
„alle Nachrichten an einen fremden Bot senden" gilt: „der Nutzer bringt
Identität und Token mit, die Software stellt nur die Werkzeuge". Damit
verschwinden die drei strukturellen Risiken des Status quo: Datenspeicherung
beim Dritten, geteilte Rate-Limits und fehlende Kontrolle über den Code.

---

## 2. Implementierungs-Approach

### 2.1 Nutzerreise in sechs Schritten

1. **Bot anlegen** — `@BotFather` → `/newbot` → Name + Benutzername → Token.
   *Wichtig:* Das Token gilt lebenslang und ist **nicht** auf Rechte
   eingrenzbar; es ist derGeneralschlüssel für den Bot.
2. **Token sicher übergeben** — interaktiv (`BotToken.from_getpass`, keine
   Shell-Historie) oder als Env-Variable, die sofort nach dem Einlesen via
   `scrub_environment()` entfernt wird. Nie in Chats, Dateien, Screenshots.
3. **Verifizieren** — `botctl register` ruft `getMe`, prüft `is_bot` und
   `id == Token-Präfix`, legt Identität + Status an. **Das Token wird nicht
   gespeichert**, nur sein prozesslokaler Fingerprint.
4. **Bot-Code reviewen lassen** — `botctl review <datei> --bot-id <id>`
   (Statik) → Ticket → zwei Freigaben (Vier-Augen, ≥1 Maintainer:in) mit
   vollständiger Checkliste. Die Freigabe hängt an der SHA-256-Prüfsumme der
   Datei.
5. **Session öffnen** — `botctl send --chat-id … --bot-source <datei> --send`
   (oder `SessionManager.open()` im Code). Die Session prüft Registrierung,
   Freigabe, Chat-ID und Grenzen; danach gehen die Chunks über
   `sender.send_message` raus.
6. **Ende** — Session schließen (Token-Referenz verwerfen), `deleteWebhook`
   mit `drop_pending_updates=True`, keine Offsets, keine Dateien.

### 2.2 Hürden und praktische Lösungen

| # | Hürde | Warum sie entsteht | Lösung in `botkit` |
|---|---|---|---|
| 1 | Token-Leak (Shell-Historie, Chat, `.env` im Repo) | Token ist ein Langzeit-Generalschlüssel | `from_getpass()`, `scrub_environment()`, `BotToken.repr` redacted, `RedactingFilter` auf allen Loggern, gitleaks/CI-Scan, BotFather `/revoke` im Runbook |
| 2 | Token im Speicher/Logs von Bibliotheken | `urllib3` loggt bei DEBUG die komplette URL inkl. Token | `install_privacy_filters()` rüstet Root- und Fremdlogger nach; `audit()` loggt nur Metadaten |
| 3 | Chat-ID unbekannt | Nutzer kennt die numerische ID nicht | `/start` beim eigenen Bot → `getUpdates` liefert `chat.id`; Web-Wizard zeigt sie einmalig an; `validate_chat_id()` verhindert Fehleingaben |
| 4 | Webhook braucht öffentliches HTTPS mit gültigem Zertifikat | Telegram akzeptiert nur TLS | Standardpfad ist **Long-Polling** (`getUpdates`, kein TLS nötig); Webhook nur optional hinter TLS-Terminator mit `secret_token` |
| 5 | „Keine Persistenz" vs. Komfort (Resume, Historie) | Bequemlichkeit erzeugt Speicherpflicht | Bewusst gegen Komfort entschieden: kein Resume, kein Offset-Archiv, `drop_pending_updates=True`; Sessions enden nach TTL/Leerlauf |
| 6 | Telegram-Rate-Limits (429) | ~1 Nachricht/Sekunde pro Chat, ~30/Sekunde global | Gleitendes 60-Sekunden-Fenster in `BotSession` (`max_messages_per_minute`), Backoff im Bot-Loop |
| 7 | Gruppe: Bot „hört" Nachrichten nicht | Privacy Mode ist standardmäßig an | `/setprivacy` → `Disable` beim BotFather (oder Bot als Admin) — im Howto dokumentiert |
| 8 | Review-Aufwand für viele Bots | Manuelle Reviews skalieren schlecht | Automatik zuerst (BK-Regeln + Ruff/Bandit), Mensch prüft nur, was Automatik nicht entscheiden kann; Freigabe an Prüfsumme gebunden |
| 9 | Gehosteter Modus: Token muss den Server erreichen | Server soll senden, ohne zu speichern | `InMemoryTokenVault` mit 15-min-TTL + opakem Handle; strengere Alternative: Token bleibt im Browser und wird je Request mitgesendet (Passthrough) |
| 10 | Nutzer verliert Token / Will Rotation | Kein Passwort-Reset bei Telegram | `botctl register` jederzeit mit neuem Token (`/revoke` beim BotFather), Registrierung ist bewusst flüchtig |

### 2.3 Minimale Integration in bestehenden Code

```python
from telegram_formatter.botkit.privacy import install_privacy_filters
from telegram_formatter.botkit.registry import BotRegistry
from telegram_formatter.botkit.review import ReviewGate, ReviewLedger
from telegram_formatter.botkit.session import SessionConfig, SessionManager
from telegram_formatter.botkit.telegram_api import get_me
from telegram_formatter.botkit.tokens import BotToken

install_privacy_filters()                                  # 1. Logs absichern
registry = BotRegistry(verify=lambda secret: get_me(secret))  # 2. Verifikation
registry.register(BotToken.from_getpass(), owner_ref="alice")

gate = ReviewGate(ReviewLedger.load("audit/reviews.json"))  # 3. Review-Stand
manager = SessionManager(registry=registry, review_gate=gate,
                         config=SessionConfig(require_review=True))

with manager.open(BotToken.from_getpass(), "-1001234567890",
                  source_path="examples/own_bot/minimal_bot.py") as session:
    session.send("**Fett** und $E=mc^2$")                 # 4. Session-Sendeweg
```

Die Konvertierungslogik (`telegram_formatter.utils`) und der Versand
(`telegram_formatter.sender`) bleiben unverändert — `botkit` ergänzt nur
Identität, Grenzen und Review.

---

## 3. Code-Struktur und Best Practices

### 3.1 Modulbaum

```
telegram_formatter/botkit/
├── __init__.py          # Öffentliche Fassade
├── privacy.py           # Redaction, Fingerprints, audit(), scrub_environment()
├── tokens.py            # BotToken, InMemoryTokenVault, PassthroughTokenVault
├── registry.py          # BotIdentity, RegistrationStatus, BotRegistry
├── review.py            # BK-Regeln, Checkliste, ReviewLedger, ReviewGate
├── session.py           # BotSession, SessionManager, SessionConfig
└── telegram_api.py      # getMe, getUpdates, setWebhook, deleteWebhook
telegram_formatter/botctl.py      # CLI-Einstieg (Audit-Trail: audit/reviews.json)
bots/                             # Ablage für eigene Nutzer-Bots (CI-Gate)
examples/own_bot/minimal_bot.py   # Referenz-Bot (besteht alle BK-Regeln)
tests/fixtures/insecure_bot.py    # Negativbeispiel (löst BK001–BK012 aus)
tests/test_{tokens,privacy,registry,review,session}.py
```

Konventionen: Typannotationen überall, deutschsprachige Docstrings (wie im
Rest des Projekts), keine globalen Zustände, Uhren und Sender werden
injiziert (`clock=`, `sender_fn=`) — dadurch sind TTL, Rate-Limit und
Abläufe ohne Netz testbar.

### 3.2 Beispiel 1 — Token-Umschlag (nie ein nackter String)

```python
class BotToken:
    """Umschlag für ein Bot-Token: Klartext nur über reveal()."""

    __slots__ = ("_secret", "_bot_id", "_reveal_count")

    def __init__(self, secret: str) -> None:
        raw = (secret or "").strip()
        if not TOKEN_PATTERN.match(raw):          # Input-Validierung vor Allem
            raise TokenError("Ungültiges Bot-Token-Format …")
        self._secret = raw
        self._bot_id = int(raw.split(":", 1)[0])
        self._reveal_count = 0

    def reveal(self) -> str:      # einziger Ausgang — Review-relevant
        self._reveal_count += 1
        return self._secret

    def __repr__(self) -> str:    # kein Leak über Logs/Tracebacks/f-Strings
        return f"<BotToken bot_id={self._bot_id} fp={self.fingerprint} redacted>"
```

Best Practices dahinter: **Validierung beim Eintritt**, **ein einziger
dokumentierter Ausgang**, **Redaction in der Darstellung**, **Zähler** für
Reviews („warum wird das Token 20-mal je Nachricht geholt?").

### 3.3 Beispiel 2 — Registrierung ohne Token-Speicherung

```python
def register(self, token: BotToken, *, owner_ref: str) -> RegistrationRecord:
    owner = validate_owner_ref(owner_ref)          # Pseudonym, keine E-Mail

    try:
        payload = self._verify(token.reveal())     # getMe — einzige Nutzung
    except Exception as exc:
        audit(LOGGER, logging.WARNING, "registry.verify_failed",
              bot=token.bot_id, error=exc.__class__.__name__)
        raise RegistrationError("Token konnte nicht verifiziert werden …") from None

    result = payload.get("result")
    if not result.get("is_bot"):
        raise RegistrationError("Das Token gehört nicht zu einem Bot.")
    if int(result["id"]) != token.bot_id:          # Token ≠ Identität? Block!
        raise RegistrationError("getMe-ID stimmt nicht mit dem Token überein.")

    record = RegistrationRecord(
        identity=BotIdentity(...),                 # id, username, first_name
        owner_ref=owner,
        owner_fingerprint=fingerprint(owner),      # Pseudonym nur als HMAC
        status=RegistrationStatus.PENDING,
    )
    self._records[record.bot_id] = record          # kein Secret, keine Inhalte
    return record
```

### 3.4 Beispiel 3 — Session mit harten Grenzen

```python
class BotSession:
    def send(self, markdown_text: str) -> list[dict]:
        if not isinstance(markdown_text, str):                     # Typ
            raise SessionError("markdown_text muss ein String sein.")
        if len(markdown_text) > self._config.max_input_chars:      # Länge
            raise SessionError("Eingabe zu lang …")

        messages = build_messages(markdown_text, self._chat_id)    # Format
        if not messages:
            return []

        self._ensure_active()                     # TTL + Leerlauf
        self._reserve_rate_budget(len(messages))  # 60-Sekunden-Fenster
        responses = [self._sender(m, self._require_token().reveal(),
                                  timeout=self._config.timeout,
                                  api_base=self._config.api_base) for m in messages]

        audit(LOGGER, logging.INFO, "session.sent", bot=self.bot_id,
              chat_fp=fingerprint(self._chat_id), chunks=len(messages),
              chars=len(markdown_text))           # Metadaten statt Inhalt
        return responses

    def close(self) -> None:
        self.closed = True
        self._token = None                        # Referenz verwerfen
```

### 3.5 Wie Sicherheit durchgesetzt wird

**a) Keine Datenpersistenz**

* Es existiert **keine** Schreibschnittstelle im Paket: keine Datei, keine DB,
  kein Cache. Der `PassthroughTokenVault` wirft bei `store()` absichtlich.
* Einzige Datei, die entsteht, ist der **Audit-Trail**
  (`audit/reviews.json`) — er enthält ausschließlich Ticket-IDs, Bot-IDs,
  SHA-256-Prüfsummen, Regel-IDs und Entscheidungen.
* Telegram-seitig: `deleteWebhook(drop_pending_updates=True)`, keine
  Offset-Datei, keine Update-Historie im RAM beyond Verarbeitung.
* Erzwingbar im Review: BK002 (Schreibzugriffe) und BK005 (Inhalte im Log)
  sind **Blocker**; Tests prüfen zusätzlich, dass Logausgaben keine Inhalte
  enthalten (`tests/test_session.py::test_session_logs_metadata_only`).

**b) Sicheres Token-Handling**

| Maßnahme | Umsetzung |
|---|---|
| Eingabe ohne Spuren | `BotToken.from_getpass()` (kein Echo, keine Historie) |
| Env nur kurzfristig | `scrub_environment("TELEGRAM_BOT_TOKEN")` direkt nach dem Lesen |
| Kein versehentliches Loggen | `BotToken.__repr__` redacted + `RedactingFilter` auf Root/urllib3/requests |
| Keine Persistenz | nur RAM-Vault mit TTL oder Passthrough |
| Kein `os.environ`-Erbe | Scrubbing vor allen Subprozessen/Reloads |
| Rotation | `/revoke` bei BotFather + erneutes `botctl register` |
| Nachweis im Review | `reveal_count`, C1 explizit als Checkpoint |

**c) Input-Validierung** (Verteidigung an jeder Grenze)

| Eingabe | Prüfung |
|---|---|
| Bot-Token | Regex `^\d{5,16}:[A-Za-z0-9_-]{35}$` + `getMe`-Gegenprobe (id/is_bot) |
| `chat_id` | `^-?\d{1,32}$` — verhindert Injection in den API-Payload |
| `owner_ref` | Pseudonym-Whitelist (keine E-Mail, kein Klarname) |
| Nachrichtentext | Typ ist `str`, Länge ≤ `max_input_chars` (Standard 100 000) |
| Update-JSON | Typ- und Präsenzprüfung je Feld, unbekannte Updates werden ignoriert |
| Ziel-URLs | statisch prüfbar und ausschließlich `api.telegram.org` |

---

## 4. Peer-Review-Mechanismus

### 4.1 Prozess (vier Stufen, jede Stufe kann stoppen)

```
 PR eröffnet                 Automatik                 Mensch              Deployment
 ───────────                ─────────                 ──────              ──────────
 Bot-Code ──▶ Ruff/Bandit/pip-audit ──▶ botctl review ──▶ 2 Approvals ──▶ botctl verify
              gitleaks (Secrets)        (BK-Regeln)        (C1–C9)          (CI-Gate)
                    │                        │                 │                 │
                    └── fail ──┘             └── fail ──┘       └── fail ──┘      └── fail ──┘
                        PR rot                 PR rot            PR rot             kein Deploy
```

1. **Automatik (CI):** Ruff, Bandit, `pip-audit`, Secret-Scan, `pytest`.
2. **Statik des Bot-Codes:** `botctl review` → BK-Regeln (siehe 4.2).
3. **Mensch:** zwei Freigaben, mindestens eine von Maintainer:in, mit
   vollständiger Checkliste (C1–C9, siehe 4.3). CODEOWNERS erzwingt die
   Maintainer-Beteiligung.
4. **Binding:** Freigabe gilt nur für die SHA-256-Prüfsumme der Datei.
   Jede Änderung ⇒ neues Ticket. `botctl verify` ist das Tor im CI.

### 4.2 Automatische Sicherheitsprüfungen

| Regel | Findet | Schwere |
|---|---|---|
| BK001 | Importe mit Persistenz/Shell/Socket (`sqlite3`, `pickle`, `subprocess`, …) | Blocker |
| BK002 | Schreibzugriffe (`open(…,'w')`, `write_text`, `json.dump`, `sqlite3.connect`, `os.open(…, O_WRONLY\|O_CREAT)`, `os.write`) | Blocker |
| BK003 | Dynamische Ausführung (`eval`, `exec`, `pickle.loads`, `getattr(__builtins__, "eval")`) | Blocker |
| BK004 | Ausgehende HTTP-Aufrufe an andere Hosts als `api.telegram.org` | Blocker |
| BK005 | Nachrichteninhalte in Log-Aufrufen | Blocker |
| BK006 | Hartkodierte Tokens/Secrets | Blocker |
| BK007 | Shell-/Prozessausführung (`os.system`, `getattr(os, "system")`) | Blocker |
| BK008 | Eigener Socket-Server (Webhook ohne geprüften TLS-Terminator) | Blocker |
| BK010 | Nicht statisch prüfbare Ziel-URL (seit v2.4.0) | Blocker |
| BK011 | `print()` von Inhalten | Warnung |
| BK012 | `random` statt `secrets` | Warnung |

Gezielte Ausnahmen sind möglich, aber **sichtbar**:
`requests.post(url)  # botkit:allow BK010` (Regel-ID + Begründung im Code).
Suppressions für Blocker brauchen die Zustimmung beider Reviewer:innen.

### 4.3 Checkliste für Reviewer:innen (C1–C9, Pflicht bei Freigabe)

| ID | Frage | Warum |
|---|---|---|
| C1 | Token nur über `BotToken`/Environment, nie geloggt oder gespeichert? | Häufigster Leak-Weg |
| C2 | Kein Schreibzugriff auf Dateisystem, Datenbank oder Cache? | Kernanforderung |
| C3 | Alle Eingaben validiert (Typ, Länge, Format, `chat_id`, Callback-Daten)? | Ungeprüfte Eingaben landen im API-Payload |
| C4 | Nur erlaubte Telegram-Methoden, keine Fremd-APIs? | Datenabfluss |
| C5 | Fehler klassifiziert geloggt — ohne Inhalte, Token oder `chat_id`? | Zweithäufigster Leak-Weg |
| C6 | Rate-Limits/429 mit Backoff, Retry-Budget begrenzt? | Bot-Sperren, Endlosschleifen |
| C7 | Session-Ende räumt auf (`deleteWebhook`, `drop_pending_updates`, kein Offset)? | Kein Zustand bei Telegram |
| C8 | Keine neuen Abhängigkeiten ohne Begründung, Versionen gepinnt? | Supply Chain |
| C9 | Tests für Konvertierung, Fehlerpfad und „keine Persistenz"? | Regressionen |

### 4.4 Worauf Reviewer:innen konkret achten sollten

* **Token-Fluss:** Wo kommt das Token her, wo geht es hin? `reveal()`-Aufrufe
  zählen — mehr als einer pro API-Aufruf ist verdächtig.
* **„Harmlose" Ausgaben:** `print(text)`, `logging.info(..., update)`,
  Debug-Pfade, Exception-Meldungen mit `response.text`.
* **Persistenz-Umwege:** `tempfile`, `shelve`, `.cache`-Pfade, `pickle` in
  `try/except`, ORM-Modelle, `to_csv` „nur fürs Debugging".
* **Exfiltrationswege:** Webhook-URLs auf fremde Domains, Analytics,
  Sentry/Telemetrie mit Inhalten, `requests` mit dynamischer URL.
* **Eingaben aus dem Netz:** `update["message"]["text"]`, Callback-Daten,
  Datei-Uploads — Typ und Länge prüfen, niemals f-stringen.
* **Fehlerbehandlung:** Keine Endlos-Retries, keine Geheimnisse in Meldungen,
  Unterscheidung Netzwerk/API/Validierung.
* **Abhängigkeiten:** neue Pakete brauchen Begründung + gepinnte Version.
* **Tests:** Gibt es einen Test, der das Gegenteil beweist („Inhalt landet
  nicht im Log")?

### 4.5 Automatisierung im CI

`.github/workflows/bot-review.yml` führt aus: Ruff, Bandit, `pip-audit`
(Laufzeit **und** Entwicklung), `pytest`, Secret-Scan (gitleaks) sowie
`botctl review --check` und `botctl verify` für jede geänderte Bot-Datei.
Ergänzt wird das durch einen wöchentlichen Terminlauf, damit neue CVEs auch
ohne Pull Request auffallen. Branch-Protection verlangt: grünes CI,
≥1 Maintainer-Approval, aktueller Stand, keine Force-Pushes.

> **Erfahrungswert aus dem ersten CI-Lauf:** zwei Audit-Klassen schlugen an —
> eine veraltete Abhängigkeit (`requests` 2.32.4, `PYSEC-2026-2275`) und ein
> Dummy-Token in der Dokumentation (gitleaks). Beides ist behoben; die
> Freigaben für das absichtlich unsichere Negativbeispiel liegen in
> `.gitleaks.toml`, Dokumentations-Tokens sind durch Platzhalter ersetzt.

---

## 5. Optimierungen gegenüber dem Status quo

| Dimension | Verbesserung durch Dezentralisierung |
|---|---|
| **Datenschutz** | Inhalte laufen nur zwischen Nutzer und Telegram; im gehosteten Modus gibt es keine Persistenzschicht. Logs enthalten ausschließlich Metadaten (`len=`, `fp=`). |
| **Kontrolle** | Nutzer besitzt Token und Code; Rotation (`/revoke`) und Löschung liegen bei ihm, nicht beim Betreiber. |
| **Transparenz** | Der Code jedes Bots ist reviewbar (BK-Regeln + Checkliste); der zentrale Bot ist eine Blackbox. |
| **Verfügbarkeit** | Kein Single Point of Failure; Ausfall eines fremden Dienstes betrifft den Nutzer nicht. |
| **Skalierung** | Rate-Limits und Quoten pro eigenem Bot statt geteilt mit allen Nutzern. |
| **Identität** | Eigener Bot-Name/Avatar im Ziel-Chat statt eines fremden Absenders — relevant für Kanäle und Marken. |
| **Erweiterbarkeit** | Eigene Befehle, Filter, Zielchats; kein Feature-Ticket beim Betreiber. |
| **Compliance** | Keine Datenverarbeitung durch Dritte → einfacher für Teams mit DSGVO/ interne Richtlinien. |
| **Kosten** | Betrieb der Bot-Runner verteilt sich; kein zentraler Speicher/Betrieb nötig. |

**Grenzen (ehrlich benannt):** Der Einstieg ist technischer als „Bot
hinzufügen" (BotFather + Token + evtl. Chat-ID). Dafür gibt es `botctl`,
den Referenz-Bot und den Wizard-Pfad. Für Empfangs-Bots bleibt der
Betriebsaufwand (Prozess oder Webhook mit TLS) beim Nutzer — das ist der
Preis echter Dezentralisierung.

---

## 6. Abnahmekriterien und nächste Schritte

**Abnahme (heute erfüllt):**

- [x] `pytest -q` → 175 Tests grün (81 bestehend + 94 neu)
- [x] Referenz-Bot `examples/own_bot/minimal_bot.py` besteht alle BK-Regeln
- [x] Negativbeispiel löst BK001–BK012 aus (Regeltest)
- [x] Registrierung speichert kein Token (Test: `test_registry_never_stores_the_secret`)
- [x] Session-Logs enthalten keine Inhalte (Test: `test_session_logs_metadata_only`)
- [x] Vier-Augen-Prinzip: gleiche Person zählt nicht doppelt, Maintainer-Pflicht
- [x] Freigabe ist an die Code-Prüfsumme gebunden
- [x] End-to-End: `register → review → 2× approve → verify → send`

**Nächste Schritte:**

1. ~~Web-Wizard (Modus B)~~ — **umgesetzt in v2.2.0** als BYOB-Websessions
   (Abschnitt 1.5.1): Token-Eingabe im Browser, ephemere RAM-Session,
   Handle im Request-Body (statt Cookie, siehe Begründung dort),
   Integration in `telegram_formatter/app.py` + `static/js/byob.js`.
2. Signierte Review-Entscheidungen (GPG/Sigstore), damit der Audit-Trail
   manipulationssicher wird.
3. Sandbox-Laufzeit für User-Bots im gehosteten Modus (Container mit
   Netzwerk-Allowlist auf `api.telegram.org`).
4. Katalog: reviewte Bot-Vorlagen mit Prüfsumme und Freigabestatus.
