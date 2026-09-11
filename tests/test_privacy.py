"""Tests für :mod:`botkit.privacy` — Redaction, Fingerprints, audit()."""

from __future__ import annotations

import logging

import pytest

from botkit.privacy import (
    REDACTED,
    RedactingFilter,
    audit,
    content_digest,
    fingerprint,
    install_privacy_filters,
    redact,
    scrub_environment,
)

SECRET = "123456789:" + "A" * 35


# --------------------------------------------------------------------------- #
# redact
# --------------------------------------------------------------------------- #
def test_redact_removes_token_like_values():
    line = f"POST https://api.telegram.org/bot{SECRET}/sendMessage"
    cleaned = redact(line)
    assert SECRET not in cleaned
    assert REDACTED in cleaned


def test_redact_masks_secret_assignments():
    cleaned = redact('token = "supergeheim123"')
    assert "supergeheim123" not in cleaned
    assert "token" in cleaned  # Kontext bleibt erhalten


def test_redact_leaves_harmless_text_untouched():
    assert redact("Nachricht mit 3 Chunks gesendet") == "Nachricht mit 3 Chunks gesendet"


def test_redact_handles_empty_input():
    assert redact("") == ""


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #
def test_fingerprint_is_deterministic_within_process():
    assert fingerprint(SECRET) == fingerprint(SECRET)
    assert fingerprint(SECRET) != fingerprint(SECRET[:-1] + "B")
    assert SECRET not in fingerprint(SECRET)


def test_content_digest_differs_from_raw_sha256():
    digest = content_digest("vertraulicher Text")
    assert "vertraulicher Text" not in digest
    assert len(digest) == 12


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def test_redacting_filter_scrubs_log_records(caplog):
    logger = logging.getLogger("test.privacy.filter")
    logger.addFilter(RedactingFilter())
    logger.propagate = True

    with caplog.at_level(logging.DEBUG):
        logger.info("Token im Fließtext: %s", SECRET)

    assert SECRET not in caplog.text
    assert REDACTED in caplog.text


def test_redacting_filter_is_installed_on_noisy_libraries():
    install_privacy_filters()
    for name in ("urllib3", "requests", "werkzeug", "botkit"):
        assert any(isinstance(f, RedactingFilter) for f in logging.getLogger(name).filters)


def test_audit_summarizes_sensitive_fields(caplog):
    logger = logging.getLogger("test.privacy.audit")
    with caplog.at_level(logging.DEBUG):
        audit(logger, logging.INFO, "session.sent", bot=123456789, chunks=2,
              text="**geheim** und $x^2$", chat_id="-1001234567890")

    assert "geheim" not in caplog.text
    assert "-1001234567890" not in caplog.text
    assert "len=" in caplog.text and "fp=" in caplog.text
    assert "chunks=2" in caplog.text


def test_audit_truncates_long_values(caplog):
    logger = logging.getLogger("test.privacy.audit.long")
    with caplog.at_level(logging.DEBUG):
        audit(logger, logging.INFO, "evt", reason="x" * 500)
    assert "x" * 500 not in caplog.text


# --------------------------------------------------------------------------- #
# Environment
# --------------------------------------------------------------------------- #
def test_scrub_environment_removes_only_listed_names(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", SECRET)
    monkeypatch.setenv("UNRELATED", "bleibt")

    removed = list(scrub_environment("TELEGRAM_BOT_TOKEN", "FEHLT"))

    assert removed == ["TELEGRAM_BOT_TOKEN"]
    assert "TELEGRAM_BOT_TOKEN" not in __import__("os").environ
    assert __import__("os").environ["UNRELATED"] == "bleibt"


@pytest.mark.parametrize("value", ["", None])
def test_redact_with_falsy_values(value):
    assert redact(value or "") == ""
