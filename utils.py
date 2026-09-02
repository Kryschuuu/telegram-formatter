r"""
utils.py
========
Reine, I/O-freie Konvertierungs- und Aufteilungslogik für
"Markdown + LaTeX -> Telegram".

Dieses Modul enthält KEINE Netzwerk- oder Flask-Abhängigkeiten und ist
dadurch isoliert testbar (siehe ``tests/test_utils.py``). Die eigentlichen
Versandaufrufe liegen in :mod:`sender`, die Web-Oberfläche in :mod:`app`.

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
#: Maximale Block-Anzahl einer Rich Message (Listeneinträge, Tabellenzeilen ...).
RICH_MESSAGE_MAX_BLOCKS = 500


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
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
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
            if end != -1:
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

            # Einzelnes $ suchen (nicht $$)
            start = i + 1
            end = text.find("$", start)
            if end != -1 and (end == start or text[end - 1] != "$"):
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
            if end != -1:
                out.append(text[i : end + 2])
                i = end + 2
                continue

        # 2. Bestehende $...$-Formel unverändert übernehmen
        #    ("$ 20" ist eine Preisangabe, kein Formelbeginn).
        if text[i] == "$" and not (i + 1 < n and text[i + 1].isspace()):
            end = text.find("$", i + 1)
            if end != -1 and (end == i + 1 or text[end - 1] != "$"):
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
        for i, value in enumerate(self._items):
            text = text.replace(f"\x00{i}\x00", value)
        return text


def _escape_html(text: str) -> str:
    """Escaped die drei in Telegram-HTML bedeutungstragenden Zeichen."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    def fenced(m: re.Match) -> str:
        lang = m.group(1).strip()
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

    # 8. Links.
    text = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text
    )

    # 9. Listenpunkte.
    text = re.sub(r"^\s*[-*+]\s+", "• ", text, flags=re.M)
    text = re.sub(r"^\s*\d+[.)]\s+", "• ", text, flags=re.M)

    # 10. Inline-Formatierung (Reihenfolge wichtig: fett vor kursiv,
    #     Unterstreichen vor Kursiv-_).
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"__(.+?)__", r"<u>\1</u>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", text)
    text = re.sub(r"~~(.+?)~~", r"<s>\1</s>", text)

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
            if end != -1:
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
            if end != -1 and (end == start or text[end - 1] != "$"):
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
    def fenced(m: re.Match) -> str:
        lang = m.group(1).strip()
        content = m.group(2).rstrip("\n")
        return store(f"```{lang}\n{content}\n```")

    text = re.sub(r"```([^\n]*)\n(.*?)```", fenced, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", lambda m: store(f"`{m.group(1)}`"), text)

    # 2. DeepSeek/Gemini-Delimiter auf Telegram-Syntax normalisieren
    #    (\(...\) -> $...$, \[...\] -> $$...$$). Code ist bereits geschützt.
    text = convert_deepseek_latex_syntax(text)

    # 3. Unterstreichen __x__ -> <u>x</u>.
    text = re.sub(r"__([^_\n]+)__", r"<u>\1</u>", text)

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


def build_messages(raw_text: str, chat_id: int | str) -> list[TelegramMessage]:
    """
    Baut aus Rohtext sendefertige Telegram-Nachrichten.

    - Enthält der Text LaTeX oder Tabellen, wird er in Rich Markdown
      übersetzt und über ``sendRichMessage`` (Feld ``markdown``) verschickt.
    - Reiner Formatierungstext läuft über ``sendMessage`` mit
      ``parse_mode="HTML"``.

    In beiden Fällen wird an den jeweiligen Zeichenlimits aufgeteilt
    (4096 bzw. 32768), wobei die Aufteilung an Absatz-/Zeilen-/Wortgrenzen
    erfolgt und Formatierungen so weit wie möglich intakt bleiben.
    """
    text = normalize_text(raw_text).strip()
    if not text:
        return []

    if needs_rich_message(text):
        rich_text = markdown_to_rich_markdown(text)
        chunks = chunk_text(rich_text, RICH_MESSAGE_MAX_CHARS)
        return [
            TelegramMessage(
                kind="rich",
                payload={"chat_id": chat_id, "rich_message": {"markdown": chunk}},
            )
            for chunk in chunks
        ]

    html = markdown_to_html(text)
    chunks = chunk_text(html, REGULAR_MESSAGE_MAX_CHARS)
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
    chunks: list[str] = []
    buf: list[str] = []
    for item in items:
        if len(item) > max_chars:
            if buf:
                chunks.append(joiner.join(buf))
                buf = []
            chunks.extend(item[i : i + max_chars] for i in range(0, len(item), max_chars))
            continue
        if buf and len(joiner.join(buf)) + len(joiner) + len(item) > max_chars:
            chunks.append(joiner.join(buf))
            buf = []
        buf.append(item)
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
