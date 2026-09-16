"""
telegram_formatter/sender.py
============================
Netzwerkversand der in :mod:`telegram_formatter.utils` gebauten Nachrichten an die
Telegram-Bot-API.

Bewusst von der reinen Konvertierungslogik getrennt, damit ``telegram_formatter.utils`` ohne
Netzwerkzugriff testbar bleibt. ``requests`` wird erst beim tatsächlichen
Versand importiert (lazy), damit Konvertierung + Tests ohne die Bibliothek
funktionieren.

Härtungsregeln (Security-Audit 2026-09, Befunde K-1/H-3/B-7):

* **Fehlermeldungen enthalten niemals die Request-URL.** Die Exception-Texte von
  ``requests`` beinhalten bei Netzwerkfehlern die komplette URL — und damit das
  **Bot-Token** im Klartext. Alle Ausnahmen werden zu statischen Meldungen
  normalisiert, die nur Statuscode, Exception-Klassenname und eine gekürzte,
  unbedenkliche API-Description enthalten.
* **Kein Rohtext-Durchreichen:** ``response.text`` wird nie in Fehler geladen;
  nur das ``description``-Feld (max. 200 Zeichen) wird übernommen.
* **429-Backoff wird kommuniziert:** :attr:`SendError.retry_after` trägt den
  Telegram-Wert (Sekunden), aufrufende Schichten können daraus ein
  ``Retry-After`` ableiten.
* **Verbindungspooling:** ein modul-weites ``requests.Session``-Objekt reusing
  hält die TLS-Handshakes pro Host auf 1 (statt je Chunk neu).
* ``api_base`` muss HTTPS sein (Ausnahme localhost) — schützt Tokens vor
  Klartext-MITM über konfigurierte Proxy-/Testserver.
"""

from __future__ import annotations

import threading
from urllib.parse import urlparse

from telegram_formatter.utils import TelegramMessage

#: Maximale Länge der übernommenen API-Error-Description (Zeichen).
MAX_DESCRIPTION_CHARS = 200

#: Localhost-Ausnahmen für die HTTPS-Pflicht (Tests, private Instanzen).
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})


class SendError(RuntimeError):
    """Wird geworfen, wenn der Versand fehlschlägt (Netz, API oder fehlende Lib).

    :attr:`retry_after` enthält den Telegram-Backoff-Wert in Sekunden bei 429
    (sonst ``None``). Die Meldung selbst ist bewusst inhaltsfrei: kein Token,
    keine URL, kein Rohtext.
    """

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ApiBaseError(ValueError):
    """Die konfigurierte API-Basis ist unzulässig (z. B. HTTP statt HTTPS)."""


def validate_api_base(api_base: str | None, *, error_cls: type[Exception] = ApiBaseError) -> None:
    """
    Erzwingt HTTPS für fremde API-Basen; localhost bleibt für Tests erlaubt.

    Ein ``api_base``-Fehler (Tippfehler, Downgrade-Proxy, Environment-Injection)
    würde sonst Token und Chat-Inhalt im Klartext an einen MITM liefern.
    """
    if not api_base:
        return
    parsed = urlparse(api_base)
    host = (parsed.hostname or "").lower()
    if not parsed.scheme or not parsed.netloc or parsed.scheme not in {"http", "https"}:
        raise error_cls("api_base muss eine absolute HTTP(S)-URL sein.")
    if parsed.scheme != "https" and host not in _LOCAL_HOSTS:
        raise error_cls("api_base ist nur mit HTTPS erlaubt (Ausnahme: localhost).")


# --------------------------------------------------------------------------- #
# Modul-weite requests.Session (Verbindungspooling) — defensiv: Umgebungen
# ohne Session-Support (Test-Stub) fallen auf requests.post zurück.
# --------------------------------------------------------------------------- #
_SESSION: tuple[tuple[int, int], object] | None = None
_SESSION_LOCK = threading.Lock()


def _get_session():
    """Liefert eine gecachte ``requests.Session`` oder ``None`` ohne Support.

    Der Cache-Schlüssel kombiniert Modul- *und* Session-Klasse: Wird eine der
    beiden ausgetauscht (Tests, Hot-Patching), entsteht automatisch eine neue
    Session — sonst würden Tests eine veraltete gepoolte Session erben.
    """
    global _SESSION
    import requests  # lazy, wie im Rest des Moduls

    factory = getattr(requests, "Session", None)
    if factory is None:
        return None
    marker = (id(requests), id(factory))
    if _SESSION is None or _SESSION[0] != marker:
        with _SESSION_LOCK:
            if _SESSION is None or _SESSION[0] != marker:
                _SESSION = (marker, factory())
    return _SESSION[1]


def _error_detail(response) -> tuple[str, float | None]:
    """Holt (gekürzte Description, retry_after) aus einem Fehler-Body — sicher."""
    try:
        body = response.json()
    except (ValueError, AttributeError):
        return "", None
    if not isinstance(body, dict):
        return "", None
    description = str(body.get("description", ""))[:MAX_DESCRIPTION_CHARS]
    params = body.get("parameters")
    retry_after = None
    if isinstance(params, dict) and isinstance(params.get("retry_after"), (int, float)):
        retry_after = float(params["retry_after"])
    return description, retry_after


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

    :param api_base: Alternative API-Basis (Tests, privater Bot-API-Server).
        Das Token wird immer als ``/bot<token>/``-Segment eingebaut — auch
        bei gesetztem ``api_base`` (seit v2.11.1; vorher fehlte es dort und
        lokale Bot-API-Server wiesen den Aufruf ab).
    :raises ApiBaseError: bei unzulässiger ``api_base`` (kein HTTPS).
    :raises SendError: bei fehlender ``requests``-Bibliothek, Netzwerkfehlern,
        HTTP-Fehlerstatus oder einer API-Ablehnung (``ok: false``). Die Meldung
        ist so gebaut, dass sie **niemals** Token, URL oder Nachrichteninhalte
        enthält; :attr:`SendError.retry_after` signalisiert 429-Backoffs.
    """
    validate_api_base(api_base)
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - Umgebung ohne requests
        raise SendError(
            "Das Paket 'requests' wird für den Versand benötigt (pip install requests)."
        ) from exc

    base = f"{(api_base or 'https://api.telegram.org').rstrip('/')}/bot{bot_token}"
    method = "sendRichMessage" if message.kind == "rich" else "sendMessage"
    session = _get_session()
    post = session.post if session is not None else requests.post

    try:
        response = post(f"{base}/{method}", json=message.payload, timeout=timeout)
    except requests.RequestException as exc:
        # Nur der Klassenname — str(exc) würde die URL inkl. Bot-Token enthalten (K-1).
        raise SendError(
            f"Netzwerkfehler beim Versand ({exc.__class__.__name__})."
        ) from None

    if response.status_code != 200:
        detail, retry_after = _error_detail(response)
        suffix = f": {detail}" if detail else ""
        raise SendError(f"Telegram-API-Fehler {response.status_code}{suffix}.", retry_after=retry_after)

    try:
        body = response.json()
    except ValueError:
        raise SendError("Telegram-API: ungültige JSON-Antwort erhalten.") from None

    if isinstance(body, dict) and body.get("ok") is False:
        detail, retry_after = _error_detail(response)
        raise SendError(
            f"Telegram-API: {detail or 'Anfrage abgelehnt.'}",
            retry_after=retry_after,
        )
    return body if isinstance(body, dict) else {"result": body}
