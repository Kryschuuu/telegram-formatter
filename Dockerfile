# telegram-formatter — Produktions-Image
# =======================================
# Schlankes Laufzeit-Image auf python-slim: installiert ausschließlich die
# gepinnten Laufzeit-Abhängigkeiten (requirements.txt) plus das Paket selbst.
# Kein Build-Tooling, keine Tests, keine Doku im Image (siehe .dockerignore).
#
# Bauen & starten (komfortabel über Compose):
#   docker compose up --build -d
#
# Manuell (nur die App, ohne Caddy — z. B. für Smoke-Tests):
#   docker build -t telegram-formatter:2.12.0 .
#   docker run --rm -p 5000:5000 --env-file .env telegram-formatter:2.12.0
#
# BYOB-Hinweis: Die Web-Sessions leben prozesslokal im RAM (siehe
# docs/DECENTRAL_BOT_ARCHITECTURE.md). Deshalb läuft Gunicorn mit genau
# EINEM Worker-Prozess; Nebenläufigkeit liefert --threads (gthread).
# --workers 2 ohne Sticky-Routing würde Sessions im jeweils anderen
# Prozess „verlieren" (Client bekäme 410 statt eines Fehlversands).

FROM python:3.11-slim

# Kein .pyc-Schreibversuch (Image ist zur Laufzeit unveränderlich gedacht),
# Logs ohne Pufferung (docker logs zeigt sofort), kein pip-Cache.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

# Abhängigkeiten zuerst kopieren: Diese Schicht bleibt im Build-Cache, solange
# sich requirements.txt nicht ändert — Code-Änderungen bauen danach schnell.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Nur das Laufzeitpaket ins Image (keine Tests/Doku/CI ins Image).
COPY telegram_formatter/ ./telegram_formatter/

# Least Privilege: Der Prozess läuft als unprivilegierter Systemnutzer.
# Port 5000 ist >1024 und damit ohne Capabilities bindbar.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /srv/app
USER appuser

EXPOSE 5000

# Liveness-Probe gegen die eingebaute /healthz-Route (kein curl nötig —
# reine Standardbibliothek). Compose wartet mit
# depends_on/service_healthy auf diesen Status, bevor Caddy startet.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4)"

# Exec-Form (kein Shell-Wrapper): Gunicorn ist PID 1 und erhält SIGTERM beim
# Container-Stopp direkt (sauberes Herunterfahren, kein verwaister Prozess).
CMD ["gunicorn", "telegram_formatter.app:app", \
     "--bind", "0.0.0.0:5000", \
     "--workers", "1", \
     "--threads", "8", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
