# PR-Beschreibung — v2.8.0: Öffentliche Demo als public Supergroup/Channel

> Archiviert aus `peer-review/2026-09_PUBLIC-DEMO-CHAT.md`. Branch:
> `arena/01a09ea0-telegram-formatter`.

## Zusammenfassung

Die gehostete Demo läuft künftig über einen **öffentlichen** Zielchat
(public Supergroup/Channel). Dadurch ist die Privatsphäre-Warnung auf der Seite
wörtlich korrekt — jeder, der die Gruppe oder den Kanal öffnet, kann den
gesamten Verlauf lesen — und das persönliche Postfach des Betreibers bleibt
für die Demo unberührt. Gleichzeitig Peer-Review mit Bugfixes, Doku-Bereinigung
und Versionierung.

## Änderungen

- **Warntexte** (Template + `app.py`-Docstring): einheitlich „jeder, der die
  Gruppe oder den Kanal auf Telegram öffnet, kann den gesamten Verlauf lesen"
  (statt „wer den Bot hinzufügt") an allen vier Stellen.
- **Deployment** (`docs/DEPLOYMENT.md` Schritt 4b, `render.yaml`): Anleitung,
  den Zielchat als public Supergroup/Channel einzurichten und als
  `TELEGRAM_CHAT_ID` zu pinnen; `TELEGRAM_FORMATTER_SHARED_BOT_HANDLE` im
  Blueprint deklariert.
- **Doku-Inkonsistenzen** (README): Version `2.4.0`→`2.8.0`, Testzahl aktualisiert,
  Versions-Badge, Sichtbarkeits-Tabelle präzisiert.
- **Redundanz entfernt:** tote `sid is None or session_secret is None`-Prüfung in
  den drei BYOB-Handlern.
- **Tests:** `tests/test_public_demo_chat.py` (Warnung formuliert öffentlichen
  Chat korrekt; Blueprint deklariert Chat + Bot).
- **Version:** `2.7.0` → `2.8.0`; Changelog-Eintrag + Peer-Review-Bericht.

## Testplan

- [x] `pytest -q` — 365 passed, 1 skipped
- [x] `ruff check .` — sauber
- [x] `bandit -c pyproject.toml -r telegram_formatter` — keine Befunde

## Risiko

Niedrig. Rein textliche/deployment-seitige Änderungen plus eine Verhaltens-
äquivalente Bereinigung; kein API-/Payload-Wechsel, keine neuen Abhängigkeiten.
