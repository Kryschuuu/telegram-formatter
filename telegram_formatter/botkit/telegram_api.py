"""
telegram_formatter/botkit/telegram_api.py
=========================================
Schmale Netzwerkschicht zur Telegram-Bot-API.

Nur diese drei Operationen werden für Registrierung und Session-Betrieb
benötigt:

* :func:`get_me`         — Token → Bot-Identität (Verifikation bei Registrierung)
* :func:`set_webhook`    — optional, für Empfangs-Bots (inkl. Secret-Header)
* :func:`delete_webhook` — Pflicht beim Session-Ende (kein Zustand bei Telegram)

Bewusst getrennt von der Geschäftslogik, damit Registry und Session ohne
Netzwerk testbar sind (die Aufrufe werden dort als Funktion injiziert).
``requests`` wird wie im restlichen Projekt lazy importiert.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from telegram_formatter.sender import validate_api_base

__all__ = ["API_BASE", "TelegramAPIError", "delete_webhook", "get_me", "get_updates", "set_webhook"]


def _validate_base(api_base: str) -> None:
    """HTTPS-Pflicht (Audit M-3) — TelegramAPIError statt ApiBaseError."""
    validate_api_base(api_base, error_cls=TelegramAPIError)

#: Öffentlicher API-Endpunkt. Für Tests/Private-Instanzen überschreibbar.
API_BASE = "https://api.telegram.org"

_DEFAULT_TIMEOUT = 10.0


class TelegramAPIError(RuntimeError):
    """Telegram hat einen Fehler gemeldet oder war nicht erreichbar."""


def _post(secret: str, method: str, payload: Mapping[str, Any], *, timeout: float, api_base: str) -> dict:
    _validate_base(api_base)
    try:
        import requests  # lazy: Konvertierung/Tests brauchen keine Netzwerk-Lib
    except ImportError as exc:  # pragma: no cover - defensiv
        raise TelegramAPIError(
            "Das Paket 'requests' wird für die Telegram-API benötigt (pip install requests)."
        ) from exc

    url = f"{api_base.rstrip('/')}/bot{secret}/{method}"
    try:
        response = requests.post(url, json=dict(payload), timeout=timeout)
    except requests.RequestException as exc:
        # Kein Inhalt, kein Token in der Fehlermeldung.
        raise TelegramAPIError(f"Netzwerkfehler bei {method}: {exc.__class__.__name__}") from exc

    if response.status_code != 200:
        raise TelegramAPIError(f"Telegram-API {method} → HTTP {response.status_code}")

    try:
        body = response.json()
    except ValueError as exc:  # pragma: no cover - defensiv
        raise TelegramAPIError(f"Ungültige JSON-Antwort bei {method}") from exc

    if not isinstance(body, dict):
        # Fremde Netzantwort (z. B. Proxy-Fehlerseite als Liste) — kein
        # roher AttributeError auf body.get (seit v2.11.1).
        raise TelegramAPIError(f"Ungültige JSON-Antwort bei {method}")

    if not body.get("ok"):
        # Telegram liefert 'description' — kann Inhalte enthalten, daher kürzen.
        description = str(body.get("description", "unbekannter Fehler"))[:200]
        raise TelegramAPIError(f"Telegram-API {method} lehnte ab: {description}")
    return body


def get_me(secret: str, *, timeout: float = _DEFAULT_TIMEOUT, api_base: str = API_BASE) -> dict:
    """
    Verifiziert ein Token und liefert die Bot-Identität (``result`` von getMe).

    Enthält ausschließlich nicht-geheime Felder (``id``, ``username``,
    ``first_name``, ``is_bot``). Das Token wird nur für diesen einen Aufruf
    verwendet und nicht gespeichert.
    """
    return _post(secret, "getMe", {}, timeout=timeout, api_base=api_base)


def get_updates(
    secret: str,
    *,
    offset: int | None = None,
    limit: int = 100,
    poll_timeout: int = 0,
    allowed_updates: Sequence[str] | None = None,
    timeout: float | None = None,
    api_base: str = API_BASE,
) -> dict:
    """
    Long-Polling: liefert neue Updates ab ``offset``.

    Bewusst **ohne** Persistenz des Offsets: Ein Neustart beginnt bei 0 bzw.
    beim mitgegebenen Offset, ältere Updates werden verworfen. Genau das ist
    im dezentralen Modell gewollt — kein Update-Archiv, keine Historie.

    :param poll_timeout: Long-Polling-Sekunden bei Telegram (0 = sofort).
    :param timeout: HTTP-Timeout; Standard: ``poll_timeout + 5`` Sekunden.
    """
    payload: dict[str, Any] = {"limit": int(limit), "timeout": int(poll_timeout)}
    if offset is not None:
        payload["offset"] = int(offset)
    if allowed_updates is not None:
        payload["allowed_updates"] = list(allowed_updates)
    http_timeout = timeout if timeout is not None else float(poll_timeout) + 5.0
    return _post(secret, "getUpdates", payload, timeout=http_timeout, api_base=api_base)


def set_webhook(
    secret: str,
    url: str,
    *,
    secret_token: str | None = None,
    drop_pending_updates: bool = True,
    timeout: float = _DEFAULT_TIMEOUT,
    api_base: str = API_BASE,
) -> dict:
    """
    Registriert einen Webhook. ``secret_token`` ist der von Telegram
    mitgesendete ``X-Telegram-Bot-Api-Secret-Token``-Header — ohne ihn darf
    ein Webhook-Endpunkt keine Updates akzeptieren.
    """
    if not url.lower().startswith("https://"):
        raise TelegramAPIError("Webhook-URL muss HTTPS sein (Telegram-Vorgabe).")
    payload: dict[str, Any] = {"url": url, "drop_pending_updates": drop_pending_updates}
    if secret_token:
        payload["secret_token"] = secret_token
    return _post(secret, "setWebhook", payload, timeout=timeout, api_base=api_base)


def delete_webhook(
    secret: str,
    *,
    drop_pending_updates: bool = True,
    timeout: float = _DEFAULT_TIMEOUT,
    api_base: str = API_BASE,
) -> dict:
    """
    Entfernt den Webhook am Session-Ende.

    Pflicht im dezentralen Modell: Nach der Session soll **kein** Zustand bei
    Telegram verbleiben, der Updates an eine nicht mehr existierende
    Infrastruktur leitet.
    """
    return _post(
        secret,
        "deleteWebhook",
        {"drop_pending_updates": drop_pending_updates},
        timeout=timeout,
        api_base=api_base,
    )
