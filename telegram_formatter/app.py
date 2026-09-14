"""
telegram_formatter/app.py
=========================
Kleine Flask-Weboberfläche, die die Konvertierung aus :mod:`telegram_formatter.utils`
demonstriert: links Markdown/LaTeX eingeben, rechts die gebaute Telegram-
Nachricht (HTML-Payload für ``sendMessage`` bzw. Rich-Markdown-Payload für
``sendRichMessage``) betrachten. Optional kann direkt gesendet werden.

Die eigentliche Logik liegt in ``telegram_formatter/utils``/``telegram_formatter/sender``; dieses Modul ist
eine dünne HTTP-Schicht darüber — die aber die Sicherheitsgrenze des
gehosteten Dienstes bildet.

Zwei Versand-Wege (seit v2.2.0):

* **Geteilter Bot** (``/api/send``): sendet über den zentral konfigurierten
  Bot in den gepinnten ``TELEGRAM_CHAT_ID``. Auf einer öffentlichen Instanz
  ist das ein **öffentlicher, gemeinsamer Chat** (public Supergroup/Channel) —
  jeder Besucher sieht alle bisher gesendeten Nachrichten und jeder, der die
  Gruppe/den Kanal öffnet, kann den gesamten Verlauf lesen. Die Oberfläche
  warnt entsprechend; für private Inhalte ist BYOB der empfohlene Weg.
  Zwei Zugangsarten (seit 2.6.0):

  - **Browser-Versand** (``TELEGRAM_FORMATTER_SHARED_WEB_SEND``, Standard
    ``1``): nur wirksam, wenn *beide* Bedingungen gelten — Bot-Token gesetzt
    **und** Zielchat gepinnt. Anonyme Aufrufe brauchen zusätzlich
    ``"confirm_public": true`` im Body (die UI holt diese Bestätigung im
    Sende-Dialog ein) und unterliegen engeren Grenzen
    (``SHARED_WEB_SENDS_PER_MINUTE`` pro IP, …_TOTAL instanzweit,
    ``SHARED_WEB_MAX_INPUT_CHARS``).
  - **API-only** (Operator-Token via ``X-Auth-Token``): volle Textlänge,
    ``SENDS_PER_MINUTE``. Mit ``…_SHARED_WEB_SEND=0`` ist der Endpunkt für den
    Browser wieder komplett geschlossen (Zustand bis 2.5.0).
* **Eigener Bot — BYOB** (``/api/byob/*``): Nutzende registrieren ihr eigenes
  Bot-Token, der Server öffnet darüber eine **ephemere Session** (Botkit,
  Betriebsmodus B aus ``docs/DECENTRAL_BOT_ARCHITECTURE.md``). Das Token
  liegt ausschließlich im RAM der Session (TTL/Leerlauf-Timeout, Rate-Limit
  pro Session) und wird beim Ende verworfen — keine Datei, keine Datenbank,
  kein Log, kein Cookie. Der Session-Handle wandert als opaker Zufallswert
  in den Request-Body zurück und wieder mit (bewusst *kein* Cookie: keine
  Ambient-Authority, kein CSRF-Risiko, funktioniert auch in Kontexten mit
  blockierten Third-Party-Cookies). Es läuft **kein Nutzer-Code** auf dem
  Server — deshalb gilt das Review-Gate hier per Konstruktion als erfüllt
  (``require_review=False``); es bleibt Pflicht für eigenen Bot-Code über
  ``botctl``/CI.

Härtungen (Security-Audit 2026-09, Befunde K-1/K-2/H-2/H-5/M-6/B-5/B-6):

* **Chat-Pinning:** ist ``TELEGRAM_CHAT_ID`` gesetzt, akzeptiert ``/api/send``
  ausschließlich diesen Zielchat; ein abweichender ``chat_id``-Wert im Request
  wird mit 400 abgelehnt. Ohne konfigurierten Chat (reiner Selbstbetrieb)
  muss der Body eine gültige, numerische ``chat_id`` enthalten — niemals
  beliebige JSON-Werte. Damit ist der Endpunkt **kein offener Relay** mehr.
* **Fail-Closed im Selbstbetrieb (R-1):** Läuft ``/api/send`` *ohne*
  ``TELEGRAM_CHAT_ID``, ist der Zugangsschutz Pflicht — fehlt auch
  ``TELEGRAM_FORMATTER_API_TOKEN``, antwortet der Endpunkt mit 503 statt
  anonym beliebige Chats zu beliefern.
* **Shared-Versand nur über zwei explizite Zugänge:** ``/api/send`` ist
  deaktiviert (503), wenn weder der Operator-Token
  (``TELEGRAM_FORMATTER_API_TOKEN`` + ``X-Auth-Token``) noch der
  Browser-Versand (``TELEGRAM_FORMATTER_SHARED_WEB_SEND`` bei gepinntem
  Zielchat) freigeschaltet ist. Der Browser erhält das Operator-Secret nie —
  anonym ist ausschließlich die *gepinnte* Demo-Spur möglich, nie ein
  Fremd-Chat.
* **Optionaler API-Token für übrige POSTs:** ist
  ``TELEGRAM_FORMATTER_API_TOKEN`` gesetzt, verlangen alle POST-Endpunkte einen
  passenden ``X-Auth-Token``-Header (zeitkonstanter Vergleich).
* **Größen- & Mengengrenzen:** Request-Body hart auf ``MAX_BODY_BYTES``
  begrenzt (413 statt OOM), Text auf ``MAX_INPUT_CHARS`` (wie
  ``botkit.SessionConfig``), pro IP ein einfaches Frequenzlimit für
  ``/api/send`` (Standard 6/min) und ``/api/convert`` (Standard 60/min)
  sowie separate Limits für die BYOB-Endpunkte (Öffnen/Erkennen/Senden).
* **BYOB-Anti-Missbrauch:** harte Kappen für aktive Sessions (insgesamt
  ``BYOB_MAX_SESSIONS_TOTAL``, pro Absender ``BYOB_MAX_SESSIONS_PER_IP``),
  ``reap_expired`` vor jedem Öffnen, Chat-ID-Validierung über
  ``botkit.registry.validate_chat_id``.
* **Origin-Check:** POSTs mit fremdem ``Origin``-Header werden abgewiesen.
* **Sicherheits-Header:** CSP strikt ``'self'`` (seit dem UI-Redesign keine
  CDN-Whitelists mehr, kein ``unsafe-inline``), ``nosniff``, ``no-referrer``,
  ``DENY`` für Frames.
* **Kein Upstream-Detail-Leak:** Fehler antworten mit kurter Meldung;
  ``SendError``-Meldungen enthalten per Konstruktionsregel (Modul ``sender``)
  weder Token noch URL. Zusätzlich installiert die App die
  Privacy-Redaction der botkit-Schicht auf den Root-Logger.

Betriebshinweis: BYOB-Sessions leben **pro Prozess** im RAM. Der Start-Befehl
muss deshalb mit genau einem Gunicorn-Worker (plus ``--threads``) laufen —
siehe ``render.yaml`` und ``docs/DEPLOYMENT.md``.

Starten::

    flask --app telegram_formatter.app run        # Entwicklung
    gunicorn "telegram_formatter.app:app" --threads 8   # Produktion (BYOB-tauglich: 1 Prozess)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from telegram_formatter import __version__
from telegram_formatter.botkit.registry import (
    CHAT_ID_PATTERN,
    BotRegistry,
    RegistrationError,
)
from telegram_formatter.botkit.session import (
    RateLimitExceeded,
    SessionConfig,
    SessionError,
    SessionExpired,
    SessionManager,
)
from telegram_formatter.botkit.telegram_api import TelegramAPIError, get_me, get_updates
from telegram_formatter.botkit.tokens import BotToken, TokenError
from telegram_formatter.sender import SendError, send_message
from telegram_formatter.utils import build_messages

app = Flask(__name__)
# Plattform-Proxys (Render & Co.) liefern die Client-IP im ``X-Forwarded-For``-
# Header; ohne ProxyFix wäre ``request.remote_addr`` für ALLE Besucher die
# Proxy-Adresse und die IP-Rate-Limits (convert/send/BYOB) fielen auf einen
# gemeinsamen Eimer zusammen. ``x_for=1`` vertraut genau einer Proxy-Ebene —
# der Standard-Topologie der Hosting-Plattform. Die Rate-Limits sind
# Missbrauchs-Heuristik (keine Authentifizierung); ein gefälschter
# Forward-Header kann sie aufblähen, aber keine Sicherheitsgrenze überwinden.
# Direct deployments must not trust a client-controlled forwarding header.
# Proxy deployments set the exact trusted hop count explicitly.
TRUSTED_PROXY_HOPS = int(os.environ.get("TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS", "0"))
if TRUSTED_PROXY_HOPS > 0:
    app.wsgi_app = ProxyFix(  # type: ignore[method-assign]
        app.wsgi_app, x_for=TRUSTED_PROXY_HOPS, x_proto=TRUSTED_PROXY_HOPS
    )
LOGGER = logging.getLogger("telegram_formatter.app")

# --- harte Grenzen ----------------------------------------------------------
#: Maximale Request-Körpergröße in Bytes (413 darüber). Kein Flask-Default!
MAX_BODY_BYTES = 512 * 1024
#: Flask-Layer: body-Größe **vor** dem Parsen begrenzen (Audit H-2 — ohne
#: dieses Limit liest Flask unbegrenzt in den RAM).
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
#: Maximale Textlänge je Konvertierung/Versand (bewusst wie SessionConfig).
MAX_INPUT_CHARS = int(os.environ.get("TELEGRAM_FORMATTER_MAX_INPUT_CHARS", "100000"))
#: Sendungen pro Minute und IP (Telegram-Limits + DoS-Schutz für die Instanz).
SENDS_PER_MINUTE = int(os.environ.get("TELEGRAM_FORMATTER_SENDS_PER_MINUTE", "6"))
#: Konvertierungen pro Minute und IP (Live-Preview mit Debounce braucht Luft).
CONVERTS_PER_MINUTE = int(os.environ.get("TELEGRAM_FORMATTER_CONVERTS_PER_MINUTE", "60"))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
#: Optionaler Zugangsschutz für den gehosteten Betrieb (POSTs brauchen den
#: Header ``X-Auth-Token``). Für rein lokalen Gebrauch kann er leer bleiben —
#: AUSSER im Selbstbetrieb (BOT_TOKEN ohne CHAT_ID): dort ist er Pflicht,
#: sonst wäre ``/api/send`` ein offener Relay (R-1, Fail-Closed).
API_TOKEN = os.environ.get("TELEGRAM_FORMATTER_API_TOKEN", "")


def _env_flag(name: str, default: bool = True) -> bool:
    """Liest ein Boolean-Flag aus der Umgebung (``1/true/yes/on`` ⇒ wahr)."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


# --- Shared-Versand im Browser (seit 2.6.0 explizit konfigurierbar) ---------- #
#: Darf der **Browser** den geteilten Bot nutzen (``POST /api/send`` ohne
#: Operator-Token)? Wirksam ist das Opt-in nur zusammen mit einem gepinnten
#: Zielchat — siehe :func:`_shared_web_send_available`. Ein Betreiber, der
#: seine Instanz ausschließlich für BYOB öffnen will, setzt ``0`` und
#: schließt damit den anonymen Versand komplett (Zustand bis 2.5.0).
SHARED_WEB_SEND = _env_flag("TELEGRAM_FORMATTER_SHARED_WEB_SEND", True)
#: Anonyme Browser-Sendungen pro Minute und IP. Bewusst enger als
#: ``SENDS_PER_MINUTE`` für authentifizierte API-Aufrufe: der geteilte Chat
#: ist öffentlich, also ist Spam hier der Hauptfall, den es zu dämpfen gilt.
SHARED_WEB_SENDS_PER_MINUTE = int(
    os.environ.get("TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE", "4")
)
#: Anonyme Browser-Sendungen pro Minute **instanzweit** (alle IPs zusammen).
#: Bremst Flash-artige Last von vielen Adressen, bevor Telegram den geteilten
#: Bot wegen Rate-Limits drosselt oder sperrt.
SHARED_WEB_SENDS_PER_MINUTE_TOTAL = int(
    os.environ.get("TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE_TOTAL", "30")
)
#: Längenkappe für anonyme Browser-Sendungen (authentizierte Aufrufe behalten
#: ``MAX_INPUT_CHARS``). Ein geteilter Chat soll keine 100k-Zeichen-Wände
#: bekommen — lange Texte gehören in den privaten BYOB-Weg.
SHARED_WEB_MAX_INPUT_CHARS = int(
    os.environ.get("TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS", "8000")
)
#: Fehlerantwort, wenn kein Shared-Zugang offen ist (weder API noch Browser).
SHARED_SEND_DISABLED = (
    "Shared-Versand deaktiviert: nötig ist entweder der Operator-Token "
    "(TELEGRAM_FORMATTER_API_TOKEN + Header X-Auth-Token) oder der "
    "Browser-Versand (TELEGRAM_FORMATTER_SHARED_WEB_SEND=1 zusammen mit "
    "gepinntem TELEGRAM_CHAT_ID)."
)


def _shared_web_send_available() -> bool:
    """
    Ist der geteilte Bot im Browser benutzbar?

    Drei Bedingungen müssen *gleichzeitig* gelten — sonst bleibt der Endpunkt
    fail-closed (503):

    1. ``TELEGRAM_BOT_TOKEN`` ist gesetzt (es existiert ein geteilter Bot),
    2. ``TELEGRAM_CHAT_ID`` ist gepinnt (Senden kann nur in *den einen*
       Betreiber-Chat — die Bedingung, die K-2 geschlossen hält: ohne Pinning
       wäre der Endpunkt ein offener Relay für beliebige Fremd-Chats),
    3. ``TELEGRAM_FORMATTER_SHARED_WEB_SEND`` ist nicht abgeschaltet.
    """
    return bool(SHARED_WEB_SEND and BOT_TOKEN and CHAT_ID)


# Fail-Closed-Hinweis beim Start: BOT_TOKEN ohne CHAT_ID und ohne API_TOKEN
# würde den Versand anonymisieren — genau das verbietet R-1. Der
# Browser-Versand greift hier nicht, er verlangt den gepinnten Zielchat.
if BOT_TOKEN and not CHAT_ID and not API_TOKEN:
    LOGGER.warning(
        "app.selfhost_unprotected: TELEGRAM_BOT_TOKEN ist gesetzt, aber weder "
        "TELEGRAM_CHAT_ID noch TELEGRAM_FORMATTER_API_TOKEN — /api/send ist "
        "deaktiviert (503, Fail-Closed)."
    )


# --- BYOB: Eigene Bots in ephemeren Web-Sessions (botkit, Modus B) ---------- #
#: BYOB-Websessions aktiv? (Abschaltbar für Betreiber, die keine fremden
#: Tokens über ihre Instanz relayen wollen.)
BYOB_ENABLED = _env_flag("TELEGRAM_FORMATTER_BYOB_ENABLED", True)
#: Session-Öffnungen (getMe-Verifikation) pro Minute und IP.
BYOB_SESSIONS_PER_MINUTE = int(
    os.environ.get("TELEGRAM_FORMATTER_BYOB_SESSIONS_PER_MINUTE", "3")
)
#: Chat-ID-Erkennungen (getUpdates) pro Minute und IP.
BYOB_DISCOVER_PER_MINUTE = int(
    os.environ.get("TELEGRAM_FORMATTER_BYOB_DISCOVER_PER_MINUTE", "3")
)
#: Sendungen über BYOB-Sessions pro Minute und IP (zusätzlich zum
#: Session-eigenen Limit von ``max_messages_per_minute``).
BYOB_SENDS_PER_MINUTE = int(os.environ.get("TELEGRAM_FORMATTER_BYOB_SENDS_PER_MINUTE", "6"))
#: Harte Lebensdauer einer BYOB-Web-Session (Sekunden) — entspricht
#: ``SessionConfig.ttl_seconds``; danach ist das Token verworfen.
BYOB_TTL_SECONDS = float(os.environ.get("TELEGRAM_FORMATTER_BYOB_TTL_SECONDS", "1800"))
#: Leerlauf-Timeout einer BYOB-Web-Session (Sekunden).
BYOB_IDLE_SECONDS = float(os.environ.get("TELEGRAM_FORMATTER_BYOB_IDLE_SECONDS", "600"))
#: Anzeige-Name des geteilten Bots für die Privatsphäre-Warnung im UI.
SHARED_BOT_HANDLE = (
    os.environ.get("TELEGRAM_FORMATTER_SHARED_BOT_HANDLE", "@mdtotxt_bot")
    or "@mdtotxt_bot"
).strip()

#: RAM-Schutz gegen Session-Flooding: Obergrenzen aktiver Sessions insgesamt
#: bzw. pro Absender-IP. Bewusst Konstanten (kein ENV): sie schützen den
#: Prozess, nicht die Produktpolitik.
BYOB_MAX_SESSIONS_TOTAL = 100
BYOB_MAX_SESSIONS_PER_IP = 3
#: HTTP-Timeout für die Telegram-Aufrufe der BYOB-Endpunkte (getMe/getUpdates).
BYOB_API_TIMEOUT = 15.0
#: RAM-TTL der Registry-Einträge (nur Metadaten: Bot-ID, Handle, Fingerprint).
BYOB_REGISTRY_TTL = 3600.0


# --------------------------------------------------------------------------- #
# Privacy-Redaction (best effort): verhindert Token-Leaks über Fremdbibliotheken,
# z. B. urllib3 im Debug-Modus. App liegt bewusst *über* botkit (kein Zyklus).
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - Import kann in Minimalinstallationen fehlen
    from telegram_formatter.botkit.privacy import install_privacy_filters

    install_privacy_filters()  # Root-Logger + Handler + verräterische Libs
except Exception:  # noqa: BLE001 - Sicherheitsnetz, nie Grund für App-Absturz
    LOGGER.debug("botkit-Privacy-Filter nicht installiert (optionale Schicht).")


# --------------------------------------------------------------------------- #
# Eingaben validieren
# --------------------------------------------------------------------------- #
def _valid_chat_id(raw: object) -> str | None:
    """Numerische Chat-ID akzeptieren (Pattern aus ``botkit.registry``), sonst ``None``."""
    if raw is None:
        return None
    if not isinstance(raw, (str, int)):
        return None
    candidate = str(raw).strip()
    if CHAT_ID_PATTERN.match(candidate):
        return candidate
    return None


# --------------------------------------------------------------------------- #
# Frequenzbegrenzung pro IP (In-Process; Multi-Worker: grobe Näherung)
# --------------------------------------------------------------------------- #
_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _rate_limited(
    bucket: str,
    limit: int,
    window_seconds: float = 60.0,
    *,
    per_ip: bool = True,
) -> bool:
    """
    ``True``, wenn das Limit im Fenster erreicht ist.

    ``per_ip=True`` zählt pro Client-Adresse (der Normalfall). Mit
    ``per_ip=False`` zählt der Zähler **instanzweit** — das braucht der
    anonyme Browser-Versand, damit viele Adressen zusammen den geteilten Bot
    nicht gegen die Telegram-Rate-Limits laufen lassen.
    """
    if limit <= 0:
        return False
    who = (request.remote_addr or "unknown") if per_ip else "alle"
    key = (bucket, who)
    now = time.monotonic()
    with _RATE_LOCK:
        hits = _RATE_HITS[key]
        while hits and hits[0] < now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


# --------------------------------------------------------------------------- #
# BYOB-Laufzeit: Registry + SessionManager + Session-Metadaten (alles RAM)
# --------------------------------------------------------------------------- #
@dataclass
class _BotInfo:
    """Nicht-geheime Bot-Identität für die Anzeige im UI (aus ``getMe``)."""

    id: int
    username: str
    display_name: str

    def as_dict(self) -> dict:
        handle = f"@{self.username}" if self.username else f"id:{self.id}"
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "handle": handle,
        }


class _ByobRuntime:
    """
    Web-Laufzeitumgebung für BYOB-Sessions (Betriebsmodus B).

    Kapselt die botkit-Bausteine (``BotRegistry`` + ``SessionManager``) plus
    die session-bezogenen Metadaten, die nur die Webschicht braucht (Absender-
    IP für die Per-IP-Kappe, Bot-Identität für die Statusanzeige). Alles lebt
    im RAM dieses Prozesses und endet mit der Session bzw. dem Prozess:

    * das Token selbst hält ausschließlich das ``BotSession``-Objekt,
    * ``meta`` enthält IP und Identität, niemals das Token,
    * abgelaufene Sessions werden beim nächsten Zugriff entfernt
      (``SessionManager.reap_expired`` + ``prune``).

    Der Konstruktor ist bewusst injizierbar (Registry/Manager/Config), damit
    Tests ohne Netzwerk und mit Fake-Uhren arbeiten können.
    """

    def __init__(
        self,
        *,
        registry: BotRegistry,
        manager: SessionManager,
        config: SessionConfig,
    ) -> None:
        self._registry = registry
        self._manager = manager
        self._config = config
        #: session_id -> metadata; session_secret is stored only as a digest.
        self._meta: dict[str, dict[str, object]] = {}
        self._lock = threading.RLock()
        # Serializes capacity checks with session creation. Without this,
        # concurrent opens could all pass the same active-count check.
        self._capacity_lock = threading.Lock()

    @property
    def capacity_lock(self) -> threading.Lock:
        return self._capacity_lock

    @property
    def manager(self) -> SessionManager:
        return self._manager

    @property
    def config(self) -> SessionConfig:
        return self._config

    def prune(self) -> None:
        """Entfernt Metadaten von Sessions, die der Manager nicht mehr kennt."""
        with self._lock:
            stale = [sid for sid in self._meta if self._manager.get(sid) is None]
            for sid in stale:
                del self._meta[sid]

    def count_for_ip(self, ip: str) -> int:
        with self._lock:
            return sum(1 for meta in self._meta.values() if meta.get("ip") == ip)

    def open_session(self, token: BotToken, chat_id: str, *, ip: str):
        """
        Verifiziert + registriert den Bot und öffnet die ephemere Session.

        :raises RegistrationError: Telegram lehnt das Token ab (ungültig,
            widerrufen, kein Bot, ID-Mismatch).
        :raises SessionError: Chat-ID ungültig oder Session-Grenzen verletzt.
        """
        record = self._registry.register(token, owner_ref="web")
        bot_info = _BotInfo(
            id=record.identity.bot_id,
            username=record.identity.username,
            display_name=record.identity.display_name,
        )
        session = self._manager.open(token, chat_id)
        session_secret = secrets.token_urlsafe(32)
        secret_digest = hashlib.sha256(session_secret.encode("ascii")).digest()
        with self._lock:
            self._meta[session.session_id] = {
                "ip": ip,
                "bot": bot_info,
                "secret_digest": secret_digest,
            }
        return session, bot_info, session_secret

    def authenticate(self, session_id: str, session_secret: str) -> bool:
        """Validate the second, per-session proof without storing it in plaintext."""
        with self._lock:
            meta = self._meta.get(session_id)
            expected = meta.get("secret_digest") if meta else None
        if not isinstance(expected, bytes) or not isinstance(session_secret, str):
            return False
        supplied = hashlib.sha256(session_secret.encode("utf-8")).digest()
        return hmac.compare_digest(supplied, expected)

    def bot_info(self, session_id: str) -> _BotInfo | None:
        with self._lock:
            meta = self._meta.get(session_id)
        if not meta:
            return None
        bot = meta.get("bot")
        return bot if isinstance(bot, _BotInfo) else None

    def close(self, session_id: str) -> bool:
        """Schließt die Session (Token-Referenz fällt) und räumt ``meta``."""
        closed = self._manager.close(session_id)
        with self._lock:
            self._meta.pop(session_id, None)
        return closed


_BYOB_RUNTIME: _ByobRuntime | None = None
_BYOB_BUILD_LOCK = threading.Lock()


def _build_byob(config: SessionConfig | None = None) -> _ByobRuntime:
    """
    Baut die BYOB-Laufzeit. ``get_me`` wird über das Modul-Global aufgelöst,
    damit Tests es monkeypatchen können.

    ``require_review=False`` ist hier sicher, weil auf dem Server ausschließlich
    der geprüfte Code dieses Projekts läuft (``utils``/``sender``) — es gibt
    keinen Nutzer-Bot-Code, der ein Review bräuchte. Das Review-Gate bleibt
    für eigenen Bot-Code (``botctl``, CI) unverändert Pflicht.
    """
    cfg = config or SessionConfig(
        require_review=False,
        ttl_seconds=BYOB_TTL_SECONDS,
        idle_timeout_seconds=BYOB_IDLE_SECONDS,
        max_input_chars=MAX_INPUT_CHARS,
        timeout=BYOB_API_TIMEOUT,
    )
    registry = BotRegistry(
        verify=lambda secret: get_me(secret, timeout=BYOB_API_TIMEOUT),
        ttl_seconds=BYOB_REGISTRY_TTL,
    )
    manager = SessionManager(registry=registry, review_gate=None, config=cfg)
    return _ByobRuntime(registry=registry, manager=manager, config=cfg)


def _byob() -> _ByobRuntime:
    """Lazy Singleton — erst bei erster Nutzung bauen (Import bleibt billig)."""
    global _BYOB_RUNTIME
    if _BYOB_RUNTIME is None:
        with _BYOB_BUILD_LOCK:
            if _BYOB_RUNTIME is None:
                _BYOB_RUNTIME = _build_byob()
    return _BYOB_RUNTIME


# --------------------------------------------------------------------------- #
# Request-Guards
# --------------------------------------------------------------------------- #
#: Endpoint-Name des geteilten Versands (``@app.route("/api/send")`` →
#: ``def send()``). Nur dieser Endpunkt kann vom Operator-Token ausgenommen
#: werden — und auch das nur, wenn der Browser-Versand freigeschaltet ist.
#: Eine Wildcard-Ausnahme (etwa „alle BYOB-Endpunkte“) gibt es bewusst nicht:
#: Der Operator-Token bleibt für jede Instanz, die ihn setzt, Pflicht.
SHARED_SEND_ENDPOINT = "send"


def _operator_token_required() -> bool:
    """
    Muss dieser Request den Operator-Token ``X-Auth-Token`` mitbringen?

    Ja — sobald ``TELEGRAM_FORMATTER_API_TOKEN`` gesetzt ist. Einzige
    Ausnahme: der geteilte Versand, wenn der Betreiber zusätzlich den
    Browser-Versand freigeschaltet hat (:func:`_shared_web_send_available`).
    Ohne diese Ausnahme wäre die Kombination „Operator-Token gesetzt +
    ``SHARED_WEB_SEND=1``“ widersprüchlich: das Secret dürfte dem Browser nie
    ausgehändigt werden, ``/api/send`` wäre aber gerade für den Browser
    gedacht. Alle übrigen POSTs (auch ``/api/byob/*``) bleiben pflichtig.
    """
    if not API_TOKEN:
        return False
    return not (request.endpoint == SHARED_SEND_ENDPOINT and _shared_web_send_available())


def _request_authenticated() -> bool:
    """Zeitkonstanter Vergleich des ``X-Auth-Token``-Headers gegen den Operator-Token."""
    if not API_TOKEN:
        return False
    return hmac.compare_digest(request.headers.get("X-Auth-Token", ""), API_TOKEN)


@app.before_request
def _guard():
    """Origin-Bindung und optionales API-Token für alle schreibenden Endpunkte."""
    if request.method == "GET":
        return None
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.netloc and parsed.netloc != request.host:
            return jsonify({"error": "Ursprung (Origin) nicht erlaubt."}), 403
    if _operator_token_required():
        supplied = request.headers.get("X-Auth-Token", "")
        if not hmac.compare_digest(supplied, API_TOKEN):
            return jsonify({"error": "Autorisierung erforderlich."}), 401
    return None


@app.after_request
def _security_headers(response):
    """CSP ohne Inline-Skripte, keine Frames, kein MIME-Sniffing, no-referrer."""
    csp = "; ".join(
        (
            "default-src 'self'",
            # Redesign 2026-09: komplett selbst-gehostete Assets (static/css,
            # static/js) — die Tailwind-/Font-Awesome-CDNs sind entfernt und
            # damit jede Fremdnets-Whitelist. Root-Cause des Design-Bruchs war
            # genau dieses Gespann: Das Tailwind-Play-CDN injizierte Inline-
            # <style>-Regeln, die die damalige CSP (style-src ohne
            # 'unsafe-inline') blockierte -> ungestylter Rohtext. Ohne CDN kann
            # so etwas nicht mehr passieren; Tests: tests/test_app.py und
            # tests/test_frontend.py.
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
            "form-action 'none'",
        )
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.errorhandler(404)
def _not_found(_exc):
    return jsonify({"error": "Endpunkt nicht gefunden."}), 404


@app.errorhandler(405)
def _method_not_allowed(_exc):
    return jsonify({"error": "Methode nicht erlaubt."}), 405


@app.errorhandler(413)
def _too_large(_exc):
    return jsonify({"error": f"Anfrage zu groß (max. {MAX_BODY_BYTES // 1024} KiB)."}), 413


@app.errorhandler(Exception)
def _unhandled(exc):
    # Nur Klassenname ins Log-Event (Redaction aktiv); keine Details an Clients.
    LOGGER.warning("app.unhandled_error error=%s", exc.__class__.__name__)
    return jsonify({"error": "Interner Fehler — bitte später erneut versuchen."}), 500


# --------------------------------------------------------------------------- #
# Request-Payload extrahieren + validieren
# --------------------------------------------------------------------------- #
def _json_body() -> tuple[dict | None, tuple | None]:
    """JSON-Objekt aus dem Request-Body — oder eine 400-Fehlerantwort."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, (jsonify({"error": "JSON-Objekt als Body erwartet."}), 400)
    return data, None


def _valid_text(data: dict, max_chars: int | None = None) -> tuple[str | None, tuple | None]:
    """
    ``text``-Feld prüfen (Typ + Länge) — gemeinsam für alle Sendewege.

    ``max_chars`` erlaubt die kürzere Kappe des anonymen Browser-Versands
    (:data:`SHARED_WEB_MAX_INPUT_CHARS`); ohne Wert gilt :data:`MAX_INPUT_CHARS`.
    """
    limit = MAX_INPUT_CHARS if max_chars is None else max_chars
    text = data.get("text", "")
    if not isinstance(text, str):
        return None, (jsonify({"error": "'text' muss ein String sein."}), 400)
    if len(text) > limit:
        return None, (
            jsonify({"error": f"Eingabe zu lang (max. {limit} Zeichen)."}),
            400,
        )
    return text, None


def _public_consent(data: dict) -> bool:
    """
    Anonyme Browser-Sendungen brauchen das Eingeständnis, dass der geteilte
    Chat **öffentlich** ist (``"confirm_public": true``). Die Website holt es
    im Sende-Bestätigungsdialog ein; ein Skript umgeht damit nichts — die
    Hürde stellt sicher, dass niemand *unbeabsichtigt* in den öffentlichen Chat
    postet. Authentizierte API-Aufrufe (Operator-Token) brauchen sie nicht.
    """
    return data.get("confirm_public") is True


def _resolve_target_chat(data: dict, *, require_chat: bool) -> tuple[str | None, tuple | None]:
    """
    Zielchat bestimmen — liefert ``(chat_id, None)`` oder ``(None, Fehlerantwort)``.

    Gehosteter Betrieb (``TELEGRAM_CHAT_ID`` gesetzt) ist **gepinnt**: ein
    abweichender ``chat_id``-Wert im Body wird abgewiesen (Audit K-2 — sonst
    wäre der Endpunkt ein offener Relay). Selbstbetrieb ohne ENV-Chat muss
    eine numerische ``chat_id`` im Body mitbringen; ungültige Werte werden
    nicht still ersetzt, sondern abgelehnt.
    """
    raw_chat = data.get("chat_id")
    if CHAT_ID:
        if raw_chat is not None and _valid_chat_id(raw_chat) != CHAT_ID:
            return None, (
                jsonify({"error": "chat_id kann hier nicht gesetzt werden — "
                                  "der Dienst sendet nur in den konfigurierten Chat."}),
                400,
            )
        return CHAT_ID, None

    chat = _valid_chat_id(raw_chat)
    if chat is None:
        if raw_chat is not None:
            return None, (
                jsonify({"error": "chat_id muss eine Ganzzahl sein (z. B. -1001234567890)."}),
                400,
            )
        if require_chat:
            return None, (jsonify({"error": "Keine Chat-ID angegeben."}), 400)
        chat = "0"
    return chat, None


# --------------------------------------------------------------------------- #
# Routen
# --------------------------------------------------------------------------- #
@app.route("/", methods=["GET"])
def index() -> str:
    """Rendert die Editor-Seite (Markdown/LaTeX -> Telegram-Vorschau)."""
    return render_template(
        "index.html",
        # ``configured`` = es gibt einen geteilten Bot mit gepinntem Zielchat
        # (steuert die Privatsphäre-Warnung). ``shared_send_available`` = dieser
        # Bot darf **vom Browser** genutzt werden (Opt-in + Pinning, siehe
        # ``_shared_web_send_available``). Das Operator-Secret erhält der
        # Browser nie — ohne Freischaltung bleibt der Shared-Versand API-only.
        configured=bool(BOT_TOKEN and CHAT_ID),
        shared_send_available=_shared_web_send_available(),
        byob_enabled=BYOB_ENABLED,
        shared_bot_handle=SHARED_BOT_HANDLE,
        version=__version__,
    )


@app.route("/api/convert", methods=["POST"])
def convert():
    """
    Wandelt den übermittelten Text in sendefertige Telegram-Nachrichten um
    und gibt die Payloads (inkl. Aufteilung) als JSON zurück. Rein lesend —
    es wird nichts versendet.
    """
    if _rate_limited("convert", CONVERTS_PER_MINUTE):
        return jsonify({"error": "Zu viele Anfragen — bitte kurz warten."}), 429
    data, err = _json_body()
    if err is not None:
        return err
    text, err = _valid_text(data)
    if err is not None:
        return err
    chat_id, err = _resolve_target_chat(data, require_chat=False)
    if err is not None:
        return err
    messages = build_messages(text, chat_id)
    return jsonify(
        {
            "count": len(messages),
            "messages": [{"kind": m.kind, "payload": m.payload} for m in messages],
        }
    )


@app.route("/api/send", methods=["POST"])
def send():
    """
    Sendet den übermittelten Text über den **geteilten** Bot — ausschließlich
    in den konfigurierten ``TELEGRAM_CHAT_ID`` (bzw. die geprüfte ``chat_id``
    des Selbstbetriebs ohne ENV-Chat).

    Zwei Zugangsarten, beide explizit — sonst 503 (fail-closed):

    * **Authentifiziert** — ``X-Auth-Token`` passt zu
      ``TELEGRAM_FORMATTER_API_TOKEN``: serverseitiger Aufruf des Betreibers,
      volle Textlänge, Limit ``SENDS_PER_MINUTE``.
    * **Anonym aus dem Browser** — nur bei freigeschaltetem
      ``TELEGRAM_FORMATTER_SHARED_WEB_SEND`` **und** gepinntem Zielchat. Dafür
      gelten die strengeren Regeln des öffentlichen Raums: Body-Feld
      ``confirm_public: true`` als Bestätigung der öffentlichen Sichtbarkeit,
      Kürzung auf ``SHARED_WEB_MAX_INPUT_CHARS``, engere Frequenzlimits
      (pro IP und instanzweit).
    """
    if not BOT_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN nicht konfiguriert."}), 400

    authed = _request_authenticated()
    if not authed and not _shared_web_send_available():
        return jsonify({"error": SHARED_SEND_DISABLED}), 503

    if authed:
        if _rate_limited("send", SENDS_PER_MINUTE):
            return jsonify({"error": "Zu viele Sendeversuche — bitte kurz warten."}), 429
    elif _rate_limited("shared-web-send", SHARED_WEB_SENDS_PER_MINUTE) or _rate_limited(
        "shared-web-send-total", SHARED_WEB_SENDS_PER_MINUTE_TOTAL, per_ip=False
    ):
        return jsonify({
            "error": "Zu viele Sendeversuche — bitte kurz warten. Der geteilte Bot "
                     "soll nicht zuboomen; für private Inhalte die eigene Bot-Session.",
            "retry_after": 60,
        }), 429

    data, err = _json_body()
    if err is not None:
        return err

    if authed:
        text, err = _valid_text(data)
    else:
        # Anonymer Versand ist öffentlich sichtbar -> bewusste Bestätigung
        # voraus und kürzere Längenkappe (siehe Docstring).
        if not _public_consent(data):
            return jsonify({
                "error": "Öffentlicher Versand: bitte mit \"confirm_public\": true "
                         "bestätigen, dass die Nachricht im geteilten Chat für alle "
                         "Besucher sichtbar ist."
            }), 400
        text, err = _valid_text(data, SHARED_WEB_MAX_INPUT_CHARS)
    if err is not None:
        return err

    chat_id, err = _resolve_target_chat(data, require_chat=True)
    if err is not None:
        return err

    messages = build_messages(text, chat_id)
    results = []
    for m in messages:
        try:
            send_message(m, BOT_TOKEN)
        except SendError as exc:
            # B-6: bereits gesendete Chunks offenlegen — Nutzer sollen nicht
            # blind neu senden (Duplikate). retry_after bei 429 durchreichen.
            payload = {"error": str(exc), "sent_before_error": len(results), "results": results}
            if exc.retry_after is not None:
                payload["retry_after"] = exc.retry_after
                payload["note"] = "Telegram-Rate-Limit: erst nach der Wartezeit erneut senden."
            elif results:
                payload["note"] = "Teile wurden bereits gesendet — kein kompletter Wiederholungsversand."
            status = 429 if exc.retry_after is not None else 502
            return jsonify(payload), status
        results.append({"kind": m.kind, "status": "ok"})

    return jsonify({
        "sent": len(results),
        "results": results,
        # Reine Anzeigeinformation für die UI: worüber wurde gesendet?
        # Enthält keine Secrets und keine Chat-Details außer dem Ziel.
        "via": {"bot": SHARED_BOT_HANDLE, "chat_id": chat_id, "public": not authed},
    })


# --------------------------------------------------------------------------- #
# BYOB: Eigener Bot in ephemeren Web-Sessions (keine zentrale Speicherung)
# --------------------------------------------------------------------------- #
def _byob_disabled() -> tuple | None:
    """404-Antwort, wenn der Betreiber BYOB abgeschaltet hat."""
    if not BYOB_ENABLED:
        return jsonify({"error": "BYOB ist auf dieser Instanz deaktiviert."}), 404
    return None


def _byob_session_id(data: dict) -> tuple[str | None, str | None, tuple | None]:
    """Validate the session handle and its second proof-of-possession secret."""
    sid = data.get("session_id")
    session_secret = data.get("session_secret")
    if not isinstance(sid, str) or not 8 <= len(sid) <= 128:
        return None, None, (jsonify({"error": "session_id fehlt oder ist ungültig."}), 400)
    if not isinstance(session_secret, str) or not 32 <= len(session_secret) <= 128:
        return None, None, (jsonify({"error": "session_secret fehlt oder ist ungültig."}), 400)
    return sid, session_secret, None


@app.route("/api/byob/session", methods=["POST"])
def byob_session_open():
    """
    Öffnet eine ephemere Session mit dem Bot des Nutzers (BYOB).

    Ablauf: Token-Format prüfen → ``getMe``-Verifikation über die RAM-Registry
    → ``SessionManager.open`` (TTL, Leerlauf-Timeout, Rate-Limit) → opaker
    Session-Handle zurück. Das Token wird **nicht** gespeichert, geloggt oder
    in der Antwort wiedergegeben; es lebt nur im RAM der Session.
    """
    disabled = _byob_disabled()
    if disabled is not None:
        return disabled
    if _rate_limited("byob-open", BYOB_SESSIONS_PER_MINUTE):
        return jsonify({"error": "Zu viele Anfragen — bitte kurz warten."}), 429

    data, err = _json_body()
    if err is not None:
        return err

    if data.get("consent") is not True:
        return jsonify(
            {"error": "Bitte bestätige den Hinweis zum Umgang mit dem Token (Checkbox)."}
        ), 400

    token_raw = data.get("token")
    if not isinstance(token_raw, str):
        return jsonify({"error": "'token' muss ein String sein."}), 400
    try:
        token = BotToken.parse(token_raw)
    except TokenError as exc:
        return jsonify({"error": str(exc)}), 400

    chat = _valid_chat_id(data.get("chat_id"))
    if chat is None:
        return jsonify(
            {"error": "chat_id muss eine Ganzzahl sein (z. B. -1001234567890 oder 4711)."}
        ), 400

    runtime = _byob()
    ip = request.remote_addr or "unknown"
    # Keep the capacity check and creation together. Telegram verification is
    # intentionally inside the lock: otherwise concurrent opens can all pass
    # the cap before any of them is inserted into the manager.
    with runtime.capacity_lock:
        runtime.manager.reap_expired()
        runtime.prune()
        if runtime.manager.active_count >= BYOB_MAX_SESSIONS_TOTAL:
            return jsonify(
                {"error": "Zu viele aktive Sessions auf dieser Instanz — bitte später erneut versuchen."}
            ), 429
        if runtime.count_for_ip(ip) >= BYOB_MAX_SESSIONS_PER_IP:
            return jsonify(
                {"error": "Zu viele aktive Sessions von dieser Adresse — bitte zuerst eine beenden."}
            ), 429

        try:
            session, bot_info, session_secret = runtime.open_session(token, chat, ip=ip)
        except RegistrationError as exc:
            # Verifikation fehlgeschlagen (ungültig/widerrufen/Netzwerk) — die
            # Meldung ist per Konstruktion token-frei.
            return jsonify({"error": str(exc)}), 400
        except SessionError as exc:
            return jsonify({"error": str(exc)}), 400

    cfg = runtime.config
    return jsonify(
        {
            "session_id": session.session_id,
            "session_secret": session_secret,
            "bot": bot_info.as_dict(),
            "chat_id": session.chat_id,
            "limits": {
                "ttl_seconds": cfg.ttl_seconds,
                "idle_timeout_seconds": cfg.idle_timeout_seconds,
                "max_messages_per_minute": cfg.max_messages_per_minute,
                "max_input_chars": cfg.max_input_chars,
            },
        }
    ), 201


@app.route("/api/byob/discover", methods=["POST"])
def byob_discover_chats():
    """
    Findet Chat-IDs, mit denen der Bot kürzlich Kontakt hatte (``getUpdates``).

    Der Aufruf ist ein reiner *Blick* in die Update-Warteschlange: kein
    ``offset`` ⇒ nichts wird bestätigt oder verbraucht. Zurück kommen
    ausschließlich Chat-Metadaten (ID, Typ, Name) — **niemals**
    Nachrichteninhalte in der *Antwort*. Die Update-Inhalte werden dabei
    transient gelesen und sofort verworfen (nicht gespeichert, nicht
    geloggt). Zweck: die numerische Chat-ID für den Session-Start ohne
    Handarbeit herauszufinden.
    """
    disabled = _byob_disabled()
    if disabled is not None:
        return disabled
    if _rate_limited("byob-discover", BYOB_DISCOVER_PER_MINUTE):
        return jsonify({"error": "Zu viele Anfragen — bitte kurz warten."}), 429

    data, err = _json_body()
    if err is not None:
        return err

    token_raw = data.get("token")
    if not isinstance(token_raw, str):
        return jsonify({"error": "'token' muss ein String sein."}), 400
    try:
        token = BotToken.parse(token_raw)
    except TokenError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        updates = get_updates(token.reveal(), limit=100, poll_timeout=0,
                              timeout=BYOB_API_TIMEOUT)
    except TelegramAPIError as exc:
        # Meldung ist sanitisiert (kein Token, keine URL); typische Fälle:
        # Webhook konfligiert (409) oder Token ungültig.
        return jsonify({"error": f"Chat-Erkennung fehlgeschlagen: {exc}"}), 502

    chats: list[dict] = []
    seen: set[int] = set()
    for update in updates.get("result", []) if isinstance(updates.get("result"), list) else []:
        if not isinstance(update, dict):
            continue
        for key in ("message", "edited_message", "channel_post", "edited_channel_post",
                    "my_chat_member"):
            obj = update.get(key)
            if not isinstance(obj, dict):
                continue
            chat = obj.get("chat")
            if not isinstance(chat, dict) or "id" not in chat:
                continue
            chat_id = chat.get("id")
            if not isinstance(chat_id, int) or chat_id in seen:
                continue
            seen.add(chat_id)
            name = chat.get("title") or chat.get("first_name") or chat.get("username") or "?"
            chats.append({"id": chat_id, "type": str(chat.get("type", "?")), "name": str(name)})
            if len(chats) >= 20:
                break
        if len(chats) >= 20:
            break

    return jsonify({"chats": chats})


@app.route("/api/byob/send", methods=["POST"])
def byob_session_send():
    """
    Sendet Text über die eigene Bot-Session (``session.send``).

    Die Session erzwingt ihre Grenzen selbst: TTL/Leerlauf, Rate-Limit pro
    Minute, Eingabelänge. Fehler werden unterschieden in *Session weg*
    (410 — neu öffnen), *Session-Limit* (429) und *Telegram-Fehler* (502/429
    mit ``sent_before_error`` für Teilfortschritt).
    """
    disabled = _byob_disabled()
    if disabled is not None:
        return disabled
    if _rate_limited("byob-send", BYOB_SENDS_PER_MINUTE):
        return jsonify({"error": "Zu viele Sendeversuche — bitte kurz warten."}), 429

    data, err = _json_body()
    if err is not None:
        return err

    sid, session_secret, err = _byob_session_id(data)
    if err is not None:
        return err

    text, err = _valid_text(data)
    if err is not None or text is None:
        return err

    runtime = _byob()
    runtime.manager.reap_expired()
    if not runtime.authenticate(sid, session_secret):
        return jsonify({"error": "Session-Zugangsdaten ungültig."}), 401
    session = runtime.manager.get(sid)
    if session is None:
        # A valid proof for an expired/closed session reaches this branch.
        # Invalid proofs were rejected above without revealing session state.
        return jsonify(
            {"error": "Session abgelaufen oder unbekannt — bitte erneut öffnen."}
        ), 410

    chunks_before = session.stats.chunks_sent
    try:
        responses = session.send(text)
    except SessionExpired:
        return jsonify({"error": "Session abgelaufen (TTL/Leerlauf) — bitte neu öffnen."}), 410
    except RateLimitExceeded as exc:
        return jsonify({"error": str(exc)}), 429
    except SessionError as exc:
        return jsonify({"error": str(exc)}), 400
    except SendError as exc:
        # B-6-Äquivalent: Teilfortschritt offenlegen, kein blindes Neu-Senden.
        payload = {"error": str(exc), "sent_before_error": session.stats.chunks_sent - chunks_before}
        if exc.retry_after is not None:
            payload["retry_after"] = exc.retry_after
            payload["note"] = "Telegram-Rate-Limit: erst nach der Wartezeit erneut senden."
        elif payload["sent_before_error"]:
            payload["note"] = "Teile wurden bereits gesendet — kein kompletter Wiederholungsversand."
        status = 429 if exc.retry_after is not None else 502
        return jsonify(payload), status

    return jsonify(
        {
            "sent": len(responses),
            "session": {
                "ttl_remaining_seconds": round(session.ttl_remaining_seconds),
                "idle_remaining_seconds": round(session.idle_remaining_seconds),
            },
        }
    )


@app.route("/api/byob/status", methods=["POST"])
def byob_session_status():
    """Lebend-Status einer Session (Countdown/Zähler) — ohne Token-Bezug."""
    disabled = _byob_disabled()
    if disabled is not None:
        return disabled

    data, err = _json_body()
    if err is not None:
        return err
    sid, session_secret, err = _byob_session_id(data)
    if err is not None:
        return err

    runtime = _byob()
    if not runtime.authenticate(sid, session_secret):
        return jsonify({"error": "Session-Zugangsdaten ungültig."}), 401
    session = runtime.manager.get(sid)
    if session is None:
        return jsonify({"active": False})
    bot = runtime.bot_info(sid)
    return jsonify(
        {
            "active": True,
            "bot": bot.as_dict() if bot else {"id": session.bot_id, "handle": f"id:{session.bot_id}"},
            "chat_id": session.chat_id,
            "ttl_remaining_seconds": round(session.ttl_remaining_seconds),
            "idle_remaining_seconds": round(session.idle_remaining_seconds),
            "messages_sent": session.stats.messages_sent,
            "chunks_sent": session.stats.chunks_sent,
        }
    )


@app.route("/api/byob/close", methods=["POST"])
def byob_session_close():
    """Beendet die Session sofort — die Token-Referenz fällt."""
    disabled = _byob_disabled()
    if disabled is not None:
        return disabled

    data, err = _json_body()
    if err is not None:
        return err
    sid, session_secret, err = _byob_session_id(data)
    if err is not None:
        return err

    runtime = _byob()
    if not runtime.authenticate(sid, session_secret):
        return jsonify({"error": "Session-Zugangsdaten ungültig."}), 401
    closed = runtime.close(sid)
    return jsonify({"closed": closed})


if __name__ == "__main__":
    # Nur für lokale Entwicklung. In Produktion: gunicorn "telegram_formatter.app:app".
    # 0.0.0.0 ist hier Absicht (Container-/Dev-Zugriff); der Produktions-
    # Einstieg ist Gunicorn hinter dem Plattform-Proxy. Der Werkzeug-Debugger
    # bleibt deaktiviert (kein FLASK_DEBUG=1 mit diesem Block starten).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)  # nosec B104
