"""Tests für die Flask-Weboberfläche (``app``)."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from telegram_formatter import __version__
from telegram_formatter import app as app_module

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIM_PATH = REPO_ROOT / "app.py"


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


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


def test_send_missing_token(client):
    app_module.BOT_TOKEN = ""
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
