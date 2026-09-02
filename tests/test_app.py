"""Tests für die Flask-Weboberfläche (``app``)."""

from __future__ import annotations

import pytest

import app as app_module


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c


def test_index_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Telegram Formatter" in resp.data


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
