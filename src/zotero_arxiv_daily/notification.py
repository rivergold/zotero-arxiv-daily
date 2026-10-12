"""Deliver the rendered daily digest through the configured channel."""

import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from loguru import logger
from omegaconf import DictConfig


def send_pushplus(config: DictConfig, html: str) -> str | None:
    settings = config.pushplus
    token = settings.token
    if not isinstance(token, str) or not token.strip():
        raise ValueError("Set PUSHPLUS_TOKEN or config.pushplus.token before sending.")

    payload = {
        "token": token.strip(),
        "title": f"Daily arXiv {datetime.now():%Y/%m/%d}",
        "content": html,
        "template": "html",
        "channel": "wechat",
    }
    if settings.get("topic"):
        payload["topic"] = str(settings.topic)

    request = Request(
        "https://www.pushplus.plus/send",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.get("timeout", 30)) as response:
            result = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"PushPlus HTTP request failed (status {exc.code}).") from None
    except (URLError, TimeoutError):
        raise RuntimeError("PushPlus request failed; check network connectivity or timeout.") from None
    except (ValueError, UnicodeError):
        raise RuntimeError("PushPlus returned an invalid JSON response.") from None

    if not isinstance(result, dict) or result.get("code") != 200:
        # Do not include the response body, which may contain credentials.
        code = result.get("code") if isinstance(result, dict) else None
        raise RuntimeError(f"PushPlus rejected the request (code {code}). Check your token and account limits.")

    return result.get("data")


def send_notification(config: DictConfig, html: str) -> None:
    channel = config.notification.channel
    if channel == "pushplus":
        message_id = send_pushplus(config, html)
        logger.info("PushPlus accepted the notification request; message ID: {}", message_id)
    elif channel == "email":
        from .utils import send_email

        send_email(config, html)
        logger.info("Email sent successfully")
    else:
        raise ValueError(f"Unsupported notification channel: {channel!r}. Use 'pushplus' or 'email'.")
