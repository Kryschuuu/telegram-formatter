#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Vendoring-Helfer für KaTeX (v2.14.0)
#
# Holt KaTeX und legt die Dateien so ab, dass sie zum Projekt passen.
# Aufruf (aus dem Projektwurzelverzeichnis):
#
#     scripts/vendor-katex.sh            # 0.18.9 = die gepinnte Version
#     scripts/vendor-katex.sh 0.16.11    # andere Version
#
# Warum das Skript existiert und nicht "einmal von Hand":
# 1. Die Assets liegen als **Kopie** im Repository (die App läuft ohne
#    Netzwerk, CSP erlaubt nur 'self', kein CDN).
# 2. `katex.min.css` referenziert je Schrift **drei** Formate
#    (woff2, woff, ttf). Ausgeliefert wird nur woff2 — es steht in jeder
#    `src`-Liste an erster Stelle und wird von allen Browsern seit 2015
#    unterstützt. Die anderen beiden würden nie abgerufen; sie mitzuliefern
#    hieße ~1,2 MB tote Dateien in jedem Image und jedem Wheel.
# 3. Weil das CSS damit vom Original abweicht, prüft
#    `tests/test_frontend.py::test_katex_assets_are_complete`, dass **jede**
#    verbleibende `url(fonts/…)`-Referenz auch wirklich existiert. Beim
#    erneuten Vendoring ohne dieses Skript schlägt der Test also alarmiert
#    an, statt die Formeln still in einer Ersatzschrift zu zeigen.
#
# Setzt `npm` voraus (Netz) — das Ergebnis ist danach vollständig offline.
# ---------------------------------------------------------------------------
set -euo pipefail

VERSION="${1:-0.18.9}"
DEST="telegram_formatter/static/katex"

command -v npm >/dev/null || { echo "npm fehlt — Node >= 18 wird gebraucht" >&2; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "→ hole katex@$VERSION"
npm pack "katex@$VERSION" --silent --pack-destination "$WORK" >/dev/null
tar xzf "$WORK"/katex-*.tgz -C "$WORK"

echo "→ lege ab nach $DEST"
rm -rf "$DEST"
mkdir -p "$DEST/fonts"
cp "$WORK/package/dist/katex.min.css" "$DEST/"
cp "$WORK/package/dist/katex.min.js"  "$DEST/"
cp "$WORK/package/LICENSE"             "$DEST/LICENSE"
printf '%s\n' "$VERSION"                > "$DEST/VERSION"
cp "$WORK/package/dist/fonts/"*.woff2  "$DEST/fonts/"

echo "→ entferne die nie abgerufenen woff-/ttf-Quellen aus dem CSS"
python3 - "$DEST/katex.min.css" <<'PY'
import re
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    css = fh.read()

# Jede `src`-Liste auf ihr **erstes** Format beschränken.
#
# Welches Format das ist, wird nicht entschieden — die Reihenfolge im
# Original ist maßgeblich, und sie zu ändern wäre ein Fehler. Wir
# schneiden nur alles ab dem **zweiten** `url(` weg. Das Ergebnis ist
# funktional identisch: ein Browser nimmt das erste Format, das er
# unterstützt, und woff2 steht überall an erster Stelle.
#
# Muster: `src:` + erstes `url(…)` + optionales `format("…")`; der Rest
# der Liste bis zum Semikolon bzw. zur schließenden Klammer wird verworfen.
pattern = re.compile(r'(src:url\([^)]*\)(?:\s*format\("[^"]*"\))?)[^;{}]*')
css, count = pattern.subn(lambda m: m.group(1), css)

with open(path, "w", encoding="utf-8") as fh:
    fh.write(css)

remaining = sorted(set(re.findall(r"url\((fonts/[^)]+)\)", css)))
print(f"   {count} src-Listen gekürzt, {len(remaining)} Font-Referenzen übrig")
if not remaining:
    raise SystemExit("FEHLER: das CSS referenziert danach keine Fonts mehr")
if any(not ref.endswith(".woff2") for ref in remaining):
    print(
        "   WARNUNG: es sind noch Nicht-woff2-Referenzen übrig: "
        + ", ".join(ref for ref in remaining if not ref.endswith(".woff2")),
        file=sys.stderr,
    )
    raise SystemExit(1)
PY

echo "→ fertig:"
printf '   %s: %s Dateien, %s\n' \
    "$DEST" \
    "$(find "$DEST" -type f | wc -l | tr -d ' ')" \
    "$(du -sh "$DEST" | cut -f1)"
echo "   Jetzt: pytest tests/test_frontend.py -k katex"
