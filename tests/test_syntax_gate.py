"""Tests für das Syntax-Gate (`scripts/check_syntax.py`).

Hintergrund: PR #30 („actions/setup-python 7.0.0 → 7.1.0“) scheiterte am
2026-10-26 nicht an der Version, sondern an einem handgelösten Merge auf der
Dependabot-Branche. Zwei Testdateien kamen beschädigt an:

* ``@pytest.fixture()`` wurde zu ``@pytestfixture()`` (NameError),
* ``def test_…():`` wurde zu ``def test_…:`` (SyntaxError).

Beides fiel erst nach ``pip install`` mitten im Testlauf auf. Das Gate prüft
denselben Zustand ohne Abhängigkeiten in ~0,1 s — die Tests hier halten fest,
dass es die historischen Schäden *erkennt* und gültigen Code *nicht* meldet.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_syntax.py"


def _load_module():
    """`scripts/check_syntax.py` als Modul laden (es ist kein Paketmodul)."""
    spec = importlib.util.spec_from_file_location("check_syntax", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Vor dem Ausführen registrieren: `dataclasses` (siehe `Problem`) schlägt
    # `cls.__module__` über `sys.modules` nach und stolpert sonst über None.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


check_syntax = _load_module()


# ------------------------------------------------------------------ Vertrag: Repo sauber
def test_repository_passes_the_gate():
    """Der echte Baum muss das Gate bestehen — sonst ist die CI rot, bevor sie startet."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "SYNTAX-GATE OK" in result.stdout


def test_gate_needs_no_third_party_imports():
    """Das Gate läuft vor ``pip install`` — nur Standardbibliothek ist erlaubt."""
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    third_party = {
        name
        for name in imported
        if name not in sys.stdlib_module_names and name != "__future__"
    }
    assert not third_party, f"Fremdabhängigkeit im Gate: {sorted(third_party)}"


# ------------------------------------------------------------------ Historische Schäden
def test_missing_parentheses_are_reported(tmp_path):
    """``def test_x:`` (Klammern beim Merge verloren) → SyntaxError."""
    path = tmp_path / "test_broken.py"
    path.write_text("def test_x:\n    assert True\n", encoding="utf-8")

    problems = check_syntax.check_python(path, path.read_text(encoding="utf-8"))

    assert len(problems) == 1
    assert "SyntaxError" in problems[0].message
    assert problems[0].line == 1


def test_missing_dot_in_decorator_is_reported(tmp_path):
    """``@pytestfixture()`` statt ``@pytest.fixture()`` → ungebundener Decorator.

    Das ist die eigentliche Falle: syntaktisch gültig, fällt erst zur Laufzeit
    als ``NameError`` auf.
    """
    path = tmp_path / "test_broken.py"
    path.write_text(
        "import pytest\n\n\n@pytestfixture()\ndef client():\n    return None\n",
        encoding="utf-8",
    )

    problems = check_syntax.check_python(path, path.read_text(encoding="utf-8"))

    assert [p.line for p in problems] == [4]
    assert "pytestfixture" in problems[0].message


@pytest.mark.parametrize(
    "source",
    [
        # Normalfall: Import + Attribut-Dekorator
        "import pytest\n\n\n@pytest.fixture()\ndef client():\n    return None\n",
        # Eingebaute Dekoratoren ohne Import
        "class A:\n    @property\n    def x(self):\n        return 1\n\n"
        "    @staticmethod\n    def y():\n        return 2\n",
        # Aufruf über Alias/Attributkette
        "from telegram_formatter import app as app_module\n\n\n"
        "@app_module.app.route('/x')\ndef x():\n    return ''\n",
        # Funktionsdekorator, der im Modul weiter unten definiert wird
        "@register\ndef handler():\n    return None\n\n\ndef register(fn):\n    return fn\n",
        # ``import *``: Namen sind statisch nicht auflösbar → kein Fehlalarm
        "from os.path import *\n\n\n@join\ndef f():\n    return None\n",
    ],
)
def test_valid_decorators_are_not_flagged(tmp_path, source):
    path = tmp_path / "test_ok.py"
    path.write_text(source, encoding="utf-8")

    assert check_syntax.check_python(path, source) == []


# ------------------------------------------------------------------ Konfliktmarker & Encoding
@pytest.mark.parametrize("marker", check_syntax.CONFLICT_MARKERS)
def test_conflict_markers_are_reported(tmp_path, marker):
    path = tmp_path / "mod.py"
    source = f"x = 1\n{marker} HEAD\ny = 2\n"

    problems = check_syntax.check_text(path, source)

    assert [p.line for p in problems] == [2]
    assert "Konfliktmarker" in problems[0].message


def test_markdown_setext_rule_is_not_a_conflict_marker(tmp_path):
    """``=======`` ist in Markdown eine Überschriften-Unterstreichung, kein Konflikt."""
    path = tmp_path / "NOTES.md"

    assert check_syntax.check_text(path, "Titel\n=======\n\nText\n") == []


def test_bom_is_reported(tmp_path):
    path = tmp_path / "mod.py"
    data = b"\xef\xbb\xbfx = 1\n"

    _, problems = check_syntax.decode_text(path, data)

    assert len(problems) == 1
    assert "BOM" in problems[0].message


def test_invalid_utf8_is_reported(tmp_path):
    path = tmp_path / "mod.py"

    source, problems = check_syntax.decode_text(path, b"x = '\xff'\n")

    assert source is None
    assert "UTF-8" in problems[0].message


# ------------------------------------------------------------------ End-to-End
def test_check_files_reports_everything_and_skips_binaries(tmp_path):
    """Ein Lauf über mehrere Dateien meldet alle Befunde und ignoriert Binäres."""
    broken = tmp_path / "test_broken.py"
    broken.write_text("@pytestfixture()\ndef f():\n    return None\n", encoding="utf-8")
    syntax = tmp_path / "test_syntax.py"
    syntax.write_text("def test_x:\n    pass\n", encoding="utf-8")
    doc = tmp_path / "README.md"
    doc.write_text("<<<<<<< HEAD\n", encoding="utf-8")
    binary = tmp_path / "logo.png"
    binary.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    problems = check_syntax.check_files([broken, syntax, doc, binary, tmp_path / "missing.py"])

    reported = sorted(p.path.name for p in problems)
    assert reported == ["README.md", "test_broken.py", "test_syntax.py"]
