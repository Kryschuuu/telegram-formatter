"""
botkit/session.py
=================
Ephemere Bot-Sessions — der Kern des dezentralen Modells.

Eine Session ist: **ein Bot + ein Ziel-Chat + ein Zeitfenster**, gehalten
ausschließlich im RAM. Sie existiert, solange der Nutzer sie braucht, und
verschwindet danach spurlos:

* das Token liegt im Session-Objekt und wird bei :meth:`BotSession.close`
  fallengelassen (kein Vault, keine Datei, kein Log);
* TTL und Leerlauf-Timeout beenden die Session automatisch
  (:class:`SessionManager.reap_expired`);
* gesendet wird über die bestehenden Projekt-Module ``utils.build_messages``
  und ``sender.send_message`` — die Konvertierung bleibt damit unberührt;
* geloggt werden nur Metadaten (Bot-ID, Anzahl Chunks, Zeichenzahl,
  Fingerprints), **nie** der Nachrichteninhalt.

Typischer Ablauf::

    registry = BotRegistry(verify=lambda secret: get_me(secret))
    gate = ReviewGate(ReviewLedger(), registry)
    manager = SessionManager(registry=registry, review_gate=gate)

    with manager.open(token, chat_id="-1001234567890", source_path="my_bot.py") as session:
        session.send("**Fett** und $E=mc^2$")
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sender import SendError, send_message
from utils import TelegramMessage, build_messages

from .privacy import audit, fingerprint
from .registry import BotRegistry, RegistrationError, validate_chat_id
from .review import ReviewGate, ReviewGateError
from .tokens import BotToken

__all__ = [
    "BotSession",
    "RateLimitExceeded",
    "SessionConfig",
    "SessionError",
    "SessionExpired",
    "SessionManager",
    "SessionStats",
]

LOGGER = logging.getLogger("botkit.session")


def _validated_chat_id(chat_id: int | str) -> str:
    """Wie :func:`botkit.registry.validate_chat_id`, aber als :class:`SessionError`."""
    try:
        return validate_chat_id(chat_id)
    except RegistrationError as exc:
        raise SessionError(str(exc)) from None

#: Signatur des Versand-Hooks (injizierbar, damit Tests ohne Netz laufen).
SenderFn = Callable[..., dict]


class SessionError(RuntimeError):
    """Session kann nicht geöffnet oder nicht verwendet werden."""


class SessionExpired(SessionError):
    """Session ist abgelaufen (TTL oder Leerlauf)."""


class RateLimitExceeded(SessionError):
    """Zu viele Nachrichten im Fenster — Schutz vor Telegram-Sperren (429)."""


@dataclass(frozen=True)
class SessionConfig:
    """Parameter einer Session — bewusst konservativ voreingestellt."""

    ttl_seconds: float = 1_800.0  # 30 min harte Obergrenze
    idle_timeout_seconds: float = 600.0  # 10 min ohne Aktivität -> zu
    max_messages_per_minute: int = 20  # Telegram: ~1 msg/s pro Chat
    max_input_chars: int = 100_000  # Input-Validierung vor der Konvertierung
    timeout: float = 15.0  # HTTP-Timeout je API-Aufruf
    api_base: str | None = None  # None = offizielle API
    require_review: bool = True  # Fail-Closed: ohne Freigabe keine Session


@dataclass
class SessionStats:
    """Zähler für Monitoring — ohne Inhalte."""

    messages_sent: int = 0
    chunks_sent: int = 0
    errors: int = 0


class BotSession:
    """
    Eine aktive Bot-Session.

    Hält genau ein :class:`~botkit.tokens.BotToken` und genau ein Ziel-Chat.
    Alle Grenzen (TTL, Leerlauf, Rate-Limit, Eingabelänge) werden hier
    durchgesetzt — nicht im aufrufenden Code.
    """

    def __init__(
        self,
        token: BotToken,
        chat_id: int | str,
        *,
        config: SessionConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        sender_fn: SenderFn | None = None,
        session_id: str | None = None,
    ) -> None:
        self._token: BotToken | None = token
        self._chat_id = _validated_chat_id(chat_id)
        self._config = config or SessionConfig()
        self._clock = clock
        self._sender = sender_fn or self._default_sender
        self._id = session_id or uuid.uuid4().hex
        self._created_at = self._clock()
        self._last_activity = self._created_at
        self._sent_timestamps: list[float] = []
        self.stats = SessionStats()
        self.closed = False

    # ------------------------------------------------------------- Metadaten
    @property
    def session_id(self) -> str:
        return self._id

    @property
    def bot_id(self) -> int:
        return self._token.bot_id if self._token else -1

    @property
    def chat_id(self) -> str:
        return self._chat_id

    @property
    def age_seconds(self) -> float:
        return self._clock() - self._created_at

    @property
    def is_expired(self) -> bool:
        """``True`` bei Überschreiten von TTL oder Leerlauf-Timeout."""
        if self.closed:
            return True
        now = self._clock()
        return (
            now - self._created_at > self._config.ttl_seconds
            or now - self._last_activity > self._config.idle_timeout_seconds
        )

    def __repr__(self) -> str:
        state = "closed" if self.closed else ("expired" if self.is_expired else "active")
        return (
            f"<BotSession id={self._id[:8]} bot={self.bot_id} state={state} "
            f"chunks={self.stats.chunks_sent}>"
        )

    # ---------------------------------------------------------------- Senden
    def send(self, markdown_text: str) -> list[dict]:
        """
        Konvertiert Markdown/LaTeX und versendet es über den Bot der Session.

        Ablauf:

        1. Input-Validierung (Typ, Länge);
        2. Konvertierung via ``utils.build_messages`` (wie im Rest des Projekts);
        3. Grenzen prüfen (aktiv, Rate-Limit);
        4. Versand je Chunk über ``sender.send_message``;
        5. Nur Metadaten loggen.
        """
        if not isinstance(markdown_text, str):
            raise SessionError("markdown_text muss ein String sein.")
        if len(markdown_text) > self._config.max_input_chars:
            raise SessionError(
                f"Eingabe zu lang ({len(markdown_text)} > {self._config.max_input_chars} Zeichen)."
            )

        messages: Sequence[TelegramMessage] = build_messages(markdown_text, self._chat_id)
        if not messages:
            return []
        return self.send_messages(messages, source_chars=len(markdown_text))

    def send_messages(
        self,
        messages: Sequence[TelegramMessage],
        *,
        source_chars: int = 0,
    ) -> list[dict]:
        """Versendet vorbereitete Nachrichten (Rate-Limit- und Ablaufprüfung inklusive)."""
        self._ensure_active()
        self._reserve_rate_budget(len(messages))

        responses: list[dict] = []
        for message in messages:
            try:
                responses.append(
                    self._sender(
                        message,
                        self._require_token().reveal(),
                        timeout=self._config.timeout,
                        api_base=self._config.api_base,
                    )
                )
                self.stats.chunks_sent += 1
            except SendError as exc:
                self.stats.errors += 1
                audit(
                    LOGGER,
                    logging.ERROR,
                    "session.send_failed",
                    session=self._id[:8],
                    bot=self.bot_id,
                    kind=message.kind,
                    error=exc.__class__.__name__,
                )
                raise

        self.stats.messages_sent += 1
        self._touch()
        audit(
            LOGGER,
            logging.INFO,
            "session.sent",
            session=self._id[:8],
            bot=self.bot_id,
            chat_fp=fingerprint(self._chat_id),
            chunks=len(messages),
            chars=source_chars,
        )
        return responses

    # ------------------------------------------------------------------ Ende
    def close(self) -> None:
        """
        Beendet die Session und verwirft das Token.

        Nach ``close()`` ist die Session nicht mehr nutzbar; der Verweis auf
        das Token wird gelöscht, damit es keine Referenz mehr im Objektgraphen
        gibt (und damit auch nicht in Tracebacks oder Debug-Dumps).
        """
        if self.closed:
            return
        bot_id = self.bot_id  # vor dem Verwerfen des Tokens festhalten
        chunks = self.stats.chunks_sent
        self.closed = True
        self._token = None
        self._sent_timestamps.clear()
        audit(LOGGER, logging.INFO, "session.closed", session=self._id[:8], bot=bot_id,
              chunks=chunks)

    def __enter__(self) -> BotSession:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -------------------------------------------------------------- Interna
    @staticmethod
    def _default_sender(message: TelegramMessage, secret: str, *, timeout: float,
                        api_base: str | None) -> dict:
        return send_message(message, secret, timeout=timeout, api_base=api_base)

    def _require_token(self) -> BotToken:
        if self._token is None or self.closed:
            raise SessionError("Session ist geschlossen — Token nicht mehr verfügbar.")
        return self._token

    def _ensure_active(self) -> None:
        if self.closed:
            raise SessionError("Session ist geschlossen.")
        if self.is_expired:
            raise SessionExpired(
                "Session abgelaufen (TTL/Leerlauf) — bitte neu öffnen."
            )

    def _touch(self) -> None:
        self._last_activity = self._clock()

    def _reserve_rate_budget(self, count: int) -> None:
        """Gleitendes Fenster (60 s) gegen Telegram-Rate-Limits (429)."""
        now = self._clock()
        window_start = now - 60.0
        self._sent_timestamps = [ts for ts in self._sent_timestamps if ts >= window_start]
        if len(self._sent_timestamps) + count > self._config.max_messages_per_minute:
            raise RateLimitExceeded(
                f"Rate-Limit: {self._config.max_messages_per_minute} Nachrichten/Minute "
                "überschritten. Bitte kurz warten."
            )
        self._sent_timestamps.extend([now] * count)


class SessionManager:
    """
    Verwaltet die aktiven Sessions eines Prozesses.

    Aufgaben: Öffnen nur nach Registrierung (und optional Review),
    Wiederfinden per Session-ID, geordnetes Schließen und das Abernten
    abgelaufener Sessions (``reap_expired``, z. B. aus einem Timer oder vor
    jedem Öffnen).
    """

    def __init__(
        self,
        *,
        registry: BotRegistry,
        review_gate: ReviewGate | None = None,
        config: SessionConfig | None = None,
        clock: Callable[[], float] = time.monotonic,
        sender_fn: SenderFn | None = None,
    ) -> None:
        self._registry = registry
        self._gate = review_gate
        self._config = config or SessionConfig()
        self._clock = clock
        self._sender_fn = sender_fn
        self._sessions: dict[str, BotSession] = {}

    # --------------------------------------------------------------- Öffnen
    def open(
        self,
        token: BotToken,
        chat_id: int | str,
        *,
        source_path: str | None = None,
    ) -> BotSession:
        """
        Öffnet eine Session — nur für registrierte (und freigegebene) Bots.

        :param source_path: Pfad zum reviewten Bot-Code. Im gehosteten Modus
            (``require_review=True``) Pflicht, weil die Freigabe an die
            Prüfsumme dieser Datei gebunden ist.
        :raises SessionError: Bot nicht registriert oder Review fehlt.
        """
        chat = _validated_chat_id(chat_id)
        record = self._registry.get(token.bot_id)
        if record is None:
            raise SessionError(
                f"Bot {token.bot_id} ist nicht registriert. "
                "Registrierung: 'botctl register --token-env TELEGRAM_BOT_TOKEN --owner <handle>'."
            )

        if self._config.require_review:
            if self._gate is None:
                # Fail-closed: keine Review-Instanz => keine gehostete Session.
                raise SessionError(
                    "require_review=True, aber kein ReviewGate konfiguriert."
                )
            if source_path is None:
                raise SessionError("source_path ist im Review-Modus erforderlich.")
            try:
                self._gate.verify(token.bot_id, source_path)
            except ReviewGateError as exc:
                raise SessionError(f"Bot ist nicht freigegeben: {exc}") from None
        elif not self._registry.is_approved(token.bot_id) and self._gate is not None:
            raise SessionError("Bot ist nicht freigegeben (Status != approved).")

        self.reap_expired()
        session = BotSession(
            token,
            chat,
            config=self._config,
            clock=self._clock,
            sender_fn=self._sender_fn,
        )
        self._sessions[session.session_id] = session
        audit(LOGGER, logging.INFO, "session.opened", session=session.session_id[:8],
              bot=token.bot_id, chat_fp=fingerprint(chat), ttl=self._config.ttl_seconds)
        return session

    # -------------------------------------------------------------- Verwaltung
    def get(self, session_id: str) -> BotSession | None:
        session = self._sessions.get(session_id)
        if session is None or session.is_expired:
            return None
        return session

    def close(self, session_id: str) -> bool:
        """Schließt eine Session. ``True``, wenn sie existierte."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def reap_expired(self) -> int:
        """Schließt alle abgelaufenen Sessions, liefert die Anzahl."""
        expired = [sid for sid, session in self._sessions.items() if session.is_expired]
        for session_id in expired:
            session = self._sessions.pop(session_id)
            session.close()
        if expired:
            audit(LOGGER, logging.INFO, "session.reaped", count=len(expired))
        return len(expired)

    @property
    def active_count(self) -> int:
        return len(self._sessions)
