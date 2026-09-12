## Was ändert sich?

Die Web-Oberfläche war kaputt: sie hing am **Tailwind-Play-CDN**, dessen zur Laufzeit injizierte Inline-`<style>`-Regeln von der gehärteten CSP (`style-src` ohne `'unsafe-inline'`) blockiert wurden — die Seite renderte als **ungestylter Rohtext**. Dieses PR ersetzt das CDN-Design durch ein **vollständig selbst-gehostetes Design-System** („tf“: 4 CSS-Schichten, 2 JS-Module, Inline-SVG-Icons) mit **vier Themes + Auto-Modus** und Theme-Switcher. Die Formatter-Logik (`utils.py`, `sender.py`, API) bleibt **unberührt**; alle Endpunkte, Limits, Guards und der Deployment-Start sind unverändert.

### Root-Cause (Postmortem in `docs/DESIGN.md` Abschnitt 0)

```text
CSP: style-src 'self' https://cdnjs.cloudflare.com   (bewusst ohne 'unsafe-inline', Audit H-5)
Tailwind Play-CDN: injiziert <style>-Blöcke inline    → blockiert → 0 CSS → Rohtext
```

Fix = strukturell, nicht symptomatisch: **kein externes Asset mehr**, CSP strikt `'self'`, damit kann diese Fehlerklasse nicht wiederkehren. Tests erzwingen das (siehe unten).

## Typ

- [ ] Nutzer-Bot (neu/geändert)
- [ ] `telegram_formatter/botkit`-Baukasten
- [x] Doku / CI (Docs: `docs/DESIGN.md` u. a.)
- [x] Bestehende Konvertierung (in `telegram_formatter/`: … `app.py` …) — nur die **CSP** in `app.py`; `utils.py`/`sender.py`/`cli.py` unverändert

## Design-System (Kurzübersicht — Details: [`docs/DESIGN.md`](docs/DESIGN.md))

```text
telegram_formatter/
├── templates/index.html        Semantik + Content; keine Farben, kein Inline-Style
└── static/
    ├── css/tokens.css          Ebene 1: Design-Tokens, alle Themes (data-theme)
    ├── css/base.css            Ebene 2: Reset & Typografie (strikt farbfrei)
    ├── css/layout.css          Ebene 3: Struktur & mobile-first Breakpoints (farbfrei)
    ├── css/components.css      Ebene 4: .tf-*-Komponenten, nur var(--token)
    ├── js/theme.js             Theme-Switcher (synchron im <head>, localStorage)
    ├── js/app.js               Editor: Vorschau, Debounce→/api/convert, /api/send
    └── favicon.svg             selbst-gehostet
```

### Themes (wechselbar, persistiert)

| Theme | Charakter |
|---|---|
| **Light** | Ruhiges Tageslicht, Telegram-Blaue, weiche Schatten (Standard) |
| **Dark** | Telegram-Desktop-Nacht, chatblaue Sprechblase in der Vorschau |
| **Colorful** | Indigo→Petrol-Nachtverlauf, Glas-Karten (`backdrop-filter` hinter `@supports`), Gradient-Buttons, rundeste Radien |
| **Minimal** | Redaktionell-monochrom: hairline-Rahmen, keine Schatten, kantige Radien |
| **Auto** | Folgt dem Betriebssystem **ohne JS-Anteil** (`prefers-color-scheme`), reagiert live auf Systemwechsel |

Theme = **eine Token-Deklaration** in `tokens.css`; Komponenten kennen nur `var(--token)` → neues Theme in ~10 Minuten (Rezept in `docs/DESIGN.md` §3.1, Vollständigkeit erzwungen durch `test_every_theme_block_defines_full_token_set`).

### Theme-Switcher

Buttons im Sticky-Header (`role="group"`, `aria-pressed`), Auswahl unter `localStorage["tf-theme"]`; `theme.js` setzt `<html data-theme>` **synchron im `<head>`** → kein „Flash of wrong theme“; robust ohne localStorage/matchMedia; ohne JS greift der System-Fallback. Meta-`theme-color` wird für Browser-Oberfläche/Handy-Statusbar mitgezogen.

### UX & visuelle Hierarchie

Hero-Zeile (was tut das Tool) → Zwei-Spalten-Workspace (Eingabe | Live-Vorschau in angedeuteter Chat-Umgebung + Payload-Terminal, auf Desktop sticky) → Howto-Schritte → FAQ → Footer. Neu: Zeichenzähler (warnt > 4096), **Strg/Cmd+Enter = senden**, Skip-Link, `role="status"`-Live-Region, Noscript-Hinweis, verbesserter Vorschau-Renderer (Code, Durchstreichen, Links, Formel-Highlight — weiterhin escaping-first, **kein HTML-Injection-Weg**).

### Responsive

Mobile-first; Breakpoints bei 40/48/64/72 rem: Telefon einspaltig & kompakt → Tablet (3-spaltiger Footer) → Desktop (Zweispalten + sticky Ausgabe) → 5-er-Schritt-Kette. Header bricht per `flex-wrap` sauber um; Switcher zeigt auf Telefon Farbkleckse, ab 640 px mit Label; `100svh` mit `100vh`-Fallback.

## Sicherheit (unverändert / verbessert)

- CSP jetzt **strenger**: `default-src/script-src/style-src 'self'` — alle Fremdhost-Whitelists entfernt; keine Inline-Skripte/-Styles (`test_assets_are_self_hosted_no_inline_script`).
- Keine CDN-/Tracking-Netze mehr = kleinere Attack-/Failure-Surface, funktioniert offline (Audit-H-5-SRI-Thema erledigt, da keine CDN-Assets).
- Chat-Pinning, `X-Auth-Token`, Body-/Input-Limits, Rate-Limits, Origin-Bindung, Privacy-Redaction: **Code untouched**, alle Härtungs-Tests grün.

## Tests (alles grün: **259 passed** + 37 jsdom-Checks)

- `pytest -q` → **259 passed** (warum 238→259: +20 Struktur- + 1 jsdom-Test).
- **Funktionalität:** bestehende Suite (Konvertierung, Rich/Regular-Pfad, Chunking, Senden, alle Security-Fixes) unverändert grün; API-Verhalten gegen den Dev-Server geprüft (`/api/convert` Rich-Erkennung ✓, 404/413-Pfade ✓, Header ✓).
- **Design/Themes:** `tests/test_frontend.py` — 20 Strukturverträge (Token-Vollständigkeit je Theme, Auto==Dark, `var()`-Abdeckung, keine Geist-Klassen, keine Farb-Hartkodierung außerhalb Tokens/Ausnahmen, Wellformedness, Kontrast-Heuristik ≥ 4:1 …).
- **DOM-Funktionsablauf:** `tests/frontend/jsdom_spec.cjs` führt die **echten** `theme.js`/`app.js` gegen das echt gerenderte Template aus (Stub-Fetch): Theme-Boot/Persistenz/Klicks, Vorschau-Rendering, Debounce (genau 1 POST pro Tipppause), Leerer-Editor-Kurzschluss, Sende-Erfolg, 429-Fehler inkl. `retry_after`/Teilsende-Hinweis, Reset — **37 Checks**, sauberer Skip ohne Node/jsdom.
- **Syntax:** `ruff check app.py telegram_formatter examples tests` ✓ · `bandit` ✓ (0 Funde) · CSS/JS zusätzlich mit `css-tree`-Parser + `node --check` verifiziert (0 Fehler).
- **Browser-Kompatibilität:** Bewusst nur Baseline-CSS (Custom Properties, Grid/Flexbox, `clamp()`, `@supports`, `:focus-visible`, `prefers-reduced-motion`); `test_browser_baseline_features_only` **verbietet** `:has()`, `color-mix()`, `oklch()/lab()`, `light-dark()`, `@container`. `backdrop-filter` nur mit Fallback-Pfad. Manuelle Kontrollen in Chromium-/Firefox-/WebKit-aktuellen-Versionen über die Vorschau dieses PR-Zweigs empfohlen; jsdom deckt die DOM-Ebene bereits ab.

→ **Deployment nach Merge:** Render-Dashboard braucht *nichts* Neues (gleicher Startbefehl, keine Env-Vars, keine Abhängigkeiten).

## Pflichtteil für Nutzer-Bots

Nicht zutreffend — es wurden keine Bot-Dateien (`bots/`, `examples/own_bot/`) geändert; Bot-Review-Gate greift bei leeren Diff-Pfaden ins Leere (CI-Log: „Keine Bot-Dateien geändert“).

### Datenschutz (Kernanforderung)

- [x] Keine Personendaten/Inhalte/Tokens in Code, Tests oder Logs — UI schreibt ausschließlich `localStorage["tf-theme"]` (Theme-Name) und hält Inhalte nur im RAM des Tabs; keine Cookies, kein Tracking, keine Analysen
- [x] Keine neue Persistenz (auch keine temporären Dateien/Caches)
- [x] `audit/reviews.json` nicht angefasst

## Tests (Checkboxen)

- [x] `pytest -q` grün (259 passed)
- [x] Neue Tests für das neue Verhalten (inkl. Negativfall: CDN-Verbot, Geist-Klassen, Leerer-Editor, Fehler-/429-Pfad, ohne-JS-Fallback)

## Reviewer

- [ ] Zwei Freigaben, mindestens eine von Maintainer:in (Vier-Augen-Prinzip)

---

<sub>Commits: 5 logische Schritte (CSS-System → JS → Template/CSP → Tests → Doku). Dokumentation: [`docs/DESIGN.md`](docs/DESIGN.md) · CHANGELOG-Eintrag `[Unreleased]` · Architektur-Tabelle ergänzt · README/CONTRIBUTING aktualisiert.</sub>
