# Review: Gemini-Link-Bereinigung — reine URLs (v2.15.0)

- **Datum:** 2026-10-25
- **Reviewende:** Arena-Agent (Implementierungs-Review)
- **Gegenstand:** Branch `arena/362e8d5d-telegram-formatter` —
  `telegram_formatter/utils.py` (Link-Bereinigung: `clean_url`,
  `clean_gemini_links`, `_normalize_links`), `telegram_formatter/__init__.py`,
  Tests (`tests/test_utils.py`, `tests/test_preview.py`, `tests/test_app.py`),
  Doku (`docs/FORMATTING.md` §4, README, `docs/API.md`, `CHANGELOG.md`,
  `MIGRATION.md` §16, FAQ in `templates/index.html`)
- **Ergebnis:** ✔ freigegeben

## Kontext

Nutzermeldung: „google gemini füht jetzt ids an alles links“ — Gemini hängt
an jeden zitierten Link die Tracking-IDs `&ra=gemini&xstg=<Token>` (das
Token allein ~1 500 Zeichen) und wickelt Zeitstempel-Zitate doppelt
(`[[00:05]([url&amp;…](url&…))]`). Der Formatter soll die Tags entfernen
und nur reine URLs übrig lassen. v2.15.0 führt `clean_url()` (einzelne URL)
und `clean_gemini_links()` (ganzer Text) ein; beide Konverter wenden die
Bereinigung über `_normalize_links()` automatisch an.

Alle Befunde wurden vor der Implementierung mit dem gemeldeten Beispiel
(strukturell 1:1, Token-Länge wie im Original) reproduziert: Eingabe
9 625 Zeichen (drei Zitate + Video-Zeile) → 5 007 Zeichen HTML-Payload vor
dem Fix, 452 Zeichen danach; der volle Text wurde in 16 Nachrichten
zerteilt statt in eine.

## Befunde

### [BLOCKER→behoben] Gemini-Tracking stand 1:1 in den Payloads

- **Fund:** `telegram_formatter/utils.py:_normalize_links` (alte Fassung) —
  kannte nur Redirect-Entpacken; `ra=gemini`/`xstg=…` auf dem eigentlichen
  Ziel blieben erhalten.
- **Kategorie:** correctness
- **Begründung:** Jedes Zeitstempel-Zitat trug eine ~1 600-Zeichen-URL.
  Bei ~20 Zitaten sind das >30 000 Zeichen Ballast — die Nachricht wird
  in viele Teile zerteilt (Regular: 4 096-Limit) und ist im Rohtext
  unlesbar („der formatter wird dann unübersichtlich lang“).
- **Fix:** `clean_url()` (`utils.py:845`) entfernt nach dem Entpacken
  Tracking-Parameter (`_strip_tracking_params`, `utils.py:816`):
  `xstg`, `utm_*`, Klick-IDs (`fbclid`, `gclid`, `dclid`, `gbraid`,
  `wbraid`, `msclkid`, `mc_cid`, `mc_eid`, `igshid`) und `ra` nur bei
  Wert `gemini` (`utils.py:783-804`). Die Query wird roh an `&` zerlegt —
  Reihenfolge/Kodierung der übrigen Parameter bleiben byte-genau; ohne
  Treffer kein Re-Encoding. Getestet: `TestCleanUrl` (34 Fälle).

### [BLOCKER→behoben] Duplizierte Video-Zeile hinterließ Link-Müll

- **Fund:** `telegram_formatter/utils.py:_normalize_links` (alte Fassung)
  mit `_NESTED_LINK_RE`/`_WRAPPED_LINK_RE`.
- **Kategorie:** correctness
- **Begründung:** Die gemeldete Schlusszeile
  `[[https://youtube.com/shorts/…](https://www.google.com/search?q=…)](https://youtube.com/shorts/…](https://www.google.com/search?q=…))`
  wurde nur zur Hälfte erkannt; in der Nachricht stand sichtbar
  `…</a>(https://…](https://…))` (HTML) bzw. der entsprechende Anhang in
  Rich Markdown.
- **Fix:** Neue Artefakt-Muster `_GEMINI_DOUBLE_NESTED_RE` (`utils.py:885`,
  Zeitstempel-Zitate) und `_GEMINI_DUPLICATED_LINK_RE` (`utils.py:892`,
  duplizierte Zeile); die Glättung läuft zusammen mit den bestehenden
  Mustern bis zum Fixpunkt (Kappe `_LINK_CLEANUP_PASSES = 5` — jede
  Ersetzung entfernt mindestens eine Klammer-Ebene, also terminiert
  sie). Getestet: `TestCleanGeminiLinks`, `TestGeminiLinksInMessages`.

### [WARNING→behoben] `&amp;` zerstörte Parameter und Prosa

- **Fund:** `telegram_formatter/utils.py:markdown_to_html` Schritt 4
  (`_escape_html` nach `_normalize_links`) und Rich-Ziele allgemein.
- **Kategorie:** correctness
- **Begründung:** Gemini-Exporte liefern `?v=…&amp;t=7`. HTML-Pfad:
  `&amp;` wurde zu `&amp;amp;` escapet → der Parameter hieß `amp;t`,
  der Zeitstempel ging verloren; Rich-Pfad: `&amp;` blieb wörtlich im
  Ziel. In Prosa („Wirkungen &amp; Vorteile“) wurde „&amp;“ sichtbar
  zugestellt. Reproduktion vor Fix:
  `<a href="https://www.youtube.com/watch?v=abc&amp;amp;ra=gemini…">`.
- **Fix:** `_decode_amp()` (`_AMP_RE`, `utils.py:771`: `&amp;`, `&#38;`,
  `&#x26;`) läuft als erster Schritt in `_normalize_links()` — zu dem
  Zeitpunkt ist Code in beiden Konvertern bereits platzhaltergeschützt;
  `clean_gemini_links()` schützt Code selbst (`utils.py:970`).
  Nebeneffekt: `&amp;` als Zeilentrenner in exportierten Formeln wird
  wieder zum LaTeX-`&` (getestet).

### [INFO] `unwrap_redirect_url` bleibt unverändert (kein Verhaltens-Mix)

- **Fund:** `telegram_formatter/utils.py:unwrap_redirect_url`
- **Kategorie:** maintainability
- **Begründung:** Der Redirect-Teil (v2.7.0) ist sicherheitsreviewt
  (Schema-Guard, Host-`fullmatch` — s. `2026-09_REDIRECT-UNWRAP.md`).
  Die neue Parameter-Politik liegt davor/danach geschichtet
  (`_decode_amp` → Redirect → `clean_url`), statt die Funktion
  umzubauen. Intern nutzt `_normalize_links` jetzt durchgehend den einen
  Einstieg `clean_url` (Umbenennungen `_clean_redirect_label` →
  `_clean_link_label` (`utils.py:868`), `_unwrap_bare_url` →
  `_clean_bare_url` (`utils.py:913`) — keine doppelten Reinigungspfade).

### [INFO] Keine ReDoS-Verschlechterung durch die Fixpunkt-Schleife

- **Fund:** `telegram_formatter/utils.py:_normalize_links` (`utils.py:925`)
- **Kategorie:** security
- **Begründung:** Alle Muster arbeiten mit beschränkten
  Zeichenklassen (`[^\[\]]+`, `[^)\s]+`). Gemessen: 30 Gemini-Zitate
  (~88 000 Zeichen) in 0,05 s (HTML-Pfad gesamt); 50 000 eckige Klammern
  in `clean_gemini_links` in 0,003 s. Als Leistungsgrenzen getetestet
  (`TestGeminiCleanupPerformance`, Schwelle je 3 s wie bei der
  Fence-Regression in v2.13.0).

## Angenommen / abgewogen

- **`si`, `ref` und andere generische Parameter werden nicht entfernt.**
  Bei YouTube wäre `si` Share-Tracking, auf anderen Seiten kann derselbe
  Name funktional sein. Lieber ein Parameter zu viel als ein kaputter
  Link; per Negativtest festgeschrieben.
  **`ra` nur mit Wert `gemini`** — gleiche Begründung.
- **Zeitstempel bleiben Links** (`[00:05](…&t=5)`) statt nackter URLs
  oder gelöschter Zitate: „nur reine URLs“ heißt hier reine *Ziele* —
  konsistent mit der Behandlung solcher Zitate seit v2.7.0. Wiederholt
  der Linktext die URL, steht dort die reine URL (Konvention v2.7.0).
- **Unbekannte Deformationen werden nicht geraten repariert.** Verwaiste
  `](url)`-Fragmente fremder Herkunft werden nicht gelöscht (kein stiller
  Textverlust); geglättet werden nur belegte Artefakt-Formen.
- **`&amp;` wird auch in Prosa dekodiert.** Wer wörtlich über die Entität
  schreiben will, nutzt Code-Schreibweise (geschützt, getestet). Der
  Ist-Zustand zuvor („&amp;“ sichtbar in jeder Gemini-Überschrift) war
  der gemeldete Schaden.
- **`&amp` ohne Semikolon bleibt unberührt** — in URLs mehrdeutig
  (`?ampb=2` vs. Trenner); Gemini exportiert wohlgeformt mit `;`.

## Follow-ups

- [ ] **Älterer Klammer-Sturm-Befund (unabhängig von v2.15.0):**
      `"[" * 50_000` kostet `markdown_to_html`/`markdown_to_rich_markdown`
      ~10 s — **vor und nach dieser Änderung identisch** (gemessen:
      10,209 s auf 2.14.1 vs. 10,219 s auf 2.15.0; die neue Stufe selbst:
      0,003 s). Ursache ist das unbeschränkte `[^\]]+` in den späteren
      Link-Schritten der Konverter (`utils.py:1094` HTML,
      `utils.py:1225` Rich): ohne ein einziges `]` im Text scannt jede
      `[`-Position bis zum Dateiende → O(n²). Ein Fix muss Labels mit `[`
      im Text weiter erlauben und gehört — wie der Fence-Fix in v2.13.0 —
      in ein eigenes, getestetes Thema (z. B. Längenkappe für Labels).
