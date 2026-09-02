"""
cli.py
======
Kommandozeilen-Einstieg für die Konvertierung und den Versand ohne Browser.

Beispiele::

    # Dry-Run: zeigt nur die gebauten API-Payloads an (kein Token nötig)
    python cli.py beispiel_input.txt

    # Aus STDIN lesen
    echo "**fett** und $x^2$" | python cli.py

    # Wirklich senden
    python cli.py beispiel_input.txt --chat-id -100123456789 --send --token 123:ABC

Umgebungsvariablen: ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from sender import SendError, send_message
from utils import build_messages


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Markdown + LaTeX -> Telegram-Formatter (CLI)",
    )
    parser.add_argument(
        "input",
        nargs="?",
        help="Eingabedatei (UTF-8). Ohne Angabe wird von STDIN gelesen.",
    )
    parser.add_argument(
        "--chat-id",
        default=os.environ.get("TELEGRAM_CHAT_ID", ""),
        help="Ziel-Chat/-Kanal (z. B. -100123456789).",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        help="Telegram-Bot-Token.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Wirklich an Telegram senden (sonst nur Dry-Run der Payloads).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    # Eingabe lesen (Datei oder STDIN), immer explizit UTF-8.
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    messages = build_messages(text, args.chat_id or 0)

    if not messages:
        print("Keine (nicht leere) Eingabe zum Senden.")
        return 0

    for i, msg in enumerate(messages, 1):
        print(f"--- Nachricht {i}/{len(messages)} ({msg.kind}) ---")
        print(json.dumps(msg.payload, ensure_ascii=False, indent=2))
        if args.send:
            if not args.token:
                print("FEHLER: --token bzw. TELEGRAM_BOT_TOKEN fehlt.")
                return 1
            if not args.chat_id:
                print("FEHLER: --chat-id bzw. TELEGRAM_CHAT_ID fehlt.")
                return 1
            try:
                print("Telegram-Antwort:", send_message(msg, args.token))
            except SendError as exc:
                print(f"FEHLER: {exc}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
