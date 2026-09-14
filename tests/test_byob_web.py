"""Tests für die BYOB-Websessions (``/api/byob/*``, v2.2.0).

Botkit-Betriebsmodus B („gehostete Session“): eigenes Token verifizieren,
ephemere RAM-Session öffnen, darüber senden, Status/Countdown, Chat-Erkennung
und das sichere Beenden — alles **ohne** Speicherung von Token oder Inhalten.

Die Tests härten die Sicherheitsversprechen ab:

* Token taucht in **keiner** Antwort auf (auch nicht in Fehlern),
* Sessions sind an IP-Kappen und Rate-Limits gebunden,
* abgelaufene/geschlossene Sessions sind weg und geben RAM frei; fehlende
  Proof-of-Possession-Daten werden als 401/400 abgewiesen,
* die Chat-Erkennung liefert ausschließlich Metadaten (keine Nachrichtentexte),
* Origin-/Auth-Guards greifen auch für die neuen Endpunkte.
"""

from __future__ import annotations

import pytest

from telegram_formatter import app as app_module
from telegram_formatter.botkit.session import SessionConfig
from telegram_formatter.sender import SendError

#: Wohlgeformtes Test-Token (Format analog @BotFather, ohne echten Bot).
TOKEN = "123456789:AAH1bcDefGhIjKlMnOpQrStUvWxYz012345"
BOT_ID = 123456789
OTHER_TOKEN = "987654321:AAH1bcDefGhIjKlMnOpQrStUvWxYz054321"


def _get_me_ok(secret: str, **_kwargs) -> dict:
    """getMe-Stub: Token-Präfix bestimmt die Bot-Identität."""
    bot_id = int(secret.split(":", 1)[0])
    return {
        "ok": True,
        "result": {
            "id": bot_id,
            "is_bot": True,
            "username": f"bot_{bot_id}",
            "first_name": f"Bot {bot_id}",
        },
    }


@pytest.fixture()
def byob_client(monkeypatch):
    """Isolierter BYOB-Zustand: frische Runtime, Limits aus, leere Buckets."""
    monkeypatch.setattr(app_module, "BYOB_ENABLED", True)
    monkeypatch.setattr(app_module, "BYOB_SESSIONS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "BYOB_DISCOVER_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "BYOB_SENDS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "BYOB_MAX_SESSIONS_TOTAL", 100)
    monkeypatch.setattr(app_module, "BYOB_MAX_SESSIONS_PER_IP", 3)
    monkeypatch.setattr(app_module, "_BYOB_RUNTIME", None)
    monkeypatch.setattr(app_module, "get_me", _get_me_ok)
    app_module._RATE_HITS.clear()
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as client:
        yield client
    app_module._RATE_HITS.clear()
    monkeypatch.setattr(app_module, "_BYOB_RUNTIME", None)


def _open_session(client, *, token=TOKEN, chat="-1001234567890", consent=True):
    return client.post(
        "/api/byob/session",
        json={"token": token, "chat_id": chat, "consent": consent},
    )


# --------------------------------------------------------------------------- #
# Session öffnen
# --------------------------------------------------------------------------- #
def test_open_session_returns_handle_and_identity(byob_client):
    resp = _open_session(byob_client)
    assert resp.status_code == 201
    data = resp.json
    assert data["session_id"]
    assert data["bot"] == {
        "id": BOT_ID,
        "username": f"bot_{BOT_ID}",
        "display_name": f"Bot {BOT_ID}",
        "handle": f"@bot_{BOT_ID}",
    }
    assert data["chat_id"] == "-1001234567890"
    limits = data["limits"]
    assert limits["ttl_seconds"] == app_module.BYOB_TTL_SECONDS
    assert limits["idle_timeout_seconds"] == app_module.BYOB_IDLE_SECONDS
    assert limits["max_messages_per_minute"] == 20
    # Kernversprechen: kein Token in der Antwort.
    assert TOKEN not in resp.data.decode()
    assert len(data["session_secret"]) >= 32


def test_session_secret_is_required_and_not_interchangeable(byob_client):
    data = _open_session(byob_client).json
    sid = data["session_id"]
    missing = byob_client.post("/api/byob/status", json={"session_id": sid})
    assert missing.status_code == 400
    wrong = byob_client.post(
        "/api/byob/status",
        json={"session_id": sid, "session_secret": "x" * 43},
    )
    assert wrong.status_code == 401
    valid = byob_client.post(
        "/api/byob/status",
        json={"session_id": sid, "session_secret": data["session_secret"]},
    )
    assert valid.status_code == 200


def test_open_session_requires_consent(byob_client):
    resp = _open_session(byob_client, consent=False)
    assert resp.status_code == 400
    assert "bestätige" in resp.json["error"]


def test_open_session_rejects_malformed_token(byob_client):
    resp = _open_session(byob_client, token="123:nope")
    assert resp.status_code == 400
    assert "Token-Format" in resp.json["error"]


def test_open_session_rejects_non_string_token(byob_client):
    resp = byob_client.post(
        "/api/byob/session", json={"token": 12345, "chat_id": "1", "consent": True}
    )
    assert resp.status_code == 400


def test_open_session_rejects_invalid_chat_id(byob_client):
    resp = _open_session(byob_client, chat="abc")
    assert resp.status_code == 400
    assert "chat_id" in resp.json["error"]


def test_open_session_reports_verification_failure_without_token(byob_client, monkeypatch):
    def rejected(secret, **_kw):
        raise app_module.TelegramAPIError("Telegram-API getMe lehnte ab: Unauthorized")

    monkeypatch.setattr(app_module, "get_me", rejected)
    resp = _open_session(byob_client)
    assert resp.status_code == 400
    assert "verifiziert werden" in resp.json["error"]
    assert TOKEN not in resp.data.decode()


def test_open_session_rejects_id_mismatch(byob_client, monkeypatch):
    def wrong_bot(secret, **_kw):
        return {"ok": True, "result": {"id": 1, "is_bot": True, "username": "x", "first_name": "X"}}

    monkeypatch.setattr(app_module, "get_me", wrong_bot)
    resp = _open_session(byob_client)
    assert resp.status_code == 400
    assert "stimmt nicht" in resp.json["error"]


def test_open_session_rate_limited(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_SESSIONS_PER_MINUTE", 1)
    assert _open_session(byob_client).status_code == 201
    resp = _open_session(byob_client)
    assert resp.status_code == 429


def test_open_session_caps_per_ip(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_MAX_SESSIONS_PER_IP", 1)
    assert _open_session(byob_client).status_code == 201
    resp = _open_session(byob_client, token=OTHER_TOKEN)
    assert resp.status_code == 429
    assert "Adresse" in resp.json["error"]


def test_open_session_caps_total(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_MAX_SESSIONS_TOTAL", 1)
    assert _open_session(byob_client).status_code == 201
    resp = _open_session(byob_client, token=OTHER_TOKEN)
    assert resp.status_code == 429
    assert "Instanz" in resp.json["error"]


def test_close_frees_per_ip_cap(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_MAX_SESSIONS_PER_IP", 1)
    data = _open_session(byob_client).json
    assert byob_client.post("/api/byob/close", json={"session_id": data["session_id"], "session_secret": data["session_secret"]}).status_code == 200
    # Nach dem Schließen ist der Platz wieder frei.
    assert _open_session(byob_client, token=OTHER_TOKEN).status_code == 201


def test_disabled_byob_returns_404(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_ENABLED", False)
    assert byob_client.post("/api/byob/session", json={}).status_code == 404
    assert byob_client.post("/api/byob/discover", json={}).status_code == 404
    assert byob_client.post("/api/byob/send", json={}).status_code == 404
    assert byob_client.post("/api/byob/status", json={}).status_code == 404
    assert byob_client.post("/api/byob/close", json={}).status_code == 404


# --------------------------------------------------------------------------- #
# Senden über die Session
# --------------------------------------------------------------------------- #
@pytest.fixture()
def fake_sender(monkeypatch):
    """Protokolliert Aufrufe; sendet 'erfolgreich', ohne Netzwerk."""
    calls = []

    def sender(message, secret, **_kwargs):
        calls.append({"kind": message.kind, "secret": secret, "payload": message.payload})
        return {"ok": True, "result": {"message_id": len(calls)}}

    import telegram_formatter.botkit.session as session_module

    monkeypatch.setattr(session_module, "send_message", sender)
    return calls


def test_send_via_session_uses_own_token(byob_client, fake_sender):
    data = _open_session(byob_client).json
    resp = byob_client.post(
        "/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "**Hallo** $x^2$"}
    )
    assert resp.status_code == 200
    assert resp.json["sent"] == 1
    assert resp.json["session"]["ttl_remaining_seconds"] > 0
    # Der eigene Bot (TOKEN) wurde benutzt — nicht der geteilte Server-Bot.
    assert len(fake_sender) == 1
    assert fake_sender[0]["secret"] == TOKEN
    assert fake_sender[0]["payload"]["chat_id"] == "-1001234567890"
    assert TOKEN not in resp.data.decode()


def test_send_unknown_session_gives_410(byob_client):
    resp = byob_client.post("/api/byob/send", json={"session_id": "f" * 32, "session_secret": "s" * 43, "text": "hi"})
    assert resp.status_code == 401
    assert "Zugangsdaten ungültig" in resp.json["error"]


def test_send_requires_session_id(byob_client):
    resp = byob_client.post("/api/byob/send", json={"text": "hi"})
    assert resp.status_code == 400


def test_send_rejects_oversized_text(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "MAX_INPUT_CHARS", 10)
    data = _open_session(byob_client).json
    resp = byob_client.post(
        "/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "x" * 11}
    )
    assert resp.status_code == 400
    assert "zu lang" in resp.json["error"]


def test_send_expired_session_gives_410(byob_client, monkeypatch):
    data = _open_session(byob_client).json
    # TTL kürzer als die Lebensdauer der gerade geöffneten Session machen und
    # den Status-Pfad prüfen: abgelaufene Sessions verschwinden aktiv.
    runtime = app_module._BYOB_RUNTIME
    session = runtime.manager.get(data["session_id"])
    session._config = SessionConfig(ttl_seconds=0.0, idle_timeout_seconds=0.0)
    assert runtime.manager.get(data["session_id"]) is None
    resp = byob_client.post("/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "hi"})
    assert resp.status_code == 410


def test_send_rate_limited_per_ip(byob_client, fake_sender, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_SENDS_PER_MINUTE", 1)
    data = _open_session(byob_client).json
    assert byob_client.post(
        "/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "a"}
    ).status_code == 200
    resp = byob_client.post("/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "b"})
    assert resp.status_code == 429


def test_send_maps_session_rate_limit_to_429(byob_client, fake_sender, monkeypatch):
    # Session-eigenes Nachrichtenlimit (nicht das IP-Limit) erzwingen.
    config = SessionConfig(
        require_review=False, ttl_seconds=1800, idle_timeout_seconds=600,
        max_messages_per_minute=1, max_input_chars=100_000,
    )
    runtime = app_module._build_byob(config)
    monkeypatch.setattr(app_module, "_BYOB_RUNTIME", runtime)

    data = _open_session(byob_client).json
    first = byob_client.post(
        "/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "eins"}
    )
    assert first.status_code == 200
    second = byob_client.post(
        "/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "zwei"}
    )
    assert second.status_code == 429
    assert "Rate-Limit" in second.json["error"]


def test_send_reports_partial_progress_on_send_error(byob_client, monkeypatch):
    import telegram_formatter.botkit.session as session_module

    sent = []

    def flaky(message, secret, **_kwargs):
        sent.append(message)
        if len(sent) == 2:  # erster Chunk ok, zweiter scheitert
            raise SendError("Telegram-API-Fehler 400: chat not found.")
        return {"ok": True, "result": {"message_id": len(sent)}}

    monkeypatch.setattr(session_module, "send_message", flaky)

    # Ausreichend langer Plain-Text => Regular-Pfad, mehrere 4096-Chunks.
    text = "**fett** und ganz viel Text\n\n" * 900
    data = _open_session(byob_client).json
    resp = byob_client.post("/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": text})
    assert resp.status_code == 502
    assert resp.json["sent_before_error"] >= 1
    assert "kein kompletter Wiederholungsversand" in resp.json["note"]
    assert TOKEN not in resp.data.decode()


def test_send_retries_reported_on_429_from_telegram(byob_client, monkeypatch):
    import telegram_formatter.botkit.session as session_module

    def limited(_message, _secret, **_kwargs):
        raise SendError("Telegram-API-Fehler 429: Too Many Requests.", retry_after=13.0)

    monkeypatch.setattr(session_module, "send_message", limited)
    data = _open_session(byob_client).json
    resp = byob_client.post("/api/byob/send", json={"session_id": data["session_id"], "session_secret": data["session_secret"], "text": "hi"})
    assert resp.status_code == 429
    assert resp.json["retry_after"] == 13.0


# --------------------------------------------------------------------------- #
# Status & Schließen
# --------------------------------------------------------------------------- #
def test_status_active_and_gone(byob_client):
    data = _open_session(byob_client).json
    sid = data["session_id"]

    resp = byob_client.post("/api/byob/status", json={"session_id": sid, "session_secret": data["session_secret"]})
    assert resp.status_code == 200
    body = resp.json
    assert body["active"] is True
    assert body["bot"]["handle"] == f"@bot_{BOT_ID}"
    assert body["chat_id"] == "-1001234567890"
    assert body["ttl_remaining_seconds"] <= 1800
    assert body["idle_remaining_seconds"] <= 600
    assert body["messages_sent"] == 0

    assert byob_client.post("/api/byob/close", json={"session_id": sid, "session_secret": data["session_secret"]}).json["closed"] is True
    assert byob_client.post("/api/byob/status", json={"session_id": sid, "session_secret": data["session_secret"]}).status_code == 401


def test_close_is_idempotent(byob_client):
    data = _open_session(byob_client).json
    sid = data["session_id"]
    assert byob_client.post("/api/byob/close", json={"session_id": sid, "session_secret": data["session_secret"]}).json["closed"] is True
    assert byob_client.post("/api/byob/close", json={"session_id": sid, "session_secret": data["session_secret"]}).status_code == 401


def test_close_drops_token_reference(byob_client):
    data = _open_session(byob_client).json
    sid = data["session_id"]
    runtime = app_module._BYOB_RUNTIME
    session = runtime.manager.get(sid)
    assert session is not None and not session.closed
    assert byob_client.post("/api/byob/close", json={"session_id": sid, "session_secret": data["session_secret"]}).json["closed"] is True
    assert session.closed
    assert session._token is None  # Token-Referenz gefallen
    assert runtime.manager.active_count == 0


def test_expired_sessions_are_pruned_from_meta(byob_client, monkeypatch):
    first = _open_session(byob_client).json
    runtime = app_module._BYOB_RUNTIME
    session = runtime.manager.get(first["session_id"])
    session._config = SessionConfig(ttl_seconds=0.0, idle_timeout_seconds=0.0)

    # Neue Session triggert reap + prune: Metadaten der alten fallen.
    second = _open_session(byob_client, token=OTHER_TOKEN)
    assert second.status_code == 201
    runtime.prune()
    assert first["session_id"] not in runtime._meta
    assert second.json["session_id"] in runtime._meta


# --------------------------------------------------------------------------- #
# Chat-Erkennung
# --------------------------------------------------------------------------- #
def _updates_fixture() -> dict:
    return {
        "ok": True,
        "result": [
            {
                "update_id": 1,
                "message": {
                    "message_id": 11,
                    "text": "GEHEIMER INHALT privater chat",
                    "chat": {"id": 4711, "type": "private", "first_name": "Alice", "username": "alice"},
                },
            },
            {
                "update_id": 2,
                "message": {
                    "message_id": 12,
                    "text": "noch so ein geheimer text",
                    "chat": {"id": 4711, "type": "private", "first_name": "Alice"},
                },
            },
            {
                "update_id": 3,
                "channel_post": {
                    "message_id": 13,
                    "text": "kanal-nachricht",
                    "chat": {"id": -1001234567890, "type": "channel", "title": "Mein Kanal"},
                },
            },
        ],
    }


def test_discover_returns_chat_metadata_only(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_updates", lambda *a, **kw: _updates_fixture())
    resp = byob_client.post("/api/byob/discover", json={"token": TOKEN})
    assert resp.status_code == 200
    chats = resp.json["chats"]
    assert [c["id"] for c in chats] == [4711, -1001234567890]
    assert chats[0]["type"] == "private"
    assert chats[0]["name"] == "Alice"
    assert chats[1]["name"] == "Mein Kanal"
    # Privacy: keine Nachrichtentexte in der Antwort.
    body = resp.data.decode()
    assert "GEHEIMER" not in body and "geheimer" not in body and "kanal-nachricht" not in body


def test_discover_rejects_bad_token(byob_client):
    resp = byob_client.post("/api/byob/discover", json={"token": "nope"})
    assert resp.status_code == 400


def test_discover_maps_api_error_to_502(byob_client, monkeypatch):
    def conflict(*_a, **_kw):
        raise app_module.TelegramAPIError(
            "Telegram-API getUpdates lehnte ab: Conflict: terminated by other getUpdates"
        )

    monkeypatch.setattr(app_module, "get_updates", conflict)
    resp = byob_client.post("/api/byob/discover", json={"token": TOKEN})
    assert resp.status_code == 502
    assert "Chat-Erkennung fehlgeschlagen" in resp.json["error"]
    assert TOKEN not in resp.data.decode()


def test_discover_empty_result_is_ok(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "get_updates", lambda *a, **kw: {"ok": True, "result": []})
    resp = byob_client.post("/api/byob/discover", json={"token": TOKEN})
    assert resp.status_code == 200
    assert resp.json == {"chats": []}


def test_discover_rate_limited(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_DISCOVER_PER_MINUTE", 1)
    monkeypatch.setattr(app_module, "get_updates", lambda *a, **kw: {"ok": True, "result": []})
    assert byob_client.post("/api/byob/discover", json={"token": TOKEN}).status_code == 200
    assert byob_client.post("/api/byob/discover", json={"token": OTHER_TOKEN}).status_code == 429


# --------------------------------------------------------------------------- #
# Guards: Origin, Auth-Token, Body-Limit — gelten auch für BYOB
# --------------------------------------------------------------------------- #
def test_byob_rejects_foreign_origin(byob_client):
    resp = byob_client.post(
        "/api/byob/session",
        json={"token": TOKEN, "chat_id": "1", "consent": True},
        headers={"Origin": "https://boese.example"},
    )
    assert resp.status_code == 403


def test_byob_requires_api_token_when_configured(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "API_TOKEN", "s3cret")
    resp = byob_client.post("/api/byob/session", json={})
    assert resp.status_code == 401
    resp = byob_client.post(
        "/api/byob/session", json={}, headers={"X-Auth-Token": "s3cret"}
    )
    assert resp.status_code == 400  # Guard passiert, dann normale Validierung


def test_byob_rejects_non_json_body(byob_client):
    resp = byob_client.post("/api/byob/session", data="kein json",
                            content_type="text/plain")
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Template-Verträge der BYOB-Oberfläche
# --------------------------------------------------------------------------- #
def test_index_renders_byob_panel_and_warning(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456789:" + "A" * 35)
    monkeypatch.setattr(app_module, "CHAT_ID", "-1001234567890")
    monkeypatch.setattr(app_module, "API_TOKEN", "s3cret")
    page = byob_client.get("/").data.decode("utf-8")

    assert 'id="byob"' in page
    assert 'id="byobForm"' in page
    assert 'id="byobToken"' in page
    assert 'id="byobChat"' in page
    assert 'id="byobConsent"' in page
    assert 'id="byobDiscoverBtn"' in page
    assert 'id="byobActive"' in page
    assert 'id="byobCloseBtn"' in page
    assert 'id="sendPathNote"' in page
    assert 'data-byob-base="/api/byob"' in page
    # Token-Feld: Passwortfeld ohne Autocomplete (Schulterblick-Schutz).
    assert 'id="byobToken"' in page and 'type="password"' in page
    assert 'autocomplete="off"' in page
    # Warnung zum geteilten Bot + empfohlener BYOB-Weg: seit v2.3.0 als
    # Top-Warnung GANZ OBEN auf der Seite (vor Hero und Editor).
    assert 'class="tf-top-warning"' in page
    assert "Wichtig — der geteilte Bot ist öffentlich!" in page
    assert page.index('class="tf-top-warning"') < page.index('class="tf-hero"')
    assert "tf-note--danger" in page
    assert "@mdtotxt_bot" in page
    # Seit v2.9.0 benennt die Warnung den konkreten öffentlichen Kanal statt
    # nur „einen gemeinsamen Chat" (Details: tests/test_shared_channel.py).
    assert "öffentlichen Telegram-Kanal" in page
    assert "t.me/mdtotxt_bot_web" in page
    assert "Bring Your Own Bot" in page
    # js/byob.js ist eingebunden.
    assert "js/byob.js" in page


def test_index_without_shared_bot_shows_neutral_note(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BOT_TOKEN", "")
    monkeypatch.setattr(app_module, "CHAT_ID", "")
    page = byob_client.get("/").data.decode("utf-8")
    assert "Kein geteilter Bot konfiguriert" in page
    # Keine sichtbare Rot-Warnung ohne geteilten Bot: keine Top-Warnung …
    assert 'class="tf-top-warning"' not in page
    assert "Wichtig — der geteilte Bot ist öffentlich!" not in page
    # … die Warnung des Sende-Bestätigungsdialogs bleibt dabei hidden im
    # Markup (sichtbar schaltet sie app.js erst bei aktivem geteilten Bot) …
    assert 'id="sendConfirmWarning" class="tf-note tf-note--danger" role="note" hidden' in page
    assert 'id="sendConfirmUnavailable" class="tf-note" role="note" hidden' in page
    # … aber das BYOB-Panel bleibt.
    assert 'id="byobForm"' in page


def test_index_hides_byob_when_disabled(byob_client, monkeypatch):
    monkeypatch.setattr(app_module, "BYOB_ENABLED", False)
    page = byob_client.get("/").data.decode("utf-8")
    assert 'id="byobForm"' not in page
    assert "BYOB ist auf dieser Instanz deaktiviert" in page
