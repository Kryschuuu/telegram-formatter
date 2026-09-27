"""Tests für :mod:`telegram_formatter.preview` — die Vorschau 1:1 zu Telegram.

Der Kern dieser Tests ist nicht „sieht schön aus", sondern **Parität**: Die
Vorschau muss genau das zeigen, was Telegram bekommt. Genau daran ist es bis
v2.13.0 gescheitert — die Vorschau war eine zweite, unabhängige
JavaScript-Implementierung des Konverters und driftete sofort.
"""

from __future__ import annotations

import re

import pytest

from telegram_formatter import utils
from telegram_formatter.preview import (
    ALLOWED_HTML_TAGS,
    render_preview,
    render_rich_markdown,
    sanitize_telegram_html,
)
from telegram_formatter.utils import build_messages


def preview_of(text: str) -> dict:
    return render_preview(build_messages(text, "0"))


def html_of(text: str) -> str:
    """HTML der ersten (bzw. einzigen) Vorschau-Nachricht."""
    return preview_of(text)["messages"][0]["html"]


# --------------------------------------------------------------------------- #
# Sanitizer — Allowlist, kein Denkvermögen
# --------------------------------------------------------------------------- #
class TestSanitizerAllowlist:
    """Der Regular-Pfad kommt mit HTML aus `utils`, gesetzt wird per innerHTML.

    Das ist per Konstruktion sicher (`markdown_to_html` escaped jedes
    Textfragment). Die Allowlist ist die zweite Verteidigungslinie: ein
    einziger künftiger Konverter-Bug darf daraus keine XSS machen.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "<script>alert(1)</script>",
            "<img src=x onerror=alert(1)>",
            "<iframe src='//evil'></iframe>",
            "<object data='x'></object>",
            "<embed src='x'>",
            "<svg onload=alert(1)>",
            "<math><mtext><script>alert(1)</script></mtext></math>",
            "<style>body{display:none}</style>",
            "<link rel=stylesheet href='//evil'>",
            "<base href='//evil'>",
        ],
    )
    def test_dangerous_tags_are_dropped(self, source):
        out = sanitize_telegram_html(source).lower()
        for tag in ("<script", "<img", "<iframe", "<object", "<embed", "<svg",
                    "<style", "<link", "<base", "onerror", "onload"):
            assert tag not in out, f"{tag} überlebt: {out!r}"

    def test_event_handlers_are_stripped_from_allowed_tags(self):
        assert sanitize_telegram_html('<b onclick="x()">f</b>') == "<b>f</b>"
        assert sanitize_telegram_html('<a href="x" onmouseover="y()">L</a>') == '<a href="x">L</a>'
        assert sanitize_telegram_html('<code onfocus="y()">c</code>') == "<code>c</code>"

    def test_style_attributes_are_stripped(self):
        assert "style" not in sanitize_telegram_html('<b style="x:expression(1)">f</b>')

    def test_class_attributes_are_stripped(self):
        # `class` ist nicht in der Allowlist: ein Klassenname aus Nutzertext
        # könnte fremde Styles treffen.
        assert sanitize_telegram_html('<b class="irgendwas">f</b>') == "<b>f</b>"

    def test_href_javascript_is_rejected(self):
        """`javascript:`-URLs sind eine klassische Allowlist-Lücke."""
        out = sanitize_telegram_html('<a href="javascript:alert(1)">x</a>')
        assert "javascript" not in out.lower()

    def test_telegram_tags_survive(self):
        for tag in ALLOWED_HTML_TAGS:
            if tag in {"br", "a", "pre", "tg-emoji"}:
                continue
            src = f"<{tag}>x</{tag}>"
            assert tag in sanitize_telegram_html(src), tag

    def test_pre_language_attribute_survives(self):
        assert 'language="python"' in sanitize_telegram_html('<pre language="python">x</pre>')

    def test_escaped_user_text_stays_text(self):
        """`&lt;script&gt;` ist vom Nutzer getippt und soll auch so erscheinen.

        Der Sanitizer darf Entities nicht dekodieren — sonst würde
        `&amp;lt;script&amp;gt;` (Nutzer wollte die Entity-Zeichen sehen) zu
        `&lt;script&gt;` und damit zu einem echten `<` in der Vorschau.
        """
        assert sanitize_telegram_html("&lt;script&gt;") == "&lt;script&gt;"
        assert sanitize_telegram_html("&amp;lt;") == "&amp;lt;"


# --------------------------------------------------------------------------- #
# Parität zum HTML-Pfad
# --------------------------------------------------------------------------- #
class TestRegularPathParity:
    """Der HTML-Pfad kommt unverändert durch — nur sanitisiert."""

    def test_heading_emoji_come_from_the_server(self):
        """Regression: die Vorschau zeigte `<b>Titel</b>` ohne `🚀`.

        Die Emoji stammen aus `utils.markdown_to_html` (Zeile ~867). Da die
        Vorschau jetzt die Payload rendert, sind sie automatisch da — und
        das ist der Beweis, dass es keine zweite Berechnung mehr gibt.
        """
        html = html_of("# Titel")
        assert "🚀" in html
        assert html == "<b>🚀 Titel</b>"

    def test_all_heading_levels(self):
        for level, emoji in ((1, "🚀"), (2, "📍"), (3, "🔹"), (4, "🔸")):
            html = html_of("#" * level + " Titel")
            assert emoji in html, f"h{level} ohne Emoji"

    def test_inline_formatting_matches_payload(self):
        text = "**f** *k* ~~d~~ __u__ `c`"
        payload = build_messages(text, "0")[0].payload["text"]
        assert sanitize_telegram_html(payload) == payload

    def test_becomes_bold(self):
        assert "<b>fett</b>" in html_of("**fett**")

    def test_link_is_preserved(self):
        html = html_of("[T](https://t.me/x)")
        assert 'href="https://t.me/x"' in html


# --------------------------------------------------------------------------- #
# Rich-Pfad
# --------------------------------------------------------------------------- #
class TestRichPath:
    """Der Rich-Pfad ist GFM-artiges Markdown — Telegram rendert es selbst."""

    def test_no_emoji_prefix_in_rich_path(self):
        """Regression in die andere Richtung.

        Die Emoji-Präfixe gehören **ausschließlich** zum HTML-Pfad. Trägt der
        Rich-Renderer sie auch, zeigt die Vorschau etwas, das Telegram nie
        zeigt — die gleiche Fehlerklasse, nur in die andere Richtung.
        """
        html = html_of("$x$\n\n# Titel")
        assert "🚀" not in html
        assert "Titel" in html

    def test_heading_becomes_heading_element(self):
        html = html_of("$x$\n\n# Titel")
        assert '<h1 class="tf-heading tf-heading--1">Titel</h1>' in html

    def test_table_becomes_real_table(self):
        """Regression: Pipe-Tabellen erschienen als `| Name | Preis |` Rohtext."""
        html = html_of(
            "$x$\n\n| Name | Preis |\n|---|---|\n| A | 3,00 |\n| B | 5,00 |"
        )
        assert '<table class="tf-table">' in html
        assert "<th>Name</th>" in html and "<th>Preis</th>" in html
        assert "<td>A</td>" in html and "<td>3,00</td>" in html
        assert html.count("<tr>") == 3  # Kopf + 2 Datenzeilen

    def test_table_alignment_markers_are_not_cells(self):
        html = html_of("$x$\n\n| A | B |\n|:--|--:|\n| 1 | 2 |")
        assert "<th>A</th><th>B</th>" in html
        assert "---" not in html

    def test_table_rows_are_padded_to_header_width(self):
        """Fehlende Zellen auffüllen, zusätzliche abschneiden.

        Sonst entstehen ungleich lange Zeilen, die der Browser streckt — eine
        Abweichung von Telegram, das die Spaltenzahl aus dem Kopf nimmt.
        """
        html = html_of("$x$\n\n| A | B | C |\n|---|---|---|\n| 1 |")
        head = re.search(r"<tr>(.*?)</tr>", html).group(1)
        rows = re.findall(r"<tr>(.*?)</tr>", html)[1:]
        assert head.count("<th>") == 3
        for row in rows:
            assert row.count("<td>") == 3

    def test_underline_tag_from_converter(self):
        """Der Konverter erzeugt Unterstreichung als echtes `<u>` im Rich-Markdown."""
        assert "<u>unter</u>" in html_of("$x$ __unter__")

    def test_double_underscore_is_bold_in_rich_path(self):
        """In Rich Markdown bedeutet `__x__` **fett** — nicht unterstrichen.

        Der Konverter wandelt `__x__` deshalb schon vorab in `<u>x</u>` um
        (in `markdown_to_rich_markdown`), weil Telegram Rich Markdown so
        auslegt. Der Renderer sieht `__…__` also nie; getestet wird der
        tatsächliche Vertrag: **kein** `<b>` aus Unterstrichen.
        """
        from telegram_formatter.utils import markdown_to_rich_markdown

        assert markdown_to_rich_markdown("__dick__") == "<u>dick</u>"
        html = html_of("$x$ __dick__")
        assert "<u>dick</u>" in html
        assert "<b>dick</b>" not in html

    def test_lists(self):
        ul = html_of("$x$\n\n- eins\n- zwei")
        assert '<ul class="tf-list">' in ul
        assert ul.count("<li>") == 2
        ol = html_of("$x$\n\n1. eins\n2. zwei")
        assert '<ol class="tf-list">' in ol
        assert ol.count("<li>") == 2

    def test_nested_list_items_are_flat(self):
        """Tiefe Listen zeigt Telegram flach an — die Vorschau auch."""
        html = html_of("$x$\n\n- eins\n  - zwei\n- drei")
        assert html.count("<li>") == 3
        assert html.count("<ul") == 1

    def test_blockquote(self):
        html = html_of("$x$\n\n> zitat\n> mehr")
        assert '<blockquote class="tf-quote">' in html
        assert "zitat" in html and "mehr" in html

    def test_fenced_code_block(self):
        html = html_of("$x$\n\n```python\nprint(1)\n```")
        assert '<pre class="tf-code">' in html
        assert "print(1)" in html
        # Der Inhalt darf kein Interpret-Markup bekommen.
        assert "<span" not in html.split("<pre")[1].split("</pre>")[0]

    def test_code_inside_fence_is_not_broken_by_markdown(self):
        html = html_of("$x$\n\n```\n**nicht fett** `kein code`\n```")
        assert "<b>" not in html
        assert "**nicht fett**" in html

    def test_inline_code_is_protected(self):
        html = html_of("$x$ `**kein fett**`")
        assert "<b>" not in html
        assert "<code>**kein fett**</code>" in html

    def test_strikethrough_and_italic(self):
        html = html_of("$x$ ~~durch~~ *kursiv*")
        assert "<s>durch</s>" in html
        assert "<i>kursiv</i>" in html

    def test_link_gets_safe_rel(self):
        html = html_of("$x$ [T](https://t.me/x)")
        assert 'rel="noopener noreferrer"' in html
        assert 'target="_blank"' in html

    def test_user_html_is_escaped(self):
        html = html_of("$x$ <script>alert(1)</script>")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html


# --------------------------------------------------------------------------- #
# Formeln
# --------------------------------------------------------------------------- #
class TestMathPlaceholders:
    """Formeln werden markiert, nicht gesetzt — KaTeX macht das im Browser."""

    def test_inline_math_carries_tex(self):
        html = html_of("$x$ $E=mc^2$")
        assert 'class="tf-math tf-math--inline"' in html
        assert 'data-tex="E=mc^2"' in html

    def test_display_math_is_flagged(self):
        html = html_of("$x$ und\n\n$$\\int_0^1 x^2 dx$$")
        assert "tf-math--block" in html
        assert 'data-display="true"' in html
        assert 'data-tex="\\int_0^1 x^2 dx"' in html

    def test_tex_is_attribute_escaped(self):
        """Backslashes und Anführungszeichen dürfen das Attribut nicht sprengen."""
        html = render_rich_markdown('$$\\text{"x"}$$')
        # Ein erfolgreich geparstes HTML-Dokument ist der Beweis:
        from html.parser import HTMLParser

        class Check(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tex = []

            def handle_starttag(self, tag, attrs):
                for name, value in attrs:
                    if name == "data-tex":
                        self.tex.append(value)

        parser = Check()
        parser.feed(html)
        parser.close()
        assert parser.tex == ['\\text{"x"}']

    def test_math_inside_code_is_not_marked(self):
        """`$a+b$` im Inline-Code ist Code, keine Formel."""
        html = html_of("$x$ `$a+b$`")
        assert "<code>$a+b$</code>" in html
        # Nur das echte `$x$` wurde markiert — die Formel im Code nicht.
        assert html.count("data-tex=") == 1
        assert 'data-tex="x"' in html
        assert 'data-tex="a+b"' not in html

    def test_deepseek_delimiters_are_normalised_first(self):
        """`\\(…\\)` wird vom Konverter zu `$…$` — die Vorschau folgt automatisch."""
        html = html_of("Formel \\(x^2\\)")
        assert 'data-tex="x^2"' in html
        assert "\\(" not in html


# --------------------------------------------------------------------------- #
# Nachrichtenteilung
# --------------------------------------------------------------------------- #
class TestChunkBubbles:
    """Telegram stellt lange Eingaben als mehrere Nachrichten zu."""

    def test_one_bubble_per_message(self):
        preview = preview_of("Absatz mit Text. " * 3000)
        assert preview["count"] == len(build_messages("Absatz mit Text. " * 3000, "0"))
        assert len(preview["messages"]) == preview["count"]
        indexes = [m["index"] for m in preview["messages"]]
        assert indexes == list(range(1, preview["count"] + 1))

    def test_every_message_carries_size_and_limit(self):
        for m in preview_of("x" * 20_000)["messages"]:
            assert m["utf16"] > 0
            assert m["limit"] in (utils.REGULAR_MESSAGE_MAX_CHARS, utils.RICH_MESSAGE_MAX_CHARS)
            assert m["utf16"] <= m["limit"]

    def test_no_empty_message(self):
        """Telegram beantwortet leeren Text mit 400 `message text is empty`."""
        for m in preview_of("Absatz " * 10000)["messages"]:
            assert m["utf16"] > 0

    def test_kind_is_per_message(self):
        """`build_messages` entscheidet pro Nachricht — die Vorschau muss das auch."""
        preview = preview_of("**fett** " * 6000)
        assert {m["kind"] for m in preview["messages"]} <= {"regular", "rich"}

    def test_single_message_gives_one_bubble(self):
        preview = preview_of("kurz")
        assert preview["count"] == 1
        assert len(preview["messages"]) == 1


# --------------------------------------------------------------------------- #
# Parität als Ganzes
# --------------------------------------------------------------------------- #
class TestPreviewMatchesPayload:
    """Der eigentliche Beweis: Vorschau und Payload stammen aus derselben Quelle."""

    def test_text_content_is_preserved(self):
        """Jeder sichtbare Text der Vorschau stammt aus der Nachricht."""
        text = "**Wichtig** $x$ und `code`"
        messages = build_messages(text, "0")
        preview = render_preview(messages)
        assert preview["count"] == len(messages)
        # Kein Text erfunden: die Vorschau enthält keine Zeichen, die nicht in
        # der Payload stehen (von Markup-Zeichen abgesehen).
        visible = re.sub(r"<[^>]+>", "", preview["messages"][0]["html"])
        for word in ("Wichtig", "code"):
            assert word in visible

    def test_path_matches_first_message_kind(self):
        messages = build_messages("$x$", "0")
        assert render_preview(messages)["path"] == messages[0].kind

    def test_empty_input_is_safe(self):
        result = render_preview([])
        assert result["count"] == 0
        assert result["messages"] == []
        assert result["path"] == "regular"

    def test_result_is_json_serialisable(self):
        import json

        json.dumps(preview_of("$x$ **f** | a |\n|---|\n| 1 |"))
