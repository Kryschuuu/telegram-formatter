# Peer-Reviews

Dieser Ordner ist die zentrale Stelle für **Code-Review-Vorschläge und ihren
Verlauf** — für den Kern-Code des Projekts ebenso wie für Nutzer-Bots
(`bots/`, `examples/own_bot/`).

## Aufbau

```
peer-review/
├── README.md            # diese Konventionen
├── TEMPLATE.md          # Vorlage für einen neuen Review-Bericht
├── CODE_REVIEW.md       # aktuellster abgeschlossener Review-Bericht
└── archive/             # abgeschlossene Vorgänge (Prompts, PR-Beschreibungen, alte Berichte)
    └── …
```

## Konventionen

1. **Neuer Review-Vorgang:** `TEMPLATE.md` kopieren nach
   `peer-review/<JAHR>-<MONAT>_<kurztitel>.md` und ausfüllen.
2. **Findings immer mit Triple:** `Datei:Zeile — Regel/Kategorie — Begründung — Fix`.
   Ein Befund ohne konkrete Fundstelle ist kein Befund.
3. **Regel-IDs:** Für Nutzer-Bots gelten die `BK001`–`BK012`-Regeln aus
   `telegram_formatter/botkit/review.py` und die Checkliste `C1`–`C9`
   (`python -m telegram_formatter.botctl checklist`). Für den Kern-Code sind
   freie Kategorien (`security`, `correctness`, `maintainability`, …) ok.
4. **Entscheidungen werden protokolliert:** Freigaben/Ablehnungen für
   Nutzer-Bots laufen maschinenlesbar im Audit-Trail
   [`audit/reviews.json`](../audit/) (via `botctl approve`), die menschliche
   Begründung als Markdown hier.
5. **Nach Abschluss:** abgeschlossene Einzelvorgänge (Prompts,
   PR-Beschreibungen) nach `archive/` verschieben — der Ordner enthält nur
   *Ergebnisse*, keine Baustellen.

## Warum hier und nicht in `docs/`?

`docs/` beschreibt, **wie das Projekt ist**. `peer-review/` dokumentiert,
**wie es geprüft wurde und was dabei gefunden wurde** — das ist ein
unterschiedlicher Leserkreis (Reviewer:innen, Audits) und ein
unterschiedlicher Reifegrad. Veraltete Review-Artefakte contaminieren nicht
die aktuelle Doku, bleiben aber als Verlauf erhalten.
