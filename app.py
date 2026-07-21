"""
app.py
======
Liest eine Textdatei (z. B. beispiel_input.txt) ein und verschickt sie als
Telegram-Nachricht — je nach Inhalt entweder als klassische Regular Message
(sendMessage) oder als Rich Message (sendRichMessage, Bot API 10.1/10.2),
damit LaTeX-Formeln und Tabellen nativ und korrekt gerendert werden.

STATUS DIESER DATEI — BITTE ZUERST LESEN
-----------------------------------------
Das Original-Repository (github.com/Kryschuuu/telegram-formatter) war zum
Zeitpunkt der Bearbeitung nicht einsehbar (robots.txt blockiert den
automatisierten Zugriff auf die tree-Ansicht; der Name taucht in keiner
Websuche auf) und es wurden keine Dateien in diesem Chat hochgeladen. Dies
ist daher KEINE Korrektur des Originalcodes, sondern eine lauffähige
Referenzimplementierung, die die in der Aufgabenstellung beschriebenen
Fehlerklassen korrekt löst. Für eine echte Zeile-für-Zeile-Korrektur bitte
die vier Originaldateien in den Chat hochladen.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from utils import (
    RICH_MESSAGE_MAX_CHARS,
    chunk_rich_markdown,
    escape_markdown_v2,
    needs_rich_message,
    normalize_text,
    to_rich_markdown,
)

try:
    import requests  # nur nötig, wenn tatsächlich live gegen die Bot API gesendet wird
except ImportError:
    requests = None


BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
API_BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"


@dataclass
class TelegramMessage:
    """Repräsentiert eine sendefertige Telegram-Nachricht (Regular oder Rich)."""

    kind: str  # "regular" | "rich"
    payload: dict


def build_messages(raw_text: str, chat_id: int | str) -> list[TelegramMessage]:
    """
    Baut aus dem eingelesenen Rohtext sendefertige Nachrichten.

    FEHLER 4 IM URSPRÜNGLICHEN app.py (rekonstruiert aus der Aufgaben-
    beschreibung, siehe Modulkommentar oben): Der gesamte Text — inklusive
    $...$-Formeln und Tabelle 3 — wurde vermutlich in einem einzigen
    sendMessage(parse_mode="MarkdownV2")-Aufruf verschickt. Das erklärt das
    kaputte Rendering: MarkdownV2 in Regular Messages kennt kein LaTeX
    (Dollarzeichen sind dort ohne Bedeutung) und unterstützt grundsätzlich
    keine Tabellen.

    KORREKTUR: needs_rich_message() prüft den Text auf Formeln/Tabellen.
    Nur dann wird der Rich-Message-Pfad (sendRichMessage, Bot API
    10.1/10.2) verwendet; reiner Fließtext geht weiterhin als günstigere
    Regular Message raus.
    """
    text = normalize_text(raw_text)

    if needs_rich_message(text):
        rich_text = to_rich_markdown(text)
        chunks = chunk_rich_markdown(rich_text, max_chars=RICH_MESSAGE_MAX_CHARS)
        return [
            TelegramMessage(
                kind="rich",
                payload={
                    "chat_id": chat_id,
                    "rich_message": {
                        # format="markdown" -> Rich Markdown: GFM-kompatibel,
                        # inkl. nativer $...$/$$...$$-LaTeX-Syntax und
                        # nativer Pipe-Tabellen (core.telegram.org/bots/
                        # features#rich-messages).
                        "format": "markdown",
                        "text": chunk,
                    },
                },
            )
            for chunk in chunks
        ]

    # Kein LaTeX, keine Tabelle -> klassische, günstigere Regular Message reicht.
    return [
        TelegramMessage(
            kind="regular",
            payload={
                "chat_id": chat_id,
                "text": escape_markdown_v2(text),
                "parse_mode": "MarkdownV2",
            },
        )
    ]


def send(message: TelegramMessage) -> dict:
    """Verschickt eine einzelne TelegramMessage über die passende Bot-API-Methode."""
    if requests is None:
        raise RuntimeError(
            "Das Paket 'requests' wird fuer den echten Versand benoetigt "
            "(pip install requests)."
        )
    method = "sendRichMessage" if message.kind == "rich" else "sendMessage"
    resp = requests.post(f"{API_BASE}/{method}", json=message.payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main(input_path: str, chat_id: str, *, dry_run: bool = True) -> None:
    # FEHLER 3 (Sonderzeichen "ì"): Datei OHNE explizites encoding="utf-8" zu
    # oeffnen ist die haeufigste Ursache fuer Mojibake bei Diakritika.
    with open(input_path, "r", encoding="utf-8") as f:
        raw_text = f.read()

    messages = build_messages(raw_text, chat_id)

    for i, msg in enumerate(messages, 1):
        print(f"--- Nachricht {i}/{len(messages)} ({msg.kind}) ---")
        print(json.dumps(msg.payload, ensure_ascii=False, indent=2))
        if not dry_run:
            result = send(msg)
            print("Telegram-Antwort:", result)


if __name__ == "__main__":
    input_file = sys.argv[1] if len(sys.argv) > 1 else "beispiel_input.txt"
    target_chat = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("TELEGRAM_CHAT_ID", "")
    # dry_run=True: zeigt nur die exakten API-Payloads an, ohne einen echten
    # Bot-Token zu benoetigen (praktisch zum Nachvollziehen/Testen).
    main(input_file, target_chat, dry_run=True)
