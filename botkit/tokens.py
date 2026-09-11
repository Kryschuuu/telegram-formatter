"""
botkit/tokens.py
================
Sicheres Handling von Telegram-Bot-Tokens.

Regeln, die dieses Modul erzwingt:

1. **Umschlagpflicht.** Ein Token liegt nie als nackter ``str`` in der
   Anwendungslogik, sondern in :class:`BotToken`. Der Klartext ist nur über
   :meth:`BotToken.reveal` erreichbar — eine aufrufende Stelle, die im
   Code-Review sofort auffällt.
2. **Kein versehentliches Leaken.** ``repr``, ``str``, f-Strings und
   Exceptions geben nur ``bot_id`` und :func:`~botkit.privacy.fingerprint`
   aus, niemals das Geheimnis.
3. **Keine Persistenz.** Ablage ausschließlich im RAM
   (:class:`InMemoryTokenVault`, TTL-begrenzt) oder gar nicht
   (:class:`PassthroughTokenVault`). Es gibt bewusst *keine* Implementierung,
   die auf die Festplatte schreibt.
4. **Validierung vor Verwendung.** :meth:`BotToken.parse` prüft das Format,
   die Registrierung prüft anschließend per ``getMe``, dass Token und
   Bot-Identität zusammenpassen.
5. **Keine Shell-Spuren.** :meth:`BotToken.from_getpass` liest Tokens ohne
   Shell-Historie und ohne Klartexteingabe im Terminal ein.
"""

from __future__ import annotations

import os
import re
import secrets
import time
from collections.abc import Callable
from typing import Protocol

from .privacy import fingerprint

__all__ = [
    "TOKEN_PATTERN",
    "BotToken",
    "InMemoryTokenVault",
    "PassthroughTokenVault",
    "TokenError",
    "TokenVault",
    "VaultEntry",
    "VaultError",
]

#: Format eines Bot-Tokens: numerische Bot-ID, Doppelpunkt, 35 Zeichen Secret.
TOKEN_PATTERN = re.compile(r"^\d{5,16}:[A-Za-z0-9_-]{35}$")

#: Standard-Env-Variable (kompatibel zum restlichen Projekt).
DEFAULT_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"


class TokenError(ValueError):
    """Ungültiges, falsch formatiertes oder abgelaufenes Bot-Token."""


class VaultError(RuntimeError):
    """Verstoß gegen die Speicherregeln des Token-Vaults."""


class BotToken:
    """
    Umschlag für ein Bot-Token.

    >>> token = BotToken.parse("123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345")  # doctest: +SKIP
    >>> token.bot_id                                                            # doctest: +SKIP
    123456789
    >>> print(token)                                                            # doctest: +SKIP
    <BotToken bot_id=123456789 fp=9f1c2b… redacted>
    """

    __slots__ = ("_bot_id", "_reveal_count", "_secret")

    def __init__(self, secret: str) -> None:
        raw = (secret or "").strip()
        if not TOKEN_PATTERN.match(raw):
            raise TokenError(
                "Ungültiges Bot-Token-Format. Erwartet wird '<bot_id>:<35 Zeichen>' "
                "von @BotFather — keine Leerzeichen, keine Anführungszeichen."
            )
        self._secret = raw
        self._bot_id = int(raw.split(":", 1)[0])
        self._reveal_count = 0

    # ------------------------------------------------------------------ Bau
    @classmethod
    def parse(cls, secret: str) -> BotToken:
        """Baut ein Token aus einem Rohtext (trimmt, validiert)."""
        return cls(secret)

    @classmethod
    def from_environment(cls, name: str = DEFAULT_TOKEN_ENV) -> BotToken:
        """
        Liest das Token aus einer Umgebungsvariablen.

        Hinweis: Für dauerhaft laufende Server ist das nur die
        Kompatibilitätsvariante. Besser ist die Übergabe pro Session, damit
        das Token nicht über die gesamte Prozesslaufzeit im Environment
        (und damit in ``/proc/<pid>/environ``) steht.
        """
        raw = os.environ.get(name, "")
        if not raw:
            raise TokenError(f"Umgebungsvariable {name} ist nicht gesetzt.")
        return cls(raw)

    @classmethod
    def from_getpass(cls, prompt: str = "Bot-Token (Eingabe unsichtbar): ") -> BotToken:
        """
        Liest das Token interaktiv ein — keine Shell-Historie, kein Klartext
        im Terminal, kein Environment-Eintrag.
        """
        from getpass import getpass  # lazy: nur im interaktiven Pfad nötig

        return cls(getpass(prompt))

    # -------------------------------------------------------------- Zugriff
    @property
    def bot_id(self) -> int:
        """Numerische Bot-ID (nicht geheim, darf geloggt werden)."""
        return self._bot_id

    @property
    def fingerprint(self) -> str:
        """Prozesslokaler Fingerprint — der einzige logbare Token-Bezug."""
        return fingerprint(self._secret)

    @property
    def reveal_count(self) -> int:
        """
        Wie oft wurde das Geheimnis angefordert?

        Nützlich für Tests und Reviews: Ein Bot-Client sollte das Token pro
        API-Aufruf *einmal* anfordern, nicht dutzendfach in Schleifen.
        """
        return self._reveal_count

    def reveal(self) -> str:
        """
        Gibt das Geheimnis zurück — einziger dokumentierter Ausgang.

        Aufrufstellen müssen im Review begründet sein ( Ziel: genau dort, wo
        die Telegram-API aufgerufen wird, sonst nirgends).
        """
        self._reveal_count += 1
        return self._secret

    # ----------------------------------------------------------- Darstellung
    def __repr__(self) -> str:
        return f"<BotToken bot_id={self._bot_id} fp={self.fingerprint} redacted>"

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BotToken):
            return NotImplemented
        # Zeitkonstanter Vergleich, damit Timing-Seitenkanäle ausscheiden.
        return secrets.compare_digest(self._secret, other._secret)

    def __hash__(self) -> int:
        # Nicht vom Geheimnis abgeleitet: Hashes landen potenziell in Logs.
        return hash(("BotToken", self._bot_id, self.fingerprint))


class VaultEntry:
    """Metadaten eines abgelegten Tokens — ohne das Geheimnis selbst."""

    __slots__ = ("bot_id", "created_at", "expires_at", "handle", "token_fingerprint")

    def __init__(
        self,
        handle: str,
        bot_id: int,
        token_fingerprint: str,
        created_at: float,
        expires_at: float,
    ) -> None:
        self.handle = handle
        self.bot_id = bot_id
        self.token_fingerprint = token_fingerprint
        self.created_at = created_at
        self.expires_at = expires_at

    def __repr__(self) -> str:
        return (
            f"<VaultEntry bot_id={self.bot_id} fp={self.token_fingerprint} "
            f"expires_in={max(0.0, self.expires_at - self.created_at):.0f}s>"
        )


class TokenVault(Protocol):
    """Schnittstelle für die (ausschließlich flüchtige) Token-Ablage."""

    def store(self, token: BotToken, *, ttl_seconds: float) -> str:
        """Legt ein Token ab und liefert ein opakes Handle zurück."""
        ...

    def fetch(self, handle: str) -> BotToken | None:
        """Gibt das Token zurück oder ``None`` (unbekannt/abgelaufen)."""
        ...

    def revoke(self, handle: str) -> bool:
        """Löscht das Token sofort. ``True``, wenn es existierte."""
        ...

    def purge_expired(self) -> int:
        """Räumt abgelaufene Einträge ab, liefert die Anzahl."""
        ...


class InMemoryTokenVault:
    """
    Token-Ablage **nur im RAM** — mit harter TTL und sofortigem Revoke.

    Eigenschaften:

    * kein Dateisystem, keine Datenbank, kein Redis, kein Log;
    * Handles sind 256-Bit-Zufallswerte (nicht erratbar, nicht ableitbar);
    * die TTL ist hart: :meth:`fetch` verlängert sie *nicht*, eine Session
      muss sich bei Bedarf neu registrieren (Fail-Closed statt Komfort);
    * :meth:`purge_expired` wird vom Session-Manager regelmäßig aufgerufen.

    Geeignet für den gehosteten Modus, in dem das Token einmalig übergeben
    und danach nur noch über ein HttpOnly-Cookie-Handle referenziert wird.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        default_ttl_seconds: float = 900.0,
    ) -> None:
        self._clock = clock
        self._default_ttl = default_ttl_seconds
        self._entries: dict[str, tuple[VaultEntry, BotToken]] = {}

    def store(self, token: BotToken, *, ttl_seconds: float | None = None) -> str:
        ttl = self._default_ttl if ttl_seconds is None else ttl_seconds
        now = self._clock()
        handle = secrets.token_urlsafe(32)
        entry = VaultEntry(
            handle=handle,
            bot_id=token.bot_id,
            token_fingerprint=token.fingerprint,
            created_at=now,
            expires_at=now + ttl,
        )
        self._entries[handle] = (entry, token)
        return handle

    def fetch(self, handle: str) -> BotToken | None:
        item = self._entries.get(handle)
        if item is None:
            return None
        entry, token = item
        if self._clock() >= entry.expires_at:
            del self._entries[handle]  # fail-closed: abgelaufen = gelöscht
            return None
        return token

    def revoke(self, handle: str) -> bool:
        return self._entries.pop(handle, None) is not None

    def purge_expired(self) -> int:
        now = self._clock()
        expired = [h for h, (entry, _) in self._entries.items() if now >= entry.expires_at]
        for handle in expired:
            del self._entries[handle]
        return len(expired)

    def __len__(self) -> int:
        return len(self._entries)


class PassthroughTokenVault:
    """
    Strengster Modus: der Vault speichert **nichts**.

    Einsatz: lokaler/self-hosted Betrieb und CLI. Das Token bleibt im
    Request- bzw. Session-Objekt (RAM) und wird nach der Session verworfen;
    es gibt kein Handle, das gestohlen oder verlängert werden könnte.

    :meth:`store` wirft absichtlich einen :class:`VaultError`: Wer im
    strengen Modus persistiert, soll laut scheitern statt still zu
    degradieren.
    """

    def store(self, token: BotToken, *, ttl_seconds: float | None = None) -> str:
        raise VaultError(
            "PassthroughTokenVault speichert keine Tokens. Token im "
            "Session-Objekt halten (botkit.session.BotSession) statt im Vault."
        )

    def fetch(self, handle: str) -> BotToken | None:
        return None

    def revoke(self, handle: str) -> bool:
        return False

    def purge_expired(self) -> int:
        return 0
