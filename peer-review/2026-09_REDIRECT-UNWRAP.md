# Review: Redirect-Unwrap & Link-Artefakte (v2.7.0)

- **Datum:** 2026-09-14
- **Reviewende:** Arena-Agent (Implementierungs-Review)
- **Gegenstand:** Branch `arena/01a09e55-telegram-formatter` — `telegram_formatter/utils.py` (Redirect-Regelwerk, `_normalize_links`), `tests/test_utils.py`, Doku (`docs/FORMATTING.md`, README, CHANGELOG, ARCHITECTURE)
- **Ergebnis:** ✔ freigegeben

## Kontext

LLM-Antworten (v. a. Such-KIs) zitieren externe Ziele häufig über
Redirect-/Tracking-Adressen wie
`https://www.google.com/search?q=<percent-kodiertes Ziel>` und liefern dazu
verschachtelte Link-Artefakte (`[[00:38]([url](url))]`). Bisher wurden diese
URLs 1:1 übernommen. v2.7.0 entpackt bekannte Redirect-Dienste auf ihre
Ziel-URL und glättet die Artefakte — auf beiden Konvertierungspfaden.

## Befunde

### [INFO] Schema-Guard verhindert Target-Injection

- **Fund:** `telegram_formatter/utils.py:_looks_like_http_url` / `_match_redirect_rule`
- **Kategorie:** security
- **Begründung:** Ein Redirect-Parameter könnte prinzipiell `javascript:`
  oder `data:` enthalten und würde sonst in `<a href="…">` landen. Das Ziel
  wird nur akzeptiert, wenn `urlsplit` Schema `http`/`https` und einen Host
  ergibt.
- **Fix:** implementiert und negativ getestet
  (`tests/test_utils.py::TestUnwrapRedirectUrl::test_non_redirects_stay_untouched`).

### [INFO] Host-Regeln sind fullmatch-verankert (kein Suffix-Phishing)

- **Fund:** `telegram_formatter/utils.py:_REDIRECT_RULES`
- **Kategorie:** security
- **Begründung:** Eine naive Endung `google.com` würde auch
  `evilgoogle.com` treffen. Die Regel-Hosts werden mit `fullmatch` geprüft
  (`(?:[a-z0-9-]+\.)*google\.[a-z]{2,}(?:\.[a-z]{2,})?`), d. h. nur echte
  Subdomains von `google.<tld>` greifen. Getestet mit `evilgoogle.com` und
  `notgoogle.de` (Negativfälle).
- **Fix:** implementiert + getestet.

### [INFO] Keine False-Positives bei normalen Suchanfragen

- **Fund:** `telegram_formatter/utils.py:_decode_param_value`
- **Kategorie:** correctness
- **Begründung:** `google.com/search?q=katze` darf nicht umgeschrieben
  werden. Die Regel zieht den Wert nur, wenn er nach Dekodierung selbst eine
  absolute http(s)-URL ist. Getestet (`q=hallo+welt` bleibt 1:1).
- **Fix:** —

### [INFO] Code und Formeln bleiben geschützt

- **Fund:** Aufrufstellen `markdown_to_html` (Schritt 3b) / `markdown_to_rich_markdown` (Schritt 2b)
- **Kategorie:** correctness
- **Begründung:** `_normalize_links` läuft nach dem Platzhalter-Schutz für
  Codeblöcke/Inline-Code/Formeln; URLs in Code werden nicht umgeschrieben
  (getestet für Inline-Code, Fence und Rich-Pfad). Der Aufruf im HTML-Pfad
  liegt vor dem Escaping, damit `&` im Query-String noch als Trenner lesbar
  ist.
- **Fix:** —

### [INFO] Rekursionstiefe begrenzt, idempotent

- **Fund:** `telegram_formatter/utils.py:unwrap_redirect_url`
- **Kategorie:** correctness
- **Begründung:** Verschachtelte Redirects (Facebook → Google → Ziel) werden
  rekursiv entpackt, hart begrenzt auf 5 Ebenen plus `seen`-Menge gegen
  Zyklen. `unwrap(unwrap(x)) == unwrap(x)` getestet.
- **Fix:** —

## Angenommen / abgewogen

- **Kurz-URLs (t.co, bit.ly, goo.gl) bleiben 1:1.** Offline ist das Ziel
  nicht bestimmbar; ein HTTP-Follow im Parser würde die I/O-Freiheit von
  `utils.py` (Schichtregel) verletzen. Dokumentiert in
  `docs/FORMATTING.md` §4.
- **Bare-URL-Regex schließt Klammern aus** (`…/Foo_(Bar)` wird im Fließtext
  vor der Klammer abgeschnitten). Gilt nur für die Entpack-Prüfung nicht
  erkannter Redirects (Match wird dann unverändert zurückgegeben) und deckt
  sich mit dem Autolink-Verhalten gängiger Renderer; der bekannte
  Klammer-Limit der Link-Regex ist als Ausbau-Kandidat in
  `docs/FORMATTING.md` §5 dokumentiert.
- **Regeltabelle statt generischem „irgendein url-Parameter“-Heuristik:**
  Eine generische Extraktion beliebiger `url=`/`q=`-Parameter würde zu
  Fehltreffern bei normalen Seiten führen. Die Tabelle ist bewusst explizit
  und erweiterbar.

## Follow-ups

- [ ] Spoiler `||x||` → `<tg-spoiler>` (docs/FORMATTING.md §5, Prio 1)
- [ ] Balancierte Klammer-Links für Wikipedia-artige URLs (docs/FORMATTING.md §5, Prio 5)
