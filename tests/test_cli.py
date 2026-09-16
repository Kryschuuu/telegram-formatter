"""Tests für das ``telegram_formatter.cli``-Modul (Dry-Run, STDIN, Fehlerpfade).

Neu in v2.11.1: Die CLI war das einzige Frontend ohne eigene Tests; der
Review ergänzt sie zusammen mit dem sauberen Fehlerpfad für unlesbare
Eingaben (Exit 2 statt Traceback).
"""

from __future__ import annotations

import io
import sys

from telegram_formatter.cli import main


def test_dry_run_from_file(tmp_path, capsys):
    src = tmp_path / "in.md"
    src.write_text("**fett** und $x^2$", encoding="utf-8")
    rc = main([str(src)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Nachricht 1/1 (rich)" in out
    assert "rich_message" in out


def test_dry_run_from_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("nur *Text*, keine Formel"))
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Nachricht 1/1 (regular)" in out


def test_empty_input_reports_nothing_to_send(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("   \n  "))
    rc = main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Keine (nicht leere) Eingabe" in out


def test_missing_file_is_clean_error(tmp_path, capsys):
    rc = main([str(tmp_path / "fehlt.md")])
    out = capsys.readouterr().out
    assert rc == 2
    assert "nicht lesbar" in out


def test_send_without_token_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    src = tmp_path / "in.md"
    src.write_text("hi", encoding="utf-8")
    rc = main([str(src), "--send", "--chat-id", "-1"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "TELEGRAM_BOT_TOKEN fehlt" in out


def test_send_without_chat_id_fails_cleanly(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    src = tmp_path / "in.md"
    src.write_text("hi", encoding="utf-8")
    rc = main([str(src), "--send"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "chat-id" in out
