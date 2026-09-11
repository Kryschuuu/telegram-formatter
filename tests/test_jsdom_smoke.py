"""Wrapper: führt die funktionalen jsdom-Smoke-Tests aus (falls Node vorhanden).

Ohne Node bzw. ohne global installiertes jsdom wird der Test sauber
übersprungen (Exit-Code 77 der Spec => skip). Für die volle Abdeckung:

    npm install jsdom        # ins Repo-Elternverzeichnis ODER via NODE_PATH

Details zum Was/Warum: docs/DESIGN.md, Abschnitt „Testing“.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from telegram_formatter import app as app_module

REPO_ROOT = Path(app_module.__file__).resolve().parents[1]
SPEC = REPO_ROOT / "tests" / "frontend" / "jsdom_spec.cjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None or not SPEC.is_file(), reason="node (oder Spec) nicht verfügbar"
)


def _render_index(tmp_path: Path) -> Path:
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        html = client.get("/").data.decode("utf-8")
    target = tmp_path / "index.rendered.html"
    target.write_text(html, encoding="utf-8")
    return target


def test_jsdom_functional_smoke(tmp_path):
    """Theme-Boot, Switcher-Klicks, Vorschau-Rendering, Debounce/Fetch,
    Senden inkl. Fehlerpfad und Zurücksetzen — gegen das echt gerenderte
    Template mit den echten statischen Skripten (Stub-Fetch, kein Netz)."""
    html_file = _render_index(tmp_path)
    proc = subprocess.run(
        [NODE, str(SPEC), str(html_file)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(SPEC.parent),
    )
    if proc.returncode == 77 or "MISSING_JSDOM" in proc.stdout:
        pytest.skip("jsdom nicht installiert — `npm install jsdom` im Repo-Root aktiviert den Test")
    assert proc.returncode == 0, (
        f"jsdom-Spec fehlgeschlagen (code={proc.returncode}):\n{proc.stdout}\n{proc.stderr}"
    )
    assert "JSDOM_SPEC_OK" in proc.stdout, f"unerwartetes Spec-Resultat:\n{proc.stdout}"
