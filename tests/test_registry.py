"""Tests für :mod:`telegram_formatter.botkit.registry` — Registrierung ohne Token-Speicherung."""

from __future__ import annotations

import pytest

from telegram_formatter.botkit.registry import (
    BotRegistry,
    RegistrationError,
    RegistrationStatus,
    validate_chat_id,
    validate_owner_ref,
)
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
    assert registry.get(123456789) is None
    assert registry.revoke(123456789) is False


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
