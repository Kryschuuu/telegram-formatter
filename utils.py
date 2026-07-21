"""
utils.py
========
Hilfsfunktionen zur Konvertierung von Rohtext (LaTeX-Formeln, Tabellen,
Sonderzeichen) in Telegram-kompatibles Nachrichtenformat.

STATUS DIESER DATEI — BITTE ZUERST LESEN
-----------------------------------------
Das GitHub-Repository "Kryschuuu/telegram-formatter" sowie die Dateien
app.py, utils.py, beispiel_input.txt und beispiel_output.txt konnten zum
Zeitpunkt dieser Bearbeitung NICHT eingesehen werden (GitHub blockiert
automatisierten Zugriff auf die tree-Ansicht per robots.txt; der Repo-Name
taucht in keiner Websuche auf; es wurden auch keine Dateien in diesen Chat
hochgeladen). Diese Datei ist deshalb KEINE Zeile-für-Zeile-Korrektur des
Originalcodes, sondern eine lauffähige, getestete Referenzimplementierung,
die exakt die in der Aufgabenstellung beschriebenen Fehlerklassen sauber
löst:

  1. Verschachtelte LaTeX-Strukturen (z. B. \\binom{\\binom{70}{6}}{33})
     werden von naiven Regex-Ersetzungen zerstört, weil reguläre Ausdrücke
     keine beliebig tief verschachtelten, geklammerten Strukturen erkennen
     können ("balanced matching" ist mit regulären Sprachen nicht lösbar).
  2. Tabellen (z. B. "Tabelle 3" mit L(n,6,6,2)-Ergebnissen) wurden
     vermutlich als monospaced ASCII-Text verschickt statt als Telegrams
     native Tabellen-Blöcke — das verrutscht auf schmalen Bildschirmen.
  3. Sonderzeichen wie "ì" wurden verstümmelt, weil Dateien ohne explizites
     UTF-8-Encoding bzw. ohne Unicode-Normalisierung (NFC) verarbeitet
     wurden.
  4. LaTeX-Formeln wurden vermutlich über sendMessage() + parse_mode=
     "MarkdownV2" verschickt. Das ist der Kernfehler: Regular Messages
     unterstützen KEIN LaTeX — auch nicht mit $...$-Syntax. Natives LaTeX
     gibt es erst seit Telegram Bot API 10.1 (11. Juni 2026) und
     ausschließlich über Rich Messages (Methode sendRichMessage, Format
     "Rich Markdown" bzw. "Rich HTML").

Quellen (verifiziert per Live-Abruf am 21. Juli 2026, da dies nach dem
Trainingsstand des Modells liegt):
  - https://core.telegram.org/bots/api (Changelog Bot API 10.1 / 10.2)
  - https://core.telegram.org/bots/features#rich-messages
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# FEHLER 3 (Sonderzeichen, z. B. "ì"): Unicode-Normalisierung
# ---------------------------------------------------------------------------
def normalize_text(text: str) -> str:
    """
    Normalisiert Unicode-Text nach NFC (vorkomponierte Form).

    URSPRÜNGLICHER FEHLER (rekonstruiert):
    Öffnet man eine Datei mit open(path).read() OHNE encoding="utf-8" (z. B.
    unter Windows mit dem Locale-Default cp1252), werden Zeichen wie "ì"
    (U+00EC, LATIN SMALL LETTER I WITH GRAVE) entweder zu Mojibake ("Ã¬")
    oder — falls die Quelle NFD-normalisiert war — in zwei Codepoints
    zerlegt: "i" (U+0069) + COMBINING GRAVE ACCENT (U+0300). Nachgelagerte,
    zeichenweise arbeitende Funktionen (z. B. MarkdownV2-Escaping oder die
    Berechnung von MessageEntity-Offsets, die Telegram in UTF-16-Code-Units
    verlangt, siehe core.telegram.org/api/entities) zählen dann falsche
    Zeichenlängen und verschieben nachfolgende Formatierungen.

    KORREKTUR:
    - Dateien werden IMMER explizit mit encoding="utf-8" geöffnet (app.py).
    - Der Text wird zusätzlich mit unicodedata.normalize("NFC", ...) in die
      vorkomponierte Form gebracht, sodass "ì" unabhängig von der
      Eingabeform immer EIN einzelner Codepoint ist.
    """
    return unicodedata.normalize("NFC", text)


# ---------------------------------------------------------------------------
# FEHLER 1 (verschachtelte Formeln): klammern-balanciertes Parsing statt Regex
# ---------------------------------------------------------------------------
def validate_latex_braces(formula: str) -> bool:
    """Prüft, ob alle {}-Klammern in einer Formel korrekt balanciert sind."""
    depth = 0
    for ch in formula:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


@dataclass
class Segment:
    kind: str  # "text" | "inline_math" | "display_math"
    content: str


def split_formulas(text: str) -> list[Segment]:
    """
    Zerlegt einen Textblock zeichenweise in Text- und Formel-Segmente.

    URSPRÜNGLICHER FEHLER (rekonstruiert):
    Ein Muster wie re.compile(r'\\$(.+?)\\$') oder
    re.compile(r'\\\\binom\\{(.*?)\\}\\{(.*?)\\}') funktioniert nur für NICHT
    verschachtelte Ausdrücke. Bei $\\binom{\\binom{70}{6}}{33}$ matcht die
    "non-greedy" Gruppe (.*?) bereits an der ERSTEN schließenden Klammer
    nach der ersten öffnenden — die Formel wird syntaktisch mittendrin
    zerschnitten, z. B. zu "\\binom{70" statt "\\binom{70}{6}".

    KORREKTUR:
    Der Text wird zeichenweise durchlaufen. '$$' markiert Display-Formeln
    (Block, zentriert dargestellt), ein einzelnes '$' markiert Inline-
    Formeln. Für jede gefundene Formel wird validate_latex_braces()
    aufgerufen — verschachtelte Klammern werden dadurch NICHT mehr
    zerschnitten, weil kein Regex-Gruppen-Matching mehr auf den
    Klammerinhalt angewendet wird, sondern nur die Fundstelle der
    schließenden '$'/'$$'-Marke gesucht wird; der Formelinhalt selbst
    bleibt unangetastet und wird 1:1 an Telegram weitergereicht.
    Ein '$' vor einem Leerzeichen (z. B. Preisangaben wie "$ 20") wird
    NICHT als Formelbeginn gewertet.
    """
    segments: list[Segment] = []
    i, n = 0, len(text)
    buf: list[str] = []

    def flush_text() -> None:
        if buf:
            segments.append(Segment("text", "".join(buf)))
            buf.clear()

    while i < n:
        if text[i] == "$":
            is_display = text[i : i + 2] == "$$"
            delim = "$$" if is_display else "$"
            start = i + len(delim)

            if not is_display and start < n and text[start].isspace():
                buf.append(text[i])
                i += 1
                continue

            end = text.find(delim, start)
            if end == -1:
                buf.append(text[i])
                i += 1
                continue

            formula = text[start:end]
            if not validate_latex_braces(formula):
                formula = f"\\text{{[FEHLER: unbalancierte Klammern]}} {formula}"

            flush_text()
            segments.append(
                Segment("display_math" if is_display else "inline_math", formula)
            )
            i = end + len(delim)
        else:
            buf.append(text[i])
            i += 1

    flush_text()
    return segments


# ---------------------------------------------------------------------------
# FEHLER 2 (Tabellen): native Rich-Markdown-Tabellen statt ASCII-Grafik
# ---------------------------------------------------------------------------
def parse_pipe_table(block: str) -> list[list[str]] | None:
    """
    Parst einen Pipe-getrennten Tabellenblock (z. B. "Tabelle 3" mit
    L(n,6,6,2)-Ergebnissen) in eine Liste von Zeilen (Zeile = Liste Zellen).

    Erwartetes Rohformat in der Eingabedatei, z. B.:
        n     | L(n,6,6,2)  | Quelle
        ------|-------------|-------
        20    | 10          | [Thm 3.1]

    URSPRÜNGLICHER FEHLER (rekonstruiert):
    Solche Blöcke wurden vermutlich mit str.split() auf Leerzeichen zerlegt
    und in einen <pre>-/Codeblock verpackt (sendMessage-Regular-Message).
    Auf dem Desktop sieht das notdürftig nach Tabelle aus, auf schmalen
    Handybildschirmen bricht die feste Breite um — aus der Tabelle wird
    Zeichensalat, und Telegram unterstützt in Regular Messages ohnehin
    keine echten Tabellen.

    KORREKTUR:
    Das Pipe-Format wird erkannt (inkl. optionaler Markdown-Trennzeile
    "---|---|---") und als saubere Zeilenstruktur zurückgegeben, die
    anschließend über rows_to_rich_markdown_table() in eine ECHTE Rich-
    Markdown-Tabelle übersetzt wird (GFM-Syntax; Telegram rendert das seit
    Bot API 10.1 nativ inkl. Ausrichtung, colspan/rowspan, striped-Zebra-
    streifen — siehe core.telegram.org/bots/features#rich-messages).
    """
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2 or "|" not in lines[0]:
        return None

    rows: list[list[str]] = []
    for idx, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if idx == 1 and all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
            continue  # Markdown-Trennzeile, keine Nutzdaten
        rows.append(cells)
    return rows if len(rows) >= 2 else None


def rows_to_rich_markdown_table(rows: list[list[str]]) -> str:
    """
    Baut aus geparsten Zeilen eine GitHub-Flavored-Markdown-Tabelle, wie sie
    Telegrams "Rich Markdown"-Stil seit Bot API 10.1 nativ rendert.
    """
    if not rows:
        return ""
    header, *data = rows
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(" --- " for _ in header) + "|"]
    out.extend("| " + " | ".join(row) + " |" for row in data)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# FEHLER 4 (LaTeX in Regular Messages statt Rich Messages): Zusammenbau
# ---------------------------------------------------------------------------
def to_rich_markdown(raw_text: str) -> str:
    """
    Wandelt einen Rohtext-Abschnitt in gültiges "Rich Markdown" (Bot API
    10.1/10.2) um: $...$/$$...$$-Formeln bleiben unverändert als natives
    LaTeX stehen (Telegram rendert das nur im Rich-Message-Pfad, siehe
    sendRichMessage() in app.py — NICHT über sendMessage()), Pipe-
    Tabellenblöcke werden in native Rich-Markdown-Tabellen übersetzt.
    """
    text = normalize_text(raw_text)
    blocks = re.split(r"\n\s*\n", text)
    rendered = []
    for block in blocks:
        table_rows = parse_pipe_table(block)
        if table_rows:
            rendered.append(rows_to_rich_markdown_table(table_rows))
            continue

        segments = split_formulas(block)
        parts = []
        for seg in segments:
            if seg.kind == "text":
                parts.append(seg.content)
            elif seg.kind == "inline_math":
                parts.append(f"${seg.content}$")
            else:
                parts.append(f"$${seg.content}$$")
        rendered.append("".join(parts))
    return "\n\n".join(rendered)


RICH_MESSAGE_MAX_CHARS = 32_768  # UTF-8-Zeichen-Limit für Rich Messages (Bot API 10.1)
RICH_MESSAGE_MAX_BLOCKS = 500    # Max. Blöcke (Listeneinträge, Tabellenzeilen, Zitate ...)


def chunk_rich_markdown(text: str, max_chars: int = RICH_MESSAGE_MAX_CHARS) -> list[str]:
    """
    Teilt langen Rich-Markdown-Text an Absatzgrenzen unterhalb des
    32.768-Zeichen-Limits von Rich Messages auf. Es wird nie mitten in einer
    Formel oder Tabelle getrennt, da Absätze durch Leerzeilen begrenzt sind
    und Formeln/Tabellen innerhalb eines Absatzes zusammengehalten werden.
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = text.split("\n\n")
    chunks, current, current_len = [], [], 0
    for p in paragraphs:
        p_len = len(p) + 2
        if current_len + p_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(p)
        current_len += p_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


# ---------------------------------------------------------------------------
# Fallback: Escaping für REGULAR Messages (sendMessage, parse_mode=MarkdownV2)
# ---------------------------------------------------------------------------
_MARKDOWN_V2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def escape_markdown_v2(text: str) -> str:
    """
    Escaped Sonderzeichen für Telegrams klassisches MarkdownV2 (Regular
    Messages, siehe core.telegram.org/bots/api#markdownv2-style).

    WICHTIG: MarkdownV2 kennt KEIN LaTeX — '$' hat dort keine Sonderbe-
    deutung und würde einfach als literales Dollarzeichen angezeigt. Diese
    Funktion wird deshalb nur für kurze Klartext-Abschnitte OHNE Formeln
    /Tabellen verwendet; alles mit Formeln/Tabellen läuft stattdessen über
    to_rich_markdown() + sendRichMessage() (siehe app.py: build_messages()).
    """
    return re.sub(f"([{re.escape(_MARKDOWN_V2_SPECIAL)}])", r"\\\1", text)


def needs_rich_message(text: str) -> bool:
    """
    True, wenn der Textabschnitt LaTeX-Formeln oder eine Pipe-Tabelle
    enthält und deshalb zwingend über sendRichMessage() statt sendMessage()
    verschickt werden muss, weil Regular Messages beides nicht darstellen
    können.
    """
    has_math = "$" in text
    has_table = bool(re.search(r"^\s*\S.*\|.*\S\s*$", text, re.MULTILINE))
    return has_math or has_table
