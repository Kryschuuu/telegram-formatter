"""Tests für die Offenlegung des geteilten Ziel-Kanals (v2.9.0).

Wer keinen eigenen Bot nutzt, sendet über den geteilten Bot in **einen**
öffentlichen Kanal. Diese Tatsache muss für Besuchende eindeutig erkennbar
sein — mit Namen, Link und der Aussage, wann die Nachrichten automatisch
gelöscht werden. Die Tests sichern drei Ebenen:

1. **Konfiguration:** ``normalize_public_chat_url`` akzeptiert ausschließlich
   öffentliche ``https://t.me/<handle>``-Links (kein ``javascript:``, keine
   Fremd-Domain, keine privaten Einladelinks), ``_env_int`` fällt bei
   Tippfehlern laut auf den Standardwert zurück.
2. **Rendering:** Kanal-Link, Bot-Name und Löschhinweis erscheinen an allen
   vorgesehenen Stellen der Seite (Top-Warnung, Kanal-Banner im Hero,
   Hinweis am Senden-Button, Bestätigungsdialog, Privatsphäre-Sektion, FAQ,
   Footer) — und an keiner, wenn kein geteilter Bot konfiguriert ist.
3. **API:** ``POST /api/send`` meldet im ``via``-Feld denselben Kanal und
   dieselbe Aufbewahrungsdauer, die auch die Seite zeigt.
"""

from __future__ import annotations

import logging
import time
from collections import deque

import pytest
from werkzeug.exceptions import BadRequest, ServiceUnavailable

import telegram_formatter.app as app_module

CHANNEL_URL = "https://t.me/mdtotxt_bot_web"
CHANNEL_LABEL = "t.me/mdtotxt_bot_web"


@pytest.fixture()
def client(monkeypatch):
    """Isolierte App: Rate-Limits aus, Buckets leer."""
    monkeypatch.setattr(app_module, "MAX_INPUT_CHARS", 100_000)
    monkeypatch.setattr(app_module, "SENDS_PER_MINUTE", 0)
    monkeypatch.setattr(app_module, "CONVERTS_PER_MINUTE", 0)
    app_module._RATE_HITS.clear()
    monkeypatch.setattr(app_module, "_RATE_LAST_PRUNE", 0.0)
    app_module.app.config["TESTING"] = True
    with app_module.app.test_client() as c:
        yield c
    app_module._RATE_HITS.clear()


@pytest.fixture()
def demo(client, monkeypatch):
    """Gehostete Demo: geteilter Bot, gepinnter Kanal, Browser-Versand offen."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "123456:" + "s" * 35)
    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    monkeypatch.setattr(app_module, "API_TOKEN", "")
    monkeypatch.setattr(app_module, "SHARED_WEB_SEND", True)
    monkeypatch.setattr(app_module, "BYOB_ENABLED", True)
    monkeypatch.setattr(app_module, "SHARED_BOT_HANDLE", "@mdtotxt_bot")
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", CHANNEL_URL)
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 30)
    return client


def _page(client) -> str:
    return client.get("/").data.decode("utf-8")


# --------------------------------------------------------------------------- #
# 1) Konfiguration: URL-Normalisierung und ENV-Parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        "https://t.me/mdtotxt_bot_web",
        "http://t.me/mdtotxt_bot_web",  # wird nicht auf https angehoben -> abgelehnt
    ],
)
def test_normalize_accepts_only_https_telegram_links(raw):
    """https://t.me/<handle> bleibt, http:// fällt weg (kein Downgrade-Link)."""
    if raw.startswith("https://"):
        assert app_module.normalize_public_chat_url(raw) == raw
    else:
        assert app_module.normalize_public_chat_url(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@mdtotxt_bot_web", CHANNEL_URL),
        ("t.me/mdtotxt_bot_web", CHANNEL_URL),
        ("https://telegram.me/mdtotxt_bot_web", CHANNEL_URL),
        ("  https://t.me/mdtotxt_bot_web  ", CHANNEL_URL),
        ("https://t.me/MDtoTXT_bot_web", "https://t.me/MDtoTXT_bot_web"),
    ],
)
def test_normalize_canonicalizes_shorthands(raw, expected):
    """Kurzformen landen alle auf derselben kanonischen URL."""
    assert app_module.normalize_public_chat_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "   ",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "https://evil.example.com/t.me/mdtotxt_bot_web",
        "https://t.me.evil.example.com/mdtotxt_bot_web",
        "https://t.me/+AbCdEfGhIjKl",        # privater Einladelink
        "https://t.me/c/1234567890/12",      # numerischer (privater) Kanal
        "https://t.me/mdtotxt_bot_web/42",   # Deep-Link auf eine Nachricht
        "https://t.me/mdtotxt_bot_web?start=x",
        "https://t.me/mdtotxt_bot_web#top",
        "https://t.me/ab",                   # Handle zu kurz
        "https://t.me/" + "a" * 33,          # Handle zu lang
        "https://t.me/1abc",                 # Handle darf nicht mit Ziffer starten
        "https://t.me/",
        "https://t.me",
    ],
)
def test_normalize_rejects_unsafe_or_private_targets(raw):
    """Fremde Schemata/Domains, private Links und Müll ergeben ``None``.

    ``None`` heißt: Die UI zeigt keinen Link (und der Dialog blendet die
    Kanal-Zeile aus) — ein Konfigurationswert kann so niemals als Klickziel
    auf eine fremde Seite missbraucht werden.
    """
    assert app_module.normalize_public_chat_url(raw) is None


def test_default_channel_is_the_public_demo_channel():
    """Ohne ENV gilt der öffentliche Demo-Kanal der gehosteten Instanz."""
    assert app_module.DEFAULT_SHARED_CHAT_URL == CHANNEL_URL
    assert app_module.SHARED_RETENTION_DAYS == 30


@pytest.mark.parametrize(
    ("value", "expected"),
    [("45", 45), ("0", 0), ("", 30), (None, 30), ("viel", 30), ("-5", 30), ("3.5", 30)],
)
def test_env_int_falls_back_loudly(monkeypatch, caplog, value, expected):
    """Tippfehler im Dashboard deaktivieren kein Limit stillschweigend."""
    if value is None:
        monkeypatch.delenv("TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS", raising=False)
    else:
        monkeypatch.setenv("TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS", value)
    with caplog.at_level(logging.WARNING, logger="telegram_formatter.app"):
        got = app_module._env_int("TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS", 30, minimum=0)
    assert got == expected
    if value in {"viel", "-5", "3.5"}:
        assert "TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS" in caplog.text


def test_env_int_rejects_out_of_range_and_caps(monkeypatch):
    monkeypatch.setenv("TELEGRAM_FORMATTER_X", "99")
    assert app_module._env_int("TELEGRAM_FORMATTER_X", 1, minimum=0, maximum=16) == 1
    monkeypatch.setenv("TELEGRAM_FORMATTER_X", "0")
    assert app_module._env_int("TELEGRAM_FORMATTER_X", 1, minimum=1) == 1


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (0, None),
        (-3, None),
        (1, "Neue Nachrichten werden automatisch nach 1 Tag gelöscht."),
        (30, "Neue Nachrichten werden automatisch nach 30 Tagen gelöscht."),
    ],
)
def test_retention_sentence(days, expected):
    """``0`` Tage bedeutet „keine Aussage" — niemals „sofort gelöscht"."""
    assert app_module._retention_sentence(days) == expected


def test_shared_channel_view_model_is_the_single_source(monkeypatch):
    """Ein View-Model für UI und API — Werte werden pro Request neu gelesen."""
    monkeypatch.setattr(app_module, "CHAT_ID", "-100999")
    monkeypatch.setattr(app_module, "SHARED_BOT_HANDLE", "@demo_bot")
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", "https://t.me/demo_kanal")
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 7)
    assert app_module._shared_channel() == {
        "bot": "@demo_bot",
        "url": "https://t.me/demo_kanal",
        "label": "t.me/demo_kanal",
        "retention_days": 7,
        "retention_text": "Neue Nachrichten werden automatisch nach 7 Tagen gelöscht.",
    }
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", None)
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 0)
    assert app_module._shared_channel() == {
        "bot": "@demo_bot",
        "url": None,
        # Ohne Link bleibt die Kurzform der Bot-Name — die UI zeigt nie leer.
        "label": "demo_bot",
        "retention_days": None,
        "retention_text": None,
    }


def test_channel_is_not_claimed_without_pinned_chat(monkeypatch):
    """Kein gepinnter Zielchat ⇒ kein behaupteter Kanal (falsche Zusage)."""
    monkeypatch.setattr(app_module, "CHAT_ID", "")
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", CHANNEL_URL)
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 30)
    channel = app_module._shared_channel()
    assert channel["url"] is None
    assert channel["retention_days"] is None
    assert channel["retention_text"] is None
    assert channel["label"] == "mdtotxt_bot"  # Rückfall auf den Bot-Namen


def test_unpinned_channel_is_logged_as_inconsistency(monkeypatch, caplog):
    """Kanal-Angabe ohne gepinnten Zielchat = Aussage ins Blaue → Warnung."""
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", CHANNEL_URL)
    monkeypatch.setattr(app_module, "CHAT_ID", "")
    with caplog.at_level(logging.WARNING, logger="telegram_formatter.app"):
        if app_module.SHARED_CHAT_URL and not app_module.CHAT_ID:
            app_module.LOGGER.warning("app.shared_channel_unpinned")
    assert "app.shared_channel_unpinned" in caplog.text


# --------------------------------------------------------------------------- #
# 2) Rendering: der Kanal ist an mehreren Stellen sichtbar
# --------------------------------------------------------------------------- #
#: Die Stellen, an denen der Kanal-Link stehen muss. Reihenfolge = Seitenlauf.
DISCLOSURE_SPOTS = (
    ('class="tf-top-warning"', "Top-Warnung"),
    ('id="sharedChannel"', "Kanal-Banner im Hero"),
    ('id="sendPathNote"', "Hinweis am Senden-Button"),
    ('id="sendConfirmWarning"', "Warnung im Bestätigungsdialog"),
    ('id="privacy"', "Privatsphäre-Sektion"),
    ('id="faq"', "FAQ"),
    ('class="tf-footer"', "Footer"),
)


def test_channel_link_appears_in_every_disclosure_spot(demo):
    """Jeder der Abschnitte nennt den Kanal — als Link oder als Klartext.

    Der Hinweis am Senden-Button (``#sendPathNote``) ist bewusst reiner Text:
    ``app.js`` überschreibt ihn mit ``textContent``, ein dort hinterlegtes
    ``<a>`` wäre nach dem ersten Weg-Wechsel verschwunden.
    """
    page = _page(demo)
    for marker, name in DISCLOSURE_SPOTS:
        start = page.index(marker)
        # Abschnittsende: der nächste Marker bzw. das Dateiende.
        following = [page.index(m) for m, _ in DISCLOSURE_SPOTS if page.index(m) > start]
        end = min(following) if following else len(page)
        section = page[start:end]
        assert CHANNEL_LABEL in section, f"{name} ({marker}) nennt den Kanal nicht"


def test_channel_is_linked_not_just_mentioned(demo):
    """Mindestens vier echte <a href>-Verweise auf den Kanal (nicht nur Text)."""
    page = _page(demo)
    hrefs = [h for h in page.split('href="') if h.startswith(CHANNEL_URL)]
    assert len(hrefs) >= 4, f"zu wenige Kanal-Links im Markup: {len(hrefs)}"
    # Jeder Kanal-Link öffnet sicher in einem neuen Tab.
    for chunk in hrefs:
        tag = chunk.split(">", 1)[0]
        assert 'rel="noopener noreferrer"' in tag


def test_retention_is_stated_next_to_the_channel(demo):
    """Die automatische Löschung nach einem Monat wird wörtlich benannt."""
    page = _page(demo)
    sentence = "Neue Nachrichten werden automatisch nach 30 Tagen gelöscht."
    assert page.count(sentence) >= 4, f"Löschhinweis fehlt an zu vielen Stellen: {page.count(sentence)}"
    assert sentence in page[page.index('class="tf-top-warning"'):page.index('class="tf-hero"')]


def test_body_carries_channel_facts_for_js(demo):
    """app.js erfindet nichts — es liest Kanal und Löschsatz vom <body>."""
    page = _page(demo)
    assert f'data-shared-chat-url="{CHANNEL_URL}"' in page
    assert f'data-shared-chat-label="{CHANNEL_LABEL}"' in page
    assert 'data-shared-retention-text="Neue Nachrichten werden automatisch nach 30 Tagen ' \
           'gelöscht."' in page
    assert 'data-shared-bot="@mdtotxt_bot"' in page


def test_dialog_has_a_channel_row_for_js_to_fill(demo):
    """Die Kanal-Zeile im Dialog ist im Markup versteckt und leer (JS füllt)."""
    page = _page(demo)
    assert 'id="sendConfirmChannelRow" hidden' in page
    assert 'id="sendConfirmChannelLink"' in page
    assert 'id="sendConfirmChannelNote"' in page
    # Die Radio-Zeile des geteilten Wegs nennt den Kanal schon serverseitig.
    assert f"öffentlicher Kanal {CHANNEL_LABEL}" in page


def test_no_channel_disclosure_without_shared_bot(client, monkeypatch):
    """Ohne geteilten Bot gibt es keinen Kanal — und keine Aussage darüber."""
    monkeypatch.setattr(app_module, "BOT_TOKEN", "")
    monkeypatch.setattr(app_module, "CHAT_ID", "")
    page = _page(client)
    assert 'id="sharedChannel"' not in page
    assert CHANNEL_URL not in page
    assert "automatisch nach 30 Tagen gelöscht" not in page
    assert 'data-shared-configured="0"' in page


def test_missing_channel_url_degrades_to_text_only(demo, monkeypatch):
    """Kein gültiger Link ⇒ kein Kanal-Link und keine erfundene Kanal-Aussage."""
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", None)
    page = _page(demo)
    assert CHANNEL_URL not in page
    # Kein <a> ohne Ziel: Alle Sätze wechseln auf die allgemeine Formulierung,
    # nur das Kanal-Banner nennt ersatzweise den Bot-Namen (fett, kein Link).
    assert page.count('<a class="tf-channel-link"') == 0
    assert '<strong class="tf-channel-link">@mdtotxt_bot</strong>' in page
    assert "einen öffentlichen, gemeinsamen Chat" in page
    assert "öffentlichen Telegram-Kanal" not in page
    # JS bekommt ein leeres Attribut → blendet die Kanal-Zeile im Dialog aus.
    assert 'data-shared-chat-url=""' in page
    # Der einzige href="#" im Markup gehört der versteckten Dialog-Zeile.
    assert page.count('href="#"') == 1
    assert 'id="sendConfirmChannelRow" hidden' in page
    # Die Top-Warnung nennt weiterhin den Bot und den öffentlichen Charakter.
    assert "@mdtotxt_bot" in page


def test_retention_zero_makes_no_deletion_claim(demo, monkeypatch):
    """``0`` Tage = keine Aussage — die Seite verspricht dann keine Löschung."""
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 0)
    page = _page(demo)
    assert "Neue Nachrichten werden automatisch nach" not in page
    assert 'data-shared-retention-text=""' in page
    # … und die FAQ sagt stattdessen die Wahrheit über dauerhafte Sichtbarkeit.
    assert "keine automatische Löschung hinterlegt" in page


# --------------------------------------------------------------------------- #
# 3) API: /api/send meldet denselben Kanal
# --------------------------------------------------------------------------- #
def test_send_response_discloses_channel(demo, monkeypatch):
    monkeypatch.setattr(app_module, "send_message", lambda m, t, **kw: {"ok": True})
    resp = demo.post("/api/send", json={"text": "hallo", "confirm_public": True})
    assert resp.status_code == 200
    via = resp.get_json()["via"]
    assert via["chat_url"] == CHANNEL_URL
    assert via["retention_days"] == 30
    assert via["bot"] == "@mdtotxt_bot"
    assert via["public"] is True


def test_send_response_without_channel_config(demo, monkeypatch):
    """Ohne Kanal-Konfiguration meldet die API ``null`` statt einer Erfindung."""
    monkeypatch.setattr(app_module, "send_message", lambda m, t, **kw: {"ok": True})
    monkeypatch.setattr(app_module, "SHARED_CHAT_URL", None)
    monkeypatch.setattr(app_module, "SHARED_RETENTION_DAYS", 0)
    resp = demo.post("/api/send", json={"text": "hallo", "confirm_public": True})
    via = resp.get_json()["via"]
    assert via["chat_url"] is None
    assert via["retention_days"] is None


def test_authenticated_send_discloses_channel_too(demo, monkeypatch):
    """Auch der Operator-Weg meldet den Kanal — die UI zeigt ihn identisch."""
    monkeypatch.setattr(app_module, "send_message", lambda m, t, **kw: {"ok": True})
    monkeypatch.setattr(app_module, "API_TOKEN", "s3cret")
    resp = demo.post(
        "/api/send",
        json={"text": "hallo"},
        headers={"X-Auth-Token": "s3cret"},
    )
    assert resp.status_code == 200
    via = resp.get_json()["via"]
    assert via["chat_url"] == CHANNEL_URL
    assert via["public"] is False


# --------------------------------------------------------------------------- #
# 4) Review-Fixes: HTTP-Status bleiben erhalten, Rate-Buckets laufen nicht voll
# --------------------------------------------------------------------------- #
def test_http_exception_handler_keeps_the_status_code():
    """HTTP-Fehler ohne eigenen Handler werden nicht mehr zu 500 geflattet.

    Regression: ``@app.errorhandler(Exception)`` fing **jede** HTTPException
    (``abort(400)``, 408, 414, 503 …) und machte daraus „Interner Fehler" —
    falscher Status für Clients plus irreführende Log-Warnung.
    """
    with app_module.app.test_request_context("/api/send"):
        response, code = app_module._http_error(BadRequest())
        assert code == 400
        assert response.get_json()["error"] == "Ungültige Anfrage."

        response, code = app_module._http_error(ServiceUnavailable())
        assert code == 503
        assert response.get_json()["error"] == "Dienst vorübergehend nicht verfügbar."


def test_unknown_http_status_gets_a_generic_html_free_message():
    with app_module.app.test_request_context("/"):
        response, code = app_module._http_error(BadRequest())
        assert code == 400
        # Werkzeug-Defaults sind HTML-Absätze — in einer JSON-API haben sie
        # nichts zu suchen.
        assert "<" not in response.get_json()["error"]


def test_prune_rate_buckets_removes_empty_and_expired_entries():
    """v2.13.0: Der Sweep räumt nach Alter, nicht nur nach Leerheit.

    v2.12.0 entfernte nur *leere* Eimer. Ein Client, der genau einmal kam,
    hinterlässt aber ein ``deque`` mit einem Zeitstempel — das wird erst
    geleert, wenn derselbe Key zurueckkehrt, und das Leeren passiert nach dem
    Prune-Aufruf. Ein-shot-Adressen sammelten sich damit dauerhaft an.
    """
    app_module._RATE_HITS.clear()
    now = 1_000_000.0
    app_module._RATE_HITS[("convert", "1.1.1.1")] = deque([now - 1.0])  # frisch
    app_module._RATE_HITS[("convert", "2.2.2.2")] = deque()  # leer
    app_module._RATE_HITS[("send", "3.3.3.3")] = deque([now - 600.0])  # alt
    assert app_module._prune_rate_buckets(now, window_seconds=60.0) == 2
    assert list(app_module._RATE_HITS) == [("convert", "1.1.1.1")]
    app_module._RATE_HITS.clear()


def test_rate_limited_prunes_when_buckets_pile_up(client, monkeypatch):
    """Viele verwaiste Eimer (je gesehene IP) werden beim Zählen entfernt.

    Regression: ``_RATE_HITS`` war ein ``defaultdict``, das für jede jemals
    gesehene Adresse einen Eintrag behielt — in einem langlebigen Prozess ein
    unbegrenztes Speicherwachstum.
    """
    monkeypatch.setattr(app_module, "CONVERTS_PER_MINUTE", 60)
    monkeypatch.setattr(app_module, "_RATE_LAST_PRUNE", 0.0)
    now = time.monotonic()
    for i in range(app_module._RATE_PRUNE_THRESHOLD + 10):
        # Verwaist: leer ODER älter als jedes benutzte Fenster.
        hits = deque() if i % 2 else deque([now - 3600.0])
        app_module._RATE_HITS[("convert", f"10.{i // 250}.{i % 250}.1")] = hits
    client.post("/api/convert", json={"text": "hi"})
    assert len(app_module._RATE_HITS) < app_module._RATE_PRUNE_THRESHOLD
    app_module._RATE_HITS.clear()


def test_one_shot_client_addresses_are_eventually_removed(client, monkeypatch):
    """Regression v2.13.0: Ein-shot-Adressen blieben fuer immer im Dict.

    Der alte Sweep pruefte ``not hits`` — ein Client mit genau einem Request
    liess einen nicht-leeren Eimer mit *einem* Zeitstempel zurueck, der nur
    bei einer Rueckkehr desselben Keys geleert wurde (und zwar nach dem
    Sweep). Bei IPv6 ist jeder /64-Praefix eine „neue“ Adresse.
    """
    monkeypatch.setattr(app_module, "CONVERTS_PER_MINUTE", 60)
    monkeypatch.setattr(app_module, "_RATE_PRUNE_THRESHOLD", 10)
    app_module._RATE_HITS.clear()
    # 40 Adressen, jede genau einmal gesehen, alle weit in der Vergangenheit.
    stale = time.monotonic() - 3600.0
    for i in range(40):
        app_module._RATE_HITS[("convert", f"fd00:{i:x}::1")] = deque([stale])
    app_module._maybe_prune_rate_buckets(time.monotonic(), window_seconds=60.0)
    assert app_module._RATE_HITS == {}
    app_module._RATE_HITS.clear()


def test_prune_sweep_is_rate_limited(client, monkeypatch):
    """Regression v2.13.0: der Sweep lief bei *jedem* Request unter dem Lock.

    Sobald die Schwelle einmal ueberschritten ist, bleibt sie es (die Zahl
    waechst monoton). Jeder Request zahlte dann ein vollstaendiges O(n)-
    Iterieren unter dem globalen Lock — ein Serialisierungspunkt ueber alle
    Threads. Der Sweep darf hoechstens einmal je `_RATE_PRUNE_INTERVAL` laufen.
    """
    monkeypatch.setattr(app_module, "_RATE_LAST_PRUNE", 0.0)
    now = time.monotonic()
    for i in range(app_module._RATE_PRUNE_THRESHOLD + 10):
        app_module._RATE_HITS[("convert", f"10.1.{i // 250}.{i % 250}")] = deque()
    app_module._maybe_prune_rate_buckets(now, window_seconds=60.0)
    first = len(app_module._RATE_HITS)
    assert first < app_module._RATE_PRUNE_THRESHOLD
    # Sofort erneut: der Schwellwert ist wieder ueberschritten, aber der Sweep
    # ist gedrosselt -> die Eimer bleiben unangetastet.
    for i in range(app_module._RATE_PRUNE_THRESHOLD + 10):
        app_module._RATE_HITS[("convert", f"10.2.{i // 250}.{i % 250}")] = deque()
    app_module._maybe_prune_rate_buckets(now + 1.0, window_seconds=60.0)
    assert len(app_module._RATE_HITS) > app_module._RATE_PRUNE_THRESHOLD
    # Nach Ablauf der Intervallzeit greift er wieder.
    app_module._maybe_prune_rate_buckets(
        now + app_module._RATE_PRUNE_INTERVAL + 1.0, window_seconds=60.0
    )
    assert len(app_module._RATE_HITS) < app_module._RATE_PRUNE_THRESHOLD
    app_module._RATE_HITS.clear()
