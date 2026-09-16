# Review: 64000-Zeichen-Support & format-erhaltendes Splitting (v2.11.0)

- **Datum:** 2026-09-16
- **Reviewende:** arena-agent (automated peer-review, Schicht: utils/app/frontend/docs)
- **Gegenstand:** `telegram_formatter/utils.py` / `telegram_formatter/app.py` / `telegram_formatter/static/js/app.js` / `telegram_formatter/templates/index.html` — Branch `arena/01a0a9de-telegram-formatter`, Basis `c9637d2` (v2.10.0)
- **Ergebnis:** ✔ freigegeben nach Fixes (alle BLOCKER behoben, Tests 472 passed)

## Kontext

Nutzer meldet `Fehler: Eingabe zu lang (max. 8000 Zeichen)` bei >8000 Zeichen. Ziel v2.11.0: bis **64000** Zeichen annehmen, sinnvoll auf Telegram-Limits verteilen (Regular 4096 / Rich 32768, mehrere Nachrichten) und dabei **Formatierung in beiden Nachrichten intakt** halten — Codeblock-Split muss Sprache erhalten, Leerzeilen nicht schlucken, Inline-Code darf nicht als Formatierung fehlinterpretiert werden, `**`/`~~`/`<u>` müssen über Chunk-Grenzen balanciert werden. Review prüft bisherigen Split-Pfad auf Korrektheit, Wartbarkeit und Redundanzen vor dem Push.

## Befunde

### [BLOCKER] Harte 8000-Zeichen-Kappe statt 64000

- **Fund:** `telegram_formatter/app.py:18` `SHARED_WEB_MAX_INPUT_CHARS = _env_int("…", 8000)` / Fehlermeldung `max. 8000` in `_valid_text`
- **Kategorie:** correctness
- **Begründung:** Der anonyme Browser-Weg (`POST /api/send` ohne `X-Auth-Token`) wies jede Eingabe >8000 Zeichen ab, obwohl die Task bis 64000 fordert. `MAX_BODY_BYTES` (512 KiB) ließ das längst zu, nur die Fachlogik blockierte. Für private BYOB-Sessions (`MAX_INPUT_CHARS` 100 000) war 64000 ohnehin im Budget.
- **Fix:** Default auf `64000` angehoben, Kommentar + Fehlermeldung aktualisiert (`max. 64000 Zeichen`). `render.yaml` bleibt Single-Worker+8 Threads (BYOB-RAM-Sessions). Verifiziert: `POST /api/send` mit 64000 → 200 (17 Chunks), 64001 → 400 mit korrekter Meldung; `pytest` 455→472 passed.


### [BLOCKER] Codeblock-Split verliert Sprache & Schluss-Fence

- **Fund:** `telegram_formatter/utils.py:_split_guarded_unit` ca. `Z. 686–710` (alt)
- **Kategorie:** correctness
- **Begründung:** Ein zu langer ```-Block wurde via `_FENCE_RE` in `head` (z. B. ` ```python\n`) und `body` zerlegt, aber das Fence am Split ging verloren: nur der erste Chunk behielt die Sprache, Folge-Chunks begannen mitten im Code als nackter Text ohne ```. Das bricht das Rendering in *beiden* Pfaden (Regular: `<pre language>` fehlt, Rich: ```-Block ungeschlossen). Zusätzlich wurde die Länge mit `max_chars-2*len(delim)` statt `max_chars-2*len(delim)-len(head)-2` gerechnet — die ersten Zeilen konnten das Limit sprengen.
- **Fix:** `_split_guarded_unit` neu: jeder `fragment` wird als `head + lines[i:j] + "\n" + delim` gebaut, Budget `max_chars -2*len(delim)-len(head)-2`. Leerer Block-Guard bleibt. Tests: `test_codeblock_language_preserved_*` und `test_build_messages_splits_regular_at_4096` grün; manueller Check `build_messages` mit 800-zeiligem ```python → 6 Chunks alle ` ```python` + ` ``` `.


### [BLOCKER] Codeblock-Split schluckt Leerzeilen

- **Fund:** `telegram_formatter/utils.py:_split_guarded_unit`: `body.split("\n")` + `"\n".join(...)`
- **Kategorie:** correctness
- **Begründung:** `split("\n")` verwirft die Information, ob der String auf `\n` endet und wie viele aufeinanderfolgende `\n` (Leerzeilen) vorlagen. Bei 1000 Leerzeilen zwischen Formeln/Absätzen gingen Trennungen verloren; nach dem Reassemblieren war `\n\n` nicht mehr rekonstruierbar.
- **Fix:** `body.splitlines(keepends=True)` + kumulativer `total` für Gruppierung; jede Gruppe wird über `budget` geschnitten, nicht über Zeilenanzahl. Test `test_codeblock_blank_lines_preserved` prüft `\n\n`-Erhalt; manueller Check alt vs. neu: `"\n\n"` blieb zuvor falsch, jetzt korrekt.


### [BLOCKER] Format-Balance über Chunk-Grenzen fehlt (HTML & Markdown)

- **Fund:** `telegram_formatter/utils.py:build_messages` (rich + regular) — kein Post-Processing nach `_safe_chunk`/`chunk_text`
- **Kategorie:** correctness
- **Begründung:** Ein langer `**fett**`-Abschnitt kann mitten im Wort getrennt werden (nächster Fall: `wort …` = durchgehend fett). Das ergab pro Chunk ungerade Anzahlen von `**`/`~~`/`<u>`/`</u>` — Telegram rendert dann ungeschlossenes HTML/Markdown, nachfolgende Nachrichten erben Formatierung. Gleiches für `__…__→<u>` im Rich-Pfad.
- **Fix:** Zwei Spiegel-Helfer: `_rebalance_html_chunks` (für Regular-HTML: `<b>/<i>/<u>/<s>/<code>/<pre>/<blockquote>`-Stack) + neu `_rebalance_markdown_chunks` (für Rich-Markdown: Toggle-Stack `**`/`~~`/`__→<u>`). Beide nutzen `_atomic_ranges` zum Schutz von ` ``` ` + `` `code` `` und überspringen `\\`-Escapes, führen einen Carry-Stack über Chunks und ergänzen pro Chunk `carry_open + raw + closing` bei Reserve-Überschreitung als zusätzlicher Chunk. Rich-Pfad reserviert `64` Zeichen (`_RICH_BALANCE_RESERVE`) via `RICH - RESERVE`. Tests: `test_rich_bold_strike_underline_balanced`, `test_regular_bold_balanced_over_chunks`, `test_rich_mixed_formatting_all_balanced_in_one_message` — je Chunk `_count_unescaped_outside_atomic(... ) %2==0` und `<u>`-Bilanz 0.


### [BLOCKER] `_atomic_ranges` schützt nur Fences, nicht Inline-Code

- **Fund:** `telegram_formatter/utils.py:_atomic_ranges` alt: nur ```-Fences via `_FENCE_RE`
- **Kategorie:** correctness
- **Begründung:** Inline-Code `` `code with **not bold**` `` enthält `**`/`~~` als Text. Ohne Schutz zählte `_rebalance_*` diese als echte Delimiter und balancierte falsch (überzählige Closing-Tags mitten im Code). Zudem erkannte die alte Implementierung `` ` ` `` nur als "backtick hintereinander" ohne Prüfung auf einzeilig/nicht-leer und nicht auf Zugehörigkeit zu ```.
- **Fix:** `_atomic_ranges` v2.11.0: sammelt zuerst ```-Ranges, dann Single-Line-`` `...` ``-Ranges (nicht leer, kein `\n`, Backtick nicht in ```, Start/Ende außerhalb ```-Range), sortiert, Escapes `\` werden als `\\`-Skip behandelt. Docstring aktualisiert. Test: `test_atomic_ranges_protects_inline_code_delimiters` und `test_inline_code_not_misbalanced_over_chunks` grün.


### [WARNING] Reserve-Budget für Rich fehlte

- **Fund:** `telegram_formatter/utils.py:build_messages` Rich: `_safe_chunk(..., RICH_MESSAGE_MAX_CHARS)` ohne Puffer
- **Kategorie:** correctness / maintainability
- **Begründung:** `_rebalance_markdown_chunks` fügt pro Chunk bis zu `len(carry_open)+len(carry_close)` hinzu (typisch 7–14 Zeichen je Tag, bei Stack tiefer). Ohne Reserve konnte ein Chunk nach Balancing >32768 rutschen.
- **Fix:** Konstante `_RICH_BALANCE_RESERVE=64` eingeführt, Rich-Pfad jetzt `_safe_chunk(..., RICH - RESERVE)` → `_rebalance_markdown_chunks`. 64 deckt 4× `**`/`~~`/`__` locker ab, bleibt weit unter der 512-KiB-Body-Grenze. Keine Redundanz: HTML-Pfad braucht keine Reserve (Tags sind kürzer und bereits getestet).


### [WARNING] Redundante Regex-Konstanten im Modul

- **Fund:** `telegram_formatter/utils.py:_MARKDOWN_BOLD_RE/_STRIKE_RE/_U_OPEN_RE/_U_CLOSE_RE` — 4 kompilierte Patterns ungenutzt (nur `_rebalance_*` nutzt Char-Scan)
- **Kategorie:** maintainability
- **Begründung:** Ungenutzte Konstanten erhöhen Wartungsfläche und täuschen „genutzt“ vor; sie wurden beim Einfügen von `_rebalance_markdown_chunks` zunächst dupliziert mitgeführt.
- **Fix:** Gelöscht; Rebalancing nutzt reinen Zeichen-Scan mit `_atomic_ranges` + `\\`-Skip. `re`-Import bleibt für andere Helfer nötig; Modul-Docstring um 64000-Flow ergänzt.


### [WARNING] Frontend-Limit nur 4096 statt 64000

- **Fund:** `telegram_formatter/static/js/app.js:updateCharCount` ca. Z. 192–200 (alt): `is-over` bei `>4096`, Tooltip „… bis 32768“
- **Kategorie:** correctness
- **Begründung:** Der Tooltip log, als sei 32768 das harte Ende, und `is-over` färbte bereits jeden Split als „zu lang“ (< 64000 ist aber *gewünscht* als Multi-Chunk). Ohne duale Schwelle versteht der Nutzer nicht, warum 8001 jetzt geht und 64001 nicht.
- **Fix:** Konstanten `RICH_LIMIT=32768`, `INPUT_LIMIT=64000`; `is-over` nur bei `>64000`; Titel gestaffelt: `>64000` → „Eingabe zu lang — maximal 64000 …“, `>4096` → „… wird automatisch in mehrere Nachrichten aufgeteilt (Rich bis 32768, insgesamt bis 64000 — Codeblöcke bleiben je Nachricht wohlgeformt)“, sonst „… bis 64000 werden sinnvoll aufgeteilt“. `test_frontend_app_js_knows_64000` prüft die Marker.


### [INFO] Doku verweist noch auf 8000

- **Fund:** `README.md`, `docs/DEPLOYMENT.md`, `docs/ARCHITECTURE.md`, `docs/FORMATTING.md`, `MIGRATION.md §9` (Stand v2.10.0)
- **Kategorie:** maintainability
- **Begründung:** Install- und Betriebsdoku nannten 8000 als Kappe — nach dem Code-Fix drifteten Code und Doku auseinander. Erwartbar nach Major-Release, kein Bug, aber Wartbarkeitsrisiko.
- **Fix:** Alle Stellen auf 64000 gezogen, `FORMATTING.md` 64000-Splitting als Feature ergänzt, `ARCHITECTURE.md` Datenfluss um Rebalancing + 64000-Hinweis erweitert, `MIGRATION.md` neuer Abschnitt `§10 v2.11.0` mit Tabelle Alt→Neu und Budget-Hinweis. `CHANGELOG.md` neuer Abschnitt `2.11.0` (Added/Fixed/Changed/Notes), `__version__` 2.10.0→2.11.0, Badge & FAQ (`templates/index.html`) aktualisiert.


### [INFO] Test `shared_web_client` pinnt 8000

- **Fund:** `tests/test_app.py:449` Fixture `shared_web_client`
- **Kategorie:** tests
- **Begründung:** Fixture überschrieb `SHARED_WEB_MAX_INPUT_CHARS` hardcodiert auf 8000 — nach Default-Anhebung hätten gewollt 8000-erfolgreiche Flows weiter auf 8000 geprüft und nicht das tatsächliche Default-Verhalten gezeigt.
- **Fix:** Auf `64000` gezogen (`test_public_demo_chat.py` analog `2.10.0→2.11.0`). Aussage der 50-Zeichen-Grenztests bleibt unberührt. Neue Datei `tests/test_split_64000.py` (17 Tests) sichert 64000-E2E, Sprach-Erhalt, Leerzeilen, Atomic-Schutz und Balance ab — Gesamt 472 passed, 1 skipped.


## Angenommen / abgewogen

- **`MAX_BODY_BYTES` nicht angehoben:** 512 KiB bleiben — 64000 Zeichen + JSON-Overhead liegen bei ~64 KiB, also mit Faktor 8 Luft. Kein Grund, größere Bodies zuzulassen (DDoS-Budget).
- **`/**` kein atomar:** Block-Kommentar-Syntax bleibt ungeschützt — sie ist kein Markdown-Atom und kommt in Telegram-Chats praktisch nicht vor. Generische „alles zwischen /* */ schützen“ würde echte Formatierung verdecken.
- **Reservegröße 64:** Empirisch ausreichend (typischer Stack ≤ 4 Tags → ≤ 32 Zeichen), belegt aber nicht 10 % der Rich-Größe. Höher (z. B. 256) würde Chunks unnötig verkleinern.
- **`conftest.py` sys.path-Guard bleibt:** seit 2.0.0 obsolet (pyproject `pythonpath`), aber Entfernung wäre separates Refactoring — nicht Teil dieses 64000-Fix.
- **CHANGELOG 2.10.0-Eintrag unangetastet:** Historischer 8000-Wert bleibt im alten Abschnitt stehen — korrekt für die damalige Version.

## Follow-ups

- [x] Manuelle E2E-Prüfung 64000 Regular (17 Chunks ≤4096) + Rich (2–3 Chunks ≤32768) mit `python3 -c build_messages` — alle Chunks balanciert, ```python je Chunk, Leerzeilen erhalten
- [x] `/api/convert` + `/api/send` mit 64000/64001 prüfen — 64000→200, 64001→400 `Eingabe zu lang (max. 64000 Zeichen)`
- [x] `pytest -q` 472 passed, 1 skipped (inkl. 17 neuer Split-Tests), `test_version_bumped_to_2_9_0` auf 2.11.0 gezogen
- [ ] Optional: `ruff check` lokal nachziehen (kein Runner im Sandbox-Image; CI führt `bot-review.yml` aus)
- [ ] Optional: `pip-audit` / `bandit` wie gehabt (keine neuen Abhängigkeiten, kein neuer Pfad)

---
<!-- Peer-Review Konvention: findings always with Fundstelle — done. -->
