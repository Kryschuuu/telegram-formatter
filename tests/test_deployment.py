"""Vertragstests für beide Deployment-Wege (Docker/Caddy seit v2.12.0,
Render-Blueprint seit v2.14.1).

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

Der ``render.yaml``-Teil (v2.14.1) prüft den Blueprint gegen die Blueprint-Spec
und — vor allem — gegen die *Implementierung*: der Health-Check-Pfad muss eine
tatsächlich registrierte Route sein, und jeder ``envVars``-Schlüssel muss vom
Code gelesen werden. Beides sind Drift-Klassen, die sonst erst im Dashboard
auffallen, wo niemand mehr einen Testlauf macht.

Der Compose-/Blueprint-Teil braucht PyYAML (requirements-dev.txt); fehlt es,
wird er übersprungen — Dockerfile/Caddyfile/.env-Prüfungen laufen immer (reine
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
RENDER_YAML_PATH = REPO_ROOT / "render.yaml"
APP_PY_PATH = REPO_ROOT / "telegram_formatter" / "app.py"
CHECK_BUILD_PATH = REPO_ROOT / "scripts" / "check_build.py"

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


@pytest.fixture(scope="module")
def blueprint() -> dict:
    yaml = pytest.importorskip("yaml", reason="PyYAML nur in requirements-dev.txt")
    return yaml.safe_load(RENDER_YAML_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def blueprint_service(blueprint: dict) -> dict:
    services = blueprint["services"]
    assert len(services) == 1, "der Blueprint soll genau einen Dienst deklarieren"
    return services[0]


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


# --------------------------------------------------------------------------- #
# render.yaml — der Blueprint gegen Spec UND gegen die Implementierung
#
# Motivation (v2.14.1): ein Blueprint, der im Dashboard scheitert, liefert
# dort keine brauchbare Fehlermeldung. Diese Tests verschieben die Diagnose
# dorthin, wo sie hingehört — ins Repository.
# --------------------------------------------------------------------------- #
#: Feldnamen, die die Blueprint-Spec für einen Web Service kennt. Quelle:
#: https://render.com/docs/blueprint-spec (Abschnitt "Service fields"). Ein
#: Tippfehler wie ``healthcheckPath`` fällt sonst *nicht* auf: die Spec
#: erlaubt unbekannte Felder still, und Render startet den Dienst ohne
#: Health-Check — der Container wird dann endlos neu gestartet, ohne dass
#: irgendwo eine Fehlermeldung steht.
KNOWN_SERVICE_FIELDS: frozenset[str] = frozenset(
    {
        "name", "type", "runtime", "plan", "branch", "repo", "region",
        "buildCommand", "startCommand", "preDeployCommand", "autoDeployTrigger",
        "numInstances", "scaling", "healthCheckPath", "envVars", "disk",
        "domains", "renderSubdomainPolicy", "maxShutdownDelaySeconds",
        "maintenanceMode", "initialDeployHook", "buildFilter", "rootDir",
        "ipAllowList", "previews", "staticPublishPath", "headers", "routes",
        "image", "dockerCommand", "dockerfilePath", "dockerContext",
        "registryCredential", "schedule",
    }
)

#: Werte, die die Spec als Aufzählung kennt. Wieder ein stiller Fehlerraum:
#: ein unbekanntes ``region`` wird nicht als Tippfehler gemeldet, sondern
#: schlicht nicht angewendet — der Dienst landet dann in oregon, obwohl
#: frankfurt dasteht.
ALLOWED_TYPE = frozenset({"web", "pserv", "worker", "cron", "keyvalue", "workflow"})
ALLOWED_RUNTIME = frozenset(
    {"node", "python", "ruby", "go", "elixir", "rust", "docker", "image", "static"}
)
ALLOWED_REGION = frozenset({"oregon", "ohio", "virginia", "frankfurt", "singapore"})
ALLOWED_PLAN = frozenset(
    {"free", "0.5c-512mb", "1c-2g", "2c-4g", "2c-8g", "2c-16g", "4c-8g",
     "4c-16g", "4c-32g", "8c-16g", "8c-32g", "8c-64g", "12c-24g", "12c-48g",
     "12c-96g"}
)
ALLOWED_AUTODEPLOY = frozenset({"commit", "checksPass", "off"})

#: Schemata, die in ``environment`` des Compose-Stacks gesetzt werden dürfen —
#: dieselben drei, die der Blueprint setzt. Render liefert exakt einen
#: vertrauenswürdigen Proxy-Hop hinter Caddy tut Render auch, und die
#: IP-Rate-Limits sehen sonst die Proxy-Adresse statt der Client-Adresse.
SHARED_DEMO_ENV = (
    "TELEGRAM_FORMATTER_SHARED_WEB_SEND",
    "TELEGRAM_FORMATTER_SHARED_BOT_HANDLE",
    "TELEGRAM_FORMATTER_SHARED_CHAT_URL",
    "TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS",
    "TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS",
)

#: Die drei echten Geheimnisse. Sie dürfen ausschließlich ``sync: false``
#: haben — nie einen ``value``.
SECRET_ENV_KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_FORMATTER_API_TOKEN",
)

#: Formen, in denen app.py eine Umgebungsvariable liest. Wer eine neue
#: Zugriffsform erfindet, muss die Liste hier erweitern — sonst meldet
#: ``test_render_yaml_env_keys_are_read_by_app`` fälschlich "toter Eintrag".
ENV_READ_PATTERNS = (
    r'_env_\w+\(\s*"([A-Z0-9_]+)"',
    r'os\.environ\.get\(\s*"([A-Z0-9_]+)"',
    r'os\.environ\[\s*"([A-Z0-9_]+)"\s*\]',
    r'os\.getenv\(\s*"([A-Z0-9_]+)"',
)


def _env_keys_read_by_app() -> set[str]:
    """Alle ENV-Schlüssel, die app.py tatsächlich liest."""
    source = APP_PY_PATH.read_text(encoding="utf-8")
    keys: set[str] = set()
    for pattern in ENV_READ_PATTERNS:
        keys |= set(re.findall(pattern, source))
    return keys


def test_render_yaml_health_path_is_a_registered_route(blueprint_service):
    """``healthCheckPath`` muss eine Route sein, die app.py wirklich hat.

    Das ist der wichtigste Test im Blueprint-Abschnitt. Ein Tippfehler in
    *einer* der beiden Dateien bedeutet: Render pollt einen Pfad, den es
    nicht gibt, und startet den Container endlos neu — ohne Fehlermeldung
    im Build-Log, weil der Build erfolgreich war.
    """
    from telegram_formatter.app import app

    path = blueprint_service["healthCheckPath"]
    assert path.startswith("/"), "healthCheckPath muss mit / beginnen"
    registered = {rule.rule for rule in app.url_map.iter_rules()}
    assert path in registered, (
        f"healthCheckPath {path!r} ist nicht registriert; vorhanden: "
        f"{sorted(registered)}"
    )


def test_render_yaml_health_path_matches_docker_healthcheck(blueprint_service, dockerfile):
    """Beide Deployment-Wege müssen dieselbe Route prüfen.

    Sonst hängt die eine Instanz an ``/healthz`` und die andere an ``/`` —
    und das fällt nur auf, wenn ausgerechnet diese Instanz ausfällt.
    """
    # Das HEALTHCHECK-Kommando ist über Zeilen umbrüche fortgesetzt, deshalb
    # wird der Block als Ganzes gemustert statt Zeile für Zeile.
    probe = re.search(r"HEALTHCHECK(?:.|\n)*?urlopen\(\s*'([^']+)'", dockerfile)
    assert probe, "Dockerfile hat kein HEALTHCHECK mit urlopen(...)"
    checked_url = probe.group(1)
    assert blueprint_service["healthCheckPath"] in checked_url, (
        f"Blueprint prüft {blueprint_service['healthCheckPath']!r}, Dockerfile "
        f"prüft {checked_url!r} — die Deployments überwachen verschiedene Routen"
    )


def test_render_yaml_start_command_uses_canonical_module(blueprint_service):
    """``telegram_formatter.app:app`` — nicht den Root-Shim ``app:app``."""
    start = blueprint_service["startCommand"]
    assert "telegram_formatter.app:app" in start
    # ``app:app`` würde als Substring auch im kanonischen Namen stecken;
    # deshalb explizit gegen den nackten Shim prüfen.
    assert not re.search(r"(?<!telegram_formatter\.)app:app", start), (
        "der Root-Shim app.py ist veraltet und entfällt mit 3.0.0"
    )
    assert "--bind 0.0.0.0:$PORT" in start, "Render lauscht nur auf $PORT"
    # Genau EIN Worker: BYOB-Sessions leben prozesslokal im RAM.
    assert "--workers" not in start, (
        "ein zweiter Worker würde BYOB-Sessions verlieren (410 statt Fehlversand)"
    )
    assert "--threads" in start, "Nebenläufigkeit kommt über --threads (gthread)"


def test_render_yaml_start_command_matches_documentation(blueprint_service):
    """Was der Blueprint startet, steht so auch in docs/DEPLOYMENT.md.

    Sonst liest jemand die Anleitung, baut etwas anderes und wundert sich
    über ``ModuleNotFoundError`` (dokumentierter erster Troubleshooting-Fall).
    """
    doc = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert blueprint_service["startCommand"] in doc, (
        "der Start-Befehl im Blueprint weicht von docs/DEPLOYMENT.md ab"
    )


def test_render_yaml_build_command_runs_the_self_check(blueprint_service):
    """Der Build prüft sich selbst, bevor der Dienst startet.

    Fehlt ein Asset (z. B. KaTeX, 596 KB, seit v2.14.0), fällt das sonst erst
    zur Laufzeit auf: die Seite lädt 404, und der einzige Ort, an dem das
    auffällt, ist die Sprechblase im Browser eines Nutzers.
    """
    build = blueprint_service["buildCommand"]
    assert "requirements.txt" in build, "Abhängigkeiten müssen installiert werden"
    assert "check_build.py" in build, "der Build-Selbsttest läuft nicht mit"
    assert CHECK_BUILD_PATH.is_file(), "scripts/check_build.py fehlt im Repository"
    # requirements-dev.txt gehört ausdrücklich NICHT ins Produktions-Image.
    assert "requirements-dev.txt" not in build


def test_render_yaml_only_uses_known_service_fields(blueprint_service):
    """Keine Tippfehler in Feldnamen — die Spec ignoriert sie still."""
    unknown = sorted(set(blueprint_service) - KNOWN_SERVICE_FIELDS)
    assert unknown == [], (
        f"unbekannte Felder im Blueprint: {unknown}. Die Blueprint-Spec kennt "
        f"für einen Web Service: {sorted(KNOWN_SERVICE_FIELDS)}"
    )


def test_render_yaml_field_values_are_in_the_spec(blueprint_service):
    """``type``/``runtime``/``region``/``plan``/``autoDeployTrigger`` sind enums."""
    assert blueprint_service["type"] in ALLOWED_TYPE
    assert blueprint_service["runtime"] in ALLOWED_RUNTIME
    assert blueprint_service.get("region") in ALLOWED_REGION, (
        "region fehlt oder ist unbekannt — ohne sie landet der Dienst in "
        "oregon (USA), und region ist nach dem Anlegen nicht mehr änderbar"
    )
    assert blueprint_service["plan"] in ALLOWED_PLAN
    if "autoDeployTrigger" in blueprint_service:
        assert blueprint_service["autoDeployTrigger"] in ALLOWED_AUTODEPLOY


def test_render_yaml_declares_eu_region_explicitly(blueprint_service):
    """Frankfurt ist eine bewusste Entscheidung, kein Default.

    Render setzt sonst still ``oregon`` (USA) — und die Region lässt sich
    nach dem Anlegen **nicht mehr ändern** ("You can't modify this value
    after creation"). Ein Versäumnis hier ist also endgültig, bis der
    Dienst gelöscht und neu angelegt wird.
    """
    assert blueprint_service.get("region") == "frankfurt"


def test_render_yaml_does_not_pin_branch(blueprint_service):
    """``branch:`` bleibt weg — Render nimmt ohnehin den Blueprint-Branch.

    Die Spec: *"Render uses the Blueprint's branch if the service uses the
    same repo as the Blueprint file."* Gepinnt werden kann der Wert also nur
    zusätzlich stören; genau dieses Feld liefert in Renders eigenem
    Blueprint-Beispiel den Fehler ``branch prod could not be found``.
    """
    assert "branch" not in blueprint_service, (
        "branch nicht pinnen — der Blueprint-Branch wird ohnehin verwendet, "
        "und ein gepinnter Wert erzeugt nur 'branch could not be found'"
    )


def test_render_yaml_pins_single_instance(blueprint_service):
    """Genau eine Instanz, wegen der RAM-residenten BYOB-Sessions.

    Dieselbe Begründung wie beim einzelnen Gunicorn-Worker: eine zweite
    Instanz „verliert" eine offene Session (410 statt Fehlversand).
    """
    assert blueprint_service.get("numInstances") == 1


def test_render_yaml_declares_python_version(blueprint_service):
    """Build-Umgebung auf eine Python-Version pinnen, die zu pyproject passt."""
    env = {e["key"]: e for e in blueprint_service["envVars"]}
    assert env["PYTHON_VERSION"]["value"] == "3.11"


def test_render_yaml_health_path_is_not_rate_limited(blueprint_service):
    """/healthz statt / — billiger und nie ratenlimitiert (seit v2.12.0).

    Wäre ``/`` der Health-Check, würde jeder Probe-Request ein Template
    rendern *und* gegen das Ratelimit laufen; ein ausgelastetes Limit lässt
    Render den Container dann als ungesund einstufen und neu starten.
    """
    assert blueprint_service["healthCheckPath"] != "/"


def test_render_yaml_secrets_use_sync_false(blueprint_service):
    """Echte Secrets niemals mit ``value:`` im Git."""
    env = {e["key"]: e for e in blueprint_service["envVars"]}
    for key in SECRET_ENV_KEYS:
        entry = env.get(key)
        assert entry is not None, f"{key} fehlt im Blueprint"
        assert "value" not in entry, (
            f"{key} steht mit Klartext im Blueprint — Geheimnisse gehören "
            f"mit 'sync: false' in den Dashboard-Store"
        )
        assert entry.get("sync") is False, f"{key} muss 'sync: false' haben"


def test_render_yaml_does_not_commit_token_shaped_secrets(blueprint_service):
    """Vertrag für den Gitleaks-CI, auf Blueprint-Ebene.

    Ein Bot-Token im Blueprint wäre ein Leak, das erst auffällt, wenn der
    Token im Telegram-Channel umbenannt werden muss.
    """
    token_shaped = re.compile(r"[0-9]{5,16}:[A-Za-z0-9_-]{35}")
    text = RENDER_YAML_PATH.read_text(encoding="utf-8")
    assert token_shaped.search(text) is None


#: Variablen, die der Blueprint **mit Wert** pinnt. Bewusst eine kurze
#: Whitelist: sie macht sichtbar, welche Deployment-Entscheidungen das
#: Projekt überhaupt trifft, und lässt eine neue Variable nicht still
#: hinzukommen.
#:
#: Vier der fünf wiederholen dabei den Default aus app.py. Das ist Absicht
#: und kein Versehen: der Blueprint ist gleichzeitig die *Dokumentation* der
#: Demo-Posture — ein Betreiber sieht an der Konfiguration, dass der geteilte
#: Bot im Browser aktiv und der Kanal öffentlich gepinnt ist, ohne Python zu
#: lesen. Weglassen wäre die eigentliche Redundanz: die Aussage ginge dann
#: nur noch an einer Stelle im Code.
#:
#: Der einzige Wert, der den Default tatsächlich **übersteuert**, ist
#: ``TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS`` (Code-Default 0, Render 1).
BLUEPRINT_PINNED_VALUES: frozenset[str] = frozenset(
    {
        "TELEGRAM_FORMATTER_SHARED_WEB_SEND",
        "TELEGRAM_FORMATTER_SHARED_BOT_HANDLE",
        "TELEGRAM_FORMATTER_SHARED_CHAT_URL",
        "TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS",
        "TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS",
    }
)


def test_render_yaml_env_keys_are_read_by_app(blueprint_service):
    """Jeder Blueprint-Schlüssel wird vom Code auch tatsächlich gelesen.

    Das ist die Drift-Klasse, die sonst erst im Dashboard auffällt: ein
    Schlüssel wird in ``app.py`` umbenannt, der Blueprint behält den alten —
    und die Variable steht im Dashboard, ohne Wirkung.

    Geprüft wird bewusst nur diese Richtung. Die Gegenrichtung wäre falsch:
    app.py liest ~20 optionale Stellschrauben (Rate-Limits, TTLs,
    Chunk-Obergrenzen), die der Blueprint *nicht* pinnt — und nicht pinnen
    soll. Sie haben brauchbare Vorgaben und gehören in die Umgebung des
    Betreibers, nicht in die Projektkonfiguration.

    ``PYTHON_VERSION`` ist die einzige Ausnahme: sie wird von Renders
    Build-Umgebung ausgewertet, nicht von der Anwendung.
    """
    blueprint_keys = {e["key"] for e in blueprint_service["envVars"]}
    read_keys = _env_keys_read_by_app()
    render_only = {"PYTHON_VERSION"}

    dead = sorted(blueprint_keys - read_keys - render_only)
    assert dead == [], (
        f"im Blueprint, aber von app.py nie gelesen: {dead}. Entweder ist die "
        f"Variable umbenannt (dann Blueprint anpassen) oder sie ist tot "
        f"(dann entfernen)."
    )


def test_render_yaml_pins_only_deliberate_values(blueprint_service):
    """Der Blueprint pinnt nur die dokumentierte Demo-Konfiguration.

    Jede neue gepinnte Variable ist eine Deployment-Entscheidung und soll
    eine bewusste sein. Ohne diese Whitelist könnte ein dritter Wert, der
    den Default bloß wiederholt, unbemerkt dazukommen — und dieselbe Aussage
    stünde dann in app.py, render.yaml *und* .env.example.
    """
    pinned = {
        e["key"] for e in blueprint_service["envVars"] if "value" in e
    } - {"PYTHON_VERSION"}
    assert pinned == set(BLUEPRINT_PINNED_VALUES), (
        f"gepinnte Variablen weichen ab. Erwartet: "
        f"{sorted(BLUEPRINT_PINNED_VALUES)} — neu: {sorted(pinned - set(BLUEPRINT_PINNED_VALUES))}, "
        f"entfallen: {sorted(set(BLUEPRINT_PINNED_VALUES) - pinned)}"
    )


def test_render_yaml_proxy_hops_is_the_only_real_override(blueprint_service):
    """Nur der Proxy-Hop weicht vom Code-Default ab — und das aus gutem Grund.

    ``TRUSTED_PROXY_HOPS`` steht auf 0 (sicherer Direktbetrieb) und wird
    nur für Render auf 1 gesetzt, weil dort genau ein vertrauenswürdiger
    Hop vor dem Dienst steht. Bliebe er auf 0, sähen die IP-Rate-Limits
    die Proxy-Adresse statt der Client-Adresse — ein gemeinsamer Eimer für
    alle Besucher.
    """
    env = {e["key"]: e.get("value") for e in blueprint_service["envVars"]}
    assert env["TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS"] == "1"
    assert env["TELEGRAM_FORMATTER_SHARED_WEB_SEND"] == "1"


def test_render_yaml_env_values_are_strings(blueprint_service):
    """``value:`` muss ein String sein.

    ``value: 30`` parst als YAML-``int`` und Render lehnt den Blueprint ab —
    die Datei sieht dabei völlig harmlos aus.
    """
    for entry in blueprint_service["envVars"]:
        if "value" in entry:
            assert isinstance(entry["value"], str), (
                f"{entry['key']}: value muss in Anführungszeichen stehen, "
                f"sonst parst es als Zahl/Bool und Render lehnt den Blueprint ab"
            )


def test_render_yaml_env_entries_are_well_formed(blueprint_service):
    """Jeder envVar-Eintrag braucht eine der von der Spec erlaubten Formen."""
    allowed = {"value", "sync", "generateValue", "fromDatabase", "fromService"}
    for entry in blueprint_service["envVars"]:
        assert "key" in entry, f"Eintrag ohne 'key': {entry}"
        unknown = sorted(set(entry) - allowed - {"key"})
        assert unknown == [], f"{entry['key']}: unbekannte Felder {unknown}"
        # Entweder ein Wert, oder genau eine der Wert-Quellen.
        sources = allowed & set(entry)
        assert len(sources) == 1, (
            f"{entry['key']}: genau eine Wert-Quelle erwartet, gefunden {sources}"
        )


def test_render_yaml_pins_demo_configuration(blueprint_service):
    """Öffentliche Demo: geteilter Bot, gepinnter Kanal, ein Proxy-Hop."""
    env = {e["key"]: e["value"] for e in blueprint_service["envVars"] if "value" in e}
    for key in SHARED_DEMO_ENV:
        assert key in env, f"{key} fehlt im Blueprint"
    assert env["TELEGRAM_FORMATTER_SHARED_WEB_SEND"] == "1"
    assert env["TELEGRAM_FORMATTER_SHARED_CHAT_URL"] == "https://t.me/mdtotxt_bot_web"
    assert env["TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS"] == "1"


def test_render_yaml_demo_values_agree_with_env_example(blueprint_service, env_example):
    """Blueprint und .env.example dürfen nicht auseinanderlaufen.

    Dieselbe Variable, zwei Deploy-Wege, zwei Orte an denen sie gepflegt
    wird. Hier ist die Kopplung über die konkreten Werte statt über eine
    zweite Liste: der Test nennt die Schlüssel, die **dieselbe** Demo
    beschreiben.
    """
    env = {e["key"]: e["value"] for e in blueprint_service["envVars"] if "value" in e}
    for key in ("TELEGRAM_FORMATTER_SHARED_CHAT_URL", "TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS"):
        assert f"{key}={env[key]}" in env_example, (
            f"{key}={env[key]} steht im Blueprint, aber nicht in .env.example — "
            f"die beiden Deploy-Wege würden unterschiedlich konfiguriert"
        )
