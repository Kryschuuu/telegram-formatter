r"""
telegram_formatter/utils.py
===========================
Reine, I/O-freie Konvertierungs- und Aufteilungslogik für
"Markdown + LaTeX -> Telegram".

Dieses Modul enthält KEINE Netzwerk- oder Flask-Abhängigkeiten und ist
dadurch isoliert testbar (siehe ``tests/test_utils.py``). Die eigentlichen
Versandaufrufe liegen in :mod:`telegram_formatter.sender`, die Web-Oberfläche in :mod:`telegram_formatter.app`.

Übersicht der Verarbeitungskette
--------------------------------
Eingabe (Markdown mit LaTeX ``$...$``/``$$...$$`` und Pipe-Tabellen)
    -> normalize_text()                       Unicode/Zeilenumbrüche
    -> build_messages()                       entscheidet Rich vs. Regular
       |-- Rich-Pfad  (LaTeX/Tabellen vorhanden)
       |     -> markdown_to_rich_markdown()   GFM + nativem LaTeX
       |        `-> convert_deepseek_latex_syntax()  \(..\)->$..$, \[..\]->$$..$$
       |            (Inhalt normalisiert: $ x $ -> $x$, Leerzeilen im Block raus)
       |     -> _safe_chunk(..., 32768)       Aufteilung an Formel-/Blockgrenzen
       |        -> _rebalance_markdown_chunks (seit v2.11.0: **, ~~, <u> bleiben über Grenzen erhalten)
       |     -> payload "sendRichMessage"
       `-- Regular-Pfad (reiner Text mit Formatierung)
             -> markdown_to_html()            Telegram-HTML (fett/kursiv/...)
             -> chunk_text(..., 4096)         Aufteilung am 4096-Limit
             -> _rebalance_html_chunks        Tags über Grenzen nachtragen
             -> payload "sendMessage"

Eingaben bis **64000** Zeichen werden komplett angenommen und sinnvoll
aufgeteilt — Codeblöcke (```...```) und Formatierungen (**fett**, *kursiv*,
~~durchgestrichen~~, <u>unterstrichen</u>) bleiben dabei in *jedem* Chunk
wohlgeformt: Ein zu langer Codeblock wird in mehrere eigenständige
```-Blöcke mit erhaltener Sprache zerlegt, offene Formatierungen werden am
Chunk-Ende geschlossen und im nächsten Chunk wieder geöffnet (seit v2.11.0).

`iter_math_spans()` ist der gemeinsame Formel-Scanner für alle vier Pfade
(Erkennung/Routing, Konvertierung, Schutz im HTML-Pfad, Chunk-Sicherheit) —
Audit O-3: vorher liefen drei leicht divergierende Zeichen-Schleifen parallel.

Unterstützte LaTeX-Delimiter in der Eingabe (Details: :func:`iter_math_spans`):
- ``$...$`` / ``$$...$$``  (klassisch, von Telegram nativ gerendert)
- ``\(...\)`` / ``\[...\]`` (DeepSeek/Gemini) -- werden vor dem Versand in die
  Dollar-Syntax übersetzt, weil Telegram sie sonst als Text ausgeben würde.
  Rand-Whitespace und Zeilenumbrüche werden dabei entfernt bzw. zusammengezogen:
  ``\( x \)`` -> ``$x$``. Ohne diese Normalisierung entsteht ``$ x $``, das
  Telegram/GFM nicht als Formel wertet -- die Formel steht dann wörtlich
  (sichtbares ``\cdot`` samt ``$``) in der Nachricht.

Wichtige Telegram-Fakten (Bot API 10.1+, Stand 2026):
- ``sendMessage`` limitiert den Text auf **4096** Zeichen und unterstützt
  **kein** LaTeX und **keine** Tabellen (nur MarkdownV2/HTML).
- ``sendRichMessage`` akzeptiert bis zu **32768** Zeichen und unterstützt
  nativ LaTeX ($...$ / $$...$$) sowie GFM-Tabellen. Das Feld im
  ``rich_message``-Objekt heißt **``markdown``** (alternativ ``html`` oder
  ``blocks``) -- es gibt KEIN Feld ``format``/``text``.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit

# ---------------------------------------------------------------------------
# Konstanten (Telegram-Limits)
# ---------------------------------------------------------------------------
#: Maximale Zeichenlänge einer klassischen Nachricht (sendMessage).
REGULAR_MESSAGE_MAX_CHARS = 4_096
#: Maximale Zeichenlänge einer Rich Message (sendRichMessage, UTF-8).
RICH_MESSAGE_MAX_CHARS = 32_768


@dataclass
class Segment:
    """Ein zusammenhängender Text- oder Formel-Abschnitt."""

    kind: str  # "text" | "inline_math" | "display_math"
    content: str


@dataclass
class TelegramMessage:
    """Eine sendefertige Nachricht mit Methoden-Wahl und API-Payload."""

    kind: str  # "rich" (sendRichMessage) | "regular" (sendMessage)
    payload: dict


# ---------------------------------------------------------------------------
# Unicode-Normalisierung
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """
    Normalisiert Eingabetext nach NFC (vorkomponierte Form) und
    vereinheitlicht Zeilenumbrüche auf ``\\n``.

    Hintergrund: Wird eine Datei ohne explizites ``encoding="utf-8"``
    gelesen oder kommt Text NFD-normalisiert an, zerfallen Zeichen wie
    ``ì`` in mehrere Codepoints (``i`` + COMBINING GRAVE ACCENT). Das
    verschiebt nachgelagerte Offsets und Längenberechnungen. NFC stellt
    sicher, dass ein Zeichen immer EIN Codepoint ist.

    NUL-Zeichen werden entfernt: ``_PlaceholderStore`` markiert geschützte
    Bereiche mit ``\\x00…\\x00``; Nutzer-NULs (via JSON ``\\u0000`` möglich)
    würden sonst Platzhalter kollidieren lassen und von Telegram ohnehin
    abgelehnt (Audit N-1).
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x00", "")
    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------
# LaTeX: klammern-balanciertes Erkennen statt Regex
# ---------------------------------------------------------------------------
def validate_latex_braces(formula: str) -> bool:
    """
    Prüft, ob alle geschweiften Klammern in ``formula`` balanciert sind.

    Reguläre Ausdrücke können beliebig tief verschachtelte Klammerstrukturen
    nicht erkennen ("balanced matching" ist mit regulären Sprachen nicht
    lösbar). Deshalb wird hier iterativ gezählt.
    """
    depth = 0
    for ch in formula:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:  # schließende Klammer ohne öffnende
                return False
    return depth == 0


# ---------------------------------------------------------------------------
# Formel-Scanner: EIN Durchlauf für Erkennung, Konvertierung, Schutz, Chunking
# ---------------------------------------------------------------------------
# Früher liefen vier handgeschriebene Zeichen-Schleifen nebeneinander
# (`split_formulas`, `convert_deepseek_latex_syntax`, `_protect_math` und
# `_atomic_ranges`) und waren leicht divergent — genau daraus entstand der Bug
# „`\( x \)`“: Die Konvertierung schrieb `$ x $` (Leerzeichen am Rand), was
# Telegram/GFM nicht als Formel rendert, während `\(x\)` funktionierte. Seit
# v2.10.0 gibt es nur noch `iter_math_spans()`; alle Pfade teilen dieselben
# Regeln (Audit O-3: ein Scanner statt vier).
#
# Regeln (Pandoc/GFM — Telegram übernimmt sie für Rich Markdown):
# * ``$…$``   — kein Whitespace direkt hinter dem öffnenden bzw. vor dem
#   schließenden ``$``, keine Ziffer direkt danach (sonst gälten
#   ``$20,000 und $30,000`` als Formel), keine Leerzeile im Inhalt.
# * ``$$…$$`` — die Delimiter dürfen durch Whitespace vom Inhalt getrennt sein,
#   eine Leerzeile beendet den Block (der Inhalt darf also keine enthalten).
# * ``\(…\)`` / ``\[…\]`` — Backslash-Delimiter von DeepSeek/Gemini. Sie sind
#   eindeutig (mit Preisen nicht verwechselbar) und werden deshalb großzügig
#   erkannt: Rand-Whitespace und Zeilenumbrüche sind erlaubt und werden bei der
#   Übersetzung in die Dollar-Syntax normalisiert, damit Telegram rendert.
# * Ein ``$ … $`` mit Rand-Whitespace, dessen Inhalt **mit** einem
#   Backslash-Kommando beginnt (``$ \frac{a}{b} $``), ist ebenfalls eine
#   Formel — zwischen zwei Preisen steht dort nie ein Kommando.

#: Backslash-Kommando (``\cdot``, ``\frac``, …) — eindeutiges LaTeX-Signal.
_LATEX_COMMAND_RE = re.compile(r"\\[A-Za-z]{2,}")

#: Obergrenze für die Erkennung rand-behafteter ``$ … $``-Formeln. Verhindert,
#: dass zwei weit auseinanderliegende ``$`` (z. B. zwei Preise über mehrere
#: Absätze) wegen eines zufälligen Backslashs im Text zu einer „Formel“
#: verschmelzen.
_PADDED_MATH_MAX_CHARS = 400


@dataclass(frozen=True)
class MathSpan:
    """Ein gefundener Formelbereich — Delimiter inklusive (``text[start:end]``).

    ``delimiter`` ist einer der vier erkannten Delimiter (``"$$"``, ``r"\\["``,
    ``r"\\("``, ``"$"``), ``kind`` die daraus folgende Formelart
    (``"display_math"``/``"inline_math"``). ``content`` ist der Rohtext
    zwischen den Delimitern — 1:1, ohne Trim und ohne Normalisierung.
    """

    start: int
    end: int
    kind: str  # "inline_math" | "display_math"
    delimiter: str
    content: str


def _math_bounds_ok(text: str, start: int, end: int) -> bool:
    """Telegram/GFM-Regeln für Inline-Math: nicht leer, keine Leerzeichen an
    den Rändern, kein Leerabsatz innen, keine Ziffer direkt nach dem ``$``."""
    if end <= start:
        return False
    inner = text[start:end]
    if inner[:1].isspace() or inner[-1:].isspace() or _has_blank_line(inner):
        return False
    following = text[end + 1 : end + 2]
    return not following.isdigit()


def _has_blank_line(inner: str) -> bool:
    """True, wenn ``inner`` eine Leerzeile enthält (beendet GFM-Block-Math)."""
    return re.search(r"\n[ \t]*\n", inner) is not None


def _dollar_inline_ok(text: str, start: int, end: int) -> bool:
    """True, wenn ``text[start:end]`` zwischen zwei ``$`` Inline-Math ist.

    Maßstab sind die GFM-/Pandoc-Randregeln (:func:`_math_bounds_ok`, Audit
    B-4). Zusätzlich gilt ein rand-behafteter Bereich als Formel, wenn er
    **mit** einem Backslash-Kommando beginnt (``$ \\frac{a}{b} $``,
    ``$ \\sum_{i=1}^{n} i $``) — eine so geschriebene Formel stammt aus einem
    Formelkontext, während zwischen zwei Preisen (``$ 5 und $ 10``) nie ein
    Kommando am Anfang steht. Bewusst eng gefasst: ``$ 5 (\\circa) und $ 10``
    und ``$ x \\cdot y $`` bleiben Text, weil beide nicht von Prosa zu
    unterscheiden sind (dokumentiert in ``docs/FORMATTING.md`` §5).
    """
    if _math_bounds_ok(text, start, end):
        return True
    inner = text[start:end]
    if not (inner[:1].isspace() or inner[-1:].isspace()):
        return False
    stripped = inner.strip()
    if not stripped or len(stripped) > _PADDED_MATH_MAX_CHARS or _has_blank_line(stripped):
        return False
    return _LATEX_COMMAND_RE.match(stripped) is not None


def _match_math_span(text: str, i: int) -> MathSpan | None:
    """Erkennt an Position ``i`` einen Formelbeginn; sonst ``None``."""
    # 1. $$…$$ (Display) — Inhalt nicht leer, keine Leerzeile.
    if text.startswith("$$", i):
        close = text.find("$$", i + 2)
        if close == -1:
            return None
        inner = text[i + 2 : close]
        if inner.strip() and not _has_blank_line(inner):
            return MathSpan(i, close + 2, "display_math", "$$", inner)
        return None

    # 2. \[…\] (Display, DeepSeek/Gemini) — großzügig, wird normalisiert.
    if text.startswith(r"\[", i):
        close = text.find(r"\]", i + 2)
        if close == -1:
            return None
        inner = text[i + 2 : close]
        if inner.strip():
            return MathSpan(i, close + 2, "display_math", r"\[", inner)
        return None

    # 3. \(…\) (Inline, DeepSeek/Gemini) — großzügig, wird normalisiert.
    if text.startswith(r"\(", i):
        close = text.find(r"\)", i + 2)
        if close == -1:
            return None
        inner = text[i + 2 : close]
        if inner.strip():
            return MathSpan(i, close + 2, "inline_math", r"\(", inner)
        return None

    # 4. $…$ (Inline) — GFM-Randregeln plus Backslash-Ausnahme.
    if text[i] == "$":
        close = text.find("$", i + 1)
        if (
            close != -1
            and (close == i + 1 or text[close - 1] != "$")
            and _dollar_inline_ok(text, i + 1, close)
        ):
            return MathSpan(i, close + 1, "inline_math", "$", text[i + 1 : close])
    return None


def iter_math_spans(text: str, *, skip_code_fences: bool = False) -> Iterator[MathSpan]:
    """Liefert alle Formelbereiche in ``text`` in Reihenfolge (Delimiter inkl.).

    Der **einzige** Formel-Scanner des Moduls — Erkennung/Routing
    (:func:`split_formulas`), Konvertierung
    (:func:`convert_deepseek_latex_syntax`), Schutz im HTML-Pfad
    (:func:`_protect_math`) und Chunk-Sicherheit (:func:`_atomic_ranges`)
    nutzen ihn, damit die Pfade nicht auseinanderdriften (Audit O-3).

    :param skip_code_fences: Überspringt ```` ``` ````-Codeblöcke. Nötig beim
        Aufteilen von Rich-Markdown (dort stehen echte Fences); die übrigen
        Aufrufer haben Code vorher durch Platzhalter ersetzt.
    """
    i, n = 0, len(text)
    while i < n:
        if skip_code_fences and text.startswith("```", i):
            close = text.find("```", i + 3)
            if close != -1:
                i = close + 3
                continue
        if text.startswith("\\\\", i):
            i += 2  # Doppelter Backslash (Zeilenumbruch/Escape), kein Delimiter
            continue
        span = _match_math_span(text, i)
        if span is not None:
            yield span
            i = span.end
            continue
        i += 1


def split_formulas(text: str) -> list[Segment]:
    """
    Zerlegt ``text`` in Text- und Formel-Segmente.

    Unterstützte Formel-Delimiter (Details: :func:`iter_math_spans`):

    - ``$$...$$`` markiert eine Display-Formel (Block, zentriert).
    - ``$...$`` markiert eine Inline-Formel.
    - ``\\\\[...\\\\]`` markiert eine Display-Formel (DeepSeek/Gemini-Syntax).
    - ``\\\\(...\\\\)`` markiert eine Inline-Formel (DeepSeek/Gemini-Syntax).

    Ein ``$`` gefolgt von Leerzeichen (z. B. Preisangabe ``$ 20``) wird
    NICHT als Formelbeginn gewertet.
    Unvollständige/unbalancierte Formeln bleiben als normaler Text stehen,
    statt den Rest des Dokuments zu zerstören.

    Entscheidend ist, dass der Formelinhalt 1:1 (unverändert) übernommen
    wird -- inklusive verschachtelter Strukturen wie
    ``\\\\binom{\\\\binom{70}{6}}{33}`` und Spezialsymbole wie ``\\\\alpha``,
    ``\\\\sum``, ``\\\\int``. Es wird nur die Fundstelle der schließenden
    Marke gesucht, nie der Klammerinhalt per Regex gruppiert.
    """
    segments: list[Segment] = []
    last = 0
    for span in iter_math_spans(text):
        if span.start > last:
            segments.append(Segment("text", text[last : span.start]))
        segments.append(Segment(span.kind, span.content))
        last = span.end
    if last < len(text):
        segments.append(Segment("text", text[last:]))
    return segments


def has_latex(text: str) -> bool:
    """True, wenn ``text`` mindestens eine gültige ``$...$``/``$$...$$``-Formel enthält."""
    return any(seg.kind != "text" for seg in split_formulas(text))


def _normalize_inline_math(content: str) -> str:
    r"""
    Normalisiert den Inhalt einer Inline-Formel für Telegram.

    GFM verlangt ein Whitespace-freies Zeichen direkt hinter dem öffnenden und
    direkt vor dem schließenden ``$``; außerdem darf eine Inline-Formel keine
    Zeilenumbrüche enthalten. Beides erzeugen LLMs aber regelmäßig
    (``\( x \)``, ``\(\n x \)\n``). Zeilenumbrüche innerhalb der Formel werden
    zu Leerzeichen (LaTeX wertet sie im Mathe-Modus ohnehin so), der Rest wird
    an den Rändern getrimmt.
    """
    return re.sub(r"\s*\n\s*", " ", content).strip()


def _normalize_display_math(content: str) -> str:
    r"""
    Normalisiert den Inhalt einer Block-Formel für Telegram.

    Whitespace um die Formel ist bei ``$$…$$`` erlaubt, eine Leerzeile beendet
    den Block jedoch. LLM-Antworten trennen mehrere Zeilen einer Blockformel
    gern mit Leerzeilen (``\[\n a\n\n b\n\]``); diese werden zu einem
    Zeilenumbruch zusammengezogen, damit Telegram die Formel rendert.
    """
    return re.sub(r"[ \t]*\n[ \t]*\n[ \t\n]*", "\n", content)


def _render_math_span(span: MathSpan) -> str:
    """Gibt einen Formelbereich in Telegram-Syntax aus (``$…$`` / ``$$…$$``).

    Bereits vorhandene Dollar-Formeln bleiben unangetastet, sofern sie gültig
    sind. Backslash-Delimiter (DeepSeek/Gemini) werden übersetzt und ihr Inhalt
    dabei normalisiert — sonst würde Telegram die Formel als Text anzeigen
    (genau der Bug aus dem Issue: sichtbares ``\\cdot`` samt ``$``).
    """
    if span.delimiter == "$$":
        return "$$" + span.content + "$$"
    if span.delimiter == "$":
        if not span.content[:1].isspace() and not span.content[-1:].isspace() and "\n" not in span.content:
            return "$" + span.content + "$"  # gültige Formel: 1:1 übernehmen
        return "$" + _normalize_inline_math(span.content) + "$"
    if span.delimiter == r"\(":
        return "$" + _normalize_inline_math(span.content) + "$"
    return "$$" + _normalize_display_math(span.content) + "$$"


def convert_deepseek_latex_syntax(text: str) -> str:
    r"""
    Normalisiert DeepSeek/Gemini-LaTeX-Delimiter auf Telegram-Syntax.

    - ``\(...\)``  ->  ``$...$``    (Inline-Math)
    - ``\[...\]``  ->  ``$$...$$``  (Display-Math)

    Telegram Rich Messages rendern ausschließlich ``$...$``/``$$...$$``.
    KI-Tools wie DeepSeek Chat und Gemini liefern jedoch die
    Backslash-Delimiter, die Telegram unverändert als Text ausgeben würde.

    Der Formelinhalt wird inhaltlich **1:1** übernommen (inklusive
    verschachtelter Strukturen wie ``\binom{\binom{70}{6}}{33}``), aber für
    Telegram normalisiert:

    - Inline: Rand-Whitespace und Zeilenumbrüche werden entfernt
      (``\( x \)`` -> ``$x$``). Ohne diesen Schritt entsteht ``$ x $``, das
      Telegram weder als Formel noch als Code rendert — die Formel steht dann
      wörtlich in der Nachricht (sichtbares ``\cdot``, sichtbares ``$``).
    - Display: Leerzeilen im Block werden zu einem Zeilenumbruch
      (``\[\n a\n\n b\n\]`` -> ``$$\na\n b\n$$``), weil eine Leerzeile den
      GFM-Block beendet.

    Robustheit:

    - Bereits vorhandene gültige ``$...$``/``$$...$$``-Formeln bleiben
      unangetastet -- gemischte Dokumente funktionieren dadurch.
    - Ein doppelter Backslash (``\\``, LaTeX-Zeilenumbruch bzw. escapter
      Backslash) wird nicht als Delimiter-Beginn fehlinterpretiert.
    - Unvollständige Delimiter ohne Gegenstück bleiben unverändert stehen,
      statt den restlichen Text zu zerstören.

    Hinweis: Code muss vor dem Aufruf geschützt sein (Platzhalter), damit
    Backslash-Klammern in Codeblöcken nicht umgeschrieben werden.
    """
    out: list[str] = []
    last = 0
    for span in iter_math_spans(text):
        out.append(text[last : span.start])
        out.append(_render_math_span(span))
        last = span.end
    out.append(text[last:])
    return "".join(out)


# ---------------------------------------------------------------------------
# Tabellen: Pipe-Format -> GFM
# ---------------------------------------------------------------------------
def parse_pipe_table(block: str) -> list[list[str]] | None:
    """
    Parst einen Pipe-getrennten Tabellenblock in eine Liste von Zeilen
    (jede Zeile ist eine Liste von Zellen). Gibt ``None`` zurück, wenn der
    Block keine erkennbare Tabelle ist.

    Akzeptiert sowohl das klassische Format::

        n    | L(n,6,6,2) | Quelle
        -----|------------|--------
        20   | 10         | [Thm 3.1]

    als auch GFM mit Markdown-Trennzeile (``---|---``), die beim Parsen
    verworfen wird.
    """
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2 or "|" not in lines[0]:
        return None

    rows: list[list[str]] = []
    for idx, line in enumerate(lines):
        body = line.strip()
        if body.startswith("|"):
            body = body[1:]
        if body.endswith("|"):
            body = body[:-1]
        cells = [c.strip() for c in body.split("|")]

        # Markdown-Trennzeile (z. B. "---|---") -> keine Nutzdaten.
        if idx == 1 and all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue

        rows.append(cells)

    if len(rows) < 2:  # Header allein reicht nicht für eine Tabelle
        return None

    # Spaltenanzahl an die breiteste Zeile angleichen (defensive Pufferung).
    width = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (width - len(r)))
    return rows


def rows_to_gfm_table(rows: list[list[str]]) -> str:
    """
    Baut aus geparsten Zeilen eine GitHub-Flavored-Markdown-Tabelle, wie sie
    Telegrams "Rich Markdown" (sendRichMessage) nativ rendert.
    """
    if not rows:
        return ""
    header, *data = rows
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in data)
    return "\n".join(lines)


def has_table(text: str) -> bool:
    """True, wenn ``text`` mindestens einen Pipe-Tabellenblock enthält."""
    return any(parse_pipe_table(b) is not None for b in re.split(r"\n\s*\n", text))


# ---------------------------------------------------------------------------
# Hilfsklasse: Platzhalter-Schutz für Code/Formeln
# ---------------------------------------------------------------------------
class _PlaceholderStore:
    """
    Ersetzt sensible Textstücke (Codeblöcke, Formeln) durch eindeutige
    Platzhalter, damit nachgelagerte Regex-Ersetzungen sie nicht anfassen.
    Verwendet das NUL-Zeichen als Marker, das in normalem Text praktisch nie
    vorkommt und nach dem Restore vollständig verschwindet.
    """

    def __init__(self) -> None:
        self._items: list[str] = []

    def __call__(self, value: str) -> str:
        """Ersetzt ``value`` durch einen eindeutigen Platzhalter."""
        token = f"\x00{len(self._items)}\x00"
        self._items.append(value)
        return token

    def restore(self, text: str) -> str:
        # In reverse order to correctly handle nested placeholders:
        # e.g. code placeholder \x000\x00 inside a link placeholder
        # \x001\x00. Forward replacement would leave inner token
        # unreplaced after outer has been expanded.
        for i in range(len(self._items) - 1, -1, -1):
            text = text.replace(f"\x00{i}\x00", self._items[i])
        return text


def _escape_html(text: str) -> str:
    """
    Escaped die in Telegram-HTML bedeutungstragenden Zeichen.

    Das Anführungszeichen wird zusätzlich escaped (Audit M-1): unsere
    Erzeuger schreiben Textanteile in Attributkontexte (``<a href="…">``,
    ``<pre language="…">``), wo ein rohes ``"`` das Attribut aufbrechen und
    Payload-Injection ermöglichen würde.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


#: Zulässiges Alphabet für Info-Strings von Codefences (``language="…"``).
#: Unbekannte Sprachen führen zu leerem Attribut statt Attribut-Injection.
_FENCE_LANG_RE = re.compile(r"^[A-Za-z0-9_+#.-]{1,40}$")


def _safe_fence_lang(raw: str) -> str:
    """Normalisiert die Fence-Sprache auf ein Attribut-sicheres Token."""
    lang = raw.strip().split()[0] if raw.strip() else ""
    return lang if _FENCE_LANG_RE.match(lang) else ""


# ---------------------------------------------------------------------------
# Redirect-URLs: Entpacken auf die eigentliche Ziel-URL
# ---------------------------------------------------------------------------
# Suchmaschinen, Videoplattformen und soziale Netzwerke leiten externe Links
# über eigene Redirect-Adressen (google.com/url?q=…, youtube.com/redirect?q=…,
# l.facebook.com/l.php?u=…, …). Such-KIs zitieren solche Wrapper-Adressen,
# wodurch die eigentliche Ziel-URL nur percent-kodiert in einem
# Query-Parameter sichtbar ist. unwrap_redirect_url() holt das Ziel heraus,
# damit in Telegram-Nachrichten lesbare Links statt Tracking-Adressen stehen.


def _looks_like_http_url(value: str) -> bool:
    """True, wenn ``value`` eine absolute http(s)-URL mit Host ist.

    Einzige akzeptierte Zielschemata: ``http``/``https`` — dadurch können
    über Redirect-Parameter niemals ``javascript:``, ``data:`` o. Ä.
    in Link-Ziele eingeschleust werden.
    """
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    try:
        return bool(parts.hostname)
    except ValueError:  # z. B. ungültige IPv6-Klammern
        return False


def _decode_param_value(raw: str) -> str | None:
    """Dekodiert einen (ggf. mehrfach percent-kodierten) Parameter-Wert in
    eine http(s)-URL; sonst ``None``."""
    value = raw
    for _ in range(3):
        if _looks_like_http_url(value):
            return value
        decoded = unquote(value)
        if decoded == value:
            return None
        value = decoded
    return None


def _decode_bing_click(raw: str) -> str | None:
    """Bing-Klicktracking trägt das Ziel als Base64URL nach dem Präfix ``a1``."""
    token = raw[2:] if raw.startswith("a1") else raw
    token += "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(token).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if _looks_like_http_url(decoded) else None


@dataclass(frozen=True)
class _RedirectRule:
    """Ein bekannter Redirect-Dienst: Host-Muster, Pfadpräfix, Parameternamen."""

    host: re.Pattern
    path: str
    params: tuple[str, ...]
    decode: Callable[[str], str | None] | None = None


_GOOGLE_HOST = re.compile(r"(?:[a-z0-9-]+\.)*google\.[a-z]{2,}(?:\.[a-z]{2,})?")

#: Regelwerk bekannter Redirect-Dienste. Eine URL wird nur dann ersetzt,
#: wenn der jeweilige Parameter eine gültige absolute http(s)-URL ergibt —
#: normale Suchanfragen (``google.com/search?q=hello``) bleiben stehen.
_REDIRECT_RULES: tuple[_RedirectRule, ...] = (
    # Google: klassische Weiterleitung /url?q=… sowie die /search?q=<URL>-
    # Wrapper-Links, die Such-KIs (z. B. Perplexity) als Zitate ausgeben.
    _RedirectRule(_GOOGLE_HOST, "/url", ("q", "url")),
    _RedirectRule(_GOOGLE_HOST, "/search", ("q",)),
    # YouTube-Weiterleitung aus Videobeschreibungen
    _RedirectRule(re.compile(r"(?:[a-z0-9-]+\.)*youtube\.com"), "/redirect", ("q", "redirect_url")),
    # Ausstiegsseiten von Facebook (l.facebook.com, lm.facebook.com)
    _RedirectRule(re.compile(r"(?:l|lm)\.facebook\.com"), "/l.php", ("u",)),
    # DuckDuckGo-Exit-Link
    _RedirectRule(re.compile(r"(?:[a-z0-9-]+\.)*duckduckgo\.com"), "/l/", ("uddg",)),
    # Reddit-Exit-Link
    _RedirectRule(re.compile(r"(?:out|click)\.reddit\.com"), "/", ("url",)),
    # Steam-Linkfilter
    _RedirectRule(re.compile(r"(?:[a-z0-9-]+\.)*steamcommunity\.com"), "/linkfilter/", ("url", "u")),
    # LinkedIn-Weiterleitung
    _RedirectRule(re.compile(r"(?:[a-z0-9-]+\.)*linkedin\.com"), "/redir/redirect", ("url",)),
    # Bing-Klicktracking (Ziel als Base64URL im u-Parameter)
    _RedirectRule(re.compile(r"(?:www\.)?bing\.com"), "/ck/a", ("u",), _decode_bing_click),
)

#: Maximale Entpackungstiefe — Redirects können ineinander verschachtelt
#: sein (z. B. Facebook-Seite, die einen Google-Redirect linkt).
_MAX_REDIRECT_DEPTH = 5


def _match_redirect_rule(url: str) -> str | None:
    """Liefert die Ziel-URL, falls ``url`` auf eine Redirect-Regel passt."""
    if not url.lower().startswith(("http://", "https://")):
        return None
    try:
        parts = urlsplit(url)
        host = parts.hostname
    except ValueError:
        return None
    if not host:
        return None
    query_params: dict[str, list[str]] = {}
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query_params.setdefault(key, []).append(value)
    path = parts.path or "/"
    for rule in _REDIRECT_RULES:
        if rule.host.fullmatch(host) is None or not path.startswith(rule.path):
            continue
        for name in rule.params:
            for raw in query_params.get(name, ()):
                target = (rule.decode or _decode_param_value)(raw)
                if target:
                    return target
    return None


def unwrap_redirect_url(url: str) -> str:
    """
    Entpackt bekannte Redirect-/Tracking-URLs auf die eigentliche Ziel-URL.

    Unterstützte Dienste (Ziel steht als absolute http(s)-URL im genannten
    Query-Parameter)::

        google.*/url?q=…                     google.*/search?q=<URL>
        youtube.com/redirect?q=…             l.facebook.com/l.php?u=…
        duckduckgo.com/l/?uddg=…             out.reddit.com/…?url=…
        steamcommunity.com/linkfilter/?url=… linkedin.com/redir/redirect?url=…
        bing.com/ck/a?u=a1<Base64URL>

    Regeln:

    - Das extrahierte Ziel wird nur akzeptiert, wenn es selbst eine
      absolute ``http(s)``-URL ist. Andere Schemata werden nie ausgegeben,
      und normale Suchanfragen (``google.com/search?q=katze``) bleiben
      unverändert.
    - Es wird rekursiv entpackt (bis :data:`_MAX_REDIRECT_DEPTH` Ebenen),
      damit auch ineinander verschachtelte Redirects beim Ziel ankommen.
    - Unbekannte Adressen werden unverändert zurückgegeben — Offline-Parser
      können Kurz-URLs (t.co, bit.ly, goo.gl, …) nicht auflösen.
    """
    current = url.strip()
    seen: set[str] = set()
    for _ in range(_MAX_REDIRECT_DEPTH):
        if current in seen:
            break
        seen.add(current)
        target = _match_redirect_rule(current)
        if target is None or target == current:
            break
        current = target
    return current


def _clean_redirect_label(label: str, url: str, target: str) -> str:
    """Linktext, der nur die (Redirect-)URL wiederholt, wird durch die
    Ziel-URL ersetzt — die Nachricht zeigt dann das echte Ziel."""
    stripped = label.strip()
    if stripped == url:
        return target
    if stripped.lower().startswith(("http://", "https://")):
        unwrapped = unwrap_redirect_url(stripped)
        if unwrapped != stripped:
            return unwrapped
    return label


#: Verschachtelter Link ``[text]([label](url))`` — ungültiges Markdown,
#: typisches Artefakt von Such-KI-Zitaten; wird zu ``[text](url)``.
_NESTED_LINK_RE = re.compile(r"\[([^\[\]]+)\]\(\[[^\[\]]*\]\((https?://[^)\s]+)\)\)")
#: Einzelner, in eckige Klammern gewickelter Link ``[[text](url)]``.
_WRAPPED_LINK_RE = re.compile(r"\[(\[[^\[\]]+\]\(https?://[^)\s]+\))\]")
#: Gewöhnlicher Markdown-Link.
_MARKDOWN_LINK_RE = re.compile(r"\[([^\[\]]+)\]\((https?://[^)\s]+)\)")
#: Nackte URL im Fließtext (Telegram verlinkt sie automatisch).
_BARE_URL_RE = re.compile(r"https?://[^\s\[\]()<>\"'|`]+")
#: Satzzeichen, die im Fließtext direkt an einer URL kleben können.
_TRAILING_PUNCT = ".,;:!?…"


def _unwrap_bare_url(m: re.Match) -> str:
    """Ersetzt nackte Redirect-URLs im Fließtext durch ihre Ziel-URL."""
    url = m.group(0)
    trail = ""
    while url and url[-1] in _TRAILING_PUNCT:
        trail = url[-1] + trail
        url = url[:-1]
    target = unwrap_redirect_url(url)
    return target + trail if target != url else m.group(0)


def _normalize_links(text: str) -> str:
    """
    Bereinigt Links vor der eigentlichen Konvertierung.

    1. Link-Artefakte von Such-KIs werden geglättet: verschachtelte Links
       ``[text]([label](url))`` und Klammer-Wicklungen ``[[text](url)]``
       werden zu ``[text](url)``.
    2. Redirect-URLs (:func:`unwrap_redirect_url`) werden in Link-Zielen,
       Link-Texten und nackten URLs auf die Ziel-URL entpackt.

    Muss **vor** dem HTML-Escaping laufen (damit ``&`` in Query-Strings
    noch als Trenner lesbar ist) und **nach** dem Schutz von Code/Formeln
    (URLs in Codeblöcken bleiben 1:1 stehen).
    """
    text = _NESTED_LINK_RE.sub(lambda m: f"[{m.group(1)}]({m.group(2)})", text)
    text = _WRAPPED_LINK_RE.sub(lambda m: m.group(1), text)

    def _link_repl(m: re.Match) -> str:
        label, url = m.group(1), m.group(2)
        target = unwrap_redirect_url(url)
        return f"[{_clean_redirect_label(label, url, target)}]({target})"

    text = _MARKDOWN_LINK_RE.sub(_link_repl, text)
    return _BARE_URL_RE.sub(_unwrap_bare_url, text)


# ---------------------------------------------------------------------------
# Markdown -> Telegram-HTML (Regular-Pfad, sendMessage)
# ---------------------------------------------------------------------------
def markdown_to_html(text: str) -> str:
    """
    Wandelt Markdown in Telegram-HTML (``parse_mode="HTML"``) um.

    Unterstützt: Fett ``**x**``, Kursiv ``*x*``/``_x_``, Unterstreichen
    ``__x__``, Durchgestrichen ``~~x~~``, Inline-Code `` `x` ``, Codeblöcke,
    Links, Überschriften, Blockquotes und Listen. Code und (defensiv) LaTeX
    werden vor den Ersetzungen geschützt und am Ende unverändert
    wiederhergestellt.

    Bekannte Redirect-/Tracking-URLs (z. B. ``google.com/url?q=…``) werden
    über :func:`_normalize_links` auf ihre Ziel-URL entpackt.
    """
    text = normalize_text(text)
    store = _PlaceholderStore()

    # 1. Fenced-Code-Blöcke (```...```) und Inline-Code schützen.
    #    Die Sprache läuft durch eine Allowlist — sie landet sonst roh im
    #    Attribut language="…" (Audit M-1: Attribut-Injection verhindern).
    def fenced(m: re.Match) -> str:
        lang = _safe_fence_lang(m.group(1))
        content = m.group(2).rstrip("\n")
        if lang:
            return store(f'<pre language="{lang}">{_escape_html(content)}</pre>')
        return store(f"<pre>{_escape_html(content)}</pre>")

    text = re.sub(r"```([^\n]*)\n(.*?)```", fenced, text, flags=re.DOTALL)
    text = re.sub(
        r"`([^`\n]+)`",
        lambda m: store(f"<code>{_escape_html(m.group(1))}</code>"),
        text,
    )

    # 2. DeepSeek/Gemini-Delimiter auf Telegram-Syntax normalisieren, damit
    #    der Formelschutz unten nur noch eine Syntax kennen muss.
    text = convert_deepseek_latex_syntax(text)

    # 3. Formeln defensiv schützen (HTML kann sie nicht rendern, aber der
    #    Inhalt darf durch die folgenden Regexes nicht zerstört werden).
    text = _protect_math(text, store)

    # 3b. Links normalisieren: Redirect-URLs auf die Ziel-URL entpacken und
    #     Such-KI-Link-Artefakte glätten. Läuft vor dem Escapen, damit "&"
    #     in Query-Strings noch als Trenner lesbar ist.
    text = _normalize_links(text)

    # 4. Verbleibenden Text escapen.
    text = _escape_html(text)

    # 5. Tabellen als lesbare Zeilen (Fallback; wird normalerweise nicht
    #    erreicht, weil Tabellen über den Rich-Pfad laufen).
    text = _tables_to_lines(text)

    # 6. Überschriften in Fettschrift mit Emoji-Marker.
    text = re.sub(r"^####+\s+(.*)$", r"<b>🔸 \1</b>", text, flags=re.M)
    text = re.sub(r"^###\s+(.*)$", r"<b>🔹 \1</b>", text, flags=re.M)
    text = re.sub(r"^##\s+(.*)$", r"<b>📍 \1</b>", text, flags=re.M)
    text = re.sub(r"^#\s+(.*)$", r"<b>🚀 \1</b>", text, flags=re.M)

    # 7. Blockquotes ("> " am Zeilenanfang).
    text = _wrap_blockquotes(text)

    # 8. Listenpunkte — Einrückung bleibt erhalten (Audit B-12), sonst
    #    kollabieren verschachtelte Listen auf eine Ebene.
    text = re.sub(r"^(\s*)[-*+]\s+", r"\1• ", text, flags=re.M)
    text = re.sub(r"^(\s*)\d+[.)]\s+", r"\1• ", text, flags=re.M)

    # 9. Inline-Formatierung & Links — Links müssen VOR der Formatierung
    #    geschützt werden, sonst interpretiert die Kursiv-Regex
    #    Unterstriche in URLs als Formatierung (z. B.
    #    https://.../watch?v=730MtctOC_w) und erzeugt überlappendes HTML
    #    wie <a href="...OC<i>w">...OC</i>w</a>, das Telegram mit
    #    "Unmatched end tag ... expected </a> found </i>" ablehnt.
    #    Daher: Link-Text separat formatieren, URL nie anfassen, Link als
    #    Platzhalter schützen, danach restlichen Text formatieren.
    def _apply_inline_html(s: str) -> str:
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"__(.+?)__", r"<u>\1</u>", s)
        s = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", s)
        s = re.sub(r"~~(.+?)~~", r"<s>\1</s>", s)
        return s

    def _link_repl(m: re.Match) -> str:
        raw_text = m.group(1)
        url = m.group(2)
        formatted = _apply_inline_html(raw_text)
        return store(f'<a href="{url}">{formatted}</a>')

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _link_repl, text)
    text = _apply_inline_html(text)

    return store.restore(text)


def _protect_math(text: str, store: _PlaceholderStore) -> str:
    r"""
    Ersetzt gültige LaTeX-Formeln durch Platzhalter.

    HTML (``parse_mode="HTML"``) kann keine Formeln rendern: Der Inhalt bleibt
    als Text erhalten, nur die Delimiter fallen weg. Erkannt werden dieselben
    Bereiche wie überall sonst (:func:`iter_math_spans`) — ``$...$``,
    ``$$...$$``, ``\\(...\\)`` und ``\\[...\\]``. Formeln mit
    unbalancierten geschweiften Klammern bleiben unangetastet (sie sind
    vermutlich Fließtext und keine Formel).
    """
    out: list[str] = []
    last = 0
    for span in iter_math_spans(text):
        out.append(text[last : span.start])
        if validate_latex_braces(span.content):
            out.append(store(_escape_html(span.content)))
        else:
            out.append(text[span.start : span.end])
        last = span.end
    out.append(text[last:])
    return "".join(out)


def _wrap_blockquotes(text: str) -> str:
    """Gruppiert aufeinanderfolgende ``> ``-Zeilen in ein ``<blockquote>``."""
    lines = text.split("\n")
    out: list[str] = []
    in_quote = False
    for line in lines:
        m = re.match(r"^\s*&gt;\s?(.*)", line)  # ">" wurde bereits zu "&gt;"
        if m:
            if not in_quote:
                out.append("<blockquote>" + m.group(1))
                in_quote = True
            else:
                out.append(m.group(1))
        else:
            if in_quote:
                out[-1] += "</blockquote>"
                in_quote = False
            out.append(line)
    if in_quote:
        out[-1] += "</blockquote>"
    return "\n".join(out)


def _tables_to_lines(text: str) -> str:
    """Wandelt Pipe-Tabellen in lesbare ``Header: Wert``-Zeilen um (Fallback)."""

    def repl(m: re.Match) -> str:
        rows = parse_pipe_table(m.group(0))
        if not rows:
            return m.group(0)
        header, *data = rows
        lines = []
        for row in data:
            parts = [f"<b>{header[i]}:</b> {row[i]}" for i in range(len(header))]
            lines.append("🔸 " + " • ".join(parts))
        return "\n".join(lines)

    return re.sub(r"(?m)(^.*\|.*$\n?){2,}", repl, text)


# ---------------------------------------------------------------------------
# Markdown -> Rich Markdown (Rich-Pfad, sendRichMessage)
# ---------------------------------------------------------------------------
def markdown_to_rich_markdown(text: str) -> str:
    r"""
    Wandelt Markdown in Telegrams "Rich Markdown" um (Feld ``markdown`` im
    ``InputRichMessage``-Objekt).

    Rich Markdown ist GFM-kompatibel und unterstützt daher ``**fett**``,
    ``*kursiv*``, ``~~durchgestrichen~~``, Code, Listen, Überschriften,
    Blockquotes, LaTeX (``$...$``/``$$...$$``) und Pipe-Tabellen nativ.

    DeepSeek/Gemini-Delimiter (``\(...\)``/``\[...\]``) werden dabei über
    :func:`convert_deepseek_latex_syntax` in die Dollar-Syntax übersetzt.

    Der einzige Unterschied zur Eingabe: Unterstreichen wird hier über
    ``<u>...</u>`` ausgedrückt, weil ``__...__`` in Rich Markdown Fett
    bedeutet. Tabellen werden zusätzlich in gültige GFM-Form normalisiert.

    Bekannte Redirect-/Tracking-URLs (z. B. ``youtube.com/redirect?q=…``)
    werden über :func:`_normalize_links` auf ihre Ziel-URL entpackt.
    """
    text = normalize_text(text)
    store = _PlaceholderStore()

    # 1. Code schützen, damit "$" und "__" darin unangetastet bleiben.
    #    Sprache nur, wenn sie das erlaubte Alphabet trifft (Audit M-1);
    #    unbekannte Info-Strings werden verworfen, der Block bleibt ``` ohne
    #    Sprachangabe.
    def fenced(m: re.Match) -> str:
        lang = _safe_fence_lang(m.group(1))
        content = m.group(2).rstrip("\n")
        return store(f"```{lang}\n{content}\n```")

    text = re.sub(r"```([^\n]*)\n(.*?)```", fenced, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", lambda m: store(f"`{m.group(1)}`"), text)

    # 2. DeepSeek/Gemini-Delimiter auf Telegram-Syntax normalisieren
    #    (\(...\) -> $...$, \[...\] -> $$...$$). Code ist bereits geschützt.
    text = convert_deepseek_latex_syntax(text)

    # 2b. Links normalisieren: Redirect-URLs auf die Ziel-URL entpacken und
    #     Such-KI-Link-Artefakte glätten (vor Unterstreichungs-/Link-Schutz).
    text = _normalize_links(text)

    # 3. Unterstreichen __x__ -> <u>x</u> — URLs dürfen dabei nicht
    #    angefasst werden (z. B. https://.../__foo__ würde sonst zu
    #    https://.../<u>foo</u>). Links werden daher über Platzhalter geschützt;
    #    der Link-Text selbst darf __ enthalten und wird separat formatiert.
    def _apply_underline_rich(s: str) -> str:
        return re.sub(r"__([^_\n]+)__", r"<u>\1</u>", s)

    def _rich_link_repl(m: re.Match) -> str:
        raw_text = m.group(1)
        url = m.group(2)
        formatted = _apply_underline_rich(raw_text)
        return store(f"[{formatted}]({url})")

    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", _rich_link_repl, text)
    text = _apply_underline_rich(text)

    # 4. Tabellen blockweise normalisieren; alles andere bleibt GFM.
    blocks = re.split(r"\n\s*\n", text)
    rendered = []
    for block in blocks:
        rows = parse_pipe_table(block)
        rendered.append(rows_to_gfm_table(rows) if rows else block)

    return store.restore("\n\n".join(rendered))


# ---------------------------------------------------------------------------
# Nachrichtenbau und Aufteilung
# ---------------------------------------------------------------------------
def needs_rich_message(text: str) -> bool:
    """True, wenn der Text LaTeX oder eine Tabelle enthält (-> Rich-Pfad)."""
    return has_latex(text) or has_table(text)


# ---------------------------------------------------------------------------
# Syntaxbewusste Aufteilung (Audit B-1): Formatierungen, Formeln und Code-
# fences dürfen an Chunk-Grenzen nicht zerrissen werden — Telegram lehnt
# unbalanciertes HTML mit 400 ab und Render-Markdown-Blöcke sonst falsch.
# ---------------------------------------------------------------------------
#: Reserve pro Chunk für nachgetragene Öffner + angehängte Schließer, damit
#: die harte 4096-Grenze nie gerissen wird (4 Tags × ~28 Zeichen, gerundet).
_HTML_BALANCE_RESERVE = 224
#: Reserve für den Rich-Pfad: Markdown-Marker (** / ~~ / <u>) werden nach
#: dem Chunking balanciert; 64 Zeichen decken 4 offene Marker locker ab.
_RICH_BALANCE_RESERVE = 64
#: Max. Tiefe, die über Chunk-Grenzen hinweg nachgetragen (reopened) wird.
_HTML_MAX_CARRY = 4
_TAG_SCAN_RE = re.compile(r"<(/?)(b|i|u|s|code|pre|blockquote|a)(\s[^<>]*)?>", re.I)
#: Tags, die beim nächsten Chunk wieder geöffnet werden (Links ausgenommen:
#: deren href-Länge wäre nicht kalkulierbar — sie werden nur sauber geschlossen).
_CARRYABLE_TAGS = frozenset({"b", "i", "u", "s", "code", "pre", "blockquote"})
def _dangling_tail(chunk: str) -> str:
    """Fragment am Chunk-Ende, das einen unvollständigen Tag/Entity anzeigt.

    Der Chunker kann (bei harten Schnitten) genau vor dem ``>`` eines Tags oder
    mitten in einer Entity ``&amp;`` enden. Solche Fragmente wandern komplett
    in den nächsten Chunk — dort sind sie wieder wohlgeformt.
    """
    tail_from = -1
    lt = chunk.rfind("<")
    if lt != -1 and ">" not in chunk[lt:]:
        tail_from = lt
    ent = re.search(r"&[#A-Za-z0-9]{1,7}$", chunk)
    if ent is not None and (tail_from == -1 or ent.start() < tail_from):
        tail_from = ent.start()
    return chunk[tail_from:] if tail_from != -1 else ""


def _rebalance_html_chunks(chunks: list[str]) -> list[str]:
    """Schließt über Chunk-Grenzen offene Formatierungs-Tags sauber und trägt
    sie im nächsten Chunk nach (Telegram-HTML-Pfad).

    Regeln:
    * Überzählige schließende Tags am Chunk-Anfang (deren Öffner im Vorgänger
      schon balanciert wurde) werden entfernt — ihr Inhalt bleibt stehen.
    * Am Chunk-Ende offene Tags werden geschlossen; ihre öffnende Variante
      (bis auf ``<a>``, s. o.) wird dem nächsten Chunk vorangestellt, sodass
      die Formatierung über Chunk-Grenzen hinweg erhalten bleibt.
    * Dangling-Tag/Entity-Fragmente wandern an den Anfang des nächsten Chunks.
    """
    result: list[str] = []
    prepend = ""
    last_index = len(chunks) - 1
    for index, raw in enumerate(chunks):
        chunk = prepend + raw
        prepend = ""
        # 1) Fragmente, die den nächsten Tag/Entity anfangen, rüberschieben.
        tail = _dangling_tail(chunk) if index < last_index else ""
        if tail:
            chunk = chunk[: len(chunk) - len(tail)]
        # 2) Tags scannen: Stack führen, verwaiste Schließungen entfernen.
        parts: list[str] = []
        stack: list[tuple[str, str]] = []
        pos = 0
        for match in _TAG_SCAN_RE.finditer(chunk):
            parts.append(chunk[pos:match.start()])
            pos = match.end()
            tag = match.group(2).lower()
            if match.group(1) == "/":
                for k in range(len(stack) - 1, -1, -1):
                    if stack[k][0] == tag:
                        del stack[k]
                        parts.append(match.group(0))
                        break
                # else: verwaistes </tag> -> Tag verwerfen, Inhalt behalten
            else:
                stack.append((tag, match.group(0)))
                parts.append(match.group(0))
        parts.append(chunk[pos:])
        chunk = "".join(parts)
        # 3) Offene Tags schließen und (falls sinnvoll) nachtragen.
        for tag, _opener in reversed(stack):
            chunk += f"</{tag}>"
        if index < last_index:
            carry = "".join(
                opener
                for tag, opener in stack[-_HTML_MAX_CARRY:]
                if tag in _CARRYABLE_TAGS
            )
            prepend = carry + tail  # Öffner zuerst, dann das Tail-Fragment
        else:
            chunk += tail  # letzter Chunk: Fragment verbleibt (defekter Eingangstext)
        result.append(chunk)
    return result


def _rebalance_markdown_chunks(chunks: list[str]) -> list[str]:
    """Balanciert Markdown-Formatierungen über Chunk-Grenzen (Rich-Pfad, v2.11.0).

    Offene Marker (**fett**, ~~durchgestrichen~~, <u>unterstrichen</u>) werden
    am Chunk-Ende geschlossen und im nächsten Chunk wieder geöffnet, damit
    die Formatierung in *jedem* Chunk wohlgeformt bleibt und nicht verloren
    geht. Codeblöcke (```), Inline-Code (`...`) und Formeln sind atomar und
    werden hier nicht als Formatierung behandelt — sie sind über
    :func:`_atomic_ranges` geschützt.
    """
    _CLOSING = {"**": "**", "~~": "~~", "<u>": "</u>"}
    result: list[str] = []
    stack: list[tuple[str, str]] = []
    for idx, raw in enumerate(chunks):
        carry_open = "".join(op for _, op in stack)
        protected = _atomic_ranges(raw)

        def _is_protected(pos: int) -> tuple[bool, int]:
            for a, b in protected:
                if a <= pos < b:
                    return True, b
                if pos < a:
                    break
            return False, pos

        cur: list[tuple[str, str]] = list(stack)
        i = 0
        n = len(raw)
        while i < n:
            is_prot, nxt = _is_protected(i)
            if is_prot:
                i = nxt
                continue
            low3 = raw[i : i + 3].lower()
            low4 = raw[i : i + 4].lower()
            if low3 == "<u>":
                cur.append(("<u>", "<u>"))
                i += 3
                continue
            if low4 == "</u>":
                for k in range(len(cur) - 1, -1, -1):
                    if cur[k][0] == "<u>":
                        del cur[k]
                        break
                i += 4
                continue
            if raw.startswith("**", i):
                if cur and cur[-1][0] == "**":
                    cur.pop()
                else:
                    cur.append(("**", "**"))
                i += 2
                continue
            if raw.startswith("~~", i):
                if cur and cur[-1][0] == "~~":
                    cur.pop()
                else:
                    cur.append(("~~", "~~"))
                i += 2
                continue
            i += 1

        closing = "".join(_CLOSING[typ] for typ, _ in reversed(cur))
        balanced = carry_open + raw + closing
        result.append(balanced)
        stack = cur
    return result



def _atomic_ranges(text: str) -> list[tuple[int, int]]:
    """Indivisible Bereiche des Rich-Textes: Fenced Code, Inline-Code und Formeln
    (``$$\u2026$$``, ``$\u2026$`` — und defensiv die Backslash-Delimiter).

    Liefert sortierte, nicht überlappende ``(start, end)``-Paare. Ein Chunk-
    schnitt darf nie *in* einem dieser Bereiche landen (B-1) — sonst zerreißt
    die Teilung Code oder Formeln. Der Formel-Teil kommt aus dem gemeinsamen
    Scanner (:func:`iter_math_spans`, Audit O-3), Inline-Code (`` `...` ``) ist
    seit v2.11.0 ebenfalls atomar, damit ``*``/``**`` darin nicht als
    Formatierung fehlinterpretiert wird.
    """
    ranges: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("```", i):
            close = text.find("```", i + 3)
            if close != -1:
                ranges.append((i, close + 3))
                i = close + 3
                continue
        if text.startswith("\\\\", i):
            i += 2  # Doppelter Backslash: kein Delimiter-Beginn
            continue
        # Inline-Code `...` (einzeilig, nicht leer) — atomar, schützt vor
        # Fehlinterpretation von **/~~/etc innerhalb von Code.
        if text[i] == "`":
            close = text.find("`", i + 1)
            if close != -1 and "\n" not in text[i + 1 : close] and close > i + 1:
                ranges.append((i, close + 1))
                i = close + 1
                continue
        span = _match_math_span(text, i)
        if span is not None:
            ranges.append((span.start, span.end))
            i = span.end
            continue
        i += 1
    return ranges


def _split_guarded_unit(unit: str, max_chars: int) -> list[str]:
    """Zerlegt eine das Limit überschreitende Atom-Einheit in wohlgeformte Teile.

    Die ``$$…$$``/``` ```…``` ``/``$…$``-Delimiters werden je Fragment neu
    gesetzt, damit jedes Stück für Telegram eine vollständige Formel bzw. ein
    vollständiger Code-Block bleibt (nur die *Inhalte* teilen sich auf).

    Codeblöcke: Seit v2.11.0 bleibt die Sprachangabe (z. B. ``python``) in
    **jedem** Fragment erhalten und Leerzeilen im Code gehen nicht verloren —
    ein 64000-Zeichen-Block wird so in mehrere eigenständige, korrekt
    gefence'te Blöcke zerlegt, jeweils mit gleicher Sprache und korrekter
    Einrückung.
    """
    for delim in ("$$", "```", "$"):
        if len(unit) > 2 * len(delim) and unit.startswith(delim) and unit.endswith(delim):
            inner = unit[len(delim) : -len(delim)]
            head = ""
            if delim == "```":
                nl = inner.find("\n")
                if 0 < nl <= 64:
                    head, inner = inner[: nl + 1], inner[nl + 1 :]
                # Sprache für alle Fragmente bewahren; leere head bedeutet generisches ```
                budget = max(max_chars - 2 * len(delim) - len(head) - 2, 16)
                # Leerzeilen bewahren: splitlines(keepends=True) behält \n,
                # leere Zeilen werden als "\n" repräsentiert.
                raw_lines = inner.splitlines(keepends=True)
                # Falls inner mit \n endet, ist das letzte Element bereits mit \n;
                # falls nicht, bleibt letztes ohne \n — _group kommt damit zurecht.
                # Leere Eingabe (nur head) ergibt eine Zeile.
                if not raw_lines:
                    raw_lines = [""]
                parts = _group(raw_lines, budget, "") or [""]
                fragments: list[str] = []
                for part in parts:
                    body = part if part.endswith("\n") else part + "\n"
                    fragments.append(f"```{head}{body}```")
                return [f for f in fragments if f]
            budget = max(max_chars - 2 * len(delim) - len(head) - 2, 16)
            lines = inner.split("\n")
            parts = _group([ln + "\n" for ln in lines if ln != ""], budget, "") or [""]
            fragments: list[str] = []
            for part in parts:
                if delim == "$$":
                    fragments.append(f"$${part.strip()}$$")
                else:
                    fragments.append(f"${part.strip()}$")
            return [f for f in fragments if f]
    return [unit]


def _safe_chunk(text: str, max_chars: int) -> list[str]:
    """Teilt Rich-Markdown in Chunks, ohne geschützte Bereiche (Formeln,
    Code-Fences) zu durchschneiden; nutzt exakte Original-Slices.

    - Atomare Einheiten bleiben unantastbar, solange sie ins Limit passen.
    - Über große Einheiten werden per :func:`_split_guarded_unit` in
      selbst-ständige Blöcke zerlegt (jedes Chunk bleibt syntaktisch gültig).
    - Lückentext wird an Zeilengrenzen gruppiert; ein harter Schnitt ist nur
      bei pathologisch langen Einzelzeilen möglich.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buf:
            joined = "".join(buf).strip()
            if joined:
                chunks.append(joined)
            buf.clear()
            size = 0

    def emit_unit(unit: str) -> None:
        nonlocal size
        if not unit:
            return
        if len(unit) > max_chars:
            flush()
            for frag in _split_guarded_unit(unit, max_chars):
                frag = frag.strip()
                if frag:
                    chunks.append(frag)
            return
        if size + len(unit) > max_chars and buf:
            flush()
        buf.append(unit)
        size += len(unit)

    def emit_gap(seg: str) -> None:
        if not seg:
            return
        if len(seg) <= max_chars:
            emit_unit(seg)
        else:
            for piece in _split_oversized_paragraph(seg, max_chars):
                emit_unit(piece)

    last = 0
    for start, end in _atomic_ranges(text):
        if start > last:
            emit_gap(text[last:start])
        emit_unit(text[start:end])
        last = end
    if last < len(text):
        emit_gap(text[last:])
    flush()
    return chunks


def build_messages(raw_text: str, chat_id: int | str) -> list[TelegramMessage]:
    """
    Baut aus Rohtext sendefertige Telegram-Nachrichten.

    - Enthält der Text LaTeX oder Tabellen, wird er in Rich Markdown
      übersetzt und über ``sendRichMessage`` (Feld ``markdown``) verschickt.
    - Reiner Formatierungstext läuft über ``sendMessage`` mit
      ``parse_mode="HTML"``.

    In beiden Fällen wird an den jeweiligen Zeichenlimits aufgeteilt
    (4096 bzw. 32768), wobei die Aufteilung an Absatz-/Zeilen-/Wortgrenzen
    erfolgt. Seit dem Security-Audit bleiben dabei Formatierungs-Tags
    (Regular-Pfad, ``</x>``-Balance pro Chunk) sowie Formeln und Code-Blöcke
    (Rich-Pfad, atomare Bereiche) intakt — Telegram verwirft sonst die ganze
    Nachricht mit ``400 Can't parse entities``.
    """
    text = normalize_text(raw_text).strip()
    if not text:
        return []

    if needs_rich_message(text):
        rich_text = markdown_to_rich_markdown(text)
        chunks = _safe_chunk(rich_text, RICH_MESSAGE_MAX_CHARS - _RICH_BALANCE_RESERVE)
        chunks = _rebalance_markdown_chunks(chunks)
        return [
            TelegramMessage(
                kind="rich",
                payload={"chat_id": chat_id, "rich_message": {"markdown": chunk}},
            )
            for chunk in chunks
        ]

    html = markdown_to_html(text)
    chunks = chunk_text(html, REGULAR_MESSAGE_MAX_CHARS - _HTML_BALANCE_RESERVE)
    chunks = _rebalance_html_chunks(chunks)
    return [
        TelegramMessage(
            kind="regular",
            payload={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"},
        )
        for chunk in chunks
    ]


def _group(items: list[str], max_chars: int, joiner: str) -> list[str]:
    """
    Gruppiert ``items`` zu Strings (verbunden mit ``joiner``), die jeweils
    ``max_chars`` nicht überschreiten. Überlange Einzel-Items werden hart
    geteilt.
    """
    # Längensaldo wird inkrementell geführt: join().len() je Item wäre
    # O(n^2) (Audit O-2); die Grenze bleibt exakt dieselbe.
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    sep = len(joiner)
    for item in items:
        if len(item) > max_chars:
            if buf:
                chunks.append(joiner.join(buf))
                buf, size = [], 0
            chunks.extend(item[i : i + max_chars] for i in range(0, len(item), max_chars))
            continue
        add = len(item) + (sep if buf else 0)
        if buf and size + add > max_chars:
            chunks.append(joiner.join(buf))
            buf, size = [], 0
            add = len(item)
        buf.append(item)
        size += add
    if buf:
        chunks.append(joiner.join(buf))
    return chunks


def _split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Teilt einen einzelnen zu langen Absatz: erst an Zeilen-, dann an Wortgrenzen."""
    lines = paragraph.split("\n")
    if len(lines) > 1:
        return _group(lines, max_chars, "\n")
    words = paragraph.split(" ")
    if len(words) > 1:
        return _group(words, max_chars, " ")
    return [paragraph[i : i + max_chars] for i in range(0, len(paragraph), max_chars)]


def chunk_text(text: str, max_chars: int) -> list[str]:
    """
    Teilt ``text`` in Stücke von höchstens ``max_chars`` Zeichen auf.

    Reihenfolge der bevorzugten Trennstellen:
    1. Absatzgrenzen (Leerzeilen) -- Tabellen/Formeln liegen innerhalb eines
       Absatzes und bleiben dadurch erhalten.
    2. Zeilengrenzen.
    3. Wortgrenzen.
    4. Harter Schnitt (nur bei pathologisch langen Einzelwörtern).
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []

    for p in paragraphs:
        if len(p) <= max_chars:
            if buf and len("\n\n".join(buf)) + 2 + len(p) > max_chars:
                chunks.append("\n\n".join(buf))
                buf = []
            buf.append(p)
        else:
            # Absatz allein zu lang -> vorherigen Puffer abschließen.
            if buf:
                chunks.append("\n\n".join(buf))
                buf = []
            chunks.extend(_split_oversized_paragraph(p, max_chars))

    if buf:
        chunks.append("\n\n".join(buf))
    return chunks
