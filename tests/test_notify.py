"""Telegram notification: must never raise (a notification failure can't
be allowed to break the trading job it's attached to), and must skip
cleanly when secrets aren't configured rather than crashing on a
None token/chat_id."""
import json
import urllib.error

import pytest

from sj_trading import notify
from sj_trading.gridbot_body import _format_drift_message


def test_send_telegram_message_skips_when_secrets_missing(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    assert notify.send_telegram_message("hello") is False


def test_send_telegram_message_posts_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data)
        return FakeResponse()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)

    assert notify.send_telegram_message("hello world") is True
    assert captured["url"] == "https://api.telegram.org/botfake-token/sendMessage"
    assert captured["body"] == {"chat_id": "12345", "text": "hello world"}


def test_send_telegram_message_returns_false_on_api_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")

    def fake_urlopen(req, timeout=None):
        raise urllib.error.URLError("network down")

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)

    assert notify.send_telegram_message("hello") is False


def test_send_telegram_message_returns_false_on_not_ok_response(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "bad-chat-id")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": False, "description": "chat not found"}).encode()

    monkeypatch.setattr(notify.urllib.request, "urlopen", lambda req, timeout=None: FakeResponse())

    assert notify.send_telegram_message("hello") is False


def test_format_drift_message_includes_key_facts():
    msg = _format_drift_message(
        today="2026-09-18", pnl_pct=-0.05, z=-4.2, regime="Bear",
        today_equity=31000.0, prior_equity=32600.0,
        realized=-500.0, unrealized=-1100.0,
        uppershare=1000, lowershare=2000,
    )
    assert "2026-09-18" in msg
    assert "-5.00%" in msg
    assert "z=-4.20" in msg
    assert "regime=Bear" in msg
    assert "31000.00" in msg and "32600.00" in msg
    assert "1000" in msg and "2000" in msg
    assert "CLAUDE.md" in msg
