# UI-Design-System „tf“ (Web-Oberfläche)

> Stand: Redesign 2026-09. Diese Datei beschreibt **Aussehen und Verhalten**
> der Flask-Weboberfläche (`telegram_formatter/app.py` + `static/` +
> `templates/`). Die Konvertierungslogik selbst ist davon unberührt und in
> [ARCHITECTURE.md](ARCHITECTURE.md) dokumentiert.

## 0. Warum dieses Kapitel existiert (Design-Postmortem)

Die Oberfläche lag lange auf dem **Tailwind Play-CDN** plus Font Awesome von
cdnjs. Die gehärtete CSP (Audit H-5) erlaubte zwar das CDN-*Skript*, nicht
aber die vom Skript zur Laufzeit injizierten **Inline-`<style>`-Regeln**
(`style-src` ohne `'unsafe-inline'`). Ergebnis: Das Skript lud, die Regeln
wurden blockiert — die Seite fiel auf ungestylten Rohtext zurück und war
zusätzlich von der Verfügbarkeit eines Drittanbieters abhängig.

Konsequenz des Redesigns:

* **Keine CDN-Assets mehr.** HTML, CSS, JS und Icons (Inline-SVG) sind
  vollständig selbst-gehostet; die CSP ist jetzt strikt `'self'` für
  alles — es gibt nichts mehr, was blockiert werden *könnte*.
* **Ein eigenes, schlichtes Design-System** (Tokens + Komponenten) statt
  Utility-Klassen-Kosmos; Themes sind damit eine Datendeklaration, kein
  Markup-Umbau.
* **Vertragstests** (siehe Abschnitt 6) erzwingen die Regeln dauerhaft.

## 1. Dateiorganisation

```text
telegram_formatter/
├── templates/
│   └── index.html          einziges Template; rein strukturell (Semantik,
│                           Content, Hooks) — keine Farben, kein Inline-Style
└── static/
    ├── favicon.svg         selbst-gehostetes Icon (kein data:-Trick nötig)
    ├── css/                Design-System, vier Schichten — LADREIHENFOLGE!
    │   ├── tokens.css      Ebene 1: Design-Tokens & Theme-Definitionen
    │   ├── base.css        Ebene 2: Reset, Typografie, Elemente (farbfrei)
    │   ├── layout.css      Ebene 3: Seitenstruktur, Grids, Breakpoints (farbfrei)
    │   └── components.css  Ebene 4: Bausteine (.tf-*), nur mit var(--token)
    └── js/
        ├── theme.js        Theme-Switcher (läuft synchron im <head>)
        ├── app.js          Editor-Funktionen (Vorschau, /api/convert, Sende-Bestätigung, Senden)
        └── byob.js         BYOB-Session-UI (v2.2.0): Session-Start/Status/
                            Chat-Erkennung; stellt window.tfByob bereit, an
                            das app.js den Senden-Button delegiert
```

Laderegeln (durch `tests/test_frontend.py::test_css_load_order_is_layers`
abgesichert):

1. `theme.js` steht als einziges Skript **ohne `defer` im `<head>`** — es
   muss `data-theme` setzen, *bevor* der erste Frame gemalt wird
   (Thema-Flashing vermeiden).
2. CSS in der Reihenfolge tokens → base → layout → components, damit
   Spezifität und Kaskade berechenbar bleiben.
3. `app.js` und `byob.js` bleiben mit `defer` am `</body>`-Ende
   (Reihenfolge: `app.js` vor `byob.js` — `byob.js` setzt beim Laden das
   Senden-Label, `app.js` liest `window.tfByob` nur zur Sendezeit).

## 2. CSS-Architektur

### 2.1 Token-System

Alle visuellen Entscheidungen (Farben, Radien, Schatten, Fonts) sind CSS
Custom Properties in `tokens.css`. Komponenten referenzieren **ausschließlich**
`var(--token)` — harte Farbwerte sind nur in drei dokumentierten Ausnahmen
erlaubt (Theme-Swatches, Buy-me-a-coffee-Brandgold, neutrale
`rgba(127,127,127,α)`-Overlays). Der Test
`test_no_color_hardcoding_outside_tokens` erzwingt das.

Leitender Token-Satz (Auszug; die vollständige Liste ist der `:root`-Block —
`test_every_theme_block_defines_full_token_set` vergleicht jede Theme-Definition
dagegen):

| Token | Verwendung |
|---|---|
| `--bg`, `--bg-image` | Seitenhintergrund (colorful setzt hier den Verlauf) |
| `--surface`, `--surface-2` | Karten / inaktive Flächen (Felder, Tabellenzeilen-Alternativen) |
| `--border`, `--border-strong` | Hairlines / Fokus-Rahmen |
| `--text`, `--text-muted`, `--text-faint`, `--heading` | Textstufen = visuelle Hierarchie |
| `--accent`, `--accent-hover`, `--accent-soft`, `--on-accent`, `--accent-2` | Aktionsfarbe + Textfarbe *auf* Akzent |
| `--btn-grad` | optionales Button-Gradient (nur `colorful` setzt es; sonst `none`) |
| `--bubble`, `--bubble-text` | Telegram-Sprechblase der Live-Vorschau |
| `--code-bg`, `--code-text` | Payload-Terminal |
| `--note-bg/-border/-text` | Disclaimer-/Noscript-Hinweisbox |
| `--ok`, `--warn`, `--err` | Statusfarben (Badge, Sendestatus) |
| `--shadow`, `--shadow-lg`, `--ring` | Tiefe + Fokus-Ring |
| `--radius`, `--radius-lg` | Eckenradien (Themes dürfen Form *und* Farbe ändern!) |
| `--header-*`, `--footer-*` | Kopfbereich/ Fußzeile (eigene Flächen, da sticky/invertiert) |

Theme-unabhängige Konstanten (`--font-sans`, `--font-mono`, `--container`,
`--transition`) stehen nur im `:root`; sie in Theme-Blöcken zu duplizieren ist
kein Fehler, aber unnötig (Test whitelistet sie).

### 2.2 Namenskonvention (modifiziertes BEM)

```css
.tf-card            /* Block: eigenständiger Baustein                     */
.tf-card__title     /* Element: Unterteil, genau ein „__“, nie global frei  */
.tf-btn--primary    /* Modifikator: Variante eines Blocks                  */
.is-active, .is-over, .is-ok, .is-error   /* Zustand: wird NUR von JS gesetzt */
```

Zusätzlich gibt es einen historischen Hook: **`.tg-bubble`** (Live-Vorschau
als Telegram-Nachricht) heißt weiter so, weil er seit v1.x Bestand hat und in
Payload-/Vorschau-Tests als Anker dient. Neue Bausteine bekommen das `tf-`-Prefix.

### 2.3 Schichtregeln

* `base.css` und `layout.css` sind **strikt farbfrei** (nur `var()`-Referenzen
  aus `tokens.css` bzw. Geometrie).
* Das Template enthält **keine** `style="…"`-Attribute und **keine**
  `<style>`-Blöcke — die CSP (`script-src/style-src 'self'`) ließe beides
  ohnehin nicht zu; `test_assets_are_self_hosted_no_inline_script` (in
  `tests/test_app.py`) prüft die Seite, `test_rendered_page_is_well_formed`
  die Nestschachtelung.
* Icons sind Inline-SVG mit `class="tf-icon"`; sie erben `currentColor` und
  damit automatisch jedes Theme.

## 3. Themes

Fünf wählbare Modi, davon **vier gestaltete Themes** + „Auto“:

| Wahl | `data-theme` | Charakter | Markenzeichen |
|---|---|---|---|
| **Auto** | `auto` | folgt dem Betriebssystem | nutzt `light`-Tokens bei Tag, Media-Fallback bei Nacht |
| **Light** | `light` | ruhiges Tageslicht | weiße Karten auf `#eef2f7`, Telegram-Blaue `#0b66c2` |
| **Dark** | `dark` | Telegram-Desktop-Nacht | tiefes Blaugrau, chatblaue Sprechblase (`#215781`) |
| **Colorful** | `colorful` | Vibrant/Nachtverlauf | Indigo→Petrol-Verlauf auf `--bg-image`, Glas-Karten, Lila→Cyan-Buttons (`--btn-grad`), rundeste Radien |
| **Minimal** | `minimal` | redaktionell-monochrom | keine Schatten, hairline-Rahmen, kantige Radien, Akzent = Tiefschwarz |

Aktive Werte (Kontrast ≥ 4:1 auf Flächen; grobe Heuristik, abgesichert durch
`test_accent_and_surface_contrast_pairs`): s. `tokens.css` — dort ist jedes
Theme **ein** zusammenhängender Block, kommentiert nach Stimmungs-Lage.

### 3.1 So fügt man ein Theme hinzu (Rezept)

1. **Tokens definieren.** In `tokens.css` einen neuen Block anlegen, z. B.

   ```css
   /* Theme „Sepia“: warmes Papier, weiche Kontraste */
   [data-theme="sepia"] {
       color-scheme: light;
       --bg: #f4ecd8;
       /* … JEDEN Token aus dem :root-Block definieren (außer den 4 globalen) … */
   }
   ```

   Der Vollständigkeits-Test listet fehlende Tokens namentlich auf —
   er ist das Formular für diese Aufgabe.

2. **Auswahl im Switcher.** In `templates/index.html` einen Button ergänzen:

   ```html
   <button type="button" class="tf-theme-btn" data-theme-choice="sepia"
           aria-pressed="false" title="Sepia — warmes Papier">
       <svg …>…</svg>
       <span class="tf-theme-btn__swatch" aria-hidden="true"></span>
       <span class="tf-theme-btn__label">Sepia</span>
   </button>
   ```

3. **Swatch-Farbe** für den Kleckspunkt unter `components.css` ergänzen
   (`[data-theme-choice="sepia"] .tf-theme-btn__swatch { background: … }`).

4. **JS-Whitelists.** In `static/js/theme.js` den String `"sepia"` in
   `CHOICES` und `META_COLORS` eintragen (Meta-Farbe = typischer
   Seitenhintergrund des Themes, färbt Browser-Oberfläche/Statusbar).

5. **Tests aktualisieren:** `tests/test_frontend.py::test_theme_switcher_offers_all_choices`
   erwartet die exakte Auswahl-Menge; `test_every_theme_block_defines_full_token_set`
   prüft Vollständigkeit automatisch (Liste `("light","dark","colorful","minimal")`
   erweitern).

6. **Doku:** Tabelle in diesem Dokument und CHANGELOG-Eintrag nicht vergessen.

**Nichts anderes muss geändert werden** — Komponenten und Layout bleiben
unberührt, weil sie nur Tokens kennen. Genau das ist der Zweck der Architektur.

### 3.2 Wie das Theme-Switching funktioniert

Ablauf (Code: `static/js/theme.js` — Bewusst **~120 Zeilen ohne jede Farbe
außer `META_COLORS`**; Färbung ist ausschließlich CSS):

1. **Boot (synchron im `<head>`):** `storedChoice()` liest `localStorage["tf-theme"]`
   (Schlüssel constants: Werte `auto|light|dark|colorful|minimal`; Default `auto`,
   unbekanntes → `auto`). `apply()` schreibt das Ergebnis als Attribut
   `<html data-theme="…">` — noch vor dem ersten Paint. Kein `defer`!
2. **CSS übernimmt:** Der passende Block in `tokens.css` färbt die gesamte
   Seite um. Für `auto` entscheidet allein
   `@media (prefers-color-scheme: dark)` über den Dark-Fallback-Block — der
   Browser wechselt also **live mit**, wenn das Betriebssystem umschaltet,
   ohne dass JS etwas tun muss.
3. **Wechsel per Klick:** Delegierter `click`-Listener auf `#themeSwitcher`
   (überlebt dynamisches Markup, kostet einen Listener). Klick →
   `localStorage.setItem` → `apply()` → `setPressed()` aktualisiert
   `aria-pressed` und `.is-active`.
4. **Robustheit:** Kein `localStorage` (Privacy-Modus, `file://`) → `try/catch`,
   Umschalten funktioniert trotzdem, nur ohne Persistenz. `matchMedia` fehlt
   (uralte Browser) → es wird lediglich die `meta[name="theme-color"]`-Aktualisierung
   übersprungen, die Seite selbst ist davon unabhängig.
5. **Kein JS, kein Flash:** Ohne JavaScript bleibt das Attribut weg; dann greift
   `:root:not([data-theme])` im Media-Block — dunkle Systeme sehen dunkel,
   helle sehen `light`. Das Template trägt deshalb **kein** `data-theme`
   (`test_html_carries_no_data_theme_and_lang_is_set`).

Zustandsklassen, die JS an Stellschrauben klebt (nur diese drei, alle in
`components.css` definiert): `.is-active` (Switcher), `.is-ok`/`.is-error`
(Sendestatus), `.is-over` (Zeichenzähler > 4096).

## 4. Visuelle Hierarchie

1. **Sticky-Header** = permanente Orientierung: Marke (links), Bot-Status
   (Badge mit Statuspunkt), Theme-Switcher, Unterstützen-Button (rechts).
2. **Top-Warnung** (`.tf-top-warning`, nur wenn ein geteilter Bot
   konfiguriert ist): rotes Vollbreite-Band direkt unter dem Header — die
   „geteilte Bot ist öffentlich“-Warnung steht bewusst VOR Hero und Editor,
   weil der geteilte Bot der Default-Versandweg ist (v2.3.0).
3. **Hero-Zeile** erklärt in einem Satz + Tagline, was das Tool tut — vor
   jedem Eingriff.
3. **Zwei-Spalten-Arbeitsbereich** (`≥ 64rem`): links Eingabe (Erzeugen),
   rechts Ausgabe (Prüfen) — Lesereihenfolge = Arbeitsfluss. Die Ausgabespalte
   ist auf dem Desktop sticky (`top: 4.5rem`), die Vorschau sitzt in einer
   angedeuteten Chat-Umgebung (`.tf-preview-frame` mit gestricheltem Rand +
   Sprechblase `.tg-bubble` mit Telegram-typischer abgeschrägter Ecke).
4. **Unter dem Editor:** Zeichenzähler + Sendestatus (`role="status"`,
   `aria-live="polite"`) + Button-Zeile (primär/sekundär) +
   Disclaimer-Box als `aside` mit Warn Tokens — Pflichtinfo, aber bewusst
   in der niedrigsten Textstufe der Editor-Karte.
5. **Payloads** als dunkles Terminal (`--code-bg`) — visueller Bruch, der
   signalisiert: „rohe API-Daten, hier nichts ändern“.
6. **Sende-Bestätigung** (`.tf-modal`, v2.3.0): Modal über neutralem
   Backdrop, das vor jedem Versand Absender-Bot, Ziel und Vorschau zeigt —
   bewusst als Sperre zwischen „Senden klicken“ und „wirklich senden“.
7. **Howto & FAQ** darunter (scrollend nachrangig), inkl.
   Privatsphäre-Sektion (`.tf-privacy*`) zwischen BYOB und Howto; Footer mit
   Version/Lizenz/Kurz-Disclaimer.

## 5. Responsive-Strategie

* **Mobile zuerst**: Basis = einspaltig und kompakt für Telefone. Breakpoints als
  `min-width` (40/48/64/72 rem): 48 → Tabletablen (Footer dreispaltig,
  Hero-Luft), 64 → Desktop-Zweispalten + sticky Ausgabe, 72 → Fünfer-Schritt-Kette.
* Header `flex-wrap` — auf schmalen Screens brechen Status/Switcher/Buttons
  sauber in eine zweite Zeile um; Switcher zeigt < 640 px nur Farbkleckse,
  ab 640 px zusätzlich Labels (JS setzt `.is-rich` via `matchMedia`).
* `textarea` mit `resize: vertical`, `min-height`; Payloads scrollen intern
  (`overflow:auto`, `max-height`) statt die Seite aufzublasen.
* `100svh` mit `100vh`-Fallback (Mobile-Adressleisten),
  `-webkit-text-size-adjust: 100%`, `font-variant-numeric: tabular-nums` für
  den Zähler, `text-wrap: balance` für Überschriften (ignoriert von älteren
  Engines — reine Verschönerung).

## 6. Teststrategie & Browserkompatibilität

| Ebene | Ort | prüft |
|---|---|---|
| Struktur-Verträge | `tests/test_frontend.py` (pytest, ohne Browser) | Token-Vollständigkeit je Theme, var()-Abdeckung, Asset-Existenz/-Orphanings, JS↔HTML-ID-Verträge, Swatch-Whitelist, mobile-first, `min-width` only, HTML-Wellformedness, Kontrast-Heuristik, Feature-Baseline |
| Funktionale DOM-Tests | `tests/frontend/jsdom_spec.cjs` via `tests/test_jsdom_smoke.py` | echtes theme.js/app.js/byob.js-Verhalten: Boot aus localStorage, Switcher-Klicks + Persistenz, Markdown-Vorschau, Debounce + ein POST pro Tipppause, Senden-Bestätigung (Dialog-Inhalte, Abbrechen, Escape, Bestätigen), Erfolg/Fehler (429-Merge), Reset sowie kompletter BYOB-Durchlauf (Session öffnen/senden/beenden, Chat-Chips, 410-Reset — jeweils durch den Bestätigungsdialog); Skippt sauber ohne Node/jsdom (`npm install` — jsdom ist als Dev-Dependency in `package.json` gepinnt) |
| API/Security | `tests/test_app.py` | CSP strikt `'self'`, keine Inline-Skripte/Styles, alle Formatter-Endpunkte unverändert |
| Syntax-Check (manuell/local) | `css-tree` + `node --check` | Parse-Fehlerfreiheit von CSS/JS (in CI durch pytest-Strukturtests abgedeckt) |
| Menschlich | Dev-Server (`flask --app telegram_formatter.app run` + Browser) | reales Rendering; Browser-Matrix s. unten |

**Browser-Matrix:** bewusst nur CSS/JS eingesetzt, das in Chromium-,
Firefox- und Safari-Stand-Versionen der letzten Jahre ohne Fallback funktioniert:
Grid/Flexbox, Custom Properties, `clamp()`, `minmax()`, `@supports`-Guard für
`backdrop-filter`, `:focus-visible`. Explizit verboten (Test
`test_browser_baseline_features_only`): `:has()`, `color-mix()`, `oklch()/lab()`,
`light-dark()`, `@container`. `prefers-reduced-motion` und `color-scheme`
werden respektiert; Touch-Ziele ≥ ~40 px.

## 7. Anleitung: Das Design erweitern

* **Neue Komponente?** Block in `components.css` anlegen (`tf-…`),
  nur Tokens benutzen, Markup ins Template, Klasse-Liste lebt automatisch
  durch `test_no_ghost_tf_classes`.
* **Neue Sektion im Template?** Als `section.tf-card.tf-section` in
  `main.tf-container` einreihen; Anker-IDs (`howto`, `faq`, …) sind Tests
  und Skip-Link-Logik heilig.
* **Farbe/Radius/Schatten anders?** Nicht die Komponente anfassen — den
  Token im betroffenen Theme-Block ändern. Wenn ein Token *pro Theme gleich*
  bleiben soll, gehört er zu den globalen Konstanten in `:root`
  (`GLOBAL_TOKENS` in `tests/test_frontend.py`).
* **JS erweitern?** `app.js`/`byob.js` kennen nur IDs (`getElementById`) und
  die Zustandsklassen; neue IDs in
  `tests/test_frontend.py::test_js_ids_exist_in_template` spiegeln (die
  Mindestmenge ist dort hart hinterlegt). Fetch-Aufrufe laufen ausschließlich
  über `data-convert-url`/`data-send-url`/`data-byob-base` vom `<body>` —
  Endpunkte nie hartkodieren.
* **Keine neuen Netze:** Kein CDN, keine Fonts, keine Analytics — die Seite
  muss in einer Flugzeugtoilette funktionieren. (CSP würde es auch blockieren.)

## 8. BYOB-Komponenten (v2.2.0)

Die Sektion „Versandweg: geteilter Bot oder eigener Bot (BYOB)?“ nutzt
diese Bausteine (alle in `components.css`, nur mit `var(--token)`):

| Klasse | Zweck |
|---|---|
| `.tf-note--danger` | rote Warnbox für den geteilten Bot (neue Tokens `--danger-bg/-border/-text` in **jedem** Theme-Block + Auto-Fallback) |
| `.tf-byob__lead` | Einleitungstext des BYOB-Panels |
| `.tf-send-path` | Hinweis am Senden-Button; `.is-private` färbt grün, wenn die eigene Session aktiv ist |
| `.tf-form`, `.tf-field`, `.tf-field__hint`, `.tf-input`, `.tf-check`, `.tf-form-msg` | Formular (Passwort-Feld ohne Autocomplete, Consent-Checkbox, Fehler-/Erfolgsmeldung mit `.is-error`/`.is-ok`) |
| `.tf-chip-row`, `.tf-chip`, `.tf-chip__meta` | Chat-Vorschläge der Chat-ID-Erkennung als klickbare Pills |
| `.tf-session`, `.tf-session__title/__info/__meta/__stats` | Statuskarte der aktiven Session (Countdown `role="timer"`, Zähler, „Session beenden“) |
| `.tf-steps--compact` | drei Schritte im BYOB-Panel (dichter als die Howto-Liste) |

Zusätzlich seit v2.3.0 (Sende-Bestätigung, Top-Warnung, Privatsphäre):

| Klasse | Zweck |
|---|---|
| `.tf-top-warning` | rotes Vollbreite-Band unter dem Header für die öffentliche-Bot-Warnung (nur bei konfiguriertem geteilten Bot im Template) |
| `.tf-modal`, `.tf-modal__backdrop/__panel/__title/__lead/__preview/__length` | Sende-Bestätigungsdialog: Fixed-Overlay, neutraler Backdrop `rgba(127,127,127,α)` (dokumentierte Ausnahme), Panel mit `margin: auto` (zentriert UND bei Überhöhe scrollbar) |
| `.tf-facts`, `.tf-facts__row` | dt/dd-Faktenliste im Dialog (Bot, Ziel, Nachricht); ab `40rem` zweispaltig Label/Wert |
| `.tf-note--ok` | grüne Gegenbox zu `.tf-note--danger` (Titel `--ok`, Fließtext normal) |
| `.tf-privacy__grid/__item/__lead/__note` | Privatsphäre-Sektion: 2×2 Erklär-Karten (BotFather, Bots, geteilter Bot, BYOB) mit `--danger`/`--ok` Akzentkante |

JS-Vertrag zwischen `app.js` und `byob.js` (bewusst minimal, kein Framework):

* `window.tfByob = { isActive(): bool, describeTarget(): {bot, chat}|null, sendText(text): Promise }` — ist eine
  Session aktiv, delegiert `app.js#send()` an `sendText` (gleiche Antwortform
  `{ok, status, data}`), sonst gilt der klassische `/api/send`-Weg.
  `describeTarget()` liefert Bot-Handle + Chat-ID der aktiven Session für
  die Senden-Bestätigung (`null` ohne Session).
* `window.tfSendLabel` — optionaler Button-Text („Über eigenen Bot senden“),
  den `app.js#setBusy` beim Zurücksetzen übernimmt.
* `<body>`-Datatribute: `data-shared-bot` (Anzeige-Handle des geteilten
  Bots) und `data-configured` (`"1"`/`"0"`) — daraus baut `app.js` den
  Dialog-Zustand, wenn keine Session läuft.
* Statusanzeigen teilen sich `#sendStatus` (Editor) und `#byobError` (BYOB).

## 9. Umgebungs-/Deployment-Hinweise

* Statische Assets werden von Flask automatisch unter `/static/` ausgeliefert;
  Render-Start (`render.yaml` → `gunicorn "telegram_formatter.app:app"`)
  ändert sich **nicht**, keine neuen Python-Abhängigkeiten.
* Caching: Werkzeug/Gunicorn setzen hier bewusst keine langen `Cache-Control`-
  Header; wer ein CDN davor schaltet, sollte `static/` mit `max-age=300`
  bedienen. (Falls später Content-Hashing-Namen eingeführt werden, müsste das
  Template zur Build-Zeit angepasst werden — aktuell genügen saubere Releases.)
