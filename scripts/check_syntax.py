#!/usr/bin/env python3
"""
check_syntax.py — Gate gegen kaputte Merge-Auflösungen und Syntaxschäden.

Wofür
-----
Am 2026-10-26 scheiterte der Dependabot-PR „actions/setup-python 7.0.0 → 7.1.0“
nicht am Versionssprung, sondern an der *Branche*: ein handgelöster
Merge-Konflikt („Merge origin/main: resolve conflicts …“) hatte Zeichen aus
Python-Dateien entfernt. Ergebnis waren Fehler, die wie Testfehler aussehen,
aber keine sind:

* ``@pytest.fixture()`` → ``@pytestfixture()``  ⇒ ``NameError: name
  'pytestfixture' is not defined`` (Der Punkt fehlt),
* ``def test_…():`` → ``def test_…:``  ⇒ ``SyntaxError: expected '('``
  (die Klammern fehlen),
* dazu Textverluste in Docstrings (``erklären`` → ``erklaraeren``) und ein
  Rückfall der Version (2.15.1 → 2.15.0).

Sichtbar wurde das erst mitten im Testlauf, nach dem Installieren aller
Abhängigkeiten, und die Fehlermeldung zeigte auf die Testdatei statt auf die
Ursache (den Merge). Dieses Skript macht den Zustand in Sekunden und **ohne
jede Abhängigkeit** prüfbar — direkt nach dem Checkout, vor ``pip install``:

1. **Jede** versionierte ``*.py``-Datei parst (``ast.parse``) — auch Dateien,
   die kein Test importiert (``bots/``, ``scripts/``, ``examples/``) und die
   absichtlich schlechten Fixtures (``tests/fixtures/``).
2. Dekoratoren auf **Modul-Ebene** verweisen auf einen tatsächlich
   gebundenen Namen (Import, ``def``, ``class`` oder Zuweisung im Modul).
   Das fängt ``@pytestfixture()`` — ein Name, der syntaktisch gültig ist und
   deshalb von ``ast.parse`` allein nicht auffällt. Wer ``import *`` nutzt,
   wird übersprungen (nicht auflösbar).
3. Keine Datei enthält Konfliktmarker (``<<<<<<<``, ``|||||||``,
   ``>>>>>>>``) — ein committeter Marker ist immer ein Unfall.
4. Alle versionierten Textdateien sind gültiges UTF-8 ohne BOM.

Bewusste Grenze
---------------
Kein Gate erkennt, ob ein *Satz* stimmt: ein Merge, der eine Zeile semantisch
verfälscht, bleibt für Maschinen unsichtbar. Genau deshalb steht in
CONTRIBUTING.md, dass Konflikte **nie** durch Abtippen gelöst werden, sondern
durch vollständiges Übernehmen einer Seite (``git checkout --ours/--theirs``)
plus erneutes Anwenden der eigenen Änderung. Dieses Skript fängt den Fall ab,
in dem dabei Code kaputtgeht — und nennt die Ursache, statt einen Testfehler
zu zeigen.

Aufruf
------
    python scripts/check_syntax.py          # Exit 0 = sauber, 1 = Befund
    python scripts/check_syntax.py --all    # inkl. unversionierter Dateien

Das Skript nutzt ausschließlich die Standardbibliothek: es läuft im CI vor
``pip install`` und in Umgebungen ohne Test-Abhängigkeiten (Render-Build).
"""

from __future__ import annotations

import ast
import builtins
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Repository-Wurzel — das Skript liegt in ``scripts/``.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: Endungen, die als Text geprüft werden. Alles andere gilt als binär.
TEXT_SUFFIXES = frozenset(
    {
        ".cjs",
        ".css",
        ".html",
        ".js",
        ".json",
        ".md",
        ".mjs",
        ".py",
        ".sh",
        ".svg",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)

#: Konfliktmarker am Zeilenanfang. ``=======`` fehlt absichtlich: eine Zeile
#: aus ``=`` ist auch eine gültige Setext-Überschrift in Markdown.
CONFLICT_MARKERS = ("<<<<<<<", "|||||||", ">>>>>>>")

#: Verzeichnisse, die nie geprüft werden (Build-Artefakte, Abhängigkeiten).
SKIP_DIRS = frozenset(
    {".git", ".venv", "venv", "node_modules", "__pycache__", "build", "dist", ".mypy_cache"}
)

#: Namen, die ohne Modul-Bindung auflösbar sind (``@property``, ``@staticmethod``).
BUILTIN_NAMES = frozenset(dir(builtins))


@dataclass(frozen=True)
class Problem:
    """Ein Befund, der den Check fehlschlagen lässt."""

    path: Path
    line: int
    message: str

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO_ROOT) if self.path.is_relative_to(REPO_ROOT) else self.path
        return f"{rel}:{self.line}: {self.message}"


def tracked_files() -> list[Path]:
    """Versionierte Dateien aus ``git ls-files``; Fallback: alle Dateien im Repo."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return _walk(REPO_ROOT)
    return [REPO_ROOT / name for name in out.decode("utf-8").split("\0") if name]


def _walk(root: Path) -> list[Path]:
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and not SKIP_DIRS.intersection(path.relative_to(root).parts)
    ]


def _module_bindings(tree: ast.Module) -> tuple[set[str], bool]:
    """Alle auf Modulebene gebundenen Namen; zweiter Wert: ``import *`` vorhanden?"""
    names: set[str] = set()
    has_star_import = False

    def collect(body: list[ast.stmt]) -> None:
        nonlocal has_star_import
        for node in body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "*":
                        has_star_import = True
                    else:
                        names.add(alias.asname or alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                # Der Rumpf ist ein eigener Namensraum; nur der Name selbst zählt hier.
                names.add(node.name)
            elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    names.update(
                        sub.id for sub in ast.walk(target) if isinstance(sub, ast.Name)
                    )
            elif isinstance(node, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                for field in ("body", "orelse", "finalbody"):
                    collect(getattr(node, field, []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    if handler.name:
                        names.add(handler.name)
                    collect(handler.body)
                for item in getattr(node, "items", []) or []:
                    if item.optional_vars is not None:
                        names.update(
                            sub.id
                            for sub in ast.walk(item.optional_vars)
                            if isinstance(sub, ast.Name)
                        )

    collect(tree.body)
    return names, has_star_import


def _decorator_root(node: ast.expr) -> str | None:
    """Wurzelname eines Dekorators: ``@a.b(c)`` → ``a``, ``@f`` → ``f``."""
    while True:
        if isinstance(node, ast.Call):
            node = node.func
        elif isinstance(node, ast.Attribute):
            node = node.value
        elif isinstance(node, ast.Name):
            return node.id
        else:
            return None


def check_python(path: Path, source: str) -> list[Problem]:
    """Syntaxfehler und nicht auflösbare Modul-Dekoratoren melden."""
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        # exc.lineno ist 1-basiert; fehlt es, zeigen wir auf Zeile 1.
        return [Problem(path, exc.lineno or 1, f"SyntaxError: {exc.msg}")]

    bindings, has_star_import = _module_bindings(tree)
    if has_star_import:
        return []  # ``import *``: Namen sind statisch nicht auflösbar

    problems: list[Problem] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            root = _decorator_root(decorator)
            if root and root not in bindings and root not in BUILTIN_NAMES:
                problems.append(
                    Problem(
                        path,
                        decorator.lineno,
                        f"Decorator-Name {root!r} ist im Modul nicht gebunden "
                        "(Tippfehler oder Zeichenverlust beim Merge?)",
                    )
                )
    return problems


def decode_text(path: Path, data: bytes) -> tuple[str | None, list[Problem]]:
    """UTF-8 ohne BOM dekodieren; ``None``, wenn die Datei kein gültiger Text ist."""
    problems: list[Problem] = []
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append(Problem(path, 1, "UTF-8-BOM am Dateianfang (unerwünscht)"))
        data = data[len(b"\xef\xbb\xbf") :]
    try:
        return data.decode("utf-8"), problems
    except UnicodeDecodeError as exc:
        problems.append(Problem(path, 1, f"kein gültiges UTF-8: {exc.reason} (Byte {exc.start})"))
        return None, problems


def check_text(path: Path, source: str) -> list[Problem]:
    """Konfliktmarker im Text finden."""
    return [
        Problem(path, number, f"Konfliktmarker {line[:7]!r} — Merge nicht abgeschlossen")
        for number, line in enumerate(source.splitlines(), start=1)
        if line.startswith(CONFLICT_MARKERS)
    ]


def check_file(path: Path) -> list[Problem]:
    """Alle Textprüfungen für eine Datei."""
    if path.suffix not in TEXT_SUFFIXES:
        return []
    source, problems = decode_text(path, path.read_bytes())
    if source is None:
        return problems
    problems.extend(check_text(path, source))
    if path.suffix == ".py":
        problems.extend(check_python(path, source))
    return problems


def check_files(paths: list[Path]) -> list[Problem]:
    """Alle Prüfungen über die übergebenen Pfade; fehlende Dateien werden übersprungen."""
    problems: list[Problem] = []
    for path in paths:
        if path.is_file():
            problems.extend(check_file(path))
    return problems


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    unknown = [arg for arg in args if arg != "--all"]
    if unknown:
        print(f"Unbekannte Option(en): {' '.join(unknown)}", file=sys.stderr)
        print("Aufruf: python scripts/check_syntax.py [--all]", file=sys.stderr)
        return 2

    paths = _walk(REPO_ROOT) if "--all" in args else tracked_files()
    problems = check_files(paths)

    if problems:
        print(f"\nSYNTAX-GATE FEHLGESCHLAGEN — {len(problems)} Befund(e)\n", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nUrsache ist meist ein schlecht aufgelöster Merge. Nicht abtippen, "
            "sondern eine Seite vollständig\nübernehmen "
            "(git checkout --ours <datei> bzw. --theirs) und die eigene Änderung "
            "erneut anwenden.\nDetails: CONTRIBUTING.md.\n",
            file=sys.stderr,
        )
        return 1

    print(f"SYNTAX-GATE OK — {len(paths)} Datei(en) geprüft.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
