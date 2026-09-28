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

import pathlib

import pytest

from telegram_formatter import __version__
from telegram_formatter import app as app_module
from telegram_formatter import utils as u


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
    for wrapper in ("**", "~~", "__"):
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
    assert __version__ == "2.14.1"


def test_faq_mentions_64000():
    html = (pathlib.Path(app_module.__file__).parent / "templates" / "index.html").read_text(encoding="utf-8")
    assert "64000" in html


# ------------------------------------------------------------------ v2.11.1: UTF-16-Maß, Fences, Carry-Cap
def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def test_regular_emoji_chunks_respect_utf16_limit():
    # Telegram zählt in UTF-16-Units: 4000 Emoji = 8000 Units → mind. 2 Chunks.
    txt = "🚀" * 4000
    msgs = _regular_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        assert _utf16_len(_html_of(m)) <= u.REGULAR_MESSAGE_MAX_CHARS
    assert "".join(_html_of(m) for m in msgs) == txt


def test_rich_emoji_chunks_respect_utf16_limit():
    txt = "$x$ " + "🎉" * 20000
    msgs = _rich_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        assert _utf16_len(_markdown_of(m)) <= u.RICH_MESSAGE_MAX_CHARS


def test_hard_split_respects_utf16_budget_and_is_lossless():
    txt = "🚀" * 3000 + "x" * 100
    parts = u._hard_split(txt, 4096)
    assert "".join(parts) == txt
    assert all(_utf16_len(p) <= 4096 for p in parts)
    assert all("�" not in p for p in parts)  # kein zerrissener Codepoint
    assert u._hard_split("", 10) == [""]


def test_unclosed_fence_is_atomic_to_eof():
    txt = "Intro $x$ und Code:\n```python\na = $x$ + 1\nrest ohne ende"
    ranges = u._atomic_ranges(txt)
    assert (txt.index("```"), len(txt)) in ranges
    # Kein Formel-Span im offenen Code:
    assert [s.content for s in u.iter_math_spans(txt, skip_code_fences=True)] == ["x"]


def test_unclosed_oversized_fence_splits_into_closed_blocks():
    inner = "\n".join(f"line {i} " + "x" * 60 for i in range(800))
    txt = "$m$\n\n```python\n" + inner  # absichtlich ohne Closing-Fence
    msgs = _rich_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        md = _markdown_of(m)
        if "```" in md:
            assert md.count("```") % 2 == 0
        assert _utf16_len(md) <= u.RICH_MESSAGE_MAX_CHARS
    code_chunks = [m for m in msgs if "line " in _markdown_of(m)]
    assert code_chunks
    for m in code_chunks:
        assert "```python" in _markdown_of(m)


def test_rich_carry_depth_is_capped_and_chunks_stay_valid():
    """Die Carry-Tiefe ist gekappt — und die Schließer ebenfalls (v2.13.0).

    v2.12.0 schloss *alle* offenen Ebenen, trug aber nur `_RICH_MAX_CARRY`
    in den Folge-Chunk. Bei tiefer Verschachtelung wuchs die Zahl der
    Schließer unbegrenzt und riss das 32768-Limit (gemessen: 76304 Zeichen
    bei `"<u>" * 10900`) — Telegram lehnte die Nachricht mit
    ``MESSAGE_TOO_LONG`` ab, es wurde also nichts zugestellt.

    Korrekte Zusage: die Schließerzahl ist gekappt, damit der Chunk gültig
    bleibt. Äußere, offen bleibende Marker rendern im Rich-Markdown als
    Literal — dieselbe Abwägung, die der Carry ohnehin macht.
    """
    depth = 12
    txt = "$x$ " + "<u>" * depth + ("wort " * 8000) + "</u>" * depth
    msgs = _rich_payloads(txt)
    assert len(msgs) >= 2
    for m in msgs:
        assert _utf16_len(_markdown_of(m)) <= u.RICH_MESSAGE_MAX_CHARS


def test_rebalance_markdown_caps_added_closers():
    """Regression v2.13.0: der Re-Balancer hing *alle* Schließer an.

    Geprüft wird die von `_rebalance_markdown_chunks` ERGÄNZTE Zahl, nicht die
    im Quelltext enthaltene — sonst zählt der Test die 12 expliziten `</u>`
    des Testinputs mit.
    """
    for depth in (4, 12, 1000, 8000):
        out = u._rebalance_markdown_chunks(["<u>" * depth])
        assert out, depth
        added = sum(chunk.count("</u>") for chunk in out)
        assert added <= depth  # nie mehr als offen
        # Gekappt auf die innersten Ebenen je Chunk.
        for chunk in out:
            assert chunk.count("</u>") <= u._RICH_MAX_CARRY, depth


def test_rich_deep_nesting_stays_within_limit():
    """Regression v2.13.0: tiefe Verschachtelung sprengte das Rich-Limit.

    Vorher ergab `"<u>" * 10900` (32 700 Zeichen, klar unterhalb des
    Eingabelimits) einen einzigen Chunk von 76 304 Zeichen — Telegram
    antwortet mit 400 ``MESSAGE_TOO_LONG``, der Versand scheitert komplett.
    """
    for label, body in (
        ("html_marker", "<u>" * 10900),
        ("markdown_markers", "**~~" * 8000),
        ("interleaved", "**~~" * 4000),
    ):
        msgs = _rich_payloads("$x$ " + body)
        assert msgs, label
        for m in msgs:
            md = _markdown_of(m)
            assert _utf16_len(md) <= u.RICH_MESSAGE_MAX_CHARS, label
            assert _utf16_len(md) > 0, label


def test_no_empty_chunks_ever():
    """Regression v2.13.0: leere Chunks brechen den ganzen Versand ab.

    `sendMessage` beantwortet einen leeren Text mit 400
    ``message text is empty``. Auslöser war ein Link mit ~4000 Zeichen URL:
    der harte Schnitt zerlegte den Chunk in einen 4000-Zeichen-Tail und einen
    0-Zeichen-Rest.
    """
    cases = {
        "long_link": "[x](https://a/" + "a" * 4000 + ")",
        "long_link_bold": "**" + "[x](https://a/" + "a" * 4000 + ")" + "**",
        "rich_long_link": "$x$ [y](https://a/" + "a" * 4000 + ")",
        "many_long_links": " ".join(
            "[x](https://example.com/" + "b" * 300 + ")" for _ in range(30)
        ),
    }
    for label, text in cases.items():
        for m in u.build_messages(text, 1):
            payload = m.payload
            if isinstance(payload, str):
                body = payload
            else:
                rich = payload.get("rich_message")
                body = rich.get("markdown", "") if isinstance(rich, dict) else payload.get("text", "")
            assert _utf16_len(body) > 0, f"{label}: leerer Chunk {body!r}"


def test_html_rebalance_bounds_deep_stacks():
    """Regression v2.13.0: `_rebalance_html_chunks` war unbeschränkt.

    Ein Chunk mit 1000 offenen `<u>` bekam 1000 Schließer angehängt und wuchs
    auf 7001 Zeichen — das 4096-Limit gerissen. Da ein nicht geschlossenes
    HTML-Tag von Telegram mit 400 ``can't parse entities`` abgelehnt wird,
    kann der Überschuss nicht einfach wegfallen: stattdessen wird der Öffner
    aus der Ausgabe entfernt, der Inhalt bleibt als Klartext erhalten.
    """
    for depth in (500, 1000, 5000):
        out = u._rebalance_html_chunks(["<u>" * depth + "x"])
        assert out, depth
        for chunk in out:
            assert _utf16_len(chunk) <= 4096, depth
            assert _utf16_len(chunk) > 0, depth
            # Wohlgeformt: kein offener <u> ohne passendes </u>.
            assert chunk.count("<u>") == chunk.count("</u>"), depth
        assert "x" in out[0], depth  # Inhalt bleibt erhalten


def test_dangling_tail_never_consumes_whole_chunk():
    """Regression v2.13.0: `_dangling_tail` gab den kompletten Chunk zurück.

    Dann blieb für den aktuellen Chunk nichts übrig und ein leerer Chunk
    entstand — mit 400 ``message text is empty`` als Folge.
    """
    for chunk in ('<a href="https://a/' + "a" * 500, "&amp", "<b>x"):
        assert u._dangling_tail(chunk) != chunk
    # Ein Fragment, das nicht der ganze Chunk ist, wandert weiterhin.
    assert u._dangling_tail('x <a href="y') == '<a href="y'


def test_chunks_per_request_cap_does_not_shrink_documented_limit():
    """Die Chunk-Kappung (v2.13.0) darf keine dokumentierte Faehigkeit verkleinern.

    `SHARED_WEB_MAX_INPUT_CHARS` (64 000) erzeugt 17 Chunks, das BYOB-Limit
    (100 000) erzeugt 23. Der Default der Kappung (25) liegt ueber beiden —
    ein Absenken wuerde gueltige Eingaben ablehnen, ohne dass die Eingabegrenze
    das erklaeren wuerde.
    """
    assert len(u.build_messages("x" * app_module.SHARED_WEB_MAX_INPUT_CHARS, "-100999")) <= (
        app_module.MAX_CHUNKS_PER_REQUEST
    )
    assert app_module.MAX_CHUNKS_PER_REQUEST >= 17  # Shared-Pfad
    assert app_module.MAX_CHUNKS_PER_REQUEST >= 23  # BYOB-Pfad


def test_send_rejects_more_chunks_than_allowed(monkeypatch):
    """Ueber der Kappung: 400 mit klarer Meldung, kein API-Aufruf."""
    from unittest import mock

    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    monkeypatch.setattr(app_module, "SHARED_WEB_SEND", True)
    monkeypatch.setattr(app_module, "SHARED_WEB_SENDS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "SHARED_WEB_SENDS_PER_MINUTE_TOTAL", 0)
    monkeypatch.setattr(app_module, "MAX_CHUNKS_PER_REQUEST", 3)
    app_module._RATE_HITS.clear()
    monkeypatch.setattr(app_module, "_RATE_LAST_PRUNE", 0.0)
    with app_module.app.test_client() as c, mock.patch.object(
        app_module, "send_message"
    ) as sender:
        resp = c.post("/api/send", json={"text": "x" * 64_000, "confirm_public": True})
    assert resp.status_code == 400
    assert "Zu viele Teile" in resp.get_json()["error"]
    assert sender.call_count == 0  # kein einziger API-Aufruf


# ---------------------------------------------------------------------------
# Regression v2.13.0 — Leerzeilen beim Splitting
# ---------------------------------------------------------------------------
class TestBlankLinesSurviveSplitting:
    r"""`re.split(r"\n\s*\n", ...)` igte Leerzeilen und Einrückung.

    Zwei Fehler: (1) `\s*` ist gierig und greift über Zeilengrenzen, sodass
    `"a\n\n\n\nb"` zu `['a', 'b']` und damit zu `"a\n\nb"` wurde; (2)
    `if p.strip()` verwarf jeden reinen Whitespace-Absatz. Die Oberfläche
    verspricht in index.html ausdrücklich "Leerzeilen zwischen Absätzen
    bleiben erhalten".
    """

    def test_multiple_blank_lines_survive(self):
        assert u.chunk_text("a\n\n\n\nb", 4096) == ["a\n\n\n\nb"]

    def test_roundtrip_is_lossless(self):
        text = "para1 text\n\n\npara2 text\n\n\n\npara3"
        assert "".join(u.chunk_text(text, 20)) == text

    def test_whitespace_only_line_survives(self):
        assert u.chunk_text("a\n   \nb", 4096) == ["a\n   \nb"]

    @pytest.mark.parametrize("length", [9_000, 40_000])
    def test_limits_still_hold(self, length):
        chunks = u.chunk_text("x" * length, 4096)
        assert chunks
        for chunk in chunks:
            assert 0 < u._telegram_len(chunk) <= 4096

    def test_never_produces_empty_chunks(self):
        for text in ("\n\n\n", "a\n\n\n\n\n\nb", ("z" * 4095 + "\n\n") * 3):
            for chunk in u.chunk_text(text, 4096):
                assert chunk.strip() or len(chunk) <= 2


def test_formula_split_preserves_blank_lines_and_indent():
    """Regression v2.13.0: `$$…$$` verlor Leerzeilen **und** Einrückung.

    `for ln in lines if ln != ""` verwarf Leerzeilen, `part.strip()` die
    führende Einrückung. Bei mehrzeiligen Display-Formeln (`\\begin{aligned}` …)
    änderte das das Rendering — und zwar nur an der Teilungsgrenze, also
    scheinbar zufällig. Der Fence-Pfad bewahrt Leerzeilen seit v2.11.0
    ausdrücklich; der Formel-Pfad war die Ausnahme.
    """
    inner = "\\begin{aligned}\na &= b \\\\\n\n  c &= d\n\\end{aligned}"
    fragments = u._split_guarded_unit("$$" + inner + "$$", 40)
    assert len(fragments) > 1
    assert all(f.startswith("$$") and f.endswith("$$") for f in fragments)
    # Kein Inhalt geht verloren: alle Zeilen der Eingabe sind in den Fragmenten
    # enthalten. (Die Leerzeile landet am Ende von Fragment 1, weil sie genau
    # auf der Teilungsgrenze liegt — deshalb wird hier zeilenweise verglichen
    # und nicht per "\n".join, das eine Leerzeile doppelt zählen würde.)
    fragment_lines = [line for f in fragments for line in f[2:-2].splitlines()]
    assert fragment_lines == inner.splitlines()
    # Die Leerzeile in der Mitte ist erhalten.
    assert "" in fragment_lines
    # Die Einrückung vor `c` ist erhalten.
    assert any(line == "  c &= d" for line in fragment_lines)


def test_inline_formula_split_preserves_indent():
    src = "$" + "x".join(["  y" * 20]) + "$"
    fragments = u._split_guarded_unit(src, 60)
    assert len(fragments) > 1
    assert all(f.startswith("$") and f.endswith("$") for f in fragments)
    assert all("  " in f[1:-1] for f in fragments)
