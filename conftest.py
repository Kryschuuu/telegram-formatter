"""
conftest.py
===========
Stellt sicher, dass Projekt-Root und ``tests/`` beim Import von Testmodulen
auflösbar sind — unabhängig davon, aus welchem Verzeichnis ``pytest``
gestartet wird (lokal, CI, Container).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for candidate in (ROOT, ROOT / "tests", ROOT / "examples"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))
