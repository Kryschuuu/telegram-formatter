"""Unit-Tests für die Konvertierungslogik in ``utils``."""

from __future__ import annotations

import pytest

from utils import (
    REGULAR_MESSAGE_MAX_CHARS,
    RICH_MESSAGE_MAX_CHARS,
    build_messages,
    chunk_text,
    has_latex,
    has_table,
    markdown_to_html,
    markdown_to_rich_markdown,
    normalize_text,
    parse_pipe_table,
    split_formulas,
    validate_latex_braces,
)


# ---------------------------------------------------------------------------
# Unicode-Normalisierung
# ---------------------------------------------------------------------------
def test_normalize_line_endings():
    assert normalize_text("a\r\nb\rc") == "a\nb\nc"


def test_normalize_nfc_combining_grave():
    # "i" + COMBINING GRAVE ACCENT (NFD) -> "ì" (NFC, ein Codepoint)
    decomposed = "i\u0300"
    assert len(decomposed) == 2
    normalized = normalize_text(decomposed)
    assert normalized == "\u00ec"
    assert len(normalized) == 1


def test_normalize_idempotent():
    text = "È già finita\r\n"
    assert normalize_text(normalize_text(text)) == normalize_text(text)


# ---------------------------------------------------------------------------
# LaTeX-Klammern-Validierung
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "formula,expected",
    [
        ("x^2", True),
        ("\\binom{70}{6}", True),
        ("\\binom{\\binom{70}{6}}{33}", True),
        ("\\frac{a}{\\frac{b}{c}}", True),
        ("{unbalanced", False),
        ("unbalanced}", False),
        ("\\frac{a}{b", False),
        ("", True),
    ],
)
def test_validate_latex_braces(formula, expected):
    assert validate_latex_braces(formula) is expected


# ---------------------------------------------------------------------------
# Formel-Erkennung (split_formulas)
# ---------------------------------------------------------------------------
def test_split_inline_formula():
    segs = split_formulas("Text $x^2$ Ende")
    assert [s.kind for s in segs] == ["text", "inline_math", "text"]
    assert segs[1].content == "x^2"


def test_split_display_formula():
    segs = split_formulas("Vor $$E=mc^2$$ nach")
    assert segs[1].kind == "display_math"
    assert segs[1].content == "E=mc^2"


def test_split_nested_binom_not_cut():
    formula = "\\binom{\\binom{70}{6}}{33}"
    segs = split_formulas(f"${formula}$")
    assert segs[0].kind == "inline_math"
    assert segs[0].content == formula


def test_split_preserves_special_symbols():
    formula = "\\sum_{i=1}^{n} \\alpha_i = \\int x\\,dx"
    segs = split_formulas(f"${formula}$")
    assert segs[0].content == formula


def test_dollar_before_space_is_not_math():
    segs = split_formulas("Preis $ 20 nur")
    assert all(s.kind == "text" for s in segs)
    assert "".join(s.content for s in segs) == "Preis $ 20 nur"


def test_unterminated_dollar_stays_text():
    text = "Hier $fehlt das Ende"
    segs = split_formulas(text)
    assert all(s.kind == "text" for s in segs)
    assert "".join(s.content for s in segs) == text


def test_multiple_formulas():
    segs = split_formulas("$a$ und $b$")
    kinds = [s.kind for s in segs]
    assert kinds == ["inline_math", "text", "inline_math"]


def test_has_latex():
    assert has_latex("kein latex") is False
    assert has_latex("$x$") is True
    assert has_latex("$$y$$") is True
    assert has_latex("$ 20") is False  # Preisangabe


# ---------------------------------------------------------------------------
# Tabellen
# ---------------------------------------------------------------------------
def test_parse_pipe_table_classic():
    block = "n | L(n,6,6,2) | Quelle\n---|--------|-------\n20 | 10 | [Thm 3.1]"
    rows = parse_pipe_table(block)
    assert rows == [["n", "L(n,6,6,2)", "Quelle"], ["20", "10", "[Thm 3.1]"]]


def test_parse_pipe_table_with_leading_trailing_pipes():
    block = "| a | b |\n|---|---|\n| 1 | 2 |"
    rows = parse_pipe_table(block)
    assert rows == [["a", "b"], ["1", "2"]]


def test_parse_pipe_table_not_a_table():
    assert parse_pipe_table("nur ein normaler satz") is None
    assert parse_pipe_table("eine | zeile") is None  # < 2 Zeilen


def test_parse_pipe_table_uneven_columns_padded():
    block = "a | b\n---\n1\n2 | 3 | 4"
    rows = parse_pipe_table(block)
    assert all(len(r) == 3 for r in rows)


def test_has_table():
    assert has_table("normaler text") is False
    assert has_table("a | b\n---|---\n1 | 2") is True


# ---------------------------------------------------------------------------
# Markdown -> Telegram-HTML (Regular-Pfad)
# ---------------------------------------------------------------------------
def test_html_bold_italic_underline_strike():
    html = markdown_to_html("**fett** *kursiv* __unterstrichen__ ~~weg~~")
    assert "<b>fett</b>" in html
    assert "<i>kursiv</i>" in html
    assert "<u>unterstrichen</u>" in html
    assert "<s>weg</s>" in html


def test_html_inline_code_and_block():
    html = markdown_to_html("`code` und ```\nblock\n```")
    assert "<code>code</code>" in html
    assert "<pre>block</pre>" in html


def test_html_escapes_special_chars():
    html = markdown_to_html("a < b & c > d")
    assert "&lt;" in html and "&gt;" in html and "&amp;" in html


def test_html_link():
    html = markdown_to_html("[Text](https://example.com)")
    assert '<a href="https://example.com">Text</a>' in html


def test_html_headings_and_list():
    html = markdown_to_html("# Titel\n\n- Punkt")
    assert "<b>🚀 Titel</b>" in html
    assert "• Punkt" in html


def test_html_code_keeps_specials_unformatted():
    html = markdown_to_html("`**nicht** fett`")
    assert "<code>**nicht** fett</code>" in html
    assert "<b>" not in html


def test_html_blockquote():
    html = markdown_to_html("> Zitat")
    assert "<blockquote>Zitat</blockquote>" in html


# ---------------------------------------------------------------------------
# Markdown -> Rich Markdown (Rich-Pfad)
# ---------------------------------------------------------------------------
def test_rich_preserves_math():
    rich = markdown_to_rich_markdown("Formel $x^2$ bleibt")
    assert "$x^2$" in rich


def test_rich_underline_becomes_u():
    rich = markdown_to_rich_markdown("__unterstrichen__")
    assert "<u>unterstrichen</u>" in rich


def test_rich_table_normalized():
    rich = markdown_to_rich_markdown("a | b\n---|---\n1 | 2")
    assert "| a | b |" in rich
    assert "| 1 | 2 |" in rich


# ---------------------------------------------------------------------------
# Nachrichtenbau (build_messages)
# ---------------------------------------------------------------------------
def test_build_regular_for_plain_formatting():
    msgs = build_messages("**fett** text", 123)
    assert len(msgs) == 1
    assert msgs[0].kind == "regular"
    assert msgs[0].payload["parse_mode"] == "HTML"
    assert "<b>fett</b>" in msgs[0].payload["text"]


def test_build_rich_for_math():
    msgs = build_messages("Euler: $e^{i\\pi}+1=0$", 123)
    assert len(msgs) == 1
    assert msgs[0].kind == "rich"
    # Korrektes Feld: markdown (nicht "format"/"text")
    assert "markdown" in msgs[0].payload["rich_message"]
    assert "$e^{i\\pi}+1=0$" in msgs[0].payload["rich_message"]["markdown"]


def test_build_rich_for_table():
    msgs = build_messages("a | b\n---|---\n1 | 2", 123)
    assert msgs[0].kind == "rich"


def test_build_empty():
    assert build_messages("   \n\n  ", 123) == []


# ---------------------------------------------------------------------------
# Aufteilung (chunk_text)
# ---------------------------------------------------------------------------
def test_chunk_short_text_unchanged():
    assert chunk_text("kurz", 1000) == ["kurz"]


def test_chunk_empty():
    assert chunk_text("", 100) == []
    assert chunk_text("   ", 100) == []


def test_chunk_splits_at_paragraph_boundary():
    para = "Wort " * 50  # 250 Zeichen
    text = "\n\n".join([para] * 4)
    chunks = chunk_text(text, 500)
    assert len(chunks) > 1
    assert all(len(c) <= 500 for c in chunks)
    # Absätze bleiben zusammenhängend (jeder Chunk endet mit intaktem Absatz).
    for c in chunks:
        assert c.count("Wort") % 50 == 0 or c.count("Wort") > 0


def test_chunk_splits_long_single_paragraph_at_word_boundary():
    text = " ".join(["abc"] * 1000)  # 3999 Zeichen
    chunks = chunk_text(text, 1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert all(c.split()[-1] == "abc" for c in chunks)


def test_chunk_hard_split_pathological_word():
    text = "x" * 2500
    chunks = chunk_text(text, 1000)
    assert len(chunks) == 3
    assert all(len(c) <= 1000 for c in chunks)


def test_chunk_keeps_table_intact():
    table = "| h1 | h2 |\n|---|---|\n| a | b |\n| c | d |"
    text = "kurz\n\n" + table
    chunks = chunk_text(text, 1000)
    assert len(chunks) == 1  # Tabelle bleibt als Block erhalten


def test_build_messages_splits_regular_at_4096():
    long_text = ("Wort " * 1000) + "\n\n" + ("Wort " * 1000)  # > 4096
    msgs = build_messages(long_text, 123)
    assert len(msgs) > 1
    for m in msgs:
        assert m.kind == "regular"
        assert len(m.payload["text"]) <= REGULAR_MESSAGE_MAX_CHARS


def test_build_messages_splits_rich_at_32768():
    para = "$x^2$ " + ("Wort " * 2000)  # > 10k
    text = "\n\n".join([para] * 6)  # > 32k
    msgs = build_messages(text, 123)
    assert len(msgs) > 1
    for m in msgs:
        assert m.kind == "rich"
        assert len(m.payload["rich_message"]["markdown"]) <= RICH_MESSAGE_MAX_CHARS
