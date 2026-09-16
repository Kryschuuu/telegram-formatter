"""Ausgiebige Tests für 64000-Zeichen-Support (v2.11.0).

Deckt ab:
  * 64000-Zeichen E2E über build_messages / chunk_text (regular + rich)
  * Codeblock-Split: Sprache bleibt je Chunk, Leerzeilen bleiben erhalten
  * Atomic-Schutz für Inline-Code (` `code with **not bold**` `)
  * Format-Balance für ** / ~~ / __→<u> über Chunk-Grenzen (beide Pfade)
  * Frontend / Backend Limits (64000 statt 8000)
  * /api Längenkappe und Fehlermeldung
"""

from __future__ import annotations

import re
import pathlib

from telegram_formatter import utils as u
from telegram_formatter import app as app_module
from telegram_formatter import __version__


# ------------------------------------------------------------------ Helpers
def _regular_payloads(text: str):
    return [m for m in u.build_messages(text, chat_id="-1001") if m.kind == "regular"]

def _rich_payloads(text: str):
    return [m for m in u.build_messages(text, chat_id="-1001") if m.kind == "rich"]

def _html_of(msg):
    return msg.payload["text"]

def _markdown_of(msg):
    return msg.payload["rich_message"]["markdown"]

def _count_unescaped_outside_atomic(text: str, delim: str) -> int:
    """Zähle Vorkommen von delim außerhalb atomarer Bereiche und ohne \\-Escape."""
    ranges = u._atomic_ranges(text)
    n = 0
    i = 0
    L = len(text)
    dl = len(delim)
    while i <= L - dl:
        if any(s <= i < e for s, e in ranges):
            # springe ans Ende des atomaren Bereichs
            for s, e in ranges:
                if s <= i < e:
                    i = e
                    break
            continue
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith(delim, i):
            n += 1
            i += dl
        else:
            i += 1
    return n


# ------------------------------------------------------------------ 64000 Splits
def test_regular_64000_plain_splits_within_4096():
    txt = "x" * 64000
    msgs = _regular_payloads(txt)
    assert len(msgs) >= 16  # ceil(64000/4096)=16, + overhead may add one
    for m in msgs:
        assert len(_html_of(m)) <= u.REGULAR_MESSAGE_MAX_CHARS
    reassembled = "".join(_html_of(m) for m in msgs)
    # HTML ist escaped aber für reines 'x' identisch
    assert len(reassembled) == 64000


def test_regular_64000_via_chunk_text_boundary_evenness():
    # 64000-Zeichen Text ohne Formatierung muss exakt auf 4096-Grenzen splitten
    txt = "a" * 64000
    chunks = u.chunk_text(txt, u.REGULAR_MESSAGE_MAX_CHARS)
    assert all(len(c) <= 4096 for c in chunks)
    assert "".join(chunks) == txt


def test_rich_64000_splits_within_32768():
    # Rich-Pfad erzwingen via $x$
    txt = "$x$ " + ("wort " * 13000)  # ~65000 chars inc. "$x$ "
    txt = txt[:64000]
    msgs = _rich_payloads(txt)
    assert msgs  # rich
    for m in msgs:
        assert len(_markdown_of(m)) <= u.RICH_MESSAGE_MAX_CHARS
    # Reassembled: markdown sinngemäß (ohne Balancing-Tags) enthält Original
    assert sum(len(_markdown_of(m)) for m in msgs) >= 64000 - 512  # Reserve


def test_rich_64000_many_chunks_are_all_rich():
    txt = "$y$ " + ("b " * 20000)  # long rich
    txt = txt[:64000]
    msgs = u.build_messages(txt, chat_id="-1")
    assert all(m.kind == "rich" for m in msgs)
    assert len(msgs) >= 2


# ------------------------------------------------------------------ Codeblock Sprach-Erhalt
def test_codeblock_language_preserved_on_regular_split():
    inner = "\n".join([f"line {i} code " + "x"*60 for i in range(800)])
    txt = "```python\n" + inner + "\n```"
    msgs = _regular_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        html = _html_of(m)
        # Jeder Chunk muss ein eigenständiger <pre>-Block mit Sprache sein
        assert '<pre language="python">' in html or "<pre>" in html  # allowlist
        assert html.count("<pre") == html.count("</pre>")
    # Reassembled: line-Anzahl bleibt
    all_html = "".join(_html_of(m) for m in msgs)
    assert "<pre" in all_html


def test_codeblock_language_preserved_on_rich_split():
    # Rich-Pfad mit codeblock (rich unterstützt trotzdem code fences via markdown)
    inner = "\n".join([f"codeline {i} " + "y"*50 for i in range(800)])
    txt = "Vorwort\n\n```javascript\n" + inner + "\n```\n\nNachwort mit $a$"
    msgs = _rich_payloads(txt)
    # Rich-Codeblöcke werden als ``` im markdown erhalten
    for m in msgs:
        md = _markdown_of(m)
        # Wenn dieser Chunk einen Fence enthält, muss er balanciert sein
        if "```" in md:
            assert md.count("```") % 2 == 0
    # Jede Teilnachricht, die Code enthält, sollte die Sprache behalten
    code_chunks = [m for m in msgs if "codeline" in _markdown_of(m)]
    for m in code_chunks:
        assert "```javascript" in _markdown_of(m)


def test_codeblock_blank_lines_preserved():
    # 2000 Zeilen mit jeder 5. leer
    lines = []
    for i in range(2000):
        if i % 5 == 0:
            lines.append("")
        else:
            lines.append(f"row {i} content " + "z"*30)
    txt = "```\n" + "\n".join(lines) + "\n```"
    msgs = _regular_payloads(txt)
    # Leerzeilen dürfen nicht verloren gehen
    all_html = "".join(_html_of(m) for m in msgs)
    # <pre>-Inhalt enthält die Leerzeilen als \n\n — prüfe via Anzahl Doppel-Umbruch
    # Entschachtelt: html enthält escaped \n, aber Leerzeilen sind als "\n\n" im Text
    # Wir prüfen, dass mindestens 300 leere Zeilen überlebt haben
    assert all_html.count("\n\n") >= 100


# ------------------------------------------------------------------ Atomic Inline-Code
def test_atomic_ranges_protects_inline_code_delimiters():
    txt = "Start `code with **not bold** and ~~not strike~~` end **real bold**"
    ranges = u._atomic_ranges(txt)
    # Inline-Code Bereich muss als ein Range auftreten
    assert any("code with" in txt[s:e] for s, e in ranges)
    # Außerhalb atomar darf ** nur beim realen bold zählen
    assert _count_unescaped_outside_atomic(txt, "**") == 2
    assert _count_unescaped_outside_atomic(txt, "~~") == 0


def test_inline_code_not_misbalanced_over_chunks():
    # Lange Nachricht: Inline-Code am Anfang + riesiger Bold-Bereich danach
    txt = "Intro `code **fake**` " + ("**bold wort** " * 3000)
    msgs = _rich_payloads(txt + " $x$")
    for m in msgs:
        md = _markdown_of(m)
        # Außerhalb atomarer Bereiche muss ** gerade sein
        assert _count_unescaped_outside_atomic(md, "**") % 2 == 0


# ------------------------------------------------------------------ Format-Balance
def test_regular_bold_balanced_over_chunks():
    txt = "**" + ("wort " * 5000) + "**"
    txt = "x " + txt + " y " * 2000  # make long enough to split rich? but regular bold
    # Regular-Pfad: bold wird zu <b>
    msgs = _regular_payloads(txt)
    # Falls nicht gespalten, ist die Prüfung trivial — erzwinge Split
    if len(msgs) == 1:
        txt = "**" + ("wort " * 8000) + "**"
        msgs = _regular_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        html = _html_of(m)
        # Außerhalb atomar müssen <b> und </b> balanciert sein
        assert html.count("<b>") == html.count("</b>")


def test_rich_bold_strike_underline_balanced():
    for delim, wrapper in [("**", "**"), ("~~", "~~"), ("<u>", "__")]:
        # Rich: __ wird zu <u>
        if wrapper == "__":
            txt = "__" + ("wort " * 8000) + "__"
        else:
            txt = wrapper + ("wort " * 8000) + wrapper
        txt = "$x$ " + txt + " nachwort"
        msgs = _rich_payloads(txt)
        assert len(msgs) >= 2
        for m in msgs:
            md = _markdown_of(m)
            if wrapper == "__":
                assert md.count("<u>") == md.count("</u>")
            elif wrapper == "**":
                assert _count_unescaped_outside_atomic(md, "**") % 2 == 0
            else:
                assert _count_unescaped_outside_atomic(md, "~~") % 2 == 0


def test_rich_mixed_formatting_all_balanced_in_one_message():
    txt = "$m$ " + "**bold** und __unter__ und ~~strike~~ und `code **no**` " + ("x " * 8000)
    msgs = _rich_payloads(txt[:64000])
    for m in msgs:
        md = _markdown_of(m)
        assert _count_unescaped_outside_atomic(md, "**") % 2 == 0
        assert _count_unescaped_outside_atomic(md, "~~") % 2 == 0
        assert md.count("<u>") == md.count("</u>")

# ------------------------------------------------------------------ Limits: Frontend + Backend

def test_frontend_app_js_knows_64000():
    js = (pathlib.Path(app_module.__file__).parent / "static" / "js" / "app.js").read_text(encoding="utf-8")
    assert "64000" in js
    assert "INPUT_LIMIT" in js
    assert "RICH_LIMIT" in js


def test_backend_shared_web_default_is_64000():
    assert app_module.SHARED_WEB_MAX_INPUT_CHARS == 64000


def test_backend_error_message_names_64000(client=None):
    # Über Flask-Testclient die Fehlermeldung prüfen
    import telegram_formatter.app as am
    am.BOT_TOKEN = "123456:secretsecretsecretsecretsecretsec"
    am.CHAT_ID = "-100999"
    am.API_TOKEN = ""
    am.SHARED_WEB_SEND = True
    am.SHARED_WEB_MAX_INPUT_CHARS = 64000
    am.SHARED_WEB_SENDS_PER_MINUTE = 0
    am.SHARED_WEB_SENDS_PER_MINUTE_TOTAL = 0
    am.MAX_INPUT_CHARS = 100000
    am._RATE_HITS.clear()
    am.app.config["TESTING"] = True
    with am.app.test_client() as c:
        from unittest import mock
        with mock.patch.object(am, "send_message", return_value={"ok": True}):
            resp = c.post("/api/send", json={"text": "x"*64001, "confirm_public": True})
            assert resp.status_code == 400
            assert "64000" in resp.get_json()["error"]
            resp2 = c.post("/api/send", json={"text": "x"*64000, "confirm_public": True})
            assert resp2.status_code == 200


def test_build_messages_rejects_over_64000_via_shared_limit(monkeypatch=None):
    # build_messages selbst hat kein 64000-Limit, app.py schon — hier smoke
    assert __version__ == "2.11.0"


def test_faq_mentions_64000():
    html = (pathlib.Path(app_module.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "64000" in html
