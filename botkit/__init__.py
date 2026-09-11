"""
botkit
======
Baukasten für **eigene, dezentrale Telegram-Bots** im Umfeld von
``telegram-formatter`` — als Alternative zum zentralen Bot
(``@mdtotxt_bot``), bei dem alle Nachrichten über fremde Infrastruktur
laufen und dort gespeichert werden.

Leitprinzipien:

* **Bring Your Own Bot (BYOB).** Der Nutzer bringt sein eigenes Bot-Token
  mit; die Registry kennt nur Identität und Status, niemals das Geheimnis.
* **Keine Persistenz.** Kein Schreibzugriff auf Dateisystem, Datenbank,
  Cache oder Log. Alles lebt im RAM und endet mit der Session.
* **Keine Inhalte in Logs.** Geloggt werden ausschließlich Metadaten
  (Zähler, Längen, Fingerprints) über :func:`botkit.privacy.audit`.
* **Review vor Deployment.** Statische Regeln, Checkliste und
  Vier-Augen-Prinzip (:mod:`botkit.review`) sind Teil des Ablaufs, kein
  nachgelagerter Prozess.

Öffentliche Fassade:

===========================  ==================================================
:mod:`botkit.tokens`         ``BotToken``, Token-Vaults (RAM-only)
:mod:`botkit.privacy`        Redaction, Fingerprints, ``audit()``
:mod:`botkit.registry`       Registrierung ohne Token-Speicherung
:mod:`botkit.review`         Statik, Checkliste, Review-Gate
:mod:`botkit.session`        ephemere Bot-Sessions
:mod:`botkit.telegram_api`   getMe / setWebhook / deleteWebhook
===========================  ==================================================
"""

from __future__ import annotations

from .privacy import audit, fingerprint, install_privacy_filters, redact
from .registry import BotRegistry, RegistrationStatus, validate_chat_id
from .review import Reviewer, ReviewGate, ReviewLedger, ReviewRole, analyze_source
from .session import BotSession, SessionConfig, SessionError, SessionManager
from .tokens import BotToken, InMemoryTokenVault, PassthroughTokenVault

__version__ = "1.3.0"

__all__ = [
    "BotRegistry",
    "BotSession",
    "BotToken",
    "InMemoryTokenVault",
    "PassthroughTokenVault",
    "RegistrationStatus",
    "ReviewGate",
    "ReviewLedger",
    "ReviewRole",
    "Reviewer",
    "SessionConfig",
    "SessionError",
    "SessionManager",
    "__version__",
    "analyze_source",
    "audit",
    "fingerprint",
    "install_privacy_filters",
    "redact",
    "validate_chat_id",
]
