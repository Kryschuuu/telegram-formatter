"""Tests für ``botkit.telegram_api`` — Fehlertoleranz gegen unerwartete Antworten.

Neu in v2.11.1: Die Netzwerkschicht war nur indirekt (über gemockte
``verify``-Funktionen) getestet; der Review ergänzt direkte Tests für
nicht-dict-Antworten und die token-freie Fehlerklassifizierung.
"""

from __future__ import annotations

import sys
import types

import pytest

from telegram_formatter.botkit.telegram_api import TelegramAPIError, get_me

SECRET = "123456789:" + "A" * 35


def _install_fake_requests(monkeypatch, post):
    fake = types.SimpleNamespace(RequestException=ConnectionError, post=post)
    monkeypatch.setitem(sys.modules, "requests", fake)


def test_non_dict_json_body_is_clean_error(monkeypatch):
    """Eine JSON-Antwort ohne Objekt (z. B. Proxy-Fehlerseite als Liste) meldet
    TelegramAPIError statt rohem AttributeError."""
    calls: list[dict] = []

    def post(url, json=None, timeout=None):
        calls.append({"url": url})
        return types.SimpleNamespace(status_code=200, json=lambda: ["kein", "dict"])

    _install_fake_requests(monkeypatch, post)
    with pytest.raises(TelegramAPIError, match="Ungültige JSON-Antwort"):
        get_me(SECRET)
    assert calls[0]["url"].endswith("/bot" + SECRET + "/getMe")


def test_network_error_mentions_only_class_name(monkeypatch):
    """Netzwerkfehler nennen nur die Exception-Klasse — nie URL oder Token."""
    url_with_token = f"https://api.telegram.org/bot{SECRET}/getMe"

    def post(url, json=None, timeout=None):
        raise ConnectionError(f"Max retries exceeded with url: {url_with_token}")

    _install_fake_requests(monkeypatch, post)
    with pytest.raises(TelegramAPIError) as excinfo:
        get_me(SECRET)
    assert "ConnectionError" in str(excinfo.value)
    assert SECRET not in str(excinfo.value)
    assert "api.telegram.org" not in str(excinfo.value)


def test_ok_false_reports_short_description(monkeypatch):
    """API-Ablehnung (ok:false) übernimmt nur die gekürzte Description."""

    def post(url, json=None, timeout=None):
        return types.SimpleNamespace(
            status_code=200,
            json=lambda: {"ok": False, "description": "Unauthorized: bot wurde gelöscht"},
        )

    _install_fake_requests(monkeypatch, post)
    with pytest.raises(TelegramAPIError, match="Unauthorized"):
        get_me(SECRET)
