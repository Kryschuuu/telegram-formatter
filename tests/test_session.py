"""Tests für :mod:`telegram_formatter.botkit.session` — Ephemeralität, Grenzen, keine Inhalte in Logs."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from telegram_formatter.botkit.registry import BotRegistry
from telegram_formatter.botkit.review import (
    CHECKLIST_IDS,
    Reviewer,
    ReviewGate,
    ReviewLedger,
    ReviewRole,
)
from telegram_formatter.botkit.session import (
    BotSession,
    RateLimitExceeded,
    SessionConfig,
    SessionError,
    SessionExpired,
    SessionManager,
)
from telegram_formatter.botkit.tokens import BotToken

ROOT = Path(__file__).resolve().parents[1]
CLEAN_BOT = ROOT / "examples" / "own_bot" / "minimal_bot.py"

SECRET = "123456789:" + "A" * 35
CHAT_ID = "-1001234567890"
MARKDOWN = "**Fett**, *kursiv* und $E=mc^2$ — vertraulicher Inhalt 4711"


class FakeClock:
    def __init__(self) -> None:
        self.now = 5_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class RecordingSender:
    """Ersetzt ``sender.send_message`` — kein Netzwerk, protokolliert Aufrufe."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, message, secret, *, timeout=None, api_base=None) -> dict:
        self.calls.append(
            {
                "kind": message.kind,
                "payload": message.payload,
                "secret": secret,
                "timeout": timeout,
            }
        )
        return {"ok": True}


def make_registry(clock: FakeClock) -> BotRegistry:
    return BotRegistry(
        verify=lambda _secret: {
            "ok": True,
            "result": {"id": 123456789, "username": "mein_bot", "first_name": "Mein",
                       "is_bot": True},
        },
        clock=clock,
    )


ALL_CHECKS = tuple(sorted(CHECKLIST_IDS))


def approve(gate: ReviewGate, bot_id: int = 123456789) -> None:
    """Erteilt zwei unabhängige Freigaben (davon eine Maintainer:in)."""
    ticket = gate.submit(bot_id, CLEAN_BOT)
    gate.approve(ticket.ticket_id, Reviewer("alice", ReviewRole.MAINTAINER), checks=ALL_CHECKS)
    gate.approve(ticket.ticket_id, Reviewer("bob"), checks=ALL_CHECKS)


# --------------------------------------------------------------------------- #
# BotSession
# --------------------------------------------------------------------------- #
def test_session_sends_converted_messages():
    clock = FakeClock()
    sender = RecordingSender()
    session = BotSession(
        BotToken.parse(SECRET),
        CHAT_ID,
        config=SessionConfig(require_review=False),
        clock=clock,
        sender_fn=sender,
    )

    responses = session.send(MARKDOWN)

    assert len(responses) == 1
    assert len(sender.calls) == 1
    assert sender.calls[0]["secret"] == SECRET
    assert sender.calls[0]["kind"] == "rich"  # LaTeX → Rich Message
    assert sender.calls[0]["payload"]["chat_id"] == CHAT_ID
    assert session.stats.chunks_sent == 1


def test_session_logs_metadata_only(caplog):
    clock = FakeClock()
    sender = RecordingSender()
    session = BotSession(
        BotToken.parse(SECRET), CHAT_ID,
        config=SessionConfig(require_review=False), clock=clock, sender_fn=sender,
    )

    with caplog.at_level(logging.DEBUG):
        session.send(MARKDOWN)

    assert "vertraulicher Inhalt" not in caplog.text
    assert SECRET not in caplog.text
    assert CHAT_ID not in caplog.text
    assert "session.sent" in caplog.text


def test_session_repr_contains_no_secret():
    session = BotSession(BotToken.parse(SECRET), CHAT_ID)
    rendered = repr(session)
    assert SECRET not in rendered and "123456789" in rendered


def test_close_drops_the_token_and_blocks_further_use():
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(require_review=False))
    session.close()

    assert session.closed is True
    assert session.is_expired is True
    with pytest.raises(SessionError):
        session.send("text")


def test_ttl_expires_the_session():
    clock = FakeClock()
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(ttl_seconds=30.0, require_review=False), clock=clock)

    clock.advance(31.0)
    assert session.is_expired is True
    with pytest.raises(SessionExpired):
        session.send(MARKDOWN)


def test_idle_timeout_expires_the_session():
    clock = FakeClock()
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(idle_timeout_seconds=60.0, require_review=False),
                         clock=clock, sender_fn=RecordingSender())

    clock.advance(30.0)
    session.send("kurz")  # aktiv → Leerlaufzähler zurückgesetzt
    clock.advance(30.0)
    assert session.is_expired is False

    clock.advance(45.0)
    assert session.is_expired is True


def test_remaining_seconds_properties_feed_web_countdown():
    """ttl/idle_remaining_seconds: Basis für den BYOB-Web-Status (v2.2.0)."""
    clock = FakeClock()
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(ttl_seconds=100.0, idle_timeout_seconds=40.0,
                                              require_review=False),
                         clock=clock, sender_fn=RecordingSender())

    assert session.ttl_remaining_seconds == 100.0
    assert session.idle_remaining_seconds == 40.0

    clock.advance(10.0)
    session.send("aktivitaet")  # Leerlauf-Zähler zurücksetzen
    assert session.ttl_remaining_seconds == 90.0
    assert session.idle_remaining_seconds == 40.0

    clock.advance(35.0)
    assert session.ttl_remaining_seconds == 55.0
    assert session.idle_remaining_seconds == 5.0

    session.close()
    assert session.ttl_remaining_seconds == 0.0
    assert session.idle_remaining_seconds == 0.0


def test_rate_limit_protects_against_flooding():
    clock = FakeClock()
    sender = RecordingSender()
    session = BotSession(
        BotToken.parse(SECRET), CHAT_ID,
        config=SessionConfig(max_messages_per_minute=2, require_review=False),
        clock=clock, sender_fn=sender,
    )

    session.send("eins")
    session.send("zwei")
    with pytest.raises(RateLimitExceeded):
        session.send("drei")

    clock.advance(61.0)  # Fenster weitergerutscht
    session.send("vier")


def test_input_validation():
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(require_review=False, max_input_chars=10))

    with pytest.raises(SessionError):
        session.send("x" * 11)
    with pytest.raises(SessionError):
        session.send(None)  # type: ignore[arg-type]


def test_empty_input_sends_nothing():
    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(require_review=False),
                         sender_fn=RecordingSender())
    assert session.send("   ") == []
    assert session.stats.chunks_sent == 0


def test_send_errors_are_counted_and_reraised():
    from telegram_formatter.sender import SendError

    def failing_sender(message, secret, *, timeout=None, api_base=None):
        raise SendError("Telegram-API-Fehler 429")

    session = BotSession(BotToken.parse(SECRET), CHAT_ID,
                         config=SessionConfig(require_review=False),
                         sender_fn=failing_sender)
    with pytest.raises(SendError):
        session.send(MARKDOWN)
    assert session.stats.errors == 1


def test_context_manager_closes_the_session():
    with BotSession(BotToken.parse(SECRET), CHAT_ID,
                    config=SessionConfig(require_review=False)) as session:
        assert session.closed is False
    assert session.closed is True


# --------------------------------------------------------------------------- #
# SessionManager
# --------------------------------------------------------------------------- #
def test_manager_opens_session_for_registered_bot():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    manager = SessionManager(registry=registry, config=SessionConfig(require_review=False),
                             clock=clock, sender_fn=RecordingSender())
    session = manager.open(token, CHAT_ID)

    assert manager.active_count == 1
    assert manager.get(session.session_id) is session
    assert session.bot_id == 123456789


def test_manager_refuses_unregistered_bot():
    clock = FakeClock()
    manager = SessionManager(registry=make_registry(clock), config=SessionConfig(
        require_review=False), clock=clock)
    with pytest.raises(SessionError):
        manager.open(BotToken.parse(SECRET), CHAT_ID)


def test_manager_requires_review_in_hosted_mode(tmp_path):
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry)
    manager = SessionManager(registry=registry, review_gate=gate,
                             config=SessionConfig(require_review=True), clock=clock)

    with pytest.raises(SessionError):  # Bot noch nicht reviewt
        manager.open(token, CHAT_ID, source_path=str(CLEAN_BOT))

    approve(gate)
    session = manager.open(token, CHAT_ID, source_path=str(CLEAN_BOT))
    assert session.bot_id == 123456789


def test_manager_fails_closed_without_gate():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    manager = SessionManager(registry=registry, review_gate=None,
                             config=SessionConfig(require_review=True), clock=clock)
    with pytest.raises(SessionError):
        manager.open(token, CHAT_ID, source_path=str(CLEAN_BOT))


def test_manager_requires_source_path_in_review_mode():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    gate = ReviewGate(ReviewLedger(clock=FakeClock()), registry)
    approve(gate)
    manager = SessionManager(registry=registry, review_gate=gate,
                             config=SessionConfig(require_review=True), clock=clock)
    with pytest.raises(SessionError):
        manager.open(token, CHAT_ID)


def test_manager_reaps_expired_sessions():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    config = SessionConfig(ttl_seconds=10.0, require_review=False)
    manager = SessionManager(registry=registry, config=config, clock=clock,
                             sender_fn=RecordingSender())
    manager.open(token, CHAT_ID)
    assert manager.active_count == 1

    clock.advance(11.0)
    assert manager.reap_expired() == 1
    assert manager.active_count == 0


def test_manager_close_ends_session():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    manager = SessionManager(registry=registry, config=SessionConfig(require_review=False),
                             clock=clock)
    session = manager.open(token, CHAT_ID)

    assert manager.close(session.session_id) is True
    assert session.closed is True
    assert manager.close(session.session_id) is False


def test_invalid_chat_id_is_rejected_by_manager():
    clock = FakeClock()
    registry = make_registry(clock)
    token = BotToken.parse(SECRET)
    registry.register(token, owner_ref="alice")

    manager = SessionManager(registry=registry, config=SessionConfig(require_review=False),
                             clock=clock)
    with pytest.raises(SessionError):
        manager.open(token, "keine-chat-id")


# --------------------------------------------------------------------------- #
# Audit-Regressionen: Session-Lebenszyklus (Akku-Bereinigung), Thread-Safety,
# Repr-Zustand, get() entfernt Abgelaufenes
# --------------------------------------------------------------------------- #
def make_manager(clock: FakeClock, sender: RecordingSender | None = None):
    reg = make_registry(clock)
    reg.register(BotToken.parse(SECRET), owner_ref="alice")
    cfg = SessionConfig(require_review=False)
    mgr = SessionManager(registry=reg, config=cfg, clock=clock, sender_fn=sender)
    return reg, mgr


def test_context_closed_sessions_are_forgotten():
    """Audit B-13/Akku-Test: with-Block entleert den Manager sofort."""
    clock = FakeClock()
    _reg, mgr = make_manager(clock)
    for _ in range(5):
        with mgr.open(BotToken.parse(SECRET), CHAT_ID):
            pass
    assert mgr.active_count == 0


def test_get_drops_expired_session():
    clock = FakeClock()
    _reg, mgr = make_manager(clock)
    session = mgr.open(BotToken.parse(SECRET), CHAT_ID)
    sid = session.session_id
    clock.advance(100_000)  # über TTL + Idle
    assert mgr.get(sid) is None
    assert mgr.active_count == 0


def test_repr_reports_closed_state():
    clock = FakeClock()
    _reg, mgr = make_manager(clock)
    session = mgr.open(BotToken.parse(SECRET), CHAT_ID)
    session.close()
    assert "state=closed" in repr(session)


def test_manager_is_thread_safe_under_parallel_open_close():
    """Audit M-7: parallele open()/close() ohne Lock -> dict-Races."""
    import threading

    clock = FakeClock()
    _reg, mgr = make_manager(clock, sender=RecordingSender())
    errors: list[BaseException] = []

    def work() -> None:
        try:
            for _ in range(20):
                with mgr.open(BotToken.parse(SECRET), CHAT_ID):
                    pass
        except BaseException as exc:  # noqa: BLE001 - Test erfasst alles
            errors.append(exc)

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert mgr.active_count == 0


def test_get_closes_expired_session_and_drops_token():
    """v2.11.1: get() auf abgelaufener Session schließt sie (Token fällt, closed=True)."""
    clock = FakeClock()
    registry = make_registry(clock)
    registry.register(BotToken.parse(SECRET), owner_ref="alice")
    manager = SessionManager(
        registry=registry,
        config=SessionConfig(require_review=False, ttl_seconds=10.0),
        clock=clock,
    )
    session = manager.open(BotToken.parse(SECRET), CHAT_ID)
    session_id = session.session_id
    clock.advance(11.0)

    assert manager.get(session_id) is None
    assert session.closed is True
    with pytest.raises(SessionError):
        session.send("danach geht nichts mehr")
