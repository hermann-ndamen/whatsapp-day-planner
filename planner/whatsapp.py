"""
whatsapp.py
-----------
Thin client for the WhatsApp Cloud API (Meta / Graph API).

Two directions:
  - send_text(...)          — outbound: the planner texts the user
  - verify_webhook(...)     — inbound handshake (GET) when you register the URL
  - parse_inbound(...)      — inbound: pull the message text out of a webhook POST

This module knows nothing about scheduling or Claude. It only speaks HTTP to
Meta and returns plain dicts, so it is easy to test and reuse.
"""

from __future__ import annotations

from typing import Optional

import requests

from .config import settings

_TIMEOUT = 20


def _base_url() -> str:
    return (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )


def send_text(body: str, to: Optional[str] = None) -> dict:
    """
    Send a plain-text WhatsApp message via the Cloud API.

    Returns {"success": bool, ...}. Never raises on an API error — the caller
    (a cron job) should keep going and log the failure.
    """
    recipient = to or settings.whatsapp_recipient
    if not settings.whatsapp_token or not settings.whatsapp_phone_number_id or not recipient:
        return {"success": False, "error": "WhatsApp Cloud API not configured"}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"preview_url": False, "body": body[:4096]},
    }
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(_base_url(), headers=headers, json=payload, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        return {"success": False, "error": str(exc)}

    if resp.status_code in (200, 201):
        data = resp.json()
        message_id = (data.get("messages") or [{}])[0].get("id")
        return {"success": True, "message_id": message_id}
    return {"success": False, "error": resp.text[:300], "status": resp.status_code}


def verify_webhook(mode: str, token: str, challenge: str) -> Optional[str]:
    """
    Handle the Cloud API verification GET.

    Meta calls your webhook URL once with hub.mode=subscribe and your verify
    token. Echo back hub.challenge if the token matches; otherwise return None
    so the caller can respond 403.
    """
    if mode == "subscribe" and token and token == settings.whatsapp_verify_token:
        return challenge
    return None


def parse_inbound(body: dict) -> Optional[dict]:
    """
    Extract the first inbound text message from a webhook POST body.

    Returns {"from": <sender>, "text": <str>, "message_id": <str>} or None if
    the payload carries no user text (e.g. a delivery status callback).
    """
    try:
        entry = (body.get("entry") or [])[0]
        change = (entry.get("changes") or [])[0]
        value = change.get("value", {})
        messages = value.get("messages") or []
        if not messages:
            return None  # status callback, not a message

        message = messages[0]
        if message.get("type") != "text":
            return None

        return {
            "from": message.get("from", ""),
            "text": (message.get("text") or {}).get("body", "").strip(),
            "message_id": message.get("id", ""),
        }
    except (IndexError, KeyError, AttributeError, TypeError):
        return None


__all__ = ["send_text", "verify_webhook", "parse_inbound"]
