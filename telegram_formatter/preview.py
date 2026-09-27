"""
telegram_formatter/preview.py
=============================
Rendert die von :func:`telegram_formatter.utils.build_messages` erzeugten
Telegram-Payloads **so, wie Telegram sie anzeigt** — für die Live-Vorschau
der Weboberfläche.

Warum ein eigenes Modul
-----------------------
Bis v2.13.0 hat ``static/js/app.js`` die Vorschau mit einer **zweiten,
unabhängigen** JavaScript-Implementierung des Konverters gebildet
(``renderPreview``). Diese Parallelfassung driftete sofort: Überschriften
bekamen keine Emoji-Präfixe, Pipe-Tabellen wurden gar nicht behandelt, LaTeX
blieb ungesetzt, und die Nachrichtenteilung war unsichtbar. Die Vorschau
zeigte damit etwas anderes als das, was Telegram bekommen hätte — genau die
Fehlerklasse, die das Code-Review in utils.py gerade behoben hat.

Grundsatz seit v2.14.0: **Die Vorschau ist die Payload, nicht eine zweite
Rechnung.** Sie bekommt dieselben Nachrichten, die Telegram bekommt, und
rendert sie. Es gibt nur eine Wahrheit.

Schichtregel
------------
Wie ``utils.py`` bleibt dieses Modul rein: kein Flask, kein I/O, keine
Netzwerkzugriffe. Es nimmt fertige :class:`~telegram_formatter.utils.
TelegramMessage`-Objekte entgegen und liefert ein serialisierbares Dict.
``app.py`` hängt es nur noch an die Antwort an.

Die beiden Anzeigepfade
----------------------
Telegram rendert zwei völlig verschiedene Dialekte, und die Vorschau muss den
jeweils **richtigen** nehmen — nicht einen gemeinsamen:

``regular`` (``sendMessage`` + ``parse_mode="HTML"``)
    ``payload["text"]`` ist bereits Telegram-HTML. Die Vorschau sanitisiert
    ihn gegen eine Allowlist (siehe :func:`sanitize_telegram_html`) und
    lässt den Browser dieselben Tags mit derselben Semantik rendern, die
    Telegram verwendet. Die Emoji-Präfixe der Überschriften
    (``#`` → ``🚀``) stecken bereits **im HTML** — sie kommen aus
    :func:`~telegram_formatter.utils.markdown_to_html`, nicht aus diesem
    Modul.

``rich`` (``sendRichMessage``, Feld ``markdown``)
    ``payload["rich_message"]["markdown"]`` ist GFM-artiges Markdown.
    Telegram übersetzt Überschriften, Tabellen und **LaTeX** selbst; dieses
    Modul bildet genau dieses Anzeige-Subset ab
    (:func:`render_rich_markdown`). Wichtig: der Rich-Pfad kennt **keine**
    Emoji-Präfixe — ``# Titel`` bleibt dort ein schlichtes ``Titel``.

Formeln
-------
Formeln werden **nicht** hier gesetzt, sondern als
``<span class="tf-math" data-tex="…">`` markiert. Die Entscheidung *was* eine
Formel ist, trifft der Server (er kennt :func:`~telegram_formatter.utils.
has_latex` und die Chunk-Grenzen); das **Setzen** übernimmt KaTeX im
Browser. So bleibt die Math-Regel an einer Stelle, und der Browser bekommt
genau die TeX-Zeichenkette, die Telegram gesetzt bekommen hätte.

Sicherheit
----------
Die Ausgabe landet per ``innerHTML`` im Browser. Sie ist zwar per Konstruktion
sicher — :func:`~telegram_formatter.utils.markdown_to_html` escaped jedes
Textfragment über :func:`~telegram_formatter.utils._escape_html`, bevor ein
Tag erzeugt wird —, aber :func:`sanitize_telegram_html` setzt zusätzlich eine
Allowlist. Grund: ein einziger künftiger Konverter-Bug, der ein Attribut
durchreicht, wäre sonst sofort eine XSS-Lücke. Allowlist schlägt Denkvermögen,
und ``tests/test_preview.py`` prüft die Ablehnung explizit.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from typing import Any

from telegram_formatter.utils import (
    REGULAR_MESSAGE_MAX_CHARS,
    RICH_MESSAGE_MAX_CHARS,
    TelegramMessage,
    _telegram_len,
)

__all__ = ["render_preview", "sanitize_telegram_html"]


#: Tags, die ``sendMessage`` mit ``parse_mode="HTML"`` versteht. Quelle:
#: https://core.telegram.org/bots/api#html-style — die Liste ist **absichtlich
#: kurz**, Telegram lehnt alles andere mit 400 ``can't parse entities`` ab.
#: Wir bilden dieselbe Menge im Browser ab, damit Anzeige und Vorschau
#: übereinstimmen.
ALLOWED_HTML_TAGS: frozenset[str] = frozenset(
    {
        "b",  # fett
        "strong",  # fett (Alias, von manchen Markdown-Produzenten erzeugt)
        "i",  # kursiv
        "em",  # kursiv (Alias)
        "u",  # unterstrichen
        "ins",  # unterstrichen (Alias)
        "s",  # durchgestrichen
        "strike",  # durchgestrichen (Alias)
        "del",  # durchgestrichen (Alias)
        "code",  # Inline-Code
        "pre",  # Codeblock (bei `language="…"` mit Syntax-Highlighting)
        "a",  # Link
        "blockquote",  # Zitat
        "tg-spoiler",  # Telegram-Spoiler
        "tg-emoji",  # Custom-Emoji
        "details",  # aufklappbares Zitat
        "br",  # expliziter Umbruch
    }
)

#: Attribute je erlaubtem Tag. Alles andere fliegt raus — insbesondere jedes
#: ``on*``-Attribut, jedes ``style`` und ``class``.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href"}),
    "pre": frozenset({"language"}),
    "tg-emoji": frozenset({"emoji-id"}),
    "details": frozenset({"open"}),
}

#: Void-Elemente (kein schließendes Tag). Alle anderen bekommen ein
#: ``</tag>`` gesetzt, auch unbekannte — damit entsteht kein offenes Markup.
_VOID_TAGS: frozenset[str] = frozenset({"br"})

#: HTML-CSS-Entity, die Telegram als Smiley darstellt. Bewusst *nicht* in der
#: Allowlist: der Konverter erzeugt sie nicht, und eine zukünftige
#: Unterstützung soll eine bewusste Entscheidung sein.
_TELEGRAM_SMILEY = re.compile(r"&#(\d+);")

#: URL-Schemata, die ein ``href`` tragen darf. Alles andere — insbesondere
#: ``javascript:`` und ``data:`` — wird verworfen und der Link als reiner Text
#: ausgegeben.
#:
#: Aktuell **unerreichbar**: der Konverter erkennt Links nur als
#: ``[text](https?://…)``, erzeugt also nie ein ``javascript:``-``href``. Das ist
#: genau der Grund, warum die Prüfung hier steht: diese Funktion ist die zweite
#: Verteidigungslinie, und ein späterer Konverter-Bug darf nicht sofort eine
#: XSS-Lücke daraus machen. Ein per ``innerHTML`` gesetzter
#: ``<a href="javascript:…">`` ist in aktuellen Browsern anklickbar.
_SAFE_URL_SCHEMES: frozenset[str] = frozenset({"http", "https", "tg", "mailto"})


def _safe_href(value: str) -> str | None:
    """Prüft ein ``href``-Ziel; ``None`` heißt „verwenden" (nicht erlaubt).

    Ohne Doppelpunkt ist das Ziel relativ ("/pfad", "pfad", "?q=", "#anker") und
    damit im Browser harmlos — es durchreicht. Mit Doppelpunkt muss das Schema
    auf der Allowlist stehen; alles andere (``javascript:``, ``data:``,
    ``vbscript:`` …) wird verworfen und der Link verfällt zum reinen Text.
    """
    candidate = value.strip()
    if not candidate or ":" not in candidate:
        return candidate
    scheme = candidate.split(":", 1)[0].lower()
    return candidate if scheme in _SAFE_URL_SCHEMES else None


# --------------------------------------------------------------------------- #
# Sanitizer (nur für den Regular-Pfad)
# --------------------------------------------------------------------------- #
class _TelegramHTMLSanitizer(HTMLParser):
    """Allowlist-Filter für Telegram-HTML.

    Arbeitet auf dem Token-Stream statt auf dem Rohtext, damit Textmaskierung
    und Tags sauber getrennt bleiben: jeder ``handle_data``-Knoten wird neu
    escaped, jedes erlaubte Tag mit gefilterten Attributen wieder ausgegeben.
    Alles andere wird verworfen (nicht escaped — es wird gar nicht erst als
    Text ausgegeben).
    """

    def __init__(self) -> None:
        # `convert_charrefs=False`: Entities müssen *nicht* vorab dekodiert
        # werden, sonst würde `&lt;` zu `<` und wieder zu `&lt;` — das wäre
        # korrekt, aber `&amp;lt;` (vom Nutzer getippt) würde zu `&lt;`
        # und damit zu einem echten `<` in der Vorschau. Mit
        # `convert_charrefs=False` bleiben Entities als Entities erhalten.
        super().__init__(convert_charrefs=False)
        self._out: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_HTML_TAGS:
            return  # Tag verwerfen — der Inhalt bleibt (s. handle_data)
        self._emit_start(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in ALLOWED_HTML_TAGS:
            return
        self._emit_start(tag, attrs, self_closing=True)

    def _emit_start(
        self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool
    ) -> None:
        allowed = _ALLOWED_ATTRS.get(tag, frozenset())
        parts: list[str] = []
        for name, value in attrs:
            if name not in allowed or value is None:
                continue
            if name == "href" and _safe_href(value) is None:
                # Nicht erlaubtes Schema: der Link verfällt zum reinen Text.
                continue
            parts.append(f' {name}="{html.escape(value, quote=True)}"')
        rendered = "".join(parts)
        if tag in _VOID_TAGS:
            self._out.append(f"<{tag}{rendered}>")
        elif self_closing:
            self._out.append(f"<{tag}{rendered}></{tag}>")
        else:
            self._out.append(f"<{tag}{rendered}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in ALLOWED_HTML_TAGS and tag not in _VOID_TAGS:
            self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        # Rohtext des Konverters ist bereits escaped (er kommt aus
        # `_escape_html`) — Entities dürfen daher **nicht** dekodiert werden.
        self._out.append(data)

    def handle_entityref(self, name: str) -> None:
        self._out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self._out.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        return  # Kommentare sind Angriffsfläche ohne Nutzen

    def handle_decl(self, decl: str) -> None:
        return  # <!DOCTYPE>, <![CDATA[ …

    def handle_pi(self, data: str) -> None:
        return  # <? …

    def unknown_decl(self, data: str) -> None:
        return

    def result(self) -> str:
        return "".join(self._out)


def sanitize_telegram_html(source: str) -> str:
    """Filtert ``source`` auf die von Telegram erlaubten Tags und Attribute.

    >>> sanitize_telegram_html('<b>fett</b><script>alert(1)</script>')
    '<b>fett</b>'
    >>> sanitize_telegram_html('<a href="x" onclick="boom()">L</a>')
    '<a href="x">L</a>'
    >>> sanitize_telegram_html('<img src=x onerror=alert(1)>')
    ''
    """
    parser = _TelegramHTMLSanitizer()
    parser.feed(source)
    parser.close()
    return parser.result()


# --------------------------------------------------------------------------- #
# Rich-Markdown -> Anzeige-HTML
# --------------------------------------------------------------------------- #
#: Überschriften 1–6. Telegram setzt sie fett und größer; die **Emoji-Präfixe
#: des HTML-Pfads gehören hier ausdrücklich nicht dazu** (siehe Modul-Docstring).
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$", re.M)
#: Codeblock mit optionaler Sprache: ``` oder ~~~ am Zeilenanfang.
_FENCE_RE = re.compile(r"^([ \t]*)(`{3,}|~{3,})[ \t]*([^\n`]*)\n(.*?)\n?\1\2[ \t]*$", re.S | re.M)
#: Inline-Code mit höherer Priorität als jeder andere Marker.
_INLINE_CODE_RE = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
#: GFM-Tabelle: Kopfzeile, Trennzeile mit `:?-+:?`, dann Datenzeilen.
_TABLE_RE = re.compile(
    r"^ {0,3}(?P<head>\|?[^\n|]+\|?[^\n]*)\n"
    r" {0,3}(?P<sep>\|?[ \t]*:?-{1,}:?[ \t]*(\|[ \t]*:?-{1,}:?[ \t]*)+\|?)[ \t]*\n"
    r"(?P<rows>(?:[ \t]*\|[^\n]*\n?)*)",
    re.M,
)
#: Blockquote: eine oder mehrere `>`-Zeilen (optional mit Leerzeien dazwischen).
_QUOTE_RE = re.compile(r"(?:^[ \t]*>[^\n]*(?:\n|$))+", re.M)
#: Ungeordnete und geordnete Listen. Verschachtelung wird bewusst flach
#: gerendert — Telegram zeigt sie ebenfalls flach, wenn auch eingerückt.
_ULI_RE = re.compile(r"^[ \t]*[-*+][ \t]+(.*)$", re.M)
_OLI_RE = re.compile(r"^[ \t]*(\d{1,9})[.)][ \t]+(.*)$", re.M)
_LIST_ITEM_RE = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d{1,9}[.)][ \t]+)(.*)$")
#: Link: `[text](url)` — der Konverter hat Redirect-URLen bereits entpackt.
_LINK_RE = re.compile(r"\[([^\]\n]*)\]\((https?://[^)\s]+)\)")
#: Emoji-Smiley wie `:tada:` → Telegram ersetzt das durch das echte Emoji.
_SMILERY_RE = re.compile(r"(?<![\w:]):([a-z0-9_+-]{2,32}):(?![\w:])")

#: Inline-Marker in **fester Reihenfolge**. Reihenfolge ist nicht beliebig:
#: Code zuerst (sonst zerlegt `*` den Code), dann `$`-Math (enthält
#: Backslashes und Klammern, die spätere Muster stören könnten), dann
#: Unterstreichung/Fett vor den einfachen Italic-Varianten, zuletzt ~~strike~~.
_INLINE_ORDER: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("code", _INLINE_CODE_RE, "code"),
    ("math-display", re.compile(r"\$\$(.+?)\$\$", re.S), "math-block"),
    ("math-inline", re.compile(r"(?<![\$\\])\$([^\$\n]+?)(?<![\$\\])\$(?!\$)"), "math-inline"),
    ("u", re.compile(r"<u>(.*?)</u>", re.S), "u"),
    ("b", re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S), "b"),
    ("i-star", re.compile(r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\*\w])"), "i"),
    ("i-us", re.compile(r"(?<![_\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![_\w])"), "i"),
    ("s", re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S), "s"),
    ("smiley", _SMILERY_RE, "smiley"),
)

#: Platzhalter-Marker. Bewusst ein Steuerzeichen, das im Nutzertext praktisch
#: nie vorkommt und im Gegensatz zu ``\u0000`` (das ``utils.py`` benutzt) nicht
#: aus einem Telegram-Payload stammen kann.
_PH = ""


class _Inline:
    """Ersetzt Inline-Muster durch Platzhalter und setzt sie am Ende ein."""

    def __init__(self) -> None:
        self._slots: list[str] = []

    def hold(self, html_fragment: str) -> str:
        self._slots.append(html_fragment)
        return f"{_PH}{len(self._slots) - 1}{_PH}"

    def restore(self, text: str) -> str:
        # Platzhalter der Länge 1 (Index 0..9) zuerst, sonst würde `1`
        # als Rest von `10` übrig bleiben.
        out = text
        for index in range(len(self._slots) - 1, -1, -1):
            out = out.replace(f"{_PH}{index}{_PH}", self._slots[index])
        return out


def _render_inline(text: str) -> str:
    """Wendet die Inline-Marker in Telegram-Reihenfolge an.

    Nimmt **un-escaped** Text entgegen (der Nutzertext bzw. der
    Rich-Markdown) und escaped erst das Endergebnis. Deshalb werden die
    gefangenen Gruppen nicht vorab escaped, sondern die Slots nach dem
    Escaping eingesetzt.
    """
    inline = _Inline()

    # 1) Code zuerst — schützt den Inhalt vor allen weiteren Ersetzungen.
    def code_sub(match: re.Match[str]) -> str:
        return inline.hold(f"<code>{html.escape(match.group(2), quote=False)}</code>")

    text = _INLINE_CODE_RE.sub(code_sub, text)

    # 2) Math. Der TeX-Code landet als Attribut; `quote=True` maskiert Anführungszeichen.
    def math_block(match: re.Match[str]) -> str:
        return inline.hold(
            f'<span class="tf-math tf-math--block" data-display="true" '
            f'data-tex="{html.escape(match.group(1), quote=True)}"></span>'
        )

    def math_inline(match: re.Match[str]) -> str:
        return inline.hold(
            f'<span class="tf-math tf-math--inline" '
            f'data-tex="{html.escape(match.group(1), quote=True)}"></span>'
        )

    text = re.sub(r"\$\$(.+?)\$\$", math_block, text, flags=re.S)
    text = re.sub(r"(?<![\$\\])\$([^\$\n]+?)(?<![\$\\])\$(?!\$)", math_inline, text)

    # 3) Der Rest des Textes wird jetzt escaped — die folgenden Muster arbeiten
    #    nur noch auf Markup, das garantiert keine Nutzer-Zeichenkette mehr
    #    zerlegen kann.
    text = html.escape(text, quote=False)

    # 4) Unterstreichung zuerst: der Konverter erzeugt sie als echtes `<u>`,
    #    und `__` bedeutet in Rich Markdown *fett*.
    text = re.sub(r"&lt;u&gt;(.*?)&lt;/u&gt;", lambda m: f"<u>{m.group(1)}</u>", text, flags=re.S)

    # 5) Fett / kursiv / durchgestrichen.
    text = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", lambda m: f"<b>{m.group(1)}</b>", text, flags=re.S)
    text = re.sub(r"(?<![\*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\*\w])", lambda m: f"<i>{m.group(1)}</i>", text)
    text = re.sub(r"(?<![_\w])_(?=\S)([^_\n]+?)(?<=\S)_(?![_\w])", lambda m: f"<i>{m.group(1)}</i>", text)
    text = re.sub(r"~~(?=\S)(.+?)(?<=\S)~~", lambda m: f"<s>{m.group(1)}</s>", text, flags=re.S)

    # 6) Links. `html.escape` hat die Anführungszeichen im Text nicht berührt
    #    (`quote=False`), Attribute hier aber schon — deshalb `&amp;` zurück.
    def link_sub(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2).replace("&amp;", "&")
        return (
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{label}</a>'
        )

    text = _LINK_RE.sub(link_sub, text)

    # 7) Telegram-Emoji-Smiley. Der Name wird als Attribut übergeben; die
    #    Auflösung übernimmt der Client.
    text = _SMILERY_RE.sub(
        lambda m: f'<span class="tf-smiley" data-smiley="{m.group(1)}">:{m.group(1)}:</span>',
        text,
    )

    return inline.restore(text)


def _split_table_row(line: str) -> list[str]:
    """Zerlegt eine GFM-Tabellenzeile in Zellen (führendes/abschließendes ``|`` optional)."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped)]


def _render_table(match: re.Match[str]) -> str:
    """Eine GFM-Tabelle als echte ``<table>`` — so zeigt Telegram sie an."""
    head = _split_table_row(match.group("head"))
    rows = [
        _split_table_row(row)
        for row in (match.group("rows") or "").splitlines()
        if row.strip()
    ]
    out = ['<table class="tf-table">', "<thead><tr>"]
    out.extend(f"<th>{_render_inline(cell)}</th>" for cell in head)
    out.append("</tr></thead>")
    if rows:
        out.append("<tbody>")
        for row in rows:
            # Fehlende Zellen auffüllen, zusätzliche abschneiden — sonst
            # entstehen ungleich lange Zeilen, die der Browser streckt.
            cells = (row + [""] * len(head))[: len(head)]
            out.append("<tr>" + "".join(f"<td>{_render_inline(c)}</td>" for c in cells) + "</tr>")
        out.append("</tbody>")
    out.append("</table>")
    return "".join(out)


def _render_list(lines: list[str], ordered: bool) -> str:
    """Listeneinträge als ``<ul>``/``<ol>``.

    Verschachtelung wird bewusst **flach** gerendert: Telegram zeigt tiefe
    Listen flach an, und eine verschachtelte ``<ul>`` in der Vorschau wäre eine
    Abweichung in die andere Richtung.
    """
    tag = "ol" if ordered else "ul"
    items = []
    for line in lines:
        item = _LIST_ITEM_RE.match(line)
        if item:
            items.append(f"<li>{_render_inline(item.group(1))}</li>")
    return f"<{tag} class=\"tf-list\">" + "".join(items) + f"</{tag}>"


def render_rich_markdown(markdown: str) -> str:
    """Rendert Telegram-Rich-Markdown als Anzeige-HTML.

    Bildet genau die Elemente ab, die Telegram in ``InputRichMessage.markdown``
    darstellt: Überschriften, fett/kursiv/unterstrichen/durchgestrichen,
    Links, Inline-Code, Codeblöcke, Zitate, Listen, **GFM-Tabellen** und
    LaTeX (als ``data-tex``-Span für KaTeX).

    Bewusst **keine** Emoji-Präfixe bei Überschriften: die stammen aus dem
    HTML-Pfad (``markdown_to_html``) und wären im Rich-Pfad eine Fälschung.
    """
    text = markdown.replace("\r\n", "\n").replace("\r", "\n")

    # 1) Codeblöcke zuerst herauslösen — sie dürfen von keinem anderen Muster
    #    angefasst werden, auch nicht von Tabellenerkennung.
    inline = _Inline()
    text = _FENCE_RE.sub(
        lambda m: inline.hold(
            f'<pre class="tf-code"><code>{html.escape(m.group(4), quote=False)}</code></pre>'
        ),
        text,
    )

    # 2) Blockweise arbeiten: leere Zeile trennt Absätze.
    blocks = re.split(r"\n[ \t]*\n", text)
    out: list[str] = []

    for block in blocks:
        if not block.strip():
            continue
        out.append(_render_block(block, inline))

    return inline.restore("".join(out))


def _render_block(block: str, inline: _Inline) -> str:
    """Rendert einen Absatz (durch Leerzeile getrennter Block)."""
    stripped = block.strip("\n")

    # Codeblock-Platzhalter: kann der ganze Block einer sein.
    if stripped.startswith(_PH) and stripped.endswith(_PH) and "\n" not in stripped:
        return stripped

    # Überschrift
    heading = _HEADING_RE.match(stripped)
    if heading and "\n" not in stripped:
        level = min(len(heading.group(1)), 6)
        return (
            f'<h{level} class="tf-heading tf-heading--{level}">'
            f"{_render_inline(heading.group(2))}</h{level}>"
        )

    # Blockquote
    quote = _QUOTE_RE.fullmatch(stripped)
    if quote:
        inner = "\n".join(
            re.sub(r"^[ \t]*>[ \t]?", "", line) for line in quote.group(0).splitlines()
        )
        return f'<blockquote class="tf-quote">{_render_inline(inner)}</blockquote>'

    # Tabelle
    table = _TABLE_RE.fullmatch(stripped)
    if table:
        return _render_table(table)

    # Liste (alle Zeilen müssen Listeneinträge sein)
    lines = stripped.splitlines()
    if lines and all(_LIST_ITEM_RE.match(line) for line in lines):
        ordered = bool(_OLI_RE.match(lines[0]))
        return _render_list(lines, ordered)

    # Absatz
    return f'<p class="tf-para">{_render_inline(stripped)}</p>'


# --------------------------------------------------------------------------- #
# Öffentliche API
# --------------------------------------------------------------------------- #
def _message_html(message: TelegramMessage) -> tuple[str, int, int]:
    """Liefert ``(html, utf16_laenge, limit)`` für eine einzelne Nachricht."""
    if message.kind == "rich":
        raw = message.payload.get("rich_message", {})
        markdown = raw.get("markdown", "") if isinstance(raw, dict) else ""
        return render_rich_markdown(markdown), _telegram_len(markdown), RICH_MESSAGE_MAX_CHARS

    text = message.payload.get("text", "")
    if not isinstance(text, str):
        text = ""
    return sanitize_telegram_html(text), _telegram_len(text), REGULAR_MESSAGE_MAX_CHARS


def render_preview(messages: list[TelegramMessage]) -> dict[str, Any]:
    """Rendert **alle** Nachrichten für die Live-Vorschau.

    :return: serialisierbares Dict — vom Client direkt als HTML einsetzbar::

        {
          "path": "rich",          # regular | rich
          "count": 2,
          "messages": [
            {"index": 1, "html": "…", "utf16": 1820, "limit": 32768, "kind": "rich"},
            …
          ],
        }

    ``path`` nennt den gewählten Anzeigedialekt. Er kann je Nachricht
    wechseln (``build_messages`` entscheidet pro Nachricht), die Vorschau
    zeigt aber **eine** Sprechblase pro Nachricht — genau wie Telegram sie
    einzeln zustellt.
    """
    rendered: list[dict[str, Any]] = []
    for index, message in enumerate(messages, start=1):
        markup, size, limit = _message_html(message)
        rendered.append(
            {
                "index": index,
                "kind": message.kind,
                "html": markup,
                "utf16": size,
                "limit": limit,
            }
        )

    return {
        "path": messages[0].kind if messages else "regular",
        "count": len(messages),
        "messages": rendered,
    }
