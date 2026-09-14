"""Tests für die öffentliche Demo-Chat-Konfiguration (public Supergroup/Channel).

Die Privatsphäre-Warnung auf der Seite muss **wörtlich korrekt** sein, sobald
der geteilte Bot in einen *öffentlichen* Zielchat sendet: dann kann jeder, der
die Gruppe oder den Kanal auf Telegram öffnet, den gesamten Verlauf lesen. Dies
ist der empfohlene Aufbau für die gehostete Demo (siehe docs/DEPLOYMENT.md,
Schritt 4b) — die Warnung darf nicht mehr nur „wer den Bot hinzufügt“ sagen.
"""

from __future__ import annotations

import pathlib

import pytest

import telegram_formatter.app as app_module
from telegram_formatter import __version__

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
RENDER_YAML = REPO_ROOT / "render.yaml"


@pytest.fixture()
def client(monkeypatch):
    """Isolierte App: Modulglobals zurückgesetzt, Limits aus, Rate-Buckets leer."""
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


@pytest.fixture()
def public_demo_client(monkeypatch):
    """Demo-Instanz: geteilter Bot + gepinnter Chat, Browser-Versand an."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456:secretsecretsecretsecretsecretsec")
    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    monkeypatch.setattr(app_module, "SHARED_WEB_SEND", True)
    monkeypatch.setattr(app_module, "SHARED_BOT_HANDLE", "@mdtotxt_bot")
    monkeypatch.setattr(app_module, "MAX_INPUT_CHARS", 100_000)
    monkeypatch.setattr(app_module, "SENDS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "CONVERTS_PER_MINUTE", 0)
    app_module._RATE_HITS.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
    app_module._RATE_HITS.clear()


def test_index_warning_reflects_public_chat(public_demo_client):
    """Die Top-Warnung ist für einen öffentlichen Chat wörtlich korrekt."""
    page = public_demo_client.get("/").data.decode("utf-8")
    # Die Top-Warnung wird nur bei konfiguriertem Bot gerendert.
    assert 'class="tf-top-warning"' in page
    # Öffentlicher Charakter des Zielchats ...
    assert "öffentlichen" in page
    assert "gemeinsamen Chat" in page
    # ... und die korrekte Sichtbarkeitsaussage: jeder, der die Gruppe/Kanal
    # öffnet, liest den *gesamten* Verlauf (auch nachträglich).
    assert "Gruppe oder den Kanal" in page
    assert "gesamten Verlauf" in page
    # Die alte, für private Chats zugeschnittene Formulierung ist verschwunden.
    assert "den Bot auf Telegram hinzufügt" not in page


def test_index_warning_absent_when_unconfigured(client):
    """Ohne konfigurierten Bot gibt es keinen öffentlichen Chat und keine Top-Warnung."""
    page = client.get("/").data.decode("utf-8")
    assert 'class="tf-top-warning"' not in page
    assert "gesamten Verlauf" not in page


def test_send_confirmation_warning_names_public_channel(client, monkeypatch):
    """Auch der Sende-Bestätigungsdialog nennt den öffentlichen Chat korrekt."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456:secretsecretsecretsecretsecretsec")
    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    monkeypatch.setattr(app_module, "SHARED_WEB_SEND", True)
    monkeypatch.setattr(app_module, "SHARED_BOT_HANDLE", "@mdtotxt_bot")
    app_module._RATE_HITS.clear()
    page = client.get("/").data.decode("utf-8")
    assert "öffentlichen, gemeinsamen Chat" in page
    assert "Gruppe oder den Kanal" in page


def test_render_yaml_pins_public_chat_and_shared_bot():
    """Der Blueprint deklariert den gepinnten Zielchat und den geteilten Bot."""
    assert RENDER_YAML.is_file(), "render.yaml fehlt — der kanonische Start ist undefiniert."
    text = RENDER_YAML.read_text(encoding="utf-8")
    # Gepinnter Zielchat (ohne Wert im Git — sync: false).
    assert "TELEGRAM_CHAT_ID" in text
    # Browser-Versand für den geteilten Bot ist der Demo-Standard.
    assert "TELEGRAM_FORMATTER_SHARED_WEB_SEND" in text
    # Anzeige-Name des geteilten Bots in der Warnung.
    assert "TELEGRAM_FORMATTER_SHARED_BOT_HANDLE" in text
    assert "@mdtotxt_bot" in text


def test_render_yaml_documents_public_supergroup_setup():
    """Die Deployment-Hinweise empfehlen explizit die public Supergroup/Channel."""
    text = RENDER_YAML.read_text(encoding="utf-8")
    assert "public" in text.lower()
    assert "Supergroup" in text


def test_version_bumped_to_2_8_0():
    assert __version__ == "2.8.0"
