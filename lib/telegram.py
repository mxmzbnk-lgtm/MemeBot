"""Thin wrapper around the Telegram Bot API using httpx (sync)."""

from typing import Optional

import httpx

from lib.config import TELEGRAM_API

_TIMEOUT = 15


def _post(method: str, data: dict) -> dict:
    resp = httpx.post(f"{TELEGRAM_API}/{method}", json=data, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def send_photo(
    chat_id: str,
    photo: str,
    caption: Optional[str] = None,
    schedule_date: Optional[int] = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "photo": photo}
    if caption:
        payload["caption"] = caption
    if schedule_date:
        payload["schedule_date"] = schedule_date
    return _post("sendPhoto", payload)


def send_video(
    chat_id: str,
    video: str,
    caption: Optional[str] = None,
    schedule_date: Optional[int] = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "video": video}
    if caption:
        payload["caption"] = caption
    if schedule_date:
        payload["schedule_date"] = schedule_date
    return _post("sendVideo", payload)


def send_message(
    chat_id: str,
    text: str,
    schedule_date: Optional[int] = None,
) -> dict:
    payload: dict = {"chat_id": chat_id, "text": text}
    if schedule_date:
        payload["schedule_date"] = schedule_date
    return _post("sendMessage", payload)


def reply(chat_id: int, message_id: int, text: str) -> dict:
    return _post("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_to_message_id": message_id,
    })


def set_webhook(url: str, secret_token: str) -> dict:
    return _post("setWebhook", {"url": url, "secret_token": secret_token})


def delete_webhook() -> dict:
    return _post("deleteWebhook", {})
