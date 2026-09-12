"""
telegram_formatter/app.py
=========================
Kleine Flask-Weboberfläche, die die Konvertierung aus :mod:`telegram_formatter.utils`
demonstriert: links Markdown/LaTeX eingeben, rechts die gebaute Telegram-
Nachricht (HTML-Payload für ``sendMessage`` bzw. Rich-Markdown-Payload für
``sendRichMessage``) betrachten. Optional kann direkt gesendet werden.

Die eigentliche Logik liegt in ``telegram_formatter/utils``/``telegram_formatter/sender``; dieses Modul ist
eine dünne HTTP-Schicht darüber — die aber die Sicherheitsgrenze des
gehosteten Dienstes bildet.

Härtungen (Security-Audit 2026-09, Befunde K-1/K-2/H-2/H-5/M-6/B-5/B-6):

* **Chat-Pinning:** ist ``TELEGRAM_CHAT_ID`` gesetzt, akzeptiert ``/api/send``
  ausschließlich diesen Zielchat; ein abweichender ``chat_id``-Wert im Request
  wird mit 400 abgelehnt. Ohne konfigurierten Chat (reiner Selbstbetrieb)
  muss der Body eine gültige, numerische ``chat_id`` enthalten — niemals
  beliebige JSON-Werte. Damit ist der Endpunkt **kein offener Relay** mehr.
* **Optionales API-Token:** ist ``TELEGRAM_FORMATTER_API_TOKEN`` gesetzt,
  verlangen alle POST-Endpunkte einen passenden ``X-Auth-Token``-Header
  (zeitkonstanter Vergleich).
* **Größen- & Mengengrenzen:** Request-Body hart auf ``MAX_BODY_BYTES``
  begrenzt (413 statt OOM), Text auf ``MAX_INPUT_CHARS`` (wie
  ``botkit.SessionConfig``), pro IP ein einfaches Frequenzlimit für
  ``/api/send`` (Standard 6/min) und ``/api/convert`` (Standard 60/min).
* **Origin-Check:** POSTs mit fremdem ``Origin``-Header werden abgewiesen.
* **Sicherheits-Header:** CSP strikt ``'self'`` (seit dem UI-Redesign keine
  CDN-Whitelists mehr, kein ``unsafe-inline``), ``nosniff``, ``no-referrer``,
  ``DENY`` für Frames.
* **Kein Upstream-Detail-Leak:** Fehler antworten mit kurter Meldung;
  ``SendError``-Meldungen enthalten per Konstruktionsregel (Modul ``sender``)
  weder Token noch URL. Zusätzlich installiert die App die
  Privacy-Redaction der botkit-Schicht auf den Root-Logger.

Starten::

    flask --app telegram_formatter.app run        # Entwicklung
    gunicorn "telegram_formatter.app:app" --workers 2 --timeout 120   # Produktion
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from collections import defaultdict, deque
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

from telegram_formatter import __version__
from telegram_formatter.botkit.registry import CHAT_ID_PATTERN
from telegram_formatter.sender import SendError, send_message
from telegram_formatter.utils import build_messages

app = Flask(__name__)
LOGGER = logging.getLogger("telegram_formatter.app")

# --- harte Grenzen ----------------------------------------------------------
#: Maximale Request-Körpergröße in Bytes (413 darüber). Kein Flask-Default!
MAX_BODY_BYTES = 512 * 1024
#: Flask-Layer: body-Größe **vor** dem Parsen begrenzen (Audit H-2 — ohne
#: dieses Limit liest Flask unbegrenzt in den RAM).
app.config["MAX_CONTENT_LENGTH"] = MAX_BODY_BYTES
#: Maximale Textlänge je Konvertierung/Versand (bewusst wie SessionConfig).
MAX_INPUT_CHARS = int(os.environ.get("TELEGRAM_FORMATTER_MAX_INPUT_CHARS", "100000"))
#: Sendungen pro Minute und IP (Telegram-Limits + DoS-Schutz für die Instanz).
SENDS_PER_MINUTE = int(os.environ.get("TELEGRAM_FORMATTER_SENDS_PER_MINUTE", "6"))
#: Konvertierungen pro Minute und IP (Live-Preview mit Debounce braucht Luft).
CONVERTS_PER_MINUTE = int(os.environ.get("TELEGRAM_FORMATTER_CONVERTS_PER_MINUTE", "60"))

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
#: Optionaler Zugangsschutz für den gehosteten Betrieb (POSTs brauchen den
#: Header ``X-Auth-Token``). Für rein lokalen Gebrauch kann er leer bleiben.
API_TOKEN = os.environ.get("TELEGRAM_FORMATTER_API_TOKEN", "")


# --------------------------------------------------------------------------- #
# Privacy-Redaction (best effort): verhindert Token-Leaks über Fremdbibliotheken,
# z. B. urllib3 im Debug-Modus. App liegt bewusst *über* botkit (kein Zyklus).
# --------------------------------------------------------------------------- #
try:  # pragma: no cover - Import kann in Minimalinstallationen fehlen
    from telegram_formatter.botkit.privacy import install_privacy_filters

    install_privacy_filters()  # Root-Logger + Handler + verräterische Libs
except Exception:  # noqa: BLE001 - Sicherheitsnetz, nie Grund für App-Absturz
    LOGGER.debug("botkit-Privacy-Filter nicht installiert (optionale Schicht).")


# --------------------------------------------------------------------------- #
# Eingaben validieren
# --------------------------------------------------------------------------- #
def _valid_chat_id(raw: object) -> str | None:
    """Numerische Chat-ID akzeptieren (Pattern aus ``botkit.registry``), sonst ``None``."""
    if raw is None:
        return None
    if not isinstance(raw, (str, int)):
        return None
    candidate = str(raw).strip()
    if CHAT_ID_PATTERN.match(candidate):
        return candidate
    return None


# --------------------------------------------------------------------------- #
# Frequenzbegrenzung pro IP (In-Process; Multi-Worker: grobe Näherung)
# --------------------------------------------------------------------------- #
_RATE_LOCK = threading.Lock()
_RATE_HITS: dict[tuple[str, str], deque[float]] = defaultdict(deque)


def _rate_limited(bucket: str, limit: int, window_seconds: float = 60.0) -> bool:
    """``True``, wenn das Limit für die aktuelle IP im Fenster erreicht ist."""
    if limit <= 0:
        return False
    key = (bucket, request.remote_addr or "unknown")
    now = time.monotonic()
    with _RATE_LOCK:
        hits = _RATE_HITS[key]
        while hits and hits[0] < now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return True
        hits.append(now)
        return False


# --------------------------------------------------------------------------- #
# Request-Guards
# --------------------------------------------------------------------------- #
@app.before_request
def _guard():
    """Origin-Bindung und optionales API-Token für alle schreibenden Endpunkte."""
    if request.method == "GET":
        return None
    origin = request.headers.get("Origin")
    if origin:
        parsed = urlparse(origin)
        if parsed.netloc and parsed.netloc != request.host:
            return jsonify({"error": "Ursprung (Origin) nicht erlaubt."}), 403
    if API_TOKEN:
        supplied = request.headers.get("X-Auth-Token", "")
        if not hmac.compare_digest(supplied, API_TOKEN):
            return jsonify({"error": "Autorisierung erforderlich."}), 401
    return None


@app.after_request
def _security_headers(response):
    """CSP ohne Inline-Skripte, keine Frames, kein MIME-Sniffing, no-referrer."""
    csp = "; ".join(
        (
            "default-src 'self'",
            # Redesign 2026-09: komplett selbst-gehostete Assets (static/css,
            # static/js) — die Tailwind-/Font-Awesome-CDNs sind entfernt und
            # damit jede Fremdnets-Whitelist. Root-Cause des Design-Bruchs war
            # genau dieses Gespann: Das Tailwind-Play-CDN injizierte Inline-
            # <style>-Regeln, die die damalige CSP (style-src ohne
            # 'unsafe-inline') blockierte -> ungestylter Rohtext. Ohne CDN kann
            # so etwas nicht mehr passieren; Tests: tests/test_app.py und
            # tests/test_frontend.py.
            "script-src 'self'",
            "style-src 'self'",
            "img-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
            "form-action 'none'",
        )
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


@app.errorhandler(404)
def _not_found(_exc):
    return jsonify({"error": "Endpunkt nicht gefunden."}), 404


@app.errorhandler(405)
def _method_not_allowed(_exc):
    return jsonify({"error": "Methode nicht erlaubt."}), 405


@app.errorhandler(413)
def _too_large(_exc):
    return jsonify({"error": f"Anfrage zu groß (max. {MAX_BODY_BYTES // 1024} KiB)."}), 413


@app.errorhandler(Exception)
def _unhandled(exc):
    # Nur Klassenname ins Log-Event (Redaction aktiv); keine Details an Clients.
    LOGGER.warning("app.unhandled_error error=%s", exc.__class__.__name__)
    return jsonify({"error": "Interner Fehler — bitte später erneut versuchen."}), 500


# --------------------------------------------------------------------------- #
# Request-Payload extrahieren + validieren
# --------------------------------------------------------------------------- #
def _extract_request(require_chat: bool):
    """Liefert ``(text, chat_id, None)`` oder ``(None, None, error_response)``."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return None, None, (jsonify({"error": "JSON-Objekt als Body erwartet."}), 400)

    text = data.get("text", "")
    if not isinstance(text, str):
        return None, None, (jsonify({"error": "'text' muss ein String sein."}), 400)
    if len(text) > MAX_INPUT_CHARS:
        return None, None, (
            jsonify({"error": f"Eingabe zu lang (max. {MAX_INPUT_CHARS} Zeichen)."}),
            400,
        )

    raw_chat = data.get("chat_id")
    if CHAT_ID:
        # Gehosteter Betrieb: Zielchat ist fest konfiguriert, kein Override.
        if raw_chat is not None and _valid_chat_id(raw_chat) != CHAT_ID:
            return None, None, (
                jsonify({"error": "chat_id kann hier nicht gesetzt werden — "
                                  "der Dienst sendet nur in den konfigurierten Chat."}),
                400,
            )
        return text, CHAT_ID, None

    # Selbstbetrieb ohne konfigurierten Chat: Body-Wert (falls vorhanden) muss
    # eine numerische Chat-ID sein — ungültige Werte werden nicht still
    # ersetzt, sondern abgewiesen.
    chat = _valid_chat_id(raw_chat)
    if chat is None:
        if raw_chat is not None:
            return None, None, (
                jsonify({"error": "chat_id muss eine Ganzzahl sein (z. B. -1001234567890)."}),
                400,
            )
        if require_chat:
            return None, None, (jsonify({"error": "Keine Chat-ID angegeben."}), 400)
        chat = "0"
    return text, chat, None


# --------------------------------------------------------------------------- #
# Routen
# --------------------------------------------------------------------------- #
@app.route("/", methods=["GET"])
def index() -> str:
    """Rendert die Editor-Seite (Markdown/LaTeX -> Telegram-Vorschau)."""
    return render_template(
        "index.html",
        configured=bool(BOT_TOKEN and CHAT_ID),
        version=__version__,
    )


@app.route("/api/convert", methods=["POST"])
def convert():
    """
    Wandelt den übermittelten Text in sendefertige Telegram-Nachrichten um
    und gibt die Payloads (inkl. Aufteilung) als JSON zurück. Rein lesend —
    es wird nichts versendet.
    """
    if _rate_limited("convert", CONVERTS_PER_MINUTE):
        return jsonify({"error": "Zu viele Anfragen — bitte kurz warten."}), 429
    text, chat_id, err = _extract_request(require_chat=False)
    if err is not None:
        return err
    messages = build_messages(text, chat_id)
    return jsonify(
        {
            "count": len(messages),
            "messages": [{"kind": m.kind, "payload": m.payload} for m in messages],
        }
    )


@app.route("/api/send", methods=["POST"])
def send():
    """
    Sendet den übermittelten Text an Telegram — ausschließlich in den
    konfigurierten ``TELEGRAM_CHAT_ID`` (bzw. die geprüfte ``chat_id`` des
    Selbstbetriebs ohne ENV-Chat).
    """
    if not BOT_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN nicht konfiguriert."}), 400
    if _rate_limited("send", SENDS_PER_MINUTE):
        return jsonify({"error": "Zu viele Sendeversuche — bitte kurz warten."}), 429

    text, chat_id, err = _extract_request(require_chat=True)
    if err is not None:
        return err

    messages = build_messages(text, chat_id)
    results = []
    for m in messages:
        try:
            send_message(m, BOT_TOKEN)
        except SendError as exc:
            # B-6: bereits gesendete Chunks offenlegen — Nutzer sollen nicht
            # blind neu senden (Duplikate). retry_after bei 429 durchreichen.
            payload = {"error": str(exc), "sent_before_error": len(results), "results": results}
            if exc.retry_after is not None:
                payload["retry_after"] = exc.retry_after
                payload["note"] = "Telegram-Rate-Limit: erst nach der Wartezeit erneut senden."
            elif results:
                payload["note"] = "Teile wurden bereits gesendet — kein kompletter Wiederholungsversand."
            status = 429 if exc.retry_after is not None else 502
            return jsonify(payload), status
        results.append({"kind": m.kind, "status": "ok"})

    return jsonify({"sent": len(results), "results": results})


if __name__ == "__main__":
    # Nur für lokale Entwicklung. In Produktion: gunicorn "telegram_formatter.app:app".
    # 0.0.0.0 ist hier Absicht (Container-/Dev-Zugriff); der Produktions-
    # Einstieg ist Gunicorn hinter dem Plattform-Proxy. Der Werkzeug-Debugger
    # bleibt deaktiviert (kein FLASK_DEBUG=1 mit diesem Block starten).
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)  # nosec B104
