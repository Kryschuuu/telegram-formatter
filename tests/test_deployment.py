"""Vertragstests für das lokale Docker/Caddy-Deployment (seit v2.12.0).

Diese Tests prüfen die *Konfigurationsdateien* des Deployments — nicht einen
laufenden Container (dafür braucht es einen Docker-Host; E2E-Schritte siehe
docs/DOCKER.md). Sie stellen sicher, dass die Produktionsversprechen des
Stacks dauerhaft gelten:

* genau EIN Gunicorn-Worker (BYOB-Sessions leben prozesslokal im RAM),
* die App ist nur über Caddy erreichbar (kein Host-Port an ``app``),
* Caddy terminiert TLS automatisch (``tls internal`` fürs LAN) und setzt
  die IP-Zugriffskontrolle (ACL) für den freigegebenen Client um,
* ``.env.example`` dokumentiert alle Variablen — und enthält garantiert
  keine tokenförmigen Geheimnisse (hielte sonst den Gitleaks-CI auf).

Der Compose-Teil braucht PyYAML (requirements-dev.txt); fehlt es, wird er
übersprungen — Dockerfile/Caddyfile/.env-Prüfungen laufen immer (reine
Textverträge ohne Abhängigkeiten).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH = REPO_ROOT / "Dockerfile"
CADDYFILE_PATH = REPO_ROOT / "Caddyfile"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"

#: Beispielnetz aus docs/DOCKER.md — genau diese Adressen müssen in der
#: ausgelieferten Caddyfile-Konfiguration stehen (konkret, nicht abstrakt).
SERVER_IP = "192.168.0.10"
CLIENT_IP = "192.168.0.20"

#: Alle ENV-Variablen, die telegram_formatter/app.py auswertet — jede muss
#: in .env.example dokumentiert sein (sonst driftet Doku und Code auseinander).
DOCUMENTED_ENV_VARS = [
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_FORMATTER_API_TOKEN",
    "TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS",
    "TELEGRAM_FORMATTER_MAX_INPUT_CHARS",
    "TELEGRAM_FORMATTER_SENDS_PER_MINUTE",
    "TELEGRAM_FORMATTER_CONVERTS_PER_MINUTE",
    "TELEGRAM_FORMATTER_BYOB_ENABLED",
    "TELEGRAM_FORMATTER_BYOB_SESSIONS_PER_MINUTE",
    "TELEGRAM_FORMATTER_BYOB_DISCOVER_PER_MINUTE",
    "TELEGRAM_FORMATTER_BYOB_SENDS_PER_MINUTE",
    "TELEGRAM_FORMATTER_BYOB_TTL_SECONDS",
    "TELEGRAM_FORMATTER_BYOB_IDLE_SECONDS",
    "TELEGRAM_FORMATTER_SHARED_BOT_HANDLE",
    "TELEGRAM_FORMATTER_SHARED_CHAT_URL",
    "TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS",
    "TELEGRAM_FORMATTER_SHARED_WEB_SEND",
    "TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE",
    "TELEGRAM_FORMATTER_SHARED_WEB_SENDS_PER_MINUTE_TOTAL",
    "TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS",
]


@pytest.fixture(scope="module")
def compose() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML nur in requirements-dev.txt")
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def dockerfile() -> str:
    return DOCKERFILE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def caddyfile() -> str:
    return CADDYFILE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def env_example() -> str:
    return ENV_EXAMPLE_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# docker-compose.yml — Topologie und Produktionsversprechen
# --------------------------------------------------------------------------- #
def test_compose_defines_app_and_caddy(compose):
    assert set(compose["services"]) == {"app", "caddy"}


def test_compose_app_has_no_published_ports(compose):
    """Direkter Host-Zugriff auf die App würde die Caddy-ACL umgehen."""
    assert "ports" not in compose["services"]["app"]


def test_compose_caddy_publishes_http_and_https(compose):
    published = " ".join(map(str, compose["services"]["caddy"]["ports"]))
    assert "80:80" in published
    assert "443:443" in published


def test_compose_services_share_one_network(compose):
    """App und Caddy kommunizieren ausschließlich über das interne Netz."""
    networks = compose.get("networks", {})
    assert len(networks) >= 1
    for name in ("app", "caddy"):
        attached = compose["services"][name].get("networks", [])
        assert attached, f"service {name} hängt an keinem Netzwerk"
        assert set(attached) <= set(networks)


def test_compose_forces_exactly_one_trusted_proxy_hop(compose):
    """Hinter Caddy steht immer genau ein Hop — sonst sehen die IP-Limits
    die Proxy-Adresse statt der Client-Adresse (ein gemeinsamer Eimer)."""
    env = compose["services"]["app"].get("environment", {})
    assert env.get("TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS") == "1"


def test_compose_app_reads_secrets_from_env_file(compose):
    """Keine Geheimnisse in der Compose-Datei — sie kommen aus der
    git-ignorierten .env (Vorlage: .env.example)."""
    assert ".env" in compose["services"]["app"].get("env_file", [])


def test_compose_caddy_waits_for_healthy_app(compose):
    depends = compose["services"]["caddy"].get("depends_on", {})
    assert depends.get("app", {}).get("condition") == "service_healthy"


def test_compose_caddy_mounts_config_readonly_and_persists_certs(compose):
    volumes = " ".join(map(str, compose["services"]["caddy"]["volumes"]))
    assert "./Caddyfile:/etc/caddy/Caddyfile:ro" in volumes
    assert "caddy_data:/data" in volumes
    assert "caddy_data" in compose.get("volumes", {})


def test_compose_services_restart_automatically(compose):
    for name in ("app", "caddy"):
        assert compose["services"][name].get("restart") == "unless-stopped"


# --------------------------------------------------------------------------- #
# Dockerfile — reproduzierbarer, unprivilegierter App-Container
# --------------------------------------------------------------------------- #
def test_dockerfile_pins_python_minor_not_latest(dockerfile):
    """Nachvollziehbare Basis (Minor-Pin) statt beweglichem :latest-Tag."""
    assert re.search(r"^FROM python:3\.\d+-slim", dockerfile, re.MULTILINE)
    assert ":latest" not in dockerfile


def test_dockerfile_runs_as_non_root(dockerfile):
    assert "USER appuser" in dockerfile
    # Der Nutzer muss existieren, bevor er verwendet wird.
    assert dockerfile.index("useradd") < dockerfile.index("USER appuser")


def test_dockerfile_runs_single_gunicorn_worker_with_threads(dockerfile):
    """Genau EIN Worker (BYOB-RAM-Sessions!) + Threads für Nebenläufigkeit."""
    assert re.search(r'"--workers",\s*"1"', dockerfile)
    assert '"--threads"' in dockerfile
    assert '"telegram_formatter.app:app"' in dockerfile


def test_dockerfile_healthcheck_hits_healthz(dockerfile):
    assert "HEALTHCHECK" in dockerfile
    assert "/healthz" in dockerfile


def test_dockerfile_installs_only_runtime_deps(dockerfile):
    """Kein Dev-Ballast, kein requirements-dev.txt im Produktions-Image."""
    assert "requirements.txt" in dockerfile
    assert "requirements-dev.txt" not in dockerfile
    assert "COPY telegram_formatter/" in dockerfile


# --------------------------------------------------------------------------- #
# Caddyfile — TLS-Management und Zugriffskontrolle (konkretes LAN-Beispiel)
# --------------------------------------------------------------------------- #
def test_caddyfile_serves_server_ip_with_automatic_tls(caddyfile):
    assert SERVER_IP in caddyfile
    assert "tls internal" in caddyfile  # LAN-CA: Ausstellung + Erneuerung


def test_caddyfile_acl_allows_only_documented_client(caddyfile):
    """Standard ist VERWEIGERN — nur der freigegebene Client (plus localhost
    für lokale Checks) kommt durch, alle anderen erhalten 403."""
    assert CLIENT_IP in caddyfile
    assert "remote_ip" in caddyfile
    assert "not remote_ip" in caddyfile
    assert "403" in caddyfile


def test_caddyfile_proxies_to_compose_app_service(caddyfile):
    assert "reverse_proxy app:5000" in caddyfile


def test_caddyfile_disables_admin_api(caddyfile):
    assert "admin off" in caddyfile


# --------------------------------------------------------------------------- #
# .env.example — vollständig, LAN-passend und garantiert geheimnisfrei
# --------------------------------------------------------------------------- #
def test_env_example_documents_every_app_variable(env_example):
    missing = [var for var in DOCUMENTED_ENV_VARS if f"{var}=" not in env_example]
    assert missing == []


def test_env_example_pins_demo_channel_and_proxy_hops(env_example):
    """Standard-Output-Kanal ist die öffentliche Demo-Gruppe; hinter Caddy
    gilt genau ein vertrauenswürdiger Hop (Compose erzwingt ihn zusätzlich)."""
    assert "TELEGRAM_FORMATTER_SHARED_CHAT_URL=https://t.me/mdtotxt_bot_web" in env_example
    assert "TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS=1" in env_example


def test_env_example_contains_no_token_shaped_secrets(env_example):
    """Vertrag für den Gitleaks-CI: In der Vorlage darf nichts stehen, was
    wie ein echtes Bot-Token (Bot-ID: 35-Zeichen-Secret) aussieht."""
    token_shaped = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{35}")
    offenders = [line for line in env_example.splitlines() if token_shaped.search(line)]
    assert offenders == []
