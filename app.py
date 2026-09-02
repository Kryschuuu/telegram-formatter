"""
app.py
======
Kleine Flask-Weboberfläche, die die Konvertierung aus :mod:`utils`
demonstriert: links Markdown/LaTeX eingeben, rechts die gebaute Telegram-
Nachricht (HTML-Payload für ``sendMessage`` bzw. Rich-Markdown-Payload für
``sendRichMessage``) betrachten. Optional kann direkt gesendet werden.

Die eigentliche Logik liegt in ``utils``/``sender``; dieses Modul ist nur
eine dünne HTTP-Schicht darüber.

Starten::

    flask --app app run            # Entwicklung
    gunicorn app:app               # Produktion (z. B. Render.com)
"""

from __future__ import annotations

import os

from flask import Flask, jsonify, render_template, request

from sender import SendError, send_message
from utils import build_messages

app = Flask(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


@app.route("/", methods=["GET"])
def index() -> str:
    """Rendert die Editor-Seite (Markdown/LaTeX -> Telegram-Vorschau)."""
    return render_template("index.html", configured=bool(BOT_TOKEN and CHAT_ID))


@app.route("/api/convert", methods=["POST"])
def convert() -> tuple:
    """
    Wandelt den übermittelten Text in sendefertige Telegram-Nachrichten um
    und gibt die Payloads (inkl. Aufteilung) als JSON zurück.
    """
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    chat_id = data.get("chat_id") or CHAT_ID or 0

    messages = build_messages(text, chat_id)
    return jsonify(
        {
            "count": len(messages),
            "messages": [
                {"kind": m.kind, "payload": m.payload} for m in messages
            ],
        }
    )


@app.route("/api/send", methods=["POST"])
def send() -> tuple:
    """
    Sendet den übermittelten Text an Telegram. Erwartet einen gesetzten
    ``TELEGRAM_BOT_TOKEN`` und ``TELEGRAM_CHAT_ID`` (oder eine ``chat_id``
    im Request-Body).
    """
    if not BOT_TOKEN:
        return jsonify({"error": "TELEGRAM_BOT_TOKEN nicht konfiguriert."}), 400

    data = request.get_json(silent=True) or {}
    text = data.get("text", "")
    chat_id = data.get("chat_id") or CHAT_ID
    if not chat_id:
        return jsonify({"error": "Keine Chat-ID angegeben."}), 400

    messages = build_messages(text, chat_id)
    results = []
    for m in messages:
        try:
            send_message(m, BOT_TOKEN)
            results.append({"kind": m.kind, "status": "ok"})
        except SendError as exc:
            return jsonify({"error": str(exc)}), 502

    return jsonify({"sent": len(results), "results": results})


if __name__ == "__main__":
    # Nur für lokale Entwicklung. In Produktion: gunicorn app:app.
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
