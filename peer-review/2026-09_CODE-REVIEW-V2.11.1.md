# Review: Code-Peer-Review v2.11.1 (UTF-16-Maß, Fences, Fehlerpfade)

- **Datum:** 2026-09-16
- **Reviewende:** arena-agent (automated peer-review, Schicht: utils/sender/botkit/frontends/docs)
- **Gegenstand:** Gesamte Codebasis — `telegram_formatter/` (utils, sender, app, cli, botctl, botkit, static, templates), `tests/`, `docs/`, READMEs — Branch `arena/01a0aa1b-telegram-formatter`, Basis `3b0ebea` (v2.11.0)
- **Ergebnis:** ✔ freigegeben nach Fixes (alle 15 Befunde behoben, 497 Tests grün, Ruff/Bandit sauber)

## Kontext

Vollständiges Peer-Review der Codebasis nach dem v2.11.0-Splitting-Release (64000-Zeichen-Support, format-erhaltendes Splitting). Auftrag: alle gefundenen Probleme beheben, Änderungen extensiv testen, READMEs/API-Docs/Kommentare/Versionierung/Changelog nachziehen, sinnvoll committen und per PR einreichen. Ergebnis ist das Patch-Release **v2.11.1** — keine API-Änderung, keine Migration nötig. Für ASCII-/BMP-Texte bleiben die Chunks byte-identisch zu v2.11.0; nur Emoji-reiche, Fence-defekte oder tief verschachtelte Eingaben teilen sich jetzt (korrekt) anders auf.

## Befunde

### [BLOCKER] Splitter misst in Codepoints statt UTF-16-Units — Telegram weist Emoji-Chunks ab

- **Fund:** `telegram_formatter/utils.py:1468` (`_group`), `:1511` (`chunk_text`), `:1355` (`_safe_chunk`), `:1309` (`_split_guarded_unit`) — überall `len()`
- **Kategorie:** correctness
- **Begründung:** Die Telegram-Limits 4096/32768 zählen **UTF-16-Code-Units** (wie die Entity-Offsets) — Emoji und andere Astral-Zeichen kosten zwei. Der Splitter maß in Python-Codepoints, sodass Emoji-reiche Eingaben als „passend“ gechunkt und von Telegram mit 400 abgewiesen wurden. Reproduktion: `build_messages("🚀" * 4000, chat_id=-1)` ergab zwei Chunks, ersterer mit 7744 UTF-16-Units (Limit 4096). Gleicher Effekt auf dem Rich-Pfad.
- **Fix:** Neues Maß `utils._telegram_len()` (`len(text.encode("utf-16-le")) // 2`) in allen Limit-Prüfungen; harte Schnitte laufen über `utils._hard_split()` (O(n), misst pro Zeichen 1/2 Units, zerreißt nie einen Codepoint; Def reports in `docs/FORMATTING.md`, `docs/ARCHITECTURE.md` §6). Tests: `test_regular_emoji_chunks_respect_utf16_limit`, `test_rich_emoji_chunks_respect_utf16_limit`, `test_hard_split_respects_utf16_budget_and_is_lossless`.

### [BLOCKER] Ungeschlossener Code-Fence fiel aus dem Atomic-Schutz und sprengte Limits

- **Fund:** `telegram_formatter/utils.py:1242` (`_atomic_ranges`), `:280` (`iter_math_spans`), `:1309` (`_split_guarded_unit`)
- **Kategorie:** correctness
- **Begründung:** Ein ```-Block ohne Closing-Fence wurde nirgends als atomar markiert: `$…$` darin galt fälschlich als Formel (falscher Rich-Pfad / Formel-Rendering im Code), und überlange Reste fielen in den generischen Wort-Split, der Chunks über dem Limit erzeugen konnte.
- **Fix:** Wie in GFM läuft ein ungeschlossener Fence jetzt bis Dokumentende (`_atomic_ranges`, `iter_math_spans(skip_code_fences=True)`); das Fence-Splitting ist in den gemeinsamen Kern `_split_fence_block()` (`utils.py:1284`) gezogen, den beide Zweige von `_split_guarded_unit` nutzen — jeder Rest wird in eigenständige, sauber geschlossene Blöcke mit erhaltener Sprache geteilt. Tests: `test_unclosed_fence_is_atomic_to_eof`, `test_unclosed_oversized_fence_splits_into_closed_blocks`.

### [WARNING] `sender` baute mit eigenem `api_base` URLs ohne Token-Segment

- **Fund:** `telegram_formatter/sender.py:154`
- **Kategorie:** correctness
- **Begründung:** Das `/bot<token>/`-Segment wurde nur für die offizielle API eingebaut; mit gesetztem `api_base` (lokaler Bot-API-Server, Tests) fehlte es und der Aufruf lief ins Leere — inkonsistent zu `botkit/telegram_api.py:51`, das immer korrekt baut.
- **Fix:** URL immer als `rstrip(api_base)/bot<token>/<method>` (Docstring dokumentiert das Verhalten). Test: `test_custom_api_base_keeps_bot_token_segment`.

### [WARNING] BK005 entging `LOGGER` — die übliche Logger-Konvention

- **Fund:** `telegram_formatter/botkit/review.py:497` (`root.lower()` fehlte)
- **Kategorie:** security
- **Begründung:** Die Regel „keine Inhalte im Log“ verglich Logger-Namen case-sensitiv — `LOGGER.info(f"…{message}")` wurde nicht erkannt, obwohl genau das die idiomatische Konvention ist. Ein Bot mit Inhalts-Logging hätte das Review-Gate passiert.
- **Fix:** Root-Vergleich case-insensitiv (`root.lower() in {"logging", "logger", …}`). Test: `test_log_content_rule_ignores_logger_name_case`.

### [WARNING] `SessionManager.get()` ließ abgelaufene Sessions ungeöffnet zurück

- **Fund:** `telegram_formatter/botkit/session.py:426` (`get`)
- **Kategorie:** privacy
- **Begründung:** Der Eintrag wurde aus dem Register entfernt, `close()` aber nie aufgerufen — die Token-Referenz lebte in der verwaisten Session weiter, ohne `closed`-Markierung. Wer die Session-Referenz hielt, konnte den geheimen Token länger als die TTL nutzen.
- **Fix:** Abgelaufene Sessions werden unter Lock entfernt und danach geschlossen (`closed=True`, Token fällt). Test: `test_get_closes_expired_session_and_drops_token`.

### [WARNING] Nicht-numerische getMe-`id` warf rohen `ValueError`

- **Fund:** `telegram_formatter/botkit/registry.py:200`
- **Kategorie:** correctness
- **Begründung:** `int(result.get("id", -1))` auf fremder API-Antwort ohne `try` — eine kaputte/proxyierte getMe-Antwort beendete `register()` mit ungefangem `ValueError` statt mit der dokumentierten `RegistrationError`.
- **Fix:** `ValueError` → `RegistrationError("… 'id' ist keine Zahl.")`; die geparste ID wird als `reported_id` wiederverwendet. Test: `test_register_rejects_non_numeric_getme_id`.

### [WARNING] Review-Gate und Audit-Trail warfen rohe `OSError`/`JSONDecodeError`/`KeyError`

- **Fund:** `telegram_formatter/botkit/review.py:986` (`verify`), `:793` (`save`), `:815` (`load`)
- **Kategorie:** maintainability
- **Begründung:** Fehlende Code-Datei (`FileNotFoundError`), nicht schreibbarer Trail (`OSError`), korrupter Trail (`JSONDecodeError`, `KeyError` bei fehlenden Feldern, `TypeError` bei Nicht-Liste) schlugen als rohe Tracebacks bis zur CLI durch — inkonsistent zum sonst typisierten Fehlervertrag (`ReviewGateError`/`ReviewError`).
- **Fix:** `verify` meldet unlesbaren Code als `ReviewGateError`; `save`/`load` melden Trail-Probleme als `ReviewError` („Audit-Trail … ist beschädigt (…)“). Tests: `test_verify_missing_file_raises_gate_error`, `test_load_corrupt_ledger_raises_review_error`, `test_corrupt_ledger_is_clean_error` (botctl, Exit 1).

### [WARNING] Telegram-API-Antwort ohne JSON-Objekt warf rohen `AttributeError`

- **Fund:** `telegram_formatter/botkit/telegram_api.py:66`
- **Kategorie:** robustness
- **Begründung:** `body.get(...)` auf dem `response.json()`-Ergebnis ohne Typprüfung — eine Proxy-Fehlerseite oder kaputte Antwort (Liste/String) beendete `get_me`/`send_message` mit `AttributeError` statt `TelegramAPIError`.
- **Fix:** `isinstance(body, dict)`-Guard → `TelegramAPIError("Ungültige JSON-Antwort …")`. Tests in neuer `tests/test_telegram_api.py` (direkte Netzwerkschicht-Tests inkl. token-freier Fehlerklassifizierung).

### [WARNING] `botctl send --bot-source X --local-trust` widersprach sich

- **Fund:** `telegram_formatter/botctl.py:280` (`cmd_send`)
- **Kategorie:** correctness
- **Begründung:** Die Kombination verlangt (`--bot-source` → Gate an) und entfernt (`--local-trust` → Gate aus) das Review-Gate zugleich und scheiterte erst kryptisch beim Session-Öffnen.
- **Fix:** Explizite Abweisung mit Begründung (Exit 2). Test: `test_send_rejects_bot_source_with_local_trust`.

### [WARNING] Unlesbare `botctl`/`cli`-Eingaben: Traceback statt Fehler, Session-Leak

- **Fund:** `telegram_formatter/botctl.py:280` (`cmd_send`), `telegram_formatter/cli.py:59` (`main`)
- **Kategorie:** robustness
- **Begründung:** `botctl send --file fehlt.md` öffnete erst die Session (Registry-Eintrag, Review-Gate) und las die Datei danach — bei `OSError` Traceback plus verwaiste Session. `cli.py` ließ `OSError`/`UnicodeDecodeError` ebenfalls roh durch — als einziges Frontend zudem komplett ungetestet.
- **Fix:** `botctl` liest die Eingabe *vor* dem Session-Öffnen und meldet „Eingabe nicht lesbar“ (Exit 2); `cli` meldet `FEHLER: Eingabe nicht lesbar (…)` (Exit 2); `botctl.main` (`botctl.py:463`) fängt zusätzlich `ReviewError|RegistrationError|SessionError|TokenError` als Sicherheitsnetz (Exit 1). Tests: `test_send_missing_file_is_clean_input_error`, neue `tests/test_cli.py` (Dry-Run Datei/STDIN, Leer-Eingabe, Exit 1/2).

### [INFO] Rich-Carry unbegrenzt — tiefe `<u>`-Verschachtelung sprengte die Balancing-Reserve

- **Fund:** `telegram_formatter/utils.py:1162` (`_rebalance_markdown_chunks`)
- **Kategorie:** correctness
- **Begründung:** Der HTML-Pfad deckelt den Übertrag offener Tags (`_HTML_MAX_CARRY`), der Markdown-Pfad trug beliebig tiefe `<u>`-Stapel nach und konnte die 64-Zeichen-Reserve je Chunk sprengen.
- **Fix:** `_RICH_MAX_CARRY = 4` (`utils.py:1047`); die Rest-Analyse startet am gekappten Stapel, äußere Ebenen entfallen in Folge-Chunks per Design (dokumentiert). Test: `test_rich_carry_depth_is_capped_and_chunks_stay_valid` (öffnet ≤ schließt, UTF-16-konform).

### [INFO] `_valid_chat_id` prüfte die Chat-ID-Regel redundant doppelt

- **Fund:** `telegram_formatter/app.py:448`
- **Kategorie:** maintainability
- **Begründung:** `app.py` wandte das Chat-ID-Pattern eigenhändig an, obwohl `botkit.registry.validate_chat_id` dieselbe Regel hält — zwei Pflegestellen für eine Regel (gegen die DRY-Vorgabe des Auftrags: „keine redundanten Redundanzen“).
- **Fix:** `_valid_chat_id` delegiert an `validate_chat_id` (Verhalten identisch, via `test_app.py` abgedeckt). Nebenbei: Audit-O-4-Digest-Encoding `ascii` → `utf-8` (`app.py:616`) — Session-Secret mit Nicht-ASCII wäre sonst abgestürzt.

### [INFO] `chunk_text` baute das Absatz-Saldo je Absatz neu auf (O(n²))

- **Fund:** `telegram_formatter/utils.py:1511` (`chunk_text`)
- **Kategorie:** performance
- **Begründung:** `len("\n\n".join(buf))` je Absatz summiert die Pufferlänge immer wieder neu — bei 64000-Zeichen-Eingaben mit vielen Absätzen quadratisch.
- **Fix:** Inkrementelle Saldoführung mit `_telegram_len()` (Verhalten identisch, via `test_split_64000.py` abgedeckt).

### [INFO] `app.js`-Fallback-Endpunkte waren relativ, `byob.js` nutzt absolute

- **Fund:** `telegram_formatter/static/js/app.js:61`
- **Kategorie:** correctness
- **Begründung:** `document.body.dataset.convertUrl || "api/convert"` (relativ) bricht unter Nicht-Root-Pfaden; `byob.js` nutzt absolute Pfade.
- **Fix:** Fallbacks absolut (`/api/convert`, `/api/send`), konsistent mit `byob.js`. Abgedeckt via `tests/frontend/jsdom_spec.cjs` (jsdom-Smoke).

### [INFO] CLI und API-Netzwerkschicht hatten keine eigenen Tests

- **Fund:** `tests/` (fehlende Dateien)
- **Kategorie:** tests
- **Begründung:** `cli.py` war das einzige Frontend ohne Tests; `botkit/telegram_api.py` war nur indirekt über gemockte `verify`-Funktionen abgedeckt — die Befunde 8 und 10 hatten deshalb keine Regressionssicherung.
- **Fix:** Neue `tests/test_cli.py` (6 Tests) und `tests/test_telegram_api.py` (3 Tests); insgesamt 24 neue Tests (472 → 496 passed + 1 jsdom-Smoke).

## Angenommen / abgewogen

- `_hard_split("")` liefert `[""]` (defensiver Fallback); kein Aufrufer übergibt je leere Strings — dokumentiert, kein Dead Code.
- Äußere `<u>`-Ebenen entfallen in Folge-Chunks jenseits Tiefe 4: bewusster Trade-off (Reserve-Schutz vor Schönheit bei pathologischer Eingabe), im Code dokumentiert.
- `botctl verify` prüft weiterhin optional gegen die Registry (`check_registry`, Netz): kein Review-Gegenstand, Verhalten unverändert.
- Frontend-`fetch`-Fehlerpfade (`app.js`/`byob.js`) wurden nur per jsdom-Smoke geprüft, nicht Zeile für Zeile — ausreichend für Patch-Umfang.

## Follow-ups

- [ ] jsdom-Smoke (`npm install` + `tests/test_jsdom_smoke.py`) in CI verdrahten — derzeit nur lokal aktivierbar (hier verifiziert: passed).
- [ ] Rich-Pfad: Telegram zählt zusätzlich max. 500 Blöcke pro Nachricht — prüfen, ob überlange Eingaben mit hunderten Absätzen einen Block-Zähler brauchen (separater Review, kein v2.11.1-Gegenstand).
- [ ] `BotToken`-Längenobergrenze: prüfen, ob überlange Token-Secrets früh abgewiesen werden sollten (derzeit nur Formatprüfung).

## Verifikation

- `pytest tests/ -q` → **497 passed** (496 Unit/Integration + 1 jsdom-Smoke nach `npm install`), 0 skipped, 0 failed
- `ruff check app.py telegram_formatter examples tests` → sauber
- `bandit -c pyproject.toml -r telegram_formatter examples/own_bot` → keine Befunde
- Reproduktion Block 1: `build_messages("🚀" * 4000, chat_id=-1)` → 3 Chunks à 3872/3872/256 UTF-16-Units (vorher 2 Chunks, erster à 7744 Units über dem 4096-Limit)
- Reproduktion Block 2: ungeschlossener Fence → bis EOF atomar, Split in geschlossene Blöcke mit ` ```python `-Kopf
- ASCII-/BMP-Regression: Chunks kurzer Texte byte-identisch zu v2.11.0 (Suite grün ohne Snapshot-Anpassung)
