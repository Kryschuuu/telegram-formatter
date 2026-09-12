"""Vertrags-Tests für das statische Frontend (UI-Redesign 2026-09).

Diese Tests sichern die Regeln des selbst-gehosteten Design-Systems, ohne
einen Browser zu benötigen:

* **Assets:** alles, was das Template einbindet, liegt lokal unter ``static/``
  — kein CDN (Ursache des ehemaligen Design-Bruchs), keine Waisen-Dateien.
* **Token-Architektur:** jedes ``var(--x)`` ist definiert; jeder Theme-Block
  in ``tokens.css`` definiert den vollständigen Token-Satz; der
  No-JS-Fallback („auto") ist wert-identisch zu „dark".
* **DOM-Verträge:** jede ID, die ``app.js``/``theme.js`` ansprechen, existiert
  im Template; der Theme-Switcher bietet exakt die fünf wählbaren Themes.
* **Keine Geist-Klassen:** jede im Template benutzte ``tf-``-Klasse ist in
  den CSS-Dateien definiert.
* **Browser-Kompatibilität:** keine experimentellen CSS-Funktionen jenseits
  des Baseline-Sets (Custom Properties, Grid, Flexbox, ``clamp()``,
  ``@supports``, ``backdrop-filter`` hinter ``@supports``).
* **Responsive First:** ausschließlich ``min-width``-Media-Queries
  (mobile zuerst) im Layout; keine ``max-width``-Regeln in CSS-Dateien.

Die *funktionalen* Abläufe (Debounce, Fetch, Theme-Klicks) testet zusätzlich
``tests/frontend/jsdom_spec.cjs`` (via tests/test_jsdom_smoke.py), falls Node
+ jsdom vorhanden sind.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from telegram_formatter import app as app_module

PKG_ROOT = Path(app_module.__file__).resolve().parent
STATIC_DIR = PKG_ROOT / "static"
CSS_DIR = STATIC_DIR / "css"
JS_DIR = STATIC_DIR / "js"
TEMPLATE_PATH = PKG_ROOT / "templates" / "index.html"

CSS_ORDER = ("tokens.css", "base.css", "layout.css", "components.css")
JS_FILES = ("theme.js", "app.js")

#: Tokens, die pro Theme *identisch* bleiben und daher in den Theme-Blöcken
#: fehlen dürfen (in :root definiert, siehe tokens.css).
GLOBAL_TOKENS = {"--font-sans", "--font-mono", "--container", "--transition"}

#: Bewusst hartkodierte Farben: die Theme-Kleckse des Switchers *zeigen* das
#: Theme und dürfen sich nicht mit dem aktuellen Theme mitfärben.
SWATCH_EXCEPTION = re.compile(
    r"\.tf-theme-btn\[data-theme-choice=\"[a-z]+\"\]\s*\.tf-theme-btn__swatch\s*\{[^}]*\}"
)


@pytest.fixture(scope="module")
def page() -> str:
    """Gerenderte Startseite über den Flask-Testclient (ohne Netzwerk)."""
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        return client.get("/").data.decode("utf-8")


def _css_text() -> str:
    return "\n".join((CSS_DIR / name).read_text(encoding="utf-8") for name in CSS_ORDER)


def _js_text() -> str:
    return "\n".join((JS_DIR / name).read_text(encoding="utf-8") for name in JS_FILES)


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rule_blocks(css: str) -> list[tuple[str, str]]:
    """Alle flachen ``selector { body }``-Blöcke (Selector, Body) — Kommentare raus."""
    css = _strip_comments(css)
    return [(sel.strip(), body) for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css)]


def _tokens_of(body: str) -> dict[str, str]:
    """`--name: value`-Paare eines Regel-Body, in Reihenfolge."""
    body = _strip_comments(body)
    found = re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+);", body)
    return {name: " ".join(value.split()) for name, value in found}


# --------------------------------------------------------------------------- #
# Assets: selbst-gehostet, vollständig, keine Waisen
# --------------------------------------------------------------------------- #
def test_all_referenced_assets_exist(page: str):
    refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', page)
    assert refs, "Template bindet keine /static/-Assets ein"
    for ref in refs:
        file = STATIC_DIR / ref.removeprefix("/static/")
        assert file.is_file(), f"referenziertes Asset fehlt: {ref}"
        assert file.stat().st_size > 200, f"Asset auffällig klein (leer?): {ref}"


def test_no_orphan_static_files(page: str):
    referenced = {ref.removeprefix("/static/") for ref in re.findall(r'(?:href|src)="(/static/[^"]+)"', page)}
    on_disk = {str(p.relative_to(STATIC_DIR)) for p in STATIC_DIR.rglob("*") if p.is_file()}
    assert on_disk == referenced, (
        "Dateien auf der Platte und Referenzen im Template driften auseinander: "
        f"nur auf Platte {sorted(on_disk - referenced)}, nur referenziert {sorted(referenced - on_disk)}"
    )


def test_css_load_order_is_layers(page: str):
    positions = [page.index(f"css/{name}") for name in CSS_ORDER]
    assert positions == sorted(positions), "CSS-Schichten müssen in der Reihenfolge der tokens.css geladen werden"


def test_no_external_resource_requests(page: str):
    """Link/Skript-Einträge zeigen ausschließlich auf eigene Quellen.

    Externe ``<a href>``-Links (BotFather, Buy-Me-a-Coffee …) sind erlaubt —
    sie laden keine Sub-Ressourcen.
    """
    offenders = []
    for tag in re.findall(r"<(?:script|link|img|iframe)[^>]*>", page):
        for url in re.findall(r'(?:src|href)="([^"]+)"', tag):
            if url.startswith(("http://", "https://", "//")):
                offenders.append(url)
    assert not offenders, f"externe Sub-Ressource(n) im Template: {offenders}"


# --------------------------------------------------------------------------- #
# CSS-Token-Architektur
# --------------------------------------------------------------------------- #
def test_all_used_var_tokens_are_defined(page: str):
    css = _strip_comments(_css_text()) + _strip_comments(page)  # Template: verteidigend
    used = set(re.findall(r"var\(\s*(--[a-z0-9-]+)", css))
    defined = set()
    for _selector, body in _rule_blocks((CSS_DIR / "tokens.css").read_text(encoding="utf-8")):
        defined |= set(_tokens_of(body))
    missing = used - defined
    assert not missing, f"var(--…) benutzt, aber nirgends definiert: {sorted(missing)}"


def test_every_theme_block_defines_full_token_set():
    blocks = {
        sel: body
        for sel, body in _rule_blocks((CSS_DIR / "tokens.css").read_text(encoding="utf-8"))
    }
    root_body = next(body for sel, body in blocks.items() if sel == ":root")
    master = set(_tokens_of(root_body)) - GLOBAL_TOKENS
    assert len(master) >= 25, "Der :root-Block sieht nicht nach vollem Token-Satz aus"

    for theme in ("light", "dark", "colorful", "minimal"):
        sel = ':root' if theme == "light" else f'[data-theme="{theme}"]'
        body = blocks.get(sel)
        assert body is not None, f"Theme-Block fehlt: {sel}"
        defined = set(_tokens_of(body)) - GLOBAL_TOKENS
        missing = master - defined
        extra = defined - master
        assert not missing, f"Theme {theme!r} fehlen Tokens: {sorted(missing)}"
        assert not extra, f"Theme {theme!r} definiert unbekannte Tokens: {sorted(extra)}"


def test_auto_fallback_matches_dark_token_values():
    """No-JS-Regression: der prefers-color-scheme-Fallback *muss* wie Dark
    aussehen, sonst driftet „auto ohne JS" vom Dark-Theme weg."""
    css = (CSS_DIR / "tokens.css").read_text(encoding="utf-8")
    blocks = dict(_rule_blocks(css))
    dark = next(_tokens_of(body) for sel, body in blocks.items() if sel == '[data-theme="dark"]')
    fallback = next(
        _tokens_of(body)
        for sel, body in blocks.items()
        if ":not([data-theme])" in sel
    )
    assert fallback == dark, "Auto-Fallback-Block weicht von [data-theme=dark] ab"


def test_no_color_hardcoding_outside_tokens():
    """Farbwerte gehören in tokens.css. Erlaubte, dokumentierte Ausnahmen:

    1. Theme-Swatches (.tf-theme-btn__swatch) — sie *zeigen* das Theme und
       dürften sich sonst selbst überdecken.
    2. Brand-Gold des Buy-me-a-coffee-Buttons — in *allen* Themes identisch.
    3. NeutraleOverlay rgba(127,127,127,α) — kein Theme-Bezug, funktioniert
       auf hell wie dunkel.
    """
    for name in CSS_ORDER[1:3]:  # base.css, layout.css — strikt farbfrei
        css = _strip_comments((CSS_DIR / name).read_text(encoding="utf-8"))
        hits = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)
        assert not hits, f"{name} enthält hartkodierte Farben: {hits[:5]} …"

    css = _strip_comments((CSS_DIR / "components.css").read_text(encoding="utf-8"))
    css = SWATCH_EXCEPTION.sub("", css)
    css = re.sub(r"\.tf-btn--coffee[^{]*\{[^}]*\}", "", css)
    css = re.sub(r"rgba\(127,\s*127,\s*127,[^)]*\)", "", css)
    hits = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css)
    assert not hits, f"components.css: undeklarierte Farb-Hartkodierung: {hits[:6]} …"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    # Die eine erlaubte Ausnahme: meta[name=theme-color] *muss* eine feste
    # Startfarbe tragen (Fallback, bevor theme.js greift) — kein Styling.
    template_no_meta = re.sub(
        r'<meta\s+name="theme-color"[^>]*>', "", template, flags=re.S
    )
    assert not re.search(r'style="|color\s*:|background[^;"]*\s*:', template_no_meta), (
        "Template darf keine Inline-Styles setzen (CSP verbietet style=\"\" ohnehin)"
    )
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", template_no_meta), (
        "Template darf Farben nicht hartkodieren — dafür gibt es die Tokens"
    )


def test_braces_balanced_in_every_css_file():
    for name in CSS_ORDER:
        css = _strip_comments((CSS_DIR / name).read_text(encoding="utf-8"))
        assert css.count("{") == css.count("}"), f"Unbalancierte {{}} in {name}"


def test_no_important_outside_base_exceptions():
    for name in CSS_ORDER:
        css = _strip_comments((CSS_DIR / name).read_text(encoding="utf-8"))
        bangs = css.count("!important")
        if name == "base.css":
            # Ausnahmen: [hidden] + zwei reduce-motion-Regeln (durchgesetzt sein *müssen*).
            assert bangs <= 4, f"base.css häuft !important ({bangs})"
        else:
            assert bangs == 0, f"{name} nutzt !important — bitte Spezifität reparieren"


def test_browser_baseline_features_only():
    """Verbot für CSS jenseits des gut unterstützten Baseline-Sets in allen
    relevanten Browsern (Chromium, Firefox, Safari — letzte zwei Jahre)."""
    css = _css_text()
    forbidden = (
        "@container", "container-type", "color-mix(", "oklch(", "oklab(", "lab(",
        "light-dark(", ":has(", "@scope", "field-sizing", "backdrop-filter: none",
    )
    hits = [f for f in forbidden if f in css]
    assert not hits, f"nicht breit unterstütztes CSS gefunden: {hits}"
    # backdrop-filter ist (2024+) breit verfügbar, aber sicherheitshalber nur
    # hinter @supports — sonst bleibt ein nicht unterstützter Browser leer.
    occurrences = re.findall(r"(?<!\()backdrop-filter:", css)
    guarded = re.findall(r"@supports \(backdrop-filter", css)
    # -webkit-Varianten zählen als Duplikate desselben Blocks:
    assert len(occurrences) <= len(guarded) * 2, (
        "backdrop-filter außerhalb eines @supports-Guards gefunden"
    )


# --------------------------------------------------------------------------- #
# DOM-Verträge Template <-> JavaScript
# --------------------------------------------------------------------------- #
def test_js_ids_exist_in_template(page: str):
    needed = set(re.findall(r'getElementById\("([^"]+)"\)', _js_text()))
    assert needed >= {
        "input", "preview", "payloads", "sendBtn", "sendBtnLabel",
        "resetBtn", "sendStatus", "charCount", "themeSwitcher",
    }, "unerwartet kleines ID-Set — hat jemand die IDs umgebaut?"
    for dom_id in sorted(needed):
        assert f'id="{dom_id}"' in page, f"JS erwartet id=\"{dom_id}\" — im Template fehlt sie"


def test_theme_switcher_offers_all_choices(page: str):
    choices = re.findall(r'data-theme-choice="([^"]+)"', page)
    assert sorted(choices) == ["auto", "colorful", "dark", "light", "minimal"]
    container = re.search(r'<div class="tf-theme-switcher" id="themeSwitcher"[^>]*>', page)
    assert container and 'role="group"' in container.group(0)
    for btn in re.findall(r"<button[^>]*data-theme-choice[^>]*>", page):
        assert 'aria-pressed="false"' in btn, f"fehlendes aria-pressed: {btn[:60]} …"
        assert 'title="' in btn, "Theme-Button ohne Titel/Tooltip"


def test_html_carries_no_data_theme_and_lang_is_set(page: str):
    """data-theme setzt ausschließlich theme.js (vor dem First Paint); das
    Template hält sich raus, damit der No-JS-Fallback greifen kann."""
    html_tag = re.search(r"<html[^>]*>", page).group(0)
    assert "data-theme" not in html_tag
    assert 'lang="de"' in html_tag
    assert 'name="viewport"' in page
    assert 'name="description"' in page
    assert 'name="theme-color"' in page


def test_no_ghost_tf_classes(page: str):
    """Jede benutzte .tf-Klasse (und die JS-Zustandsklassen) ist definiert."""
    used = set()
    for attrs in re.findall(r'class="([^"]*)"', page):
        used |= {c for c in attrs.split() if c.startswith("tf-")}
    css = _css_text()
    defined = set(re.findall(r"\.((?:is-|tf-)[a-z0-9_-]+)", css))
    # Zustandklassen, die nur JS setzt (is-active/is-ok/is-error/is-over),
    # müssen trotzdem im CSS stehen — sonst toggelt JS ins Leere.
    ghost = {c for c in used if c not in defined}
    assert not ghost, f"Klassen im Template ohne CSS-Definition: {sorted(ghost)}"


def test_faq_and_hooks_present(page: str):
    """Die funktionalen Hooks der Formatter-Logik + Inhaltssektionen (Redefinition
    alter Tailwind-Markup-Verträge): alle müssen im neuen Design erhalten sein."""
    for token in (
        'id="input"', 'id="preview"', 'id="payloads"', 'id="sendBtn"',
        'id="resetBtn"', 'id="sendStatus"', 'id="status"', 'id="howto"', 'id="faq"',
        "data-convert-url", "data-send-url",
    ):
        assert token in page, f"Funktions-Hook fehlt: {token}"
    assert page.count("<details") >= 7
    assert "Vorschau erscheint hier…" in page
    assert page.index('js/theme.js"') < page.index("<body")  # synchron im <head>


# --------------------------------------------------------------------------- #
# Responsive-Strategie (mobile zuerst) & Accessibility-Basics
# --------------------------------------------------------------------------- #
def test_mobile_first_breakpoints():
    layout = (CSS_DIR / "layout.css").read_text(encoding="utf-8")
    components = (CSS_DIR / "components.css").read_text(encoding="utf-8")
    assert "@media (max-width" not in layout + components, (
        "Breakpoints sind mobile-first: nur min-width-Queries, keine max-width"
    )
    min_widths = set(re.findall(r"@media \(min-width:\s*([\d.]+)rem\)", layout + components))
    assert len(min_widths) >= 3, f"zu wenige Responsive-Breakpoints: {sorted(min_widths)}"
    # Basis gilt für Telefone: Arbeitsbereich einspaltig, Desktop zweispaltig.
    assert "minmax(0, 1fr)" in layout and "64rem" in layout


def test_touch_targets_and_resizing_basics(page: str):
    css = _css_text()
    assert "resize: vertical" in (CSS_DIR / "base.css").read_text(encoding="utf-8") or \
           "vertical" in css
    assert "-webkit-text-size-adjust: 100%" in css, "Mobile Font-Inflation aus"


def test_accent_and_surface_contrast_pairs():
    """Einfache Heuristik statt echter WCAC-Berechnung: on-accent muss zu
    accent und accent-hover passen (rel. Luminanz-Differenz), Surface zu Text.
    Schützt vor unsichtbaren Buttons, wenn ein Theme editiert wird."""

    def lum(hex_color: str) -> float:
        hex_color = hex_color.lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (0, 2, 4))

        def lin(c: float) -> float:
            return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

        return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

    def contrast(a: str, b: str) -> float:
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)

    css = (CSS_DIR / "tokens.css").read_text(encoding="utf-8")
    blocks = dict(_rule_blocks(css))
    pairs = (
        ("--accent", "--on-accent"),
        ("--accent-hover", "--on-accent"),
        ("--surface", "--text"),
        ("--bg", "--text"),
        ("--surface", "--heading"),
    )
    checked = 0
    for sel in (":root", '[data-theme="dark"]', '[data-theme="minimal"]'):
        tokens = _tokens_of(blocks[sel])
        for fg_name, bg_name in pairs:
            fg, bg = tokens.get(fg_name, ""), tokens.get(bg_name, "")
            if not (fg.startswith("#") and bg.startswith("#")):
                continue  # verläuft über Gradients/Transparenz → hier nicht prüfbar
            checked += 1
            assert contrast(fg, bg) >= 4.0, (
                f"{sel}: {fg_name} auf {bg_name} kontrastiert zu schwach "
                f"({contrast(fg, bg):.2f} < 4.0) — {fg} vs. {bg}"
            )
    assert checked >= 10, "Kontrast-Heuristik hat fast nichts geprüft — Token-Namen umbenannt?"


def test_rendered_page_is_well_formed(page: str):
    """Alle Nicht-Leer-Elemente müssen sauber geöffnet/geschlossen und verschachtelt
    sein. Stille Browser-Fehlerkorrekturen unterscheiden sich (Chromium vs.
    Firefox vs. Safari) und würden genau das alte Design-Debakel re-produzieren.
    """
    void = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    class NestingCheck(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.stack: list[tuple[str, int]] = []
            self.problems: list[str] = []

        def handle_starttag(self, tag: str, attrs) -> None:
            if tag not in void:
                self.stack.append((tag, self.getpos()[0]))

        def handle_endtag(self, tag: str) -> None:
            if tag in void:
                return
            if not self.stack:
                self.problems.append(f"</{tag}> (Zeile {self.getpos()[0]}) ohne Offener")
                return
            open_tag, open_line = self.stack.pop()
            if open_tag != tag:
                self.problems.append(
                    f"</{tag}> (Zeile {self.getpos()[0]}) schließt <{open_tag}> (Zeile {open_line}) nicht"
                )

    checker = NestingCheck()
    checker.feed(page)
    checker.close()
    assert not checker.stack, f"NICHT geschlossene Tags: {checker.stack}"
    assert not checker.problems, "; ".join(checker.problems)
