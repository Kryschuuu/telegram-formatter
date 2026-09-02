"""Tests für den Versand-Layer (``sender``) mit gemocktem ``requests``."""

from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def fake_requests(monkeypatch):
    """Ersetzt ``requests`` durch ein Minimal-Stub mit aufzeichenbarem POST."""
    calls = []

    class Response:
        def __init__(self, status_code, text="", json_data=None):
            self.status_code = status_code
            self.text = text
            self._json = json_data or {}

        def json(self):
            return self._json

        def raise_for_status(self):
            pass

    class Requests:
        RequestException = ConnectionError

        def post(self, url, json=None, timeout=None):
            calls.append({"url": url, "json": json, "timeout": timeout})
            return Response(200, json_data={"ok": True})

    fake = Requests()
    monkeypatch.setitem(sys.modules, "requests", fake)
    return fake, calls


def test_send_regular_uses_sendmessage(fake_requests):
    from utils import TelegramMessage

    fake, calls = fake_requests
    from sender import send_message

    msg = TelegramMessage("regular", {"chat_id": 1, "text": "hi", "parse_mode": "HTML"})
    result = send_message(msg, "TOKEN")
    assert result == {"ok": True}
    assert calls[0]["url"] == "https://api.telegram.org/botTOKEN/sendMessage"


def test_send_rich_uses_sendrichmessage(fake_requests):
    from utils import TelegramMessage

    fake, calls = fake_requests
    from sender import send_message

    msg = TelegramMessage("rich", {"chat_id": 1, "rich_message": {"markdown": "$x$"}})
    send_message(msg, "TOKEN")
    assert calls[0]["url"] == "https://api.telegram.org/botTOKEN/sendRichMessage"


def test_send_api_error_raises(monkeypatch, fake_requests):
    fake, calls = fake_requests

    class ErrorResponse:
        status_code = 400
        text = "Bad Request"

        def json(self):
            return {}

    def bad_post(url, json=None, timeout=None):
        return ErrorResponse()

    fake.post = bad_post

    from utils import TelegramMessage

    import sender as sender_module
    from sender import SendError, send_message

    msg = TelegramMessage("regular", {"chat_id": 1, "text": "hi"})
    with pytest.raises(SendError):
        send_message(msg, "TOKEN")
