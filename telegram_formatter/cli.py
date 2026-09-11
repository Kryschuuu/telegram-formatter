"""
telegram_formatter/cli.py
=========================
Kommandozeilen-Einstieg für die Konvertierung und den Versand ohne Browser.

Beispiele::

    # Dry-Run: zeigt nur die gebauten API-Payloads an (kein Token nötig)
    python -m telegram_formatter.cli beispiel_input.txt

    # Aus STDIN lesen
    echo "**fett** und $x^2$" | python -m telegram_formatter.cli

    # Wirklich senden
    python -m telegram_formatter.cli beispiel_input.txt --chat-id -100123456789 --send --token 123:ABC

Umgebungsvariablen: ``TELEGRAM_BOT_TOKEN``, ``TELEGRAM_CHAT_ID``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from telegram_formatter.sender import SendError, send_message
from telegram_formatter.utils import build_messages


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
        default=None,
        help="Ziel-Chat/-Kanal (z. B. -100123456789). Standard: TELEGRAM_CHAT_ID.",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="[veraltet — sichtbar in ps & Shell-Historie] Telegram-Bot-Token. "
             "Besser: Umgebungsvariable TELEGRAM_BOT_TOKEN setzen.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Wirklich an Telegram senden (sonst nur Dry-Run der Payloads).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")

    # Audit M-2: Tokens als CLI-Argument stehen in der Prozessliste (ps)
    # und in der Shell-Historie. Der Botkit-Weg (BotToken.from_getpass /
    # Environment + scrub) ist der empfohlene Ersatz.
    token = args.token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if args.token:
        print("WARNUNG: --token ist veraltet (ps/Historie-Leak). Nutze die "
              "Umgebungsvariable TELEGRAM_BOT_TOKEN oder 'botctl' mit "
              "BotToken.from_getpass().", file=sys.stderr)

    # Eingabe lesen (Datei oder STDIN), immer explizit UTF-8.
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    messages = build_messages(text, chat_id or 0)

    if not messages:
        print("Keine (nicht leere) Eingabe zum Senden.")
        return 0

    for i, msg in enumerate(messages, 1):
        print(f"--- Nachricht {i}/{len(messages)} ({msg.kind}) ---")
        print(json.dumps(msg.payload, ensure_ascii=False, indent=2))
        if args.send:
            if not token:
                print("FEHLER: --token bzw. TELEGRAM_BOT_TOKEN fehlt.")
                return 1
            if not chat_id:
                print("FEHLER: --chat-id bzw. TELEGRAM_CHAT_ID fehlt.")
                return 1
            try:
                print("Telegram-Antwort:", send_message(msg, token))
            except SendError as exc:
                print(f"FEHLER: {exc}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
