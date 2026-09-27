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


def test_vault_parallel_store_fetch_is_thread_safe():
    """Audit M-7: Paralleles store/fetch/purge ohne Lock konnte Dict-Races erzeugen."""
    import threading

    from telegram_formatter.botkit.tokens import InMemoryTokenVault

    vault = InMemoryTokenVault()
    token = BotToken.parse(SECRET) if "SECRET" in globals() else BotToken.parse(
        "123456789:" + "A" * 35
    )
    handles: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(50):
                h = vault.store(token, ttl_seconds=5.0)
                handles.append(h)
                vault.fetch(h)
                vault.purge_expired()
                vault.revoke(h)
        except BaseException as exc:  # noqa: BLE001 - Smoke-Test
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(vault) == 0


# ---------------------------------------------------------------------------
# Regression v2.13.0 — Vault-Grenzen und ehrliche Zusage
# ---------------------------------------------------------------------------
class TestVaultIsBounded:
    """`store` verdrängte nichts — der Vault wuchs unbegrenzt.

    `purge_expired` wird nur von den Tests aufgerufen (der Docstring behauptete
    fälschlich, der Session-Manager rufe sie regelmäßig auf; er hat gar keinen
    Vault). Jeder Aufrufer von `store` hätte also dauerhaft einen Token mehr im
    Speicher.
    """

    def test_store_is_bounded(self):
        vault = InMemoryTokenVault(max_entries=5)
        for _ in range(50):
            vault.store(BotToken.parse(SECRET))
        assert len(vault) == 5

    def test_expired_entries_are_purged_on_store(self):
        clock = FakeClock()
        vault = InMemoryTokenVault(clock=clock, default_ttl_seconds=10.0, max_entries=100)
        for _ in range(5):
            vault.store(BotToken.parse(SECRET))
        assert len(vault) == 5
        clock.advance(11.0)  # alles abgelaufen
        vault.store(BotToken.parse(SECRET))
        assert len(vault) == 1  # der Sweep lief beim Speichern mit

    def test_oldest_is_evicted_first(self):
        clock = FakeClock()
        vault = InMemoryTokenVault(clock=clock, default_ttl_seconds=10_000.0, max_entries=3)
        handles = []
        for _ in range(5):
            clock.advance(1.0)
            handles.append(vault.store(BotToken.parse(SECRET)))
        # Die zwei ältesten Handles sind verdrängt, die drei jüngsten leben.
        assert vault.fetch(handles[0]) is None
        assert vault.fetch(handles[1]) is None
        for handle in handles[2:]:
            assert vault.fetch(handle) is not None

    def test_purge_expired_still_public(self):
        clock = FakeClock()
        vault = InMemoryTokenVault(clock=clock, default_ttl_seconds=5.0)
        vault.store(BotToken.parse(SECRET))
        clock.advance(6.0)
        assert vault.purge_expired() == 1
        assert len(vault) == 0


def test_docstring_does_not_claim_automatic_reaping():
    """Die Zusage „wird vom Session-Manager regelmäßig aufgerufen" war falsch.

    Geprüft wird nicht das Vorkommen des Wortes, sondern die **Richtung** der
    Aussage: „ruft sie regelmäßig auf" darf nur noch im verneinenden Satz
    vorkommen („Der Session-Manager ruft sie regelmäßig auf — er hat gar keinen
    Vault").
    """
    doc = InMemoryTokenVault.__doc__ or ""
    assert "Kein automatischer Reaper" in doc
    assert "**muss** selbst für den Aufruf sorgen" in doc
    # Jede Erwähnung von „regelmäßig aufgerufen" muss die Behauptung
    # ausdrücklich zurückweisen — nicht sie wiederholen.
    for line in doc.splitlines():
        if "regelmäßig aufgerufen" in line:
            assert "Das war falsch" in line or "hat gar keinen" in line, line
    # Und die Abwesenheit von Verschlüsselung wird klar benannt.
    assert "Keine Verschlüsselung" in doc


def test_no_encryption_anywhere_in_package():
    """Die Zusage lautet „nur RAM", nicht „verschlüsselt" — das ist belegbar."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "telegram_formatter"
    banned = ("fernet", "chacha", "pbkdf2", "argon2", "cryptography", "cipher")
    hits = [
        f"{p.relative_to(root)}:{i}"
        for p in root.rglob("*.py")
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if any(b in line.lower() for b in banned)
        and "grep nach" not in line
        and "null Treffer" not in line
    ]
    assert hits == [], f"Unerwartete Verschlüsselungs-Referenzen: {hits}"
