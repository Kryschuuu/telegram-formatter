#!/usr/bin/env python3
"""
check_build.py — Selbsttest des Deployments, ausgeführt als Teil des
Build-Kommandos in `render.yaml`.

Wofür
-----
Render meldet einen Build als erfolgreich, sobald der `buildCommand` mit
Exit-Code 0 endet. Fehlt danach ein Asset, fällt das **erst zur Laufzeit**
auf: die Seite lädt 404 statt Formeln zu setzen, und der einzige Ort, an dem
das auffällt, ist die Sprechblase im Browser eines Nutzers — ohne
Serverlog, ohne Testfehler, ohne Traceback.

Genau das ist im Release 2.14.0 passiert: KaTeX kam dazu (596 KB, `static/
katex/`), und ein unvollständiges `package-data` wäre weder im Review noch in
den Tests aufgefallen.

Dieses Skript macht den Zustand *vor* dem Deploy prüfbar. Es beantwortet vier
Fragen, die alle schon einmal für Überraschung gesorgt haben:

1. Ist das Paket überhaupt importierbar? (``pip install -r requirements.txt``
   installiert nur die *Abhängigkeiten*, nicht das Paket selbst — der Import
   hängt davon ab, dass das Arbeitsverzeichnis im ``sys.path`` liegt. Das ist
   der Normalfall und funktioniert, aber es ist eine stille Annahme.)
2. Ist der Einstiegspunkt vorhanden und ein Flask-App-Objekt?
3. Ist die Health-Check-Route registriert, die `render.yaml` in
   `healthCheckPath` nennt? (Ein Tippfehler in einer der beiden Dateien fällt
   sonst erst als „Service wird nicht gesund" auf — Render startet den
   Container dann endlos neu.)
4. Sind Templates und Assets vollständig, die ohne Build-Schritt aus dem
   Arbeitsverzeichnis gelesen werden?

Bewusste Grenze
---------------
Geprüft wird nur, was **hier** falsch sein kann. Ob Telegram erreichbar ist,
ob der Bot-Token gültig ist und ob `TELEGRAM_CHAT_ID` zum gepinnten Kanal
gehört, kann erst der Dienst selbst wissen — das sind Laufzeitfragen, keine
Buildfragen. Sie werden in docs/DEPLOYMENT.md, Schritt 6, geprüft.

Aufruf
------
    python scripts/check_build.py            # Exit 0 = bereit, 1 = Fehler

Bewusst **kein** pytest-Modul: es läuft im Build-Umfeld, wo keine Test- oder
Dev-Abhängigkeiten installiert sind (requirements-dev.txt ist im Image
absichtlich nicht dabei).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

#: Repository-Wurzel. Wichtig, weil dieses Skript als `python
#: scripts/check_build.py` läuft: Python setzt dann ``scripts/`` als
#: ``sys.path[0]``, **nicht** das Repository. Ohne dieses Einfügen schlüge der
#: Import von `telegram_formatter` fehl, obwohl das Paket vorhanden ist.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PKG_DIR = REPO_ROOT / "telegram_formatter"

#: Dateien, ohne die die Oberfläche sichtbar kaputt ist. KaTeX steht seit
#: 2.14.0 in der Liste (Formel-Renderer, MIT, via scripts/vendor-katex.sh) —
#: fehlt eines davon, bleibt die Vorschau bei ungesetztem LaTeX.
REQUIRED_FILES = (
    "templates/index.html",
    "static/favicon.svg",
    "static/css/tokens.css",
    "static/css/base.css",
    "static/css/layout.css",
    "static/css/components.css",
    "static/js/theme.js",
    "static/js/app.js",
    "static/js/byob.js",
    "static/katex/katex.min.css",
    "static/katex/katex.min.js",
    "static/katex/LICENSE",
    "static/katex/VERSION",
)

#: Mindestanzahl WOFF2-Schriften. KaTeX lädt sie über sein eigenes CSS
#: (``url(fonts/…)``), sie stehen also in keinem ``<link>`` und keinem
#: ``@font-face`` dieses Projekts — wer sie nicht mitzählt, verliert sie
#: unbemerkt und bekommt Ersatzschriften in den Formeln.
MIN_KATEX_FONTS = 10

#: Health-Check-Route, die `render.yaml` in `healthCheckPath` nennt.
HEALTH_PATH = "/healthz"


def _fail(problems: list[str]) -> None:
    print("\nBUILD-SELBSTTEST FEHLGESCHLAGEN\n", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "\nDer Dienst wird so nicht gestartet. Details: docs/DEPLOYMENT.md, "
        "Abschnitt 'Build-Selbsttest'.\n",
        file=sys.stderr,
    )
    raise SystemExit(1)


def check_files() -> list[str]:
    """Alle Assets vorhanden?"""
    problems = [
        f"fehlt: {rel}" for rel in REQUIRED_FILES if not (PKG_DIR / rel).is_file()
    ]
    fonts = list((PKG_DIR / "static" / "katex" / "fonts").glob("*.woff2"))
    if len(fonts) < MIN_KATEX_FONTS:
        problems.append(
            f"nur {len(fonts)} KaTeX-Schriften unter static/katex/fonts/ "
            f"(erwartet >= {MIN_KATEX_FONTS}) — Formeln fallen auf eine "
            f"Ersatzschrift. Neu vendorn: scripts/vendor-katex.sh"
        )
    return problems


def check_gunicorn() -> list[str]:
    """Ist der im Start-Kommando genannte Server überhaupt installiert?"""
    if shutil.which("gunicorn") is None:
        return [
            "gunicorn nicht im PATH — requirements.txt (Render) oder der "
            "Docker-Build wurde nicht ausgeführt"
        ]
    return []


def check_app(health_path: str) -> list[str]:
    """Importierbar, Flask-App, Health-Route registriert?"""
    try:
        from telegram_formatter import __version__
        from telegram_formatter.app import app
    except Exception as exc:  # noqa: BLE001 — hier ist jede Ausnahme relevant
        return [
            f"Import von telegram_formatter.app fehlgeschlagen: "
            f"{type(exc).__name__}: {exc}"
        ]

    problems: list[str] = []
    if not hasattr(app, "route"):
        return ["telegram_formatter.app:app ist kein Flask-App-Objekt"]

    routes = {rule.rule for rule in app.url_map.iter_rules()}
    if health_path not in routes:
        problems.append(
            f"healthCheckPath {health_path!r} ist in app.py NICHT registriert "
            f"(gefunden: {', '.join(sorted(routes)) or 'keine'}) — Render "
            f"würde den Container endlos neu starten"
        )

    print(f"  Version aus dem Paket: {__version__}")
    print(f"  Registrierte Routen:   {len(routes)}")
    print(f"  Health-Check-Route:    {health_path}")
    return problems


def main() -> int:
    print("Build-Selbsttest (scripts/check_build.py)")
    print(f"  Repository: {REPO_ROOT}")

    problems: list[str] = []
    problems += check_files()
    problems += check_gunicorn()
    problems += check_app(HEALTH_PATH)

    if problems:
        _fail(problems)

    print(f"  Assets:                {len(REQUIRED_FILES)} vorhanden")
    print("BUILD-SELBSTTEST OK — der Dienst kann starten.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
