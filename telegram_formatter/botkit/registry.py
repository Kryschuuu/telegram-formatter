"""
telegram_formatter/botkit/registry.py
=====================================
Registrierung eigener Bots — **ohne** Speicherung des Tokens.

Der Registry-Eintrag beschreibt *wer* einen Bot besitzt und *welchen Status*
er hat. Er enthält bewusst niemals:

* das Bot-Token (nur dessen :func:`~botkit.privacy.fingerprint`),
* Chat-IDs oder Nachrichteninhalte,
* Klarnamen oder E-Mail-Adressen (nur ein Pseudonym, z. B. GitHub-Handle).

Ein typischer Ablauf::

    registry = BotRegistry(verify=lambda secret: get_me(secret))
    record = registry.register(token, owner_ref="alice")   # Status: PENDING
    # ... Review (siehe botkit.review) ...
    registry.mark_approved(record.identity.bot_id, source_sha256)

Danach darf :class:`botkit.session.BotSession` den Bot in einer Session
verwenden — vorausgesetzt, der Aufrufende legt das Token erneut vor.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum

from .privacy import audit, fingerprint
from .tokens import BotToken

__all__ = [
    "CHAT_ID_PATTERN",
    "OWNER_REF_PATTERN",
    "BotIdentity",
    "BotRegistry",
    "RegistrationError",
    "RegistrationRecord",
    "RegistrationStatus",
    "validate_chat_id",
    "validate_owner_ref",
]

LOGGER = logging.getLogger("botkit.registry")

#: Signatur der Verifikationsfunktion: Secret → getMe-Antwort (injizierbar).
VerifyFn = Callable[[str], Mapping[str, object]]

#: Pseudonym des Besitzers (GitHub-Handle o. Ä.) — absichtlich keine E-Mail.
OWNER_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")

#: Telegram-Chat-IDs: Ganzzahl, optional mit Präfix "-100" für Kanäle/Gruppen.
CHAT_ID_PATTERN = re.compile(r"^-?\d{1,32}$")


class RegistrationError(RuntimeError):
    """Registrierung abgelehnt (Verifikation, Validierung oder Status)."""


class RegistrationStatus(str, Enum):
    """Lebenszyklus einer Registrierung."""

    PENDING = "pending"  # registriert, wartet auf Review
    APPROVED = "approved"  # freigegeben für Session-Nutzung
    REJECTED = "rejected"  # Review abgelehnt
    REVOKED = "revoked"  # vom Besitzer oder Maintainer entzogen


@dataclass(frozen=True)
class BotIdentity:
    """Nicht-geheime Identität eines Bots (Ergebnis von ``getMe``)."""

    bot_id: int
    username: str
    display_name: str
    verified_at: float

    @property
    def handle(self) -> str:
        """``@mein_bot`` bzw. die ID, falls kein Benutzername gesetzt ist."""
        return f"@{self.username}" if self.username else f"id:{self.bot_id}"

    def __str__(self) -> str:
        return f"{self.display_name} ({self.handle})"


@dataclass
class RegistrationRecord:
    """Registry-Eintrag: Identität, Besitzer-Pseudonym, Review-Status."""

    identity: BotIdentity
    owner_ref: str
    owner_fingerprint: str
    status: RegistrationStatus = RegistrationStatus.PENDING
    approved_source_sha256: str | None = None
    registered_at: float = 0.0
    expires_at: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def bot_id(self) -> int:
        return self.identity.bot_id


def validate_owner_ref(owner_ref: str) -> str:
    """
    Prüft das Besitzer-Pseudonym.

    Erlaubt sind Handles wie ``alice`` oder ``octo-cat.dev``. Abgelehnt
    werden E-Mail-Adressen, Leerzeichen und Sonderzeichen — die Registry
    soll gar nicht erst personenbeziehbare Daten aufnehmen können.
    """
    candidate = (owner_ref or "").strip()
    if not OWNER_REF_PATTERN.match(candidate):
        raise RegistrationError(
            "owner_ref muss ein Pseudonym sein: 3-64 Zeichen aus A-Z, a-z, 0-9, "
            "'.', '_' oder '-' (keine E-Mail, kein Klarname)."
        )
    return candidate


def validate_chat_id(chat_id: int | str) -> str:
    """
    Prüft eine Chat-ID und gibt sie normalisiert als String zurück.

    Ablehnung von Nicht-Zahlen schützt vor Injection in Payloads
    (``chat_id`` landet 1:1 im JSON-Body der Telegram-API).
    """
    candidate = str(chat_id).strip()
    if not CHAT_ID_PATTERN.match(candidate):
        raise RegistrationError(
            "chat_id muss eine Ganzzahl sein (z. B. -1001234567890 oder 4711)."
        )
    return candidate


class BotRegistry:
    """
    In-Memory-Registry für eigene Bots.

    Die Registry ist pro Prozess flüchtig: Ein Neustart vergisst alle
    Einträge, weil es keine Persistenzschicht gibt (und geben soll). Für den
    gehosteten Betrieb ist das gewollt — Sessions werden neu aufgebaut, statt
    alte Identitäten wiederzubeleben.
    """

    def __init__(
        self,
        *,
        verify: VerifyFn,
        clock: Callable[[], float] = time.time,
        ttl_seconds: float = 86_400.0,
    ) -> None:
        self._verify = verify
        self._clock = clock
        self._ttl = ttl_seconds
        self._records: dict[int, RegistrationRecord] = {}

    # ------------------------------------------------------------ Registrierung
    def register(self, token: BotToken, *, owner_ref: str) -> RegistrationRecord:
        """
        Verifiziert das Token gegen Telegram und legt den Eintrag an.

        Schritte:

        1. ``getMe`` mit dem Secret (einzige Verwendung des Klartext-Tokens);
        2. Plausibilitätsprüfung: ``is_bot`` und ``id == token.bot_id``;
        3. Ablage **ohne** Secret — nur Identität, Pseudonym, Fingerprint.

        :raises RegistrationError: wenn Telegram das Token ablehnt, die
            Antwort nicht zu einem Bot gehört oder die IDs nicht übereinstimmen.
        """
        owner = validate_owner_ref(owner_ref)

        try:
            payload = self._verify(token.reveal())
        except Exception as exc:
            # Kein Secret in der Meldung; Grund wird nur klassifiziert.
            audit(
                LOGGER,
                logging.WARNING,
                "registry.verify_failed",
                bot=token.bot_id,
                error=exc.__class__.__name__,
            )
            raise RegistrationError(
                f"Token konnte nicht gegen Telegram verifiziert werden ({exc.__class__.__name__})."
            ) from None

        result = payload.get("result") if isinstance(payload, Mapping) else None
        if not isinstance(result, Mapping):
            raise RegistrationError("Unerwartete getMe-Antwort: 'result' fehlt.")
        if not result.get("is_bot"):
            raise RegistrationError("Das Token gehört nicht zu einem Bot.")
        try:
            reported_id = int(result.get("id", -1))
        except (TypeError, ValueError):
            # Fremde Netzantwort, fremdes Format — kein roher ValueError (v2.11.1).
            raise RegistrationError("Unerwartete getMe-Antwort: 'id' ist keine Zahl.") from None
        if reported_id != token.bot_id:
            # Schutz vor Verwechslung: Antwort und Token müssen denselben Bot meinen.
            raise RegistrationError("getMe-ID stimmt nicht mit dem Token überein.")

        now = self._clock()
        identity = BotIdentity(
            bot_id=reported_id,
            username=str(result.get("username", "")),
            display_name=str(result.get("first_name", "")),
            verified_at=now,
        )
        record = RegistrationRecord(
            identity=identity,
            owner_ref=owner,
            owner_fingerprint=fingerprint(owner),
            registered_at=now,
            expires_at=now + self._ttl,
        )
        self._records[identity.bot_id] = record

        audit(
            LOGGER,
            logging.INFO,
            "registry.registered",
            bot=identity.bot_id,
            handle=identity.handle,
            owner_fp=record.owner_fingerprint,
            status=record.status.value,
            token_fp=token.fingerprint,
        )
        return record

    # ------------------------------------------------------------------ Lesen
    def get(self, bot_id: int) -> RegistrationRecord | None:
        """Liefert den Eintrag oder ``None`` (unbekannt/abgelaufen)."""
        record = self._records.get(int(bot_id))
        if record is None:
            return None
        if self._clock() >= record.expires_at:
            del self._records[record.bot_id]
            return None
        return record

    def is_approved(self, bot_id: int) -> bool:
        """``True`` nur bei Status APPROVED *und* nicht abgelaufener Registrierung."""
        record = self.get(bot_id)
        return record is not None and record.status is RegistrationStatus.APPROVED

    def active_bot_ids(self) -> list[int]:
        """Alle aktuell bekannten Bot-IDs (ohne abgelaufene)."""
        now = self._clock()
        return [bid for bid, rec in self._records.items() if now < rec.expires_at]

    # --------------------------------------------------------------- Status
    def mark_approved(self, bot_id: int, source_sha256: str) -> RegistrationRecord:
        """
        Gibt den Bot für Session-Nutzung frei.

        Die Freigabe wird an die **Prüfsumme des reviewten Codes** gebunden:
        Ändert sich der Bot-Code, ist die Freigabe automatisch hinfällig.
        """
        record = self._require(bot_id)
        record.status = RegistrationStatus.APPROVED
        record.approved_source_sha256 = source_sha256
        record.notes.append(f"approved: {source_sha256[:12]}")
        audit(LOGGER, logging.INFO, "registry.approved", bot=bot_id, source=source_sha256[:12])
        return record

    def mark_rejected(self, bot_id: int, reason: str) -> RegistrationRecord:
        """Lehnt den Bot ab (Grund wird als Metadatum, nicht als Inhalt geführt)."""
        record = self._require(bot_id)
        record.status = RegistrationStatus.REJECTED
        record.approved_source_sha256 = None
        record.notes.append(f"rejected: {reason[:200]}")
        audit(LOGGER, logging.INFO, "registry.rejected", bot=bot_id, reason=reason[:120])
        return record

    def revoke(self, bot_id: int) -> bool:
        """Entzieht die Registrierung (Besitzer-Logout oder Maintainer-Entscheid)."""
        record = self._records.get(int(bot_id))
        if record is None:
            return False
        record.status = RegistrationStatus.REVOKED
        record.approved_source_sha256 = None
        del self._records[int(bot_id)]
        audit(LOGGER, logging.INFO, "registry.revoked", bot=bot_id)
        return True

    def purge_expired(self) -> int:
        """Entfernt abgelaufene Einträge, liefert die Anzahl."""
        now = self._clock()
        expired = [bid for bid, rec in self._records.items() if now >= rec.expires_at]
        for bot_id in expired:
            del self._records[bot_id]
        if expired:
            audit(LOGGER, logging.INFO, "registry.purged", count=len(expired))
        return len(expired)

    # -------------------------------------------------------------- Interna
    def _require(self, bot_id: int) -> RegistrationRecord:
        record = self.get(int(bot_id))
        if record is None:
            raise RegistrationError(
                f"Bot {bot_id} ist nicht (mehr) registriert — bitte neu registrieren."
            )
        return record
