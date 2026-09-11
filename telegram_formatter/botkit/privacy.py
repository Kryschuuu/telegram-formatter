"""
telegram_formatter/botkit/privacy.py
====================================
Datenschutz-Grundlagen des ``botkit``-Pakets. Dieses Modul ist die
technische Durchsetzung der wichtigsten Anforderung: **Es werden keine
Nutzerdaten gespeichert, geloggt oder weitergegeben.**

Bausteine:

* :func:`redact`           — entfernt Token-ähnliche und geheimnis-ähnliche
                             Fragmente aus beliebigen Texten (Log-Zeilen,
                             Exception-Meldungen, API-Antworten).
* :class:`RedactingFilter` — ``logging.Filter``, der *jede* Logzeile im
                             Prozess durch :func:`redact` schickt.
* :func:`audit`            — die einzige erlaubte Art zu loggen: es landen
                             ausschließlich Metadaten (Zähler, Längen,
                             Fingerprints, Telegram-Status) im Log, niemals
                             Nachrichteninhalte.
* :func:`fingerprint`      — prozesslokaler HMAC-SHA256-Wert. Erlaubt
                             Korrelation („derselbe Bot") in Logs, ohne das
                             Geheimnis preiszugeben und ohne dass sich Werte
                             über Deployments hinweg verketten lassen.

Wichtig: Python-Strings sind unveränderlich und werden nicht sicher aus dem
Speicher gelöscht. „Keine Persistenz" bedeutet hier deshalb: **kein
Schreibvorgang auf ein dauerhaftes Medium** (Datei, Datenbank, Log, Cache,
Swap-fähige Serialisierung) und **kein Inhalt in Logzeilen**.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import secrets
from collections.abc import Iterable
from typing import Any

__all__ = [
    "REDACTED",
    "RedactingFilter",
    "audit",
    "content_digest",
    "fingerprint",
    "install_privacy_filters",
    "redact",
    "scrub_environment",
]

#: Platzhalter für entfernte Geheimnisse.
REDACTED = "[redacted]"

# Prozesslokaler Zufallsschlüssel: Fingerprints sind damit innerhalb eines
# Prozesses stabil (Logs korrelierbar), über Prozessgrenzen aber wertlos.
_PROCESS_KEY = secrets.token_bytes(32)

# Formale Form eines Telegram-Bot-Tokens: <bot_id>:<35 Zeichen>.
_TOKEN_LIKE = re.compile(r"\d{5,16}:[A-Za-z0-9_-]{35}")

# Zuweisungen wie token = "..." / "api_key": "..." / secret='...'.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|access[_-]?token)"
    r"(\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})"
)

# Schlüssel, deren Werte in :func:`audit` niemals im Klartext landen dürfen.
_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "bot_token",
        "secret",
        "password",
        "api_key",
        "text",
        "content",
        "message",
        "messages",
        "body",
        "payload",
        "markdown",
        "update",
        "chat_id",
        "user",
        "username",
    }
)

_MAX_FIELD_LENGTH = 120


def redact(text: str) -> str:
    """
    Entfernt Geheimnisse aus ``text``.

    Ersetzt (a) alles, was wie ein Bot-Token aussieht, und (b) Werte von
    Zuweisungen an offensichtlich sensible Schlüssel. Die Funktion ist
    absichtlich konservativ: Im Zweifel wird zu viel, nie zu wenig ersetzt.
    """
    if not text:
        return text
    result = _TOKEN_LIKE.sub(REDACTED, text)
    result = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", result)
    return result


def fingerprint(value: str, *, length: int = 12) -> str:
    """
    Prozesslokaler, nicht umkehrbarer Fingerprint (HMAC-SHA256).

    Wird für Bot-Tokens und Chat-IDs verwendet, damit Logs und
    Fehlermeldungen korrelierbar bleiben, ohne Identitäten preiszugeben.
    """
    digest = hmac.new(_PROCESS_KEY, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:length]


def content_digest(text: str, *, length: int = 12) -> str:
    """
    Fingerprint eines *Nachrichteninhalts*.

    Erlaubt Support-Aussagen wie „derselbe Text schlug zweimal fehl", ohne
    den Text selbst zu kennen. Weil ein HMAC (nicht ein nackter SHA-256)
    verwendet wird, sind Inhalte nicht per Wörterbuchangriff rückrechenbar.
    """
    return fingerprint(text, length=length)


class RedactingFilter(logging.Filter):
    """
    Logging-Filter, der jede Logzeile durch :func:`redact` schickt.

    Einmalig auf dem Root-Logger installiert (:func:`install_privacy_filters`)
    schützt er auch Bibliotheken, die wir nicht kontrollieren — inklusive
    ``urllib3``, das bei Debug-Level sonst die komplette URL mitsamt
    Bot-Token loggen würde.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive: krumme Log-Aufrufe
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True


def install_privacy_filters(logger: logging.Logger | None = None) -> RedactingFilter:
    """
    Installiert :class:`RedactingFilter` auf ``logger`` (Standard: Root-Logger).

    Zusätzlich werden die Handler des Loggers nachgerüstet, damit auch
    fremde Logger (``urllib3``, ``requests``, ``werkzeug``) gefiltert werden.
    Die Funktion ist idempotent.
    """
    target = logger if logger is not None else logging.getLogger()
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
    if not any(isinstance(f, RedactingFilter) for f in target.filters):
        target.addFilter(RedactingFilter())

    for noisy in ("urllib3", "requests", "werkzeug", "botkit"):
        child = logging.getLogger(noisy)
        if not any(isinstance(f, RedactingFilter) for f in child.filters):
            child.addFilter(RedactingFilter())

    return RedactingFilter()


def _summarize(key: str, value: Any) -> str:
    """Reduziert einen Feldwert auf unbedenkliche Metadaten."""
    if key.lower() in _SENSITIVE_KEYS:
        if isinstance(value, str):
            return f"len={len(value)} fp={content_digest(value)}"
        if isinstance(value, (list, tuple)):
            return f"n={len(value)}"
        return f"fp={content_digest(str(value))}"
    text = redact(str(value))
    if len(text) > _MAX_FIELD_LENGTH:
        text = text[: _MAX_FIELD_LENGTH - 1] + "…"
    return text


def audit(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """
    Einzige erlaubte Log-Schnittstelle für ``botkit``.

    Loggt ein Ereignis plus Metadaten. Felder mit sensiblen Namen (siehe
    :data:`_SENSITIVE_KEYS`) werden zu ``len=… fp=…`` zusammengefasst, damit
    nie Inhalte oder Identifikatoren im Log landen.

    Beispiel::

        audit(LOGGER, logging.INFO, "session.sent", bot=123, chunks=2, text=markdown)

    erzeugt: ``event=session.sent bot=123 chunks=2 text=len=42 fp=9f1c…``
    """
    rendered = " ".join(f"{key}={_summarize(key, value)}" for key, value in fields.items())
    logger.log(level, "event=%s %s", event, rendered)


def scrub_environment(*names: str) -> Iterable[str]:
    """
    Entfernt die genannten Umgebungsvariablen aus ``os.environ``.

    Sinn: Ein einmal eingelesenes Token soll nicht an Kindprozesse vererbt
    werden (Subprozesse, Reloads, Crash-Dumps). Rückgabe: die tatsächlich
    entfernten Namen — nützlich für Tests und Startprotokolle.
    """
    removed = [name for name in names if name in os.environ]
    for name in removed:
        del os.environ[name]
    return removed
