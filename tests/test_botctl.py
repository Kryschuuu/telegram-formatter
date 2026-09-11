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


# --------------------------------------------------------------------------- #
# Audit B-2: botctl send --local-trust muss funktionieren (Reproduktion:
# Exit 1 "Bot ist nicht freigegeben" — der dokumentierte Selbstbetrieb-Pfad
# war komplett tot).
# --------------------------------------------------------------------------- #
def test_send_local_trust_end_to_end(tmp_path, monkeypatch, capsys):
    import telegram_formatter.botctl as bc
    import telegram_formatter.botkit.session as sess_mod

    monkeypatch.setattr(
        bc, "get_me",
        lambda secret, **kw: {"ok": True, "result": {
            "id": 123456789, "is_bot": True, "username": "mein_bot", "first_name": "Mein"}},
    )
    sent: list = []
    monkeypatch.setattr(
        sess_mod, "send_message",
        lambda message, secret, **kw: sent.append(message) or {"ok": True},
    )
    monkeypatch.setenv("BOTCTL_TEST_TOKEN", "123456789:" + "A" * 35)
    payload = tmp_path / "msg.md"
    payload.write_text("**Hallo** $x^2$", encoding="utf-8")

    rc = bc.main([
        "send", "--chat-id", "-1001",
        "--token-env", "BOTCTL_TEST_TOKEN",
        "--owner", "alice", "--local-trust", "--send",
        "--file", str(payload),
        "--ledger", str(tmp_path / "reviews.json"),
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Gesendet" in out
    assert len(sent) == 1
    assert sent[0].payload["chat_id"] == "-1001"


def test_send_dry_run_local_trust(tmp_path, monkeypatch, capsys):
    import telegram_formatter.botctl as bc

    monkeypatch.setattr(
        bc, "get_me",
        lambda secret, **kw: {"ok": True, "result": {
            "id": 123456789, "is_bot": True, "username": "mein_bot", "first_name": "Mein"}},
    )
    monkeypatch.setenv("BOTCTL_TEST_TOKEN", "123456789:" + "A" * 35)
    payload = tmp_path / "msg.md"
    payload.write_text("**nur Vorschau**", encoding="utf-8")

    rc = bc.main([
        "send", "--chat-id", "-1001",
        "--token-env", "BOTCTL_TEST_TOKEN",
        "--owner", "alice", "--local-trust",
        "--file", str(payload),
        "--ledger", str(tmp_path / "reviews.json"),
    ])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "Dry-Run" in out


def test_send_requires_token_after_use_env_is_scrubbed(tmp_path, monkeypatch, capsys):
    """scrub_environment muss das Token nach der Nutzung aus dem Prozess-Env tilgen."""
    import os

    import telegram_formatter.botctl as bc

    monkeypatch.setattr(
        bc, "get_me",
        lambda secret, **kw: {"ok": True, "result": {
            "id": 123456789, "is_bot": True, "username": "mein_bot", "first_name": "Mein"}},
    )
    import telegram_formatter.botkit.session as sess_mod
    monkeypatch.setattr(sess_mod, "send_message", lambda *a, **kw: {"ok": True})
    monkeypatch.setenv("BOTCTL_TEST_TOKEN", "123456789:" + "A" * 35)
    payload = tmp_path / "m.md"
    payload.write_text("hi", encoding="utf-8")

    rc = bc.main(["send", "--chat-id", "-1", "--token-env", "BOTCTL_TEST_TOKEN",
                  "--owner", "alice", "--local-trust", "--send",
                  "--file", str(payload), "--ledger", str(tmp_path / "r.json")])
    assert rc == 0
    assert "BOTCTL_TEST_TOKEN" not in os.environ
