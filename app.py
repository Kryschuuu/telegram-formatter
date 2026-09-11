"""
app.py (Repository-Wurzel) — Kompatibilitäts-Shim, veraltet
============================================================

**Veraltet — wird mit 3.0.0 entfernt.** Diese Datei enthält **keine** Logik
und ist ein reiner Weiterleiter auf den kanonischen Einstieg
:mod:`telegram_formatter.app`.

Hintergrund: Seit der Paket-Reorganisation in 2.0.0 (siehe
[MIGRATION.md](MIGRATION.md), Abschnitt 2/3) liegt die Flask-Anwendung unter
``telegram_formatter/app.py``; das frühere Root-Modul ``app.py`` existierte
nicht mehr. Hosting-Plattformen, deren Start-Kommando noch aus der Zeit vor
der Umstellung stammt (auf Render.com im Dashboard hinterlegt, nicht im Git),
liefen deshalb ins Leere::

    ModuleNotFoundError: No module named 'app'

Der Build war erfolgreich, nur der Start scheiterte. Dieser Shim stellt die
alte Adresse wieder her, sodass ein bereits konfiguriertes Deployment sofort
weiterläuft, ohne dass im Dashboard etwas angefasst werden muss.

Richtig und langfristig ist der kanonische Start (deklariert in
[render.yaml](render.yaml))::

    gunicorn "telegram_formatter.app:app" --bind 0.0.0.0:$PORT

Regeln für diese Datei:

* Nie Logik, Routen, Konfiguration oder Importe außer ``app`` hierher —
  sonst entsteht eine zweite, kopierte Anwendungsschicht. Abgesichert durch
  ``tests/test_app.py`` (Identität des WSGI-Objekts + AST-Prüfung).
* Löschen, sobald nach dem Umstellen aller Deployments auf den kanonischen
  Befehl kein Dienst mehr ``gunicorn app:app`` verwendet (spätestens 3.0.0).
"""

from telegram_formatter.app import app

__all__ = ["app"]
