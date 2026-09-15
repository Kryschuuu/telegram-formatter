# Review: LaTeX-Normalisierung & gemeinsamer Formel-Scanner (v2.10.0)

- **Datum:** 2026-09-15
- **Reviewende:** Maintainer-Review des Fixes (Selbst-Review mit Gegenprobe:
  456 Tests, 5000 randomisierte Dokumente, Diff-Review)
- **Gegenstand:** v2.10.0; `telegram_formatter/utils.py`
  (Formel-Scanner, `convert_deepseek_latex_syntax`, `_protect_math`,
  `_atomic_ranges`), `tests/test_utils.py`, `docs/FORMATTING.md`, `README.md`
- **Ergebnis:** ✔ freigegeben

## Kontext

Gemeldet wurde: In einer DeepSeek-Antwort mit mehreren Formeln wurden **einige
Formeln korrekt gerendert, andere standen wörtlich in der Telegram-Nachricht**
— sichtbar waren die LaTeX-Symbole (`\cdot`) samt `$`. Der Bericht prüft die
Ursache, die Reichweite und die Fixes; Grundlage sind die
GFM-/Pandoc-Randregeln für `$`-Mathe, die Telegram für Rich Markdown übernimmt
(Pandoc `tex_math_dollars`: das öffnende `$` braucht rechts ein
Nicht-Whitespace-Zeichen, das schließende links, auf das schließende darf keine
Ziffer folgen; `$$`-Blöcke dürfen Whitespace um den Inhalt tragen, aber keine
Leerzeile enthalten).

## Befunde

### [BLOCKER] `\( x \)` wurde zu `$ x $` — Telegram rendert das nicht

- **Fund:** `telegram_formatter/utils.py:294` (Schritt 5: `\(...\)` -> `$...$`, ohne
  Rand-Normalisierung) bzw. `utils.py:226` (Funktion als Ganzes); der
  Dollar-Zweig ohne Randreparatur: `utils.py:189`/`utils.py:811`
- **Kategorie:** correctness
- **Begründung:** DeepSeek/Gemini setzen fast immer Leerzeichen an die Ränder
  (`\( a \cdot b \)`) oder verteilen die Formel über Zeilen. Die Konvertierung
  hat den Inhalt 1:1 übernommen und daraus `$ a \cdot b $` gebaut. Nach den
  Randregeln ist das **keine** Formel — und weil genau dieser Zweig („bereits
  vorhandene Dollar-Formeln bleiben unangetastet“) keine Randprüfung kannte,
  blieb der ungültige Bereich auch im weiteren Verlauf als „Mathe“ markiert
  (Chunk-Schutz, Routing). Ergebnis: Telegram zeigt den Quelltext wörtlich.
  Die Formel ohne Leerzeichen (`\(a \cdot b\)` → `$a \cdot b$`) rendert
  korrekt — genau das asymmetrische Verhalten aus dem Bericht („einige Formeln
  gehen, andere nicht“). Reproduktion vor dem Fix:
  `build_messages(r"Die Kraft \( a \cdot b \) wirkt.", 1)` →
  `{'rich_message': {'markdown': 'Die Kraft $ a \cdot b $ wirkt.'}}`.
- **Fix:** `utils.py:330` `_normalize_inline_math()` — Rand-Whitespace wird
  getrimmt, Zeilenumbrüche in einer Inline-Formel werden zu Leerzeichen (LaTeX
  wertet sie im Mathe-Modus ohnehin so); `utils.py:344`
  `_normalize_display_math()` zieht Leerzeilen in Block-Formeln zu einem
  Umbruch zusammen (eine Leerzeile würde den Block beenden). Angewendet in
  `_render_math_span()` (`utils.py:356`) beim Übersetzen der
  Backslash-Delimiter. Regressionstest:
  `tests/test_utils.py::TestMathNormalization`.

### [WARNING] Vier Formel-Scanner mit divergierenden Regeln (Audit O-3)

- **Fund:** `utils.py:120` (`split_formulas`), `utils.py:226`
  (`convert_deepseek_latex_syntax`), `utils.py:754` (`_protect_math`),
  `utils.py:1062` (`_atomic_ranges`)
- **Kategorie:** maintainability
- **Begründung:** Vier handgeschriebene Zeichen-Schleifen implementierten
  dieselbe Idee unterschiedlich: `$$`-Blöcke wurden von `_atomic_ranges` ohne
  Leerzeilen-Prüfung als atomar markiert, `split_formulas`/`_protect_math`
  verlangten dagegen „keine Leerzeile“; `\(...\)` wurde von allen drei
  Erkennern akzeptiert, aber nur von einem konvertiert und von keinem
  normalisiert. Das Audit hatte die Bug-Klasse in O-3 benannt („ein Pfad
  gefixt, drei nicht“) — der gemeldete Fehler ist ihr zweiter Ausläufer.
- **Fix:** `utils.py:260` `iter_math_spans()` (+ `MathSpan`) ist der einzige
  Scanner; `split_formulas`, `convert_deepseek_latex_syntax`, `_protect_math`
  und `_atomic_ranges` sind dünne Aufrufer. Die Regeln (Pandoc/GFM plus die
  großzügige Behandlung der eindeutigen LLM-Backslash-Delimiter) stehen an
  einer Stelle im Modulkopf von `utils.py`. Vertragstest:
  `tests/test_utils.py::TestMathSpans`.

### [WARNING] Erste Fassung der Dollar-Ausnahme war zu weit (im Review verworfen)

- **Fund:** `utils.py:197` (`_dollar_inline_ok`, Zwischenstand)
- **Kategorie:** correctness
- **Begründung:** Die Regel „Rand-Whitespace + Backslash-Kommando irgendwo im
  Inhalt“ hätte `$ 5 (\circa) und $ 10` und `$ 100 bei \alpha = 2\% und $ 200`
  zu `$5 (\circa) und$ 10` verschmolzen und Prosa als Formel gesendet (der
  Nutzer sieht dann einen KaTeX-Fehler statt der gemeinten Preisangabe).
  Geprüft mit `TestMathNormalization::test_padded_dollar_without_leading_command_stays_text`.
- **Fix:** auf ein **führendes** Kommando verengt (`_LATEX_COMMAND_RE.match`
  statt `.search`), Länge auf 400 Zeichen begrenzt, keine Leerzeile im Bereich.
  `$ x \cdot y $` bleibt damit bewusst Text (dokumentierte Grenze).

### [INFO] Leerzeilen-Erkennung war zu eng

- **Fund:** `utils.py:1056` (`_math_bounds_ok`), `utils.py:772`
  (`_protect_math`), `utils.py:159`/`utils.py:260` (Block-Erkennung)
- **Kategorie:** correctness
- **Begründung:** Eine „Leerzeile“ mit Leerzeichen oder Tabulator (`\n   \n`,
  typisch für kopierten Rich-Text) passierte alle Prüfungen; der Block bzw. die
  Inline-Formel wurde dann als gültig behandelt, obwohl GFM dort endet.
- **Fix:** `utils.py:192` `_has_blank_line()` (`\n[ \t]*\n`) wird von allen
  Regeln genutzt — inline (`_math_bounds_ok`) wie im Block.

### [INFO] Leere Delimiter erzeugten leere Formeln

- **Fund:** `utils.py:294` ff.
- **Kategorie:** correctness
- **Begründung:** `\(\)` wurde zu `$$` und `\[\]` zu `$$$$` zusammengezogen —
  ungültige, aber als „Formel“ geroutete Bereiche.
- **Fix:** Beide Formen bleiben Text (wie ``$$$$`` schon vorher); geprüft in
  `TestMathNormalization::test_empty_delimiters_stay_text`.

## Angenommen / abgewogen

- **`$ x $` in Dollar-Syntax:** Wird nur dann als Formel gelesen, wenn der
  Inhalt **mit** einem Backslash-Kommando beginnt (`$ \frac{a}{b} $`), der
  Bereich höchstens 400 Zeichen lang ist und keine Leerzeile enthält. Die
  erste Fassung dieses Fixes akzeptierte ein Kommando an beliebiger Stelle —
  im Review verworfen, weil sie `$ 5 (\circa) und $ 10` und
  `$ 100 bei \alpha = 2\% und $ 200` (Prosa mit Preisen) als Formel gelesen
  hätte. Die a-priori-Entscheidung lautet: lieber eine Formel unangetastet
  lassen als Prosa als Mathe senden. Randlose Dollar-Formeln bleiben wie
  bisher unangetastet (Bestandstests
  `test_convert_leaves_existing_dollar_math_untouched`).
- **Ziffer direkt hinter dem Schließer** (`\(x\)2` → `$x$2`): Nach GFM ist das
  keine Formel. Eine Reparatur ohne Textänderung gibt es nicht — ein
  unsichtbares Zeichen (ZWSP/Word-Joiner) würde kopierten Text verfälschen, ein
  `<tg-math>`-HTML-Island im Markdown-Pfad ist nicht dokumentiert und könnte
  die ganze Nachricht kosten. Bewusst **nicht** umgesetzt; die Häufigkeit ist
  verschwindend (Formel unmittelbar vor einer Ziffer).
- **`$$a\n\nb$$` in Dollar-Syntax:** Bleibt Text (GFM-Regel: Leerzeile beendet
  den Block). Nur die Backslash-Delimiter der LLMs werden repariert, weil sie
  eindeutig als Blockformel gemeint sind. Wer bewusst `$$` schreibt, bekommt
  die GFM-Semantik.
- **Doppelte Backslashes** (`\\(x\\)`, z. B. aus rohen JSON-Antworten) gelten
  weiterhin als Escape (`\\` = LaTeX-Zeilenumbruch) und bleiben Text. Eine
  Heuristik „aussah wie JSON“ wäre nicht entscheidbar und würde echte
  `\\`-Zeilenumbrüche in Formeln zerstören.
- **Leere/„unterminierte“ Delimiter** bleiben unverändert stehen, statt den
  Rest der Nachricht zu konsumieren (Bestandsverhalten,
  `test_convert_unterminated_*`).

## Follow-ups

- [ ] Beobachten, ob „`$ x $` mit Kommando in der Mitte“ (heute bewusst
      Text) in echten LLM-Antworten häufig vorkommt; dann wäre eine
      inhaltsbasierte Heuristik erneut zu bewerten — mit Preisen
      (`$ 5 (\circa) und $ 10`) als Negativtest.
- [ ] O-1/O-2 des Audits (mehrfache Volltext-Durchläufe, `_group` O(n²))
      bleiben offen; der neue Scanner berührt sie nicht.
