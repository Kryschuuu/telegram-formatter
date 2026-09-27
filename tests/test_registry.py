"""Tests für :mod:`telegram_formatter.botkit.registry` — Registrierung ohne Token-Speicherung."""

from __future__ import annotations

import threading
import time

import pytest

from telegram_formatter.botkit.registry import (
    BotRegistry,
    RegistrationError,
    RegistrationStatus,
    validate_chat_id,
    validate_owner_ref,
)
from telegram_formatter.botkit.session import BotSession, RateLimitExceeded
from telegram_formatter.botkit.tokens import BotToken

SECRET = "123456789:" + "A" * 35


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def fake_verify(bot_id: int = 123456789, *, is_bot: bool = True, username: str = "mein_bot"):
    """Ersetzt ``getMe`` — liefert eine plausible Bot-Identität."""

    def _verify(secret: str) -> dict:
        assert secret == SECRET  # nur dieses Token ist in den Tests gültig
        return {
            "ok": True,
            "result": {
                "id": bot_id,
                "username": username,
                "first_name": "Mein Bot",
                "is_bot": is_bot,
            },
        }

    return _verify


def make_registry(**kwargs) -> BotRegistry:
    return BotRegistry(verify=fake_verify(**kwargs), clock=FakeClock())


# --------------------------------------------------------------------------- #
# Validierung
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("owner", ["alice", "octo-cat", "a.b_c-1"])
def test_valid_owner_refs(owner):
    assert validate_owner_ref(owner) == owner


@pytest.mark.parametrize("owner", ["", "a", "alice@example.com", "Alice Müller", "x" * 65])
def test_invalid_owner_refs(owner):
    with pytest.raises(RegistrationError):
        validate_owner_ref(owner)


@pytest.mark.parametrize("chat_id", ["-1001234567890", 4711, "-4711"])
def test_valid_chat_ids(chat_id):
    assert validate_chat_id(chat_id) == str(chat_id)


@pytest.mark.parametrize("chat_id", ["", "abc", "12; DROP", "10.5", None])
def test_invalid_chat_ids(chat_id):
    with pytest.raises(RegistrationError):
        validate_chat_id(chat_id)


# --------------------------------------------------------------------------- #
# Registrierung
# --------------------------------------------------------------------------- #
def test_register_creates_pending_record():
    registry = make_registry()
    record = registry.register(BotToken.parse(SECRET), owner_ref="alice")

    assert record.identity.bot_id == 123456789
    assert record.identity.handle == "@mein_bot"
    assert record.status is RegistrationStatus.PENDING
    assert record.approved_source_sha256 is None
    assert registry.is_approved(123456789) is False


def test_registry_never_stores_the_secret():
    registry = make_registry()
    token = BotToken.parse(SECRET)
    record = registry.register(token, owner_ref="alice")

    serialised = f"{record!r} {registry.__dict__!r} {record.__dict__!r}"
    assert SECRET not in serialised
    assert record.owner_fingerprint in serialised  # nur Pseudonym-Fingerprint


def test_register_rejects_non_bot_tokens():
    registry = make_registry(is_bot=False)
    with pytest.raises(RegistrationError):
        registry.register(BotToken.parse(SECRET), owner_ref="alice")


def test_register_rejects_identity_mismatch():
    """getMe-ID und Token-Präfix müssen denselben Bot meinen."""
    registry = make_registry(bot_id=999999)
    with pytest.raises(RegistrationError):
        registry.register(BotToken.parse(SECRET), owner_ref="alice")


def test_register_rejects_verification_errors():
    def boom(_secret: str) -> dict:
        raise RuntimeError("Netzwerk weg")

    registry = BotRegistry(verify=boom, clock=FakeClock())
    with pytest.raises(RegistrationError):
        registry.register(BotToken.parse(SECRET), owner_ref="alice")


def test_register_rejects_missing_result():
    registry = BotRegistry(verify=lambda _secret: {"ok": True}, clock=FakeClock())
    with pytest.raises(RegistrationError):
        registry.register(BotToken.parse(SECRET), owner_ref="alice")


# --------------------------------------------------------------------------- #
# Lebenszyklus
# --------------------------------------------------------------------------- #
def test_registration_expires_and_is_purged():
    clock = FakeClock()
    registry = BotRegistry(verify=fake_verify(), clock=clock, ttl_seconds=60.0)
    registry.register(BotToken.parse(SECRET), owner_ref="alice")

    clock.advance(61.0)
    assert registry.get(123456789) is None
    assert registry.purge_expired() == 0  # get() räumt bereits auf

    registry.register(BotToken.parse(SECRET), owner_ref="alice")
    clock.advance(61.0)
    assert registry.purge_expired() == 1
    assert registry.active_bot_ids() == []


def test_approval_lifecycle():
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="alice")

    registry.mark_approved(123456789, "a" * 64)
    assert registry.is_approved(123456789) is True

    registry.mark_rejected(123456789, "Checkliste unvollständig")
    assert registry.is_approved(123456789) is False
    assert registry.get(123456789).status is RegistrationStatus.REJECTED

    registry.mark_approved(123456789, "b" * 64)
    assert registry.revoke(123456789) is True
    # v2.13.0: `revoke()` löscht den Eintrag nicht mehr, sondern setzt
    # REVOKED. Vorher wurde der Status gesetzt und im nächsten Schritt
    # gelöscht — er war nicht beobachtbar, und der Audit-Unterschied zwischen
    # „nie bekannt" und „kannte das Team und hat es widerrufen" ging verloren.
    revoked = registry.get(123456789)
    assert revoked is not None
    assert revoked.status is RegistrationStatus.REVOKED
    assert revoked.approved_source_sha256 is None
    assert registry.is_approved(123456789) is False
    assert registry.revoke(123456789) is True  # idempotent


def test_revoke_keeps_record_observable():
    """REVOKED ist von REJECTED und von "unbekannt" unterscheidbar."""
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="web")
    registry.mark_rejected(123456789, "Checkliste unvollständig")
    assert registry.get(123456789).status is RegistrationStatus.REJECTED
    assert registry.revoke(123456789) is True
    assert registry.get(123456789).status is RegistrationStatus.REVOKED


def test_reregistration_preserves_approval():
    """Regression v2.13.0: `register()` setzte jede bestehende Freigabe zurück.

    Vorher baute `register()` einen frischen `RegistrationRecord` (status
    PENDING, `approved_source_sha256=None`, `notes=[]`) und überschrieb damit
    den alten. Registrieren ist der Normalpfad beider Aufrufer — eine
    Freigabe, die an eine Code-Prüfsumme gebunden ist, wäre bei jedem zweiten
    `open()` stillschweigend hinfällig gewesen.
    """
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="web")
    registry.mark_approved(123456789, "a" * 64)
    assert registry.is_approved(123456789) is True

    # Erneute Registrierung (neuer Besitzer) darf die Freigabe nicht löschen.
    registry.register(BotToken.parse(SECRET), owner_ref="cli")
    assert registry.is_approved(123456789) is True
    assert registry.get(123456789).approved_source_sha256 == "a" * 64
    # Der Besitzerwechsel ist trotzdem übernommen.
    assert registry.get(123456789).owner_ref == "cli"

    # Geänderter Code invalidiert weiterhin die Freigabe.
    assert registry.get(123456789).approved_source_sha256 != "b" * 64


def test_reregistration_refreshes_expiry():
    """`register()` verlängert die Gültigkeit auch beim in-place-Update."""
    now = [1000.0]
    registry = BotRegistry(verify=fake_verify(), clock=lambda: now[0], ttl_seconds=100.0)
    registry.register(BotToken.parse(SECRET), owner_ref="web")
    assert registry.active_bot_ids() == [123456789]
    now[0] += 80.0
    registry.register(BotToken.parse(SECRET), owner_ref="web")
    assert registry.active_bot_ids() == [123456789]
    now[0] += 30.0  # wäre ohne Refresh abgelaufen
    assert registry.active_bot_ids() == [123456789]


def test_purge_expired_after_revoke_keeps_revoked():
    """Ein widerrufener, noch nicht abgelaufener Record überlebt `purge_expired`."""
    registry = make_registry()
    registry.register(BotToken.parse(SECRET), owner_ref="web")
    registry.revoke(123456789)
    assert registry.purge_expired() == 0
    assert registry.get(123456789) is not None


def test_concurrent_register_and_purge_do_not_raise():
    """Regression v2.13.0: `purge_expired()` lief neben `register()`.

    Vorher iterierte die Methode über `self._records.items()`, während ein
    anderer Thread darin `del` ausführte — `RuntimeError: dictionary changed
    size during iteration`.
    """
    registry = BotRegistry(verify=fake_verify(), ttl_seconds=0.0)  # alles sofort abgelaufen
    errors: list[BaseException] = []

    def register_many() -> None:
        try:
            for _ in range(60):
                registry.register(BotToken.parse(SECRET), owner_ref="web")
        except BaseException as exc:  # pragma: no cover - nur im Fehlerfall
            errors.append(exc)

    def purge_many() -> None:
        try:
            for _ in range(60):
                registry.purge_expired()
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=register_many) for _ in range(2)]
    threads += [threading.Thread(target=purge_many) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_concurrent_sends_cannot_overshoot_rate_budget():
    """Regression v2.13.0: `_reserve_rate_budget` war kein atomares Read-Modify-Write.

    Unter `--workers 1 --threads 8` konnten zwei parallele Sendungen beide
    `len(...) == 5` lesen und beide die Prüfung für `5 + 12 > 20` passieren —
    das 20-Nachrichten-pro-Minute-Limit war damit umgehbar.
    """
    limit = 12
    per_call = 5
    calls = 8

    session = BotSession.__new__(BotSession)  # nur die Buchung testen
    session._config = type("C", (), {"max_messages_per_minute": limit})()
    session._sent_timestamps = []
    session._clock = time.monotonic
    session._lock = threading.RLock()  # v2.13.0 — der Fix

    errors: list[BaseException] = []
    rejected: list[str] = []

    def worker() -> None:
        try:
            session._reserve_rate_budget(per_call)
        except RateLimitExceeded:
            rejected.append("limit")

    threads = [threading.Thread(target=worker) for _ in range(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    # Höchstens `limit // per_call` Reservierungen dürfen durchgehen; alle
    # restlichen müssen abgewiesen werden. Ohne Lock hätten alle 8 Threads
    # `len(...) == 0` gelesen und wären durchgegangen.
    assert len(session._sent_timestamps) == limit - limit % per_call
    assert len(rejected) == calls - len(session._sent_timestamps) // per_call


def test_rate_budget_rejects_overshoot():
    """`_reserve_rate_budget` weist das Überschreiten ab und bucht nichts."""
    session = BotSession.__new__(BotSession)
    session._config = type("C", (), {"max_messages_per_minute": 10})()
    session._sent_timestamps = []
    session._clock = time.monotonic
    session._lock = threading.RLock()

    session._reserve_rate_budget(10)
    assert len(session._sent_timestamps) == 10
    with pytest.raises(RateLimitExceeded):
        session._reserve_rate_budget(1)
    assert len(session._sent_timestamps) == 10  # unverändert


def test_status_changes_require_a_known_bot():
    registry = make_registry()
    with pytest.raises(RegistrationError):
        registry.mark_approved(1, "a" * 64)
    with pytest.raises(RegistrationError):
        registry.mark_rejected(1, "grund")


def test_register_rejects_non_numeric_getme_id():
    """v2.11.1: Fremde getMe-Antwort ohne numerische ID meldet RegistrationError (kein ValueError)."""
    registry = BotRegistry(
        verify=lambda _secret: {
            "ok": True,
            "result": {"is_bot": True, "id": "keine-zahl", "username": "x", "first_name": "y"},
        },
        clock=FakeClock(),
    )
    with pytest.raises(RegistrationError, match="keine Zahl"):
        registry.register(BotToken.parse(SECRET), owner_ref="alice")
