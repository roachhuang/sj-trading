"""Telegram push notifications - currently only for the end-of-day drift
alert (see gridbot_body.py's log_daily_pnl()). Uses urllib instead of
requests/httpx so this doesn't add a new declared dependency for one POST
call - shioaji/yfinance happen to pull requests in transitively today, but
that's not a guarantee.

Setup: message @BotFather on Telegram, /newbot, copy the token it gives
you into the TELEGRAM_BOT_TOKEN secret. Then message your new bot anything
and open https://api.telegram.org/bot<token>/getUpdates in a browser - the
JSON response has your chat id at result[0].message.chat.id; put that in
TELEGRAM_CHAT_ID. Both are read from env/secrets, never committed.
"""
import json
import logging
import os
import urllib.error
import urllib.request

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def send_telegram_message(text: str, timeout: float = 10.0) -> bool:
    """Best-effort - always returns False and logs rather than raising, so
    a notification failure (missing secret, Telegram API down, network
    blip) never breaks the caller's actual job (e.g. the trading loop's
    end-of-day exit, which still needs to persist money.json/exit cleanly
    regardless of whether the alert got sent)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logging.warning("send_telegram_message: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID not set, skipping")
        return False

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        TELEGRAM_API.format(token=token),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            logging.error(f"send_telegram_message: Telegram API returned not-ok: {body}")
            return False
        return True
    except urllib.error.URLError as e:
        logging.error(f"send_telegram_message failed: {e}")
        return False
    except Exception as e:
        logging.error(f"send_telegram_message failed unexpectedly: {e}")
        return False
