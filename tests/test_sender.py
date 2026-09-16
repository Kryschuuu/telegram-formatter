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
    from telegram_formatter.utils import TelegramMessage

    fake, calls = fake_requests
    from telegram_formatter.sender import send_message

    msg = TelegramMessage("regular", {"chat_id": 1, "text": "hi", "parse_mode": "HTML"})
    result = send_message(msg, "TOKEN")
    assert result == {"ok": True}
    assert calls[0]["url"] == "https://api.telegram.org/botTOKEN/sendMessage"


def test_send_rich_uses_sendrichmessage(fake_requests):
    from telegram_formatter.utils import TelegramMessage

    fake, calls = fake_requests
    from telegram_formatter.sender import send_message

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

    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    msg = TelegramMessage("regular", {"chat_id": 1, "text": "hi"})
    with pytest.raises(SendError):
        send_message(msg, "TOKEN")


# --------------------------------------------------------------------------- #
# Regressionstests aus dem Security-Audit 2026-09 (K-1, H-3, B-7, M-3)
# --------------------------------------------------------------------------- #
def _patch_module_post(monkeypatch, exc=None, response=None):
    """Ersetzt sowohl requests.post als auch Session.post (der Pool wird genutzt)."""
    import requests

    calls = []

    class _Session:
        def post(self, url, **kw):
            calls.append({"url": url, **kw})
            if exc:
                raise exc
            return response

    monkeypatch.setattr(requests, "Session", _Session)
    monkeypatch.setattr(requests, "post", lambda url, **kw: (_ for _ in ()).throw(exc) if exc else response)
    return calls


def test_network_error_message_never_contains_token(monkeypatch):
    """K-1: Exception-Texte von requests würden die URL inkl. Token enthalten."""
    import requests

    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    token = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
    _patch_module_post(
        monkeypatch,
        exc=requests.exceptions.ConnectionError(
            f"Max retries exceeded with url: https://api.telegram.org/bot{token}/sendMessage"
        ),
    )
    with pytest.raises(SendError) as ei:
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x", "parse_mode": "HTML"}), token)
    assert token not in str(ei.value)
    assert "api.telegram.org" not in str(ei.value)
    assert "ConnectionError" in str(ei.value)  # Klasse bleibt klassifizierbar


def test_http_error_exposes_only_short_description(monkeypatch):
    """H-3: response.text darf nicht durchgereicht werden, Description wird gekürzt."""
    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    class Resp:
        status_code = 400

        def json(self):
            return {"ok": False, "description": "D" * 5_000, "parameters": {}}

    _patch_module_post(monkeypatch, response=Resp())
    with pytest.raises(SendError) as ei:
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x"}), "T")
    msg = str(ei.value)
    assert "400" in msg
    assert msg.count("D") <= 200


def test_429_sets_retry_after(monkeypatch):
    """B-7: Telegram-Backoff wird als strukturierte Info geliefert."""
    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    class Resp:
        status_code = 429

        def json(self):
            return {"ok": False, "description": "Too Many Requests",
                    "parameters": {"retry_after": 37}}

    _patch_module_post(monkeypatch, response=Resp())
    with pytest.raises(SendError) as ei:
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x"}), "T")
    assert ei.value.retry_after == 37.0


def test_ok_false_at_http200_is_error(monkeypatch):
    """B-7: ok:false im Body (Proxy-Szenario) ist kein Erfolg."""
    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    class Resp:
        status_code = 200

        def json(self):
            return {"ok": False, "description": "chat not found"}

    _patch_module_post(monkeypatch, response=Resp())
    with pytest.raises(SendError):
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x"}), "T")


def test_invalid_json_at_http200_is_handled(monkeypatch):
    from telegram_formatter.sender import SendError, send_message
    from telegram_formatter.utils import TelegramMessage

    class Resp:
        status_code = 200

        def json(self):
            raise ValueError("no json")

    _patch_module_post(monkeypatch, response=Resp())
    with pytest.raises(SendError):
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x"}), "T")


def test_rejects_insecure_api_base():
    """M-3: http:// (nicht-local) würde Token + Inhalt im Klartext senden."""
    from telegram_formatter.sender import ApiBaseError, send_message
    from telegram_formatter.utils import TelegramMessage

    with pytest.raises(ApiBaseError):
        send_message(TelegramMessage("regular", {"chat_id": 1, "text": "x"}), "T",
                     api_base="http://evil.example/")

    # localhost bleibt für Tests erlaubt
    from telegram_formatter.sender import validate_api_base

    validate_api_base("http://127.0.0.1:8081")  # darf nicht werfen
    with pytest.raises(ApiBaseError):
        validate_api_base("not-a-url")


def test_telegram_api_also_rejects_insecure_base():
    from telegram_formatter.botkit.telegram_api import TelegramAPIError, get_me

    with pytest.raises(TelegramAPIError):
        get_me("123:T", api_base="http://evil.example")


def test_custom_api_base_keeps_bot_token_segment(fake_requests):
    """v2.11.1: Auch mit api_base läuft die URL über /bot<token>/ (wie botkit.telegram_api)."""
    from telegram_formatter.utils import TelegramMessage

    fake, calls = fake_requests
    from telegram_formatter.sender import send_message

    msg = TelegramMessage("regular", {"chat_id": 1, "text": "hi", "parse_mode": "HTML"})
    send_message(msg, "TOKEN", api_base="http://127.0.0.1:8081/")
    assert calls[0]["url"] == "http://127.0.0.1:8081/botTOKEN/sendMessage"
