"""Tests für :mod:`telegram_formatter.botkit.tokens` — Formatvalidierung, Redaction, Vault-TTL."""

from __future__ import annotations

import pytest

from telegram_formatter.botkit.tokens import (
    BotToken,
    InMemoryTokenVault,
    PassthroughTokenVault,
    TokenError,
    VaultError,
)

SECRET = "123456789:" + "A" * 35


class FakeClock:
    """Manuell steuerbare Uhr (monoton) für TTL-Tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# --------------------------------------------------------------------------- #
# BotToken
# --------------------------------------------------------------------------- #
def test_parse_valid_token():
    token = BotToken.parse(SECRET)
    assert token.bot_id == 123456789
    assert token.reveal() == SECRET


def test_parse_trims_surrounding_whitespace():
    assert BotToken.parse(f"  {SECRET}\n").bot_id == 123456789


@pytest.mark.parametrize(
    "candidate",
    [
        "",
        "123456789",  # ohne Secret-Teil
        "123456789:" + "A" * 34,  # Secret zu kurz
        "123:ABC",  # deutlich zu kurz
        "123456789:" + "A" * 35 + ":extra",
        "abcdef:" + "A" * 35,  # Bot-ID nicht numerisch
    ],
)
def test_invalid_tokens_are_rejected(candidate):
    with pytest.raises(TokenError):
        BotToken.parse(candidate)


def test_repr_and_str_never_leak_the_secret():
    token = BotToken.parse(SECRET)
    for rendered in (repr(token), str(token), f"{token}", f"{token!r}"):
        assert SECRET not in rendered
        assert "123456789" in rendered  # Bot-ID ist nicht geheim


def test_reveal_is_counted_and_returns_the_secret():
    token = BotToken.parse(SECRET)
    assert token.reveal_count == 0
    token.reveal()
    token.reveal()
    assert token.reveal_count == 2


def test_fingerprint_is_stable_and_secret_free():
    first = BotToken.parse(SECRET)
    second = BotToken.parse(SECRET)
    other = BotToken.parse("987654321:" + "B" * 35)
    assert first.fingerprint == second.fingerprint  # gleicher Prozess, gleicher Wert
    assert first.fingerprint != other.fingerprint
    assert SECRET not in first.fingerprint


def test_equality_ignores_object_identity():
    assert BotToken.parse(SECRET) == BotToken.parse(SECRET)
    assert BotToken.parse(SECRET) != BotToken.parse("987654321:" + "B" * 35)
    assert BotToken.parse(SECRET) != SECRET  # kein Vergleich mit Rohtext


def test_from_environment(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", SECRET)
    assert BotToken.from_environment().bot_id == 123456789

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    with pytest.raises(TokenError):
        BotToken.from_environment()


# --------------------------------------------------------------------------- #
# Vaults
# --------------------------------------------------------------------------- #
def test_in_memory_vault_store_fetch_and_revoke():
    clock = FakeClock()
    vault = InMemoryTokenVault(clock=clock, default_ttl_seconds=60.0)
    token = BotToken.parse(SECRET)

    handle = vault.store(token)
    assert len(handle) >= 32  # 256-Bit-Zufallswert
    assert vault.fetch(handle) == token

    assert vault.revoke(handle) is True
    assert vault.fetch(handle) is None
    assert vault.revoke(handle) is False


def test_in_memory_vault_expires_and_purges():
    clock = FakeClock()
    vault = InMemoryTokenVault(clock=clock, default_ttl_seconds=60.0)
    handle = vault.store(BotToken.parse(SECRET))

    clock.advance(59.0)
    assert vault.fetch(handle) is not None

    clock.advance(2.0)  # TTL überschritten -> fail-closed
    assert vault.fetch(handle) is None
    assert vault.purge_expired() == 0  # fetch() hat bereits gelöscht

    handle2 = vault.store(BotToken.parse(SECRET), ttl_seconds=5.0)
    clock.advance(6.0)
    assert vault.purge_expired() == 1
    assert len(vault) == 0
    assert handle2 not in ()


def test_passthrough_vault_never_stores():
    vault = PassthroughTokenVault()
    with pytest.raises(VaultError):
        vault.store(BotToken.parse(SECRET))
    assert vault.fetch("irgendwas") is None
    assert vault.revoke("irgendwas") is False
    assert vault.purge_expired() == 0
