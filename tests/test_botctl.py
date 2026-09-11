"""Tests für die ``botctl``-CLI — Robustheit des Review-Tors gegen Nicht-Code-Eingaben.

Regression (CI-Lauf PR #6): Der ``bot-gate``-Diff fand ``bots/README.md`` und
fütterte es in den AST-Parser — ``botctl review`` crashte mit rohem
Traceback. Das Tor meldet derartige Eingaben jetzt sauber als
Eingabefehler (Exit 2, kein Ticket, kein Audit-Trail).
"""

from __future__ import annotations

from pathlib import Path

from telegram_formatter.botctl import main

ROOT = Path(__file__).resolve().parents[1]
CLEAN_BOT = ROOT / "examples" / "own_bot" / "minimal_bot.py"


def test_review_rejects_non_python_file_without_traceback(tmp_path, capsys):
    readme = tmp_path / "README.md"
    readme.write_text(
        "# bots\n\nAblage für **eigene** Bot-Implementierungen — kein Code.\n",
        encoding="utf-8",
    )
    trail = tmp_path / "reviews.json"

    rc = main(["review", str(readme), "--bot-id", "0", "--check", "--ledger", str(trail)])

    captured = capsys.readouterr()
    assert rc == 2
    assert "nicht als Python-Quelltext lesbar" in captured.out
    # Kein halbes Ticket und kein Audit-Trail für eine Nicht-Code-Datei.
    assert not trail.exists()


def test_review_missing_file_is_input_error(tmp_path, capsys):
    rc = main(
        ["review", str(tmp_path / "fehlt.py"), "--bot-id", "0", "--check",
         "--ledger", str(tmp_path / "reviews.json")]
    )
    assert rc == 2
    assert "Datei nicht gefunden" in capsys.readouterr().out


def test_review_check_passes_for_reference_bot(tmp_path, capsys):
    rc = main(
        ["review", str(CLEAN_BOT), "--bot-id", "0", "--check",
         "--ledger", str(tmp_path / "reviews.json")]
    )
    assert rc == 0
    assert "keine Befunde" in capsys.readouterr().out
