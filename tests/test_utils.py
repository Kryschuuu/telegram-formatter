"""Unit-Tests für die Konvertierungslogik in ``telegram_formatter.utils``."""

from __future__ import annotations

import re
import time

import pytest

from telegram_formatter.utils import (
    _PADDED_MATH_MAX_CHARS,
    REGULAR_MESSAGE_MAX_CHARS,
    RICH_MESSAGE_MAX_CHARS,
    _safe_chunk,
    build_messages,
    chunk_text,
    clean_gemini_links,
    clean_url,
    convert_deepseek_latex_syntax,
    has_latex,
    has_table,
    iter_math_spans,
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
# Formel-Erkennung: DeepSeek/Gemini-Syntax \(...\) und \[...\]
# ---------------------------------------------------------------------------

def test_split_inline_math_deepseek_syntax():
    """DeepSeek verwendet \\(...\\) für Inline-Math."""
    segs = split_formulas(r"Text \(x^2\) Ende")
    assert [s.kind for s in segs] == ["text", "inline_math", "text"]
    assert segs[1].content == "x^2"


def test_split_display_math_deepseek_syntax():
    """DeepSeek verwendet \\[...\\] für Display-Math."""
    segs = split_formulas(r"Vor \[E = \frac{1}{2}CU^2\] nach")
    assert segs[1].kind == "display_math"
    assert segs[1].content == r"E = \frac{1}{2}CU^2"


def test_split_mixed_delimiters():
    """Gleiche Formel mit verschiedenen Delimitern."""
    segs = split_formulas(r"$a$ und \(b\) und $$c$$ und \[d\]")
    kinds = [s.kind for s in segs]
    assert kinds == [
        "inline_math", "text", "inline_math", "text",
        "display_math", "text", "display_math",
    ]


def test_split_preserves_nested_in_deepseek_syntax():
    """Verschachtelte Strukturen auch in DeepSeek-Syntax."""
    formula = r"\frac{a}{\frac{b}{c}}"
    segs = split_formulas(rf"\({formula}\)")
    assert segs[0].kind == "inline_math"
    assert segs[0].content == formula


def test_has_latex_deepseek_syntax():
    """\\(...\\) und \\[...\\] werden als LaTeX erkannt."""
    assert has_latex(r"Formel \(x^2\)") is True
    assert has_latex(r"Formel \[x^2\]") is True
    assert has_latex(r"Kein latex hier") is False


def test_has_latex_mixed_syntax():
    """Gemischte Syntax wird korrekt erkannt."""
    assert has_latex(r"$a$ und \(b\) und $$c$$ und \[d\]") is True


def test_unterminated_paren_stays_text():
    """Unvollständige \\(...\\) bleibt als Text."""
    text = r"Hier \(fehlt das Ende"
    segs = split_formulas(text)
    assert all(s.kind == "text" for s in segs)


def test_unterminated_bracket_stays_text():
    """Unvollständige \\[...\\] bleibt als Text."""
    text = r"Hier \[fehlt das Ende"
    segs = split_formulas(text)
    assert all(s.kind == "text" for s in segs)


def test_deepseek_real_world_example():
    """Echtes Beispiel aus DeepSeek mit komplexen Formeln."""
    text = r"""Die gespeicherte Energie \(E\) eines Kondensators beträgt:

\[
E = \frac{1}{2} C U^2
\]"""
    assert has_latex(text) is True
    segs = split_formulas(text)
    # Sollte: Text, \(E\), Text, \[Formel\], Text
    kinds = [s.kind for s in segs]
    assert "inline_math" in kinds
    assert "display_math" in kinds


def test_build_rich_for_deepseek_math():
    """DeepSeek-Syntax löst Rich-Message aus und wird zu ``$...$`` normalisiert."""
    msgs = build_messages(r"Formel \(x^2\)", 123)
    assert len(msgs) == 1
    assert msgs[0].kind == "rich"
    markdown = msgs[0].payload["rich_message"]["markdown"]
    assert "$x^2$" in markdown
    # Telegram rendert die Backslash-Delimiter nicht -> dürfen nicht mehr auftauchen.
    assert r"\(" not in markdown
    assert r"\)" not in markdown


def test_build_rich_for_deepseek_display_math():
    """DeepSeek Display-Syntax sollte Rich-Message auslösen."""
    msgs = build_messages(r"Formel \[x^2\]", 123)
    assert len(msgs) == 1
    assert msgs[0].kind == "rich"
    markdown = msgs[0].payload["rich_message"]["markdown"]
    assert "$$x^2$$" in markdown
    assert r"\[" not in markdown
    assert r"\]" not in markdown


# ---------------------------------------------------------------------------
# DeepSeek/Gemini-Delimiter -> Telegram-Syntax (convert_deepseek_latex_syntax)
# ---------------------------------------------------------------------------
def test_convert_inline_delimiters():
    assert convert_deepseek_latex_syntax(r"Die Energie \(E\) ist") == "Die Energie $E$ ist"


def test_convert_display_delimiters():
    assert convert_deepseek_latex_syntax(r"\[E = mc^2\]") == "$$E = mc^2$$"


def test_convert_task_example_end_to_end():
    """Das Beispiel aus der Aufgabenstellung muss exakt so herauskommen."""
    source = "Die Energie \\(E\\) beträgt\n\\[E = \\frac{1}{2}CU^2\\]"
    expected = "Die Energie $E$ beträgt\n$$E = \\frac{1}{2}CU^2$$"
    assert convert_deepseek_latex_syntax(source) == expected
    assert markdown_to_rich_markdown(source) == expected


def test_convert_multiline_display_keeps_newlines():
    source = "\\[\nE = \\frac{1}{2} C U^2\n\\]"
    assert convert_deepseek_latex_syntax(source) == "$$\nE = \\frac{1}{2} C U^2\n$$"


def test_convert_keeps_nested_braces_verbatim():
    source = r"\(\binom{\binom{70}{6}}{33}\)"
    assert convert_deepseek_latex_syntax(source) == r"$\binom{\binom{70}{6}}{33}$"


def test_convert_leaves_existing_dollar_math_untouched():
    source = "$x^2$ und $$y^2$$"
    assert convert_deepseek_latex_syntax(source) == source


def test_convert_mixed_syntaxes():
    source = r"$a$ und \(b\) und $$c$$ und \[d\]"
    assert convert_deepseek_latex_syntax(source) == "$a$ und $b$ und $$c$$ und $$d$$"


def test_convert_ignores_price_dollar():
    """``$ 20`` ist eine Preisangabe und darf keine Formel öffnen."""
    source = r"Kosten $ 20 und \(x\)"
    assert convert_deepseek_latex_syntax(source) == "Kosten $ 20 und $x$"


def test_convert_unterminated_inline_stays_text():
    source = r"offen \( ohne Ende"
    assert convert_deepseek_latex_syntax(source) == source


def test_convert_unterminated_display_stays_text():
    source = r"offen \[ ohne Ende"
    assert convert_deepseek_latex_syntax(source) == source


def test_convert_double_backslash_is_no_delimiter():
    """``\\\\`` (LaTeX-Zeilenumbruch) darf nicht als ``\\(`` gelesen werden."""
    source = r"Zeile \\ (kein Delimiter)"
    assert convert_deepseek_latex_syntax(source) == source


def test_convert_empty_and_plain_text():
    assert convert_deepseek_latex_syntax("") == ""
    assert convert_deepseek_latex_syntax("nur Text") == "nur Text"


def test_rich_does_not_touch_deepseek_syntax_in_code():
    """Backslash-Klammern in Code bleiben unverändert."""
    rich = markdown_to_rich_markdown("Inline `\\(x\\)` und\n\n```py\nf(\\(x\\))\n```")
    assert r"`\(x\)`" in rich
    assert r"f(\(x\))" in rich


def test_rich_converts_deepseek_but_keeps_dollar_math():
    rich = markdown_to_rich_markdown(r"\(a\) und $b$ und \[c\]")
    assert rich == "$a$ und $b$ und $$c$$"


def test_html_renders_deepseek_math_content():
    """Im HTML-Pfad bleibt der Formelinhalt erhalten (ohne Delimiter)."""
    html = markdown_to_html(r"Energie \(E\) hier")
    assert "E" in html
    assert r"\(" not in html


# ---------------------------------------------------------------------------
# Bugfix v2.10.0: Rand-Whitespace in LLM-Delimitern machte Formeln zu Text
# ---------------------------------------------------------------------------
# DeepSeek/Gemini liefern Inline-Formeln häufig mit Leerzeichen (``\( x \)``)
# oder über mehrere Zeilen verteilt. Die Konvertierung schrieb daraus
# ``$ x $`` — das ist nach den GFM/Pandoc-Randregeln KEINE Formel, Telegram
# zeigt den Quelltext wörtlich an (sichtbares ``\cdot``, sichtbares ``$``).
class TestMathNormalization:
    """Regression zu „\\( x \\)“ → „$ x $“ (Formel blieb als Text stehen)."""

    @pytest.mark.parametrize(
        "source,expected",
        [
            (r"Die Kraft \( a \cdot b \) wirkt.", r"Die Kraft $a \cdot b$ wirkt."),
            (r"\( a \cdot b\)", r"$a \cdot b$"),
            (r"\(a \cdot b \)", r"$a \cdot b$"),
            (r"\(   a \cdot b   \)", r"$a \cdot b$"),
            (r"\(\frac{a}{b} \cdot \frac{c}{d}\)", r"$\frac{a}{b} \cdot \frac{c}{d}$"),
            ("\\(a \\cdot b\n\\)", r"$a \cdot b$"),  # Umbruch vor dem Schließer
            ("\\(\na \\cdot b\n\\)", r"$a \cdot b$"),  # Umbruch an beiden Rändern
            ("\\(a \\cdot\nb\\)", r"$a \cdot b$"),  # Umbruch mitten in der Formel
        ],
    )
    def test_padded_inline_math(self, source, expected):
        assert convert_deepseek_latex_syntax(source) == expected
        assert markdown_to_rich_markdown(source) == expected

    @pytest.mark.parametrize("body", [r"a \cdot b", r"\frac{1}{2} m v^2", r"\alpha \cdot \beta"])
    def test_padded_inline_math_is_rendered_by_telegram(self, body):
        """Was der Konverter ausgibt, muss nach denselben Regeln eine Formel
        sein, die Telegram rendert — sonst bleibt der Quelltext sichtbar."""
        out = convert_deepseek_latex_syntax(rf"Text \( {body} \) Ende")
        spans = list(iter_math_spans(out))
        assert len(spans) == 1
        assert spans[0].delimiter == "$"
        assert spans[0].content == body
        assert not spans[0].content[:1].isspace()
        assert not spans[0].content[-1:].isspace()
        assert "\n" not in spans[0].content

    @pytest.mark.parametrize(
        "source,expected",
        [
            ("\\[\n a \\cdot b \n\\]", "$$\n a \\cdot b \n$$"),
            (
                "\\[\na \\cdot b\n\nc \\cdot d\n\\]",
                "$$\na \\cdot b\nc \\cdot d\n$$",
            ),
            (
                "\\[\n\na \\cdot b\n\n\\]",
                "$$\na \\cdot b\n$$",
            ),
        ],
    )
    def test_display_math_blank_lines_collapsed(self, source, expected):
        """Eine Leerzeile beendet GFM-Block-Math: Sie wird zu einem Umbruch."""
        assert convert_deepseek_latex_syntax(source) == expected

    def test_multiline_display_keeps_single_newlines(self):
        """Bestandsverhalten: ``\\[\\n…\\n\\]`` bleibt mehrzeilig (nur Leerzeilen fliegen)."""
        source = "\\[\\nE = \\\\frac{1}{2} C U^2\\n\\]"
        assert convert_deepseek_latex_syntax(source) == "$$\\nE = \\\\frac{1}{2} C U^2\\n$$"

    def test_padded_dollar_math_with_leading_latex_command(self):
        """``$ \\frac{a}{b} $`` ist eine Formel: Das Kommando steht am Anfang."""
        assert convert_deepseek_latex_syntax(r"$ \frac{a}{b} $") == r"$\frac{a}{b}$"
        assert convert_deepseek_latex_syntax(r"$ \sum_{i=1}^{n} i $") == r"$\sum_{i=1}^{n} i$"
        assert has_latex(r"$ \frac{a}{b} $") is True

    @pytest.mark.parametrize(
        "source",
        [
            r"$ x \cdot y $",                    # Formel-Text, aber wie Prosa lesbar
            r"Kosten $ 5 (\circa) und $ 10.",    # Kommando mitten im Text zwischen Preisen
            r"Zins: $ 100 bei \alpha = 2\% und $ 200",
        ],
    )
    def test_padded_dollar_without_leading_command_stays_text(self, source):
        """Die Ausnahme ist bewusst eng: nur ein *führendes* Kommando zählt.

        Alles andere ist von Prosa mit Preisen nicht unterscheidbar — lieber
        unangetastet lassen (Bestandsverhalten) als Prosa als Formel senden.
        """
        assert has_latex(source) is False
        assert convert_deepseek_latex_syntax(source) == source

    def test_dollar_math_with_line_break_is_repaired(self):
        assert convert_deepseek_latex_syntax("$a \\cdot\nb$") == r"$a \cdot b$"

    @pytest.mark.parametrize(
        "source",
        [
            "Der Preis ist $ 20 und $ 30.",
            "Kosten $100 und $200.",
            "Der Stift kostet $ 20 (netto) und $ 30 (brutto).",
            "Preis: $ 5 , Rabatt $ 1 .",
        ],
    )
    def test_prices_stay_prices(self, source):
        """B-4 bleibt gewahrt: Preise werden nie zur Formel."""
        assert has_latex(source) is False
        assert convert_deepseek_latex_syntax(source) == source
        assert markdown_to_rich_markdown(source) == source

    def test_padded_dollar_exception_is_length_bounded(self):
        """Ein Kommando in weiter Ferne darf zwei ``$`` nicht verschmelzen."""
        source = r"$ \frac{a}{b}" + "x" * (_PADDED_MATH_MAX_CHARS + 10) + " $ 30"
        assert has_latex(source) is False

    def test_display_block_with_blank_line_is_no_math(self):
        """GFM-Regel (Pandoc): Eine Leerzeile zwischen ``$$`` beendet den Block."""
        assert has_latex("$$\na\n\nb\n$$") is False

    def test_empty_delimiters_stay_text(self):
        """``\\(\\\\)``/``\\[\\]`` erzeugen keine leere Formel (»$$«)."""
        for source in (r"leer \(\) und \[] hier", "$$$$"):
            assert has_latex(source) is False
            assert convert_deepseek_latex_syntax(source) == source

    def test_backslash_escape_still_no_delimiter(self):
        source = r"Zeile \\ (kein Delimiter) und \(x\)"
        assert convert_deepseek_latex_syntax(source) == r"Zeile \\ (kein Delimiter) und $x$"

    def test_deepseek_answer_end_to_end_has_no_visible_source(self):
        """Realistische DeepSeek-Antwort: nichts davon darf wörtlich ankommen."""
        source = (
            "Die Kraft lautet:\n\n"
            "\\[\nF = m \\cdot a\n\\]\n\n"
            "Mit \\( m = 2 \\) und \\( a = 3 \\) folgt:\n\n"
            "| Größe | Wert |\n|---|---|\n| Kraft | \\( 2 \\cdot 3 \\) |\n\n"
            "Also \\( F = 6 \\) Newton.\n"
        )
        messages = build_messages(source, 123)
        assert len(messages) == 1
        assert messages[0].kind == "rich"
        markdown = messages[0].payload["rich_message"]["markdown"]
        assert r"\(" not in markdown and r"\)" not in markdown
        assert "$$\nF = m \\cdot a\n$$" in markdown
        assert "$m = 2$" in markdown and "$a = 3$" in markdown
        assert "| Kraft | $2 \\cdot 3$ |" in markdown
        assert "$F = 6$" in markdown
        # Jede Formel im Ergebnis ist nach den eigenen GFM-Regeln gültig —
        # sonst würde Telegram sie als Text anzeigen (genau der gemeldete Bug).
        assert [s.content for s in iter_math_spans(markdown)] == [
            "\nF = m \\cdot a\n",
            "m = 2",
            "a = 3",
            "2 \\cdot 3",
            "F = 6",
        ]

    def test_padded_math_survives_chunking(self):
        """Formeln mit Rand-Whitespace bleiben über Chunk-Grenzen intakt."""
        source = " ".join(rf"Satz \( a_{{{i}}} \cdot b_{{{i}}} \) Ende." for i in range(1500))
        messages = build_messages(source, "1")
        texts = [m.payload["rich_message"]["markdown"] for m in messages]
        assert len(texts) > 1
        expected = [s.content for s in iter_math_spans(convert_deepseek_latex_syntax(source))]
        produced = [s.content for s in iter_math_spans("".join(texts))]
        assert produced == expected
        assert len(produced) == 1500

    def test_html_path_keeps_content_without_delimiters(self):
        html = markdown_to_html(r"Energie \( E = m \cdot c^2 \) hier")
        assert "E = m \\cdot c^2" in html
        assert r"\(" not in html and r"\)" not in html


class TestMathSpans:
    """Der gemeinsame Scanner (Audit O-3) — eine Regelbasis für alle Pfade."""

    def test_spans_have_delimiter_and_kind(self):
        text = r"$a$ und $$b$$ und \(c\) und \[d\]"
        spans = list(iter_math_spans(text))
        assert [(s.delimiter, s.kind, s.content) for s in spans] == [
            ("$", "inline_math", "a"),
            ("$$", "display_math", "b"),
            (r"\(", "inline_math", "c"),
            (r"\[", "display_math", "d"),
        ]
        assert [text[s.start : s.end] for s in spans] == ["$a$", "$$b$$", r"\(c\)", r"\[d\]"]

    def test_spans_skip_code_fences_on_demand(self):
        text = "```\n$not math$\n```\n\n$x$"
        assert [s.content for s in iter_math_spans(text)] == ["not math", "x"]
        assert [s.content for s in iter_math_spans(text, skip_code_fences=True)] == ["x"]

    def test_split_formulas_yields_text_and_math(self):
        """Segmente enthalten den Formelinhalt *ohne* Delimiter (Bestand)."""
        text = r"Text \( x \) und $y$ Ende"
        segs = split_formulas(text)
        assert [s.kind for s in segs] == ["text", "inline_math", "text", "inline_math", "text"]
        assert [s.content for s in segs] == ["Text ", " x ", " und ", "y", " Ende"]
        assert [s.kind for s in segs] == [
            "text",
            "inline_math",
            "text",
            "inline_math",
            "text",
        ]


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


# ---------------------------------------------------------------------------
# Audit B-1: Chunk-Grenzen reißen keine Formatierungen/Formeln mehr entzwei
# ---------------------------------------------------------------------------
class TestChunkSyntaxSafety:
    @staticmethod
    def _texts(messages):
        return [
            m.payload["rich_message"]["markdown"]
            if m.kind == "rich"
            else m.payload["text"]
            for m in messages
        ]

    def test_bold_over_chunk_boundary_stays_balanced(self):
        text = "**" + "x" * 5_000 + "**"
        texts = self._texts(build_messages(text, "1"))
        assert len(texts) > 1
        for chunk in texts:
            assert chunk.count("<b>") == chunk.count("</b>"), chunk[:80]
            assert len(chunk) <= REGULAR_MESSAGE_MAX_CHARS

    def test_entities_never_split_at_chunk_end(self):
        # '<' wird zu '&lt;' escaped; harte Schnitte duerfen die Entity nicht trennen
        text = "<" * 20_000
        texts = self._texts(build_messages(text, "1"))
        for chunk in texts:
            assert not re.search(r"&(amp|lt|gt)$", chunk)

    def test_display_math_not_split_across_rich_chunks(self):
        body = "$$\n" + "\n".join(f"x_{i} = \\frac{{{i}}}{{{i + 1}}}" for i in range(5000)) + "\n$$"
        texts = self._texts(build_messages(body, "1"))
        assert len(texts) > 1
        for chunk in texts:
            assert chunk.count("$$") % 2 == 0, chunk[:80]
            assert len(chunk) <= RICH_MESSAGE_MAX_CHARS

    def test_code_fence_not_split_across_rich_chunks(self):
        code = "```py\n" + "\n".join(f"v_{i} = {i}" for i in range(12000)) + "\n```"
        # Fence allein loest keinen Rich-Pfad aus -> mit Formel kombinieren
        texts = self._texts(build_messages("$x$\n\n" + code, "1"))
        assert len(texts) > 1
        for chunk in texts:
            assert chunk.count("```") % 2 == 0

    def test_content_survives_reassembly(self):
        words = " ".join(f"w{i}" for i in range(6000))
        text = f"**{words[:200]}** {words}"
        texts = self._texts(build_messages(text, "1"))
        assert len(texts) > 1
        # Referenz: unchunked konvertiertes HTML. Tag-freier Inhalt muss
        # identisch sein (Reassembly bis auf Whitespace/Chunk-Fugen).
        from telegram_formatter.utils import markdown_to_html

        expected = re.sub(r"<[^>]+>", "", markdown_to_html(text))
        produced = re.sub(r"<[^>]+>", "", "\n".join(texts))
        assert "".join(produced.split()) == "".join(expected.split())

    def test_safe_chunk_hard_falls_back_on_oversized_unit(self):
        # Einzelne, das Limit ueberschreitende Formel: harter Schnitt, aber
        # keine Exception und jedes Stueck <= Limit.
        huge = "$" + "x" * (RICH_MESSAGE_MAX_CHARS + 500) + "$"
        chunks = _safe_chunk(huge, RICH_MESSAGE_MAX_CHARS)
        assert all(len(c) <= RICH_MESSAGE_MAX_CHARS for c in chunks)


# ---------------------------------------------------------------------------
# Audit B-4 / M-1 / B-12 / N-1: Formel-Erkennung, Escaping, Listen, NUL
# ---------------------------------------------------------------------------
class TestMathBoundaryAndEscaping:
    def test_prices_are_not_math(self):
        """B-4: '$100 und $200' ist ein Preis, keine Inline-Formel."""
        messages = build_messages("Das Buch kostet $100 und der Stift $200.", "1")
        assert all(m.kind == "regular" for m in messages)
        assert "$100" in messages[0].payload["text"]

    def test_real_inline_math_still_routes_to_rich(self):
        messages = build_messages("Energie: $E=mc^2$!", "1")
        assert messages[0].kind == "rich"
        assert "$E=mc^2$" in messages[0].payload["rich_message"]["markdown"]

    def test_math_may_not_cross_paragraphs(self):
        messages = build_messages("$a\n\nb$", "1")
        assert all(m.kind == "regular" for m in messages)

    def test_empty_dollars_not_display_math(self):
        assert not has_latex("$$$$")
        assert has_latex("$$x$$")

    def test_fence_language_allowlist(self):
        """M-1: Info-String landet im Attribut language="…" — nur sicheres Alphabet."""
        out = markdown_to_html('```"\nonclick="alert(1)\nfoo\n```')
        assert "language=" not in out          # Attribut komplett verworfen
        assert "onclick=&quot;" in out         # Inhalt escaped, nicht Attribut
        ok = markdown_to_html("```python\nx=1\n```")
        assert 'language="python"' in ok

    def test_quotes_escaped_in_link_href(self):
        """M-1: rohes " bricht href="…" auf — jetzt &quot;."""
        out = markdown_to_html('[klick](https://ex.com/")onmouseover="x)')
        assert '&quot;' in out
        assert out.count('"') <= 2  # nur die Attribut-Wrapper selbst

    def test_list_indent_preserved(self):
        """B-12: verschachtelte Listen kollabieren nicht mehr."""
        out = markdown_to_html("- top\n  - nested")
        assert "\n  \u2022 nested" in out

    def test_nul_stripped_before_placeholders(self):
        """N-1: NUL aus Nutertext würde Platzhalter des Stores kollidieren."""
        assert "\x00" not in normalize_text("a\x00b")
        out = markdown_to_html("x\x000\x00 y")  # sieht aus wie ein Platzhalter
        assert "\x00" not in out

    def test_deepseek_and_dollar_mixed_document(self):
        src = "Text \\(x^2\\) und Preis $5 und \\[ y \\] dann $10."
        out = convert_deepseek_latex_syntax(src)
        assert "$x^2$" in out and "$$ y $$" in out   # DeepSeek konvertiert
        assert "$5 und" in out and "$10." in out     # Preise unangetastet
        # Nach Konversion darf der gemischte Text keine Formel-Routing-False-Positives
        # mehr ausloesen, die ueber die echten Bloecke hinausgehen:
        segs = split_formulas(out)
        assert [s.kind for s in segs if s.kind != "text"] == ["inline_math", "display_math"]


# ---------------------------------------------------------------------------
# Redirect-URLs: Entpacken auf die Ziel-URL (v2.7.0)
# ---------------------------------------------------------------------------
class TestUnwrapRedirectUrl:
    """Unit-Tests für :func:`unwrap_redirect_url` (Regelwerk + Sicherheit)."""

    YOUTUBE_TARGET = "https://www.youtube.com/watch?v=VWsUuTQLEJQ&t=38"
    # Das Originalbeispiel: Such-KI-Zitat mit percent-kodiertem Ziel im q-Param.
    GOOGLE_SEARCH_WRAP = (
        "https://www.google.com/search?q=https%3A%2F%2Fwww.youtube.com%2Fwatch"
        "%3Fv%3DVWsUuTQLEJQ%26t%3D38"
    )

    @pytest.mark.parametrize(
        "url,expected",
        [
            # Nutzerbeispiel: google.com/search?q=<kodierte URL>
            (
                "https://www.google.com/search?q=https%3A%2F%2Fwww.youtube.com"
                "%2Fwatch%3Fv%3DVWsUuTQLEJQ%26t%3D38",
                YOUTUBE_TARGET,
            ),
            # klassische Google-Weiterleitung mit Tracking-Anhang
            (
                "https://www.google.com/url?sa=t&q=https%3A%2F%2Fexample.com"
                "%2Fseite&ved=2ahUKEwj",
                "https://example.com/seite",
            ),
            # regionale Google-Domain
            (
                "https://www.google.de/url?q=https%3A%2F%2Fexample.org%2Fx",
                "https://example.org/x",
            ),
            # YouTube-Beschreibungs-Redirect
            (
                "https://www.youtube.com/redirect?event=video_description"
                "&redir_token=QUFFLUhqbA&q=https%3A%2F%2Fexample.com%2Fvideo",
                "https://example.com/video",
            ),
            # Facebook-Ausstiegsseite
            (
                "https://l.facebook.com/l.php?u=https%3A%2F%2Fexample.com%2Ffb"
                "&h=AT0xyz",
                "https://example.com/fb",
            ),
            # DuckDuckGo-Exit
            (
                "https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fddg&rut=abc",
                "https://example.com/ddg",
            ),
            # Reddit-Exit
            (
                "https://out.reddit.com/t3_1abc?url=https%3A%2F%2Fexample.com%2Freddit"
                "&app_name=web2x",
                "https://example.com/reddit",
            ),
            # Steam-Linkfilter
            (
                "https://steamcommunity.com/linkfilter/?url=https%3A%2F%2Fexample.com%2Fsteam",
                "https://example.com/steam",
            ),
            # LinkedIn-Weiterleitung
            (
                "https://www.linkedin.com/redir/redirect?url=https%3A%2F%2Fexample.com%2Fin",
                "https://example.com/in",
            ),
        ],
    )
    def test_known_redirects_unwrap(self, url, expected):
        from telegram_formatter.utils import unwrap_redirect_url

        assert unwrap_redirect_url(url) == expected

    def test_bing_click_base64(self):
        """Bing /ck/a?u=a1<Base64URL> — Ziel ist base64-kodiert."""
        import base64

        from telegram_formatter.utils import unwrap_redirect_url

        token = base64.urlsafe_b64encode(b"https://example.com/item?id=42").decode().rstrip("=")
        url = f"https://www.bing.com/ck/a?!&&p=abc123&u=a1{token}&ntb=1"
        assert unwrap_redirect_url(url) == "https://example.com/item?id=42"

    def test_nested_redirects_unwrap_recursively(self):
        """Facebook-Seite linkt einen Google-Redirect -> beide Ebenen weg."""
        from urllib.parse import quote

        from telegram_formatter.utils import unwrap_redirect_url

        inner = "https://www.google.com/url?q=" + quote(self.YOUTUBE_TARGET, safe="")
        outer = "https://l.facebook.com/l.php?u=" + quote(inner, safe="") + "&h=AT0"
        assert unwrap_redirect_url(outer) == self.YOUTUBE_TARGET

    def test_double_percent_encoding(self):
        """Doppelt kodierte Ziele (ein Klassiker bei Copy-Paste-Ketten)."""
        from urllib.parse import quote

        from telegram_formatter.utils import unwrap_redirect_url

        url = "https://www.google.com/url?q=" + quote(quote("https://example.com/a", safe=""), safe="")
        assert unwrap_redirect_url(url) == "https://example.com/a"

    @pytest.mark.parametrize(
        "url",
        [
            # normale Suchanfrage: q ist keine URL -> unverändert
            "https://www.google.com/search?q=hallo+welt",
            "https://www.google.com/search?q=youtube+videos",
            # unbekannte Domain, selbst mit url-Parameter
            "https://example.com/redirect?url=https%3A%2F%2Fevil.example",
            # Phishing-Versuch: Host klingt nur wie Google
            "https://www.evilgoogle.com/url?q=https%3A%2F%2Fexample.com",
            "https://notgoogle.de/url?q=https%3A%2F%2Fexample.com",
            # falscher Pfad bei bekanntem Host
            "https://www.google.com/maps?q=https%3A%2F%2Fexample.com",
            # kein http(s)-Ziel im Parameter -> niemals ersetzen (Injection-Schutz)
            "https://www.google.com/url?q=javascript:alert(1)",
            "https://www.google.com/url?q=data:text/html;base64,PHNjcmlwdD4=",
            # leere/fehlende Parameter
            "https://www.google.com/url?q=",
            "https://www.google.com/url?sa=t",
            # Kurz-URLn sind offline nicht auflösbar -> 1:1
            "https://t.co/abc123",
            "https://bit.ly/xyz",
            # keine URL
            "kein-link",
        ],
    )
    def test_non_redirects_stay_untouched(self, url):
        from telegram_formatter.utils import unwrap_redirect_url

        assert unwrap_redirect_url(url) == url

    def test_idempotent(self):
        from telegram_formatter.utils import unwrap_redirect_url

        once = unwrap_redirect_url(self.GOOGLE_SEARCH_WRAP)
        assert unwrap_redirect_url(once) == once


class TestRedirectLinksInMessages:
    """Integration: Redirect-Unwrap in beiden Konvertierungspfaden."""

    YOUTUBE_TARGET = TestUnwrapRedirectUrl.YOUTUBE_TARGET
    GOOGLE_SEARCH_WRAP = TestUnwrapRedirectUrl.GOOGLE_SEARCH_WRAP

    def test_user_reported_artifact_regular_path(self):
        """Das gemeldete Beispiel: [[00:38]([url](url))] -> sauberer Link."""
        src = f"[[00:38]([{self.GOOGLE_SEARCH_WRAP}]({self.GOOGLE_SEARCH_WRAP}))]"
        html = markdown_to_html(src)
        assert html == '<a href="https://www.youtube.com/watch?v=VWsUuTQLEJQ&amp;t=38">00:38</a>'
        assert "google.com" not in html

    def test_user_reported_artifact_rich_path(self):
        src = f"$x$ [[00:38]([{self.GOOGLE_SEARCH_WRAP}]({self.GOOGLE_SEARCH_WRAP}))]"
        messages = build_messages(src, "1")
        assert messages[0].kind == "rich"
        md = messages[0].payload["rich_message"]["markdown"]
        assert f"[00:38]({self.YOUTUBE_TARGET})" in md
        assert "google.com" not in md

    def test_html_href_unwrapped(self):
        html = markdown_to_html(f"[Video]({self.GOOGLE_SEARCH_WRAP})")
        assert f'<a href="{self.YOUTUBE_TARGET.replace("&", "&amp;")}">Video</a>' in html

    def test_rich_href_unwrapped(self):
        md = markdown_to_rich_markdown(f"[Video]({self.GOOGLE_SEARCH_WRAP})")
        assert f"[Video]({self.YOUTUBE_TARGET})" in md

    def test_label_repeating_redirect_shows_target(self):
        url = "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fx"
        html = markdown_to_html(f"[{url}]({url})")
        assert '<a href="https://example.com/x">https://example.com/x</a>' in html

    def test_bare_redirect_url_in_text(self):
        html = markdown_to_html(f"Schau mal: {self.GOOGLE_SEARCH_WRAP}. Ende.")
        assert self.YOUTUBE_TARGET.replace("&", "&amp;") in html
        assert "google.com" not in html
        assert html.endswith("Ende.")  # Satzbau bleibt erhalten

    def test_urls_inside_code_are_never_rewritten(self):
        inline = markdown_to_html(f"`{self.GOOGLE_SEARCH_WRAP}`")
        assert "google.com" in inline
        fence = markdown_to_html(f"```\n{self.GOOGLE_SEARCH_WRAP}\n```")
        assert "google.com" in fence
        rich = markdown_to_rich_markdown(f"`{self.GOOGLE_SEARCH_WRAP}`")
        assert "google.com" in rich

    def test_normal_links_and_searches_untouched(self):
        html = markdown_to_html("[Suche](https://www.google.com/search?q=hallo)")
        assert '<a href="https://www.google.com/search?q=hallo">Suche</a>' in html
        plain = markdown_to_html("[Text](https://example.com)")
        assert '<a href="https://example.com">Text</a>' in plain

    def test_nested_artifact_inside_list(self):
        """Artefakt-Glättung darf Listenpunkte nicht zerlegen."""
        src = f"- Clip: [[00:38]([{self.GOOGLE_SEARCH_WRAP}]({self.GOOGLE_SEARCH_WRAP}))]"
        html = markdown_to_html(src)
        assert html.startswith("• Clip: <a href=")
        assert "google.com" not in html


# ---------------------------------------------------------------------------
# Reine URLs & Gemini-Link-Bereinigung (v2.15.0)
# ---------------------------------------------------------------------------
# Auslöser (Nutzerbeispiel): Gemini hängt an jeden zitierten Link die
# Tracking-IDs `ra=gemini` und `xstg=<~1 500 Zeichen Token>` und wickelt
# Zeitstempel-Zitate doppelt: [[00:00]([url&amp;…](url&…))]. Vor v2.15.0
# standen die Monster-URLs 1:1 in den Payloads — ein Text mit ~20 Zitaten
# wurde dadurch unübersichtlich lang und in viele Nachrichten zerteilt.

#: Repräsentatives Gemini-Klicktoken (Alphabet/Länge wie im Original).
_GEMINI_XSTG = (
    "CAMSiA0VhArM2swNqASJ0YMFoMpV0QbnLL4GuwbEBuUCxQW5BYEEoA9OsgSuA8iVng" + "A" * 1400
)
_GEMINI_VIDEO = "https://www.youtube.com/watch?v=72P-zstEqps"


def _gemini_cite(label: str, seconds: int) -> str:
    """Baut das doppelt gewickelte Gemini-Zitat aus dem Nutzerbeispiel nach."""
    amp = f"{_GEMINI_VIDEO}&amp;ra=gemini&amp;xstg={_GEMINI_XSTG}&amp;t={seconds}"
    raw = f"{_GEMINI_VIDEO}&ra=gemini&xstg={_GEMINI_XSTG}&t={seconds}"
    return f"[[{label}]([{amp}]({raw}))]"


#: Die (defekte) Video-Zeile aus dem Nutzerbeispiel: derselbe Link zweimal,
#: ineinander kopiert.
_GEMINI_VIDEO_LINE = (
    "🔗 **Video Link:** [[https://youtube.com/shorts/72P-zstEqps]"
    "(https://www.google.com/search?q=https://youtube.com/shorts/72P-zstEqps)]"
    "(https://youtube.com/shorts/72P-zstEqps]"
    "(https://www.google.com/search?q=https://youtube.com/shorts/72P-zstEqps))"
)


class TestCleanUrl:
    """Unit-Tests für :func:`clean_url` (reine URL: Redirect + Tracking)."""

    @pytest.mark.parametrize(
        "url,expected",
        [
            # Das Gemini-Original: ra/xstg weg, v und t (funktional) bleiben.
            (
                f"{_GEMINI_VIDEO}&ra=gemini&xstg={_GEMINI_XSTG}&t=5",
                f"{_GEMINI_VIDEO}&t=5",
            ),
            # HTML-escapter Export (&amp;) wird zuerst dekodiert.
            (
                f"{_GEMINI_VIDEO}&amp;ra=gemini&amp;xstg=TOKEN&amp;t=7",
                f"{_GEMINI_VIDEO}&t=7",
            ),
            ("https://ex.com/?a=1&#38;utm_source=x", "https://ex.com/?a=1"),
            ("https://ex.com/?a=1&#x26;utm_source=x", "https://ex.com/?a=1"),
            ("https://ex.com/?a=1&AMP;utm_source=x", "https://ex.com/?a=1"),
            # utm_* und gängige Klick-IDs
            (
                "https://ex.com/artikel?utm_source=nl&utm_medium=mail&id=9",
                "https://ex.com/artikel?id=9",
            ),
            ("https://ex.com/?fbclid=ABC&seite=2", "https://ex.com/?seite=2"),
            ("https://ex.com/?gclid=ABC&seite=2", "https://ex.com/?seite=2"),
            ("https://ex.com/?msclkid=ABC&seite=2", "https://ex.com/?seite=2"),
            ("https://ex.com/?mc_cid=ABC&mc_eid=DEF&x=1", "https://ex.com/?x=1"),
            # Nur Tracking -> Query fällt ganz weg (kein "?" übrig).
            ("https://ex.com/x?utm_source=a&fbclid=b", "https://ex.com/x"),
            # Reihenfolge + Kodierung der übrigen Parameter bleiben byte-genau.
            (
                "https://ex.com/?b=%2Fweg&utm_m=x&a=hallo+welt",
                "https://ex.com/?b=%2Fweg&a=hallo+welt",
            ),
            # Fragment bleibt erhalten.
            ("https://ex.com/p?utm_x=1&a=2#anker", "https://ex.com/p?a=2#anker"),
            # Parameter ohne Wert bleibt.
            ("https://ex.com/?flag&utm_x=1", "https://ex.com/?flag"),
            # Percent-kodierter Parametername wird erkannt.
            ("https://ex.com/?utm%5Fsource=x&a=1", "https://ex.com/?a=1"),
            # Groß-/Kleinschreibung bei Namen und beim Gemini-Wert.
            ("https://ex.com/?RA=GEMINI&a=1", "https://ex.com/?a=1"),
            ("https://ex.com/?UTM_SOURCE=x&a=1", "https://ex.com/?a=1"),
            # Funktionale YouTube-Parameter bleiben alle.
            (
                "https://www.youtube.com/watch?v=abc&list=PL1&index=3&t=9",
                "https://www.youtube.com/watch?v=abc&list=PL1&index=3&t=9",
            ),
            # Suchparameter sind funktional, kein Tracking.
            (
                "https://www.youtube.com/results?search_query=katze",
                "https://www.youtube.com/results?search_query=katze",
            ),
        ],
    )
    def test_tracking_params_removed(self, url, expected):
        assert clean_url(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            # ra nur als Gemini-Attribution entfernen, fremde Werte bleiben.
            "https://ex.com/?ra=partner&a=1",
            "https://ex.com/?ra=&a=1",
            # Bewusste Grenze: generische Namen (si, ref) werden NICHT
            # entfernt — sie können auf anderen Seiten funktional sein.
            "https://youtu.be/abc123?si=xyz",
            "https://ex.com/?ref=start",
            # Normale Suche: q ist keine URL und kein Tracking.
            "https://www.google.com/search?q=hallo+welt",
            # Keine Query / keine URL / kein http(s) -> unverändert.
            "https://example.com/ohne-query",
            "https://example.com/mit?frage=ohne-tracking",
            "kein-link",
            "",
            "javascript:alert(1)?utm_x=1",
            "ftp://ex.com/x?utm_a=1",
            "https://t.co/abc123",
        ],
    )
    def test_untouched(self, url):
        assert clean_url(url) == url

    def test_redirect_then_tracking(self):
        """Wrapper-Ziel mit Tracking-Anhang: erst entpacken, dann reinigen."""
        from urllib.parse import quote

        wrapped = "https://www.google.com/url?q=" + quote(
            "https://example.com/x?utm_source=g&a=1", safe=""
        )
        assert clean_url(wrapped) == "https://example.com/x?a=1"

    def test_strips_surrounding_whitespace(self):
        assert clean_url("  https://ex.com/?utm_a=1  ") == "https://ex.com/"

    def test_idempotent(self):
        url = f"{_GEMINI_VIDEO}&amp;ra=gemini&amp;xstg={_GEMINI_XSTG}&amp;t=3"
        once = clean_url(url)
        assert clean_url(once) == once == f"{_GEMINI_VIDEO}&t=3"


class TestCleanGeminiLinks:
    """Die eigenständige Textbereinigung :func:`clean_gemini_links`."""

    def test_timestamp_citation_becomes_pure_link(self):
        src = f"Entgiftung über die Fußsohlen {_gemini_cite('00:00', 0)}."
        out = clean_gemini_links(src)
        assert out == f"Entgiftung über die Fußsohlen [00:00]({_GEMINI_VIDEO}&t=0)."
        assert "xstg" not in out and "ra=gemini" not in out and "&amp;" not in out

    def test_citation_for_any_host(self):
        src = (
            "[[1]([https://example.com/a?x=1&amp;ra=gemini&amp;xstg=TOKEN]"
            "(https://example.com/a?x=1&ra=gemini&xstg=TOKEN))]"
        )
        assert clean_gemini_links(src) == "[1](https://example.com/a?x=1)"

    def test_text_label_is_preserved(self):
        src = (
            "[[Quelle]([https://ex.com/s?a=1&amp;xstg=T]"
            "(https://ex.com/s?a=1&xstg=T))]"
        )
        assert clean_gemini_links(src) == "[Quelle](https://ex.com/s?a=1)"

    def test_duplicated_video_link_line(self):
        out = clean_gemini_links(_GEMINI_VIDEO_LINE)
        assert out == (
            "🔗 **Video Link:** [https://youtube.com/shorts/72P-zstEqps]"
            "(https://youtube.com/shorts/72P-zstEqps)"
        )
        assert "google.com" not in out

    def test_single_amp_escaped_link(self):
        src = f"[00:00]({_GEMINI_VIDEO}&amp;ra=gemini&amp;xstg=TOKEN&amp;t=7)"
        assert clean_gemini_links(src) == f"[00:00]({_GEMINI_VIDEO}&t=7)"

    def test_bare_gemini_url_with_trailing_period(self):
        src = f"Siehe {_GEMINI_VIDEO}&ra=gemini&xstg={_GEMINI_XSTG}&t=3. Ende."
        assert clean_gemini_links(src) == f"Siehe {_GEMINI_VIDEO}&t=3. Ende."

    def test_amp_in_prose_becomes_ampersand(self):
        assert clean_gemini_links("Wirkungen &amp; Vorteile") == "Wirkungen & Vorteile"

    def test_triple_wrapped_citation(self):
        src = "[" + _gemini_cite("00:05", 5) + "]"
        assert clean_gemini_links(src) == f"[00:05]({_GEMINI_VIDEO}&t=5)"

    def test_code_is_never_rewritten(self):
        url = f"{_GEMINI_VIDEO}&amp;ra=gemini&amp;xstg={_GEMINI_XSTG}&amp;t=1"
        assert clean_gemini_links(f"`{url}`") == f"`{url}`"
        fence = f"```\n{url}\n```"
        assert clean_gemini_links(fence) == fence

    def test_length_collapses_and_is_idempotent(self):
        src = " ".join(_gemini_cite(f"00:{i:02d}", i) for i in range(20))
        once = clean_gemini_links(src)
        assert len(once) < len(src) / 20  # ~60 000 -> ~1 200 Zeichen
        assert clean_gemini_links(once) == once
        assert "xstg" not in once

    def test_normal_markdown_untouched(self):
        src = "**fett** [Suche](https://www.google.com/search?q=hallo+welt) $x^2$"
        assert clean_gemini_links(src) == src


class TestGeminiLinksInMessages:
    """Integration: Gemini-Bereinigung in HTML- und Rich-Pfad."""

    # Ausschnitt des gemeldeten Fußbad-Beispiels (Struktur 1:1).
    EXAMPLE = (
        "Das Video erklärt ein Fußbad " + _gemini_cite("00:00", 0) + ". "
        "Die Füße besitzen über 7.000 Nervenenden " + _gemini_cite("00:05", 5) + ".\n\n"
        "**Wirkungen &amp; Vorteile laut Video:**\n\n"
        "* 🧠 Klarerer Kopf " + _gemini_cite("00:54", 54) + "\n\n"
        + _GEMINI_VIDEO_LINE
    )

    def test_user_example_regular_path(self):
        html = markdown_to_html(self.EXAMPLE)
        assert f'<a href="{_GEMINI_VIDEO}&amp;t=0">00:00</a>' in html
        assert f'<a href="{_GEMINI_VIDEO}&amp;t=5">00:05</a>' in html
        assert f'<a href="{_GEMINI_VIDEO}&amp;t=54">00:54</a>' in html
        assert (
            '<a href="https://youtube.com/shorts/72P-zstEqps">'
            "https://youtube.com/shorts/72P-zstEqps</a>" in html
        )
        # Kein Tracking, kein doppelt escaptes &amp;, kein Link-Müll.
        assert "xstg" not in html and "ra=" not in html
        assert "&amp;amp;" not in html
        assert "<b>Wirkungen &amp; Vorteile laut Video:</b>" in html
        assert "](" not in html and "google.com" not in html

    def test_user_example_rich_path(self):
        messages = build_messages(self.EXAMPLE + "\n\n$x^2$", "1")
        assert len(messages) == 1 and messages[0].kind == "rich"
        md = messages[0].payload["rich_message"]["markdown"]
        assert f"[00:00]({_GEMINI_VIDEO}&t=0)" in md
        assert f"[00:54]({_GEMINI_VIDEO}&t=54)" in md
        assert "**Wirkungen & Vorteile laut Video:**" in md
        assert "xstg" not in md and "ra=" not in md and "&amp;" not in md

    def test_many_citations_stay_one_message(self):
        """Vor v2.15.0 zerteilten die Token-URLs denselben Text in viele
        Nachrichten; mit reinen URLs passt er in eine."""
        src = "\n\n".join(
            f"Absatz {i} mit Zitat {_gemini_cite(f'00:{i:02d}', i)}."
            for i in range(20)
        )
        messages = build_messages(src, "1")
        assert len(messages) == 1
        assert "xstg" not in messages[0].payload["text"]

    def test_citation_inside_table_cell(self):
        src = (
            "| Zutat | Stelle |\n|---|---|\n"
            f"| Bittersalz | {_gemini_cite('00:26', 26)} |\n\n$x$"
        )
        md = markdown_to_rich_markdown(src)
        assert f"[00:26]({_GEMINI_VIDEO}&t=26)" in md
        assert "| Bittersalz |" in md and "xstg" not in md

    def test_amp_escaped_formula_separator_is_fixed(self):
        # `&amp;` in einer Formel (HTML-Export) wird zum LaTeX-Trenner `&`.
        md = markdown_to_rich_markdown(r"\(a &amp;= b\)")
        assert md == "$a &= b$"

    def test_urls_inside_code_keep_tracking_token(self):
        url = f"{_GEMINI_VIDEO}&ra=gemini&xstg={_GEMINI_XSTG}&t=1"
        assert "xstg=" in markdown_to_html(f"`{url}`")
        assert "xstg=" in markdown_to_html(f"```\n{url}\n```")
        assert "xstg=" in markdown_to_rich_markdown(f"`{url}`")

    def test_label_repeating_tracking_url_shows_pure_url(self):
        url = f"{_GEMINI_VIDEO}&xstg={_GEMINI_XSTG}&t=9"
        html = markdown_to_html(f"[{url}]({url})")
        pure = f"{_GEMINI_VIDEO}&amp;t=9"
        assert f'<a href="{pure}">{pure}</a>' in html


class TestGeminiCleanupPerformance:
    """Die Bereinigung darf kein quadratisches Backtracking einführen."""

    def test_citation_heavy_input_is_fast(self):
        src = " ".join(_gemini_cite("00:01", 1) for _ in range(30))  # ~88 000 Z.
        start = time.perf_counter()
        out = markdown_to_html(src)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"{elapsed:.2f}s für 30 Gemini-Zitate"
        assert "xstg" not in out

    def test_bracket_storm_is_linear(self):
        # Fixpunkt-Schleife (clean_gemini_links) und beide Konverterpfade
        # (Follow-up aus 2026-10_GEMINI-LINK-CLEANUP in v2.15.1 behoben)
        # müssen bei 50 000 offenen Klammern linear durchlaufen.
        start = time.perf_counter()
        clean_gemini_links("[" * 50_000)
        elapsed = time.perf_counter() - start
        assert elapsed < 3.0, f"{elapsed:.2f}s für 50 000 Klammern"


def test_public_api_exports_cleaning_functions():
    import telegram_formatter

    assert telegram_formatter.clean_url is clean_url
    assert telegram_formatter.clean_gemini_links is clean_gemini_links
    assert "clean_url" in telegram_formatter.__all__
    assert "clean_gemini_links" in telegram_formatter.__all__


# ---------------------------------------------------------------------------
# Regression v2.13.0 — Code-Review
# ---------------------------------------------------------------------------
class TestFenceRegexIsNotQuadratic:
    """Der Fence-Info-String war unbeschränkt (`[^\\n]*`) — quadratische Zeit.

    ``[^\n]*`` bekommt keine ``sre``-Literal-Tail-Optimierung: an jeder der
    ~n Kandidatenpositionen frisst die Gruppe Zeichen für Zeichen bis zum
    Zeilenende. Bei einer Anfrage voller Backticks gibt es kein ``\n``, also
    scheitern *alle* Positionen nach O(n) Fehlversuchen — ~10^9 Schritte in
    einer einzigen, unauthentifizierten Anfrage. Gemessen in v2.12.0:
    0,02 s / 0,11 s / 0,39 s für 2 000 / 4 000 / 8 000 Backticks (quadratisch).
    """

    @staticmethod
    def _time(fn, payload):
        start = time.perf_counter()
        fn(payload)
        return time.perf_counter() - start

    def test_backtick_storm_is_linear_not_quadratic(self):
        # Vergleich gegen das Doppelte: linear ~2x, quadratisch ~4x. Die
        # Schwelle liegt bewusst locker, damit sie auf langsamen CI nicht
        # flake-t.
        small = self._time(markdown_to_html, "`" * 8_000)
        large = self._time(markdown_to_html, "`" * 16_000)
        assert large < max(small * 3.5, 0.05), (
            f"quadratisches Backtracking: {small:.4f}s -> {large:.4f}s"
        )

    def test_absolute_budget_at_max_input_size(self):
        # MAX_INPUT_CHARS liegt bei 100 000; in v2.12.0 kostete allein das
        # Regex ~60 s. Die Schwelle ist um Faktor ~20 gesetzt.
        elapsed = self._time(markdown_to_html, "`" * 100_000)
        assert elapsed < 3.0, f"{elapsed:.2f}s fuer 100 000 Backticks"

    def test_both_entry_points_use_the_bounded_pattern(self):
        # Beide Aufrufstellen (HTML- und Rich-Pfad) teilen dasselbe Muster —
        # Regression gegen eine neue, unbeschränkte Inline-Variante.
        from telegram_formatter import utils as u

        assert u._FENCE_BLOCK_RE.pattern == r"```([^`\n]{0,64})\n(.*?)```"
        assert u._FENCE_BLOCK_RE.flags & re.DOTALL

    def test_backtick_in_info_string_is_not_a_fence(self):
        """CommonMark: die Info-String eines Backtick-Fences hat kein Backtick.

        ```` ```a`b ```` ist gar kein Fence. Mit `[^`\n]{0,64}` fällt der
        Block durch, statt faelschlich geschluckt zu werden.
        """
        src = "```a`b\ncode\n```"
        assert "<pre" not in markdown_to_html(src)

    def test_normal_fences_still_work(self):
        for src, want in (
            ("```python\na = 1\n```", '<pre language="python">a = 1</pre>'),
            ("```\nplain\n```", "<pre>plain</pre>"),
            ("```py\nx\n```", '<pre language="py">x</pre>'),
        ):
            assert want in markdown_to_html(src)
            assert "```" in markdown_to_rich_markdown(src)


# ---------------------------------------------------------------------------
# Regression v2.15.1 — Follow-up aus 2026-10_GEMINI-LINK-CLEANUP
# ---------------------------------------------------------------------------
class TestLinkRegexIsNotQuadratic:
    """Link-Ersetzung im HTML-/Rich-Konverter und in der Vorschau war O(n²).

    ``r"\\[([^\\]]+)\\]\\((https?://[^)\\s]+)\\)"`` scannte bei einer Eingabe
    voller ``[`` ohne schließendes ``]`` (z. B. ``"[" * 50_000``) von *jeder*
    ``[``-Position bis zum Textende (~10,2 s für 50 000 Klammern, ~41 s bei
    ``MAX_INPUT_CHARS = 100_000``). Ebenso verschluckte ``[^\\]]+`` ohne
    Zeilenumbruch-Ausschluss Absätze, Überschriften und Tabellen zwischen
    einer verwaisten ``[`` und einem späteren Link.

    Mit :data:`telegram_formatter.utils._LINK_RE`
    (``r"\\[([^\\]\\n]{1,256})\\]\\((https?://[^)\\s\\[\\]]+)\\)"``) ist der
    Aufwand linear (O(n)), während ``[`` im Linktext (z. B.
    ``[Intervall [0, 1)](https://…)``) weiterhin erlaubt bleibt.
    """

    @staticmethod
    def _time(fn, payload):
        start = time.perf_counter()
        fn(payload)
        return time.perf_counter() - start

    @pytest.mark.parametrize("converter", [markdown_to_html, markdown_to_rich_markdown])
    def test_bracket_storm_scales_linearly(self, converter):
        small = self._time(converter, "[" * 8_000)
        large = self._time(converter, "[" * 16_000)
        assert large < max(small * 3.5, 0.08), (
            f"quadratisches Backtracking in {converter.__name__}: "
            f"{small:.4f}s -> {large:.4f}s"
        )

    @pytest.mark.parametrize("converter", [markdown_to_html, markdown_to_rich_markdown])
    def test_bracket_storm_50k_and_max_input_budget(self, converter):
        # Vor v2.15.1: ~10,2 s bei 50 000 und ~41 s bei 100 000 Klammern.
        elapsed_50k = self._time(converter, "[" * 50_000)
        assert elapsed_50k < 3.0, (
            f"{elapsed_50k:.2f}s fuer 50 000 '[' in {converter.__name__}"
        )
        elapsed_100k = self._time(converter, "[" * 100_000)
        assert elapsed_100k < 3.0, (
            f"{elapsed_100k:.2f}s fuer 100 000 '[' in {converter.__name__}"
        )

    def test_build_messages_bracket_storm_both_paths(self):
        # Regular-Pfad und Rich-Pfad (mit LaTeX-Trigger) samt Chunking.
        assert self._time(lambda s: build_messages(s, "1"), "[" * 50_000) < 3.0
        assert self._time(lambda s: build_messages(s, "1"), "$x$ " + "[" * 50_000) < 3.0

    @pytest.mark.parametrize(
        "converter",
        [clean_gemini_links, markdown_to_html, markdown_to_rich_markdown],
    )
    def test_unclosed_link_target_storm_is_fast(self, converter):
        # Wiederholte ungeschlossene Link-Ziele `[a](https://x` ohne `)`:
        # `[^)\s\[\]]+` stoppt am nächsten `[` statt bis zum Textende zu laufen.
        payload = "[a](https://x" * 5_000
        elapsed = self._time(converter, payload)
        assert elapsed < 3.0, (
            f"{elapsed:.2f}s fuer ungeschlossene Link-Ziele in {converter.__name__}"
        )

    def test_pipe_storm_in_table_fallback_is_fast(self):
        # Einzelzeile voller `|` ohne zweite Zeile: `_TABLE_FALLBACK_RE`
        # (`^[^\n|]*\|.*$`) darf nicht über alle `|`-Positionen backtracken.
        elapsed = self._time(markdown_to_html, "|" * 50_000)
        assert elapsed < 3.0, f"{elapsed:.2f}s fuer 50 000 '|'"

    def test_all_entry_points_share_bounded_link_pattern(self):
        from telegram_formatter import preview as p
        from telegram_formatter import utils as u

        assert u._LINK_LABEL_MAX_CHARS == 256
        assert (
            u._LINK_RE.pattern
            == r"\[([^\]\n]{1,256})\]\((https?://[^)\s\[\]]+)\)"
        )
        assert p._LINK_RE is u._LINK_RE

    @pytest.mark.parametrize(
        ("src", "expected_html", "expected_rich"),
        [
            (
                "[Intervall [0, 1)](https://example.com)",
                '<a href="https://example.com">Intervall [0, 1)</a>',
                "[Intervall [0, 1)](https://example.com)",
            ),
            (
                "[Abschnitt [A](https://example.com/path)",
                '<a href="https://example.com/path">Abschnitt [A</a>',
                "[Abschnitt [A](https://example.com/path)",
            ),
            (
                "[Intervall [0, 1) mit **fett** und __unter__](https://example.com/a_b)",
                '<a href="https://example.com/a_b">Intervall [0, 1) mit <b>fett</b> und <u>unter</u></a>',
                "[Intervall [0, 1) mit **fett** und <u>unter</u>](https://example.com/a_b)",
            ),
            (
                "[Intervall [0, 1)](https://www.youtube.com/watch?v=72P-zstEqps&ra=gemini&xstg=TOKEN&t=5)",
                '<a href="https://www.youtube.com/watch?v=72P-zstEqps&amp;t=5">Intervall [0, 1)</a>',
                "[Intervall [0, 1)](https://www.youtube.com/watch?v=72P-zstEqps&t=5)",
            ),
        ],
    )
    def test_labels_with_open_bracket_still_work(
        self, src, expected_html, expected_rich
    ):
        assert markdown_to_html(src) == expected_html
        assert markdown_to_rich_markdown(src) == expected_rich

    def test_stray_bracket_does_not_swallow_across_newlines_html(self):
        src = (
            "Absatz 1 mit [offener Klammer\n\n"
            "# Titel\n\n"
            "Absatz 2 mit [Quelle](https://example.com)"
        )
        out = markdown_to_html(src)
        assert "Absatz 1 mit [offener Klammer" in out
        assert "<b>🚀 Titel</b>" in out
        assert '<a href="https://example.com">Quelle</a>' in out

    def test_stray_bracket_does_not_swallow_table_in_rich_path(self):
        src = (
            "Intervall [0, 1)\n\n"
            "A | B\n1 | 2\n\n"
            "Siehe [Quelle](https://example.com)"
        )
        out = markdown_to_rich_markdown(src)
        assert "Intervall [0, 1)" in out
        assert "| A | B |\n|---|---|\n| 1 | 2 |" in out
        assert "[Quelle](https://example.com)" in out

    def test_label_at_and_above_length_cap(self):
        from telegram_formatter import utils as u

        cap = u._LINK_LABEL_MAX_CHARS
        exact_label = "a" * cap
        over_label = "a" * (cap + 1)

        exact_src = f"[{exact_label}](https://example.com)"
        over_src = f"[{over_label}](https://example.com)"

        assert f'<a href="https://example.com">{exact_label}</a>' == markdown_to_html(
            exact_src
        )
        assert "<a " not in markdown_to_html(over_src)
        assert over_src == markdown_to_rich_markdown(over_src)

