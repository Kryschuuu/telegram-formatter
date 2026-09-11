"""
minimal_bot.py — Referenz-Implementierung eines eigenen, dezentralen Bots
=========================================================================

Dieser Bot ist das Gegenstück zum zentralen Dienst (``@mdtotxt_bot``):
Er läuft **beim Nutzer**, mit dem **Token des Nutzers**, und speichert
**nichts**.

Funktionsweise:

1. Token einmalig aus dem Environment lesen (danach sofort entfernen).
2. Beim Start gegen Telegram verifizieren (``getMe``) und registrieren.
3. Updates per Long-Polling holen (``getUpdates``) — der Offset liegt nur im
   RAM, nach einem Neustart beginnt der Bot neu; es gibt kein Archiv.
4. Eingehenden Text über ``utils.build_messages`` konvertieren und über eine
   kurze :class:`botkit.session.BotSession` **in denselben Chat** zurücksenden.
5. Beim Beenden alle Sessions schließen und einen eventuellen Webhook
   löschen (``deleteWebhook``) — kein Zustand bleibt bei Telegram.

Der Code ist so geschrieben, dass er die statischen Prüfregeln von
``botkit.review`` (BK001–BK012) erfüllt:

* keine Importe mit Persistenz-, Shell- oder Roh-Socket-Funktion;
* keine Schreibzugriffe, keine Datenbank, kein Cache;
* keine Ausgaben von Nachrichteninhalten (weder ``print`` noch Logging);
* ausgehende Aufrufe ausschließlich über die vetted Clients
  (``botkit.telegram_api``, ``sender``) an ``api.telegram.org``.

Starten::

    export TELEGRAM_BOT_TOKEN="123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
    export TELEGRAM_CHAT_ID="-1001234567890"   # optional: nur diesen Chat bedienen
    python examples/own_bot/minimal_bot.py

Review vor dem Deployment (gehosteter Betrieb)::

    python botctl.py review examples/own_bot/minimal_bot.py --bot-id 123456789
"""

from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Mapping
from typing import Any

from botkit.privacy import (
    audit,
    fingerprint,
    install_privacy_filters,
    scrub_environment,
)
from botkit.registry import BotRegistry
from botkit.session import BotSession, SessionConfig, SessionManager
from botkit.telegram_api import delete_webhook, get_me, get_updates
from botkit.tokens import BotToken
from utils import TelegramMessage, build_messages

LOGGER = logging.getLogger("own_bot")

#: Nur private Nachrichten und Gruppennachrichten verarbeiten.
ALLOWED_UPDATES = ["message"]

#: Obergrenze je eingehender Nachricht (Input-Validierung vor der Konvertierung).
MAX_INPUT_CHARS = 20_000

#: Long-Polling-Fenster (Sekunden) und Pause bei API-Fehlern.
POLL_SECONDS = 20
ERROR_BACKOFF_SECONDS = 5

_running = True


# --------------------------------------------------------------------------- #
# Konfiguration
# --------------------------------------------------------------------------- #
def load_token(env_name: str = "TELEGRAM_BOT_TOKEN") -> BotToken:
    """
    Liest das Token und entfernt es danach aus dem Environment.

    Grund: Kindprozesse und Crash-Dumps erben ``os.environ`` — das Token soll
    nur an genau dieser einen Stelle im Speicher existieren.
    """
    token = BotToken.from_environment(env_name)
    scrub_environment(env_name)
    return token


def api_base() -> str:
    """
    API-Basis — Standard ist die offizielle Telegram-API.

    Über ``TELEGRAM_API_BASE`` lässt sich ein **privater Bot-API-Server**
    eintragen. Das ist im dezentralen Modell der konsequente nächste Schritt:
    nicht nur der Bot, sondern auch der API-Endpunkt liegt beim Nutzer.
    """
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def allowed_chat_id() -> str | None:
    """Optionaler Filter: nur dieser Chat wird bedient (sonst alle)."""
    raw = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return raw or None


def stop(signum: int, _frame: Any) -> None:
    """Signal-Handler für geordnetes Herunterfahren (SIGTERM/SIGINT)."""
    global _running
    _running = False
    audit(LOGGER, logging.INFO, "bot.shutdown_requested", signal=signum)


# --------------------------------------------------------------------------- #
# Update-Verarbeitung
# --------------------------------------------------------------------------- #
def extract_message(update: Mapping[str, Any]) -> tuple[str, str] | None:
    """
    Validiert ein Update und liefert ``(chat_id, text)`` — oder ``None``.

    Validierung ist Pflicht, weil Werte aus dem Netz kommen: Typ prüfen,
    Präsenz prüfen, Länge begrenzen. Alles Unbekannte wird ignoriert.
    """
    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    if not isinstance(chat, Mapping):
        return None
    raw_text = message.get("text")
    if not isinstance(raw_text, str) or not raw_text.strip():
        return None
    if len(raw_text) > MAX_INPUT_CHARS:
        audit(LOGGER, logging.WARNING, "bot.input_too_long", chars=len(raw_text))
        return None
    chat_id = str(chat.get("id", "")).strip()
    if not chat_id.lstrip("-").isdigit():
        return None
    return chat_id, raw_text


def build_reply(source: str, chat_id: str) -> list[TelegramMessage]:
    """Konvertiert den Quelltext in sendefertige Nachrichten (wie im Hauptprojekt)."""
    return build_messages(source, chat_id)


# --------------------------------------------------------------------------- #
# Hauptschleife
# --------------------------------------------------------------------------- #
def run() -> int:
    """Startet den Bot und läuft, bis ein Stop-Signal kommt."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    install_privacy_filters()
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    token = load_token()
    base = api_base()
    registry = BotRegistry(verify=lambda secret: get_me(secret, api_base=base))
    record = registry.register(token, owner_ref="self-hosted")

    # Lokaler Vertrauensmodus: der Nutzer betreibt seinen eigenen Bot und hat
    # den Code selbst reviewt (bzw. im PR reviewen lassen). require_review ist
    # hier False; im gehosteten Betrieb steht es auf True.
    config = SessionConfig(
        require_review=False,
        ttl_seconds=3_600.0,
        idle_timeout_seconds=600.0,
        max_messages_per_minute=20,
        max_input_chars=MAX_INPUT_CHARS,
        api_base=base,
    )
    manager = SessionManager(registry=registry, config=config)

    sessions: dict[str, str] = {}  # chat_id -> session_id (nur Metadaten, RAM)
    only_chat = allowed_chat_id()
    offset: int | None = None

    print(f"Bot aktiv: {record.identity.handle} (Abbruch mit Strg+C)")
    audit(LOGGER, logging.INFO, "bot.started", bot=record.identity.bot_id,
          restricted=bool(only_chat))

    while _running:
        try:
            payload = get_updates(
                token.reveal(),
                offset=offset,
                poll_timeout=POLL_SECONDS,
                allowed_updates=ALLOWED_UPDATES,
                api_base=base,
            )
        except Exception as exc:  # Netz/API-Fehler: klassifiziert, ohne Inhalte
            audit(LOGGER, logging.WARNING, "bot.poll_failed", error=exc.__class__.__name__)
            time.sleep(ERROR_BACKOFF_SECONDS)
            continue

        updates = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(updates, list):
            continue

        for update in updates:
            if not isinstance(update, Mapping):
                continue
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1  # nur im RAM: kein Offset-Archiv

            parsed = extract_message(update)
            if parsed is None:
                continue
            chat_id, source = parsed
            if only_chat is not None and chat_id != only_chat:
                continue

            messages = build_reply(source, chat_id)
            if not messages:
                continue

            session = _session_for(manager, sessions, token, chat_id)
            try:
                session.send_messages(messages, source_chars=len(source))
            except Exception as exc:  # pro Nachricht: nie die Schleife beenden
                audit(LOGGER, logging.ERROR, "bot.send_failed", error=exc.__class__.__name__,
                      chat_fp=fingerprint(chat_id))
            finally:
                manager.reap_expired()

    # Geordnetes Ende: Sessions schließen, Webhook entfernen, nichts bleibt zurück.
    for session_id in list(sessions.values()):
        manager.close(session_id)
    sessions.clear()
    try:
        delete_webhook(token.reveal(), api_base=base)
    except Exception as exc:  # best effort — kein Zustand soll zurückbleiben
        audit(LOGGER, logging.WARNING, "bot.teardown_incomplete", error=exc.__class__.__name__)

    audit(LOGGER, logging.INFO, "bot.stopped", bot=record.identity.bot_id)
    print("Bot beendet — keine Daten gespeichert.")
    return 0


def _session_for(
    manager: SessionManager,
    sessions: dict[str, str],
    token: BotToken,
    chat_id: str,
) -> BotSession:
    """
    Liefert die laufende Session für einen Chat oder öffnet eine neue.

    Sessions sind ephemer: Sie verfallen nach TTL bzw. Leerlaufzeit und werden
    von :meth:`SessionManager.reap_expired` geschlossen.
    """
    session_id = sessions.get(chat_id)
    session = manager.get(session_id) if session_id else None
    if session is None:
        session = manager.open(token, chat_id)
        sessions[chat_id] = session.session_id
    return session


if __name__ == "__main__":
    raise SystemExit(run())
