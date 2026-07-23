"""
webhook.py
----------
Standalone FastAPI webhook for inbound WhatsApp messages.

Use this if you host the webhook yourself (a small VM, a container, Fly, Render,
etc.) instead of the Modal endpoint in planner/app.py. It reuses the exact same
planner logic, so behaviour is identical either way.

Run locally:
    uvicorn webhook:app --host 0.0.0.0 --port 8000

Then point your WhatsApp Cloud API webhook at  https://<host>/webhook  and use
WHATSAPP_VERIFY_TOKEN as the verify token.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from planner import whatsapp
from planner.checkin import handle_inbound

app = FastAPI(title="WhatsApp Day Planner webhook")


@app.get("/webhook")
async def verify(request: Request):
    """Cloud API verification handshake."""
    params = request.query_params
    challenge = whatsapp.verify_webhook(
        params.get("hub.mode", ""),
        params.get("hub.verify_token", ""),
        params.get("hub.challenge", ""),
    )
    if challenge is not None:
        return PlainTextResponse(challenge)
    return Response(status_code=403)


@app.post("/webhook")
async def inbound(request: Request):
    """Receive an inbound message, interpret it, and reply."""
    body = await request.json()
    message = whatsapp.parse_inbound(body)
    if message and message.get("text"):
        try:
            handle_inbound(message["text"])
        except Exception as exc:  # noqa: BLE001
            print(f"handle_inbound error: {exc}")
    return {"status": "ok"}


@app.get("/health")
async def health():
    return {"status": "ok"}
