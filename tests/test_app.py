"""Tests für die Flask-Weboberfläche (``app``).

Der Fixture-Block härzt gegen die Befunde des Security-Audits 2026-09:
Chat-Pinning, Input-Validierung, Größen-/Mengenlimits, API-Token,
Origin-Check, Security-Header und „Fehler enthalten niemals den Token".
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from telegram_formatter import __version__
from telegram_formatter import app as app_module
from telegram_formatter.sender import SendError

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIM_PATH = REPO_ROOT / "app.py"


@pytest.fixture()
def client(monkeypatch):
    """Isolierte App: Modulglobals zurücksetzen, Limits aus, Rate-Buckets leer."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "")
    monkeypatch.setattr(app_module, "CHAT_ID", "")
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    monkeypatch.setattr(app_module, "MAX_INPUT_CHARS", 100_000)
    monkeypatch.setattr(app_module, "SENDS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "CONVERTS_PER_MINUTE", 0)
    app_module._RATE_HITS.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
    app_module._RATE_HITS.clear()


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Telegram Formatter" in resp.data


def test_index_contains_new_ui_elements(client):
    """Reset-Button, Coffee-Link, Disclaimer und Howto/FAQ sind vorhanden."""
    resp = client.get("/")
    page = resp.data.decode("utf-8")
    # Reset-Button
    assert 'id="resetBtn"' in page
    assert "Zurücksetzen" in page
    # Buy-me-a-coffee: Header + Footer, sicherer Extern-Link
    assert "https://buymeacoffee.com/rg4free" in page
    assert 'target="_blank"' in page
    assert 'rel="noopener"' in page
    # Disclaimer (Hinweisbox + Kurzform im Footer)
    assert "Haftungsausschluss" in page
    assert "Keine Datenspeicherung" in page
    # Howto & FAQ
    assert 'id="howto"' in page
    assert 'id="faq"' in page
    assert page.count("<details") >= 7  # sieben aufklappbare Akkordeons


def test_assets_are_self_hosted_no_inline_script(client):
    """Audit H-5 + Redesign 2026-09: gar keine Fremdnetze mehr.

    Der Tailwind-Play-CDN war die Ursache des Design-Bruchs: er injizierte
    Inline-<style>-Regeln, die die CSP (ohne 'unsafe-inline') blockierte —
    die Seite fiel auf ungestylten Rohtext zurück. Seit dem Redesign liegt
    das komplette Design (4 CSS-Schichten + Theme-/Editor-JS) selbst-gehostet
    unter ``static/``; die CSP bleibt strikt ``'self'``.
    """
    page = client.get("/").data.decode("utf-8")
    assert "<script>" not in page.replace("<script src", "<script_src")
    assert "<style" not in page
    assert 'style="' not in page
    for asset in (
        "css/tokens.css",
        "css/base.css",
        "css/layout.css",
        "css/components.css",
        "js/theme.js",
        "js/app.js",
    ):
        assert f"static/{asset}" in page, f"fehlendes Asset: {asset}"
    # Frühere CDN-Quellen müssen spurlos entfernt sein.
    for host in ("cdn.tailwindcss.com", "cdnjs.cloudflare.com", "unpkg.com", "jsdelivr"):
        assert host not in page


def test_security_headers_present(client):
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    # Selbst-gehostetes Design: keine externen Hosts mehr in der CSP.
    assert "cdn." not in csp
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"


def test_convert_regular(client):
    resp = client.post("/api/convert", json={"text": "**fett** text"})
    data = resp.get_json()
    assert data["count"] == 1
    assert data["messages"][0]["kind"] == "regular"
    assert "<b>fett</b>" in data["messages"][0]["payload"]["text"]


def test_convert_rich_math(client):
    resp = client.post("/api/convert", json={"text": "$x^2$"})
    data = resp.get_json()
    assert data["messages"][0]["kind"] == "rich"
    assert "markdown" in data["messages"][0]["payload"]["rich_message"]


def test_convert_empty_text(client):
    resp = client.post("/api/convert", json={"text": "   "})
    assert resp.get_json()["count"] == 0


# --------------------------------------------------------------------------- #
# K-2 / H-2 / B-5: Eingabevalidierung und Chat-Pinning
# --------------------------------------------------------------------------- #
def test_convert_rejects_non_string_text(client):
    """B-5 (Audit): früher 500 durch AttributeError — jetzt saubere 400."""
    resp = client.post("/api/convert", json={"text": 12345})
    assert resp.status_code == 400
    assert "String" in resp.get_json()["error"]


def test_convert_rejects_oversized_text(client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_INPUT_CHARS", 10)
    resp = client.post("/api/convert", json={"text": "x" * 11})
    assert resp.status_code == 400
    assert "zu lang" in resp.get_json()["error"]


def test_convert_rejects_non_numeric_chat_id(client):
    """Audit K-2: chat_id darf kein beliebiges JSON-Objekt sein."""
    resp = client.post("/api/convert", json={"text": "hi", "chat_id": {"$gt": ""}})
    assert resp.status_code == 400


def test_send_ignores_body_chat_id_when_pinned(client, monkeypatch):
    """K-2 (kritisch): mit konfiguriertem CHAT_ID ist der Zielchat NICHT
    überschreibbar — andernfalls wäre der Endpunkt ein offener Relay."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456:secretsecretsecretsecretsecretsec")
    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    seen = []
    monkeypatch.setattr(app_module, "send_message",
                        lambda m, t, **kw: seen.append(m.payload["chat_id"]) or {"ok": True})

    resp = client.post("/api/send", json={"text": "hallo", "chat_id": "42"})
    assert resp.status_code == 400  # Fremd-Chat abgelehnt

    resp = client.post("/api/send", json={"text": "hallo"})
    assert resp.status_code == 200
    assert seen == ["-100999"]


def test_send_requires_valid_chat_in_selfhosted_mode(client, monkeypatch):
    """Ohne ENV-Chat (Selbstbetrieb): numerische chat_id im Body ist Pflicht."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456:x")
    seen = []
    monkeypatch.setattr(
        app_module, "send_message",
        lambda m, t, **kw: seen.append(m.payload["chat_id"]) or {"ok": True},
    )
    resp = client.post("/api/send", json={"text": "hallo"})
    assert resp.status_code == 400  # ohne chat_id gar nicht sendefähig
    resp = client.post("/api/send", json={"text": "hallo", "chat_id": "77"})
    assert resp.status_code == 200
    assert seen == ["77"]
    resp = client.post("/api/send", json={"text": "hallo", "chat_id": "4;2"})
    assert resp.status_code == 400  # numerisches Format erzwungen


def test_send_missing_token(client):
    resp = client.post("/api/send", json={"text": "hallo"})
    assert resp.status_code == 400
    assert "TELEGRAM_BOT_TOKEN" in resp.get_json()["error"]


# ---------------------------------------------------------------------------
# Kompatibilitäts-Shim in der Repository-Wurzel (`app.py`)
#
# Nach der Paket-Reorganisation (2.0.0) lautet der kanonische WSGI-Einstieg
# `telegram_formatter.app:app`. Deployments mit noch im Hosting-Dashboard
# hinterlegtem Alt-Befehl `gunicorn app:app` brauchen das Root-Modul als
# reine Weiterleitung — diese beiden Tests sichern genau diese Eigenschaft:
# dieselbe App-Instanz, und sonst nichts.
# ---------------------------------------------------------------------------

# Alles, was im Shim *nicht* vorkommen darf: eigene Logik jeder Art.
_FORBIDDEN_NODES = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
    ast.Lambda,
    ast.If,
    ast.For,
    ast.While,
    ast.With,
    ast.Try,
    ast.Import,
    ast.Call,
    ast.AnnAssign,
)


def _load_shim_module():
    """Importiert das Root-Modul, ohne `telegram_formatter.app` zu verdrängen."""
    assert SHIM_PATH.is_file(), (
        "Root-Modul app.py fehlt: der Alt-Startbefehl `gunicorn app:app` würde "
        "auf Render mit ModuleNotFoundError crashen."
    )
    spec = importlib.util.spec_from_file_location("_telegram_formatter_root_shim", SHIM_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_shim_exposes_the_identical_wsgi_object():
    """`app:app` und `telegram_formatter.app:app` müssen dieselbe Instanz sein."""
    shim = _load_shim_module()

    # Identität statt Nachbau: nur so teilen sich beide Adressen Templates,
    # Konfiguration und die registrierten Routen — der Shim kopiert nichts.
    assert shim.app is app_module.app
    routes = {
        rule.rule for rule in shim.app.url_map.iter_rules() if rule.endpoint != "static"
    }
    assert routes == {"/", "/api/convert", "/api/send"}

    # Rauchtest wie auf Render: Startbefehl liefert die Seite, und der Footer
    # zieht die Version aus telegram_formatter.__version__ (nicht hartkodiert).
    resp = shim.app.test_client().get("/")
    assert resp.status_code == 200
    page = resp.data.decode("utf-8")
    assert f"Version {__version__}" in page
    assert "Version 1.2.0" not in page  # früherer, verdrifteter Footer-Wert


def test_root_shim_stays_a_pure_forwarder():
    """Der Shim darf nie zu einer zweiten Logik-Kopie werden (AST-Vertrag)."""
    tree = ast.parse(SHIM_PATH.read_text(encoding="utf-8"))

    offenders = [
        f"{type(node).__name__} (Zeile {node.lineno})"
        for node in ast.walk(tree)
        if isinstance(node, _FORBIDDEN_NODES)
    ]
    assert not offenders, f"Shim enthält Logik: {', '.join(offenders)}"

    forwarded = []
    for node in tree.body:
        # Docstring: muss den Shim als veraltet ausweisen (Entfernung 3.0.0).
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(
            node.value.value, str
        ):
            assert "veraltet" in node.value.value.lower(), "Shim muss als veraltet markiert sein"
            assert "3.0.0" in node.value.value, "Shim muss sein Verfallsdatum nennen (3.0.0)"
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            assert node.module == "telegram_formatter.app", (
                "Shim darf ausschließlich aus telegram_formatter.app importieren"
            )
            forwarded.extend(alias.name for alias in node.names)
            continue
        # Einzige erlaubte Zuweisung: das Re-Export-Statement gegen F401.
        if isinstance(node, ast.Assign) and [t.id for t in node.targets if isinstance(t, ast.Name)] == [
            "__all__"
        ]:
            assert [e.value for e in node.value.elts] == ["app"]
            continue
        pytest.fail(f"Unerwartete Anweisung im Shim: {type(node).__name__} (Zeile {node.lineno})")

    assert forwarded == ["app"], "Shim muss genau ein Objekt weiterleiten: app"
# --------------------------------------------------------------------------- #
# K-1/H-3: Fehlerpfade dürfen nichts Sensibles enthalten
# --------------------------------------------------------------------------- #
def test_send_error_body_never_contains_token(client, monkeypatch):
    token = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
    monkeypatch.setattr(app_module, "BOT_TOKEN", token)
    monkeypatch.setattr(app_module, "CHAT_ID", "-1")

    def boom(message, bot_token, **kw):
        raise SendError(f"Netzwerkfehler beim Versand ({'ConnectionError'}).")

    monkeypatch.setattr(app_module, "send_message", boom)
    resp = client.post("/api/send", json={"text": "hi"})
    assert resp.status_code == 502
    assert token not in resp.get_data(as_text=True)


def test_send_reports_partial_progress_and_backoff(client, monkeypatch):
    """B-6/B-7: bereits gesendete Chunks + retry_after werden kommuniziert."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123:x")
    monkeypatch.setattr(app_module, "CHAT_ID", "-1")
    calls = {"n": 0}

    def flaky(message, bot_token, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True}
        raise SendError("Telegram-API-Fehler 429: Too Many Requests.", retry_after=37)

    monkeypatch.setattr(app_module, "send_message", flaky)
    long_bold = "**" + "x" * 5_000 + "**"  # erzwingt > 1 Chunk
    resp = client.post("/api/send", json={"text": long_bold})
    data = resp.get_json()
    assert resp.status_code == 429
    assert data["retry_after"] == 37
    assert data["sent_before_error"] == 1
    assert "nicht" in data["note"] or "Wartezeit" in data["note"]


# --------------------------------------------------------------------------- #
# API-Token + Origin + Größenlimit + Rate-Limit
# --------------------------------------------------------------------------- #
def test_api_token_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(app_module, "API_TOKEN", "s3cret")
    resp = client.post("/api/convert", json={"text": "hi"})
    assert resp.status_code == 401
    resp = client.post("/api/convert", json={"text": "hi"}, headers={"X-Auth-Token": "nope"})
    assert resp.status_code == 401
    resp = client.post("/api/convert", json={"text": "hi"}, headers={"X-Auth-Token": "s3cret"})
    assert resp.status_code == 200


def test_foreign_origin_rejected(client):
    resp = client.post("/api/convert", json={"text": "hi"},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


def test_body_size_limit_rejected(client):
    """H-2: ohne MAX_CONTENT_LENGTH liest Flask unbegrenzt in den RAM."""
    assert app_module.app.config["MAX_CONTENT_LENGTH"] == app_module.MAX_BODY_BYTES
    big = b'{"text": "' + b"x" * (app_module.MAX_BODY_BYTES + 64) + b'"}'
    resp = client.post("/api/convert", data=big, content_type="application/json")
    assert resp.status_code == 413


def test_send_rate_limit_per_ip(client, monkeypatch):
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123:x")
    monkeypatch.setattr(app_module, "CHAT_ID", "-1")
    monkeypatch.setattr(app_module, "SENDS_PER_MINUTE", 2)
    monkeypatch.setattr(app_module, "send_message", lambda m, t, **kw: {"ok": True})

    assert client.post("/api/send", json={"text": "a"}).status_code == 200
    assert client.post("/api/send", json={"text": "b"}).status_code == 200
    blocked = client.post("/api/send", json={"text": "c"})
    assert blocked.status_code == 429


def test_unknown_route_returns_json_error(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    assert "error" in resp.get_json()
