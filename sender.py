"""
sender.py
=========
Netzwerkversand der in :mod:`utils` gebauten Nachrichten an die
Telegram-Bot-API.

Bewusst von der reinen Konvertierungslogik getrennt, damit ``utils`` ohne
Netzwerkzugriff testbar bleibt. ``requests`` wird erst beim tatsächlichen
Versand importiert (lazy), damit Konvertierung + Tests ohne die Bibliothek
funktionieren.
"""

from __future__ import annotations

from utils import TelegramMessage


class SendError(RuntimeError):
    """Wird geworfen, wenn der Versand fehlschlägt (Netz, API oder fehlende Lib)."""


def send_message(
    message: TelegramMessage,
    bot_token: str,
    *,
    timeout: float = 15.0,
    api_base: str | None = None,
) -> dict:
    """
    Verschickt genau eine :class:`TelegramMessage` an die Telegram-Bot-API.

    - ``kind == "rich"``   -> Methode ``sendRichMessage``
    - ``kind == "regular"``-> Methode ``sendMessage``

    :raises SendError: bei fehlender ``requests``-Bibliothek, Netzwerkfehlern
        oder einem HTTP-Fehlerstatus der API.
    """
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - Umgebung ohne requests
        raise SendError(
            "Das Paket 'requests' wird für den Versand benötigt (pip install requests)."
        ) from exc

    base = api_base or f"https://api.telegram.org/bot{bot_token}"
    method = "sendRichMessage" if message.kind == "rich" else "sendMessage"

    try:
        response = requests.post(f"{base}/{method}", json=message.payload, timeout=timeout)
    except requests.RequestException as exc:
        raise SendError(f"Netzwerkfehler beim Versand: {exc}") from exc

    if response.status_code != 200:
        raise SendError(f"Telegram-API-Fehler {response.status_code}: {response.text}")

    return response.json()
