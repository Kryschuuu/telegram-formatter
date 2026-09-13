"""
telegram_formatter
==================
Konvertiert Markdown mit LaTeX-Formeln und Tabellen in sendefertige
Telegram-Nachrichten und bietet mit :mod:`telegram_formatter.botkit` einen
Baukasten für eigene, dezentrale Bots (BYOB).

Modultrennung (Schichtarchitektur, von innen nach außen):

=========================  ==========================================================
:mod:`~telegram_formatter.utils`      Reine Konvertierungs- & Splitting-Logik (I/O-frei)
:mod:`~telegram_formatter.sender`     HTTP-Versand an die Telegram-Bot-API
:mod:`~telegram_formatter.cli`        Kommandozeilen-Einstieg (Dry-Run/Send)
:mod:`~telegram_formatter.app`        Flask-Weboberfläche (Vorschau + Versand)
:mod:`~telegram_formatter.botctl`     CLI für eigene Bots (register/review/approve/send)
:mod:`telegram_formatter.botkit`      BYOB-Baukasten: Tokens, Registry, Review, Sessions
=========================  ==========================================================

Bibliotheksnutzung::

    from telegram_formatter import build_messages, send_message

    for msg in build_messages("**fett** und $x^2$", chat_id="-100123456789"):
        send_message(msg, bot_token="123456:ABC")
"""

from __future__ import annotations

from telegram_formatter.sender import SendError, send_message
from telegram_formatter.utils import TelegramMessage, build_messages

__version__ = "2.4.0"

__all__ = [
    "SendError",
    "TelegramMessage",
    "__version__",
    "build_messages",
    "send_message",
]
