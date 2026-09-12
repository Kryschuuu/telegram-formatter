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
       |     -> chunk_text(..., 32768)        Aufteilung am Blocklimit
       |     -> payload "sendRichMessage"
       `-- Regular-Pfad (reiner Text mit Formatierung)
             -> markdown_to_html()            Telegram-HTML (fett/kursiv/...)
             -> chunk_text(..., 4096)         Aufteilung am 4096-Limit
             -> payload "sendMessage"

Unterstützte LaTeX-Delimiter in der Eingabe:
- ``$...$`` / ``$$...$$``  (klassisch, von Telegram nativ gerendert)
- ``\(...\)`` / ``\[...\]`` (DeepSeek/Gemini) -- werden vor dem Versand in die
  Dollar-Syntax übersetzt, weil Telegram sie sonst als Text ausgeben würde.

Wichtige Telegram-Fakten (Bot API 10.1+, Stand 2026):
- ``sendMessage`` limitiert den Text auf **4096** Zeichen und unterstützt
  **kein** LaTeX und **keine** Tabellen (nur MarkdownV2/HTML).
- ``sendRichMessage`` akzeptiert bis zu **32768** Zeichen und unterstützt
  nativ LaTeX ($...$ / $$...$$) sowie GFM-Tabellen. Das Feld im
  ``rich_message``-Objekt heißt **``markdown``** (alternativ ``html`` oder
  ``blocks``) -- es gibt KEIN Feld ``format``/``text``.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

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


def split_formulas(text: str) -> list[Segment]:
    """
    Zerlegt ``text`` zeichenweise in Text- und Formel-Segmente.

    Unterstützte Formel-Delimiter:
    - ``$$...$$`` markiert eine Display-Formel (Block, zentriert).
    - ``$...$`` markiert eine Inline-Formel.
    - ``\\[...\\]`` markiert eine Display-Formel (DeepSeek/Gemini-Syntax).
    - ``\\(...\\)`` markiert eine Inline-Formel (DeepSeek/Gemini-Syntax).

    Ein ``$`` gefolgt von Leerzeichen (z. B. Preisangabe ``$ 20``) wird
    NICHT als Formelbeginn gewertet.
    Unvollständige/unbalancierte Formeln bleiben als normaler Text stehen,
    statt den Rest des Dokuments zu zerstören.

    Entscheidend ist, dass der Formelinhalt 1:1 (unverändert) übernommen
    wird -- inklusive verschachtelter Strukturen wie
    ``\\binom{\\binom{70}{6}}{33}`` und Spezialsymbole wie ``\\alpha``,
    ``\\sum``, ``\\int``. Es wird nur die Fundstelle der schließenden
    Marke gesucht, nie der Klammerinhalt per Regex gruppiert.
    """
    segments: list[Segment] = []
    buf: list[str] = []
    i, n = 0, len(text)

    def flush_text() -> None:
        """Sammelt gepufferten Text in ein Text-Segment."""
        if buf:
            segments.append(Segment("text", "".join(buf)))
            buf.clear()

    while i < n:
        ch = text[i]

        # Prüfe auf die verschiedenen Formel-Delimiter (Priorität wichtig!)
        # 1. $$...$$ (Display)
        if text[i:i+2] == "$$":
            start = i + 2
            end = text.find("$$", start)
            if end != -1 and text[start:end].strip() and "\n\n" not in text[start:end]:
                formula = text[start:end]
                flush_text()
                segments.append(Segment("display_math", formula))
                i = end + 2
                continue

        # 2. \[...\] (Display, DeepSeek/Gemini)
        if text[i:i+2] == r"\[":
            start = i + 2
            end = text.find(r"\]", start)
            if end != -1:
                formula = text[start:end]
                flush_text()
                segments.append(Segment("display_math", formula))
                i = end + 2
                continue

        # 3. \(...\) (Inline, DeepSeek/Gemini)
        if text[i:i+2] == r"\(":
            start = i + 2
            end = text.find(r"\)", start)
            if end != -1:
                formula = text[start:end]
                flush_text()
                segments.append(Segment("inline_math", formula))
                i = end + 2
                continue

        # 4. $...$ (Inline)
        if ch == "$":
            # "$ " -> Preisangabe, kein Formelbeginn.
            if i + 1 < n and text[i + 1].isspace():
                buf.append(ch)
                i += 1
                continue

            # Einzelnes $ suchen (nicht $$). GFM-Grenzregeln (Audit B-4):
            # ""$100 und $200"" ist eine Preisangabe, keine Formel —
            # schließendes $ darf nicht auf Leerzeichen treffen und nicht
            # direkt vor einer Ziffer stehen.
            start = i + 1
            end = text.find("$", start)
            if (
                end != -1
                and (end == start or text[end - 1] != "$")
                and _math_bounds_ok(text, start, end)
            ):
                formula = text[start:end]
                flush_text()
                segments.append(Segment("inline_math", formula))
                i = end + 1
                continue

        # Kein Delimiter -> normaler Text
        buf.append(ch)
        i += 1

    flush_text()
    return segments


def has_latex(text: str) -> bool:
    """True, wenn ``text`` mindestens eine gültige ``$...$``/``$$...$$``-Formel enthält."""
    return any(seg.kind != "text" for seg in split_formulas(text))


def convert_deepseek_latex_syntax(text: str) -> str:
    r"""
    Normalisiert DeepSeek/Gemini-LaTeX-Delimiter auf Telegram-Syntax.

    - ``\(...\)``  ->  ``$...$``    (Inline-Math)
    - ``\[...\]``  ->  ``$$...$$``  (Display-Math)

    Telegram Rich Messages rendern ausschließlich ``$...$``/``$$...$$``.
    KI-Tools wie DeepSeek Chat und Gemini liefern jedoch die
    Backslash-Delimiter, die Telegram unverändert als Text ausgeben würde.

    Der Formelinhalt wird **1:1** übernommen (inklusive Zeilenumbrüchen und
    verschachtelter Strukturen wie ``\binom{\binom{70}{6}}{33}``).

    Robustheit:
    - Bereits vorhandene ``$...$``/``$$...$$``-Formeln werden übersprungen und
      bleiben unangetastet -- gemischte Dokumente funktionieren dadurch.
    - Ein doppelter Backslash (``\\``, LaTeX-Zeilenumbruch bzw. escapter
      Backslash) wird nicht als Delimiter-Beginn fehlinterpretiert.
    - Unvollständige Delimiter ohne Gegenstück bleiben unverändert stehen,
      statt den restlichen Text zu zerstören.

    Hinweis: Code muss vor dem Aufruf geschützt sein (Platzhalter), damit
    Backslash-Klammern in Codeblöcken nicht umgeschrieben werden.
    """
    out: list[str] = []
    i, n = 0, len(text)

    while i < n:
        pair = text[i : i + 2]

        # 1. Bestehende $$...$$-Formel unverändert übernehmen.
        if pair == "$$":
            end = text.find("$$", i + 2)
            if end != -1 and text[i + 2 : end].strip() and "\n\n" not in text[i + 2 : end]:
                out.append(text[i : end + 2])
                i = end + 2
                continue

        # 2. Bestehende $...$-Formel unverändert übernehmen — mit denselben
        #    GFM-Grenzregeln wie split_formulas ("$ 20"/"$100 und $200" sind
        #    Preise; Audit B-4), damit Routing und Konversion dieselben
        #    Bereiche als Mathematik ansehen.
        if text[i] == "$" and not (i + 1 < n and text[i + 1].isspace()):
            end = text.find("$", i + 1)
            if (
                end != -1
                and (end == i + 1 or text[end - 1] != "$")
                and _math_bounds_ok(text, i + 1, end)
            ):
                out.append(text[i : end + 1])
                i = end + 1
                continue

        # 3. Doppelter Backslash -> kein Delimiter (z. B. LaTeX-Zeilenumbruch).
        if pair == "\\\\":
            out.append(pair)
            i += 2
            continue

        # 4. \[...\] -> $$...$$ (Display, DeepSeek/Gemini)
        if pair == r"\[":
            end = text.find(r"\]", i + 2)
            if end != -1:
                out.append("$$" + text[i + 2 : end] + "$$")
                i = end + 2
                continue

        # 5. \(...\) -> $...$ (Inline, DeepSeek/Gemini)
        if pair == r"\(":
            end = text.find(r"\)", i + 2)
            if end != -1:
                out.append("$" + text[i + 2 : end] + "$")
                i = end + 2
                continue

        out.append(text[i])
        i += 1

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
    """
    Ersetzt gültige LaTeX-Formeln durch Platzhalter.

    Unterstützte Delimiter:
    - ``$...$`` / ``$$...$$`` (klassisch)
    - ``\\(...\\)`` / ``\\[...\\]`` (DeepSeek/Gemini-Syntax)
    """
    out: list[str] = []
    i, n = 0, len(text)

    while i < n:
        ch = text[i]

        # 1. $$...$$ (Display)
        if text[i:i+2] == "$$":
            start = i + 2
            end = text.find("$$", start)
            if end != -1 and text[start:end].strip() and "\n\n" not in text[start:end]:
                formula = text[start:end]
                if validate_latex_braces(formula):
                    out.append(store(_escape_html(formula)))
                    i = end + 2
                    continue
                out.append(text[i])
                i += 1
                continue

        # 2. \\[...\\] (Display, DeepSeek/Gemini)
        if text[i:i+2] == r"\[":
            start = i + 2
            end = text.find(r"\]", start)
            if end != -1:
                formula = text[start:end]
                if validate_latex_braces(formula):
                    out.append(store(_escape_html(formula)))
                    i = end + 2
                    continue
                out.append(text[i])
                i += 1
                continue

        # 3. \\(...\\) (Inline, DeepSeek/Gemini)
        if text[i:i+2] == r"\(":
            start = i + 2
            end = text.find(r"\)", start)
            if end != -1:
                formula = text[start:end]
                if validate_latex_braces(formula):
                    out.append(store(_escape_html(formula)))
                    i = end + 2
                    continue
                out.append(text[i])
                i += 1
                continue

        # 4. $...$ (Inline)
        if ch == "$":
            # "$ " -> Preisangabe, kein Formelbeginn.
            if i + 1 < n and text[i + 1].isspace():
                out.append(ch)
                i += 1
                continue

            start = i + 1
            end = text.find("$", start)
            if (
                end != -1
                and (end == start or text[end - 1] != "$")
                and _math_bounds_ok(text, start, end)  # GFM-Grenzregeln (B-4)
            ):
                formula = text[start:end]
                if validate_latex_braces(formula):
                    out.append(store(_escape_html(formula)))
                    i = end + 1
                    continue
                out.append(ch)
                i += 1
                continue

        out.append(ch)
        i += 1

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


def _math_bounds_ok(text: str, start: int, end: int) -> bool:
    """Telegram/GFM-Regeln für Inline-Math: nicht leer, keine Leerzeichen an
    den Rändern, kein Leerabsatz innen, keine Ziffer direkt nach dem ``$``."""
    if end <= start:
        return False
    inner = text[start:end]
    if inner[:1].isspace() or inner[-1:].isspace() or "\n\n" in inner:
        return False
    following = text[end + 1 : end + 2]
    return not following.isdigit()


def _atomic_ranges(text: str) -> list[tuple[int, int]]:
    """Indivisble Bereiche des Rich-Textes: Fenced Code, ``$$…$$``, ``$…$``.

    Liefert sortierte, nicht überlappende ``(start, end)``-Paare. Ein Chunk-
    schnitt darf nie *in* einem dieser Bereiche landen (B-1).
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
        if text.startswith("$$", i):
            close = text.find("$$", i + 2)
            if close != -1 and text[i + 2 : close].strip():
                ranges.append((i, close + 2))
                i = close + 2
                continue
        if text[i] == "$" and not (i + 1 < n and text[i + 1].isspace()):
            close = text.find("$", i + 1)
            if (
                close != -1
                and (close == i + 1 or text[close - 1] != "$")
                and _math_bounds_ok(text, i + 1, close)
            ):
                ranges.append((i, close + 1))
                i = close + 1
                continue
        i += 1
    return ranges


def _split_guarded_unit(unit: str, max_chars: int) -> list[str]:
    """Zerlegt eine das Limit überschreitende Atom-Einheit in wohlgeformte Teile.

    Die ``$$…$$``/``` ```…``` ``/``$…$``-Delimiters werden je Fragment neu
    gesetzt, damit jedes Stück für Telegram eine vollständige Formel bzw. ein
    vollständiger Code-Block bleibt (nur die *Inhalte* teilen sich auf).
    """
    for delim in ("$$", "```", "$"):
        if len(unit) > 2 * len(delim) and unit.startswith(delim) and unit.endswith(delim):
            inner = unit[len(delim) : -len(delim)]
            head = ""
            if delim == "```":
                nl = inner.find("\n")
                if 0 < nl <= 64:
                    head, inner = inner[: nl + 1], inner[nl + 1 :]
            budget = max(max_chars - 2 * len(delim) - len(head) - 2, 16)
            lines = inner.split("\n")
            parts = _group([ln + "\n" for ln in lines if ln != ""], budget, "") or [""]
            fragments: list[str] = []
            for index, part in enumerate(parts):
                if delim == "```":
                    body = part if part.endswith("\n") else part + "\n"
                    fragments.append(f"```{head}{body}```" if index == 0 else f"```\n{body}```")
                elif delim == "$$":
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
        chunks = _safe_chunk(rich_text, RICH_MESSAGE_MAX_CHARS)
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
